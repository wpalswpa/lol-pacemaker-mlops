"""LoL 페이스메이커(페이커) 10분 코칭 API. 계약은 AGENTS.md 3·4절.

MODEL_IMPL 로 구현체를 고르고(rule·sklearn·dl·onnx), MODEL_DIR 의 모델 판을 시작할 때 한 번 읽는다.
모델 파일이 없거나 깨졌으면 여기서 예외가 나 프로세스가 뜨지 않는다 → /health 실패 → server.sh 가 이전 판으로.
"""
import csv
import importlib
import io
import json
import logging
import os
import re
import zipfile
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from api import db, llm_coach
from api.build import GAME, RULES, resolve_my, resolve_threat
from api.contract import FEATURES, CoachResponse, GameState, Health, MatchIn, PredictResponse, to_features
from api.service import STEPS_PATH, PaceService, load_json

IMPLS = ("rule", "sklearn", "dl", "onnx")

MODEL_IMPL = os.getenv("MODEL_IMPL", "rule")
if MODEL_IMPL not in IMPLS:
    raise RuntimeError(f"MODEL_IMPL 은 {IMPLS} 중 하나여야 한다: {MODEL_IMPL!r}")
MODEL_DIR = os.getenv("MODEL_DIR", "artifacts")
APP_VERSION = os.getenv("APP_VERSION", "dev")
_card = os.path.join(MODEL_DIR, "model.json")
MODEL_VERSION = json.load(open(_card, encoding="utf-8"))["model_version"] if os.path.exists(_card) else "baked"

_predict = importlib.import_module(f"api.predictors.{MODEL_IMPL}").load(MODEL_DIR)
service = PaceService(_predict, load_json(os.path.join(MODEL_DIR, "baseline.json")), load_json(STEPS_PATH))

app = FastAPI(
    title="lol-pace-api",
    version=APP_VERSION,
    description="LoL 10분 시점 점수판 공개 값으로 승률 페이스·요인·목표 수치를 계산한다. "
    "AI 에이전트는 승률을 스스로 어림하지 말고 이 도구의 숫자만 쓴다.",
)
_INDEX = os.path.join(os.path.dirname(__file__), "static", "index.html")
_SLIDES = os.path.join(os.path.dirname(__file__), "static", "slides.html")
_OVERLAY = os.path.join(os.path.dirname(__file__), "static", "overlay.html")
_DOWNLOAD = os.path.join(os.path.dirname(__file__), "static", "download.html")
_ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
_BRIDGE_PY = os.path.join(_ROOT_DIR, "client", "bridge.py")
_POPUP_SWIFT = os.path.join(_ROOT_DIR, "client", "overlay_popup.swift")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]+$")

# 전적 DB (AGENTS.md 3-5절). PostgreSQL 이 아직 안 떴어도 앱은 뜬다. /matches 가 다음 요청에서 다시 연결한다
try:
    db.init_db()
except db.DBUnavailable:
    logging.getLogger("lol.db").warning("전적 DB 연결 실패. /matches 요청 때 다시 시도한다")


@app.exception_handler(db.DBUnavailable)
async def _db_down(_: Request, __: db.DBUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "전적 DB에 연결하지 못했다"})


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    """입력 오류는 모두 422. 기본 처리기는 받은 값(input)을 응답에 다시 싣는데,
    NaN·Infinity 는 JSON 으로 못 써서 422 가 500 으로 바뀐다. 그래서 위치·이유만 돌려준다."""
    detail = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


def _tag(body: dict) -> dict:
    """어느 구현체·판이 낸 답인지 응답마다 붙인다 (장애 추적용)."""
    return {**body, "model": MODEL_IMPL, "model_version": MODEL_VERSION}


@app.get("/health", response_model=Health, operation_id="health")
def health() -> dict:
    """상태와 코드 판·모델 판."""
    return {"status": "ok", "model": MODEL_IMPL, "app_version": APP_VERSION, "model_version": MODEL_VERSION}


@app.post("/predict", response_model=PredictResponse, operation_id="predict_pace")
def predict(state: GameState) -> dict:
    """10분 시점 내 팀 승률 페이스와, 승률에 가장 크게 작용하는 지표 3개(티어 평균 대비)를 돌려준다."""
    return _tag(service.predict(state))


@app.post("/coach", response_model=CoachResponse, operation_id="coach_targets")
def coach(state: GameState) -> dict:
    """/predict 의 근거 수치에 더해, 다음 몇 분 동안 채우면 승률이 오르는 정량 목표를 돌려준다."""
    return _tag(service.coach(state))


@app.post("/coach/briefing", include_in_schema=False)
def coach_briefing(state: GameState) -> dict:
    """ONNX + 규칙표 분석 JSON을 경량 LLM(Qwen/Haiku 등)에 전달해 2문장 프로 감독 브리핑을 생성한다."""
    coach_res = _tag(service.coach(state))
    return {**coach_res, **llm_coach.generate_briefing(coach_res)}


# 웹 시뮬레이터의 챔피언 선택·초상화용 (AGENTS.md 4절 GET /meta/champions). 도구 스키마에는 넣지 않는다
_META = {
    "version": GAME["version"],
    "source": GAME["source"],
    "champions": sorted(({"id": cid, "name": c["name"], "tags": c["tags"]} for cid, c in GAME["champions"].items()),
                        key=lambda c: c["name"]),
    "roles": list(RULES["role_class"]),
    "threat_types": list(RULES["threat_types"]),
}


@app.get("/meta/champions", include_in_schema=False)
def meta_champions() -> dict:
    return _META


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """사람이 점수판 값을 넣어 보는 시뮬레이터 화면."""
    return FileResponse(_INDEX, media_type="text/html; charset=utf-8")


@app.get("/overlay", include_in_schema=False)
def overlay() -> FileResponse:
    """게임 중 오버레이 HUD 380x560 (AGENTS.md 3-4절). 브리지 /bridge/state 를 2초마다 읽는다."""
    return FileResponse(_OVERLAY, media_type="text/html; charset=utf-8")


@app.get("/slides", include_in_schema=False)
def slides() -> FileResponse:
    """5분 발표 슬라이드(AGENTS.md 4절). 외부 자원 없이 한 파일로 완결."""
    return FileResponse(_SLIDES, media_type="text/html; charset=utf-8")


@app.get("/download", include_in_schema=False)
def download_page() -> FileResponse:
    """Windows/macOS 원클릭 인게임 런처 다운로드 화면 (AGENTS.md 3-6절)."""
    return FileResponse(_DOWNLOAD, media_type="text/html; charset=utf-8")


@app.get("/download/bridge.py", include_in_schema=False)
def download_bridge() -> FileResponse:
    """표준 라이브러리 기반 로컬 브리지 스크립트."""
    return FileResponse(_BRIDGE_PY, media_type="text/x-python; charset=utf-8", filename="bridge.py")


@app.get("/download/overlay_popup.swift", include_in_schema=False)
def download_popup_swift() -> FileResponse:
    """macOS Swift WebKit Always-On-Top 플로팅 패널 소스."""
    return FileResponse(_POPUP_SWIFT, media_type="text/plain; charset=utf-8", filename="overlay_popup.swift")


def _origin_from_request(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        raise HTTPException(status_code=400, detail="잘못된 프로토콜")
    host = (request.headers.get("host") or request.url.netloc or "").strip()
    if not host or not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail="잘못된 Host 헤더")
    return f"{proto}://{host}"


@app.get("/download/launcher", include_in_schema=False)
def download_launcher(request: Request, os: Literal["windows", "mac"] = Query(...)) -> Response:
    """요청 서버 주소가 자동 입력된 원클릭 인게임 오버레이 실행기를 생성해 내려준다."""
    origin = _origin_from_request(request)
    if os == "windows":
        # 화면 문구는 영문 ASCII 만 (AGENTS.md 3-6 "Windows .bat 규칙"): 코드페이지가 어긋나도 cmd 가 줄을 잘못 읽지 않게
        ok_py = "-c \"import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)\" >nul 2>nul"
        find_py = (
            f"python {ok_py} && set \"PY_CMD=python\"\r\n"
            f"if not defined PY_CMD (py -3 {ok_py} && set \"PY_CMD=py -3\")\r\n"
            "for %%V in (314 313 312 311 310 39) do if not defined PY_CMD if exist "
            "\"%LocalAppData%\\Programs\\Python\\Python%%V\\python.exe\" "
            "set \"PY_CMD=\"%LocalAppData%\\Programs\\Python\\Python%%V\\python.exe\"\"\r\n"
            "for %%V in (314 313 312 311 310 39) do if not defined PY_CMD if exist "
            "\"%ProgramFiles%\\Python%%V\\python.exe\" set \"PY_CMD=\"%ProgramFiles%\\Python%%V\\python.exe\"\"\r\n"
        )
        script = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "setlocal\r\n"
            "echo [LoL Pacemaker] Preparing the in-game overlay...\r\n"
            "echo   - LoL settings: Video - Window Mode = Borderless (full screen hides the HUD)\r\n"
            "echo   - The HUD waits in the lobby and turns LIVE once the game starts.\r\n"
            "echo   - Keep only this one window open.\r\n"
            "set \"PY_CMD=\"\r\n"
            + find_py +
            "if not defined PY_CMD (\r\n"
            "  echo Python 3.9+ not found. Trying to install Python 3.12 with winget...\r\n"
            "  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>nul\r\n"
            ")\r\n"
            "if not defined PY_CMD (\r\n  " + find_py.replace("\r\n", "\r\n  ").rstrip() + "\r\n)\r\n"
            "if not defined PY_CMD (\r\n"
            "  echo Python 3.9 or newer is required. Opening the download page.\r\n"
            "  start https://www.python.org/downloads/windows/\r\n"
            "  pause\r\n"
            "  exit /b 1\r\n"
            ")\r\n"
            "set \"APP_DIR=%TEMP%\\lol-pace-client\"\r\n"
            "if not exist \"%APP_DIR%\\client\" mkdir \"%APP_DIR%\\client\"\r\n"
            f"curl.exe -fsSL \"{origin}/download/bridge.py\" -o \"%APP_DIR%\\client\\bridge.py\"\r\n"
            "if errorlevel 1 (\r\n"
            f"  powershell -NoProfile -Command \"Invoke-WebRequest -Uri '{origin}/download/bridge.py' -OutFile '%APP_DIR%\\client\\bridge.py'\"\r\n"
            ")\r\n"
            "pushd \"%APP_DIR%\"\r\n"
            "echo [LoL Pacemaker] Starting the always-on-top HUD. Close this window to stop.\r\n"
            f"%PY_CMD% -m client.bridge --server \"{origin}\" --insecure-local --overlay --launch-popup --repin-sec 3 --save-match --record \"%APP_DIR%\\records\"\r\n"
            "popd\r\n"
            "pause\r\n"
        )
        return Response(
            content=script.encode("utf-8"),
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="lol-pacemaker-overlay.bat"'},
        )
    # .app 은 터미널 없이 돈다(LSUIElement): 실패는 알림창으로 알리고, 기록은 launcher.log 에 남긴다 (AGENTS.md 3-6절)
    run_sh = (
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        "APP_DIR=\"$HOME/.cache/lol-pace-client\"\n"
        "mkdir -p \"$APP_DIR/client\"\n"
        "exec >>\"$APP_DIR/launcher.log\" 2>&1\n"
        "echo \"=== $(date '+%F %T') start\"\n"
        "alert() { /usr/bin/osascript -e \"display alert \\\"LoL Pacemaker\\\" message \\\"$1\\\"\" >/dev/null 2>&1 || true; }\n"
        "PY=\"$(command -v python3 || true)\"\n"
        # 새 Mac 의 /usr/bin/python3 는 명령줄 도구(CLT)를 설치해야 돈다. 없으면 설치 창을 띄우고 멈춘다
        "if [ \"$PY\" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then\n"
        "  alert 'Python 3 실행에 필요한 명령줄 도구가 없습니다. 설치 창에서 설치를 마친 뒤 앱을 다시 여세요.'\n"
        "  /usr/bin/xcode-select --install || true\n"
        "  exit 1\n"
        "fi\n"
        "if [ -z \"$PY\" ] || ! \"$PY\" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then\n"
        "  alert 'Python 3.9 이상이 필요합니다. 다운로드 페이지를 엽니다.'\n"
        "  /usr/bin/open https://www.python.org/downloads/macos/\n"
        "  exit 1\n"
        "fi\n"
        f"if ! curl -fsSL --retry 2 --connect-timeout 10 \"{origin}/download/bridge.py\" -o \"$APP_DIR/client/bridge.py.new\" \\\n"
        f"   || ! curl -fsSL --retry 2 --connect-timeout 10 \"{origin}/download/overlay_popup.swift\" -o \"$APP_DIR/client/overlay_popup.swift.new\"; then\n"
        # HUD 화면도 서버에서 받으므로 서버가 안 되면 띄울 것이 없다. 받다 만 파일로 바꾸지 않도록 .new 로 받아 옮긴다
        f"  alert '서버({origin})에 연결하지 못했습니다. 인터넷 연결이나 서버 상태를 확인하고 다시 여세요.'\n"
        "  exit 1\n"
        "fi\n"
        "mv -f \"$APP_DIR/client/bridge.py.new\" \"$APP_DIR/client/bridge.py\"\n"
        "mv -f \"$APP_DIR/client/overlay_popup.swift.new\" \"$APP_DIR/client/overlay_popup.swift\"\n"
        "cd \"$APP_DIR\"\n"
        f"exec \"$PY\" -u -m client.bridge --server \"{origin}\" --insecure-local --overlay --launch-popup --save-match\n"
    )
    info_plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        "<key>CFBundleExecutable</key><string>run</string>"
        "<key>CFBundleIdentifier</key><string>local.lolpace.launcher</string>"
        "<key>CFBundleName</key><string>LoL-Pacemaker</string>"
        "<key>LSUIElement</key><true/>"
        "</dict></plist>\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zi_run = zipfile.ZipInfo("LoL-Pacemaker.app/Contents/MacOS/run")
        zi_run.external_attr = 0o755 << 16
        zf.writestr(zi_run, run_sh.encode("utf-8"))
        zf.writestr("LoL-Pacemaker.app/Contents/Info.plist", info_plist.encode("utf-8"))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="LoL-Pacemaker-macOS.zip"'},
    )


# ── 전적 기록 (AGENTS.md 3-5절, 4절). 쓰기 경로라 AI 에이전트 도구에는 내놓지 않는다 ──
def _canon_my(v: str | None) -> str | None:
    if v is None:
        return None
    r = resolve_my(v)
    return r["champion_id"] or v.strip().upper()


def _canon_threat(v: str | None) -> str | None:
    if v is None:
        return None
    r = resolve_threat(v)
    return r["champion_id"] or r["threat"]


def _coach_summary(c: dict) -> dict:
    b = c["build_advice"]
    return {"pace": c["pace"], "state": b["state"], "posture": b["posture"],
            "top_goal": c["coaching"][0]["message"] if c["coaching"] else None,
            "item_ids": [i["item_id"] for i in b["recommended_items"]], "skill_order": b["skill_order"]["order"],
            "model_version": MODEL_VERSION}


@app.post("/matches", status_code=201, include_in_schema=False)
def save_match(m: MatchIn) -> dict:
    """전적 한 건 저장. 피처·승률·코칭 요약은 서버가 같은 입력으로 계산한다(클라이언트 값을 믿지 않는다)."""
    state = GameState(**m.model_dump(exclude={"game_time_sec", "win"}))
    c = service.coach(state)
    features = {"names": FEATURES, "values": [round(float(v), 4) for v in to_features(state)],
                "blue": m.blue.model_dump(), "red": m.red.model_dump(),
                "first_blood": m.first_blood, "my_side": m.my_side}
    saved = db.save_match(game_time_sec=m.game_time_sec, my_champion=_canon_my(m.my_champion),
                          enemy_top_threat=_canon_threat(m.enemy_top_threat), my_win_prob=float(c["my_win_prob"]),
                          win=m.win, features=features, coach=_coach_summary(c))
    return {**saved, "my_win_prob": float(c["my_win_prob"]), "model_version": MODEL_VERSION}


@app.get("/matches", include_in_schema=False)
def list_matches(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                 champion: Annotated[str | None, Query(max_length=40)] = None):
    """최근 전적 (최신순). champion 은 저장할 때와 같이 영문 ID 로 바꿔 비교한다."""
    if champion is not None:
        try:
            champion = _canon_my(champion)
        except ValueError as e:
            return JSONResponse(status_code=422, content={"detail": [
                {"loc": ["query", "champion"], "msg": str(e), "type": "value_error"}]})
    rows = db.list_matches(limit=limit, champion=champion)
    return {"count": len(rows), "matches": rows}


@app.get("/matches/export", include_in_schema=False)
def export_matches(format: Literal["json", "csv"] = "json"):  # noqa: A002  쿼리 이름이 format 이다
    """승패가 정해진 기록을 원본 CSV 와 같은 열로 (재학습 입력)."""
    rows = [db.training_row(r) for r in db.export_matches()]
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=db.EXPORT_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="matches_export.csv"'})
    return {"count": len(rows), "columns": db.EXPORT_COLUMNS, "rows": rows}

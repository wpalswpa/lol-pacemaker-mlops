"""LoL 10분 페이스메이커 API. 계약은 AGENTS.md 3·4절.

MODEL_IMPL 로 구현체를 고르고(rule·sklearn·dl·onnx), MODEL_DIR 의 모델 판을 시작할 때 한 번 읽는다.
모델 파일이 없거나 깨졌으면 여기서 예외가 나 프로세스가 뜨지 않는다 → /health 실패 → server.sh 가 이전 판으로.
"""
import importlib
import json
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from api.build import GAME, RULES
from api.contract import CoachResponse, GameState, Health, PredictResponse
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

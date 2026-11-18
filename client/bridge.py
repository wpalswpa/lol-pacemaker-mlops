"""로컬 게임 API → LoL 페이스메이커(페이커) API 브리지 (AGENTS.md 3-1·3-2절).

쓰는 곳: 관전·리플레이·연습 모드. 랭크 게임 중에는 쓰지 않는다.
읽는 것: Riot 공식 로컬 API https://127.0.0.1:2999/liveclientdata/allgamedata 의 점수판과 이벤트만.

변환 규칙 (응답 필드 → 입력 계약)
  팀       allPlayers[].team  "ORDER" = blue, "CHAOS" = red
  kills    팀 선수 scores.kills 합       assists  scores.assists 합
  cs       팀 선수 scores.creepScore 합  avg_level 팀 선수 level 평균
  dragons  events 의 DragonKill, KillerName 이 그 팀 선수인 수
  heralds  events 의 HeraldKill, KillerName 이 그 팀 선수인 수
  towers   events 의 TurretKilled. 포탑 이름 Turret_T1_* 는 블루 포탑이므로 레드가 부순 것, T2 는 블루가 부순 것
  first_blood  events 의 FirstBlood Recipient 의 팀. 아직 없으면 내 상대 팀으로 둔다(내 승률을 낮게 잡는 쪽)
  my_side  activePlayer 이름과 같은 선수의 팀. 관전처럼 activePlayer 가 없으면 --side 로 준다
  선수 이름은 riotId, riotIdGameName, summonerName 중 무엇으로 와도 맞춘다.
  my_champion       activePlayer 와 같은 선수의 챔피언. --side 로 팀을 정했으면(관전) 넣지 않는다
  enemy_top_threat  상대 팀 최다 킬 선수의 챔피언. 동률이면 최다 CS, 그래도 같으면 목록 순서
  챔피언 이름       rawChampionName "game_character_displayname_<영문 ID>" 의 영문 ID, 없으면 championName.
                    둘 다 없으면 그 칸을 넣지 않는다(서버 기본값). /coach 가 422 면 두 칸을 빼고 한 번 더 부른다
참고 값 item_gold: 팀 아이템 가격 합(Data Dragon item.json 의 gold.total, 없으면 응답의 price). 로그에만 남기고
  모델 입력으로 쓰지 않는다(학습 데이터의 총골드와 정의가 다르다).

실제 게임 클라이언트의 응답으로는 아직 확인하지 않았다. 테스트는 공개 문서의 필드 구조로 만든 고정 자료로 한다.

알림 켜고 끄기 (실행 옵션이 환경 변수보다 앞선다, AGENTS.md 3-2절)
  --effect on|off  (LOL_EFFECT, 기본 on)  상태 색 테두리가 빛나는 토스트 카드
  --tts on|off     (LOL_TTS, 기본 off)    음성 안내
  둘 다 off 면 터미널에 찍는다. 예전 옵션 --card, --voice 는 --effect on, --tts on 과 같다.

  uv run python -m client.bridge --api http://127.0.0.1:8080 --effect on --tts on
  LOL_TTS=on uv run python -m client.bridge --file client/tests/fixtures/allgamedata_10min.json --effect off

게임 중 오버레이 (AGENTS.md 3-4절). 2초마다 읽어 10분 페이스로 환산해 /coach 를 부르고,
127.0.0.1:<--listen-port>/bridge/state 로 HUD(GET /overlay)에 넘긴다. --launch-popup 이면 항상 위 팝업을 띄운다.
  uv run python -m client.bridge --server http://127.0.0.1:8083 --insecure-local --overlay --launch-popup
"""
import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import ssl
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEN_MINUTES = 600.0
POLL_SECONDS = 2.0
TEAM = {"ORDER": "blue", "CHAOS": "red"}
STATS = ("kills", "assists", "cs", "avg_level", "dragons", "heralds", "towers")
RAW_PREFIX = "game_character_displayname_"
CHAMPION_FIELDS = ("my_champion", "enemy_top_threat")
# 토스트 카드 테두리 색 (build_advice.state). 웹 시뮬레이터의 테두리 이펙트와 같은 세 색
GLOW = {"ahead": "#3ddc97", "even": "#ffd34d", "behind": "#ff4d5e"}
# 3절 허용 범위 (10분 페이스 환산 뒤 자른다)
LIMITS = {"kills": 100, "assists": 300, "cs": 2000, "dragons": 7, "heralds": 2, "towers": 11}
COACH_MIN_SECONDS = 5.0
POPUP_SIZE = (380, 560)


def _names(p: dict) -> set[str]:
    out = set()
    for key in ("riotId", "riotIdGameName", "summonerName"):
        v = p.get(key)
        if v:
            out.add(v)
            out.add(v.split("#", 1)[0])
    return out


def champion_of(p: dict) -> str | None:
    """선수의 챔피언 이름. rawChampionName 의 영문 ID 우선, 없으면 championName."""
    raw = p.get("rawChampionName") or ""
    if raw.startswith(RAW_PREFIX) and raw[len(RAW_PREFIX):]:
        return raw[len(RAW_PREFIX):]
    return p.get("championName") or None


def top_threat(players: list[dict]) -> dict | None:
    """최다 킬, 동률이면 최다 CS, 그래도 같으면 목록 순서가 앞선 선수."""
    if not players:
        return None
    return max(enumerate(players),
               key=lambda ip: (int(ip[1]["scores"]["kills"]), int(ip[1]["scores"]["creepScore"]), -ip[0]))[1]


def _item_gold(players: list[dict], prices: dict[str, float] | None) -> float:
    total = 0.0
    for p in players:
        for it in p.get("items", []):
            price = (prices or {}).get(str(it.get("itemID"))) or it.get("price") or 0
            total += float(price) * int(it.get("count", 1))
    return total


def to_game_state(data: dict, side: str | None = None, prices: dict[str, float] | None = None) -> tuple[dict, dict]:
    """allgamedata 응답을 /coach 입력(dict)으로 바꾼다. (입력, 참고 정보)"""
    players = data["allPlayers"]
    team_of = {}
    by_team: dict[str, list[dict]] = {"blue": [], "red": []}
    for p in players:
        t = TEAM[p["team"]]
        by_team[t].append(p)
        for n in _names(p):
            team_of[n] = t

    me_player = None
    if side is None:
        me = _names(data.get("activePlayer") or {})
        me_player = next((p for p in players if _names(p) & me), None)
        if me_player is None:
            raise ValueError("내 팀을 알 수 없다(관전 모드라면 --side blue|red 를 준다)")
        side = TEAM[me_player["team"]]
    enemy = "red" if side == "blue" else "blue"

    events = (data.get("events") or {}).get("Events", [])
    obj = {t: {"dragons": 0, "heralds": 0, "towers": 0} for t in ("blue", "red")}
    first_blood = None
    for e in events:
        name = e.get("EventName")
        if name in ("DragonKill", "HeraldKill"):
            t = team_of.get(e.get("KillerName", ""))
            if t:
                obj[t]["dragons" if name == "DragonKill" else "heralds"] += 1
        elif name == "TurretKilled":
            turret = e.get("TurretKilled", "")
            if turret.startswith("Turret_T1_"):
                obj["red"]["towers"] += 1
            elif turret.startswith("Turret_T2_"):
                obj["blue"]["towers"] += 1
        elif name == "FirstBlood" and first_blood is None:
            first_blood = team_of.get(e.get("Recipient", ""))

    state: dict = {"first_blood": first_blood or enemy, "my_side": side}
    for t in ("blue", "red"):
        ps = by_team[t]
        state[t] = {
            "kills": sum(int(p["scores"]["kills"]) for p in ps),
            "assists": sum(int(p["scores"]["assists"]) for p in ps),
            "cs": sum(int(p["scores"]["creepScore"]) for p in ps),
            "avg_level": sum(float(p["level"]) for p in ps) / len(ps),
            **obj[t],
        }
    threat = top_threat(by_team[enemy])
    picks = {"my_champion": champion_of(me_player) if me_player else None,
             "enemy_top_threat": champion_of(threat) if threat else None}
    state.update({k: v for k, v in picks.items() if v})
    info = {
        "game_time": float(data["gameData"]["gameTime"]),
        "first_blood_seen": first_blood is not None,
        "item_gold": {t: _item_gold(by_team[t], prices) for t in ("blue", "red")},
        "champions": picks,
    }
    return state, info


def coach_with_fallback(coach: Callable[[dict], dict], state: dict) -> dict:
    """/coach 가 챔피언 이름 때문에 422 면(새 챔피언 등) 두 칸을 빼고 한 번만 다시 부른다."""
    try:
        return coach(state)
    except urllib.error.HTTPError as e:
        if e.code != 422 or not any(k in state for k in CHAMPION_FIELDS):
            raise
        return coach({k: v for k, v in state.items() if k not in CHAMPION_FIELDS})


def wait_and_coach(fetch: Callable[[], dict], coach: Callable[[dict], dict],
                   notify: Callable[[list[str], str | None], None],
                   side: str | None = None, prices: dict | None = None,
                   sleep: Callable[[float], None] = time.sleep, max_polls: int | None = None) -> dict | None:
    """10분이 될 때까지 시간만 확인하고, 10분이 넘으면 /coach 를 한 번만 불러 알린다."""
    polls = 0
    while True:
        data = fetch()
        if float(data["gameData"]["gameTime"]) >= TEN_MINUTES:
            state, info = to_game_state(data, side, prices)
            res = coach_with_fallback(coach, state)
            # 문구는 응답의 toast 그대로, 테두리 색은 build_advice.state
            notify(res["toast"], (res.get("build_advice") or {}).get("state"))
            print(json.dumps({"message": res["message"], "item_gold(참고)": info["item_gold"],
                              "champions": info["champions"], "model_version": res["model_version"]},
                             ensure_ascii=False))
            return res
        polls += 1
        if max_polls is not None and polls >= max_polls:
            return None
        sleep(POLL_SECONDS)


# ── 게임 중 오버레이 (AGENTS.md 3-4절) ──
def _half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def pace_scale(game_time: float) -> tuple[float, float]:
    """(경과 분, 배율). 1분 전은 1분으로 본다."""
    minutes = max(float(game_time) / 60.0, 1.0)
    return minutes, 10.0 / minutes


def scale_to_ten_minutes(state: dict, game_time: float) -> dict:
    """누적 지표를 "지금 속도로 10분이 되었을 때" 값으로. avg_level·first_blood·챔피언 칸은 그대로."""
    _, k = pace_scale(game_time)
    out = dict(state)
    for t in ("blue", "red"):
        team = dict(state[t])
        for stat, hi in LIMITS.items():
            team[stat] = min(max(_half_up(team[stat] * k), 0), hi)
        team["avg_level"] = min(max(float(team["avg_level"]), 1.0), 18.0)
        out[t] = team
    return out


def extract_features_from_liveclient(data: dict, side: str | None = None,
                                     prices: dict[str, float] | None = None) -> dict:
    """allgamedata → 10분 페이스로 환산한 /coach 입력."""
    state, info = to_game_state(data, side, prices)
    return scale_to_ten_minutes(state, info["game_time"])


def _me(data: dict) -> dict | None:
    players = data.get("allPlayers") or []
    me = _names(data.get("activePlayer") or {})
    return next((p for p in players if _names(p) & me), players[0] if players else None)


def extract_owned_items(data: dict) -> list[dict]:
    """내 선수(activePlayer 와 같은 이름, 없으면 첫 선수)의 보유 아이템."""
    p = _me(data) or {}
    return [{"itemID": int(it["itemID"]), "displayName": it.get("displayName", ""),
             "count": int(it.get("count", 1)), "slot": it.get("slot")}
            for it in p.get("items", []) if "itemID" in it]


def skill_plan(order: str | None, levels: int = 18) -> list[str | None]:
    """1~levels 레벨에 찍을 스킬. 1~3레벨은 순서대로 하나씩, 6·11·16은 R, 나머지는 상한에 닿지 않은 첫 스킬."""
    keys = [k.strip() for k in order.split(">")] if order else []
    count = {k: 0 for k in keys}
    plan: list[str | None] = []
    for lv in range(1, levels + 1):
        if lv in (6, 11, 16):
            plan.append("R")
            continue
        cap = min((lv + 1) // 2, 5)  # r랭크는 레벨 2r-1 부터
        if lv <= 3:
            pick = next((k for k in keys if count[k] == 0), None)
        else:
            pick = next((k for k in keys if count[k] < cap), None)
        if pick:
            count[pick] += 1
        plan.append(pick)
    return plan


def next_skill(level: int, order: str | None, abilities: dict[str, int] | None = None) -> tuple[str | None, str]:
    """(이번에 찍을 스킬, "now" 또는 "next_level")."""
    level = min(max(int(level), 1), 18)
    plan = skill_plan(order)
    if abilities is None:
        return plan[level - 1], "now"
    seen: dict[str, int] = {}
    for k in plan[:level]:
        if k is None:
            continue
        seen[k] = seen.get(k, 0) + 1
        if abilities.get(k, 0) < seen[k]:
            return k, "now"
    return (plan[level] if level < 18 else None), "next_level"


def _abilities(data: dict) -> dict[str, int] | None:
    ab = (data.get("activePlayer") or {}).get("abilities")
    if not isinstance(ab, dict):
        return None
    return {k: int(ab[k].get("abilityLevel", 0)) for k in ("Q", "W", "E", "R") if isinstance(ab.get(k), dict)}


def _order_of(coach: dict | None) -> str | None:
    return (((coach or {}).get("build_advice") or {}).get("skill_order") or {}).get("order")


class OverlayBridge:
    """게임 API 를 읽을 때마다 상태를 갱신한다. HTTP 서버는 snapshot() 만 읽는다."""

    def __init__(self, fetch: Callable[[], dict], coach: Callable[[dict], dict], side: str | None = None,
                 prices: dict | None = None, source: str = "live", clock: Callable[[], float] = time.time):
        self.fetch, self.coach, self.side, self.prices, self.clock = fetch, coach, side, prices, clock
        self.lock = threading.Lock()
        self.last_coach_at: float | None = None
        self.last_coach_input: dict | None = None
        self.state = {"connected": False, "source": source, "in_game": False, "game_time_sec": 0.0,
                      "minutes": None, "pace_scale": None, "updated_at": None, "champion": None, "level": None,
                      "owned_items": [], "next_skill": None, "next_skill_when": None, "features": None,
                      "raw_features": None, "coach": None, "coach_error": None, "error": None}

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def step(self) -> dict:
        try:
            data = self.fetch()
            raw, info = to_game_state(data, self.side, self.prices)
        except Exception as e:  # noqa: BLE001  게임 API 가 꺼져 있어도 서버는 살아 있어야 한다
            with self.lock:
                self.state.update(connected=False, error=f"{type(e).__name__}: {e}"[:200])
            return self.snapshot()
        gt = info["game_time"]
        minutes, k = pace_scale(gt)
        features = scale_to_ten_minutes(raw, gt)
        me = _me(data) or {}
        level = int((data.get("activePlayer") or {}).get("level") or me.get("level") or 1)
        now = self.clock()

        coach, coach_error = self.state["coach"], None
        due = self.last_coach_at is None or (features != self.last_coach_input
                                             and now - self.last_coach_at > COACH_MIN_SECONDS)
        if due:
            try:
                coach = coach_with_fallback(self.coach, features)
                self.last_coach_input, self.last_coach_at = features, now
            except Exception as e:  # noqa: BLE001  직전 응답을 그대로 둔다
                coach_error = f"{type(e).__name__}: {e}"[:200]
                self.last_coach_at = now
        skill, when = next_skill(level, _order_of(coach), _abilities(data))
        with self.lock:
            self.state.update(
                connected=True, in_game=gt > 0, game_time_sec=gt, minutes=minutes, pace_scale=k, updated_at=now,
                champion=champion_of(me) if me else None, level=level, owned_items=extract_owned_items(data),
                next_skill=skill, next_skill_when=when, features=features, raw_features=raw, coach=coach,
                coach_error=coach_error if due else self.state["coach_error"], error=None)
        return self.snapshot()


def _handler(bridge: OverlayBridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Cache-Control", "no-store")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/bridge/state":
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            body = json.dumps(bridge.snapshot(), ensure_ascii=False).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # 2초마다 찍히는 접속 기록은 숨긴다
            pass

    return Handler


def serve_state(bridge: OverlayBridge, port: int = 8765) -> ThreadingHTTPServer:
    """127.0.0.1 에만 연다. 뒤쪽 스레드에서 돈다."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(bridge))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def overlay_url(server: str, port: int) -> str:
    return f"{server.rstrip('/')}/overlay?bridge_url=http://127.0.0.1:{port}/bridge/state"


SWIFT_SRC = os.path.join(os.path.dirname(__file__), "overlay_popup.swift")
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_WIN_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _find_app_browser(system: str, which: Callable[[str], str | None], exists: Callable[[str], bool]) -> str | None:
    if system == "Darwin" and exists(CHROME_MAC):
        return CHROME_MAC
    if system == "Windows":
        local_chrome = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")
        for candidate in (local_chrome, *CHROME_WIN_PATHS):
            if candidate and exists(candidate):
                return candidate
    return next((c for c in (which("google-chrome"), which("chrome"), which("chromium"), which("msedge")) if c), None)


def popup_command(url: str, system: str | None = None, which: Callable[[str], str | None] = shutil.which,
                  exists: Callable[[str], bool] = os.path.exists, build: Callable[[], str | None] | None = None
                  ) -> tuple[str, list[str] | None]:
    """(방식, 명령). 방식은 "swift"(항상 위 패널), "chrome"(--app 창), "browser"(기본 브라우저, 명령 None)."""
    system = system or platform.system()
    w, h = POPUP_SIZE
    if system == "Darwin" and which("swiftc"):
        binary = (build or build_swift_popup)()
        if binary:
            return "swift", [binary, url, str(w), str(h)]
    chrome = _find_app_browser(system, which, exists)
    if chrome:
        return "chrome", [chrome, f"--app={url}", f"--window-size={w},{h}", "--window-position=9999,0",
                          "--user-data-dir=" + os.path.join(_cache_dir(), "chrome-profile")]
    return "browser", None


def _cache_dir() -> str:
    base = os.path.expanduser("~/Library/Caches") if platform.system() == "Darwin" else (
        os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"))
    path = os.path.join(base, "lol-pace-overlay")
    os.makedirs(path, exist_ok=True)
    return path


def build_swift_popup() -> str | None:
    """client/overlay_popup.swift 를 캐시 폴더에 컴파일한다(소스가 바뀌었을 때만). 실패하면 None."""
    with open(SWIFT_SRC, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:12]
    out = os.path.join(_cache_dir(), f"overlay-popup-{digest}")
    if os.path.exists(out):
        return out
    plist = os.path.join(_cache_dir(), "Info.plist")
    with open(plist, "w", encoding="utf-8") as f:  # 웹 화면이 http://127.0.0.1 브리지를 읽을 수 있게
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
                "<key>CFBundleIdentifier</key><string>local.lolpace.overlay</string>"
                "<key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoadsInWebContent</key><true/>"
                "<key>NSAllowsLocalNetworking</key><true/></dict></dict></plist>\n")
    cmd = ["swiftc", "-O", SWIFT_SRC, "-o", out, "-framework", "Cocoa", "-framework", "WebKit",
           "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", plist]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"swiftc 실패, 다른 방식으로 띄운다: {r.stderr.strip()[:300]}", file=sys.stderr)
        return None
    return out


def _pin_windows_topmost_async(pid: int) -> None:
    """Windows 에서 띄운 --app 창을 항상 위(HWND_TOPMOST)로 고정한다."""
    if platform.system() != "Windows":
        return

    def _worker() -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd_topmost = -1
        flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        for _ in range(25):
            matched = []

            def _cb(hwnd: int, _lparam: int) -> bool:
                if user32.IsWindowVisible(hwnd):
                    proc_id = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
                    if proc_id.value == pid or user32.GetWindowTextLengthW(hwnd) > 0:
                        buf = ctypes.create_unicode_buffer(256)
                        user32.GetWindowTextW(hwnd, buf, 256)
                        if proc_id.value == pid or "LoL" in buf.value or "페이스메이커" in buf.value:
                            matched.append(hwnd)
                return True

            user32.EnumWindows(enum_proc(_cb), 0)
            if matched:
                for h in matched:
                    user32.SetWindowPos(h, hwnd_topmost, 0, 0, 0, 0, flags)
                return
            time.sleep(0.25)

    threading.Thread(target=_worker, daemon=True).start()


def launch_popup(server: str, port: int) -> str:
    """항상 위 팝업을 띄우고 쓴 방식을 돌려준다."""
    url = overlay_url(server, port)
    how, cmd = popup_command(url)
    if cmd:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        _pin_windows_topmost_async(proc.pid)
    else:
        webbrowser.open(url)
    print(f"오버레이 팝업({how}): {url}")
    return how


def run_overlay(bridge: OverlayBridge, port: int, popup_server: str | None = None,
                sleep: Callable[[float], None] = time.sleep, max_steps: int | None = None) -> None:
    httpd = serve_state(bridge, port)
    print(f"브리지 상태: http://127.0.0.1:{httpd.server_address[1]}/bridge/state (멈추려면 Ctrl+C)")
    if popup_server:
        launch_popup(popup_server, httpd.server_address[1])
    steps = 0
    try:
        while max_steps is None or steps < max_steps:
            st = bridge.step()
            steps += 1
            if st["connected"] and st["coach"]:
                mm, ss = divmod(int(st["game_time_sec"]), 60)
                print(f"\r게임 중 {mm:02d}:{ss:02d} · 승률 {st['coach']['my_win_prob'] * 100:.0f}% · "
                      f"다음 스킬 {st['next_skill'] or '-'}   ", end="", flush=True)
            sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print()
    finally:
        httpd.shutdown()


# ── 입출력 ──
def fetch_live(base: str, cafile: str | None, insecure_local: bool = False) -> dict:
    if not base.startswith(("https://127.0.0.1", "https://localhost")):
        raise ValueError("로컬 게임 API(127.0.0.1)만 읽는다")
    # 게임 클라이언트는 Riot 이 서명한 자체 인증서를 쓴다. Riot 개발자 문서의 riotgames.pem 으로 확인한다.
    # 확인을 끄는 것은 사용자가 --insecure-local 로 명시했을 때만, 그것도 이 PC(127.0.0.1) 주소에만 허용한다.
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
        ctx.check_hostname = False  # 인증서 이름이 127.0.0.1 과 다를 수 있다. 주소는 위에서 이 PC 로 제한했다
    elif insecure_local:
        ctx = ssl._create_unverified_context()
    else:
        raise ValueError("--cafile riotgames.pem 을 주거나, 이 PC 에서만 --insecure-local 을 명시한다")
    with urllib.request.urlopen(base + "/liveclientdata/allgamedata", timeout=3, context=ctx) as r:
        return json.load(r)


def post_coach(api: str, state: dict) -> dict:
    req = urllib.request.Request(api.rstrip("/") + "/coach", data=json.dumps(state).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def speak(lines: list[str]) -> None:
    """PC 내장 음성 합성으로 한 번 읽는다. 없으면 조용히 넘어간다."""
    text = ". ".join(lines).replace("·", ",")
    system = platform.system()
    if system == "Darwin" and shutil.which("say"):
        subprocess.run(["say", text], check=False)
    elif system == "Windows":
        ps = ("Add-Type -AssemblyName System.Speech; "
              "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:LOL_PACE_TEXT)")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False, env={"LOL_PACE_TEXT": text})
    elif shutil.which("espeak"):
        subprocess.run(["espeak", text], check=False)


def show_card(lines: list[str], seconds: float = 5.0, state: str | None = None) -> None:
    """우측 상단 작은 카드. 최대 3줄, seconds 뒤 사라진다(마지막 1초 동안 흐려짐). 클릭을 가로채지 않게 작게 둔다.
    테두리는 state 색(GLOW)으로 빛난다(처음 1.5초 동안 밝기가 오르내린다)."""
    lines = lines[:3]
    seconds = min(max(seconds, 3.0), 8.0)
    try:
        import tkinter as tk
    except ImportError:
        print("\n".join(lines))
        return
    glow = GLOW.get(state or "", "#5d6b7e")
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=glow, padx=3, pady=3)  # 바깥 3px 이 빛나는 테두리
    body = tk.Frame(root, bg="#11151c")
    body.pack(fill="both", expand=True)
    for i, line in enumerate(lines):
        tk.Label(body, text=line, fg="#f2f4f7" if i == 0 else "#9fb3c8", bg="#11151c",
                 font=("Helvetica", 14 if i == 0 else 12, "bold" if i == 0 else "normal"),
                 anchor="w", padx=14, pady=2 if i else 8).pack(fill="x")
    root.update_idletasks()
    w = root.winfo_reqwidth()
    root.geometry(f"+{root.winfo_screenwidth() - w - 24}+24")
    start = time.monotonic()

    def tick() -> None:
        left = seconds - (time.monotonic() - start)
        if left <= 0:
            root.destroy()
            return
        if left < 1.0:
            root.attributes("-alpha", max(left, 0.05))
        elif seconds - left < 1.5:  # 처음 1.5초: 테두리 맥박
            on = int((seconds - left) / 0.25) % 2 == 0
            root.configure(bg=glow if on else "#11151c")
        else:
            root.configure(bg=glow)
        root.after(50, tick)

    root.after(50, tick)
    root.mainloop()


_ON = {"on": True, "1": True, "true": True, "off": False, "0": False, "false": False}


def on_off(value: str) -> bool:
    v = value.strip().lower()
    if v not in _ON:
        raise ValueError(f"on 또는 off 여야 한다: {value!r}")
    return _ON[v]


def alert_switches(effect: str | None, tts: str | None, env: Mapping[str, str] = os.environ) -> tuple[bool, bool]:
    """(이펙트, 음성). 실행 옵션 > 환경 변수 LOL_EFFECT·LOL_TTS > 기본값(on, off)."""
    return (on_off(effect or env.get("LOL_EFFECT") or "on"),
            on_off(tts or env.get("LOL_TTS") or "off"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="10분에 한 번 페이스·목표를 알려 준다")
    ap.add_argument("--api", "--server", dest="api", default="http://127.0.0.1:8080", help="API 서버 주소")
    ap.add_argument("--live", default="https://127.0.0.1:2999")
    ap.add_argument("--cafile", help="Riot 로컬 API 인증서(riotgames.pem)")
    ap.add_argument("--insecure-local", action="store_true", help="인증서 확인 없이 127.0.0.1 만 읽기")
    ap.add_argument("--file", help="저장한 allgamedata JSON (게임 없이 시연)")
    ap.add_argument("--side", choices=["blue", "red"])
    ap.add_argument("--ddragon", help="Data Dragon item.json (아이템 골드 참고 값)")
    ap.add_argument("--effect", choices=["on", "off"], help="테두리 빛나는 토스트 카드 (기본 on, 환경 변수 LOL_EFFECT)")
    ap.add_argument("--tts", choices=["on", "off"], help="음성 안내 (기본 off, 환경 변수 LOL_TTS)")
    ap.add_argument("--card", dest="effect", action="store_const", const="on", help="--effect on 과 같다")
    ap.add_argument("--voice", dest="tts", action="store_const", const="on", help="--tts on 과 같다")
    ap.add_argument("--card-seconds", type=float, default=5.0)
    ap.add_argument("--overlay", action="store_true", help="게임 중 계속 판단해 /bridge/state 로 HUD 에 넘긴다")
    ap.add_argument("--listen-port", type=int, default=8765, help="오버레이 상태 서버 포트 (127.0.0.1)")
    ap.add_argument("--launch-popup", action="store_true", help="항상 위 오버레이 팝업 창을 띄운다 (--overlay 포함)")
    a = ap.parse_args(argv)
    try:
        effect_on, tts_on = alert_switches(a.effect, a.tts)
    except ValueError as e:
        ap.error(f"LOL_EFFECT·LOL_TTS: {e}")

    prices = None
    if a.ddragon:
        with open(a.ddragon, encoding="utf-8") as f:
            prices = {k: v["gold"]["total"] for k, v in json.load(f)["data"].items()}
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            saved = json.load(f)
        fetch = lambda: saved  # noqa: E731
        max_polls = 1
    else:
        fetch = lambda: fetch_live(a.live, a.cafile, a.insecure_local)  # noqa: E731
        max_polls = None

    if a.overlay or a.launch_popup:
        bridge = OverlayBridge(fetch, lambda s: post_coach(a.api, s), a.side, prices,
                               source="file" if a.file else "live")
        run_overlay(bridge, a.listen_port, a.api if a.launch_popup else None)
        return 0

    def notify(lines: list[str], state: str | None = None) -> None:
        if tts_on:
            speak(lines)
        if effect_on:
            show_card(lines, a.card_seconds, state)
        if not (tts_on or effect_on):
            print("\n".join(lines))

    res = wait_and_coach(fetch, lambda s: post_coach(a.api, s), notify, a.side, prices, max_polls=max_polls)
    if res is None:
        print("아직 10분 전이다")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

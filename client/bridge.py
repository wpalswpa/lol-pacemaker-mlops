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
  enemy_items       상대 위협 선수의 items[].itemID 중 정수이고 1~999999 인 것, 앞에서 12개(Tab 점수판 공개 값).
                    없으면 칸을 넣지 않는다. 422 로 다시 부를 때는 이 칸도 뺀다
  덜 찬 응답        연습 모드·리플레이 시작(gameTime 0.0, abilities {}, items [])에도 멈추지 않는다. 숫자가 아닌
                    itemID·count·abilityLevel·점수는 건너뛰거나 0. 상대 팀 선수가 없으면(연습 모드 혼자) 그 팀은
                    킬·어시·CS·오브젝트 0, avg_level 은 내 팀 평균과 같게 둔다(레벨 차이 0)
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
  uv run python -m client.bridge --server http://127.0.0.1:8083 --file client/tests/fixtures/allgamedata_10min.json --overlay --save-match
"""
from __future__ import annotations  # macOS 기본 python3(3.9)에서도 돌게 (X | None 표기)

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
# 422 때 빼고 다시 부르는 선택 칸 (빌드 가이드에만 쓰는 칸)
OPTIONAL_BUILD_FIELDS = CHAMPION_FIELDS + ("enemy_items",)
ITEM_ID_MAX = 999999  # 3절 enemy_items 허용 범위
ENEMY_ITEMS_MAX = 12
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


def _int(v, default: int = 0) -> int:
    """게임 API 값을 정수로. None·빈 문자열·숫자가 아닌 값이면 default (덜 찬 응답에도 멈추지 않는다)."""
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def _num(v, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _score(p: dict, key: str) -> int:
    return _int((p.get("scores") or {}).get(key))


def _items(p: dict | None) -> list[dict]:
    return [it for it in ((p or {}).get("items") or []) if isinstance(it, dict)]


def top_threat(players: list[dict]) -> dict | None:
    """최다 킬, 동률이면 최다 CS, 그래도 같으면 목록 순서가 앞선 선수."""
    if not players:
        return None
    return max(enumerate(players),
               key=lambda ip: (_score(ip[1], "kills"), _score(ip[1], "creepScore"), -ip[0]))[1]


def enemy_item_ids(player: dict | None) -> list[int]:
    """상대 위협 선수의 공개 아이템 ID. 정수로 읽히고 1~999999 인 것만, 앞에서 12개."""
    ids = [_int(it.get("itemID")) for it in _items(player)]
    return [i for i in ids if 1 <= i <= ITEM_ID_MAX][:ENEMY_ITEMS_MAX]


def coach_input(state: dict, info: dict) -> dict:
    """/coach 본문: 점수판 입력 + 공개 아이템이 있으면 enemy_items."""
    return {**state, "enemy_items": info["enemy_items"]} if info.get("enemy_items") else dict(state)


def _item_gold(players: list[dict], prices: dict[str, float] | None) -> float:
    total = 0.0
    for p in players:
        for it in _items(p):
            price = (prices or {}).get(str(it.get("itemID"))) or it.get("price") or 0
            total += _num(price) * _int(it.get("count", 1), 1)
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
    levels = {t: [_num(p.get("level"), 1.0) for p in by_team[t]] for t in ("blue", "red")}
    for t in ("blue", "red"):
        ps = by_team[t]
        other = levels["red" if t == "blue" else "blue"]
        lv = levels[t] or other or [1.0]  # 선수가 없는 팀(연습 모드 혼자)은 상대 평균과 같게: 레벨 차이 0
        state[t] = {
            "kills": sum(_score(p, "kills") for p in ps),
            "assists": sum(_score(p, "assists") for p in ps),
            "cs": sum(_score(p, "creepScore") for p in ps),
            "avg_level": sum(lv) / len(lv),
            **obj[t],
        }
    threat = top_threat(by_team[enemy])
    picks = {"my_champion": champion_of(me_player) if me_player else None,
             "enemy_top_threat": champion_of(threat) if threat else None}
    state.update({k: v for k, v in picks.items() if v})
    enemy_items = enemy_item_ids(threat)
    info = {
        "game_time": _num(data["gameData"]["gameTime"]),
        "first_blood_seen": first_blood is not None,
        "item_gold": {t: _item_gold(by_team[t], prices) for t in ("blue", "red")},
        "champions": picks,
        "enemy_items": enemy_items,
    }
    return state, info


def coach_with_fallback(coach: Callable[[dict], dict], state: dict) -> dict:
    """/coach 가 챔피언 이름 때문에 422 면(새 챔피언 등) 두 칸과 enemy_items 를 빼고 한 번만 다시 부른다."""
    try:
        return coach(state)
    except urllib.error.HTTPError as e:
        if e.code != 422 or not any(k in state for k in OPTIONAL_BUILD_FIELDS):
            raise
        return coach({k: v for k, v in state.items() if k not in OPTIONAL_BUILD_FIELDS})


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
            res = coach_with_fallback(coach, coach_input(state, info))
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
    return [{"itemID": _int(it["itemID"]), "displayName": it.get("displayName", ""),
             "count": _int(it.get("count", 1), 1), "slot": it.get("slot")}
            for it in _items(p) if _int(it.get("itemID")) > 0]


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
    return {k: _int(ab[k].get("abilityLevel")) for k in ("Q", "W", "E", "R") if isinstance(ab.get(k), dict)}


def _order_of(coach: dict | None) -> str | None:
    return (((coach or {}).get("build_advice") or {}).get("skill_order") or {}).get("order")


def game_result(data: dict) -> bool | None:
    """이벤트의 GameEnd 로 내 팀(활성 플레이어 기준) 승패. 아직 안 끝났으면 None."""
    for ev in (data.get("events") or {}).get("Events") or []:
        if ev.get("EventName") == "GameEnd" and ev.get("Result") in ("Win", "Lose"):
            return ev["Result"] == "Win"
    return None


def match_payload(features: dict, game_time: float, win: bool | None) -> dict:
    """POST /matches 본문 (AGENTS.md 3-5절). 3절 입력 칸 + 게임 시각 + 승패만. 소환사명 등은 넣지 않는다."""
    return {**features, "game_time_sec": int(min(max(game_time, 0), 7200)), "win": win}


class OverlayBridge:
    """게임 API 를 읽을 때마다 상태를 갱신한다. HTTP 서버는 snapshot() 만 읽는다.
    save 를 주면(--save-match) 경기가 끝났을 때(GameEnd) 한 번, 저장한 응답(--file)이면 처음 읽었을 때 한 번 POST /matches."""

    def __init__(self, fetch: Callable[[], dict], coach: Callable[[dict], dict], side: str | None = None,
                 prices: dict | None = None, source: str = "live", clock: Callable[[], float] = time.time,
                 save: Callable[[dict], dict] | None = None):
        self.fetch, self.coach, self.side, self.prices, self.clock = fetch, coach, side, prices, clock
        self.source, self.save = source, save
        self.saved = False
        self.last_save_try: float | None = None
        self.lock = threading.Lock()
        self.last_coach_at: float | None = None
        self.last_coach_input: dict | None = None
        self.state = {"connected": False, "source": source, "in_game": False, "game_time_sec": 0.0,
                      "minutes": None, "pace_scale": None, "updated_at": None, "champion": None, "level": None,
                      "owned_items": [], "next_skill": None, "next_skill_when": None, "features": None,
                      "raw_features": None, "enemy_items": [], "coach": None, "coach_error": None, "error": None,
                      "saved_match": None, "save_error": None, "popup": None}

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def set_popup(self, popup: dict) -> None:
        """팝업 방식과 Windows 항상 위 고정 결과 (AGENTS.md 3-4절 "팝업"). 고정 스레드가 부른다."""
        with self.lock:
            self.state["popup"] = dict(popup)

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
        level = _int((data.get("activePlayer") or {}).get("level") or me.get("level"), 1)
        now = self.clock()

        body = coach_input(features, info)
        coach, coach_error = self.state["coach"], None
        due = self.last_coach_at is None or (body != self.last_coach_input
                                             and now - self.last_coach_at > COACH_MIN_SECONDS)
        if due:
            try:
                coach = coach_with_fallback(self.coach, body)
                self.last_coach_input, self.last_coach_at = body, now
            except Exception as e:  # noqa: BLE001  직전 응답을 그대로 둔다
                coach_error = f"{type(e).__name__}: {e}"[:200]
                self.last_coach_at = now
        skill, when = next_skill(level, _order_of(coach), _abilities(data))
        saved_match, save_error = self.state["saved_match"], self.state["save_error"]
        if self.save and not self.saved:
            result = game_result(data)
            ready = result is not None or self.source == "file"
            if ready and (self.last_save_try is None or now - self.last_save_try > COACH_MIN_SECONDS):
                self.last_save_try = now
                try:
                    out = coach_with_fallback(self.save, match_payload(features, gt, result))
                    self.saved, save_error = True, None
                    saved_match = {"id": out.get("id"), "created_at": out.get("created_at"), "win": result}
                except Exception as e:  # noqa: BLE001  저장이 안 돼도 코칭은 계속한다. 5초 뒤 다시
                    save_error = f"{type(e).__name__}: {e}"[:200]
        with self.lock:
            self.state.update(
                connected=True, in_game=gt > 0, game_time_sec=gt, minutes=minutes, pace_scale=k, updated_at=now,
                champion=champion_of(me) if me else None, level=level, owned_items=extract_owned_items(data),
                next_skill=skill, next_skill_when=when, features=features, raw_features=raw,
                enemy_items=info["enemy_items"], coach=coach,
                coach_error=coach_error if due else self.state["coach_error"], error=None,
                saved_match=saved_match, save_error=save_error)
        return self.snapshot()


# 브리지가 같은 출처(127.0.0.1)에서 HUD 를 내주기 위해 서버로 넘겨 주는 경로 (AGENTS.md 3-4절 "팝업").
# 공개 주소의 웹 페이지는 브라우저의 로컬 네트워크 접근 제한 때문에 127.0.0.1 브리지를 읽지 못한다(Chromium 159 확인).
RELAY = {("GET", "/overlay"), ("GET", "/meta/champions"), ("POST", "/coach"), ("POST", "/coach/briefing")}
RELAY_MAX_BODY = 64 * 1024
Upstream = Callable[[str, str, "bytes | None"], "tuple[int, str, bytes]"]


def http_upstream(server: str, timeout: float = 5.0) -> Upstream:
    """(메서드, 경로, 본문) → (상태 코드, Content-Type, 본문). 4xx·5xx 도 그대로 돌려준다."""
    def call(method: str, path: str, body: bytes | None) -> tuple[int, str, bytes]:
        req = urllib.request.Request(server.rstrip("/") + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", "application/json"), e.read()
    return call


def _handler(bridge: OverlayBridge, upstream: Upstream | None = None) -> type[BaseHTTPRequestHandler]:
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

        def _send(self, code: int, ctype: str | None = None, body: bytes = b"", cors: bool = True) -> None:
            self.send_response(code)
            if cors:
                self._cors()
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _relay(self, method: str) -> bool:
            """화이트리스트 경로만 서버로 넘긴다. 넘겼으면 True."""
            path = self.path.split("?", 1)[0]
            if upstream is None or (method, path) not in RELAY:
                return False
            body = None
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > RELAY_MAX_BODY:
                    self._send(413, cors=False)
                    return True
                body = self.rfile.read(n)
            try:
                code, ctype, data = upstream(method, path, body)
            except (urllib.error.URLError, OSError, ValueError):
                code, ctype, data = 502, "application/json; charset=utf-8", \
                    json.dumps({"detail": "서버에 연결하지 못했다"}, ensure_ascii=False).encode()
            self._send(code, ctype, data, cors=False)   # 같은 출처에서만 쓰므로 CORS 를 열지 않는다
            return True

        def do_POST(self) -> None:  # noqa: N802
            if not self._relay("POST"):
                self._send(404)

        def do_GET(self) -> None:  # noqa: N802
            if self._relay("GET"):
                return
            if self.path.split("?", 1)[0] != "/bridge/state":
                self._send(404)
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


def serve_state(bridge: OverlayBridge, port: int = 8765, upstream: Upstream | None = None) -> ThreadingHTTPServer:
    """127.0.0.1 에만 연다. 뒤쪽 스레드에서 돈다. upstream 을 주면 /overlay·/meta/champions·POST /coach 를 서버로 넘긴다."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(bridge, upstream))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def overlay_url(port: int) -> str:
    """팝업 주소. HUD 를 브리지(127.0.0.1)가 내주므로 상태 읽기가 같은 출처다."""
    return f"http://127.0.0.1:{port}/overlay?bridge_url=http://127.0.0.1:{port}/bridge/state"


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
        return "chrome", [chrome, f"--app={url}", f"--window-size={w},{h}", "--window-position=24,220",
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


# Windows --app 창 항상 위 고정 (AGENTS.md 3-4절 "Windows 항상 위 고정")
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
PIN_FLAGS = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
OVERLAY_TITLES = ("페이커 오버레이", "LoL 페이스메이커")
CHROME_CLASS = "Chrome_WidgetWin_1"
PIN_TRIES, PIN_INTERVAL = 25, 0.25


class Win32Windows:
    """user32 위의 얇은 껍데기. Windows 에서는 ctypes.windll.user32 와 ctypes, 테스트는 가짜 둘을 넣는다."""

    def __init__(self, user32, ct):
        self.u, self.ct = user32, ct

    def visible(self) -> list:
        out = []
        w = self.ct.wintypes
        proto = self.ct.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def cb(hwnd, _lparam):
            if self.u.IsWindowVisible(hwnd):
                out.append(hwnd)
            return True

        self.u.EnumWindows(proto(cb), 0)
        return out

    def pid(self, hwnd) -> int:
        d = self.ct.wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, self.ct.byref(d))
        return int(d.value)

    def _text(self, fn, hwnd, n: int) -> str:
        buf = self.ct.create_unicode_buffer(n)
        fn(hwnd, buf, n)
        return buf.value or ""

    def title(self, hwnd) -> str:
        return self._text(self.u.GetWindowTextW, hwnd, 512)

    def class_name(self, hwnd) -> str:
        return self._text(self.u.GetClassNameW, hwnd, 256)

    def pin(self, hwnd) -> bool:
        return bool(self.u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, PIN_FLAGS))


def find_overlay_windows(win: Win32Windows, pid: int, port: int) -> tuple[str | None, list]:
    """(찾은 단계 "pid"·"title"·"class", 창 목록). PID → 제목 → 클래스명 + 주소 제목 순서."""
    shown = win.visible()
    by_pid = [h for h in shown if win.pid(h) == pid]
    if by_pid:
        return "pid", by_pid
    by_title = [h for h in shown if any(t in win.title(h) for t in OVERLAY_TITLES)]
    if by_title:
        return "title", by_title
    hint = f"127.0.0.1:{port}"
    by_class = [h for h in shown if win.class_name(h) == CHROME_CLASS and hint in win.title(h)]
    if by_class:
        return "class", by_class
    return None, []


def pin_topmost(win: Win32Windows, pid: int, port: int, report: Callable[[dict], None],
                sleep: Callable[[float], None] = time.sleep, repin_sec: float = 0.0,
                tries: int = PIN_TRIES, max_repins: int | None = None) -> None:
    """창을 찾아 항상 위로 고정하고 결과를 report 로 알린다. repin_sec > 0 이면 그 간격으로 다시 고정."""
    report({"status": "searching"})
    how, hwnds = None, []
    for i in range(tries):
        how, hwnds = find_overlay_windows(win, pid, port)
        if hwnds:
            break
        if i < tries - 1:
            sleep(PIN_INTERVAL)
    if not hwnds:
        report({"status": "failed", "method": None, "windows": 0, "repins": 0,
                "error": f"오버레이 창을 찾지 못했다(PID {pid}, 제목 {'·'.join(OVERLAY_TITLES)}, "
                         f"클래스 {CHROME_CLASS} + 127.0.0.1:{port}, {tries}번)"})
        return
    pinned = [h for h in hwnds if win.pin(h)]
    report({"status": "pinned" if pinned else "failed", "method": how, "windows": len(pinned), "repins": 0,
            "error": None if pinned else "SetWindowPos 가 실패했다"})
    n = 0
    while repin_sec > 0 and (max_repins is None or n < max_repins):
        sleep(repin_sec)
        n += 1
        how, hwnds = find_overlay_windows(win, pid, port)
        if not hwnds:
            report({"status": "lost", "method": None, "windows": 0, "repins": n,
                    "error": "다시 고정할 오버레이 창을 찾지 못했다(창을 닫았을 수 있다)"})
            continue
        pinned = [h for h in hwnds if win.pin(h)]
        report({"status": "pinned" if pinned else "failed", "method": how, "windows": len(pinned), "repins": n,
                "error": None if pinned else "SetWindowPos 가 실패했다"})


def _pin_windows_topmost_async(pid: int, port: int, report: Callable[[dict], None], repin_sec: float = 0.0,
                               system: str | None = None) -> bool:
    """Windows 에서만 백그라운드로 pin_topmost. 다른 OS 는 windll 을 건드리지 않고 False."""
    if (system or platform.system()) != "Windows":
        return False

    def _worker() -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            # 64비트에서 HWND_TOPMOST(-1)가 포인터 크기로 넘어가게 인자 형을 정한다
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            pin_topmost(Win32Windows(user32, ctypes), pid, port, report, repin_sec=repin_sec)
        except Exception as e:  # noqa: BLE001  조용히 죽지 않고 상태에 남긴다
            report({"status": "failed", "error": f"{type(e).__name__}: {e}"[:200]})

    threading.Thread(target=_worker, daemon=True).start()
    return True


def launch_popup(port: int, report: Callable[[dict], None] | None = None, repin_sec: float = 0.0,
                 system: str | None = None) -> str:
    """항상 위 팝업을 띄우고 쓴 방식을 돌려준다. 고정 결과는 report(브리지 상태의 popup)로 남긴다."""
    system = system or platform.system()
    url = overlay_url(port)
    how, cmd = popup_command(url, system)
    state: dict = {"how": how, "status": "not_windows", "method": None, "windows": 0, "repins": 0, "error": None}

    def note(update: dict) -> None:
        state.update(update)
        if report:
            report(dict(state))
        if update.get("status") in ("failed", "lost") and update.get("error"):
            print(f"\n항상 위 고정 {update['status']}: {update['error']}", file=sys.stderr)

    if cmd:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if how == "chrome" and system == "Windows":
            _pin_windows_topmost_async(proc.pid, port, note, repin_sec, system)
        else:
            note({})
    else:
        webbrowser.open(url)
        note({"status": "failed", "error": "Chrome·Edge 가 없어 항상 위로 고정할 수 없다"} if system == "Windows" else {})
    print(f"오버레이 팝업({how}): {url}")
    return how


def run_overlay(bridge: OverlayBridge, port: int, server: str | None = None, popup: bool = False,
                sleep: Callable[[float], None] = time.sleep, max_steps: int | None = None,
                repin_sec: float = 0.0) -> None:
    httpd = serve_state(bridge, port, http_upstream(server) if server else None)
    real = httpd.server_address[1]
    print(f"브리지 상태: http://127.0.0.1:{real}/bridge/state (멈추려면 Ctrl+C)")
    if server:
        print(f"HUD: {overlay_url(real)}")
    if popup:
        launch_popup(real, bridge.set_popup, repin_sec)
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


def post_json(api: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(api.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def post_coach(api: str, state: dict) -> dict:
    return post_json(api, "/coach", state)


def post_match(api: str, payload: dict) -> dict:
    """전적 저장 (AGENTS.md 3-5절). 201 이면 {"id", "created_at", ...}."""
    return post_json(api, "/matches", payload)


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
    ap.add_argument("--repin-sec", type=float, default=0.0,
                    help="Windows 에서 N초마다 팝업을 다시 항상 위로 고정 (기본 0 = 끔)")
    ap.add_argument("--save-match", action="store_true",
                    help="오버레이 모드에서 경기가 끝나면(--file 이면 바로) 전처리 피처를 POST /matches 로 한 번 저장")
    a = ap.parse_args(argv)
    if a.save_match and not (a.overlay or a.launch_popup):
        ap.error("--save-match 는 --overlay(또는 --launch-popup)와 함께 쓴다")
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
                               source="file" if a.file else "live",
                               save=(lambda p: post_match(a.api, p)) if a.save_match else None)
        run_overlay(bridge, a.listen_port, a.api, popup=a.launch_popup, repin_sec=a.repin_sec)
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

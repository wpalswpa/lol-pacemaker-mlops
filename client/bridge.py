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
STRATEGY_MIN_SECONDS = 3.0
# 게임 API 가 이만큼 계속 끊겨야 경기가 끝난 것으로 보고 win=null 로 저장한다(한두 번의 시간 초과로 미리 저장하지 않게)
DISCONNECT_SAVE_SEC = 15.0
# 게임 시각이 이만큼 이상 되돌아가면 새 게임이다 (챔피언 10명 구성이 바뀌어도 새 게임)
NEW_GAME_REWIND_SEC = 5.0
POPUP_SIZE = (380, 560)
RECENT_KILL_WINDOW_SEC = 180.0
LOL_WINDOW_TITLES = ("League of Legends (TM) Client",)
LOL_GAME_CFG_PATHS = (
    r"C:\Riot Games\League of Legends\Config\Game.cfg",
    r"D:\Riot Games\League of Legends\Config\Game.cfg",
)
UPSTREAM_PATH_TIMEOUTS = {"/strategy/briefing": 15.0}


def _compute_bridge_version() -> str:
    try:
        with open(__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "unknown"


BRIDGE_VERSION = _compute_bridge_version()


def _names(p: dict) -> set[str]:
    out = set()
    for key in ("riotId", "riotIdGameName", "summonerName"):
        v = str(p.get(key) or "").strip()
        if v:
            out.add(v)
            head = v.split("#", 1)[0].strip()
            if head:
                out.add(head)
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


def _fetch_json_url(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def load_item_prices(server: str | None = None, cache_path: str | None = None,
                     fetch_json: Callable[[str], object] | None = None,
                     ttl_sec: float = 7 * 86400) -> dict[str, float]:
    """W1: 서버 /meta/items 또는 Data Dragon item.json 에서 가격표를 받아 캐시한다. 실패 시 빈 dict(items[].price 폴백)."""
    path = cache_path or os.path.join(_cache_dir(), "item-prices.json")
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl_sec:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and cached:
                return {str(k): float(v) for k, v in cached.items() if _num(v, -1.0) >= 0}
    except (OSError, ValueError, TypeError):
        pass

    getter = fetch_json or _fetch_json_url
    prices: dict[str, float] = {}
    if server:
        try:
            res = getter(server.rstrip("/") + "/meta/items")
            raw = res.get("prices", res) if isinstance(res, dict) else {}
            if isinstance(raw, dict):
                prices = {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float)) and v >= 0}
        except Exception:  # noqa: BLE001
            prices = {}
    if not prices:
        try:
            vers = getter("https://ddragon.leagueoflegends.com/api/versions.json")
            if isinstance(vers, list) and vers and isinstance(vers[0], str):
                dd = getter(f"https://ddragon.leagueoflegends.com/cdn/{vers[0]}/data/en_US/item.json")
                data = dd.get("data") if isinstance(dd, dict) else None
                if isinstance(data, dict):
                    prices = {
                        str(k): float(v["gold"]["total"])
                        for k, v in data.items()
                        if isinstance(v, dict) and isinstance(v.get("gold"), dict) and isinstance(v["gold"].get("total"), (int, float))
                    }
        except Exception:  # noqa: BLE001
            prices = {}
    if prices:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(prices, f)
        except OSError:
            pass
    return prices


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


# 시각 경고 (AGENTS.md 3-4절 "시각 경고", 근거 docs/pace_scaling_analysis.md 5절). HUD 데모도 같은 경계·문구를 쓴다
PACE_EARLY_END, PACE_LATE_FROM, PACE_VERY_LATE_FROM = 450.0, 720.0, 1200.0
PACE_TEXT = {
    "early": "초반 환산 ×{scale} · 킬 1개가 10분보다 크게 반영돼요",
    "late": "12분 이후 · 10분 모델 범위 밖(레벨) · 승률은 참고만",
    "very_late": "20분 이후 · 포탑 차이도 범위 밖 · 우세·열세 방향만 보세요",
}


def pace_warning(game_time: float) -> dict | None:
    """경과 초 → {"level", "text"} 또는 None. 경계 시각은 그 구간에 들어간다."""
    t = float(game_time)
    if t < PACE_EARLY_END:
        return {"level": "early", "text": PACE_TEXT["early"].format(scale=f"{pace_scale(t)[1]:.1f}")}
    if t >= PACE_VERY_LATE_FROM:
        return {"level": "very_late", "text": PACE_TEXT["very_late"]}
    if t >= PACE_LATE_FROM:
        return {"level": "late", "text": PACE_TEXT["late"]}
    return None


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


def _turret_lane(turret_name: str) -> str:
    """Turret_T1_L_03_A -> top, _C_ -> mid, _R_ -> bot."""
    if "_L_" in turret_name:
        return "top"
    if "_C_" in turret_name:
        return "mid"
    if "_R_" in turret_name:
        return "bot"
    return "other"


def _has_smite(p: dict) -> bool:
    """선수의 소환사 주문에 강타(SummonerSmite)가 있는지 판별한다(H1)."""
    spells = p.get("summonerSpells")
    if not isinstance(spells, dict):
        return False
    for key in ("summonerSpellOne", "summonerSpellTwo"):
        sp = spells.get(key)
        if isinstance(sp, dict):
            raw = str(sp.get("rawDisplayName") or "")
            disp = str(sp.get("displayName") or "")
            if "smite" in raw.lower() or "강타" in disp or "smite" in disp.lower():
                return True
    return False


def _live_player_dict(p: dict, prices: dict[str, float] | None, current_gold: float = 0.0) -> dict:
    item_ids = enemy_item_ids(p)
    return {
        "champion": champion_of(p),
        "position": str(p.get("position") or "")[:20],
        "has_smite": _has_smite(p),
        "level": max(1, min(18, _int(p.get("level"), 1))),
        "kills": max(0, min(200, _score(p, "kills"))),
        "deaths": max(0, min(200, _score(p, "deaths"))),
        "assists": max(0, min(400, _score(p, "assists"))),
        "cs": max(0, min(3000, _score(p, "creepScore"))),
        "current_gold": max(0.0, min(200000.0, _num(current_gold))),
        "item_gold": max(0.0, min(200000.0, _item_gold([p], prices))),
        "items": item_ids,
        "is_dead": bool(p.get("isDead", False)),
        "respawn_timer": max(0.0, min(300.0, _num(p.get("respawnTimer")))),
    }


def extract_live_context(data: dict, side: str | None = None, prices: dict[str, float] | None = None) -> dict:
    """allgamedata 에서 10인 전원·전 라인·골드·아이템·오브젝트·생존·모멘텀을 추출한다 (/strategy 입력)."""
    players = data.get("allPlayers") or []
    team_of: dict[str, str] = {}
    by_team: dict[str, list[dict]] = {"blue": [], "red": []}
    for p in players:
        t = TEAM.get(p.get("team", ""))
        if not t or not champion_of(p):  # W2: 챔피언을 알 수 없는 항목(허수아비 등)은 allies·enemies 에서 제외
            continue
        by_team[t].append(p)
        for n in _names(p):
            team_of[n] = t

    active = data.get("activePlayer") or {}
    me_names = _names(active)
    me_player = next((p for p in players if _names(p) & me_names), None)
    if side is None:
        if me_player is None:
            raise ValueError("내 팀을 알 수 없다(관전 모드라면 --side blue|red 를 준다)")
        side = TEAM[me_player["team"]]
    elif me_player is None and by_team[side]:
        me_player = by_team[side][0]
    enemy = "red" if side == "blue" else "blue"

    gt = max(0.0, min(7200.0, _num((data.get("gameData") or {}).get("gameTime"))))
    events = (data.get("events") or {}).get("Events") or []
    obj = {t: {"dragons": 0, "elders": 0, "grubs": 0, "heralds": 0, "barons": 0, "towers": 0, "inhibs": 0}
           for t in ("blue", "red")}
    lost_turrets = {"blue": {"top": 0, "mid": 0, "bot": 0}, "red": {"top": 0, "mid": 0, "bot": 0}}
    first_blood = None
    last_dragon_sec = None
    last_elder_sec = None
    last_elder_team = None
    last_baron_sec = None
    last_baron_team = None
    recent_ally_kills = 0
    recent_enemy_kills = 0

    for e in events:
        if not isinstance(e, dict):
            continue
        name = e.get("EventName")
        ev_t = _num(e.get("EventTime"), -1.0)
        if name == "DragonKill":
            t = team_of.get(e.get("KillerName", ""))
            dtype = str(e.get("DragonType") or "").lower()
            if dtype == "elder":
                if t:
                    obj[t]["elders"] += 1
                    if ev_t >= 0:
                        last_elder_sec = ev_t
                        last_elder_team = "ally" if t == side else "enemy"
            else:
                if t:
                    obj[t]["dragons"] += 1
                if ev_t >= 0:
                    last_dragon_sec = ev_t
        elif name == "HordeKill":
            t = team_of.get(e.get("KillerName", ""))
            if t:
                obj[t]["grubs"] += 1
        elif name == "HeraldKill":
            t = team_of.get(e.get("KillerName", ""))
            if t:
                obj[t]["heralds"] += 1
        elif name == "BaronKill":
            t = team_of.get(e.get("KillerName", ""))
            if t:
                obj[t]["barons"] += 1
                if ev_t >= 0:
                    last_baron_sec = ev_t
                    last_baron_team = "ally" if t == side else "enemy"
        elif name == "TurretKilled":
            turret = str(e.get("TurretKilled") or "")
            lane = _turret_lane(turret)
            if turret.startswith("Turret_T1_"):
                obj["red"]["towers"] += 1
                if lane in lost_turrets["blue"]:
                    lost_turrets["blue"][lane] += 1
            elif turret.startswith("Turret_T2_"):
                obj["blue"]["towers"] += 1
                if lane in lost_turrets["red"]:
                    lost_turrets["red"][lane] += 1
        elif name == "InhibKilled":
            inhib = str(e.get("InhibKilled") or "")
            if inhib.startswith("Barracks_T1_"):
                obj["red"]["inhibs"] += 1
            elif inhib.startswith("Barracks_T2_"):
                obj["blue"]["inhibs"] += 1
        elif name == "FirstBlood" and first_blood is None:
            first_blood = team_of.get(e.get("Recipient", ""))
        elif name == "ChampionKill" and ev_t >= 0 and (gt - ev_t) <= RECENT_KILL_WINDOW_SEC:
            killer_team = team_of.get(e.get("KillerName", ""))
            if killer_team == side:
                recent_ally_kills += 1
            elif killer_team == enemy:
                recent_enemy_kills += 1

    cur_gold = max(0.0, min(200000.0, _num(active.get("currentGold"))))
    me_dict = _live_player_dict(me_player, prices, cur_gold) if me_player else {
        "champion": None, "position": "", "has_smite": False, "level": max(1, min(18, _int(active.get("level"), 1))),
        "kills": 0, "deaths": 0, "assists": 0, "cs": 0, "current_gold": cur_gold,
        "item_gold": 0.0, "items": [], "is_dead": False, "respawn_timer": 0.0,
    }
    if active.get("level") is not None:
        me_dict["level"] = max(1, min(18, _int(active.get("level"), me_dict["level"])))

    allies_list = [_live_player_dict(p, prices, cur_gold if p is me_player else 0.0) for p in by_team[side][:5]]
    enemies_list = [_live_player_dict(p, prices, 0.0) for p in by_team[enemy][:5]]

    return {
        "game_time_sec": gt,
        "my_side": side,
        "first_blood": first_blood,
        "me": me_dict,
        "allies": allies_list,
        "enemies": enemies_list,
        "objectives": {
            "my_dragons": min(10, obj[side]["dragons"]),
            "enemy_dragons": min(10, obj[enemy]["dragons"]),
            "my_elders": min(10, obj[side]["elders"]),
            "enemy_elders": min(10, obj[enemy]["elders"]),
            "my_grubs": min(12, obj[side]["grubs"]),
            "enemy_grubs": min(12, obj[enemy]["grubs"]),
            "my_heralds": min(4, obj[side]["heralds"]),
            "enemy_heralds": min(4, obj[enemy]["heralds"]),
            "my_barons": min(10, obj[side]["barons"]),
            "enemy_barons": min(10, obj[enemy]["barons"]),
            "my_towers": min(11, obj[side]["towers"]),
            "enemy_towers": min(11, obj[enemy]["towers"]),
            "my_inhibs": min(20, obj[side]["inhibs"]),
            "enemy_inhibs": min(20, obj[enemy]["inhibs"]),
            "my_lost_turrets": lost_turrets[side],
            "enemy_lost_turrets": lost_turrets[enemy],
            "last_dragon_sec": last_dragon_sec,
            "last_elder_sec": last_elder_sec,
            "last_elder_team": last_elder_team,
            "last_baron_sec": last_baron_sec,
            "last_baron_team": last_baron_team,
        },
        "momentum": {
            "recent_ally_kills": min(100, recent_ally_kills),
            "recent_enemy_kills": min(100, recent_enemy_kills),
        },
    }


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


def game_key(data: dict) -> tuple:
    """한 경기를 알아보는 값: (팀, 챔피언) 10쌍. Live Client API 에는 경기 ID 가 없다."""
    return tuple(sorted((str(p.get("team")), champion_of(p) or "") for p in data.get("allPlayers") or []))


def match_payload(features: dict, game_time: float, win: bool | None) -> dict:
    """POST /matches 본문 (AGENTS.md 3-5절). 3절 입력 칸 + 게임 시각 + 승패만. 소환사명 등은 넣지 않는다."""
    return {**features, "game_time_sec": int(min(max(game_time, 0), 7200)), "win": win}


class OverlayBridge:
    """게임 API 를 읽을 때마다 상태를 갱신한다. HTTP 서버는 snapshot() 만 읽는다.
    save 를 주면(--save-match) 경기가 끝났을 때(GameEnd) 한 번, 저장한 응답(--file)이면 처음 읽었을 때 한 번 POST /matches."""

    def __init__(self, fetch: Callable[[], dict], coach: Callable[[dict], dict], side: str | None = None,
                 prices: dict | None = None, source: str = "live", clock: Callable[[], float] = time.time,
                 save: Callable[[dict], dict] | None = None,
                 strategy: Callable[[dict], dict] | None = None):
        self.fetch, self.coach, self.side, self.prices, self.clock = fetch, coach, side, prices, clock
        self.source, self.save, self.strategy_fn = source, save, strategy
        self.saved = False
        self.last_save_try: float | None = None
        self.lock = threading.Lock()
        self.last_coach_at: float | None = None
        self.last_coach_input: dict | None = None
        self.last_strategy_at: float | None = None
        self.last_strategy_input: dict | None = None
        self.fail_since: float | None = None  # 게임 API 가 끊기기 시작한 시각
        self.game_key: tuple | None = None
        self.state = {"bridge_version": BRIDGE_VERSION,
                      "connected": False, "source": source, "in_game": False, "game_time_sec": 0.0,
                      "minutes": None, "pace_scale": None, "updated_at": None, "champion": None, "level": None,
                      "current_gold": 0.0,
                      "owned_items": [], "next_skill": None, "next_skill_when": None, "features": None,
                      "raw_features": None, "live_context": None, "enemy_items": [], "coach": None,
                      "coach_error": None, "strategy": None, "strategy_error": None, "error": None,
                      "saved_match": None, "save_error": None, "popup": None, "hud_window": None, "pace_warning": None}

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def set_popup(self, popup: dict) -> None:
        """팝업 방식과 Windows 항상 위 고정 결과 (AGENTS.md 3-4절 "팝업"). 고정 스레드가 부른다."""
        with self.lock:
            self.state["popup"] = dict(popup)

    def set_hud_window(self, hud_window: dict) -> None:
        """Windows 프레임 없는 인게임 팝업 창 상태(투명도·클릭 통과·표시 여부·LoL 창 감지)."""
        with self.lock:
            self.state["hud_window"] = dict(hud_window)

    def step(self) -> dict:
        try:
            data = self.fetch()
            raw, info = to_game_state(data, self.side, self.prices)
            live_ctx = extract_live_context(data, self.side, self.prices)
        except Exception as e:  # noqa: BLE001  게임 API 가 꺼져 있어도 서버는 살아 있어야 한다
            prev_feats = self.state["features"]
            prev_gt = float(self.state["game_time_sec"] or 0.0)
            now = self.clock()
            if self.fail_since is None:
                self.fail_since = now
            gone = now - self.fail_since >= DISCONNECT_SAVE_SEC  # 잠깐 끊긴 것(시간 초과 한두 번)은 경기 끝이 아니다
            saved_match, save_error = self.state["saved_match"], self.state["save_error"]
            if self.save and not self.saved and self.source == "live" and gone and prev_feats and prev_gt >= 60.0:
                try:
                    out = coach_with_fallback(self.save, match_payload(prev_feats, prev_gt, None))
                    self.saved, save_error = True, None
                    saved_match = {"id": out.get("id"), "created_at": out.get("created_at"), "win": None}
                except Exception as se:  # noqa: BLE001
                    save_error = f"{type(se).__name__}: {se}"[:200]
            with self.lock:
                self.state.update(connected=False, error=f"{type(e).__name__}: {e}"[:200],
                                  saved_match=saved_match, save_error=save_error)
            return self.snapshot()
        gt = info["game_time"]
        prev_gt = float(self.state["game_time_sec"] or 0.0)
        self.fail_since = None
        key = game_key(data)
        if self.source == "live" and self.game_key is not None and (key != self.game_key
                                                                    or gt + NEW_GAME_REWIND_SEC < prev_gt):
            # 새 게임(게임 시각이 되돌아감 또는 챔피언 구성이 바뀜): 저장·코칭 상태를 리셋.
            # 브리지가 새 게임을 0:30 이후에 처음 봐도(로딩 중 폴링 실패, 늦게 켬) 그 게임 결과를 저장한다
            self.saved = False
            self.last_save_try = None
            self.last_coach_input = None
            self.last_coach_at = None
            self.last_strategy_input = None
            self.last_strategy_at = None
        self.game_key = key
        minutes, k = pace_scale(gt)
        features = scale_to_ten_minutes(raw, gt)
        me = _me(data) or {}
        level = _int((data.get("activePlayer") or {}).get("level") or me.get("level"), 1)
        cur_gold = max(0.0, _num((data.get("activePlayer") or {}).get("currentGold")))
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

        strategy, strategy_error = self.state["strategy"], None
        strat_due = False
        if self.strategy_fn is not None:
            strat_due = self.last_strategy_at is None or (
                live_ctx != self.last_strategy_input and now - self.last_strategy_at >= STRATEGY_MIN_SECONDS
            )
            if strat_due:
                try:
                    strategy = self.strategy_fn(live_ctx)
                    self.last_strategy_input, self.last_strategy_at = live_ctx, now
                except Exception as e:  # noqa: BLE001
                    strategy_error = f"{type(e).__name__}: {e}"[:200]
                    self.last_strategy_at = now

        order = _order_of(strategy) or _order_of(coach)
        skill, when = next_skill(level, order, _abilities(data))
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
                pace_warning=pace_warning(gt),
                champion=champion_of(me) if me else None, level=level, current_gold=cur_gold,
                owned_items=extract_owned_items(data),
                next_skill=skill, next_skill_when=when, features=features, raw_features=raw,
                live_context=live_ctx,
                enemy_items=info["enemy_items"], coach=coach,
                coach_error=coach_error if due else self.state["coach_error"],
                strategy=strategy,
                strategy_error=strategy_error if strat_due else self.state["strategy_error"],
                error=None,
                saved_match=saved_match, save_error=save_error)
        return self.snapshot()


# 브리지가 같은 출처(127.0.0.1)에서 HUD 를 내주기 위해 서버로 넘겨 주는 경로 (AGENTS.md 3-4절 "팝업").
# 공개 주소의 웹 페이지는 브라우저의 로컬 네트워크 접근 제한 때문에 127.0.0.1 브리지를 읽지 못한다(Chromium 159 확인).
RELAY = {
    ("GET", "/overlay"),
    ("GET", "/meta/champions"),
    ("POST", "/coach"),
    ("POST", "/coach/briefing"),
    ("POST", "/strategy"),
    ("POST", "/strategy/briefing"),
}
RELAY_MAX_BODY = 64 * 1024
Upstream = Callable[[str, str, "bytes | None"], "tuple[int, str, bytes]"]


def http_upstream(server: str, timeout: float = 8.0,
                  path_timeouts: Mapping[str, float] | None = None) -> Upstream:
    """(메서드, 경로, 본문) → (상태 코드, Content-Type, 본문). /strategy/briefing 은 15초, 나머지는 8초(H6)."""
    timeouts = dict(UPSTREAM_PATH_TIMEOUTS if path_timeouts is None else path_timeouts)

    def call(method: str, path: str, body: bytes | None) -> tuple[int, str, bytes]:
        req = urllib.request.Request(server.rstrip("/") + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        t_sec = float(timeouts.get(path, timeout))
        try:
            with urllib.request.urlopen(req, timeout=t_sec) as r:
                return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", "application/json"), e.read()
    return call


def _parse_content_length(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return 0
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 0 else None


def _handler(bridge: OverlayBridge, upstream: Upstream | None = None,
             window_ctl: Callable[[dict], dict] | None = None,
             on_hud_ping: Callable[[], None] | None = None,
             on_shutdown: Callable[[], None] | None = None,
             bridge_token: str | None = None) -> type[BaseHTTPRequestHandler]:
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
                n = _parse_content_length(self.headers.get("Content-Length"))
                if n is None:
                    self._send(400, cors=False)
                    return True
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

        def _check_control_auth(self) -> tuple[bool, int | None]:
            """M6: /bridge/window 와 /bridge/shutdown 은 CORS 미허용 + X-Bridge-Token + JSON + Content-Length 검사."""
            if bridge_token is not None and self.headers.get("X-Bridge-Token") != bridge_token:
                self._send(403, "application/json; charset=utf-8", b'{"error":"forbidden"}', cors=False)
                return False, None
            ctype = (self.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("application/json"):
                self._send(415, "application/json; charset=utf-8", b'{"error":"json required"}', cors=False)
                return False, None
            n = _parse_content_length(self.headers.get("Content-Length"))
            if n is None:
                self._send(400, "application/json; charset=utf-8", b'{"error":"bad content-length"}', cors=False)
                return False, None
            if n > 4096:
                self._send(413, cors=False)
                return False, None
            return True, n

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/bridge/shutdown":
                ok, n = self._check_control_auth()
                if not ok:
                    return
                if n:
                    self.rfile.read(n)
                if on_shutdown is not None:
                    on_shutdown()
                self._send(200, "application/json; charset=utf-8", b'{"ok":true}', cors=False)
                return
            if path == "/bridge/window":
                ok, n = self._check_control_auth()
                if not ok or n is None:
                    return
                try:
                    cmd = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                    if not isinstance(cmd, dict):
                        raise ValueError("dict expected")
                except Exception:  # noqa: BLE001
                    self._send(400, "application/json; charset=utf-8", b'{"error":"invalid json"}', cors=False)
                    return
                res = window_ctl(cmd) if window_ctl is not None else {"ok": False, "reason": "no_controller"}
                self._send(200, "application/json; charset=utf-8", json.dumps(res, ensure_ascii=False).encode(), cors=False)
                return
            if not self._relay("POST"):
                self._send(404)

        def do_GET(self) -> None:  # noqa: N802
            if self._relay("GET"):
                return
            if self.path.split("?", 1)[0] != "/bridge/state":
                self._send(404)
                return
            if on_hud_ping is not None:
                on_hud_ping()
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


class _BridgeHTTPServer(ThreadingHTTPServer):
    # Windows 에서는 SO_REUSEADDR 가 동일 포트 중복 바인딩을 허용해 두 번째 실행기를 못 막으므로 끈다
    allow_reuse_address = platform.system() != "Windows"


def serve_state(bridge: OverlayBridge, port: int = 8765, upstream: Upstream | None = None,
                window_ctl: Callable[[dict], dict] | None = None,
                on_hud_ping: Callable[[], None] | None = None,
                on_shutdown: Callable[[], None] | None = None,
                bridge_token: str | None = None) -> ThreadingHTTPServer:
    """127.0.0.1 에만 연다. 뒤쪽 스레드에서 돈다. upstream 을 주면 화이트리스트 경로를 서버로 넘긴다."""
    httpd = _BridgeHTTPServer(
        ("127.0.0.1", port), _handler(bridge, upstream, window_ctl, on_hud_ping, on_shutdown, bridge_token)
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def running_bridge_info(port: int, timeout: float = 1.0) -> tuple[bool, str | None]:
    """그 포트에서 브리지가 도는지와 bridge_version 을 돌려준다(C1)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/bridge/state", timeout=timeout) as r:
            data = json.load(r)
            if isinstance(data, dict) and "connected" in data:
                return True, data.get("bridge_version")
    except Exception:  # noqa: BLE001
        pass
    return False, None


def bridge_running(port: int, timeout: float = 1.0) -> bool:
    """그 포트에서 이미 브리지가 응답하는지(/bridge/state 가 JSON 에 connected 를 담아 돌려주는지)."""
    return running_bridge_info(port, timeout)[0]


def port_busy_message(port: int, running: bool, version_mismatch: bool = False) -> str:
    """상태 포트를 못 열 때 한 줄 안내 (AGENTS.md 3-4 "상태 포트가 이미 쓰이는 경우")."""
    if running and version_mismatch:
        return (f"옛 브리지가 실행 중입니다(127.0.0.1:{port}). "
                "기존 브리지(또는 HUD × 버튼)를 닫고 다시 실행하세요.")
    if running:
        return (f"브리지가 다른 창에서 이미 실행 중입니다(127.0.0.1:{port}). "
                "그 창을 그대로 쓰거나, 닫고 다시 실행하세요.")
    return (f"127.0.0.1:{port} 포트를 다른 프로그램이 쓰고 있습니다. "
            "--listen-port 로 다른 포트를 지정해 다시 실행하세요.")


def generate_bridge_token() -> str:
    return os.urandom(16).hex()


def _token_path(port: int) -> str:
    return os.path.join(_cache_dir(), f"bridge-token-{port}.txt")


def save_bridge_token(port: int, token: str) -> None:
    try:
        path = _token_path(port)
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)
        if platform.system() != "Windows":
            os.chmod(path, 0o600)
    except OSError:
        pass


def load_bridge_token(port: int) -> str | None:
    try:
        with open(_token_path(port), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def overlay_url(port: int, token: str | None = None) -> str:
    """팝업 주소. HUD 를 브리지(127.0.0.1)가 내주므로 상태 읽기가 같은 출처다."""
    base = f"http://127.0.0.1:{port}/overlay?bridge_url=http://127.0.0.1:{port}/bridge/state"
    return f"{base}&token={token}" if token else base


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
                  exists: Callable[[str], bool] = os.path.exists, build: Callable[[], str | None] | None = None,
                  disable_gpu_compositing: bool = False) -> tuple[str, list[str] | None]:
    """(방식, 명령). 방식은 "swift"(항상 위 패널), "chrome"(--app 창), "browser"(기본 브라우저, 명령 None)."""
    system = system or platform.system()
    w, h = POPUP_SIZE
    if system == "Darwin" and which("swiftc"):
        binary = (build or build_swift_popup)()
        if binary:
            return "swift", [binary, url, str(w), str(h)]
    chrome = _find_app_browser(system, which, exists)
    if chrome:
        args = [chrome, f"--app={url}", f"--window-size={w},{h}", "--window-position=24,220",
                "--user-data-dir=" + os.path.join(_cache_dir(), "chrome-profile"),
                "--no-first-run", "--no-default-browser-check",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows"]
        if disable_gpu_compositing:
            args.append("--disable-gpu-compositing")
        return "chrome", args
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


# Windows --app 창 항상 위 고정 및 프레임 없는 인게임 팝업 HUD (AGENTS.md 3-4절)
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_NOOWNERZORDER = 0x0001, 0x0002, 0x0010, 0x0020, 0x0200
PIN_FLAGS = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_POPUP = 0x80000000
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
FRAME_STYLE_MASK = WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x00000020, 0x00000080, 0x00080000, 0x08000000
LWA_ALPHA = 0x00000002
SW_HIDE, SW_SHOWNA = 0, 8
WM_CLOSE = 0x0010
VK_F8, VK_F9 = 0x77, 0x78
HUD_IDLE_EXIT_SEC = 12.0
OVERLAY_TITLES = ("LoL 페이스메이커", "페이커 오버레이")
CHROME_CLASS = "Chrome_WidgetWin_1"
ALLOWED_BROWSER_EXES = frozenset({"chrome.exe", "msedge.exe", "chromium.exe"})
NON_APP_TITLE_SUFFIXES = (" - Google Chrome", " - Microsoft\u200b Edge", " - Microsoft Edge", " - Visual Studio Code", " - Antigravity")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PIN_TRIES, PIN_INTERVAL = 25, 0.25
PIN_SETTLE_TIMES, PIN_SETTLE_SEC = 10, 1.0  # 첫 고정 직후 1초 간격 10번 다시 고정


def read_lol_window_mode(paths: tuple[str, ...] = LOL_GAME_CFG_PATHS) -> str | None:
    """LoL Game.cfg 의 WindowMode(0=fullscreen, 1=windowed, 2=borderless)를 읽기 전용으로 확인."""
    mode_map = {"0": "fullscreen", "1": "windowed", "2": "borderless"}
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if s.lower().startswith("windowmode="):
                        val = s.split("=", 1)[1].strip()
                        return mode_map.get(val)
        except OSError:
            continue
    return None


class Win32Windows:
    """user32 위의 얇은 껍데기. Windows 에서는 ctypes.windll.user32 와 ctypes, 테스트는 가짜 둘을 넣는다."""

    def __init__(self, user32, ct, gdi32=None, kernel32=None):
        self.u, self.ct, self.g, self.k = user32, ct, gdi32, kernel32

    def process_path(self, hwnd) -> str | None:
        """창 소유 프로세스의 실행 파일 전체 경로를 반환한다. kernel32 없으면 None."""
        if self.k is None:
            return None
        open_fn = getattr(self.k, "OpenProcess", None)
        query_fn = getattr(self.k, "QueryFullProcessImageNameW", None)
        close_fn = getattr(self.k, "CloseHandle", None)
        if open_fn is None or query_fn is None or close_fn is None:
            return None
        p = self.pid(hwnd)
        if not p:
            return None
        hproc = open_fn(PROCESS_QUERY_LIMITED_INFORMATION, False, p)
        if not hproc:
            return None
        try:
            size = self.ct.wintypes.DWORD()
            size.value = 512
            buf = self.ct.create_unicode_buffer(512)
            if query_fn(hproc, 0, buf, self.ct.byref(size)) and buf.value:
                return buf.value
        finally:
            close_fn(hproc)
        return None

    def process_name(self, hwnd) -> str | None:
        """창 소유 프로세스의 실행 파일명(소문자 basename)을 반환한다. kernel32 없으면 None."""
        full = self.process_path(hwnd)
        return os.path.basename(full).lower() if full else None

    def is_allowed_browser(self, hwnd) -> bool:
        """VS Code·Antigravity 같은 Electron 앱을 건드리지 않도록 chrome/msedge 프로세스만 허용한다."""
        exe = self.process_name(hwnd)
        return True if exe is None else (exe in ALLOWED_BROWSER_EXES)

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

    def _long_fns(self):
        get = getattr(self.u, "GetWindowLongPtrW", None) or getattr(self.u, "GetWindowLongW", None)
        put = getattr(self.u, "SetWindowLongPtrW", None) or getattr(self.u, "SetWindowLongW", None)
        return get, put

    def no_activate(self, hwnd) -> bool:
        """눌러도 활성화되지 않게 WS_EX_NOACTIVATE 를 더한다(게임이 키 입력을 계속 받는다). 못 하면 False."""
        get, put = self._long_fns()
        if get is None or put is None:
            return False
        style = int(get(hwnd, GWL_EXSTYLE))
        if not style & WS_EX_NOACTIVATE:
            put(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        return True

    def apply_hud_popup(self, hwnd, *, alpha: int = 235, click_through: bool = False,
                        clip_caption_px: int = 0) -> bool:
        """C5: 첫 WS_EX_TOOLWINDOW 적용 시 SW_HIDE -> 스타일 변경 -> SW_SHOWNA 순서, 선택 SetWindowRgn."""
        get, put = self._long_fns()
        if get is None or put is None:
            return False
        cur_style = int(get(hwnd, GWL_STYLE))
        new_style = (cur_style & ~FRAME_STYLE_MASK) | WS_POPUP
        cur_ex = int(get(hwnd, GWL_EXSTYLE))
        want_ex = cur_ex | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
        if click_through:
            want_ex |= WS_EX_TRANSPARENT
        else:
            want_ex &= ~WS_EX_TRANSPARENT
        first_tool = not (cur_ex & WS_EX_TOOLWINDOW)
        if first_tool:
            self.set_visible(hwnd, False)
        changed = False
        if new_style != cur_style:
            put(hwnd, GWL_STYLE, new_style)
            changed = True
        if want_ex != cur_ex:
            put(hwnd, GWL_EXSTYLE, want_ex)
            changed = True
        self.set_alpha(hwnd, alpha)
        flags = PIN_FLAGS | (SWP_FRAMECHANGED if changed else 0)
        ok = bool(self.u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags))
        if clip_caption_px > 0:
            self.clip_top(hwnd, clip_caption_px)
        if first_tool:
            self.set_visible(hwnd, True)
        return ok

    def clip_top(self, hwnd, px: int) -> bool:
        """선택 대체 경로(C5): 위쪽 제목 띠(px)를 SetWindowRgn 으로 잘라낸다."""
        set_rgn = getattr(self.u, "SetWindowRgn", None)
        create_rgn = getattr(self.g, "CreateRectRgn", None) if self.g is not None else None
        if set_rgn is None or create_rgn is None:
            return False
        rect = self.get_rect(hwnd)
        if rect is None:
            return False
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= px:
            return False
        rgn = create_rgn(0, int(px), int(w), int(h))
        if not rgn:
            return False
        return bool(set_rgn(hwnd, rgn, True))

    def set_alpha(self, hwnd, alpha: int) -> bool:
        fn = getattr(self.u, "SetLayeredWindowAttributes", None)
        if fn is None:
            return False
        clamped = max(80, min(255, int(alpha)))
        return bool(fn(hwnd, 0, clamped, LWA_ALPHA))

    def set_visible(self, hwnd, visible: bool) -> bool:
        fn = getattr(self.u, "ShowWindow", None)
        if fn is None:
            return False
        return bool(fn(hwnd, SW_SHOWNA if visible else SW_HIDE))

    def is_window(self, hwnd) -> bool:
        fn = getattr(self.u, "IsWindow", None)
        if fn is None:
            return True
        return bool(fn(hwnd))

    def is_iconic(self, hwnd) -> bool:
        fn = getattr(self.u, "IsIconic", None)
        if fn is None:
            return False
        return bool(fn(hwnd))

    def find_lol_window(self):
        fn = getattr(self.u, "FindWindowW", None)
        if fn is None:
            return None
        for t in LOL_WINDOW_TITLES:
            hwnd = fn(None, t)
            if hwnd and self.u.IsWindowVisible(hwnd):
                return hwnd
        return None

    def get_rect(self, hwnd) -> tuple[int, int, int, int] | None:
        fn = getattr(self.u, "GetWindowRect", None)
        rect_cls = getattr(getattr(self.ct, "wintypes", None), "RECT", None)
        if fn is None or rect_cls is None:
            return None
        rc = rect_cls()
        if not fn(hwnd, self.ct.byref(rc)):
            return None
        return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)

    def work_area_for(self, hwnd, fullscreen: bool = False) -> tuple[int, int, int, int] | None:
        """H7: MonitorFromWindow + GetMonitorInfoW 로 해당 모니터의 영역(전체 화면이면 rcMonitor, 창 모드면 rcWork)을 구한다."""
        mon_fn = getattr(self.u, "MonitorFromWindow", None)
        info_fn = getattr(self.u, "GetMonitorInfoW", None)
        if mon_fn is None or info_fn is None:
            return None
        hmon = mon_fn(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return None
        w = getattr(self.ct, "wintypes", None)
        mi_cls = getattr(w, "MONITORINFO", None)
        if mi_cls is not None:
            mi = mi_cls()
        elif hasattr(self.ct, "Structure") and hasattr(w, "RECT") and hasattr(w, "DWORD"):
            class _MI(self.ct.Structure):
                _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]
            mi = _MI()
            mi.cbSize = self.ct.sizeof(mi)
        else:
            return None
        if not info_fn(hmon, self.ct.byref(mi)):
            return None
        rc = getattr(mi, "rcMonitor", mi.rcWork) if fullscreen else mi.rcWork
        return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)

    def foreground_window(self):
        fn = getattr(self.u, "GetForegroundWindow", None)
        return fn() if fn is not None else 0

    def raise_topmost(self, hwnd) -> bool:
        """Windows 10/11 FSO 전체 화면 위 Z-순서 유지 (포커스 미탈취)."""
        return bool(self.u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, PIN_FLAGS | SWP_NOOWNERZORDER))

    def move_window(self, hwnd, x: int, y: int) -> bool:
        return bool(self.u.SetWindowPos(hwnd, HWND_TOPMOST, int(x), int(y), 0, 0, SWP_NOSIZE | SWP_NOACTIVATE))

    def key_down(self, vk: int) -> bool:
        fn = getattr(self.u, "GetAsyncKeyState", None)
        if fn is None:
            return False
        return bool(int(fn(vk)) & 0x8000)

    def close_window(self, hwnd) -> bool:
        fn = getattr(self.u, "PostMessageW", None)
        if fn is None:
            return False
        return bool(fn(hwnd, WM_CLOSE, 0, 0))

    def pin(self, hwnd) -> bool:
        try:
            self.no_activate(hwnd)
        except Exception:  # noqa: BLE001  초점 처리는 보조 기능이라 실패해도 고정은 한다 (AGENTS.md 3-4)
            pass
        return bool(self.u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, PIN_FLAGS))


def _clamp_to_area(x: int, y: int, w: int, h: int, area: tuple[int, int, int, int] | None) -> tuple[int, int]:
    if area is None:
        return x, y
    al, at_, ar, ab = area
    max_x = max(al, ar - max(w, 1))
    max_y = max(at_, ab - max(h, 1))
    return max(al, min(x, max_x)), max(at_, min(y, max_y))


def discover_lol_cfg_paths(win: Win32Windows | None = None, lol_hwnd=None,
                           base_paths: tuple[str, ...] = LOL_GAME_CFG_PATHS) -> tuple[str, ...]:
    """W5: 실행 중인 LoL 프로세스 경로에서 Config/Game.cfg 를 동적으로 찾아 기본 후보 앞에 둔다."""
    out: list[str] = []
    if win is not None and lol_hwnd:
        try:
            exe = win.process_path(lol_hwnd)
            if exe:
                # <LoL>/Game/League of Legends.exe -> <LoL>/Config/Game.cfg
                root_dir = os.path.dirname(os.path.dirname(exe))
                cand = os.path.join(root_dir, "Config", "Game.cfg")
                out.append(cand)
        except Exception:  # noqa: BLE001
            pass
    for p in base_paths:
        if p not in out:
            out.append(p)
    return tuple(out)


class WindowsHudController:
    """Windows 프레임 없는 인게임 팝업 HUD 제어기 (투명도·클릭 통과·F8/F9 단축키·LoL 창 자동 추적)."""

    def __init__(self, win: Win32Windows | None = None, report: Callable[[dict], None] | None = None,
                 cfg_paths: tuple[str, ...] = LOL_GAME_CFG_PATHS, port: int = 8765, pid: int = 0,
                 clip_caption_px: int = 0):
        self.win = win
        self.report = report
        self.port = port
        self.pid = pid
        self.clip_caption_px = int(clip_caption_px)
        self._cfg_paths = tuple(cfg_paths)
        self.lock = threading.Lock()
        self.hwnds: list = []
        self.alpha: int = 235
        self.click_through: bool = False
        self.visible: bool = True
        self.frameless: bool = False
        self.dx: int = 0
        self.dy: int = 0
        self.lol_detected: bool = False
        self.lol_window_mode: str | None = read_lol_window_mode(self._cfg_paths)
        self._last_lol_rect: tuple[int, int, int, int] | None = None
        self._last_work_area: tuple[int, int, int, int] | None = None
        self._prev_f8: bool = False
        self._prev_f9: bool = False

    def status(self) -> dict:
        with self.lock:
            warn = bool(self.lol_window_mode == "fullscreen" and not (self.lol_detected and self.visible))
            return {
                "frameless": self.frameless,
                "alpha": self.alpha,
                "opacity_pct": round(self.alpha / 255.0 * 100),
                "click_through": self.click_through,
                "visible": self.visible,
                "lol_detected": self.lol_detected,
                "lol_window_mode": self.lol_window_mode,
                "lol_window_mode_warning": warn,
                "hotkeys": {"click_through": "F8", "toggle_visible": "F9"},
            }

    def _emit(self) -> None:
        if self.report:
            self.report(self.status())

    def any_window_alive(self) -> bool | None:
        """C4: 부착된 HUD 창이 있으면 IsWindow 로 실제 생존 여부를 확인. 아직 없으면 None."""
        with self.lock:
            win, hwnds = self.win, list(self.hwnds)
        if win is None or not hwnds:
            return None
        try:
            return any(win.is_window(h) for h in hwnds)
        except Exception:  # noqa: BLE001
            return None

    def _hud_size(self, win: Win32Windows, hwnd) -> tuple[int, int]:
        try:
            rc = win.get_rect(hwnd)
            if rc is not None and (rc[2] - rc[0]) > 0 and (rc[3] - rc[1]) > 0:
                return rc[2] - rc[0], rc[3] - rc[1]
        except Exception:  # noqa: BLE001
            pass
        return POPUP_SIZE

    def attach(self, win: Win32Windows, hwnds: list) -> None:
        with self.lock:
            self.win = win
            self.hwnds = list(hwnds)
            alpha, ct, vis, clip_px = self.alpha, self.click_through, self.visible, self.clip_caption_px
        ok = False
        for h in hwnds:
            try:
                if win.apply_hud_popup(h, alpha=alpha, click_through=ct, clip_caption_px=clip_px):
                    ok = True
                if not vis:
                    win.set_visible(h, False)
            except Exception:  # noqa: BLE001
                pass
        with self.lock:
            self.frameless = ok or self.frameless
        self.sync_to_lol_window()
        self._emit()

    def reattach(self) -> bool:
        """C2: 재실행으로 새로 열린 팝업 창을 찾아 스타일·F8/F9를 다시 붙인다."""
        with self.lock:
            win, pid, port = self.win, self.pid, self.port
        if win is None:
            return False
        _, hwnds = find_overlay_windows(win, pid, port)
        if not hwnds:
            return False
        self.attach(win, hwnds)
        return True

    def sync_to_lol_window(self, force: bool = False) -> bool:
        with self.lock:
            win, hwnds, dx, dy = self.win, list(self.hwnds), self.dx, self.dy
            prev_rect = self._last_lol_rect
        if win is None or not hwnds:
            return False
        try:
            lol = win.find_lol_window()
            if not lol or win.is_iconic(lol):  # H7: 최소화된 LoL 창은 건너뛴다
                return False
            rect = win.get_rect(lol)
            mon = win.work_area_for(lol, fullscreen=True)
            work = mon if (mon is not None and rect == mon) else win.work_area_for(lol, fullscreen=False)
            dyn_mode = read_lol_window_mode(discover_lol_cfg_paths(win, lol, self._cfg_paths))
        except Exception:  # noqa: BLE001
            rect, work, dyn_mode = None, None, None
        with self.lock:
            self.lol_detected = rect is not None
            if dyn_mode is not None:
                self.lol_window_mode = dyn_mode
            changed = rect is not None and (force or rect != prev_rect)
            if rect is not None:
                self._last_lol_rect = rect
                self._last_work_area = work
        if changed and rect is not None:
            left, top, right, _bottom = rect
            for h in hwnds:
                try:
                    hw, hh = self._hud_size(win, h)
                    x, y = _clamp_to_area(right - hw - 18 + dx, top + 56 + dy, hw, hh, work)
                    win.move_window(h, x, y)
                except Exception:  # noqa: BLE001
                    pass
            return True
        return False

    def handle_command(self, cmd: dict) -> dict:
        if cmd.get("reattach"):
            ok = self.reattach()
            return {"ok": ok, **self.status()}
        with self.lock:
            win, hwnds = self.win, list(self.hwnds)
            if "alpha" in cmd:
                self.alpha = max(80, min(255, _int(cmd["alpha"], self.alpha)))
            if "opacity" in cmd:
                op = max(0.35, min(1.0, _num(cmd["opacity"], 0.92)))
                self.alpha = max(80, min(255, int(round(op * 255))))
            if "click_through" in cmd:
                self.click_through = bool(cmd["click_through"])
            if "visible" in cmd:
                self.visible = bool(cmd["visible"])
            if cmd.get("reset_pos"):
                self.dx, self.dy = 0, 0
            moved = False
            step_dx, step_dy = _int(cmd.get("dx"), 0), _int(cmd.get("dy"), 0)
            if "dx" in cmd or "dy" in cmd:
                # H7: LoL 창이 감지되기 전의 드래그는 dx/dy 에 누적하지 않는다
                if self._last_lol_rect is not None:
                    self.dx += step_dx
                    self.dy += step_dy
                moved = True
            alpha, ct, vis, dx, dy = self.alpha, self.click_through, self.visible, self.dx, self.dy
            lol_rect, work, clip_px = self._last_lol_rect, self._last_work_area, self.clip_caption_px

        if win is not None and hwnds:
            for h in hwnds:
                try:
                    win.apply_hud_popup(h, alpha=alpha, click_through=ct, clip_caption_px=clip_px)
                    win.set_visible(h, vis)
                    if moved or cmd.get("reset_pos"):
                        hw, hh = self._hud_size(win, h)
                        if lol_rect is not None:
                            left, top, right, _ = lol_rect
                            x, y = _clamp_to_area(right - hw - 18 + dx, top + 56 + dy, hw, hh, work)
                            win.move_window(h, x, y)
                        else:
                            cur = win.get_rect(h)
                            if cur is not None:
                                area = win.work_area_for(h)
                                x, y = _clamp_to_area(cur[0] + step_dx, cur[1] + step_dy, hw, hh, area)
                                win.move_window(h, x, y)
                except Exception:  # noqa: BLE001
                    pass
        self._emit()
        return {"ok": True, **self.status()}

    def ensure_above_lol(self, force: bool = False) -> bool:
        """Windows 10/11 FSO 전체 화면(`WindowMode=0`)에서도 게임 클릭 직후 HUD 가 뒤로 밀리지 않게 Z-순서를 유지."""
        with self.lock:
            win, hwnds, vis = self.win, list(self.hwnds), self.visible
        if not vis or win is None or not hwnds:
            return False
        try:
            lol = win.find_lol_window()
            if not lol or win.is_iconic(lol):
                return False
            fg = win.foreground_window()
            is_fg = bool(fg and fg == lol)
        except Exception:  # noqa: BLE001
            return False
        if not (force or is_fg):
            return False
        ok = False
        for h in hwnds:
            try:
                if win.raise_topmost(h):
                    ok = True
            except Exception:  # noqa: BLE001
                pass
        return ok

    def poll_hotkeys_once(self) -> list[str]:
        """F8(클릭 통과 토글)·F9(HUD 숨김/표시 토글) 엣지 감지."""
        with self.lock:
            win = self.win
            prev_f8, prev_f9 = self._prev_f8, self._prev_f9
        if win is None:
            return []
        try:
            f8 = win.key_down(VK_F8)
            f9 = win.key_down(VK_F9)
        except Exception:  # noqa: BLE001
            return []
        fired = []
        with self.lock:
            self._prev_f8, self._prev_f9 = f8, f9
        if f8 and not prev_f8:
            self.handle_command({"click_through": not self.click_through})
            fired.append("F8")
        if f9 and not prev_f9:
            self.handle_command({"visible": not self.visible})
            fired.append("F9")
        return fired

    def close_all(self) -> None:
        with self.lock:
            win, hwnds = self.win, list(self.hwnds)
        if win is None:
            return
        for h in hwnds:
            try:
                win.close_window(h)
            except Exception:  # noqa: BLE001
                pass


def find_overlay_windows(win: Win32Windows, pid: int, port: int) -> tuple[str | None, list]:
    """(찾은 단계 "pid"·"title"·"class", 창 목록). 브라우저 프로세스만 대상으로 PID → 제목 → 클래스+주소 순서."""
    shown = [h for h in win.visible() if win.is_allowed_browser(h)]
    by_pid = [h for h in shown if win.pid(h) == pid]
    if by_pid:
        return "pid", by_pid
    by_title = [
        h for h in shown
        if any(t in win.title(h) for t in OVERLAY_TITLES)
        and not any(win.title(h).endswith(suf) for suf in NON_APP_TITLE_SUFFIXES)
    ]
    if by_title:
        return "title", by_title[:1]
    hint = f"127.0.0.1:{port}"
    by_class = [h for h in shown if win.class_name(h) == CHROME_CLASS and hint in win.title(h)]
    if by_class:
        return "class", by_class[:1]
    return None, []


def pin_topmost(win: Win32Windows, pid: int, port: int, report: Callable[[dict], None],
                sleep: Callable[[float], None] = time.sleep, repin_sec: float = 0.0,
                tries: int = PIN_TRIES, max_repins: int | None = None, settle: int = PIN_SETTLE_TIMES) -> None:
    """창을 찾아 항상 위로 고정하고 결과를 report 로 알린다.

    첫 고정 뒤 settle 번은 PIN_SETTLE_SEC 간격으로 다시 고정하고(게임 창을 눌러 풀리는 경우),
    그 뒤 repin_sec > 0 이면 그 간격으로 계속 다시 고정한다. max_repins 는 전체 재고정 횟수 상한."""
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
    while (n < settle or repin_sec > 0) and (max_repins is None or n < max_repins):
        sleep(PIN_SETTLE_SEC if n < settle else repin_sec)
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
                               system: str | None = None,
                               hud_ctl: WindowsHudController | None = None) -> bool:
    """Windows 에서만 백그라운드로 pin_topmost + 프레임 없는 인게임 팝업 스타일·단축키(F8/F9)·LoL 창 추적."""
    if (system or platform.system()) != "Windows":
        return False

    def _worker() -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            gdi32 = getattr(ctypes.windll, "gdi32", None)
            kernel32 = getattr(ctypes.windll, "kernel32", None)
            if kernel32 is not None:
                if hasattr(kernel32, "OpenProcess"):
                    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                    kernel32.OpenProcess.restype = wintypes.HANDLE
                if hasattr(kernel32, "QueryFullProcessImageNameW"):
                    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
                    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
                if hasattr(kernel32, "CloseHandle"):
                    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel32.CloseHandle.restype = wintypes.BOOL
            try:
                if hasattr(user32, "SetProcessDpiAwarenessContext"):
                    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
                    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # H7: Per-Monitor DPI Aware V2
            except Exception:  # noqa: BLE001
                pass
            # 64비트에서 HWND_TOPMOST(-1)가 포인터 크기로 넘어가게 인자 형을 정한다
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            # 확장 스타일 읽기·쓰기: 64비트는 …LongPtrW, 32비트 Python 에는 그 이름이 없어 …LongW 를 쓴다
            for get_name, put_name in (("GetWindowLongPtrW", "SetWindowLongPtrW"), ("GetWindowLongW", "SetWindowLongW")):
                get_fn, put_fn = getattr(user32, get_name, None), getattr(user32, put_name, None)
                if get_fn is not None and put_fn is not None:
                    get_fn.argtypes, get_fn.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
                    put_fn.argtypes, put_fn.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
                    break
            if hasattr(user32, "SetLayeredWindowAttributes"):
                user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
                user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
            if hasattr(user32, "ShowWindow"):
                user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.ShowWindow.restype = wintypes.BOOL
            if hasattr(user32, "IsWindow"):
                user32.IsWindow.argtypes = [wintypes.HWND]
                user32.IsWindow.restype = wintypes.BOOL
            if hasattr(user32, "IsIconic"):
                user32.IsIconic.argtypes = [wintypes.HWND]
                user32.IsIconic.restype = wintypes.BOOL
            if hasattr(user32, "FindWindowW"):
                user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                user32.FindWindowW.restype = wintypes.HWND
            if hasattr(user32, "GetWindowRect"):
                user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                user32.GetWindowRect.restype = wintypes.BOOL
            if hasattr(user32, "MonitorFromWindow"):
                user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.MonitorFromWindow.restype = wintypes.HMONITOR
            if hasattr(user32, "GetMonitorInfoW"):
                user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
                user32.GetMonitorInfoW.restype = wintypes.BOOL
            if hasattr(user32, "GetForegroundWindow"):
                user32.GetForegroundWindow.argtypes = []
                user32.GetForegroundWindow.restype = wintypes.HWND
            if hasattr(user32, "GetAsyncKeyState"):
                user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
                user32.GetAsyncKeyState.restype = ctypes.c_short
            if hasattr(user32, "PostMessageW"):
                user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.PostMessageW.restype = wintypes.BOOL
            if hasattr(user32, "SetWindowRgn"):
                user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
                user32.SetWindowRgn.restype = ctypes.c_int
            if gdi32 is not None and hasattr(gdi32, "CreateRectRgn"):
                gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
                gdi32.CreateRectRgn.restype = wintypes.HRGN

            win = Win32Windows(user32, ctypes, gdi32=gdi32, kernel32=kernel32)
            if hud_ctl is not None:
                with hud_ctl.lock:
                    hud_ctl.win = win
                    hud_ctl.pid = pid
                    hud_ctl.port = port

            def _report_and_style(upd: dict) -> None:
                report(upd)
                if hud_ctl is not None and upd.get("status") == "pinned":
                    how_found, hwnds = find_overlay_windows(win, pid, port)
                    if hwnds:
                        hud_ctl.attach(win, hwnds)
                    else:
                        print(f"\nHUD attach 건너뜀: 창 미발견(method={how_found}, pid={pid}, port={port})", file=sys.stderr)

            if hud_ctl is not None:
                def _hotkey_loop() -> None:
                    tick = 0
                    while True:
                        hud_ctl.poll_hotkeys_once()
                        tick += 1
                        hud_ctl.ensure_above_lol(force=(tick % 4 == 0))  # FSO 전체 화면 위 Z-순서 즉시·0.32초 주기 유지
                        if tick % 15 == 0:  # 약 1.2초마다 LoL 창 위치 추적
                            hud_ctl.sync_to_lol_window()
                        time.sleep(0.08)

                threading.Thread(target=_hotkey_loop, daemon=True).start()

            pin_topmost(win, pid, port, _report_and_style, repin_sec=repin_sec)
        except Exception as e:  # noqa: BLE001  조용히 죽지 않고 상태에 남긴다
            report({"status": "failed", "error": f"{type(e).__name__}: {e}"[:200]})

    threading.Thread(target=_worker, daemon=True).start()
    return True


def launch_popup(port: int, report: Callable[[dict], None] | None = None, repin_sec: float = 0.0,
                 system: str | None = None, procs: list | None = None,
                 hud_ctl: WindowsHudController | None = None, token: str | None = None,
                 disable_gpu_compositing: bool = False) -> str:
    """항상 위 팝업을 띄우고 쓴 방식을 돌려준다. 고정 결과는 report(브리지 상태의 popup)로 남긴다.
    procs 를 주면 띄운 (방식, 프로세스)를 넣는다(run_overlay 가 swift 팝업이 닫혔는지 본다)."""
    system = system or platform.system()
    url = overlay_url(port, token)
    if disable_gpu_compositing:
        how, cmd = popup_command(url, system, disable_gpu_compositing=True)
    else:
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
        if procs is not None:
            procs.append((how, proc))
        if how == "chrome" and system == "Windows":
            if hud_ctl is not None:
                _pin_windows_topmost_async(proc.pid, port, note, repin_sec, system, hud_ctl=hud_ctl)
            else:
                _pin_windows_topmost_async(proc.pid, port, note, repin_sec, system)
        else:
            note({})
    else:
        webbrowser.open(url)
        note({"status": "failed", "error": "Chrome·Edge 가 없어 항상 위로 고정할 수 없다"} if system == "Windows" else {})
    print(f"오버레이 팝업({how}): {url}")
    return how


def _post_reattach(port: int, token: str | None) -> None:
    """C2: 이미 도는 브리지에 새 팝업 창을 다시 부착하도록 요청한다."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bridge-Token"] = token
    req = urllib.request.Request(f"http://127.0.0.1:{port}/bridge/window",
                                 data=b'{"reattach": true}', headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2.0):
            pass
    except Exception:  # noqa: BLE001
        pass


def _call_launch_popup(port: int, report, repin_sec: float, procs: list,
                       hud_ctl: WindowsHudController | None, token: str | None,
                       disable_gpu_compositing: bool = False) -> str:
    """M5: except TypeError 로 두 번 부르지 않고 함수 시그니처를 미리 확인해 한 번만 호출한다."""
    code = getattr(launch_popup, "__code__", None)
    names = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount] if code is not None else ()
    has_varkw = bool(code.co_flags & 0x08) if code is not None else False
    kw: dict = {"procs": procs}
    if hud_ctl is not None and (has_varkw or "hud_ctl" in names):
        kw["hud_ctl"] = hud_ctl
    if token is not None and (has_varkw or "token" in names):
        kw["token"] = token
    if disable_gpu_compositing and (has_varkw or "disable_gpu_compositing" in names):
        kw["disable_gpu_compositing"] = True
    return launch_popup(port, report, repin_sec, **kw)


def _configure_headless_stdio(log_file: str | None = None) -> None:
    """pythonw.exe(터미널 없는 Windows 실행)에서는 sys.stdout/stderr 가 None 이라 print 시 크래시한다.
    로그 파일 또는 devnull 로 안전하게 리다이렉트한다."""
    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)) or ".", exist_ok=True)
            f = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
            sys.stdout = f
            sys.stderr = f
            return
        except OSError:
            pass
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def run_overlay(bridge: OverlayBridge, port: int, server: str | None = None, popup: bool = False,
                sleep: Callable[[float], None] = time.sleep, max_steps: int | None = None,
                repin_sec: float = 0.0, exit_on_close: bool = False,
                idle_exit_sec: float = HUD_IDLE_EXIT_SEC, clip_caption_px: int = 0,
                disable_gpu_compositing: bool = False) -> None:
    hud_ctl = (WindowsHudController(report=bridge.set_hud_window, port=port, clip_caption_px=clip_caption_px)
               if platform.system() == "Windows" else None)
    stop_event = threading.Event()
    hud_ping = {"seen": False, "last": 0.0}
    token = generate_bridge_token()

    def _on_ping() -> None:
        hud_ping["seen"] = True
        hud_ping["last"] = bridge.clock()

    def _on_shutdown() -> None:
        stop_event.set()
        if hud_ctl is not None:
            hud_ctl.close_all()

    if hud_ctl is not None:
        bridge.set_hud_window(hud_ctl.status())
        if hud_ctl.lol_window_mode == "fullscreen":
            print("안내: LoL 설정(Game.cfg)이 전체 화면(WindowMode=0)입니다. Windows 10/11 FSO 환경에서 HUD 표시를 시도하며, 가려지면 테두리 없음(Borderless)으로 바꾸세요.",
                  file=sys.stderr)
    try:
        httpd = serve_state(bridge, port, http_upstream(server) if server else None,
                            window_ctl=hud_ctl.handle_command if hud_ctl is not None else None,
                            on_hud_ping=_on_ping, on_shutdown=_on_shutdown, bridge_token=token)
    except OSError as e:
        running, remote_ver = running_bridge_info(port)
        if running and remote_ver != BRIDGE_VERSION:
            # C1: 옛 브리지(판 불일치)면 재부착·팝업 재실행을 하지 않고 닫으라고 알린다
            print(port_busy_message(port, True, version_mismatch=True), file=sys.stderr)
            raise SystemExit(1) from e
        if running and popup:
            # C2: 같은 판 브리지이면 팝업을 다시 띄우고 도는 브리지에 reattach 를 보낸다
            print(f"브리지가 이미 실행 중이라(127.0.0.1:{port}) 팝업만 다시 띄웁니다.")
            existing_tok = load_bridge_token(port)
            code = getattr(launch_popup, "__code__", None)
            names = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount] if code is not None else ()
            has_varkw = bool(code.co_flags & 0x08) if code is not None else False
            if existing_tok and (has_varkw or "token" in names):
                launch_popup(port, None, repin_sec, token=existing_tok)
            else:
                launch_popup(port, None, repin_sec)
            if platform.system() == "Windows":
                sleep(PIN_TRIES * PIN_INTERVAL + PIN_SETTLE_TIMES * PIN_SETTLE_SEC + 1.0)
            _post_reattach(port, existing_tok)
            return
        print(port_busy_message(port, running), file=sys.stderr)
        raise SystemExit(1) from e
    real = httpd.server_address[1]
    save_bridge_token(real, token)
    if hud_ctl is not None:
        hud_ctl.port = real
    print(f"브리지 상태: http://127.0.0.1:{real}/bridge/state (멈추려면 Ctrl+C)")
    if server:
        print(f"HUD: {overlay_url(real, token)}")
    owned: list = []
    if popup:
        _call_launch_popup(real, bridge.set_popup, repin_sec, owned, hud_ctl, token, disable_gpu_compositing)
    steps = 0
    tty = bool(sys.stdout and getattr(sys.stdout, "isatty", lambda: False)())
    last_line = None
    try:
        while max_steps is None or steps < max_steps:
            if stop_event.is_set():
                print("\nHUD 종료 요청으로 브리지를 끝냅니다.")
                break
            if any(how == "swift" and proc.poll() is not None for how, proc in owned):
                # macOS 앱(.app)은 터미널이 없어 끌 방법이 없다: 팝업을 닫으면 브리지도 끝낸다
                print("\n팝업을 닫아 브리지를 끝냅니다.")
                break
            if exit_on_close and hud_ping["seen"]:
                # C4: F9 숨김 중이면 자동 종료 건너뜀; Windows 는 IsWindow 로 창 생존 여부 함께 확인
                hidden = hud_ctl is not None and not hud_ctl.visible
                win_alive = hud_ctl.any_window_alive() if hud_ctl is not None else None
                if not hidden:
                    if win_alive is False:
                        print("\nHUD 창이 닫혀 백그라운드 브리지를 자동 종료합니다.")
                        break
                    if win_alive is None and (bridge.clock() - hud_ping["last"]) >= idle_exit_sec:
                        print("\nHUD 창이 닫혀 백그라운드 브리지를 자동 종료합니다.")
                        break
            st = bridge.step()
            steps += 1
            strat = st.get("strategy")
            coach = st.get("coach")
            if st["connected"] and (strat or coach):
                mm, ss = divmod(int(st["game_time_sec"]), 60)
                wp = strat["win_prob"] if strat else coach["my_win_prob"]
                if tty:  # 터미널이면 한 줄을 덮어 쓴다
                    print(f"\r게임 중 {mm:02d}:{ss:02d} · 판세 {wp * 100:.0f}% · "
                          f"다음 스킬 {st['next_skill'] or '-'}   ", end="", flush=True)
                else:  # macOS .app / Windows pythonw 의 launcher.log: 승률·스킬이 바뀔 때만 한 줄
                    line = f"판세 {wp * 100:.0f}% · 다음 스킬 {st['next_skill'] or '-'}"
                    if line != last_line:
                        print(f"게임 중 {mm:02d}:{ss:02d} · {line}", flush=True)
                        last_line = line
            sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print()
    finally:
        httpd.shutdown()


# ── 입출력 ──
# 실제 응답 기록 (AGENTS.md 3-4 "실제 응답 기록 --record")
ID_KEYS = ("summonerName", "riotId", "riotIdGameName", "riotIdTagLine", "Recipient", "KillerName", "VictimName")


def mask_identities(data, names: dict[str, str]):
    """선수 식별 값을 한 실행 안에서 일관된 가명(P1…)으로 바꾼 사본. names 는 실행 동안 이어 쓴다."""
    def alias(v):
        if not isinstance(v, str) or not v:
            return v
        if v not in names:
            names[v] = f"P{len(names) + 1}"
        return names[v]

    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in ID_KEYS:
                out[k] = alias(v)
            elif k == "Assisters" and isinstance(v, list):
                out[k] = [alias(x) for x in v]
            else:
                out[k] = mask_identities(v, names)
        return out
    if isinstance(data, list):
        return [mask_identities(x, names) for x in data]
    return data


class Recorder:
    """LIVE 응답을 게임 시각 1분마다 한 번 가명 처리해 남긴다. 실패해도 브리지는 계속 돈다."""

    def __init__(self, folder: str, clock: Callable[[], float] = time.time):
        self.dir = os.path.join(folder, time.strftime("%Y%m%d-%H%M%S", time.localtime(clock())))
        self.names: dict[str, str] = {}
        self.last_minute: int | None = None
        self.warned = False

    def __call__(self, data: dict) -> None:
        try:
            gt = float(data["gameData"]["gameTime"])
        except (KeyError, TypeError, ValueError):
            return
        minute = int(gt // 60)
        if minute == self.last_minute:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            mm, ss = divmod(int(gt), 60)
            path = os.path.join(self.dir, f"allgamedata_{mm:02d}m{ss:02d}s.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(mask_identities(data, self.names), f, ensure_ascii=False)
            self.last_minute = minute
        except OSError as e:
            if not self.warned:
                print(f"\n응답 기록 실패(브리지는 계속): {e}", file=sys.stderr)
                self.warned = True


def recording(fetch: Callable[[], dict], recorder: Callable[[dict], None]) -> Callable[[], dict]:
    def wrapped() -> dict:
        data = fetch()
        recorder(data)
        return data
    return wrapped


_ctx_cache: dict = {}


def _live_context(cafile: str | None, insecure_local: bool) -> ssl.SSLContext:
    """게임 API 용 TLS 설정. 2초마다 읽으므로 한 번 만들어 다시 쓴다(인증서 파일을 매번 읽지 않게)."""
    key = (cafile, bool(insecure_local))
    if key in _ctx_cache:
        return _ctx_cache[key]
    # 게임 클라이언트는 Riot 이 서명한 자체 인증서를 쓴다. Riot 개발자 문서의 riotgames.pem 으로 확인한다.
    # 확인을 끄는 것은 사용자가 --insecure-local 로 명시했을 때만, 그것도 이 PC(127.0.0.1) 주소에만 허용한다.
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
        ctx.check_hostname = False  # 인증서 이름이 127.0.0.1 과 다를 수 있다. 주소는 위에서 이 PC 로 제한했다
    elif insecure_local:
        ctx = ssl._create_unverified_context()
    else:
        raise ValueError("--cafile riotgames.pem 을 주거나, 이 PC 에서만 --insecure-local 을 명시한다")
    _ctx_cache[key] = ctx
    return ctx


def fetch_live(base: str, cafile: str | None, insecure_local: bool = False) -> dict:
    if not base.startswith(("https://127.0.0.1", "https://localhost")):
        raise ValueError("로컬 게임 API(127.0.0.1)만 읽는다")
    ctx = _live_context(cafile, insecure_local)
    with urllib.request.urlopen(base + "/liveclientdata/allgamedata", timeout=3, context=ctx) as r:
        return json.load(r)


def post_json(api: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(api.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def post_coach(api: str, state: dict) -> dict:
    return post_json(api, "/coach", state)


def post_strategy(api: str, live_context: dict) -> dict:
    return post_json(api, "/strategy", live_context)


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
                    help="Windows 에서 처음 10초(1초 간격) 재고정 뒤에도 N초마다 다시 항상 위로 고정 (기본 0 = 처음 10초만)")
    ap.add_argument("--record", help="LIVE 응답을 1분마다 가명 처리해 이 폴더에 남긴다 (실게임 시험용)")
    ap.add_argument("--log-file", help="터미널 없는 백그라운드 실행(pythonw) 시 표준 출력을 이 파일에 남긴다")
    ap.add_argument("--exit-on-close", action="store_true",
                    help="HUD 창이 닫혀 폴링이 끊기면 백그라운드 브리지를 자동 종료한다")
    ap.add_argument("--clip-caption-px", type=int, default=0,
                    help="Windows 에서 Chrome 제목 띠가 남을 때 위쪽 N px 를 SetWindowRgn 으로 잘라낸다 (기본 0 = 끔)")
    ap.add_argument("--disable-gpu-compositing", action="store_true",
                    help="Windows WS_EX_LAYERED 뒤 검은 화면이 나올 때 Chrome --disable-gpu-compositing 을 켠다")
    ap.add_argument("--save-match", action="store_true",
                    help="오버레이 모드에서 경기가 끝나면(--file 이면 바로) 전처리 피처를 POST /matches 로 한 번 저장")
    a = ap.parse_args(argv)
    _configure_headless_stdio(a.log_file)
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
    elif a.overlay or a.launch_popup:
        prices = load_item_prices(a.api) or None
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            saved = json.load(f)
        fetch = lambda: saved  # noqa: E731
        max_polls = 1
    else:
        fetch = lambda: fetch_live(a.live, a.cafile, a.insecure_local)  # noqa: E731
        max_polls = None
        if a.record:
            fetch = recording(fetch, Recorder(a.record))

    if a.overlay or a.launch_popup:
        bridge = OverlayBridge(fetch, lambda s: post_coach(a.api, s), a.side, prices,
                               source="file" if a.file else "live",
                               save=(lambda p: post_match(a.api, p)) if a.save_match else None,
                               strategy=lambda ctx: post_strategy(a.api, ctx))
        run_overlay(bridge, a.listen_port, a.api, popup=a.launch_popup, repin_sec=a.repin_sec,
                    exit_on_close=a.exit_on_close, clip_caption_px=a.clip_caption_px,
                    disable_gpu_compositing=a.disable_gpu_compositing)
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

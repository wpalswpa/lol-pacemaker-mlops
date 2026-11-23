"""실시간 인게임 종합 상황 기반 유동 전략 엔진 (AGENTS.md 3-4·4절 POST /strategy).

기존 10분 선형 페이스 환산(× 10/t)의 한계를 대체하기 위해,
게임 진행 중 Live Client API(allgamedata)에서 공개되는 모든 정보:
  - 경과 시각 및 페이즈(초반/중반/후반)
  - 내 보유 골드(귀환 조언 전용)·KDA·CS/분·보유 아이템
  - 아군·상대 10명 전원(전 라인 유불리, 강타 정글 추정, 아군 캐리, 상대 5인 조합과 아이템)
  - 양 팀 아이템 골드 추정 격차·생존 인원·부활 타이머
  - 일반 드래곤 스택(영혼)·장로 드래곤·전령·공허 유충·바론·포탑·억제기·최근 3분 교전 모멘텀
을 종합 평가해 실시간 판세·동적 빌드·모순 없는 우선순위 전략을 생성한다.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from api.build import (
    ENEMY_HEAL_ITEMS,
    ENEMY_TANK_ITEMS,
    GAME,
    RULES,
    TIER2_BOOTS_IDS,
    advise as build_advise,
    find_champion,
    resolve_my,
    resolve_threat,
)

Side = Literal["blue", "red"]
Phase = Literal["early", "mid", "late"]

LANE_ORDER = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
LANE_KO = {
    "TOP": "탑",
    "JUNGLE": "정글",
    "MIDDLE": "미드",
    "BOTTOM": "원딜",
    "UTILITY": "서폿",
}
PHASE_KO = {
    "early": "초반 라인전",
    "mid": "중반 운영",
    "late": "후반 한타",
}
STATE_KO = {"ahead": "우세", "even": "박빙", "behind": "열세"}

# 추천 코어/신발의 하위 조합 아이템 (보유 하위템 가격 차감 계산용, game_data.json 기준)
ITEM_COMPONENTS: dict[int, tuple[int, ...]] = {
    3047: (1029,),           # 판금 장화 <- 천 갑옷
    3111: (1033,),           # 헤르메스의 발걸음 <- 마법무효화의 망토
    3089: (1058, 1058),      # 라바돈의 죽음모자 <- 쓸데없이 큰 지팡이 ×2
    3031: (1038, 1037),      # 무한의 대검 <- B.F. 대검 + 곡괭이
    3157: (1058, 1029),      # 존야의 모래시계 <- 쓸데없이 큰 지팡이 + 천 갑옷
    3102: (1058, 1033),      # 밴시의 장막 <- 쓸데없이 큰 지팡이 + 마법무효화의 망토
    3026: (1038, 1029),      # 수호 천사 <- B.F. 대검 + 천 갑옷
    3156: (1037, 1033),      # 맬모셔스의 아귀 <- 곡괭이 + 마법무효화의 망토
    3053: (1037, 1028),      # 스테락의 도전 <- 곡괭이 + 루비 수정
    3071: (1037, 1028),      # 칠흑의 양날 도끼 <- 곡괭이 + 루비 수정
    3068: (1029, 1028),      # 태양불꽃 방패 <- 천 갑옷 + 루비 수정
    3142: (1037,),           # 요우무의 유령검 <- 곡괭이
    3814: (1037, 1028),      # 밤의 끝자락 <- 곡괭이 + 루비 수정
    4401: (1033, 1028),      # 대자연의 힘 <- 마법무효화의 망토 + 루비 수정
    3050: (1029, 1028),      # 지크의 융합 <- 천 갑옷 + 루비 수정
    3190: (1029, 1033, 1028),  # 강철의 솔라리 펜던트
}

OFFENSIVE_CATEGORIES = frozenset({"오브젝트 기회", "수적 우위", "영혼 확정", "용 타이밍", "스노우볼", "템포 가속"})


class LivePlayer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    champion: str | None = Field(default=None, max_length=60)
    position: str = Field(default="", max_length=20)
    has_smite: bool = False
    level: int = Field(default=1, ge=1, le=18)
    kills: int = Field(default=0, ge=0, le=200)
    deaths: int = Field(default=0, ge=0, le=200)
    assists: int = Field(default=0, ge=0, le=400)
    cs: int = Field(default=0, ge=0, le=3000)
    current_gold: float = Field(default=0.0, ge=0.0, le=200000.0)
    item_gold: float = Field(default=0.0, ge=0.0, le=200000.0)
    items: list[int] = Field(default_factory=list, max_length=12)
    is_dead: bool = False
    respawn_timer: float = Field(default=0.0, ge=0.0, le=300.0)


class LiveObjectives(BaseModel):
    model_config = ConfigDict(extra="ignore")

    my_dragons: int = Field(default=0, ge=0, le=10)
    enemy_dragons: int = Field(default=0, ge=0, le=10)
    my_elders: int = Field(default=0, ge=0, le=10)
    enemy_elders: int = Field(default=0, ge=0, le=10)
    my_grubs: int = Field(default=0, ge=0, le=12)
    enemy_grubs: int = Field(default=0, ge=0, le=12)
    my_heralds: int = Field(default=0, ge=0, le=4)
    enemy_heralds: int = Field(default=0, ge=0, le=4)
    my_barons: int = Field(default=0, ge=0, le=10)
    enemy_barons: int = Field(default=0, ge=0, le=10)
    my_towers: int = Field(default=0, ge=0, le=11)
    enemy_towers: int = Field(default=0, ge=0, le=11)
    my_inhibs: int = Field(default=0, ge=0, le=20)
    enemy_inhibs: int = Field(default=0, ge=0, le=20)
    my_lost_turrets: dict[str, int] = Field(default_factory=dict)
    enemy_lost_turrets: dict[str, int] = Field(default_factory=dict)
    last_dragon_sec: float | None = None
    last_elder_sec: float | None = None
    last_elder_team: Literal["ally", "enemy"] | None = None
    last_baron_sec: float | None = None
    last_baron_team: Literal["ally", "enemy"] | None = None


class LiveMomentum(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recent_ally_kills: int = Field(default=0, ge=0, le=100)
    recent_enemy_kills: int = Field(default=0, ge=0, le=100)


class LiveStrategyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    game_time_sec: float = Field(default=0.0, ge=0.0, le=7200.0)
    my_side: Side = "blue"
    first_blood: Side | None = None
    me: LivePlayer = Field(default_factory=LivePlayer)
    allies: list[LivePlayer] = Field(default_factory=list, max_length=5)
    enemies: list[LivePlayer] = Field(default_factory=list, max_length=5)
    objectives: LiveObjectives = Field(default_factory=LiveObjectives)
    momentum: LiveMomentum = Field(default_factory=LiveMomentum)


def game_phase(game_time_sec: float) -> Phase:
    if game_time_sec < 840.0:
        return "early"
    if game_time_sec < 1500.0:
        return "mid"
    return "late"


def _norm_pos(pos: str) -> str:
    p = (pos or "").strip().upper()
    if p in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"):
        return p
    aliases = {"MID": "MIDDLE", "BOT": "BOTTOM", "ADC": "BOTTOM", "SUP": "UTILITY", "SUPPORT": "UTILITY", "JUG": "JUNGLE"}
    return aliases.get(p, "")


def _champ_info(name: str | None) -> dict:
    if not name:
        return {"id": None, "name": "미확인", "damage": "AD", "is_tank": False, "is_heal": False}
    cid = find_champion(name)
    if not cid:
        return {"id": None, "name": name, "damage": "AD", "is_tank": False, "is_heal": False}
    c = GAME["champions"][cid]
    cls = RULES["tag_class"].get(c["tags"][0], "fighter")
    dmg = "AP" if cls == "mage" or c["magic"] > c["attack"] else "AD"
    return {
        "id": cid,
        "name": c["name"],
        "damage": dmg,
        "is_tank": c["tags"][0] == "Tank",
        "is_heal": cid in RULES["heal_champions"],
    }


def _player_power(p: LivePlayer) -> float:
    """개별 선수의 현재 실질 성장 점수 (골드 환산)."""
    return p.item_gold + p.kills * 320.0 + p.assists * 110.0 - p.deaths * 180.0 + p.cs * 18.0 + p.level * 180.0


def _is_me_player(p: LivePlayer, me: LivePlayer) -> bool:
    """아군 목록에서 본인 행인지 판별 (ally_carry 에서 본인 제외용)."""
    if p.champion and me.champion and p.champion.lower() == me.champion.lower():
        if not p.position or not me.position or _norm_pos(p.position) == _norm_pos(me.position):
            return True
    return False


def _analyze_lanes(allies: list[LivePlayer], enemies: list[LivePlayer], me: LivePlayer) -> tuple[list[dict], bool]:
    """5개 라인별 아군 vs 상대 상황 비교. 위치 확인 선수(강타 추정 포함)가 3명 이하면 매치업 미확인."""
    known_explicit = sum(1 for p in allies + enemies if _norm_pos(p.position))
    ally_by_pos: dict[str, LivePlayer] = {}
    enemy_by_pos: dict[str, LivePlayer] = {}
    ally_unassigned: list[LivePlayer] = []
    enemy_unassigned: list[LivePlayer] = []

    for p in allies:
        pos = _norm_pos(p.position) or ("JUNGLE" if p.has_smite and "JUNGLE" not in ally_by_pos else "")
        if pos and pos not in ally_by_pos:
            ally_by_pos[pos] = p
        else:
            ally_unassigned.append(p)
    for p in enemies:
        pos = _norm_pos(p.position) or ("JUNGLE" if p.has_smite and "JUNGLE" not in enemy_by_pos else "")
        if pos and pos not in enemy_by_pos:
            enemy_by_pos[pos] = p
        else:
            enemy_unassigned.append(p)

    # W2: 1v1 커스텀 게임(예: 럼블 MIDDLE vs 에코 JUNGLE)은 포지션이 달라도 단일 매치업으로 직접 짝짓는다
    if len(allies) == 1 and len(enemies) == 1:
        a, e = allies[0], enemies[0]
        pos = _norm_pos(a.position) or _norm_pos(e.position) or "MIDDLE"
        a_info, e_info = _champ_info(a.champion), _champ_info(e.champion)
        a_gold, e_gold = a.item_gold, e.item_gold
        power_diff = _player_power(a) - _player_power(e)
        status = "ahead" if power_diff >= 650.0 else ("behind" if power_diff <= -650.0 else "even")
        return [{
            "position": pos,
            "lane_ko": "1v1 매치업",
            "status": status,
            "status_ko": STATE_KO[status],
            "gold_diff": round(a_gold - e_gold),
            "power_diff": round(power_diff),
            "is_me": bool(_is_me_player(a, me)),
            "ally": {
                "champion": a_info["name"],
                "champion_id": a_info["id"],
                "kda": f"{a.kills}/{a.deaths}/{a.assists}",
                "cs": a.cs,
                "level": a.level,
                "item_gold": round(a_gold),
                "is_dead": a.is_dead,
                "respawn_timer": round(a.respawn_timer, 1),
            },
            "enemy": {
                "champion": e_info["name"],
                "champion_id": e_info["id"],
                "kda": f"{e.kills}/{e.deaths}/{e.assists}",
                "cs": e.cs,
                "level": e.level,
                "item_gold": round(e_gold),
                "is_dead": e.is_dead,
                "respawn_timer": round(e.respawn_timer, 1),
            },
        }], False

    # 위치를 명시적으로 아는 선수가 4명 이상이어야 라인 매치업을 신뢰한다(H1)
    lanes_reliable = known_explicit >= 4

    for pos in LANE_ORDER:
        if pos not in ally_by_pos and ally_unassigned:
            ally_by_pos[pos] = ally_unassigned.pop(0)
        if pos not in enemy_by_pos and enemy_unassigned:
            enemy_by_pos[pos] = enemy_unassigned.pop(0)

    lanes = []
    for pos in LANE_ORDER:
        a = ally_by_pos.get(pos)
        e = enemy_by_pos.get(pos)
        if not a and not e:
            continue
        a_info = _champ_info(a.champion if a else None)
        e_info = _champ_info(e.champion if e else None)
        a_gold = a.item_gold if a else 0.0
        e_gold = e.item_gold if e else 0.0
        if not a or not e:
            status = "unpaired"
            status_ko = "상대만" if e else "아군만"
            gold_diff = 0
            power_diff = 0.0
        else:
            gold_diff = round(a_gold - e_gold)
            power_diff = _player_power(a) - _player_power(e)
            if power_diff >= 650.0:
                status = "ahead"
            elif power_diff <= -650.0:
                status = "behind"
            else:
                status = "even"
            status_ko = STATE_KO[status]
        lanes.append({
            "position": pos,
            "lane_ko": LANE_KO[pos] if lanes_reliable else "매치업 미확인",
            "status": status,
            "status_ko": status_ko,
            "gold_diff": gold_diff,
            "power_diff": round(power_diff),
            "is_me": bool(a and _is_me_player(a, me)),
            "ally": {
                "champion": a_info["name"],
                "champion_id": a_info["id"],
                "kda": f"{a.kills}/{a.deaths}/{a.assists}" if a else "-",
                "cs": a.cs if a else 0,
                "level": a.level if a else 0,
                "item_gold": round(a_gold),
                "is_dead": a.is_dead if a else False,
                "respawn_timer": round(a.respawn_timer, 1) if a else 0.0,
            },
            "enemy": {
                "champion": e_info["name"],
                "champion_id": e_info["id"],
                "kda": f"{e.kills}/{e.deaths}/{e.assists}" if e else "-",
                "cs": e.cs if e else 0,
                "level": e.level if e else 0,
                "item_gold": round(e_gold),
                "is_dead": e.is_dead if e else False,
                "respawn_timer": round(e.respawn_timer, 1) if e else 0.0,
            },
        })
    return lanes, lanes_reliable


def _analyze_enemy_comp(enemies: list[LivePlayer]) -> dict:
    ad_n = ap_n = tank_n = heal_n = 0
    heal_items_seen: list[int] = []
    tank_items_seen: list[int] = []
    threat_rows = []

    for e in enemies:
        info = _champ_info(e.champion)
        iset = set(e.items)
        has_heal_item = bool(iset & ENEMY_HEAL_ITEMS)
        has_tank_item = bool(iset & ENEMY_TANK_ITEMS)
        if info["damage"] == "AP":
            ap_n += 1
        else:
            ad_n += 1
        if info["is_tank"] or has_tank_item:
            tank_n += 1
        if info["is_heal"] or has_heal_item:
            heal_n += 1
        if has_heal_item:
            heal_items_seen.extend(sorted(iset & ENEMY_HEAL_ITEMS))
        if has_tank_item:
            tank_items_seen.extend(sorted(iset & ENEMY_TANK_ITEMS))
        danger = _player_power(e) + e.kills * 220.0
        threat_rows.append({
            "player": e,
            "info": info,
            "danger": danger,
            "has_heal_item": has_heal_item,
            "has_tank_item": has_tank_item,
        })

    threat_rows.sort(key=lambda r: -r["danger"])
    tags = []
    if ad_n >= 3:
        tags.append(f"물리 편중({ad_n}AD)")
    elif ap_n >= 3:
        tags.append(f"마법 편중({ap_n}AP)")
    if heal_n >= 2 or heal_items_seen:
        tags.append("회복/흡혈 강함")
    if tank_n >= 2 or tank_items_seen:
        tags.append(f"앞라인 단단({tank_n}탱)")

    top = threat_rows[0] if threat_rows else None
    return {
        "ad_count": ad_n,
        "ap_count": ap_n,
        "tank_count": tank_n,
        "heal_count": heal_n,
        "heal_items": heal_items_seen,
        "tank_items": tank_items_seen,
        "profile_label": " · ".join(tags) if tags else "균형 조합",
        "top_threat": top,
        "all_threats": threat_rows,
    }


def _select_adaptive_threat(comp: dict, state: str, game_time_sec: float) -> tuple[str | None, list[int], str | None]:
    """상대 단일 위협이 아니라 팀 전체 조합 + 아이템을 종합해 빌드 대응 유형 결정."""
    top = comp["top_threat"]
    top_champ = (top["info"]["id"] or top["info"]["name"]) if top else None
    all_enemy_items: list[int] = []
    for row in comp["all_threats"]:
        all_enemy_items.extend(row["player"].items)
    all_enemy_items = all_enemy_items[:12]

    if comp["heal_items"] or comp["heal_count"] >= 2:
        return top_champ, (comp["heal_items"] or all_enemy_items), "HEAL"
    # M2: 탱커 2명만으로 관통을 올리는 건 탱커 아이템이 보이거나 15분(900초) 이후일 때만
    if comp["tank_items"] or (comp["tank_count"] >= 2 and game_time_sec >= 900.0):
        return top_champ, (comp["tank_items"] or all_enemy_items), "TANK"
    if state in ("behind", "even"):
        if comp["ad_count"] >= 4:
            return "AD", all_enemy_items, None
        if comp["ap_count"] >= 4:
            return "AP", all_enemy_items, None
    return top_champ, (top["player"].items[:12] if top else []), None


def _owned_component_credit(item_id: int, owned_items: list[int]) -> int:
    """이미 보유한 하위템 가격 합을 차감해 실제 남은 조합 비용을 구한다(M1)."""
    comps = ITEM_COMPONENTS.get(item_id)
    if not comps:
        return 0
    pool = list(owned_items)
    credit = 0
    for cid in comps:
        if cid in pool:
            pool.remove(cid)
            credit += int((GAME["items"].get(str(cid)) or {}).get("gold", 0))
    return credit


def _recall_advice(me: LivePlayer, recommended_items: list[dict], starter_items: list[dict], game_time_sec: float) -> dict:
    gold = int(round(me.current_gold))
    # M1: 게임 시작 90초 전이고 보유 아이템이 없으면 시작 아이템 안내
    if game_time_sec < 90.0 and not me.items and starter_items:
        starter_names = " + ".join(it["name"] for it in starter_items)
        starter_cost = sum(int(it["gold"]) for it in starter_items)
        return {
            "current_gold": gold,
            "can_buy_now": gold >= starter_cost,
            "gold_needed": max(0, starter_cost - gold),
            "next_item": starter_items[0]["name"],
            "next_item_gold": starter_cost,
            "short": "시작템 구매",
            "message": f"게임 시작 · [{starter_names}] 구매 후 라인으로 이동하세요",
        }

    owned = set(me.items)
    has_any_boots = bool(owned & TIER2_BOOTS_IDS)

    def _is_owned(it: dict) -> bool:
        if it["item_id"] in owned:
            return True
        if it.get("slot") == "신발" and has_any_boots:
            return True
        return False

    next_item = next((it for it in recommended_items if not _is_owned(it)), None)
    if not next_item:
        if gold >= 1500:
            return {
                "current_gold": gold,
                "can_buy_now": True,
                "gold_needed": 0,
                "next_item": None,
                "short": "귀환 정비",
                "message": f"보유 {gold:,}G 누적 · 귀환해 상황별 아이템 또는 제어 와드를 챙기세요",
            }
        return {
            "current_gold": gold,
            "can_buy_now": False,
            "gold_needed": 0,
            "next_item": None,
            "short": "빌드 완료",
            "message": f"현재 보유 {gold:,}G · 핵심 추천 빌드 완료",
        }
    base_cost = int(next_item["gold"])
    credit = _owned_component_credit(int(next_item["item_id"]), me.items)
    effective_cost = max(0, base_cost - credit)
    diff = max(0, effective_cost - gold)
    if diff == 0:
        msg = f"지금 {gold:,}G 보유 · 웨이브 밀고 귀환하면 [{next_item['name']}] 즉시 구매 가능"
        short = "즉시 구매 가능"
    elif diff <= 350:
        msg = f"[{next_item['name']}]까지 {diff:,}G 남음 · 미니언 1~2웨이브 정리 후 귀환 추천"
        short = f"부족 {diff:,}G"
    elif gold >= 1600:
        msg = f"미사용 골드 {gold:,}G 누적 · 교전 전에 귀환해 하위템부터 스펙 전환하세요"
        short = "귀환 골드 전환"
    else:
        msg = f"보유 {gold:,}G · 다음 목표 [{next_item['name']}](남은 비용 {effective_cost:,}G)까지 {diff:,}G"
        short = f"부족 {diff:,}G"
    return {
        "current_gold": gold,
        "can_buy_now": diff == 0,
        "gold_needed": diff,
        "next_item": next_item["name"],
        "next_item_gold": effective_cost,
        "short": short,
        "message": msg,
    }


def _safe_resolve_my(champ: str | None) -> str | None:
    if not champ:
        return None
    try:
        resolve_my(champ)
        return champ
    except ValueError:
        return None


def _safe_resolve_threat(champ: str | None) -> str | None:
    if not champ:
        return None
    try:
        resolve_threat(champ)
        return champ
    except ValueError:
        return None


def analyze_live_strategy(req: LiveStrategyRequest, reference_10m_prob: float | None = None) -> dict:
    """실시간 전장 스냅샷을 종합해 판세·전 라인·동적 전략·빌드를 계산한다."""
    t = float(req.game_time_sec)
    minutes = max(t / 60.0, 0.5)
    phase = game_phase(t)
    allies = list(req.allies) if req.allies else ([req.me] if req.me.champion else [])
    enemies = list(req.enemies)
    practice_mode = len(enemies) == 0  # M4: 상대 0명(연습 모드)

    # 1. 팀 집계
    ally_kills = sum(p.kills for p in allies)
    enemy_kills = sum(p.kills for p in enemies)
    ally_cs = sum(p.cs for p in allies)
    enemy_cs = sum(p.cs for p in enemies)
    ally_item_gold = sum(p.item_gold for p in allies)
    enemy_item_gold = sum(p.item_gold for p in enemies)
    ally_lv = (sum(p.level for p in allies) / len(allies)) if allies else float(req.me.level)
    enemy_lv = (sum(p.level for p in enemies) / len(enemies)) if enemies else ally_lv
    ally_alive = sum(1 for p in allies if not p.is_dead)
    enemy_alive = sum(1 for p in enemies if not p.is_dead)
    ally_dead = len(allies) - ally_alive
    enemy_dead = len(enemies) - enemy_alive
    asymmetric = (not practice_mode) and len(allies) > 0 and len(enemies) > 0 and (len(allies) != len(enemies))
    solo_or_asymmetric = (len(allies) <= 1) or asymmetric
    gold_basis: Literal["team", "per_player"] = "per_player" if asymmetric else "team"

    # H3: 내 current_gold 는 판세·팀 골드 차에 넣지 않는다 (귀환 조언 전용)
    # P0-1: 비대칭 게임은 1인당 평균(per_player), 대칭 게임은 팀 합계(team)로 단일 기준 통일
    if practice_mode:
        ally_gold_shown = round(ally_item_gold)
        enemy_gold_shown = 0
        item_gold_diff = 0.0
        kills_diff = 0
        cs_diff = 0
        alive_diff = 0
    elif asymmetric:
        ally_gold_shown = round(ally_item_gold / len(allies))
        enemy_gold_shown = round(enemy_item_gold / len(enemies))
        item_gold_diff = float(ally_gold_shown - enemy_gold_shown)
        kills_diff = int(round((ally_kills / len(allies) - enemy_kills / len(enemies)) * len(allies)))
        cs_diff = int(round((ally_cs / len(allies) - enemy_cs / len(enemies)) * len(allies)))
        alive_diff = enemy_dead - ally_dead
    else:
        ally_gold_shown = round(ally_item_gold)
        enemy_gold_shown = round(enemy_item_gold)
        item_gold_diff = float(ally_gold_shown - enemy_gold_shown)
        kills_diff = ally_kills - enemy_kills
        cs_diff = ally_cs - enemy_cs
        alive_diff = ally_alive - enemy_alive

    est_gold_diff = item_gold_diff
    if not practice_mode and abs(item_gold_diff) < 1e-6 and (kills_diff != 0 or cs_diff != 0):
        est_gold_diff = kills_diff * 300.0 + cs_diff * 20.0

    level_diff = 0.0 if practice_mode else (ally_lv - enemy_lv)
    obj = req.objectives
    dragon_diff = obj.my_dragons - obj.enemy_dragons
    tower_diff = obj.my_towers - obj.enemy_towers
    baron_diff = obj.my_barons - obj.enemy_barons
    inhib_diff = obj.my_inhibs - obj.enemy_inhibs
    recent_diff = 0 if practice_mode else (req.momentum.recent_ally_kills - req.momentum.recent_enemy_kills)

    # 2. 시간대 인식(Phase-aware) 실시간 판세 계산 (연습 모드는 중립 0.5 고정)
    baron_active = None
    if obj.last_baron_sec is not None and 0 <= (t - obj.last_baron_sec) <= 180.0 and obj.last_baron_team:
        baron_active = obj.last_baron_team
    elder_active = None
    if obj.last_elder_sec is not None and 0 <= (t - obj.last_elder_sec) <= 150.0 and obj.last_elder_team:
        elder_active = obj.last_elder_team

    ml_blended = False
    if practice_mode:
        win_prob = 0.5
        state = "even"
        dragon_score = baron_score = tower_score = alive_score = momentum_score = 0.0
    else:
        gold_scale = 1800.0 + 135.0 * min(minutes, 30.0)
        cs_weight = 18.0 if phase == "early" else (12.0 if phase == "mid" else 5.0)
        lv_weight = 520.0 if phase == "early" else (380.0 if phase == "mid" else 220.0)

        dragon_score = dragon_diff * 420.0
        if obj.my_dragons >= 4:
            dragon_score += 900.0
        elif obj.my_dragons == 3:
            dragon_score += 350.0
        if obj.enemy_dragons >= 4:
            dragon_score -= 900.0
        elif obj.enemy_dragons == 3:
            dragon_score -= 350.0

        elder_score = (1100.0 if elder_active == "ally" else (-1100.0 if elder_active == "enemy" else 0.0))
        baron_score = baron_diff * 550.0 + (950.0 if baron_active == "ally" else (-950.0 if baron_active == "enemy" else 0.0))
        tower_score = tower_diff * 360.0 + inhib_diff * 520.0
        momentum_score = max(-600.0, min(600.0, recent_diff * 170.0))
        alive_score = alive_diff * (260.0 if phase == "early" else (420.0 if phase == "mid" else 650.0))

        econ_score = est_gold_diff * 0.72 + kills_diff * 150.0 + cs_diff * cs_weight * 0.45 + level_diff * lv_weight
        total_score = econ_score + dragon_score + elder_score + baron_score + tower_score + momentum_score + alive_score
        live_prob = 1.0 / (1.0 + math.exp(-max(-8.0, min(8.0, total_score / gold_scale))))

        if reference_10m_prob is not None and 480.0 <= t <= 720.0:
            win_prob = 0.65 * live_prob + 0.35 * float(reference_10m_prob)
            ml_blended = True
        else:
            win_prob = live_prob
        win_prob = max(0.05, min(0.95, win_prob))
        state = "ahead" if win_prob > 0.56 else ("behind" if win_prob < 0.44 else "even")

    # 3. 핵심 근거 태그
    if practice_mode:
        key_factors = ["연습 모드", f"CS {req.me.cs}", f"보유 {int(round(req.me.current_gold)):,}G"]
    else:
        gold_prefix = "1인당 골드" if gold_basis == "per_player" else "팀 골드"
        death_label = f"사망 {ally_dead}:{enemy_dead}" if asymmetric else f"생존 {ally_alive}v{enemy_alive}"
        death_mag = abs(alive_score) * 1.4 if (alive_diff != 0 and (not asymmetric or ally_dead > 0 or enemy_dead > 0)) else 0
        factor_candidates = [
            (abs(est_gold_diff), f"{gold_prefix} {'+' if est_gold_diff >= 0 else ''}{int(round(est_gold_diff)):,}G"),
            (abs(kills_diff) * 320, f"킬 {ally_kills}:{enemy_kills}"),
            (abs(dragon_score), f"드래곤 {obj.my_dragons}:{obj.enemy_dragons}"),
            (abs(tower_score), f"포탑 {obj.my_towers}:{obj.enemy_towers}"),
            (death_mag, death_label),
            (abs(momentum_score) if abs(recent_diff) >= 2 else 0, f"최근 3분 교전 {req.momentum.recent_ally_kills}:{req.momentum.recent_enemy_kills}"),
        ]
        key_factors = [label for mag, label in sorted(factor_candidates, key=lambda x: -x[0]) if mag >= 280][:3]
        if not key_factors:
            key_factors = [f"킬 {ally_kills}:{enemy_kills}", f"드래곤 {obj.my_dragons}:{obj.enemy_dragons}"]

    # 4. 전 라인 분석 & 상대 조합 분석 (연습 모드는 생략)
    if practice_mode:
        lanes, lanes_reliable = [], False
        ally_carry = None
        winning_lanes, losing_lanes = [], []
        comp = _analyze_enemy_comp([])
    else:
        lanes, lanes_reliable = _analyze_lanes(allies, enemies, req.me)
        comp = _analyze_enemy_comp(enemies)
        winning_lanes = sorted([l for l in lanes if l["status"] == "ahead"], key=lambda l: -l["power_diff"])
        losing_lanes = sorted([l for l in lanes if l["status"] == "behind"], key=lambda l: l["power_diff"])
        # M4 & P0-2: ally_carry 후보에서 나 자신(is_me) 및 1인·비대칭 게임은 제외한다
        teammate_wins = [l for l in winning_lanes if not l.get("is_me")]
        ally_carry = teammate_wins[0] if (lanes_reliable and teammate_wins and not solo_or_asymmetric) else None

    # 5. 동적 빌드 가이드 & 보유 골드 귀환 판정 (코어 완성 뒤에도 후속 코어 이어서 추천)
    threat_target, threat_items, comp_override = _select_adaptive_threat(comp, state, t)
    my_champ_arg = _safe_resolve_my(req.me.champion)
    threat_arg = _safe_resolve_threat(threat_target)
    build = build_advise(win_prob, my_champ_arg, threat_arg, enemy_items=threat_items, owned_items=req.me.items)
    if comp_override == "HEAL" and build["threat_type"] != "HEAL":
        build = build_advise(win_prob, my_champ_arg, "HEAL", enemy_items=threat_items, owned_items=req.me.items)
    elif comp_override == "TANK" and build["threat_type"] != "TANK":
        build = build_advise(win_prob, my_champ_arg, "TANK", enemy_items=threat_items, owned_items=req.me.items)

    recall = _recall_advice(req.me, build["recommended_items"], build.get("starter_items") or [], t)

    # 6. 유동 전략 카드 생성
    strategies: list[dict] = []

    def add_strategy(priority: int, category: str, title: str, detail: str, reason: str) -> None:
        strategies.append({
            "priority": priority,
            "category": category,
            "title": title,
            "detail": detail,
            "reason": reason,
        })

    if practice_mode:
        # M4: 연습 모드는 귀환·빌드·파밍 가이드만 표시
        add_strategy(90, "빌드 연습", recall["message"], "상대 챔피언이 없는 연습 모드입니다. 아이템 구매 순서와 스킬 콤보·CS 파밍 리듬을 점검하세요.", "연습 모드")
        cs_per_min = req.me.cs / minutes
        add_strategy(75, "파밍 점검", f"현재 CS {req.me.cs} (분당 {cs_per_min:.1f})", "라인전 10분 기준 분당 7~8 CS 페이스를 목표로 미니언 막타를 맞춰 보세요.", f"CS/분 {cs_per_min:.1f}")
    else:
        # (A) 긴급 수적 우위·열세 / 부활 타이머 콜 (H2·P0-2·P0-3)
        dead_enemies = [e for e in enemies if e.is_dead]
        dead_jungler = next((e for e in dead_enemies if _norm_pos(e.position) == "JUNGLE" or e.has_smite), None)
        if req.me.is_dead:
            revive_tail = (
                "복귀 직후 웨이브 위치와 상대 라인 동선을 먼저 확인하세요."
                if solo_or_asymmetric
                else "복귀 직후 아군 합류 위치와 웨이브를 먼저 확인하세요."
            )
            add_strategy(
                100, "부활 준비",
                f"부활까지 {int(round(req.me.respawn_timer))}초 · 아이템 정비 후 복귀",
                recall["message"] + ". " + revive_tail,
                "본인 사망 상태",
            )
        elif alive_diff <= -2:
            outnum_title = (
                f"사망 열세(상대 우위) → 교전 금지·포탑 수성"
                if asymmetric
                else f"수적 열세({ally_alive}v{enemy_alive}) → 교전 금지·포탑 수성"
            )
            outnum_detail = (
                "무리한 교전을 피하고 포탑 앞에서 웨이브만 정리하며 시간을 버세요."
                if solo_or_asymmetric
                else "아군 합류 전까지 시야 체크나 1대1 싸움을 멈추고, 포탑 앞에서 웨이브만 정리하며 시간을 버세요."
            )
            add_strategy(
                97, "수성 경보",
                outnum_title,
                outnum_detail,
                f"사망 {ally_dead}:{enemy_dead}" if asymmetric else f"생존 인원 {ally_alive}v{enemy_alive}",
            )
        elif dead_jungler is not None and t >= 300.0 and alive_diff >= 0 and dead_jungler.respawn_timer >= 15.0:
            obj_name = "바론 또는 장로" if phase == "late" else ("드래곤/전령" if phase == "early" else "드래곤/바론")
            j_name = _champ_info(dead_jungler.champion)["name"]
            add_strategy(
                98, "오브젝트 기회",
                f"상대 정글({j_name}) 부재 {int(round(dead_jungler.respawn_timer))}초 → {obj_name} 선점",
                f"상대 강타가 비어 있는 타이밍입니다. 시야를 지우고 {obj_name} 압박 또는 포탑 철거로 연결하세요.",
                f"상대 정글 사망({int(round(dead_jungler.respawn_timer))}s)",
            )
        elif alive_diff >= 2:
            adv_title = (
                f"상대 {enemy_dead}명 부재 → 오브젝트·포탑 즉시 압박"
                if asymmetric
                else f"수적 우위({ally_alive}v{enemy_alive}) → 오브젝트·포탑 즉시 압박"
            )
            add_strategy(
                95, "수적 우위",
                adv_title,
                "상대 인원이 비어 있습니다. 무리한 킬 추격보다 포탑 채굴·드래곤·바론 중 가까운 이득을 확정하세요.",
                f"사망 {ally_dead}:{enemy_dead}" if asymmetric else f"생존 인원 {ally_alive}v{enemy_alive}",
            )

        # (B) 장로·바론·억제기·드래곤 영혼 전략 (M3 포함)
        if elder_active == "ally":
            add_strategy(
                94, "장로 운영",
                "장로 버프 활성 → 교전으로 경기 마무리" if solo_or_asymmetric else "아군 장로 버프 활성 → 본대 한타로 경기 마무리",
                "처형 버프가 살아 있는 동안 시야를 밀고 교전을 강제하세요." if solo_or_asymmetric else "처형 버프가 살아 있는 동안 본대로 뭉쳐 시야를 밀고 한타를 강제하세요.",
                "장로 버프" if solo_or_asymmetric else "아군 장로 버프",
            )
        elif elder_active == "enemy":
            add_strategy(94, "수성 경보", "상대 장로 버프 활성 → 한타 회피·포탑 뒤 수성", "처형 버프 동안 정면 교전은 치명적입니다. 원거리 웨이브 정리로 버프 시간을 넘기세요.", "상대 장로 버프")

        if baron_active == "ally":
            add_strategy(
                92, "바론 운영",
                "바론 버프 활성 → 포탑 압박" if solo_or_asymmetric else "아군 바론 버프 활성 → 사이드 동시 압박",
                "미니언 강화 효과를 살려 포탑을 빠르게 철거하세요." if solo_or_asymmetric else "미니언 강화 효과를 살려 1-3-1 또는 1-4로 분산 압박하고, 뭉쳐서 한타 각을 주지 마세요.",
                "바론 버프" if solo_or_asymmetric else "아군 바론 버프",
            )
        elif baron_active == "enemy":
            add_strategy(93, "바론 수성", "상대 바론 버프 → 포탑 뒤 원거리 웨이브 클리어", "정면 한타를 피하고 강화 미니언만 빠르게 지워 버프 시간(3분)을 흘려보내세요.", "상대 바론 버프")

        if obj.enemy_inhibs > obj.my_inhibs:
            add_strategy(
                90, "억제기 수성",
                f"본진 억제기 파괴 열세({obj.my_inhibs}:{obj.enemy_inhibs}) → 슈퍼 미니언 수성 우선" if solo_or_asymmetric else f"아군 억제기 파괴 열세({obj.my_inhibs}:{obj.enemy_inhibs}) → 슈퍼 미니언 수성 우선",
                "슈퍼 미니언이 밀려오는 라인을 먼저 정리하며 본진 시야를 복구하고, 무리한 원정 교전을 피하세요.",
                "억제기 열세",
            )
        elif obj.my_inhibs > obj.enemy_inhibs:
            add_strategy(85, "억제기 운영", f"상대 억제기 파괴 우위({obj.my_inhibs}:{obj.enemy_inhibs}) → 반대편 오브젝트 압박", "슈퍼 미니언이 자동으로 라인을 미는 동안 반대편 바론·드래곤 시야를 장악해 수적 우위를 만드세요.", "억제기 우위")

        if obj.enemy_dragons == 3:
            add_strategy(89, "영혼 저지", "상대 드래곤 3스택(영혼 매치포인트) → 다음 용 1분 전 시야 선점", "다음 드래곤을 내주면 영혼이 완성됩니다. 웨이브를 먼저 밀고 용 둥지 시야와 귀환 타이밍을 맞추세요.", "상대 3용 경고")
        elif obj.my_dragons == 3:
            add_strategy(
                86, "영혼 확정",
                "드래곤 3스택 → 용 타이밍 맞춰 시야 선점" if solo_or_asymmetric else "아군 드래곤 3스택 → 용 타이밍 맞춰 본대 합류",
                "다음 드래곤으로 영혼을 확정할 수 있습니다. 1분 전 귀환해 아이템과 시야를 정비하고 먼저 자리 잡으세요.",
                "3용 매치포인트" if solo_or_asymmetric else "아군 3용 매치포인트",
            )
        elif obj.last_dragon_sec is not None:
            next_drake = (obj.last_dragon_sec + 300.0) - t
            if 0 <= next_drake <= 75.0:
                add_strategy(
                    84, "용 타이밍",
                    f"다음 드래곤 {int(round(next_drake))}초 전 → 웨이브 정리 후 시야 선점" if solo_or_asymmetric else f"다음 드래곤 {int(round(next_drake))}초 전 → 웨이브 정리 후 합류",
                    "라인을 먼저 밀어두고 귀환·와드 설치 뒤 바텀 강가 주도권을 선점하세요.",
                    f"드래곤 리스폰 임박({int(round(next_drake))}s)",
                )

        # (C) 내 보유 골드 & 귀환 타이밍 전략 (M1 반영)
        if t < 90.0 and not req.me.items:
            start_detail = (
                "초반 시작 아이템을 챙기고 라인 선점 위치를 잡으세요."
                if solo_or_asymmetric
                else "초반 시작 아이템을 챙기고 아군 정글 리시 또는 라인 선점 위치를 잡으세요."
            )
            add_strategy(88, "귀환 타이밍", recall["message"], start_detail, "게임 시작")
        elif recall["can_buy_now"] and recall["next_item"]:
            add_strategy(88, "귀환 타이밍", f"{recall['current_gold']:,}G 보유 → [{recall['next_item']}] 즉시 구매 각", f"지금 귀환하면 [{recall['next_item']}]을 바로 뽑을 수 있습니다. 대포 웨이브를 밀어 넣고 귀환해 아이템 파워 스파이크를 먼저 잡으세요.", f"보유 골드 {recall['current_gold']:,}G >= {recall['next_item_gold']:,}G")
        elif recall["current_gold"] >= 1600:
            add_strategy(82, "골드 전환", f"미사용 골드 {recall['current_gold']:,}G 누적 → 교전 전 귀환 필수", "주머니에 든 골드는 전투력이 아닙니다. 큰 싸움 전에 반드시 귀환해 아이템 격차를 실제 스펙으로 바꾸세요.", f"미사용 골드 {recall['current_gold']:,}G")
        elif 0 < recall["gold_needed"] <= 350 and recall["next_item"]:
            add_strategy(74, "파워 스파이크", f"[{recall['next_item']}]까지 {recall['gold_needed']:,}G → 2웨이브 후 귀환", f"무리한 딜교 대신 안전하게 {recall['gold_needed']:,}G만 채우고 귀환하면 다음 교전에서 확실한 아이템 우위를 잡습니다.", f"다음 아이템 근접(-{recall['gold_needed']:,}G)")

        # (D) 전 라인 상황 기반 매크로 운영 (H1·P0-2: lanes_reliable 이고 대칭 멀티플레이어일 때만!)
        if lanes_reliable and not solo_or_asymmetric:
            if ally_carry and losing_lanes:
                weak = losing_lanes[0]
                add_strategy(
                    80, "전 라인 운영",
                    f"{ally_carry['lane_ko']}({ally_carry['ally']['champion']}) 중심 설계 · {weak['lane_ko']} 무리 금지",
                    f"{ally_carry['lane_ko']}이 +{ally_carry['gold_diff']:,}G로 앞서고 {weak['lane_ko']}은 {weak['gold_diff']:,}G 밀립니다. "
                    f"밀리는 {weak['lane_ko']}을 억지로 구하려다 역전당하지 말고, 강한 {ally_carry['lane_ko']} 쪽 오브젝트와 합류 교전으로 굴리세요.",
                    f"라인 격차 ({ally_carry['lane_ko']} 우세 / {weak['lane_ko']} 열세)",
                )
            elif ally_carry:
                add_strategy(
                    76, "스노우볼",
                    f"아군 핵심 캐리 {ally_carry['lane_ko']}({ally_carry['ally']['champion']}) 보좌",
                    f"{ally_carry['lane_ko']}이 {ally_carry['ally']['kda']}(+{ally_carry['gold_diff']:,}G)로 성장했습니다. "
                    "시야를 깔아주고 그쪽 라인 주도권을 포탑·오브젝트로 연결하세요.",
                    f"{ally_carry['lane_ko']} 라인 우세",
                )
            elif losing_lanes:
                weak = losing_lanes[0]
                add_strategy(
                    78, "열세 복구",
                    f"{weak['lane_ko']}({weak['enemy']['champion']}) 경계 · 합류전·파밍으로 반전",
                    f"상대 {weak['lane_ko']}({weak['enemy']['kda']})이 가장 크게 성장했습니다. 정면 1대1을 피하고 수적 우위(2v1·3v2)로 현상금을 노리세요.",
                    f"{weak['lane_ko']} 열세({weak['gold_diff']:,}G)",
                )

        # (E) 상대 핵심 위협·아이템·조합 카운터 전략 (M2 반영)
        top_th = comp["top_threat"]
        if top_th:
            th_p: LivePlayer = top_th["player"]
            th_name = top_th["info"]["name"]
            gold_gap_vs_me = round(th_p.item_gold - req.me.item_gold)
            if comp["heal_items"] or comp["heal_count"] >= 2:
                add_strategy(
                    83, "조합 카운터",
                    f"상대 치유·흡혈 강함({th_name} 등) → 치감 하위템 우선",
                    f"상대 조합({comp['profile_label']})은 장기전에서 회복량이 큽니다. [{build['recommended_items'][1]['name']}] 우선 확보 후 짧은 폭딜 위주로 싸우세요.",
                    "치유/흡혈 위협 감지",
                )
            elif comp["tank_items"] or (comp["tank_count"] >= 2 and t >= 900.0):
                add_strategy(
                    79, "조합 카운터",
                    f"상대 앞라인 단단({comp['tank_count']}탱) → 관통 준비·뒷라인 포커싱",
                    f"앞라인에 스킬을 낭비하지 말고 [{build['recommended_items'][1]['name']}]로 관통을 챙겨 뒷라인 딜러를 노리세요.",
                    "탱커/방어 코어 감지",
                )
            elif gold_gap_vs_me >= 800:
                th_detail = (
                    f"{th_name}({th_p.kills}/{th_p.deaths}/{th_p.assists})이 아이템 격차로 앞서 있습니다. 시야 밖 기습을 경계하고 포탑 근처에서 안전하게 파밍하세요."
                    if solo_or_asymmetric
                    else f"{th_name}({th_p.kills}/{th_p.deaths}/{th_p.assists})이 아이템 격차로 앞서 있습니다. 시야 밖 기습을 경계하고 아군 CC와 함께 제압하세요."
                )
                add_strategy(
                    81, "위협 경계",
                    f"상대 {th_name} 아이템 +{gold_gap_vs_me:,}G 우위 → 단독 교전 회피",
                    th_detail,
                    f"상대 위협 아이템 격차 +{gold_gap_vs_me:,}G",
                )
            elif comp["ad_count"] >= 3 or comp["ap_count"] >= 3:
                add_strategy(
                    72, "조합 대응",
                    f"상대 {comp['profile_label']} → 방어 아이템 효율 상승",
                    f"상대 피해가 한쪽에 몰려 있습니다. [{build['recommended_items'][0]['name']}]·방어 하위템 효율이 매우 높습니다.",
                    comp["profile_label"],
                )

        # (F) 최근 교전 모멘텀 & 내 파밍(CS/분) (H1: 위치 미확인/서폿이면 CS 경고 생략)
        my_pos = _norm_pos(req.me.position)
        cs_per_min = req.me.cs / minutes
        if recent_diff <= -3:
            add_strategy(85, "템포 조절", f"최근 3분 연속 실점({req.momentum.recent_ally_kills}:{req.momentum.recent_enemy_kills}) → 싸움 멈추고 정비", "연달아 끊기며 흐름이 넘어갔습니다. 각자 사이드 파밍과 시야 복구로 템포를 끊고 다음 코어 타이밍을 기다리세요.", "최근 3분 열세 모멘텀")
        elif recent_diff >= 3:
            add_strategy(77, "템포 가속", f"최근 3분 교전 압도({req.momentum.recent_ally_kills}:{req.momentum.recent_enemy_kills}) → 시야 장악 후 오브젝트", "기세가 완전히 넘어왔습니다. 적 정글 시야를 밀어 넣고 포탑·오브젝트로 격차를 굳히세요.", "최근 3분 우세 모멘텀")

        if phase == "early" and lanes_reliable and my_pos not in ("", "UTILITY", "JUNGLE") and t >= 240.0 and cs_per_min < 5.5:
            add_strategy(70, "성장 보완", f"현재 분당 CS {cs_per_min:.1f} → 무리한 로밍보다 라인 CS 집중", "초반 CS 격차가 누적되면 1코어 타이밍이 밀립니다. 대포 미니언을 놓치지 말고 라인을 먼저 정리한 뒤 움직이세요.", f"CS/분 {cs_per_min:.1f}")
        elif phase == "late" and len(strategies) < 3:
            late_detail = (
                "한 번 잘리면 바로 바론·넥서스로 이어집니다. 무리한 추격을 피하고 웨이브와 포탑 중심으로 오브젝트를 준비하세요."
                if solo_or_asymmetric
                else "한 번 잘리면 바로 바론·넥서스로 이어집니다. 아군과 간격을 유지하며 본대 중심으로 오브젝트를 준비하세요."
            )
            add_strategy(71, "후반 운영", "후반 부활 시간 김 → 단독 사이드·시야 체크 금지", late_detail, "후반 25분+ 구간")

        # H2: 모순 콜 필터링
        cats_present = {s["category"] for s in strategies}
        if "수성 경보" in cats_present:
            strategies = [s for s in strategies if s["category"] not in OFFENSIVE_CATEGORIES]
        if {"수적 우위", "오브젝트 기회"} & cats_present:
            strategies = [s for s in strategies if s["category"] != "템포 조절"]

        if len(strategies) < 2:
            add_strategy(65, "라인전 기본", f"{PHASE_KO[phase]} 주도권 확보 · 선 2~3레벨 경험치 관리", "미니언 선푸시로 레벨 타이밍을 먼저 잡고, 첫 귀환 골드를 모아 하위템 우위를 만드세요.", PHASE_KO[phase])

    strategies.sort(key=lambda s: -s["priority"])
    top_strategies = strategies[:3]

    gold_txt = f"{'+' if est_gold_diff >= 0 else ''}{int(round(est_gold_diff)):,}G"
    s1 = top_strategies[0]
    s2 = top_strategies[1] if len(top_strategies) > 1 else None
    if practice_mode:
        summary_briefing = f"[연습 모드] {s1['title']}. {s1['detail']}"
    else:
        summary_briefing = f"[{PHASE_KO[phase]}] 판세 {round(win_prob * 100)}%({STATE_KO[state]}, 추정 골드 {gold_txt}). {s1['title']}: {s1['detail']}"
        if s2:
            summary_briefing += f" 이어서 {s2['title']}에 집중하세요."

    tactical_badge = "연습 모드 · 빌드·귀환 가이드" if practice_mode else {
        ("early", "ahead"): "초반 우세 · 라인 주도권 압박",
        ("early", "even"): "초반 박빙 · CS·귀환 타이밍 집중",
        ("early", "behind"): "초반 열세 · 안전 파밍·갱킹 회피",
        ("mid", "ahead"): "중반 우세 · 오브젝트 스노우볼",
        ("mid", "even"): "중반 박빙 · 시야 선점·합류 우선",
        ("mid", "behind"): "중반 열세 · 수성·현상금 역전 노림",
        ("late", "ahead"): "후반 우세 · 바론·영혼 굳히기",
        ("late", "even"): "후반 박빙 · 잘림 주의·한타 집중",
        ("late", "behind"): "후반 열세 · 포탑 수성·한타 반전",
    }[(phase, state)]
    tactical_badge_short = "연습 모드" if practice_mode else {
        ("early", "ahead"): "초반 우세",
        ("early", "even"): "초반 박빙",
        ("early", "behind"): "초반 열세",
        ("mid", "ahead"): "중반 우세",
        ("mid", "even"): "중반 박빙",
        ("mid", "behind"): "중반 열세",
        ("late", "ahead"): "후반 우세",
        ("late", "even"): "후반 박빙",
        ("late", "behind"): "후반 열세",
    }[(phase, state)]

    return {
        "game_time_sec": round(t, 1),
        "phase": phase,
        "phase_label": PHASE_KO[phase],
        "win_prob": round(win_prob, 4),
        "state": state,
        "state_ko": STATE_KO[state],
        "tactical_badge": tactical_badge,
        "tactical_badge_short": tactical_badge_short,
        "ml_blended": ml_blended,
        "practice_mode": practice_mode,
        "lanes_reliable": lanes_reliable,
        "gold_basis": gold_basis,
        "team_gold": {"ally": ally_gold_shown, "enemy": enemy_gold_shown},
        "team_gold_diff": int(round(est_gold_diff)),
        "kills": {"ally": ally_kills, "enemy": enemy_kills},
        "alive": {"ally": ally_alive, "enemy": enemy_alive},
        "dead": {"ally": ally_dead, "enemy": enemy_dead},
        "key_factors": key_factors,
        "lanes": lanes,
        "ally_carry": ally_carry["lane_ko"] + f"({ally_carry['ally']['champion']})" if ally_carry else None,
        "enemy_comp": {
            "ad_count": comp["ad_count"],
            "ap_count": comp["ap_count"],
            "tank_count": comp["tank_count"],
            "heal_count": comp["heal_count"],
            "profile_label": comp["profile_label"],
            "top_threat": (
                {
                    "champion": comp["top_threat"]["info"]["name"],
                    "kda": f"{comp['top_threat']['player'].kills}/{comp['top_threat']['player'].deaths}/{comp['top_threat']['player'].assists}",
                    "item_gold": round(comp["top_threat"]["player"].item_gold),
                }
                if comp["top_threat"]
                else None
            ),
        },
        "objectives": {
            "my_dragons": obj.my_dragons,
            "enemy_dragons": obj.enemy_dragons,
            "my_elders": obj.my_elders,
            "enemy_elders": obj.enemy_elders,
            "my_grubs": obj.my_grubs,
            "enemy_grubs": obj.enemy_grubs,
            "my_towers": obj.my_towers,
            "enemy_towers": obj.enemy_towers,
            "my_inhibs": obj.my_inhibs,
            "enemy_inhibs": obj.enemy_inhibs,
            "my_barons": obj.my_barons,
            "enemy_barons": obj.enemy_barons,
            "baron_active": baron_active,
            "elder_active": elder_active,
        },
        "recall_advice": recall,
        "strategies": top_strategies,
        "build_advice": build,
        "summary_briefing": summary_briefing,
    }

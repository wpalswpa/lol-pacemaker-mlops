"""상황별 아이템·스킬 가이드 (AGENTS.md 4절 "빌드 가이드 정의").

모델은 상태(ahead·even·behind)만 정한다. 아이템과 스킬은 사람이 정한 규칙표(build_rules.json)가 정하고,
아이템 이름·가격은 Data Dragon 에서 만든 game_data.json 에서 읽는다. 이 파일은 표를 적용만 한다.
"""
import json
import os

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "build_rules.json"), encoding="utf-8") as f:
    RULES = json.load(f)
with open(os.path.join(_HERE, "game_data.json"), encoding="utf-8") as f:
    GAME = json.load(f)

DEFAULT_MY = "AD"
DEFAULT_THREAT = "AP"
SLOTS = {1: "신발", 2: "하위템", 3: "코어"}


def _key(s: str) -> str:
    return "".join(s.split()).lower()


# 영문 ID·한국어 이름 → 영문 ID ("리 신", "leesin", "LeeSin" 모두 같은 챔피언)
_INDEX = {}
for _cid, _c in GAME["champions"].items():
    _INDEX[_key(_cid)] = _cid
    _INDEX[_key(_c["name"])] = _cid


def find_champion(s: str) -> str | None:
    return _INDEX.get(_key(s))


def _damage(cid: str, cls: str) -> str:
    c = GAME["champions"][cid]
    return "AP" if cls == "mage" or c["magic"] > c["attack"] else "AD"


def resolve_my(value: str | None) -> dict:
    """내 챔피언 또는 역할군 → {champion_id, champion, class, damage}. 모르는 이름이면 ValueError."""
    s = (value or DEFAULT_MY).strip()
    if s.upper() in RULES["role_class"]:
        cls = RULES["role_class"][s.upper()]
        return {"champion_id": None, "champion": None, "class": cls, "damage": "AP" if cls == "mage" else "AD"}
    cid = find_champion(s)
    if cid is None:
        raise ValueError(f"모르는 챔피언 또는 역할군: {s}")
    cls = RULES["tag_class"][GAME["champions"][cid]["tags"][0]]
    return {"champion_id": cid, "champion": GAME["champions"][cid]["name"], "class": cls, "damage": _damage(cid, cls)}


def resolve_threat(value: str | None) -> dict:
    """상대 위협 챔피언 또는 유형 → {champion_id, champion, threat}. 모르는 이름이면 ValueError."""
    s = (value or DEFAULT_THREAT).strip()
    if s.upper() in RULES["threat_types"]:
        return {"champion_id": None, "champion": None, "threat": s.upper()}
    cid = find_champion(s)
    if cid is None:
        raise ValueError(f"모르는 챔피언 또는 위협 유형: {s}")
    c = GAME["champions"][cid]
    if cid in RULES["heal_champions"]:
        threat = "HEAL"
    elif c["tags"][0] == "Tank":
        threat = "TANK"
    else:
        threat = _damage(cid, RULES["tag_class"][c["tags"][0]])
    return {"champion_id": cid, "champion": c["name"], "threat": threat}


def state_of(my_win_prob: float) -> str:
    """pace 와 같은 경계: > 0.55 ahead, < 0.45 behind, 그 사이 even."""
    if my_win_prob > 0.55:
        return "ahead"
    if my_win_prob < 0.45:
        return "behind"
    return "even"


def _item(step: int, item_id: int, why_key: str) -> dict:
    it = GAME["items"][str(item_id)]
    return {"step": step, "slot": SLOTS[step], "item_id": item_id, "name": it["name"], "gold": it["gold"],
            "why": RULES["why"][why_key]}


def _extra(item_id: int, why_key: str) -> dict:
    it = GAME["items"][str(item_id)]
    return {"item_id": item_id, "name": it["name"], "gold": it["gold"], "why": RULES["why"][why_key]}


def _situational(pierce: str, taken: set[int]) -> list[dict]:
    """AGENTS.md 규칙표 적용 순서 8: 회복 대응 → 관통 → 물리 방어 → 마법 방어, 이미 고른 것과 겹치면 뺀다."""
    out, seen = [], set(taken)
    for item_id, why in ((RULES["anti_heal"][pierce], "situational_heal"),
                         (RULES["anti_tank"][pierce], "situational_tank"),
                         (RULES["defensive_component"]["AD"], "situational_ad"),
                         (RULES["defensive_component"]["AP"], "situational_ap")):
        if item_id not in seen:
            seen.add(item_id)
            out.append(_extra(item_id, why))
    return out[:3]


ENEMY_HEAL_ITEMS = frozenset({3153, 3072, 6673, 3033, 3065, 6616, 1053})
ENEMY_TANK_ITEMS = frozenset({3068, 3075, 3084, 6665, 3143, 3110, 3001, 2502})
TIER2_BOOTS_IDS = frozenset({3006, 3009, 3020, 3047, 3111, 3158, 3010})


def _enemy_tactical_tip(state: str, threat: str, detected_by_item: str | None) -> str:
    if detected_by_item == "HEAL":
        return "상대 회복·흡혈 코어 감지 -> 치유 감소 하위템 우선 · 장기 교전 금지"
    if detected_by_item == "TANK":
        return "상대 방어·체력 코어 감지 -> 관통 하위템 우선 · 뒷라인 포커싱"
    if state == "behind":
        return f"열세 + 상대 {threat} 위협 -> 방어 하위템 먼저 챙기고 1대1 회피"
    if state == "ahead":
        return f"우세 -> 핵심 딜 코어로 스노우볼 · 상대 {threat} 견제 시야 확보"
    return f"접전 -> 미니언 파밍 유지하며 상대 {threat} 아이템 완성 타이밍 체크"


def _enemy_tactical_tip_short(state: str, threat: str, detected_by_item: str | None) -> str:
    if detected_by_item == "HEAL":
        return "치감 우선"
    if detected_by_item == "TANK":
        return "관통 우선"
    if state == "behind":
        return f"{threat} 방어 우선"
    if state == "ahead":
        return "딜 코어 압박"
    return "파밍·타이밍"


def _core_item(step: int, slot: str, item_id: int, why_key: str) -> dict:
    it = GAME["items"][str(item_id)]
    return {
        "step": step,
        "slot": slot,
        "item_id": item_id,
        "name": it["name"],
        "gold": it["gold"],
        "why": RULES["why"][why_key],
    }


def _extend_for_owned(
    picked: list[dict],
    owned_items: list[int],
    cls: str,
    pierce: str,
    state: str,
    threat: str,
) -> list[dict]:
    """코어 완성 이후에도 다음 코어(2~5코어·영약)를 계속 추천하도록 3슬롯 창을 전진시킨다."""
    owned = set(owned_items)
    boots, _comp, core1 = picked
    followup_list = list(RULES.get("followup_cores", {}).get(cls) or ())
    if not followup_list:
        return picked

    # 우선순위 코어 체인 구성: 1코어 → 위협 대응 코어(치감/관통/방어) → 계열 후속 코어
    chain_ids: list[int] = [core1["item_id"]]
    if threat == "HEAL":
        ah_core = (RULES.get("anti_heal_core") or {}).get(pierce)
        if ah_core and ah_core not in chain_ids:
            chain_ids.append(ah_core)
    elif threat == "TANK":
        at_core = (RULES.get("anti_tank_core") or {}).get(pierce)
        if at_core and at_core not in chain_ids:
            chain_ids.append(at_core)
    elif state == "behind" and threat in ("AD", "AP"):
        def_core = RULES["core"]["behind"][threat][cls]
        if def_core not in chain_ids:
            chain_ids.append(def_core)
    for cid in followup_list:
        if cid not in chain_ids:
            chain_ids.append(cid)

    owned_cores = [cid for cid in chain_ids if cid in owned]
    if not owned_cores:
        return picked

    has_boots = (boots["item_id"] in owned) or bool(owned & TIER2_BOOTS_IDS)
    unowned_cores = [cid for cid in chain_ids if cid not in owned]
    elixir_id = (RULES.get("elixir") or {}).get(pierce, 2140)

    def mk(step: int, slot: str, cid: int) -> dict:
        if cid == core1["item_id"]:
            return {**core1, "step": step, "slot": slot}
        if cid == elixir_id:
            return _core_item(step, slot, cid, "elixir")
        return _core_item(step, slot, cid, "core_followup")

    if not has_boots and len(unowned_cores) >= 2:
        return [
            {**boots, "step": 1, "slot": "신발"},
            mk(2, "코어", unowned_cores[0]),
            mk(3, "후속 코어", unowned_cores[1]),
        ]
    if len(unowned_cores) >= 2:
        return [
            mk(1, "코어", owned_cores[-1]),
            mk(2, "다음 코어", unowned_cores[0]),
            mk(3, "후속 코어", unowned_cores[1]),
        ]
    if len(unowned_cores) == 1:
        return [
            mk(1, "코어", owned_cores[-1]),
            mk(2, "마지막 코어", unowned_cores[0]),
            mk(3, "영약", elixir_id),
        ]
    last_two = owned_cores[-2:] if len(owned_cores) >= 2 else [owned_cores[-1], owned_cores[-1]]
    return [
        mk(1, "코어", last_two[0]),
        mk(2, "코어", last_two[1]),
        mk(3, "영약", elixir_id),
    ]


def advise(
    my_win_prob: float,
    my_champion: str | None,
    enemy_top_threat: str | None,
    enemy_items: list[int] | None = None,
    owned_items: list[int] | None = None,
) -> dict:
    """AGENTS.md "규칙표 적용 순서" 1~6 및 상대 공개 아이템·보유 코어 기반 동적 위협 보정."""
    me, th = resolve_my(my_champion), resolve_threat(enemy_top_threat)
    state, cls, dmg, threat = state_of(my_win_prob), me["class"], me["damage"], th["threat"]
    item_set = set(enemy_items or ())
    detected_by_item = None
    if item_set & ENEMY_HEAL_ITEMS:
        threat, detected_by_item = "HEAL", "HEAL"
    elif item_set & ENEMY_TANK_ITEMS and threat in ("AD", "AP", "TANK"):
        threat, detected_by_item = "TANK", "TANK"
    pierce = "tank" if cls == "tank" else dmg  # 대응 하위템을 고르는 열: 탱커는 탱커용
    hurts = threat in ("AD", "AP")  # 막아야 할 피해 유형이 정해졌나

    if state == "behind" and hurts:
        boots = _item(1, RULES["boots"]["defensive"][threat], "boots_defensive")
    else:
        boots = _item(1, RULES["boots"]["by_class"][cls], "boots_class")

    if threat == "HEAL":
        comp = _item(2, RULES["anti_heal"][pierce], "anti_heal")
    elif threat == "TANK":
        comp = _item(2, RULES["anti_tank"][pierce], "anti_tank")
    elif state == "behind":
        comp = _item(2, RULES["defensive_component"][threat], "defensive_component")
    else:
        comp = _item(2, RULES["damage_component"][cls], "damage_component")

    if state == "behind" and hurts:
        core = _item(3, RULES["core"]["behind"][threat][cls], "core_behind")
    else:
        core = _item(3, RULES["core"]["ahead"][cls], "core_ahead")

    order = RULES["skill_orders"].get(me["champion_id"] or "")
    picked = [boots, comp, core]
    if owned_items is not None:
        picked = _extend_for_owned(picked, owned_items, cls, pierce, state, threat)
    return {
        "state": state,
        "my_champion": me["champion"],
        "my_class": cls,
        "my_damage": dmg,
        "enemy_top_threat": th["champion"],
        "threat_type": threat,
        "posture": RULES["posture"][state],
        "tactical_tip": _enemy_tactical_tip(state, threat, detected_by_item),
        "tactical_tip_short": _enemy_tactical_tip_short(state, threat, detected_by_item),
        "recommended_items": picked,
        "starter_items": [_extra(i, "starter") for i in RULES["starter"][cls]],
        "situational_items": _situational(pierce, {it["item_id"] for it in picked}),
        "skill_order": {"order": order, "focus": RULES["skill_focus"][state],
                        "source": "규칙표" if order else "원칙만"},
        "basis": f"사람이 정한 규칙표(build_rules.json), 아이템은 {GAME['source']} 대조. "
                 "모델은 상태(우세·접전·열세)만 정한다.",
    }

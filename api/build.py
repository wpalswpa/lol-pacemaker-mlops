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


def champion_build(champion_id: str | None, position: str | None) -> tuple[dict | None, str | None]:
    """AGENTS.md 4절 적용 순서 0: 챔피언×포지션 빌드표. 포지션이 표에 없으면 그 챔피언의 기본 포지션."""
    builds = (RULES.get("champion_builds") or {}).get(champion_id or "")
    if not builds:
        return None, None
    pos = position if position in builds else builds["default"]
    return builds[pos], pos


def _starter_ids(cls: str, pierce: str, position: str | None, cb: dict | None) -> list[int]:
    if cb:
        return list(cb["starter"])
    ps = RULES.get("position_starter") or {}
    if position == "UTILITY":
        return list(ps["UTILITY"])
    if position == "JUNGLE":
        return list(ps["JUNGLE"][pierce])
    return list(RULES["starter"][cls])


def _plan(state: str, cls: str, pierce: str, threat: str, detected_by_item: str | None,
          cb: dict | None) -> list[tuple[int, str, str]]:
    """구매 계획 전체 순서 [(아이템, 칸 이름, why 키)] (AGENTS.md 4절 "규칙표 적용 순서").

    우세·접전: 1코어 → 신발 → (대응) → 다음 코어들 → 영약. 열세: 신발 → 하위템 → 코어 → 다음 코어들 → 영약.
    """
    hurts = threat in ("AD", "AP")
    if cb:
        boots, boots_why = cb["boots"], "champion_boots"
        cores, core_why, next_why = list(cb["cores"]), "champion_core", "champion_core"
    else:
        boots, boots_why = RULES["boots"]["by_class"][cls], "boots_class"
        first = RULES["core"]["ahead"][cls]
        cores = [first] + [c for c in RULES["followup_cores"][cls] if c != first]
        core_why, next_why = "core_ahead", "core_followup"
    counter = None
    if threat == "HEAL":
        counter = (RULES["anti_heal"][pierce], "anti_heal", RULES["anti_heal_core"][pierce])
    elif threat == "TANK":
        counter = (RULES["anti_tank"][pierce], "anti_tank", RULES["anti_tank_core"][pierce])

    plan: list[tuple[int, str, str]] = []
    if state == "behind":
        if hurts:
            plan.append((RULES["boots"]["defensive"][threat], "신발", "boots_defensive"))
            core1 = (RULES["core"]["behind"][threat][cls], "core_behind")
        else:
            plan.append((boots, "신발", boots_why))
            core1 = (cores[0], core_why)
        if counter:
            plan.append((counter[0], "하위템", counter[1]))
        else:
            plan.append((RULES["defensive_component"][threat], "하위템", "defensive_component"))
        plan.append((core1[0], "코어", core1[1]))
        rest = [c for c in cores if c != core1[0]]
        if counter and counter[2] not in rest and counter[2] != core1[0]:
            rest.insert(0, counter[2])
    else:
        plan.append((cores[0], "코어", core_why))
        plan.append((boots, "신발", boots_why if cb else "boots_after_core"))
        rest = cores[1:]
        if counter and (state == "even" or detected_by_item == "HEAL"):
            plan.append((counter[0], "대응", counter[1] if state == "even" else "counter_ahead"))
            if counter[2] not in rest and counter[2] != cores[0]:
                rest.insert(1 if rest else 0, counter[2])
    for c in rest:
        plan.append((c, "다음 코어", next_why))
    plan.append(((RULES.get("elixir") or {}).get(pierce, 2140), "영약", "elixir"))
    return plan


def _window(plan: list[tuple[int, str, str]], owned_items: list[int] | None) -> list[dict]:
    """계획에서 지금 보여 줄 3칸. 보유 코어가 있으면 [마지막 보유 코어, 다음 2개], 없으면 앞에서 3개(산 것 제외).

    코어를 하나라도 가졌으면 하위템 칸은 건너뛴다(하위템은 코어에 합쳐져 인벤토리에서 사라지기 때문).
    """
    owned = set(owned_items or ())
    has_boots = bool(owned & TIER2_BOOTS_IDS)
    core_slots = ("코어", "다음 코어")
    owned_cores = [p for p in plan if p[1] in core_slots and p[0] in owned]
    todo: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for p in plan:
        if owned_cores and p[1] in ("하위템", "대응"):
            continue
        done = has_boots if p[1] == "신발" else p[0] in owned
        if not done and p[0] not in seen:
            seen.add(p[0])
            todo.append(p)
    picks = ([owned_cores[-1]] + todo[:2]) if owned_cores else todo[:3]
    for p in reversed(owned_cores[:-1]):  # 풀빌드 끝: 앞의 보유 코어로 채운다
        if len(picks) >= 3:
            break
        picks.insert(0, p)
    while len(picks) < 3:
        picks.append(plan[-1])
    return [_core_item(step, slot, iid, why) for step, (iid, slot, why) in enumerate(picks[:3], 1)]


def advise(
    my_win_prob: float,
    my_champion: str | None,
    enemy_top_threat: str | None,
    enemy_items: list[int] | None = None,
    owned_items: list[int] | None = None,
    position: str | None = None,
) -> dict:
    """AGENTS.md "규칙표 적용 순서" 0~8 및 상대 공개 아이템·보유 아이템 기반 보정."""
    me, th = resolve_my(my_champion), resolve_threat(enemy_top_threat)
    state, cls, dmg, threat = state_of(my_win_prob), me["class"], me["damage"], th["threat"]
    item_set = set(enemy_items or ())
    detected_by_item = None
    if item_set & ENEMY_HEAL_ITEMS:
        threat, detected_by_item = "HEAL", "HEAL"
    elif item_set & ENEMY_TANK_ITEMS and threat in ("AD", "AP", "TANK"):
        threat, detected_by_item = "TANK", "TANK"
    pierce = "tank" if cls == "tank" else dmg  # 대응 하위템을 고르는 열: 탱커는 탱커용
    cb, build_pos = champion_build(me["champion_id"], position)
    picked = _window(_plan(state, cls, pierce, threat, detected_by_item, cb), owned_items)
    order = (cb or {}).get("skill_order") or RULES["skill_orders"].get(me["champion_id"] or "")
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
        "starter_items": [_extra(i, "champion_starter" if cb else "starter")
                          for i in _starter_ids(cls, pierce, position, cb)],
        "situational_items": _situational(pierce, {it["item_id"] for it in picked}),
        "skill_order": {"order": order, "focus": RULES["skill_focus"][state],
                        "source": ("챔피언 빌드표" if (cb or {}).get("skill_order") else "규칙표") if order else "원칙만"},
        "build_position": build_pos,
        "basis": f"사람이 정한 규칙표(build_rules.json){' + 챔피언 빌드표(공개 통계를 사람이 옮김)' if cb else ''}, "
                 f"아이템은 {GAME['source']} 대조. "
                 "모델은 상태(우세·접전·열세)만 정한다.",
    }

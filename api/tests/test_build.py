"""빌드 가이드 테스트 (AGENTS.md 4절 "빌드 가이드 정의", "규칙표 적용 순서").

기대 아이템 ID 는 build_rules.json 을 적용 순서 1~6 대로 사례마다 손으로 고른 값이다.
상태는 규칙 기준선(고정 자료)으로 만든다: 킬 +10 이면 골드 3,000 → 블루 약 77% (우세), 레드로 보면 약 23% (열세).
"""
import json
import os

import pytest

from api.tests.test_app import EVEN, state

HERE = os.path.dirname(os.path.dirname(__file__))
RULES = json.load(open(os.path.join(HERE, "build_rules.json"), encoding="utf-8"))
GAME = json.load(open(os.path.join(HERE, "game_data.json"), encoding="utf-8"))

AHEAD = {"blue": {**EVEN, "kills": 16}, "red": EVEN, "my_side": "blue"}   # 블루 킬 +10
BEHIND = {"blue": {**EVEN, "kills": 16}, "red": EVEN, "my_side": "red"}   # 같은 판을 레드로
EVEN_GAME = {"blue": EVEN, "red": EVEN, "my_side": "blue"}


def coach(client, game, **opt):
    body = state(blue=game["blue"], red=game["red"], my_side=game["my_side"], first_blood="blue")
    body.update(opt)
    res = client.post("/coach", json=body)
    assert res.status_code == 200, res.text
    return res.json()["build_advice"]


def ids(b):
    return [i["item_id"] for i in b["recommended_items"]]


@pytest.mark.parametrize("game,opt,want_state,want_ids,want_slots,want_threat", [
    # 우세 · 징크스(원딜, 빌드표 없음) · 상대 AP → 구매 계획(우세·접전): 1코어 core.ahead 3031, 계열 신발 3006,
    # followup_cores.marksman 에서 1코어를 뺀 첫 값 3094
    (AHEAD, {"my_champion": "Jinx", "enemy_top_threat": "AP"}, "ahead", [3031, 3006, 3094],
     ["코어", "신발", "다음 코어"], "AP"),
    # 열세 · 아리(마법사, AP) · 상대 제드(암살자, AD) → 구매 계획(열세): 방어 신발 3047, 천 갑옷 1029, 열세 AD 마법사 코어 3157
    (BEHIND, {"my_champion": "아리", "enemy_top_threat": "제드"}, "behind", [3047, 1029, 3157],
     ["신발", "하위템", "코어"], "AD"),
    # 열세 · AD 역할군 · 상대 소라카(치유) → 막을 피해 유형이 없어 계열 신발 3006, 치유 감소 3123, 1코어 3031
    (BEHIND, {"my_champion": "AD", "enemy_top_threat": "Soraka"}, "behind", [3006, 3123, 3031],
     ["신발", "하위템", "코어"], "HEAL"),
    # 접전 · 탱커 역할군 · 상대 말파이트(탱커) → 1코어 3068, 탱커 신발 3047, 대응 anti_tank.tank 1028
    (EVEN_GAME, {"my_champion": "TANK", "enemy_top_threat": "Malphite"}, "even", [3068, 3047, 1028],
     ["코어", "신발", "대응"], "TANK"),
    # 열세 · 가렌(전사, AD) · 상대 AP → 3111, 마법무효화의 망토 1033, 열세 AP 전사 코어 3156
    (BEHIND, {"my_champion": "Garen", "enemy_top_threat": "AP"}, "behind", [3111, 1033, 3156],
     ["신발", "하위템", "코어"], "AP"),
    # 생략 → 기본값 AD(원딜) · AP, 접전 → 첫 사례와 같은 계획
    (EVEN_GAME, {}, "even", [3031, 3006, 3094], ["코어", "신발", "다음 코어"], "AP"),
])
def test_rule_table_order(client, game, opt, want_state, want_ids, want_slots, want_threat):
    b = coach(client, game, **opt)
    assert b["state"] == want_state
    assert ids(b) == want_ids
    assert b["threat_type"] == want_threat
    assert [i["step"] for i in b["recommended_items"]] == [1, 2, 3]
    assert [i["slot"] for i in b["recommended_items"]] == want_slots
    assert b["build_position"] is None
    for i in b["recommended_items"]:  # 이름·가격은 Data Dragon 표에서
        assert (i["name"], i["gold"]) == (GAME["items"][str(i["item_id"])]["name"],
                                          GAME["items"][str(i["item_id"])]["gold"])
    assert b["posture"] == RULES["posture"][want_state]
    assert b["skill_order"]["focus"] == RULES["skill_focus"][want_state]
    assert "모델은 상태" in b["basis"] and GAME["version"] in b["basis"]


def test_champion_resolution(client):
    b = coach(client, BEHIND, my_champion="아리", enemy_top_threat="제드")
    assert (b["my_champion"], b["my_class"], b["my_damage"]) == ("아리", "mage", "AP")
    assert b["enemy_top_threat"] == "제드"
    assert b["skill_order"] == {"order": "Q > W > E", "focus": RULES["skill_focus"]["behind"], "source": "규칙표"}


@pytest.mark.parametrize("name", ["리 신", "leesin", "  LeeSin ", "리신"])
def test_name_variants(client, name):
    b = coach(client, EVEN_GAME, my_champion=name)
    assert b["my_champion"] == GAME["champions"]["LeeSin"]["name"]
    assert b["skill_order"]["order"] == RULES["skill_orders"]["LeeSin"]


def test_role_given_has_no_champion_and_no_skill_order(client):
    b = coach(client, EVEN_GAME, my_champion="ap", enemy_top_threat="heal")
    assert (b["my_champion"], b["my_class"], b["my_damage"]) == (None, "mage", "AP")
    assert (b["enemy_top_threat"], b["threat_type"]) == (None, "HEAL")
    # 접전 + HEAL: 1코어 → 신발 → 대응 치유 감소 (구매 계획 ③)
    assert ids(b)[2] == RULES["anti_heal"]["AP"] and b["recommended_items"][2]["slot"] == "대응"
    assert b["skill_order"]["order"] is None and b["skill_order"]["source"] == "원칙만"


def test_champion_without_skill_table(client):
    b = coach(client, EVEN_GAME, my_champion="Shaco")
    assert "Shaco" not in RULES["skill_orders"] and "Shaco" not in RULES["champion_builds"]
    assert b["skill_order"]["order"] is None and b["skill_order"]["source"] == "원칙만"


@pytest.mark.parametrize("field,value", [
    ("my_champion", "없는챔피언"), ("enemy_top_threat", "Nobody"), ("enemy_top_threat", "SUPPORT"),
    ("my_champion", "HEAL"), ("my_champion", "A" * 41), ("my_champion", 3), ("enemy_top_threat", ["AD"]),
])
def test_unknown_optional_values_422(client, field, value):
    body = state(**{field: value})
    for path in ("/predict", "/coach"):
        assert client.post(path, json=body).status_code == 422


def test_optional_fields_do_not_change_win_prob(client):
    a = client.post("/predict", json=state()).json()
    b = client.post("/predict", json=state(my_champion="Zed", enemy_top_threat="AD")).json()
    assert a["win_prob_blue"] == b["win_prob_blue"] and a["top_factors"] == b["top_factors"]


@pytest.mark.parametrize("p,want", [(0.4499, "behind"), (0.45, "even"), (0.55, "even"), (0.5501, "ahead")])
def test_state_boundaries(p, want):
    from api.build import state_of

    assert state_of(p) == want


def test_toast_build_line_with_skill_order(client):
    body = state(blue={**EVEN, "kills": 16}, red=EVEN, first_blood="blue", my_side="red",
                 my_champion="Ahri", enemy_top_threat="Zed")
    c = client.post("/coach", json=body).json()
    items = c["build_advice"]["recommended_items"]
    assert c["toast"][-1] == f"{items[0]['name']} → {items[1]['name']} · Q > W > E"
    assert len(c["toast"]) == 3


def test_rule_items_exist_in_game_data():
    """규칙표의 모든 아이템 ID 가 Data Dragon 표에 있다 (training/ddragon.py 가 만든 판)."""
    from training.ddragon import rule_item_ids

    assert rule_item_ids(RULES) <= {int(i) for i in GAME["items"]}
    assert set(RULES["skill_orders"]) <= set(GAME["champions"])
    assert set(RULES["heal_champions"]) <= set(GAME["champions"])


@pytest.mark.parametrize("game,opt,want_starter,want_situational", [
    # 아리(마법사, AP) 열세 · 상대 제드(AD): 2단계가 천 갑옷 1029 이라 물리 방어 후보는 빠진다
    # → 회복 대응 anti_heal.AP 3916, 관통 anti_tank.AP 4630, 마법 방어 1033
    (BEHIND, {"my_champion": "아리", "enemy_top_threat": "제드"}, [1056, 2003], [3916, 4630, 1033]),
    # TANK 역할군 · 상대 HEAL: 2단계가 anti_heal.tank 3076 이라 회복 대응 후보가 빠진다
    # → 관통 anti_tank.tank 1028, 물리 방어 1029, 마법 방어 1033
    (EVEN_GAME, {"my_champion": "TANK", "enemy_top_threat": "HEAL"}, [1054, 2003], [1028, 1029, 1033]),
    # 징크스(원딜, AD) 우세 · 상대 AP: 겹침 없음 → 앞에서 3개 3123, 3035, 1029
    (AHEAD, {"my_champion": "Jinx", "enemy_top_threat": "AP"}, [1055, 2003], [3123, 3035, 1029]),
    # SUPPORT: 세계 지도집으로 시작
    (EVEN_GAME, {"my_champion": "SUPPORT", "enemy_top_threat": "AD"}, [3865, 2003], None),
])
def test_starter_and_situational_items(client, game, opt, want_starter, want_situational):
    b = coach(client, game, **opt)
    assert [i["item_id"] for i in b["starter_items"]] == want_starter
    assert all(i["why"] == RULES["why"]["starter"] for i in b["starter_items"])
    if want_situational is not None:
        assert [i["item_id"] for i in b["situational_items"]] == want_situational
    sit = [i["item_id"] for i in b["situational_items"]]
    assert len(sit) <= 3 and not set(sit) & set(ids(b)) and len(set(sit)) == len(sit)
    for it in b["starter_items"] + b["situational_items"]:  # 이름·가격은 Data Dragon 표 그대로
        assert (it["name"], it["gold"]) == (GAME["items"][str(it["item_id"])]["name"],
                                            GAME["items"][str(it["item_id"])]["gold"])

"""챔피언×포지션 빌드표와 구매 계획 (AGENTS.md 4절 "규칙표 적용 순서" 0·3·5·6·7, /strategy 빌드 기준 상대).

기대 아이템 ID 는 build_rules.json 의 champion_builds 표를 계약 순서대로 사례마다 손으로 고른 값이다.
"""
import copy
import json
import os
import re

from api.build import advise
from api.tests.test_build import AHEAD, coach, ids
from api.tests.test_strategy import _base_live

HERE = os.path.dirname(os.path.dirname(__file__))
RULES = json.load(open(os.path.join(HERE, "build_rules.json"), encoding="utf-8"))
GAME = json.load(open(os.path.join(HERE, "game_data.json"), encoding="utf-8"))


def test_champion_build_default_position_on_coach(client):
    """/coach 는 포지션이 없어 판테온 기본 포지션 TOP 표: 월식 6692 → 판금 장화 3047 → 칠흑 3071."""
    b = coach(client, AHEAD, my_champion="판테온", enemy_top_threat="AP")
    assert ids(b) == [6692, 3047, 3071]
    assert [i["slot"] for i in b["recommended_items"]] == ["코어", "신발", "다음 코어"]
    assert b["build_position"] == "TOP"
    assert b["skill_order"]["order"] == "Q > E > W" and b["skill_order"]["source"] == "챔피언 빌드표"
    assert [i["item_id"] for i in b["starter_items"]] == [1055, 2003]
    assert all(i["why"] == RULES["why"]["champion_starter"] for i in b["starter_items"])
    assert "챔피언 빌드표" in b["basis"]


def test_support_position_and_owned_window():
    # 서폿 판테온: 세계 지도집 + 물약 시작, 아무것도 없으면 월식 → 판금 장화 → 칠흑
    b = advise(0.8, "Pantheon", "Xayah", position="UTILITY")
    assert [i["item_id"] for i in b["starter_items"]] == [3865, 2003]
    assert ids(b) == [6692, 3047, 3071] and b["build_position"] == "UTILITY"
    # 판금 장화 + 월식 보유 → [마지막 보유 코어 월식, 칠흑, 갈라진 하늘 6610]
    assert ids(advise(0.8, "Pantheon", "Xayah", owned_items=[3047, 6692], position="UTILITY")) == [6692, 3071, 6610]
    # 코어 전, 2단계 신발만 → 신발 칸은 산 것으로 보고 다음 3개
    assert ids(advise(0.8, "Pantheon", "Xayah", owned_items=[3047, 3865], position="UTILITY")) == [6692, 3071, 6610]
    # 풀빌드(코어 5개) → 앞의 보유 코어로 채운 뒤 영약 elixir.AD 2140
    full = [3047, 6692, 3071, 6610, 6333, 3026]
    assert ids(advise(0.8, "Pantheon", "Xayah", owned_items=full, position="UTILITY")) == [6333, 3026, 2140]
    # 표에 없는 포지션 → 기본 포지션(TOP)
    assert advise(0.8, "Pantheon", "AP", position="BOTTOM")["build_position"] == "TOP"


def test_behind_with_champion_table_keeps_survival_order():
    # 열세 · 판테온(전사) · 상대 AD → 방어 신발 3047, 천 갑옷 1029, 열세 AD 전사 코어 3053
    b = advise(0.3, "Pantheon", "AD", position="TOP")
    assert ids(b) == [3047, 1029, 3053]
    assert [i["slot"] for i in b["recommended_items"]] == ["신발", "하위템", "코어"]
    # 코어를 하나 가지면 하위템 칸은 건너뛴다: 보유 3053 → [3053, 표 1코어 6692, 칠흑 3071]
    assert ids(advise(0.3, "Pantheon", "AD", owned_items=[3047, 3053], position="TOP")) == [3053, 6692, 3071]


def test_position_starter_without_champion_table():
    assert "Leona" not in RULES["champion_builds"] and "LeeSin" not in RULES["champion_builds"]
    # 빌드표가 없는 서폿 → position_starter.UTILITY
    assert [i["item_id"] for i in advise(0.5, "Leona", "AD", position="UTILITY")["starter_items"]] == [3865, 2003]
    # 빌드표가 없는 정글 리 신(전사, AD) → position_starter.JUNGLE.AD
    assert [i["item_id"] for i in advise(0.5, "LeeSin", "AD", position="JUNGLE")["starter_items"]] == [1101, 2003]


def test_ahead_counter_only_when_heal_item_seen():
    # 우세 + 치유 챔피언(아이템 안 보임) → 구매 계획은 피해 코어, 대응은 상황별 1순위
    b = advise(0.8, "Jinx", "Soraka")
    assert ids(b) == [3031, 3006, 3094]
    assert b["situational_items"][0]["item_id"] == RULES["anti_heal"]["AD"]
    # 우세 + 상대 회복 아이템 3153 감지 → 대응 하위템을 3번째 칸에
    b = advise(0.8, "Jinx", "Caitlyn", enemy_items=[3153])
    assert ids(b) == [3031, 3006, RULES["anti_heal"]["AD"]]
    assert b["recommended_items"][2]["why"] == RULES["why"]["counter_ahead"]


def test_champion_build_table_is_valid():
    """빌드표 챔피언·아이템이 Data Dragon 표에 있고, 브리핑 검사용 item_stats 도 있으며, 스킬 순서 형식이 같다."""
    for cid, builds in RULES["champion_builds"].items():
        assert cid in GAME["champions"] and builds["default"] in builds
        for pos, cb in builds.items():
            if pos == "default":
                continue
            assert pos in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
            for iid in cb["starter"] + [cb["boots"]] + cb["cores"]:
                assert str(iid) in GAME["items"], (cid, pos, iid)
                assert str(iid) in RULES["item_stats"], (cid, pos, iid)
            assert len(cb["cores"]) == len(set(cb["cores"])) >= 3
            assert re.fullmatch(r"[QWE] > [QWE] > [QWE]", cb["skill_order"])


def test_build_threat_anchors_to_lane_opponent(client):
    """빌드 기준 상대는 라인 상대(아리 미드 → 제드). 다른 상대가 1.5배 + 1,000 넘게 크면 그 상대."""
    d = _base_live(900.0)
    # 치유 조합(HEAL)·물리 편중(AD 4명) 보정이 기준 상대 이름을 유형으로 바꾸지 않게 조합을 고른다
    d["enemies"][1]["champion"] = "Lissandra"
    d["enemies"][4]["champion"] = "Zyra"
    d["enemies"][0].update(kills=1, item_gold=3600)  # 다리우스가 제드보다 조금 큼
    out = client.post("/strategy", json=d).json()
    assert out["build_advice"]["enemy_top_threat"] == GAME["champions"]["Zed"]["name"]

    fed = copy.deepcopy(d)
    fed["enemies"][0].update(kills=15, item_gold=14000, level=16)
    out = client.post("/strategy", json=fed).json()
    assert out["build_advice"]["enemy_top_threat"] == GAME["champions"]["Darius"]["name"]

    # 서폿이면 상대 원딜이 기준, 빌드는 서폿 판테온 표
    sup = copy.deepcopy(d)
    sup["me"].update(champion="Pantheon", position="UTILITY", items=[3865])
    sup["allies"][4].update(champion="Pantheon", items=[3865])
    out = client.post("/strategy", json=sup).json()
    assert out["build_advice"]["enemy_top_threat"] == GAME["champions"]["Caitlyn"]["name"]
    assert out["build_advice"]["build_position"] == "UTILITY"
    assert [i["item_id"] for i in out["build_advice"]["starter_items"]] == [3865, 2003]


def test_recall_suggests_affordable_component_of_next_core(client):
    """다음 코어(월식 2,900G)를 못 사면 그 재료 가운데 지금 살 수 있고 실제로 내는 값이 가장 큰 것.

    월식 재료: 콜필드의 전투 망치 3133(1,050G = 장검 1036 ×2 + 2022), 곡괭이 1037(875G), 장검 1036(350G).
    """
    d = _base_live(400.0)
    d["me"].update(champion="Pantheon", position="TOP", items=[1055], current_gold=1300)
    d["allies"][0].update(champion="Pantheon", items=[1055])
    out = client.post("/strategy", json=d).json()
    rec = out["recall_advice"]
    assert rec["next_item"] == GAME["items"]["6692"]["name"] and rec["next_item_id"] == 6692
    assert rec["buy_now"] == {"item_id": 3133, "name": GAME["items"]["3133"]["name"], "gold": 1050}
    assert rec["can_buy_now"] is True and rec["gold_needed"] == 2900 - 1300
    # 장검 1개 보유: 망치 남은 값 1,050 − 350 = 700G < 곡괭이 875G → 곡괭이. 월식 남은 비용도 350G 줄어든다
    d["me"]["items"] = [1055, 1036]
    rec = client.post("/strategy", json=d).json()["recall_advice"]
    assert rec["buy_now"]["item_id"] == 1037 and rec["buy_now"]["gold"] == 875
    assert rec["gold_needed"] == 2900 - 350 - 1300
    # 재료 하나도 못 살 만큼 골드가 적으면(700G 미만 재료만 가능) buy_now 는 null
    d["me"].update(items=[1055], current_gold=500)
    rec = client.post("/strategy", json=d).json()["recall_advice"]
    assert rec["buy_now"] is None and rec["can_buy_now"] is False

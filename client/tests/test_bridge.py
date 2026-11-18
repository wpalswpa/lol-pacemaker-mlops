"""브리지 변환과 10분 한 번 호출 (AGENTS.md 3-1·3-2절, client/bridge.py 머리 주석의 변환 규칙).

기대값은 고정 자료의 선수 점수와 이벤트를 변환 규칙대로 손으로 센 값이다.
"""
import copy
import json
import os

import pytest

from api.contract import GameState
from client.bridge import TEN_MINUTES, fetch_live, to_game_state, wait_and_coach

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "allgamedata_10min.json")
DATA = json.load(open(FIX, encoding="utf-8"))

# 고정 자료 손 계산: 블루 킬 2+1+1+1+0, 어시 1+2+1+1+1, CS 70+50+60+10+60, 레벨 (7+7+7+6+7)/5, 전령 A3 1회
# 레드 킬 3+2+1+1+0, 어시 2+2+2+2+1, CS 75+55+65+13+60, 레벨 7, 드래곤 B1 1회, T1(블루) 포탑 파괴 1회, 첫 킬 B2
WANT = {
    "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 1, "towers": 0},
    "red": {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 1},
    "first_blood": "red",
    "my_side": "blue",
}


def test_convert_scoreboard_and_events():
    state, info = to_game_state(DATA)
    assert state["blue"] == pytest.approx(WANT["blue"])
    assert state["red"] == pytest.approx(WANT["red"])
    assert (state["first_blood"], state["my_side"]) == ("red", "blue")
    assert info["game_time"] == 600.4 and info["first_blood_seen"]
    GameState(**state)  # API 입력 계약을 그대로 통과해야 한다


def test_item_gold_is_reference_only():
    state, info = to_game_state(DATA)
    assert info["item_gold"] == {"blue": 450.0, "red": 700.0}  # 응답의 price × count
    _, info2 = to_game_state(DATA, prices={"1055": 1000, "1036": 400})  # Data Dragon 값이 있으면 그것
    assert info2["item_gold"] == {"blue": 1000.0, "red": 800.0}
    assert "item_gold" not in json.dumps(state)  # 모델 입력에는 넣지 않는다


def test_side_from_argument_when_spectating():
    d = copy.deepcopy(DATA)
    del d["activePlayer"]
    with pytest.raises(ValueError):
        to_game_state(d)
    state, _ = to_game_state(d, side="red")
    assert state["my_side"] == "red"


def test_red_turret_destroyed_by_blue_counts_for_blue():
    d = copy.deepcopy(DATA)
    d["events"]["Events"].append({"EventName": "TurretKilled", "TurretKilled": "Turret_T2_C_05_A", "KillerName": "A1"})
    state, _ = to_game_state(d)
    assert (state["blue"]["towers"], state["red"]["towers"]) == (1, 1)


def test_no_first_blood_yet_defaults_to_enemy():
    d = copy.deepcopy(DATA)
    d["events"]["Events"] = [e for e in d["events"]["Events"] if e["EventName"] != "FirstBlood"]
    state, info = to_game_state(d)
    assert state["first_blood"] == "red" and not info["first_blood_seen"]


def test_name_variants_match_riot_id_with_tag():
    d = copy.deepcopy(DATA)
    for e in d["events"]["Events"]:
        if e["EventName"] == "DragonKill":
            e["KillerName"] = "B1#KR1"
    assert to_game_state(d)[0]["red"]["dragons"] == 1


def test_calls_coach_once_after_ten_minutes():
    times = iter([540.0, 590.0, 599.9, TEN_MINUTES, 700.0])
    calls, shown, slept = [], [], []

    def fetch():
        d = copy.deepcopy(DATA)
        d["gameData"]["gameTime"] = next(times)
        return d

    def coach(state):
        calls.append(state)
        return {"toast": ["승률 30% · 상위 80%", "드래곤 +1 → 40% (상위 60%)"], "message": "m", "model_version": "m1"}

    res = wait_and_coach(fetch, coach, shown.append, sleep=slept.append)
    assert len(calls) == 1  # 10분이 된 첫 확인에서 한 번만
    assert len(slept) == 3  # 9:00, 9:50, 9:59.9 에서 기다림
    assert shown == [res["toast"]]  # 알림 문구는 응답의 toast 그대로
    assert 2 <= len(shown[0]) <= 3


def test_before_ten_minutes_never_calls():
    d = copy.deepcopy(DATA)
    d["gameData"]["gameTime"] = 420.0
    calls = []
    res = wait_and_coach(lambda: d, calls.append, lambda _: None, sleep=lambda _: None, max_polls=5)
    assert res is None and calls == []


def test_live_fetch_refuses_remote_hosts_and_silent_insecure():
    with pytest.raises(ValueError):
        fetch_live("https://example.com:2999", None, insecure_local=True)
    with pytest.raises(ValueError):
        fetch_live("https://127.0.0.1:2999", None)  # 인증서도, 명시적 허용도 없으면 거절

"""브리지 변환과 10분 한 번 호출 (AGENTS.md 3-1·3-2절, client/bridge.py 머리 주석의 변환 규칙).

기대값은 고정 자료의 선수 점수와 이벤트를 변환 규칙대로 손으로 센 값이다.
"""
import copy
import io
import json
import os
import urllib.error

import pytest

from api.contract import GameState
from client.bridge import (TEN_MINUTES, alert_switches, champion_of, coach_with_fallback, fetch_live, main,
                           to_game_state, wait_and_coach)

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "allgamedata_10min.json")
DATA = json.load(open(FIX, encoding="utf-8"))

# 고정 자료 손 계산: 블루 킬 2+1+1+1+0, 어시 1+2+1+1+1, CS 70+50+60+10+60, 레벨 (7+7+7+6+7)/5, 전령 A3 1회
# 레드 킬 3+2+1+1+0, 어시 2+2+2+2+1, CS 75+55+65+13+60, 레벨 7, 드래곤 B1 1회, T1(블루) 포탑 파괴 1회, 첫 킬 B2
WANT = {
    "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 1, "towers": 0},
    "red": {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 1},
    "first_blood": "red",
    "my_side": "blue",
    # 내 챔피언: activePlayer A1 의 rawChampionName. 상대 위협: 레드 최다 킬 B1(3킬)
    "my_champion": "Ahri",
    "enemy_top_threat": "Zed",
}


def _player(d: dict, name: str) -> dict:
    return next(p for p in d["allPlayers"] if p["riotIdGameName"] == name)


def test_convert_scoreboard_and_events():
    state, info = to_game_state(DATA)
    assert state["blue"] == pytest.approx(WANT["blue"])
    assert state["red"] == pytest.approx(WANT["red"])
    assert (state["first_blood"], state["my_side"]) == ("red", "blue")
    assert (state["my_champion"], state["enemy_top_threat"]) == ("Ahri", "Zed")
    assert info["game_time"] == 600.4 and info["first_blood_seen"]
    GameState(**state)  # API 입력 계약을 그대로 통과해야 한다


def test_champion_name_prefers_raw_id_then_display_name():
    assert champion_of({"rawChampionName": "game_character_displayname_MonkeyKing", "championName": "오공"}) == "MonkeyKing"
    assert champion_of({"championName": "오공"}) == "오공"  # 영문 ID 가 없으면 화면 이름
    assert champion_of({"rawChampionName": "game_character_displayname_", "championName": "Wukong"}) == "Wukong"
    assert champion_of({"rawChampionName": "", "championName": ""}) is None


def test_display_name_fallback_still_passes_contract():
    d = copy.deepcopy(DATA)
    for p in d["allPlayers"]:
        del p["rawChampionName"]  # 한국어 클라이언트의 championName("아리")만 남는다
    state, _ = to_game_state(d)
    assert (state["my_champion"], state["enemy_top_threat"]) == ("아리", "제드")
    GameState(**state)


def test_threat_tie_on_kills_goes_to_more_cs():
    d = copy.deepcopy(DATA)
    _player(d, "B2")["scores"]["kills"] = 3  # B1 3킬 CS 75, B2 3킬 CS 55
    assert to_game_state(d)[0]["enemy_top_threat"] == "Zed"
    _player(d, "B2")["scores"]["creepScore"] = 80
    assert to_game_state(d)[0]["enemy_top_threat"] == "Darius"


def test_threat_full_tie_goes_to_list_order():
    d = copy.deepcopy(DATA)
    b2 = _player(d, "B2")
    b2["scores"]["kills"], b2["scores"]["creepScore"] = 3, 75  # B1 과 킬·CS 모두 같다
    assert to_game_state(d)[0]["enemy_top_threat"] == "Zed"  # 목록에서 먼저 나온 B1


def test_threat_is_on_enemy_team_of_my_side():
    d = copy.deepcopy(DATA)
    d["activePlayer"] = {"riotId": "B3#KR1", "riotIdGameName": "B3", "summonerName": "B3"}  # 내가 레드 B3
    state, _ = to_game_state(d)
    assert state["my_side"] == "red" and state["my_champion"] == "Vladimir"
    assert state["enemy_top_threat"] == "Ahri"  # 블루 최다 킬 A1(2킬)
    GameState(**state)


def test_missing_champion_names_leave_fields_out():
    d = copy.deepcopy(DATA)
    for p in d["allPlayers"]:
        p.pop("rawChampionName"), p.pop("championName")
    state, _ = to_game_state(d)
    assert "my_champion" not in state and "enemy_top_threat" not in state  # 서버 기본값(AD·AP)
    GameState(**state)


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
    assert "my_champion" not in state  # 관전: 내가 누군지 모른다
    assert state["enemy_top_threat"] == "Ahri"  # 블루 최다 킬


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

    res = wait_and_coach(fetch, coach, lambda lines, state: shown.append((lines, state)), sleep=slept.append)
    assert len(calls) == 1  # 10분이 된 첫 확인에서 한 번만
    assert len(slept) == 3  # 9:00, 9:50, 9:59.9 에서 기다림
    assert shown == [(res["toast"], None)]  # 알림 문구는 응답의 toast 그대로, 빌드가 없으면 색 없음
    assert 2 <= len(shown[0][0]) <= 3


def test_notify_gets_build_state_for_border_color():
    shown = []
    res = {"toast": ["a", "b"], "message": "m", "model_version": "m1", "build_advice": {"state": "behind"}}
    wait_and_coach(lambda: DATA, lambda s: res, lambda lines, state: shown.append(state), sleep=lambda _: None)
    assert shown == ["behind"]


def _http_422():
    return urllib.error.HTTPError("http://x/coach", 422, "Unprocessable", {}, io.BytesIO(b"{}"))


def test_unknown_champion_422_retries_once_without_champion_fields():
    sent = []

    def coach(state):
        sent.append(state)
        if "my_champion" in state:
            raise _http_422()
        return {"ok": True}

    state, _ = to_game_state(DATA)
    assert coach_with_fallback(coach, state) == {"ok": True}
    assert len(sent) == 2 and "enemy_top_threat" not in sent[1]
    assert {k: sent[1][k] for k in ("blue", "red", "first_blood", "my_side")} == \
        {k: state[k] for k in ("blue", "red", "first_blood", "my_side")}  # 점수판 값은 그대로


def test_other_errors_are_not_retried():
    calls = []

    def coach(state):
        calls.append(state)
        raise _http_422()

    with pytest.raises(urllib.error.HTTPError):  # 챔피언 칸이 없는데 422 면 점수판 문제: 다시 부르지 않는다
        coach_with_fallback(coach, {"blue": {}, "red": {}})
    assert len(calls) == 1


@pytest.mark.parametrize("effect, tts, env, want", [
    (None, None, {}, (True, False)),                                   # 기본값: 이펙트 on, 음성 off
    (None, None, {"LOL_EFFECT": "off", "LOL_TTS": "ON"}, (False, True)),  # 환경 변수, 대소문자 무시
    ("on", "off", {"LOL_EFFECT": "off", "LOL_TTS": "on"}, (True, False)),  # 실행 옵션이 앞선다
    (None, None, {"LOL_EFFECT": "0", "LOL_TTS": "true"}, (False, True)),
])
def test_alert_switches_priority(effect, tts, env, want):
    assert alert_switches(effect, tts, env) == want


def test_alert_switches_reject_bad_env():
    with pytest.raises(ValueError):
        alert_switches(None, None, {"LOL_TTS": "maybe"})


def test_main_effect_off_tts_off_prints_toast(monkeypatch, capsys):
    import client.bridge as b

    toast = ["승률 30% · 상위 80%", "킬 +2 → 40% (상위 60%)"]
    monkeypatch.setattr(b, "post_coach", lambda api, s: {"toast": toast, "message": "m", "model_version": "m1",
                                                         "build_advice": {"state": "behind"}})
    monkeypatch.setattr(b, "show_card", lambda *a, **k: pytest.fail("이펙트 off 인데 카드를 띄움"))
    monkeypatch.setattr(b, "speak", lambda *a, **k: pytest.fail("음성 off 인데 읽음"))
    assert main(["--file", FIX, "--effect", "off", "--tts", "off"]) == 0
    assert "\n".join(toast) in capsys.readouterr().out


def test_main_effect_on_passes_state_color_and_tts_from_env(monkeypatch):
    import client.bridge as b

    cards, voices = [], []
    monkeypatch.setenv("LOL_TTS", "on")
    monkeypatch.setattr(b, "post_coach", lambda api, s: {"toast": ["a", "b"], "message": "m", "model_version": "m1",
                                                         "build_advice": {"state": "ahead"}})
    monkeypatch.setattr(b, "show_card", lambda lines, sec, state: cards.append(state))
    monkeypatch.setattr(b, "speak", voices.append)
    assert main(["--file", FIX]) == 0  # 이펙트는 기본 on
    assert cards == ["ahead"] and voices == [["a", "b"]]


def test_main_legacy_flags_and_bad_env(monkeypatch):
    import client.bridge as b

    voices = []
    monkeypatch.setenv("LOL_EFFECT", "off")
    monkeypatch.setattr(b, "post_coach", lambda api, s: {"toast": ["a"], "message": "m", "model_version": "m1"})
    monkeypatch.setattr(b, "speak", voices.append)
    assert main(["--file", FIX, "--voice"]) == 0  # --voice = --tts on
    assert voices == [["a"]]
    monkeypatch.setenv("LOL_EFFECT", "sometimes")
    with pytest.raises(SystemExit):
        main(["--file", FIX])


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

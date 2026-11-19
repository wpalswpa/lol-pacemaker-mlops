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


# ── 게임 중 오버레이 (AGENTS.md 3-4절) ──
import http.client  # noqa: E402

from api.tests.conftest import app_module as app_module  # noqa: E402,F401  (/coach 로 환산 입력 확인)
from api.tests.conftest import client as client  # noqa: E402,F401

from client.bridge import (OverlayBridge, extract_features_from_liveclient, extract_owned_items,  # noqa: E402
                           next_skill, overlay_url, popup_command, run_overlay, serve_state, skill_plan)


def at(seconds: float) -> dict:
    d = copy.deepcopy(DATA)
    d["gameData"]["gameTime"] = seconds
    return d


def teams(state):
    return {t: {k: v for k, v in state[t].items()} for t in ("blue", "red")}


@pytest.mark.parametrize("seconds,blue,red", [
    # 3분: 배율 10/3. 블루 킬 5→16.67→17, 어시 6→20, CS 250→833.3→833, 전령 1→3.33→3 이지만 허용 범위 2로 자름
    #      레드 킬 7→23.3→23, 어시 9→30, CS 268→893.3→893, 드래곤 1→3, 포탑 1→3
    (180.0, {"kills": 17, "assists": 20, "cs": 833, "dragons": 0, "heralds": 2, "towers": 0},
            {"kills": 23, "assists": 30, "cs": 893, "dragons": 3, "heralds": 0, "towers": 3}),
    # 15분: 배율 2/3. 블루 5→3.33→3, 6→4, 250→166.7→167, 전령 0.67→1 / 레드 7→4.67→5, 9→6, 268→178.7→179, 드래곤·포탑 0.67→1
    (900.0, {"kills": 3, "assists": 4, "cs": 167, "dragons": 0, "heralds": 1, "towers": 0},
            {"kills": 5, "assists": 6, "cs": 179, "dragons": 1, "heralds": 0, "towers": 1}),
    # 20분: 배율 0.5. 킬 5→2.5 는 0.5 올림으로 3 (은행가 반올림이면 2), 어시 6→3, CS 125, 전령 0.5→1
    #       레드 7→3.5→4, 9→4.5→5, 134, 드래곤·포탑 0.5→1
    (1200.0, {"kills": 3, "assists": 3, "cs": 125, "dragons": 0, "heralds": 1, "towers": 0},
             {"kills": 4, "assists": 5, "cs": 134, "dragons": 1, "heralds": 0, "towers": 1}),
    # 30초: 1분으로 보고 배율 10. CS 2500·2680 은 2000, 전령 10→2, 드래곤 10→7, 포탑 10
    (30.0, {"kills": 50, "assists": 60, "cs": 2000, "dragons": 0, "heralds": 2, "towers": 0},
           {"kills": 70, "assists": 90, "cs": 2000, "dragons": 7, "heralds": 0, "towers": 10}),
])
def test_pace_scaling_to_ten_minutes(seconds, blue, red):
    f = extract_features_from_liveclient(at(seconds))
    assert teams(f) == {"blue": {**blue, "avg_level": 6.8}, "red": {**red, "avg_level": 7.0}}  # 레벨은 그대로
    assert {k: f[k] for k in ("first_blood", "my_side", "my_champion", "enemy_top_threat")} == {
        "first_blood": "red", "my_side": "blue", "my_champion": "Ahri", "enemy_top_threat": "Zed"}
    GameState(**f)  # 정수 칸·허용 범위 그대로 /coach 입력 계약을 지킨다


def test_pace_scaling_is_identity_at_ten_minutes():
    assert extract_features_from_liveclient(at(600.0)) == WANT


def test_scaled_input_is_accepted_by_coach(client):  # noqa: F811
    for s in (30.0, 180.0, 900.0, 1500.0):
        res = client.post("/coach", json=extract_features_from_liveclient(at(s)))
        assert res.status_code == 200, (s, res.text)


def test_extract_owned_items():
    # activePlayer A1 의 아이템 (도란의 검 1개)
    assert extract_owned_items(DATA) == [{"itemID": 1055, "displayName": "item1055", "count": 1, "slot": None}]
    # activePlayer 가 없으면(관전) 첫 선수 A1, 이름이 riotId 로만 맞아도 찾는다
    d = copy.deepcopy(DATA)
    d.pop("activePlayer")
    assert [i["itemID"] for i in extract_owned_items(d)] == [1055]
    d = copy.deepcopy(DATA)
    d["activePlayer"] = {"riotId": "B1#KR1"}
    d["allPlayers"][5]["items"] = [{"itemID": 1036, "displayName": "롱소드", "count": 2, "slot": 3, "price": 350}]
    assert extract_owned_items(d) == [{"itemID": 1036, "displayName": "롱소드", "count": 2, "slot": 3}]


def test_skill_plan():
    # AGENTS.md 3-4절의 예: 1~3레벨 Q W E, 6·11·16 R, 기본 스킬 r랭크는 2r-1 레벨부터
    assert " ".join(skill_plan("Q > W > E")) == "Q W E Q Q R Q W Q W R W W E E R E E"
    assert " ".join(skill_plan("E > Q > W")) == "E Q W E E R E Q E Q R Q Q W W R W W"
    assert skill_plan(None) == [None] * 5 + ["R"] + [None] * 4 + ["R"] + [None] * 4 + ["R", None, None]


@pytest.mark.parametrize("level,abilities,want", [
    (7, None, ("Q", "now")),                                   # 계획의 7레벨 = Q
    (6, None, ("R", "now")),
    (4, {"Q": 1, "W": 1, "E": 1, "R": 0}, ("Q", "now")),       # 4레벨에 Q 두 번째를 아직 안 찍음
    (3, {"Q": 1, "W": 1, "E": 1, "R": 0}, ("Q", "next_level")),  # 다 찍었으니 4레벨 계획 Q
    (6, {"Q": 3, "W": 1, "E": 1, "R": 0}, ("R", "now")),       # 궁극기를 아직 안 찍음
    (2, {"Q": 0, "W": 1, "E": 0, "R": 0}, ("Q", "now")),       # 계획과 다르게 W 를 먼저 찍었으면 빠진 Q
    (18, {"Q": 5, "W": 5, "E": 5, "R": 3}, (None, "next_level")),
])
def test_next_skill(level, abilities, want):
    assert next_skill(level, "Q > W > E", abilities) == want


def test_next_skill_without_order():
    assert next_skill(7, None) == (None, "now")
    assert next_skill(11, None) == ("R", "now")


COACH = {"my_win_prob": 0.4, "build_advice": {"skill_order": {"order": "Q > W > E"}}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_bridge(frames, clock, calls, fail=False):
    def fetch():
        f = frames[0]
        if isinstance(f, Exception):
            raise f
        return f

    def coach(state):
        calls.append(state)
        if fail:
            raise urllib.error.URLError("서버 꺼짐")
        return {**COACH, "n": len(calls)}

    return OverlayBridge(fetch, coach, clock=clock)


def test_overlay_bridge_state_and_coach_interval():
    clock, calls, frames = Clock(), [], [at(900.0)]
    b = make_bridge(frames, clock, calls)
    st = b.step()
    feats = extract_features_from_liveclient(at(900.0))
    assert len(calls) == 1 and calls[0] == {**feats, "enemy_items": [1036]}  # 첫 호출은 바로, 상대 위협 B1 의 공개 아이템
    assert st["connected"] and st["in_game"] and st["source"] == "live"
    assert (st["game_time_sec"], st["minutes"], st["pace_scale"]) == (900.0, 15.0, pytest.approx(2 / 3))
    assert (st["champion"], st["level"]) == ("Ahri", 7)
    assert st["owned_items"] == [{"itemID": 1055, "displayName": "item1055", "count": 1, "slot": None}]
    assert (st["next_skill"], st["next_skill_when"]) == ("Q", "now")  # 응답의 순서 Q > W > E, 7레벨
    assert st["features"] == feats and st["raw_features"] == WANT and st["coach"]["n"] == 1 and st["enemy_items"] == [1036]

    clock.t += 3
    b.step()                       # 입력 같음 → 부르지 않음
    frames[0] = at(1200.0)
    b.step()                       # 입력이 바뀌었어도 3초 → 아직
    assert len(calls) == 1
    clock.t += 3                   # 직전 호출에서 6초
    st = b.step()
    assert len(calls) == 2 and st["coach"]["n"] == 2 and st["game_time_sec"] == 1200.0


def test_overlay_bridge_survives_failures():
    clock, calls, frames = Clock(), [], [at(900.0)]
    b = make_bridge(frames, clock, calls)
    b.step()
    frames[0] = urllib.error.URLError("2999 연결 거부")
    st = b.step()
    assert st["connected"] is False and "2999" in st["error"]
    assert st["coach"]["n"] == 1 and st["game_time_sec"] == 900.0  # 마지막 상태를 그대로 둔다

    clock2, calls2 = Clock(), []
    st = make_bridge([at(900.0)], clock2, calls2, fail=True).step()
    assert st["connected"] and st["coach"] is None and "서버 꺼짐" in st["coach_error"]
    assert st["next_skill"] is None  # 스킬 순서를 모르면 7레벨은 없음


def test_state_server_json_and_cors():
    clock, calls = Clock(), []
    b = make_bridge([at(900.0)], clock, calls)
    httpd = serve_state(b, 0)
    try:
        port = httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/bridge/state")
        r = conn.getresponse()
        body = json.loads(r.read())
        assert r.status == 200 and r.getheader("Access-Control-Allow-Origin") == "*"
        assert r.getheader("Access-Control-Allow-Private-Network") == "true"
        assert body["connected"] is False and body["coach"] is None  # 아직 한 번도 읽지 않음
        b.step()
        conn.request("GET", "/bridge/state?t=1")
        body = json.loads(conn.getresponse().read())
        assert set(body) >= {"connected", "in_game", "game_time_sec", "updated_at", "champion", "level",
                             "owned_items", "features", "coach"}
        assert body["connected"] and body["game_time_sec"] == 900.0
        conn.request("OPTIONS", "/bridge/state")
        r = conn.getresponse(); r.read()
        assert r.status == 204 and r.getheader("Access-Control-Allow-Origin") == "*"
        conn.request("GET", "/other")
        r = conn.getresponse(); r.read()
        assert r.status == 404
    finally:
        httpd.shutdown()


def test_run_overlay_one_step_then_stops():
    clock, calls = Clock(), []
    b = make_bridge([at(900.0)], clock, calls)
    run_overlay(b, 0, sleep=lambda s: None, max_steps=2)
    assert len(calls) == 1


def test_popup_command_choice():
    url = overlay_url(8765)   # HUD 는 브리지가 같은 출처에서 내준다 (3-4절 "팝업")
    assert url == "http://127.0.0.1:8765/overlay?bridge_url=http://127.0.0.1:8765/bridge/state"
    how, cmd = popup_command(url, "Darwin", which=lambda n: "/usr/bin/swiftc" if n == "swiftc" else None,
                             build=lambda: "/cache/overlay-popup")
    assert how == "swift" and cmd == ["/cache/overlay-popup", url, "380", "560"]
    # swiftc 가 없거나 컴파일이 실패하면 Chrome --app
    for which, build in ((lambda n: None, None), (lambda n: "/usr/bin/swiftc" if n == "swiftc" else None, lambda: None)):
        how, cmd = popup_command(url, "Darwin", which=which, exists=lambda p: True, build=build)
        assert how == "chrome" and f"--app={url}" in cmd and "--window-size=380,560" in cmd
    how, cmd = popup_command(url, "Linux", which=lambda n: None, exists=lambda p: False)
    assert (how, cmd) == ("browser", None)


# ── 전적 저장 --save-match (AGENTS.md 3-5절) ──
from client.bridge import game_result, match_payload  # noqa: E402


def ended(seconds: float, result: str) -> dict:
    d = at(seconds)
    d.setdefault("events", {}).setdefault("Events", []).append(
        {"EventID": 99, "EventName": "GameEnd", "EventTime": seconds, "Result": result})
    return d


@pytest.mark.parametrize("data, want", [
    (at(900.0), None), (ended(1500.0, "Win"), True), (ended(1500.0, "Lose"), False), ({}, None),
])
def test_game_result(data, want):
    assert game_result(data) is want


def test_match_payload_has_only_contract_fields():
    feats = extract_features_from_liveclient(at(900.0))
    p = match_payload(feats, 900.4, True)
    assert set(p) == {"blue", "red", "first_blood", "my_side", "my_champion", "enemy_top_threat",
                      "game_time_sec", "win"}                             # 소환사명·Riot ID 없음
    assert (p["game_time_sec"], p["win"]) == (900, True)
    assert match_payload(feats, 9000.0, None)["game_time_sec"] == 7200   # 계약 상한
    assert match_payload(feats, -1.0, None)["game_time_sec"] == 0


def test_payload_is_saved_by_api(client):
    from api import db
    db.init_db("sqlite://")
    res = client.post("/matches", json=match_payload(extract_features_from_liveclient(ended(1500.0, "Win")), 1500.0, True))
    assert res.status_code == 201
    rec = client.get("/matches").json()["matches"][0]
    assert (rec["my_champion"], rec["enemy_top_threat"], rec["win"], rec["game_time_sec"]) == ("Ahri", "Zed", True, 1500)


def save_bridge(frames, clock, saves, source="live", fail_times=0):
    calls = []
    b = make_bridge(frames, clock, calls)
    state = {"fails": fail_times}

    def save(payload):
        if state["fails"]:
            state["fails"] -= 1
            raise urllib.error.URLError("서버 꺼짐")
        saves.append(payload)
        return {"id": len(saves), "created_at": "t", "my_win_prob": 0.5, "model_version": "x"}
    b.save, b.source = save, source
    return b


def test_overlay_saves_once_at_game_end():
    clock, saves, frames = Clock(), [], [at(900.0)]
    b = save_bridge(frames, clock, saves)
    st = b.step()
    assert saves == [] and st["saved_match"] is None            # 아직 안 끝남
    frames[0] = ended(1500.0, "Lose")
    clock.t += 6
    st = b.step()
    assert len(saves) == 1
    assert saves[0] == extract_features_from_liveclient(ended(1500.0, "Lose")) | {"game_time_sec": 1500, "win": False}
    assert st["saved_match"] == {"id": 1, "created_at": "t", "win": False} and st["save_error"] is None
    clock.t += 60
    b.step()
    assert len(saves) == 1                                      # 한 경기에 한 번만


def test_overlay_file_source_saves_first_read():
    clock, saves = Clock(), []
    st = save_bridge([at(600.0)], clock, saves, source="file").step()
    assert len(saves) == 1 and saves[0]["win"] is None and saves[0]["game_time_sec"] == 600
    assert st["saved_match"]["id"] == 1


def test_overlay_save_failure_retries_after_interval():
    clock, saves, frames = Clock(), [], [ended(1500.0, "Win")]
    b = save_bridge(frames, clock, saves, fail_times=1)
    st = b.step()
    assert saves == [] and "서버 꺼짐" in st["save_error"] and st["coach"] is not None   # 코칭은 계속
    clock.t += 3
    b.step()
    assert saves == []                                          # 5초 안에는 다시 안 보낸다
    clock.t += 3
    st = b.step()
    assert len(saves) == 1 and st["save_error"] is None and st["saved_match"]["win"] is True


def test_overlay_without_save_never_posts():
    clock, calls = Clock(), []
    st = make_bridge([ended(1500.0, "Win")], clock, calls).step()
    assert st["saved_match"] is None and st["save_error"] is None


def test_save_match_requires_overlay(capsys):
    with pytest.raises(SystemExit):
        main(["--file", FIX, "--save-match"])
    assert "--overlay" in capsys.readouterr().err


def test_bridge_runs_on_python39():
    """macOS 기본 python3(3.9)·오래된 Windows 파이썬에서도 `python -m client.bridge` 가 떠야 한다 (README 바로 쓰기).
    X | None 표기는 3.9 에서 정의할 때 계산되면 TypeError 라 지연 평가를 켜 두고, 문법도 3.9 기준으로 확인한다."""
    import ast

    src = open(os.path.join(os.path.dirname(__file__), "..", "bridge.py"), encoding="utf-8").read()
    tree = ast.parse(src, feature_version=(3, 9))
    assert any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names)
               for n in tree.body)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"__future__", "argparse", "hashlib", "json", "math", "os", "platform", "shutil", "ssl",
                        "subprocess", "sys", "threading", "time", "webbrowser", "urllib", "collections", "http",
                        "ctypes", "tkinter"}, imported      # 표준 라이브러리만 (설치 없이)


# ── 브리지가 HUD 를 같은 출처에서 내주는 중계 (AGENTS.md 3-4절 "팝업") ──
from client.bridge import RELAY_MAX_BODY  # noqa: E402


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    conn.close()
    return out


def test_relay_serves_hud_same_origin():
    seen = []

    def upstream(method, path, body):
        seen.append((method, path, body))
        if path == "/overlay":
            return 200, "text/html; charset=utf-8", "<title>HUD</title>".encode()
        if path == "/coach" and b"bad" in body:
            return 422, "application/json", b'{"detail": []}'
        return 200, "application/json", b'{"ok": true}'

    clock, calls = Clock(), []
    httpd = serve_state(make_bridge([at(900.0)], clock, calls), 0, upstream)
    port = httpd.server_address[1]
    try:
        code, h, body = _req(port, "GET", "/overlay?bridge_url=x")
        assert (code, h["Content-Type"], body) == (200, "text/html; charset=utf-8", "<title>HUD</title>".encode())
        assert "Access-Control-Allow-Origin" not in h          # 중계 경로는 같은 출처 전용 (CORS 안 염)
        assert _req(port, "GET", "/meta/champions")[0] == 200
        code, _, body = _req(port, "POST", "/coach", b'{"a": 1}', {"Content-Type": "application/json"})
        assert (code, body) == (200, b'{"ok": true}')
        assert _req(port, "POST", "/coach", b'{"bad": 1}')[0] == 422   # 서버의 422 를 그대로
        assert _req(port, "POST", "/coach/briefing", b'{"a": 1}')[0] == 200  # HUD 의 AI 감독 브리핑 (AGENTS.md 4절)
        assert seen == [("GET", "/overlay", None), ("GET", "/meta/champions", None),
                        ("POST", "/coach", b'{"a": 1}'), ("POST", "/coach", b'{"bad": 1}'),
                        ("POST", "/coach/briefing", b'{"a": 1}')]
        # 화이트리스트 밖은 넘기지 않는다
        for method, path in (("POST", "/matches"), ("GET", "/matches"), ("GET", "/health"), ("POST", "/overlay"),
                             ("GET", "/"), ("POST", "/bridge/state")):
            assert _req(port, method, path, b"{}" if method == "POST" else None)[0] == 404, (method, path)
        assert len(seen) == 5
        big = b"x" * (RELAY_MAX_BODY + 1)
        assert _req(port, "POST", "/coach", big)[0] == 413 and len(seen) == 5
        assert _req(port, "POST", "/coach/briefing", big)[0] == 413 and len(seen) == 5
        assert _req(port, "GET", "/bridge/state")[0] == 200
    finally:
        httpd.shutdown()


def test_relay_off_without_server_and_502_when_down():
    clock, calls = Clock(), []
    httpd = serve_state(make_bridge([at(900.0)], clock, calls), 0)       # 서버 주소 없음 → 중계 없음
    try:
        assert _req(httpd.server_address[1], "GET", "/overlay")[0] == 404
    finally:
        httpd.shutdown()

    def down(method, path, body):
        raise urllib.error.URLError("연결 거부")
    httpd = serve_state(make_bridge([at(900.0)], clock, calls), 0, down)
    try:
        code, _, body = _req(httpd.server_address[1], "GET", "/overlay")
        assert code == 502 and "서버에 연결하지 못했다" in body.decode()
    finally:
        httpd.shutdown()


# ── 실게임 127.0.0.1:2999 덜 찬 응답 (연습 모드·리플레이 시작, AGENTS.md 3-1절 "덜 찬 경우") ──
# 기대값은 머리 주석의 규칙으로 손으로 정했다: 점수 0, 레벨 1, 첫 킬 없음 → 상대 팀, 0초는 1분으로 보아 배율 10(0 × 10 = 0)
from client.bridge import coach_input, enemy_item_ids  # noqa: E402

ZERO = {"kills": 0, "assists": 0, "cs": 0, "avg_level": 1.0, "dragons": 0, "heralds": 0, "towers": 0}


def early(seconds: float = 0.0) -> dict:
    """게임 시작 직후: gameTime 0.0, 이벤트 없음, abilities 빈 사전, 모든 선수 items [] 와 점수 0, 레벨 1."""
    d = at(seconds)
    d["events"]["Events"] = []
    d["activePlayer"]["abilities"] = {}
    d["activePlayer"]["level"] = 1
    for p in d["allPlayers"]:
        p["items"], p["level"] = [], 1
        p["scores"] = {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0, "wardScore": 0.0}
    return d


def api_coach(client):
    """브리지의 coach 자리에 실제 POST /coach (TestClient). 200 이 아니면 브리지처럼 HTTPError."""
    sent = []

    def coach(body):
        sent.append(body)
        res = client.post("/coach", json=body)
        if res.status_code != 200:
            raise urllib.error.HTTPError("http://x/coach", res.status_code, res.text, {}, io.BytesIO(res.content))
        return res.json()

    return coach, sent


def test_game_start_zero_state_converts_and_coaches(client):  # noqa: F811
    d = early()
    state, info = to_game_state(d)
    assert (state["blue"], state["red"]) == (ZERO, ZERO)
    assert (state["first_blood"], state["my_side"]) == ("red", "blue")  # 첫 킬 전: 상대 팀
    assert state["enemy_top_threat"] == "Zed"  # 모두 0킬·0CS 면 레드 목록 첫 선수 B1
    assert info == {"game_time": 0.0, "first_blood_seen": False, "item_gold": {"blue": 0.0, "red": 0.0},
                    "champions": {"my_champion": "Ahri", "enemy_top_threat": "Zed"}, "enemy_items": []}
    assert coach_input(state, info) == state  # 공개 아이템이 없으면 enemy_items 칸을 넣지 않는다
    assert extract_features_from_liveclient(d) == state  # 0 × 10 = 0, 레벨은 그대로
    assert extract_owned_items(d) == []

    coach, sent = api_coach(client)
    st = OverlayBridge(lambda: d, coach, clock=Clock()).step()
    assert st["connected"] and st["error"] is None and st["coach_error"] is None
    assert st["in_game"] is False and st["game_time_sec"] == 0.0 and (st["minutes"], st["pace_scale"]) == (1.0, 10.0)
    assert sent == [state] and 0 < st["coach"]["my_win_prob"] < 1
    assert st["coach"]["build_advice"]["skill_order"]["order"] == "Q > W > E"  # 규칙표 Ahri
    assert (st["level"], st["next_skill"], st["next_skill_when"]) == (1, "Q", "now")  # abilities {}: 1레벨 첫 스킬
    assert st["owned_items"] == [] and st["enemy_items"] == []


def test_practice_tool_alone_has_no_enemy_team(client):  # noqa: F811
    d = early(95.0)
    d["allPlayers"] = [_player(d, "A1")]
    me = d["allPlayers"][0]
    me["level"], me["scores"]["creepScore"] = 3, 12
    state, info = to_game_state(d)
    # 레드 선수 0명: 킬·CS 0, 평균 레벨은 블루와 같게(3.0, 레벨 차이 0). 0으로 나누지 않는다
    assert state["blue"] == {**ZERO, "cs": 12, "avg_level": 3.0}
    assert state["red"] == {**ZERO, "avg_level": 3.0}
    assert "enemy_top_threat" not in state and state["my_champion"] == "Ahri" and info["enemy_items"] == []

    coach, sent = api_coach(client)
    st = OverlayBridge(lambda: d, coach, clock=Clock()).step()
    assert st["connected"] and st["error"] is None and st["coach_error"] is None
    assert st["in_game"] and len(sent) == 1
    # 95초 → 1.5833분, 배율 10/1.5833 = 6.3158. CS 12 → 75.8 → 76
    assert sent[0]["blue"]["cs"] == 76 and sent[0]["red"] == {**ZERO, "avg_level": 3.0}


def test_enemy_items_skip_bad_values_and_keep_unknown_ids():
    b1 = {"items": [{"itemID": 999999}, {"itemID": 0}, {"itemID": "abc"}, {"itemID": 1000000}, {"price": 0},
                    {"itemID": "3153"}, "garbage", {"itemID": None}, {"itemID": 223172}, {"itemID": -5}]}
    # 정수로 읽히고 1~999999 인 것만, 순서 그대로. 게임 데이터에 없는 999999·223172 도 남긴다(서버가 무시)
    assert enemy_item_ids(b1) == [999999, 3153, 223172]
    assert enemy_item_ids({"items": [{"itemID": 1000 + i} for i in range(15)]}) == [1000 + i for i in range(12)]
    assert enemy_item_ids(None) == [] and enemy_item_ids({"items": None}) == [] and enemy_item_ids({}) == []


def test_unknown_enemy_item_ids_are_accepted_by_coach(client):  # noqa: F811
    d = early()
    _player(d, "B1")["items"] = [{"itemID": 999999}, {"itemID": "abc"}, {"itemID": 0}, {"itemID": 123456}]
    state, info = to_game_state(d)
    assert info["enemy_items"] == [999999, 123456]

    coach, sent = api_coach(client)
    st = OverlayBridge(lambda: d, coach, clock=Clock()).step()
    assert st["connected"] and st["coach_error"] is None and st["enemy_items"] == [999999, 123456]
    assert sent == [{**state, "enemy_items": [999999, 123456]}]
    # 모르는 ID 는 위협 보정을 바꾸지 않는다: 아이템 없이 부른 응답과 빌드가 같다
    base = client.post("/coach", json=state).json()["build_advice"]
    assert st["coach"]["build_advice"]["threat_type"] == base["threat_type"]
    assert st["coach"]["build_advice"]["tactical_tip"] == base["tactical_tip"]
    assert client.post("/coach", json={**state, "enemy_items": [1000000]}).status_code == 422  # 그래서 브리지가 거른다


def test_known_enemy_heal_item_switches_threat(client):  # noqa: F811
    d = early(600.0)
    _player(d, "B1")["items"] = [{"itemID": 999999}, {"itemID": 3153}]  # 모르는 ID + 몰락한 왕의 검(회복)
    coach, sent = api_coach(client)
    st = OverlayBridge(lambda: d, coach, clock=Clock()).step()
    assert sent[0]["enemy_items"] == [999999, 3153]
    assert st["coach"]["build_advice"]["threat_type"] == "HEAL"
    assert st["coach"]["build_advice"]["tactical_tip"].startswith("상대 회복·흡혈 코어 감지")


def test_malformed_numbers_never_crash_step(client):  # noqa: F811
    d = early(120.0)
    me = _player(d, "A1")
    me["items"] = [{"itemID": None, "count": None}, {"itemID": 1055, "count": None, "displayName": "도란의 검"},
                   {"itemID": "x"}, "garbage"]
    me["scores"]["kills"] = None
    _player(d, "B2")["level"] = None  # 레벨을 모르면 1
    d["activePlayer"]["abilities"] = {"Q": {"abilityLevel": None}, "W": "garbage", "E": {}}
    d["activePlayer"]["level"] = "abc"

    state, _ = to_game_state(d)
    assert state["blue"]["kills"] == 0 and state["red"]["avg_level"] == 1.0
    assert extract_owned_items(d) == [{"itemID": 1055, "displayName": "도란의 검", "count": 1, "slot": None}]

    coach, _ = api_coach(client)
    st = OverlayBridge(lambda: d, coach, clock=Clock()).step()  # 예외가 밖으로 나오면 브리지 프로세스가 끝난다
    assert st["connected"] and st["error"] is None and st["coach_error"] is None
    # activePlayer.level 이 숫자가 아니면 내 선수 level(1). abilities: Q 0, E 0 → 1레벨 첫 스킬 Q 를 지금
    assert (st["level"], st["next_skill"], st["next_skill_when"]) == (1, "Q", "now")
    assert st["owned_items"] == [{"itemID": 1055, "displayName": "도란의 검", "count": 1, "slot": None}]


def test_422_retry_also_drops_enemy_items():
    sent = []

    def coach(state):
        sent.append(state)
        if "enemy_items" in state:
            raise _http_422()
        return {"ok": True}

    body = {"blue": {}, "red": {}, "enemy_items": [999999]}  # 챔피언 칸 없이 아이템만 있어도 한 번 다시
    assert coach_with_fallback(coach, body) == {"ok": True}
    assert sent == [body, {"blue": {}, "red": {}}]


# ── Windows --app 창 항상 위 고정 (AGENTS.md 3-4절 "Windows 항상 위 고정"). 가짜 user32·ctypes 로 ──
from client.bridge import (CHROME_CLASS, HWND_TOPMOST, PIN_FLAGS, PIN_SETTLE_SEC, PIN_SETTLE_TIMES, PIN_TRIES, SWP_NOACTIVATE,  # noqa: E402
                           Win32Windows, _pin_windows_topmost_async, find_overlay_windows, launch_popup, pin_topmost)


class FakeCT:
    """ctypes 대역: WINFUNCTYPE 는 함수를 그대로, byref 는 상자를 그대로 넘긴다."""

    class wintypes:  # noqa: N801  ctypes.wintypes 와 같은 이름
        BOOL = HWND = LPARAM = int

        class DWORD:
            def __init__(self):
                self.value = 0

    @staticmethod
    def WINFUNCTYPE(*_types):  # noqa: N802
        return lambda fn: fn

    @staticmethod
    def byref(box):
        return box

    class _Buf:
        value = ""

    @staticmethod
    def create_unicode_buffer(_n):
        return FakeCT._Buf()


class FakeUser32:
    """windows: {hwnd: (pid, 제목, 클래스, 보임)}. SetWindowPos 호출을 그대로 기록한다."""

    def __init__(self, windows, set_ok=True):
        self.windows, self.set_ok, self.calls = windows, set_ok, []

    def EnumWindows(self, cb, lparam):  # noqa: N802
        for h in list(self.windows):
            if not cb(h, lparam):
                break
        return 1

    def IsWindowVisible(self, h):  # noqa: N802
        return self.windows[h][3]

    def GetWindowThreadProcessId(self, h, box):  # noqa: N802
        box.value = self.windows[h][0]
        return 1

    def GetWindowTextW(self, h, buf, n):  # noqa: N802
        buf.value = self.windows[h][1][:n]
        return len(buf.value)

    def GetClassNameW(self, h, buf, n):  # noqa: N802
        buf.value = self.windows[h][2][:n]
        return len(buf.value)

    def SetWindowPos(self, *args):  # noqa: N802
        self.calls.append(args)
        return 1 if self.set_ok else 0


PORT, PID = 8765, 4242
OTHER_CHROME = (999, "새 탭 - Chrome", CHROME_CLASS, True)  # 사용자의 평소 브라우저 창: 절대 고정하면 안 된다


def pins(u32):
    return [c[0] for c in u32.calls]


def run_pin(windows, **kw):
    u32, reports, slept = FakeUser32(windows), [], []
    kw.setdefault("settle", 0)  # 처음 굳히기 재고정은 따로 본다(test_settle_*)
    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, reports.append, sleep=slept.append, **kw)
    return u32, reports, slept


def test_pin_finds_by_pid_first():
    u32, reports, _ = run_pin({1: OTHER_CHROME, 2: (PID, "페이커 오버레이", CHROME_CLASS, True),
                               3: (PID, "숨은 창", CHROME_CLASS, False)})
    assert pins(u32) == [2]  # 보이는 PID 창만
    assert reports[-1] == {"status": "pinned", "method": "pid", "windows": 1, "repins": 0, "error": None}


def test_pin_falls_back_to_title_when_pid_has_no_window():
    # 이미 켜진 Chrome 에 --app 창이 합쳐져 띄운 PID 에는 창이 없다
    u32, reports, _ = run_pin({1: OTHER_CHROME, 2: (777, "페이커 오버레이", CHROME_CLASS, True)})
    assert pins(u32) == [2] and reports[-1]["method"] == "title"
    u32, reports, _ = run_pin({1: OTHER_CHROME, 2: (777, "LoL 페이스메이커 (페이커)", "Edge", True)})
    assert pins(u32) == [2] and reports[-1]["method"] == "title"


def test_pin_falls_back_to_chrome_class_with_overlay_address():
    u32, reports, _ = run_pin({1: OTHER_CHROME, 2: (777, f"127.0.0.1:{PORT}/overlay?bridge_url=", CHROME_CLASS, True),
                               3: (778, f"127.0.0.1:{PORT}/overlay", "Notepad", True)})
    assert pins(u32) == [2] and reports[-1]["method"] == "class"


def test_pin_not_found_records_error_and_never_pins_other_chrome():
    u32, reports, slept = run_pin({1: OTHER_CHROME, 2: (777, "메모장", "Notepad", True)})
    assert u32.calls == []  # 클래스명만 같은 평소 브라우저 창은 건드리지 않는다
    assert len(slept) == PIN_TRIES - 1  # 0.25초 간격 25번 시도
    last = reports[-1]
    assert (last["status"], last["method"], last["windows"]) == ("failed", None, 0)
    assert f"PID {PID}" in last["error"] and "페이커 오버레이" in last["error"] and f"127.0.0.1:{PORT}" in last["error"]
    assert reports[0] == {"status": "searching"}


def test_pin_found_after_a_few_tries():
    windows = {1: OTHER_CHROME}
    u32, reports, slept = FakeUser32(windows), [], []

    def sleep(sec):
        slept.append(sec)
        if len(slept) == 3:  # 0.75초 뒤 창이 뜬다
            windows[2] = (PID, "페이커 오버레이", CHROME_CLASS, True)

    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, reports.append, sleep=sleep, settle=0)
    assert slept == [0.25] * 3 and pins(u32) == [2] and reports[-1]["status"] == "pinned"


def test_repin_interval_count_and_lost_window():
    windows = {2: (PID, "페이커 오버레이", CHROME_CLASS, True)}
    u32, reports, slept = FakeUser32(windows), [], []

    def sleep(sec):
        slept.append(sec)
        if len(slept) == 2:
            del windows[2]  # 두 번째 재고정 전에 창을 닫음
        if len(slept) == 3:
            windows[5] = (777, "페이커 오버레이", CHROME_CLASS, True)  # 다시 열림 (다른 프로세스)

    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, reports.append, sleep=sleep, repin_sec=3.0, max_repins=4,
                settle=0)
    assert slept == [3.0] * 4
    assert pins(u32) == [2, 2, 5, 5]  # 처음 1 + 재고정 4번 중 창이 있던 3번
    assert [(r["status"], r["repins"]) for r in reports[1:]] == [
        ("pinned", 0), ("pinned", 1), ("lost", 2), ("pinned", 3), ("pinned", 4)]
    assert reports[3]["error"].startswith("다시 고정할 오버레이 창을 찾지 못했다")
    assert reports[4]["method"] == "title"


def test_every_setwindowpos_keeps_noactivate():
    u32, _, _ = run_pin({2: (PID, "페이커 오버레이", CHROME_CLASS, True), 3: (PID, "페이커 오버레이 2", CHROME_CLASS, True)},
                        repin_sec=3.0, max_repins=3)
    assert len(u32.calls) == 8
    for hwnd, after, x, y, cx, cy, flags in u32.calls:
        assert after == HWND_TOPMOST and (x, y, cx, cy) == (0, 0, 0, 0)
        assert flags & SWP_NOACTIVATE and flags == PIN_FLAGS == 0x0013  # NOSIZE | NOMOVE | NOACTIVATE


def test_setwindowpos_failure_is_reported():
    u32, reports = FakeUser32({2: (PID, "페이커 오버레이", CHROME_CLASS, True)}, set_ok=False), []
    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, reports.append, sleep=lambda s: None)
    assert (reports[-1]["status"], reports[-1]["error"]) == ("failed", "SetWindowPos 가 실패했다")


def test_settle_repins_ten_times_one_second_apart_by_default():
    # --repin-sec 를 안 줘도 띄운 직후 10초 동안 1초마다 다시 고정한다(게임 창을 눌러 풀리는 경우)
    u32, reports, slept = FakeUser32({2: (777, "페이커 오버레이", CHROME_CLASS, True)}), [], []
    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, reports.append, sleep=slept.append)
    assert (PIN_SETTLE_TIMES, PIN_SETTLE_SEC) == (10, 1.0)
    assert slept == [1.0] * 10  # 그 뒤엔 멈춘다
    assert pins(u32) == [2] * 11  # 처음 1 + 굳히기 10
    assert [r["repins"] for r in reports[1:]] == list(range(11))
    assert all(r["status"] == "pinned" and r["method"] == "title" for r in reports[1:])
    assert all(c[1] == HWND_TOPMOST and c[6] == PIN_FLAGS for c in u32.calls)


def test_settle_then_periodic_repin_and_lost_during_settle():
    windows = {2: (PID, "페이커 오버레이", CHROME_CLASS, True)}
    u32, reports, slept = FakeUser32(windows), [], []

    def sleep(sec):
        slept.append(sec)
        if len(slept) == 2:
            del windows[2]  # 굳히는 중에 창이 잠깐 사라짐
        if len(slept) == 3:
            windows[2] = (PID, "페이커 오버레이", CHROME_CLASS, True)

    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, reports.append, sleep=sleep, repin_sec=3.0, max_repins=12)
    assert slept == [1.0] * 10 + [3.0] * 2  # 굳히기 10번 뒤 --repin-sec 간격
    assert [r["status"] for r in reports[1:4]] == ["pinned", "pinned", "lost"]
    assert len(u32.calls) == 12  # 처음 1 + 재고정 12번 중 창이 있던 11번


def test_find_order_unit():
    win = Win32Windows(FakeUser32({1: (PID, "x", "y", True), 2: (5, "페이커 오버레이", CHROME_CLASS, True)}), FakeCT)
    assert find_overlay_windows(win, PID, PORT) == ("pid", [1])  # PID 가 있으면 제목은 보지 않는다


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_non_windows_never_takes_the_pin_path(monkeypatch, system):
    import ctypes
    import threading as th

    monkeypatch.setattr(ctypes, "windll", None, raising=False)  # 건드리면 AttributeError
    started = []
    monkeypatch.setattr(th.Thread, "start", lambda self: started.append(self))
    assert _pin_windows_topmost_async(PID, PORT, lambda r: None, 3.0, system=system) is False
    assert started == []


@pytest.mark.parametrize("system, how, cmd, want_status, pinned", [
    ("Darwin", "swift", ["/cache/overlay-popup"], "not_windows", False),
    ("Linux", "chrome", ["chrome", "--app=x"], "not_windows", False),
    ("Windows", "chrome", ["chrome.exe", "--app=x"], "searching", True),
    ("Windows", "browser", None, "failed", False),
])
def test_launch_popup_reports_into_bridge_state(monkeypatch, system, how, cmd, want_status, pinned):
    import client.bridge as br

    calls = []
    monkeypatch.setattr(br, "popup_command", lambda url, system=None: (how, cmd))
    monkeypatch.setattr(br.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": PID})())
    monkeypatch.setattr(br.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(br, "_pin_windows_topmost_async",
                        lambda pid, port, report, repin, system: (calls.append((pid, port, repin)), report({"status": "searching"})))
    b = make_bridge([at(900.0)], Clock(), [])
    assert launch_popup(PORT, b.set_popup, 3.0, system=system) == how
    st = b.snapshot()["popup"]
    assert st["how"] == how and st["status"] == want_status
    assert (calls == [(PID, PORT, 3.0)]) is pinned
    if how == "browser":
        assert st["error"] == "Chrome·Edge 가 없어 항상 위로 고정할 수 없다"
    b.step()
    assert b.snapshot()["popup"]["how"] == how  # 게임 상태를 갱신해도 popup 은 남는다


# ── 시각 경고 (AGENTS.md 3-4절 "시각 경고", docs/pace_scaling_analysis.md 5절) ──
from client.bridge import PACE_EARLY_END, PACE_LATE_FROM, PACE_TEXT, PACE_VERY_LATE_FROM, pace_warning  # noqa: E402


@pytest.mark.parametrize("seconds, level, text", [
    (0.0, "early", "초반 환산 ×10.0 · 킬 1개가 10분보다 크게 반영돼요"),     # 1분 전은 1분으로 보아 ×10
    (300.0, "early", "초반 환산 ×2.0 · 킬 1개가 10분보다 크게 반영돼요"),
    (449.9, "early", "초반 환산 ×1.3 · 킬 1개가 10분보다 크게 반영돼요"),    # 10/7.498 = 1.33
    (450.0, None, None),                                                   # 7:30 부터는 초반 경고 없음
    (600.0, None, None),
    (719.9, None, None),
    (720.0, "late", "12분 이후 · 10분 모델 범위 밖(레벨) · 승률은 참고만"),  # 12:00 부터
    (1199.9, "late", "12분 이후 · 10분 모델 범위 밖(레벨) · 승률은 참고만"),
    (1200.0, "very_late", "20분 이후 · 포탑 차이도 범위 밖 · 우세·열세 방향만 보세요"),
    (1500.0, "very_late", "20분 이후 · 포탑 차이도 범위 밖 · 우세·열세 방향만 보세요"),
])
def test_pace_warning_boundaries(seconds, level, text):
    w = pace_warning(seconds)
    assert (w and (w["level"], w["text"])) == ((level, text) if level else None)


def test_pace_warning_in_bridge_state():
    for seconds, want in ((390.0, "early"), (600.0, None), (860.0, "late"), (1390.0, "very_late")):  # 데모 세 시각 + 10분
        st = make_bridge([at(seconds)], Clock(), []).step()
        assert (st["pace_warning"] or {}).get("level") == want, seconds


def test_hud_uses_same_pace_boundaries_and_texts(client):  # noqa: F811
    t = client.get("/overlay").text
    assert (f"PACE_EARLY_END = {PACE_EARLY_END:g}, PACE_LATE_FROM = {PACE_LATE_FROM:g}, "
            f"PACE_VERY_LATE_FROM = {PACE_VERY_LATE_FROM:g}") in t
    assert PACE_TEXT["late"] in t and PACE_TEXT["very_late"] in t
    assert PACE_TEXT["early"].replace("{scale}", "${(10 / Math.max(t / 60, 1)).toFixed(1)}") in t
    assert 'id="paceWarn"' in t and '$("paceWarn").textContent = pw ? pw.text : ""' in t


def test_save_match_on_early_disconnect_after_60s():
    saved_calls = []
    frames = [at(180.0)]  # 3분 유효 게임 뒤 연결 끊김(연습모드 중도 나가기)

    def fetch():
        if frames:
            return frames.pop(0)
        raise ConnectionError("game exited")

    b = OverlayBridge(fetch, lambda s: {"my_win_prob": 0.5, "build_advice": {"skill_order": {"order": "Q > W > E"}}},
                      save=lambda p: (saved_calls.append(p), {"id": 99, "created_at": "now"})[1])
    st1 = b.step()
    assert st1["connected"] and saved_calls == []
    st2 = b.step()
    assert not st2["connected"] and len(saved_calls) == 1
    assert saved_calls[0]["win"] is None and saved_calls[0]["game_time_sec"] == 180
    assert st2["saved_match"] == {"id": 99, "created_at": "now", "win": None}
    b.step()
    assert len(saved_calls) == 1  # 중복 저장 안 함


def test_resets_saved_flag_when_new_game_starts():
    saved_calls = []
    frames = [at(180.0), None, at(10.0), at(150.0), None]

    def fetch():
        f = frames.pop(0)
        if f is None:
            raise ConnectionError("between games")
        return f

    b = OverlayBridge(fetch, lambda s: {"my_win_prob": 0.5, "build_advice": {"skill_order": {"order": "Q > W > E"}}},
                      save=lambda p: (saved_calls.append(p), {"id": len(saved_calls), "created_at": "now"})[1])
    b.step()  # 1판 180s
    b.step()  # 1판 끊김 -> 저장 1회
    b.step()  # 2판 10s 시작 -> saved 리셋
    b.step()  # 2판 150s
    b.step()  # 2판 끊김 -> 저장 2회
    assert len(saved_calls) == 2 and saved_calls[1]["game_time_sec"] == 150


# ── 실게임 시험 전 보강 (AGENTS.md 3-4 "눌러도 초점 유지", "상태 포트가 이미 쓰이는 경우") ──
from client.bridge import (GWL_EXSTYLE, WS_EX_NOACTIVATE, OverlayBridge, bridge_running,  # noqa: E402
                           port_busy_message, run_overlay, serve_state)


class StyledUser32(FakeUser32):
    """확장 스타일 함수를 가진 user32 대역. styles: {hwnd: 현재 확장 스타일}."""

    def __init__(self, windows, styles=None, fail=False):
        super().__init__(windows)
        self.styles, self.fail, self.puts = dict(styles or {}), fail, []

    def GetWindowLongPtrW(self, h, idx):  # noqa: N802
        if self.fail:
            raise OSError("access denied")
        assert idx == GWL_EXSTYLE
        return self.styles.get(h, 0x100)

    def SetWindowLongPtrW(self, h, idx, value):  # noqa: N802
        self.puts.append((h, idx, value))
        self.styles[h] = value
        return 0


def test_pin_adds_noactivate_so_clicking_hud_keeps_game_focus():
    win = {2: (PID, "페이커 오버레이", CHROME_CLASS, True)}
    u32 = StyledUser32(win)
    pin_topmost(Win32Windows(u32, FakeCT), PID, PORT, [].append, sleep=lambda _s: None, settle=2)
    assert u32.styles[2] == 0x100 | WS_EX_NOACTIVATE  # 원래 스타일은 지키고 비트만 더한다
    assert len(u32.puts) == 1  # 재고정 2번에서는 이미 들어 있어 다시 쓰지 않는다
    assert pins(u32) == [2, 2, 2]


def test_pin_still_pins_when_style_functions_missing_or_failing():
    win = {2: (PID, "페이커 오버레이", CHROME_CLASS, True)}
    u32, reports, _ = run_pin(win)  # FakeUser32 에는 확장 스타일 함수가 없다
    assert pins(u32) == [2] and reports[-1]["status"] == "pinned"
    assert Win32Windows(u32, FakeCT).no_activate(2) is False
    bad = StyledUser32(win, fail=True)
    reports = []
    pin_topmost(Win32Windows(bad, FakeCT), PID, PORT, reports.append, sleep=lambda _s: None, settle=0)
    assert pins(bad) == [2] and reports[-1] == {"status": "pinned", "method": "pid", "windows": 1,
                                                "repins": 0, "error": None}


def test_port_busy_message_tells_what_to_do():
    assert "이미 실행 중" in port_busy_message(8765, True)
    assert "--listen-port" in port_busy_message(8765, False)


def test_second_launch_exits_with_message_instead_of_traceback(capsys):
    first = OverlayBridge(lambda: (_ for _ in ()).throw(OSError("no game")), lambda body: {})
    httpd = serve_state(first, 0)
    port = httpd.server_address[1]
    try:
        assert bridge_running(port) is True
        second = OverlayBridge(lambda: {}, lambda body: {})
        with pytest.raises(SystemExit) as ex:
            run_overlay(second, port, max_steps=1, sleep=lambda _s: None)
        assert ex.value.code == 1
        assert "이미 실행 중" in capsys.readouterr().err
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_port_taken_by_other_program(capsys):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert bridge_running(port, timeout=0.3) is False
        with pytest.raises(SystemExit):
            run_overlay(OverlayBridge(lambda: {}, lambda body: {}), port, max_steps=1, sleep=lambda _s: None)
        assert "--listen-port" in capsys.readouterr().err
    finally:
        s.close()

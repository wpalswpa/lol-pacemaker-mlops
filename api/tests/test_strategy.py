"""실시간 인게임 종합 전략 엔진(POST /strategy, POST /strategy/briefing) 단위·통합 테스트."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api import llm_coach


def _base_live(t: float = 900.0) -> dict:
    return {
        "game_time_sec": t,
        "my_side": "blue",
        "first_blood": "blue",
        "me": {
            "champion": "Ahri", "position": "MIDDLE", "level": 10,
            "kills": 3, "deaths": 1, "assists": 2, "cs": 115,
            "current_gold": 600, "item_gold": 3800, "items": [1056, 3020],
            "is_dead": False, "respawn_timer": 0,
        },
        "allies": [
            {"champion": "Garen", "position": "TOP", "level": 10, "kills": 1, "deaths": 4, "assists": 1, "cs": 95, "item_gold": 2800, "items": [3047], "is_dead": False, "respawn_timer": 0},
            {"champion": "LeeSin", "position": "JUNGLE", "level": 9, "kills": 2, "deaths": 2, "assists": 4, "cs": 85, "item_gold": 3300, "items": [3047], "is_dead": False, "respawn_timer": 0},
            {"champion": "Ahri", "position": "MIDDLE", "level": 10, "kills": 3, "deaths": 1, "assists": 2, "cs": 115, "current_gold": 600, "item_gold": 3800, "items": [1056, 3020], "is_dead": False, "respawn_timer": 0},
            {"champion": "Jinx", "position": "BOTTOM", "level": 10, "kills": 5, "deaths": 0, "assists": 3, "cs": 135, "item_gold": 5300, "items": [3006, 3031], "is_dead": False, "respawn_timer": 0},
            {"champion": "Lux", "position": "UTILITY", "level": 9, "kills": 0, "deaths": 1, "assists": 7, "cs": 20, "item_gold": 2500, "items": [3865], "is_dead": False, "respawn_timer": 0},
        ],
        "enemies": [
            {"champion": "Darius", "position": "TOP", "level": 11, "kills": 4, "deaths": 1, "assists": 1, "cs": 128, "item_gold": 4800, "items": [3047, 3071], "is_dead": False, "respawn_timer": 0},
            {"champion": "Warwick", "position": "JUNGLE", "level": 9, "kills": 2, "deaths": 2, "assists": 3, "cs": 80, "item_gold": 3200, "items": [], "is_dead": False, "respawn_timer": 0},
            {"champion": "Zed", "position": "MIDDLE", "level": 9, "kills": 1, "deaths": 3, "assists": 2, "cs": 100, "item_gold": 3400, "items": [1036], "is_dead": False, "respawn_timer": 0},
            {"champion": "Caitlyn", "position": "BOTTOM", "level": 9, "kills": 1, "deaths": 4, "assists": 1, "cs": 96, "item_gold": 3000, "items": [3006], "is_dead": False, "respawn_timer": 0},
            {"champion": "Soraka", "position": "UTILITY", "level": 8, "kills": 0, "deaths": 2, "assists": 5, "cs": 16, "item_gold": 2100, "items": [3865], "is_dead": False, "respawn_timer": 0},
        ],
        "objectives": {"my_dragons": 2, "enemy_dragons": 1, "my_heralds": 1, "enemy_heralds": 0, "my_barons": 0, "enemy_barons": 0, "my_towers": 2, "enemy_towers": 1},
        "momentum": {"recent_ally_kills": 2, "recent_enemy_kills": 1},
    }


def test_strategy_evaluates_all_lanes_and_carry(client):
    res = client.post("/strategy", json=_base_live(900.0))
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["phase"] == "mid" and out["phase_label"] == "중반 운영"
    assert len(out["lanes"]) == 5
    by_pos = {l["position"]: l for l in out["lanes"]}
    assert by_pos["BOTTOM"]["status"] == "ahead" and by_pos["TOP"]["status"] == "behind"
    assert out["ally_carry"] == "원딜(징크스)"
    assert any("원딜" in s["title"] and "탑" in s["title"] for s in out["strategies"])


def test_strategy_adapts_dynamically_to_gold_deaths_and_objectives(client):
    # 1) 보유 골드 >= 다음 추천 아이템 가격 -> 즉시 귀환 구매 전략이 상위에 올라옴
    rich = _base_live(390.0)
    rich["me"]["items"] = [1056]
    rich["me"]["current_gold"] = 1250
    r1 = client.post("/strategy", json=rich).json()
    assert r1["phase"] == "early" and r1["recall_advice"]["can_buy_now"] is True
    assert any(s["category"] == "귀환 타이밍" for s in r1["strategies"])

    # 2) 상대 정글 사망 -> 오브젝트 기회 최우선 콜
    jg_dead = _base_live(1100.0)
    jg_dead["enemies"][1]["is_dead"] = True
    jg_dead["enemies"][1]["respawn_timer"] = 24
    r2 = client.post("/strategy", json=jg_dead).json()
    assert r2["strategies"][0]["category"] == "오브젝트 기회"
    assert "상대 정글" in r2["strategies"][0]["title"]

    # 3) 아군 2명 사망 + 상대 드래곤 3스택 -> 수성 경보 + 영혼 저지
    crisis = _base_live(1560.0)
    crisis["allies"][0]["is_dead"] = True
    crisis["allies"][1]["is_dead"] = True
    crisis["objectives"]["enemy_dragons"] = 3
    r3 = client.post("/strategy", json=crisis).json()
    assert r3["phase"] == "late"
    cats = [s["category"] for s in r3["strategies"]]
    assert "수성 경보" in cats and "영혼 저지" in cats

    # 4) 세 시나리오가 고정 문구가 아니라 서로 완전히 다른 전략을 제시한다
    assert r1["strategies"][0]["title"] != r2["strategies"][0]["title"] != r3["strategies"][0]["title"]


def test_strategy_detects_whole_team_heal_and_tank_comp(client):
    d = _base_live(1000.0)
    # 상대 위협 1위가 아닌 다른 상대 챔피언이 회복 코어(3153)를 사도 팀 전체 조합으로 치감 대응
    d["enemies"][3]["items"] = [3153]
    out = client.post("/strategy", json=d).json()
    assert out["build_advice"]["threat_type"] == "HEAL"
    assert out["build_advice"]["recommended_items"][1]["item_id"] == 3916  # 망각의 구


def test_strategy_blends_ml_model_only_in_8_to_12_min_window(client):
    early = client.post("/strategy", json=_base_live(360.0)).json()
    mid10 = client.post("/strategy", json=_base_live(600.0)).json()
    late = client.post("/strategy", json=_base_live(1500.0)).json()
    assert early["ml_blended"] is False
    assert mid10["ml_blended"] is True
    assert late["ml_blended"] is False


def test_strategy_briefing_with_local_ollama_style_server_and_think_strip(client, monkeypatch):
    reqs = []
    reply_box = ["<think>내부 추론</think>원딜 징크스 중심 한타로 전환하고, 귀환 시 망각의 구부터 챙기세요."]

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            reqs.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": reply_box[0]}}]}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("LLM_API_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat/completions")
        monkeypatch.setenv("LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
        llm_coach.clear_strategy_cache()

        res = client.post("/strategy/briefing", json=_base_live(900.0))
        assert res.status_code == 200
        out = res.json()
        assert out["source"] == "llm"
        assert out["llm_model"] == "qwen3:4b-instruct-2507-q4_K_M"
        assert "<think>" not in out["briefing"] and "징크스" in out["briefing"]
        # 프롬프트 JSON 에 전 라인·골드·오브젝트 정보가 실제로 전달된다
        sent = json.loads(reqs[0]["messages"][1]["content"].split("\n", 1)[1])
        assert "lanes" in sent and "team_gold_diff" in sent and "priority_strategies" in sent

        # 같은 상황 재요청은 20초 캐시로 LLM 을 다시 부르지 않는다
        client.post("/strategy/briefing", json=_base_live(900.0))
        assert len(reqs) == 1

        # 추천 밖 아이템을 지어내면 동적 규칙 문장으로 안전하게 폴백한다
        llm_coach.clear_strategy_cache()
        reply_box[0] = "지금 존야의 모래시계를 바로 사세요."
        bad = client.post("/strategy/briefing", json=_base_live(900.0)).json()
        assert bad["source"] == "dynamic_rule"
        assert bad["fallback_reason"] == "item: 존야의 모래시계"
        assert bad["briefing"] == bad["summary_briefing"]
    finally:
        httpd.shutdown()
        llm_coach.clear_strategy_cache()


# ── H1~H5, M1~M4, M9 테스트 ──
def test_h1_unknown_positions_mark_unconfirmed_and_skip_lane_cards_and_support_cs(client):
    d = _base_live(400.0)
    for p in d["allies"] + d["enemies"]:
        p["position"] = ""
    d["me"]["position"] = ""
    d["me"]["cs"] = 10  # 분당 CS 낮지만 위치 미확인(서폿일 수도 있음) → 성장 보완 경고 금지
    d["allies"][1]["has_smite"] = True
    d["enemies"][1]["has_smite"] = True
    out = client.post("/strategy", json=d).json()
    assert out["lanes_reliable"] is False
    assert all(l["lane_ko"] == "매치업 미확인" for l in out["lanes"])
    cats = {s["category"] for s in out["strategies"]}
    assert not (cats & {"전 라인 운영", "스노우볼", "열세 복구", "성장 보완"})


def test_h2_no_contradictory_calls_and_dead_jungler_requires_even_alive_and_15s(client):
    # 재현 조건: 15:00, 아군 3명 사망(2v4, alive_diff=-2), 상대 정글 부활 4초
    d = _base_live(900.0)
    d["allies"][0]["is_dead"] = True
    d["allies"][1]["is_dead"] = True
    d["allies"][4]["is_dead"] = True
    d["enemies"][1]["is_dead"] = True
    d["enemies"][1]["respawn_timer"] = 4.0
    out = client.post("/strategy", json=d).json()
    cats = [s["category"] for s in out["strategies"]]
    assert "수성 경보" in cats
    assert "오브젝트 기회" not in cats
    assert not (set(cats) & {"오브젝트 기회", "수적 우위", "영혼 확정", "용 타이밍", "스노우볼", "템포 가속"})

    # 수적 우위(비정글 2명 사망) 또는 오브젝트 기회(정글 포함 2명 사망)가 있으면 템포 조절(모순)은 뺀다
    d2 = _base_live(900.0)
    d2["enemies"][0]["is_dead"] = True
    d2["enemies"][2]["is_dead"] = True
    d2["momentum"] = {"recent_ally_kills": 0, "recent_enemy_kills": 4}
    out2 = client.post("/strategy", json=d2).json()
    cats2 = {s["category"] for s in out2["strategies"]}
    assert "수적 우위" in cats2 and "템포 조절" not in cats2

    d3 = _base_live(900.0)
    d3["enemies"][0]["is_dead"] = True
    d3["enemies"][1]["is_dead"] = True
    d3["enemies"][1]["respawn_timer"] = 25.0
    d3["momentum"] = {"recent_ally_kills": 0, "recent_enemy_kills": 4}
    out3 = client.post("/strategy", json=d3).json()
    cats3 = {s["category"] for s in out3["strategies"]}
    assert "오브젝트 기회" in cats3 and "템포 조절" not in cats3


def test_h3_current_gold_never_biases_win_prob_or_team_gold_diff(client):
    sym_team = [
        {"champion": "Garen", "position": "TOP", "level": 7, "kills": 1, "deaths": 1, "assists": 1, "cs": 70, "item_gold": 1200, "items": [1055], "is_dead": False, "respawn_timer": 0},
        {"champion": "LeeSin", "position": "JUNGLE", "level": 7, "kills": 1, "deaths": 1, "assists": 1, "cs": 60, "item_gold": 1200, "items": [1036], "is_dead": False, "respawn_timer": 0},
        {"champion": "Ahri", "position": "MIDDLE", "level": 7, "kills": 1, "deaths": 1, "assists": 1, "cs": 70, "item_gold": 1200, "items": [1056], "is_dead": False, "respawn_timer": 0},
        {"champion": "Jinx", "position": "BOTTOM", "level": 7, "kills": 1, "deaths": 1, "assists": 1, "cs": 70, "item_gold": 1200, "items": [1055], "is_dead": False, "respawn_timer": 0},
        {"champion": "Lux", "position": "UTILITY", "level": 6, "kills": 0, "deaths": 0, "assists": 2, "cs": 15, "item_gold": 800, "items": [3865], "is_dead": False, "respawn_timer": 0},
    ]
    base = {
        "game_time_sec": 900.0, "my_side": "blue", "first_blood": "blue",
        "me": {**sym_team[2], "current_gold": 3500.0},
        "allies": copy.deepcopy(sym_team),
        "enemies": copy.deepcopy(sym_team),
        "objectives": {"my_dragons": 0, "enemy_dragons": 0, "my_heralds": 0, "enemy_heralds": 0, "my_barons": 0, "enemy_barons": 0, "my_towers": 0, "enemy_towers": 0},
        "momentum": {"recent_ally_kills": 0, "recent_enemy_kills": 0},
    }
    out = client.post("/strategy", json=base).json()
    assert out["team_gold_diff"] == 0
    assert out["win_prob"] == pytest.approx(0.5, abs=1e-4)


def test_h4_strategy_briefing_rejects_bad_numbers_3_sentences_and_allows_payload_items():
    pl = {
        "win_prob_pct": 67.0,
        "team_gold_diff": 1470,
        "recall_advice": "보유 1,500G 누적 · 귀환해 상황별 아이템 또는 제어 와드를 챙기세요",
        "priority_strategies": [{"category": "귀환 타이밍", "title": "망각의 구까지 200G 남음", "reason": "골드 200G"}],
        "recommended_items": ["마법사의 신발", "망각의 구", "라바돈의 죽음모자"],
    }
    v = llm_coach.validate_strategy_briefing
    assert v("판세 67%입니다. 망각의 구까지 200G 남았으니 1,500G로 제어 와드를 챙기세요.", pl) is None
    assert v("판세 67%입니다. 망각의 구까지 20만 남았습니다.", pl) == "number: 20"
    assert v("판세 99%로 유리합니다.", pl) == "number: 99"
    assert v("첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.", pl) == "sentences: 3"


def test_h5_strategy_cache_key_uses_category_not_volatile_title():
    r1 = {
        "phase": "mid", "state": "ahead", "win_prob": 0.62,
        "lanes": [{"position": "TOP", "status": "behind"}],
        "strategies": [{"category": "용 타이밍", "title": "다음 드래곤 42초 전"}],
        "build_advice": {"my_champion": "아리", "recommended_items": [{"name": "마법사의 신발"}]},
    }
    r2 = copy.deepcopy(r1)
    r2["strategies"][0]["title"] = "다음 드래곤 40초 전"  # 숫자만 바뀐 동일 카테고리
    assert llm_coach.strategy_cache_key(r1) == llm_coach.strategy_cache_key(r2)


def test_m1_early_starter_recall_and_subcomponent_gold_deduction(client):
    # 90초 전 무아이템 -> starter_items 안내
    start = _base_live(45.0)
    start["me"]["items"] = []
    start["me"]["current_gold"] = 500
    r0 = client.post("/strategy", json=start).json()
    assert "도란의 반지" in r0["recall_advice"]["message"]

    # 쓸데없이 큰 지팡이(1200G)를 이미 보유한 상태에서 라바돈(3500G)까지 남은 골드는 3500-1200-1100 = 1200G
    mid = _base_live(1000.0)
    mid["enemies"][1]["champion"] = "LeeSin"  # 회복 2명 조건 제거 → 2단계 쓸데없이 큰 지팡이(1058)
    mid["enemies"][4]["champion"] = "Lux"
    mid["me"]["items"] = [3020, 1058]  # 신발 + 쓸데없이 큰 지팡이(1200G) 보유
    mid["me"]["current_gold"] = 1100
    r1 = client.post("/strategy", json=mid).json()
    assert r1["recall_advice"]["next_item"] == "라바돈의 죽음모자"
    assert r1["recall_advice"]["gold_needed"] == 1200  # 3500 - 1200(보유 하위템) - 1100(보유 골드)


def test_m2_tank_counter_requires_tank_item_or_15min(client):
    d = _base_live(600.0)  # 10분 (15분 전), 탱커 2명이지만 탱커 코어 아이템은 없음
    d["enemies"][0]["champion"], d["enemies"][0]["items"] = "Ornn", [1054]
    d["enemies"][1]["champion"], d["enemies"][1]["items"] = "Amumu", []
    d["enemies"][4]["champion"] = "Lux"
    r_early = client.post("/strategy", json=d).json()
    assert not any("앞라인 단단" in s["title"] for s in r_early["strategies"])

    # 15분(900초) 이후 또는 탱커 아이템(3068) 보유 시 관통 카드 등장
    d_late = copy.deepcopy(d)
    d_late["game_time_sec"] = 960.0
    r_late = client.post("/strategy", json=d_late).json()
    assert any("앞라인 단단" in s["title"] for s in r_late["strategies"])


def test_m3_elder_separate_from_soul_grubs_and_inhibitor_strategy(client):
    d = _base_live(1650.0)
    d["objectives"]["enemy_dragons"] = 2
    d["objectives"]["enemy_elders"] = 1
    d["objectives"]["my_grubs"] = 4
    d["objectives"]["enemy_inhibs"] = 1  # 상대가 아군 억제기를 1개 파괴함
    out = client.post("/strategy", json=d).json()
    assert out["objectives"]["enemy_dragons"] == 2 and out["objectives"]["enemy_elders"] == 1
    assert out["objectives"]["my_grubs"] == 4 and out["objectives"]["enemy_inhibs"] == 1
    assert not any(s["category"] == "영혼 저지" for s in out["strategies"])  # 장로는 영혼 3스택에 섞이지 않음
    assert any("억제기" in s["title"] for s in out["strategies"])


def test_m4_practice_mode_zero_enemies_and_ally_carry_excludes_me(client):
    # 연습 모드(상대 0명)
    prac = _base_live(300.0)
    prac["enemies"] = []
    prac["allies"] = [prac["me"]]
    out = client.post("/strategy", json=prac).json()
    assert out["practice_mode"] is True and out["lanes"] == [] and out["win_prob"] == pytest.approx(0.5)
    assert not ({s["category"] for s in out["strategies"]} & {"수적 우위", "수성 경보", "전 라인 운영", "스노우볼"})

    # 내가 팀 내 최강이어도 ally_carry 에는 본인을 제외한다
    d = _base_live(900.0)
    d["me"]["kills"], d["me"]["item_gold"] = 12, 8500
    d["allies"][2]["kills"], d["allies"][2]["item_gold"] = 12, 8500  # 아리(나)
    d["allies"][3]["kills"], d["allies"][3]["item_gold"] = 1, 3000   # 원딜 평범
    out2 = client.post("/strategy", json=d).json()
    assert out2["ally_carry"] != "미드(아리)"


def test_m9_is_local_url_uses_hostname_not_substring():
    assert llm_coach._is_local_url("http://127.0.0.1:11434/v1/chat/completions") is True
    assert llm_coach._is_local_url("http://localhost:11434/v1/chat/completions") is True
    assert llm_coach._is_local_url("https://evil.example.com/?next=localhost") is False
    assert llm_coach._is_local_url("https://127.0.0.1.evil.com/v1/chat/completions") is False


def test_asymmetric_1v5_bot_game_uses_fair_diff_and_no_false_outnumbered(client):
    d = _base_live(120.0)
    d["me"].update({"item_gold": 500.0, "kills": 0, "deaths": 0, "assists": 0, "cs": 10, "level": 2})
    d["allies"] = [d["me"]]
    d["objectives"] = {}
    d["momentum"] = {}
    for e in d["enemies"]:
        e.update({"item_gold": 500.0, "kills": 0, "deaths": 0, "assists": 0, "cs": 10, "level": 2, "is_dead": False})
    out = client.post("/strategy", json=d).json()
    assert out["team_gold_diff"] == 0
    assert out["win_prob"] == pytest.approx(0.5, abs=0.01)
    assert "수성 경보" not in {s["category"] for s in out["strategies"]}


def test_w2_1v1_mismatched_positions_pairs_real_opponent_no_unconfirmed(client):
    """W2: 1v1 럼블(MIDDLE) vs 에코(JUNGLE)처럼 포지션이 달라도 '미확인' 짝을 만들지 않고 1v1 상대로 직접 매칭."""
    import json
    from pathlib import Path
    from client.bridge import extract_live_context

    raw = json.loads((Path(__file__).resolve().parents[2] / "client" / "tests" / "fixtures" / "live_rumble_ekko_0001.json").read_text(encoding="utf-8"))
    ctx = extract_live_context(raw)
    out = client.post("/strategy", json=ctx).json()
    assert len(out["lanes"]) == 1
    ln = out["lanes"][0]
    assert ln["ally"]["champion_id"] == "Rumble" and ln["enemy"]["champion_id"] == "Ekko"
    assert ln["ally"]["champion"] != "미확인" and ln["enemy"]["champion"] != "미확인"



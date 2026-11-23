"""POST /coach/briefing 검사 규칙과 캐시 (AGENTS.md 4절). LLM 은 127.0.0.1 의 가짜 OpenAI 호환 서버로 대신한다.

기대값은 계약에서 정했다: payload 숫자(와 그 정수 반올림)만 인용 가능, 아이템은 recommended_items 이름만, 3문장 이상 금지.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api import llm_coach

# 블루 열세, 아리 vs 제드, 상대 회복 아이템(3153) → build_advice HEAL. 규칙 기준선(테스트 고정)에서 승률 35.0%
PAYLOAD = {
    "blue": {"kills": 5, "assists": 6, "cs": 230, "avg_level": 8.0, "dragons": 0, "heralds": 0, "towers": 0},
    "red": {"kills": 7, "assists": 9, "cs": 250, "avg_level": 8.4, "dragons": 1, "heralds": 0, "towers": 1},
    "first_blood": "red", "my_side": "blue", "my_champion": "Ahri", "enemy_top_threat": "Zed", "enemy_items": [3153],
}
ITEMS = ["마법사의 신발", "망각의 구", "라바돈의 죽음모자"]


class FakeLLM:
    """OpenAI 호환 /v1/chat/completions. reply 를 돌려주고 받은 요청을 센다."""

    def __init__(self):
        self.reply, self.requests = "", []
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                fake.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                out = json.dumps({"choices": [{"message": {"content": fake.reply}}]}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/chat/completions"


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.delenv("LLM_API_KEY", raising=False)  # 127.0.0.1 이면 키 없이 부른다(AGENTS.md 2절)
    monkeypatch.setenv("LLM_API_URL", fake.url)
    monkeypatch.setenv("LLM_MODEL", "fake-qwen")
    llm_coach.clear_cache()
    yield fake
    fake.httpd.shutdown()
    llm_coach.clear_cache()


def brief(client, reply=None, llm=None, body=PAYLOAD):
    if reply is not None:
        llm.reply = reply
    res = client.post("/coach/briefing", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_prompt_payload_is_the_fact_sheet(client, llm):
    out = brief(client, "승률 35%입니다. 망각의 구부터 사세요.", llm)
    sent = json.loads(llm.requests[0]["messages"][1]["content"].split("\n", 1)[1])
    assert sent["win_prob_pct"] == 35.0 and sent["state"] == "열세" and sent["horizon_min"] == 3.0
    assert sent["recommended_items"] == ITEMS and out["build_advice"]["threat_type"] == "HEAL"
    assert out["coaching"][0]["message"] == "킬 2개 더 → 상위 70%에서 50%로 (승률 35.0% → 40.6%, +5.6%p)"


@pytest.mark.parametrize("reply, reason", [
    ("승률 35%입니다. 황금의 대검을 먼저 사세요.", "item: 황금의 대검"),                 # 지어낸 아이템 (X의 장비 명사)
    ("승률 35%입니다. [마법사의 신발 -> 무적의 부츠] 순서로 사세요.", "item: 무적의 부츠"),  # 괄호 안 지어낸 이름
    ("승률 35%입니다. 존야의 모래시계를 사세요.", "item: 존야의 모래시계"),              # 있는 아이템이지만 추천 밖
    ("승률 37%입니다. 망각의 구를 먼저 사세요.", "number: 37"),                         # 바뀐 승률
    ("승률 35%입니다. 킬 4개를 더 하면 승률 40.6%입니다.", "number: 4"),                  # 바뀐 목표 수치
    ("승률 35%입니다. 망각의 구를 사세요. 킬 2개를 더 하세요.", "sentences: 3"),           # 3문장
    ("승률 35%입니다.\n망각의 구를 사세요\n킬 2개를 더 하세요", "sentences: 3"),          # 줄바꿈도 문장 경계
])
def test_bad_llm_replies_fall_back_to_rule(client, llm, reply, reason):
    out = brief(client, reply, llm)
    assert (out["source"], out["fallback_reason"], out["llm_model"]) == ("fallback_rule", reason, "fake-qwen")
    assert out["briefing"] == llm_coach.fallback_briefing(out) and out["briefing"] != reply


@pytest.mark.parametrize("reply", [
    "승률 35%로 열세입니다. 망각의 구를 먼저 사고 다음 3분 동안 킬 2개를 더 노려 승률 40.6%를 만드세요.",
    "현재 35.0%, 1대1은 피하고 마법사의 신발 -> 망각의 구 -> 라바돈의 죽음모자 순으로 맞추세요.",
    "승률 41%까지 올리려면 [마법사의 신발 -> 망각의 구] 순서로 사고 상대의 회복을 끊으세요.",  # 40.6 의 정수 반올림
])
def test_replies_quoting_only_json_values_stay_llm(client, llm, reply):
    out = brief(client, reply, llm)
    assert (out["source"], out["fallback_reason"], out["llm_model"], out["briefing"]) == ("llm", None, "fake-qwen", reply)


def test_cache_one_call_within_ttl_and_again_after_61s(client, llm, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_coach, "_clock", lambda: now[0])
    first = brief(client, "승률 35%입니다. 망각의 구부터 사세요.", llm)
    llm.reply = "승률 35%입니다. 라바돈의 죽음모자까지 가세요."  # 캐시가 맞으면 이 답은 아직 안 나온다
    now[0] += 59.0
    second = brief(client, llm=llm)
    assert len(llm.requests) == 1 and second["briefing"] == first["briefing"] == "승률 35%입니다. 망각의 구부터 사세요."
    now[0] += 2.0  # 처음에서 61초
    third = brief(client, llm=llm)
    assert len(llm.requests) == 2 and third["briefing"] == "승률 35%입니다. 라바돈의 죽음모자까지 가세요."


def test_cache_key_changes_call_again_and_rejections_are_cached(client, llm):
    brief(client, "승률 37%입니다.", llm)                       # 검사 실패도 그 키로 60초 기억
    assert brief(client, llm=llm)["fallback_reason"] == "number: 37" and len(llm.requests) == 1
    other = {**PAYLOAD, "enemy_items": []}                       # 상대 위협 유형이 바뀌어 추천 아이템이 달라진다
    llm.reply = "망각의 구 대신 다른 길을 보세요."
    brief(client, llm=llm, body=other)
    assert len(llm.requests) == 2


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(llm_coach, "BRIEFING_CACHE_MAX", 3)
    llm_coach.clear_cache()
    for i in range(5):
        llm_coach._cache_put(("k", i), {"text": "x", "error": None, "model": "m"})
    assert [k[1] for k in llm_coach._cache] == [2, 3, 4]  # 오래된 것부터 지운다
    llm_coach.clear_cache()


def test_no_llm_configured_is_not_cached_and_says_why(client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
    llm_coach.clear_cache()
    out = brief(client)
    assert (out["source"], out["llm_model"], out["fallback_reason"]) == ("fallback_rule", None, "not_configured")
    assert len(llm_coach._cache) == 0


def test_llm_down_says_error(client, monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "http://127.0.0.1:9/v1/chat/completions")  # 닫힌 포트
    llm_coach.clear_cache()
    out = brief(client)
    assert out["source"] == "fallback_rule" and out["fallback_reason"].startswith("llm_error: ")
    llm_coach.clear_cache()


def test_rule_sentence_is_korean_and_split_by_periods(client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
    for body in (PAYLOAD, {**PAYLOAD, "blue": PAYLOAD["red"], "red": PAYLOAD["blue"]}):  # 열세, 우세
        out = brief(client, body=body)
        text, b = out["briefing"], out["build_advice"]
        state_ko = {"ahead": "우세", "even": "접전", "behind": "열세"}[b["state"]]
        assert f"({state_ko})입니다. " in text
        for eng in ("ahead", "even", "behind"):
            assert eng not in text
        tip = b["tactical_tip"]
        assert f"{tip}. 핵심 목표는" in text  # 운영 팁과 목표 문장이 마침표로 나뉜다
        assert llm_coach.count_sentences(text) == 3


def test_validator_unit_rules():
    pl = {"win_prob_pct": 35.0, "top_goal": "킬 2개 더 (승률 35.0% → 40.6%, +5.6%p)", "horizon_min": 3.0,
          "tactical_tip": "열세 + 상대 AD 위협 -> 방어 하위템 먼저 챙기고 1대1 회피", "recommended_items": ITEMS}
    v = llm_coach.validate_briefing_output
    assert v("승률 35%, 3분 안에 킬 2개로 40.6%와 +5.6%p.", pl) is None
    assert v("승률 36%.", pl) == "number: 36"
    assert v("B.F. 대검은 빼고 망각의 구.", pl) == "item: B.F. 대검"  # B.F. 의 점은 문장 경계가 아니다
    assert v("상대의 회복과 아리의 스킬을 보세요.", pl) is None     # 장비 명사가 아니면 아이템으로 보지 않는다
    assert v("마법사의 신발을 신고 망각의 구를 사세요.", pl) is None  # 조사를 떼고 비교


@pytest.mark.parametrize("path,endpoint,fallback_label", [
    ("/", "/coach/briefing", "규칙 문장"),
    ("/overlay", "/strategy/briefing", "동적 전략"),
])
def test_pages_have_briefing_box_with_source(client, path, endpoint, fallback_label):
    t = client.get(path).text
    assert "AI 감독 브리핑" in t and 'id="briefText"' in t and 'id="briefSrc"' in t
    assert f'fetch("{endpoint}"' in t
    assert "LLM · ${r.llm_model}" in t and fallback_label in t                    # source 표시
    assert '$("briefText").textContent = r.briefing' in t                          # 글자는 textContent 로만
    assert 'briefText").innerHTML' not in t and 'briefSrc").innerHTML' not in t


def test_h5_h8_m6_m7_m8_overlay_html_contracts(client):
    t = client.get("/overlay").text
    assert "BRIEF_MIN_MS = 20000" in t                  # H5: 최소 20초 간격
    assert "x.category" in t                            # H5: 전략 제목 대신 category 로 키 생성
    assert "coachFallbackView" in t                     # H8: strategy 없을 때 coach 폴백
    assert '"X-Bridge-Token"' in t                      # M6: 브리지 제어 토큰 헤더
    assert "stale: !!st.strategy_error" in t            # M7: strategy_error 만 stale 판정
    assert "reqMode !== view.mode" in t or "view.mode !== reqMode" in t  # M7: 모드 변경 시 늦은 응답 폐기
    assert "applyOpac(true);" in t                      # M7: 초기 로드 시 저장된 투명도 OS 창 동기화
    assert '"브리핑 없음"' in t and '"동적 전략"' in t     # M8: AGENTS 문서와 실패/출처 문구 일치
    assert "<title>LoL 페이스메이커</title>" in t         # W6: 실존 선수명 대신 서비스 제목 사용
    assert "lol_window_mode_warning" in t                # W5: 동적 경고 플래그 연동
    assert "openStratCats" in t and "lastBriefSource" in t  # 전략 카드 펼침 유지 + 비LLM 브리핑 실시간 숫자 동기화

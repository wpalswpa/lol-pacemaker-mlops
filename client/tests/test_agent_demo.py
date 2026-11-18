"""AI 코칭 에이전트 시연 (AGENTS.md 4절 "시연 스크립트 client/agent_demo.py").

서버는 실제 앱(규칙 기준선, 고정 자료)을 TestClient 로 띄워 쓴다. 기대값은 서버 응답에서 꺼내 비교하고,
"지어낸 숫자 금지"는 브리핑의 숫자를 모두 뽑아 응답에서 나올 수 있는 값인지로 확인한다.
"""
import copy
import json
import re

import pytest

from api.tests.conftest import client as client  # noqa: F401  (세션 고정 TestClient)
from api.tests.conftest import app_module as app_module  # noqa: F401
from client.agent_demo import SCENARIO, compose_briefing, load_tool_schemas, main, run_agent_briefing

BASE = "http://testserver"


@pytest.fixture()
def io(client):  # noqa: F811
    def get(url):
        res = client.get(url.removeprefix(BASE))
        assert res.status_code == 200
        return res.json()

    def post(url, body):
        res = client.post(url.removeprefix(BASE), json=body)
        res.raise_for_status()
        return res.json()

    return get, post


def _has_ref(node) -> bool:
    if isinstance(node, dict):
        return "$ref" in node or any(_has_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_ref(v) for v in node)
    return False


def test_tools_from_openapi(io):
    get, _ = io
    tools = load_tool_schemas(BASE, get)
    assert [t["name"] for t in tools] == ["predict_pace", "coach_targets"]
    assert {t["path"] for t in tools} == {"/predict", "/coach"}
    for t in tools:
        s = t["input_schema"]
        assert t["description"]  # 언어 모델이 언제 부를지 고를 설명
        assert not _has_ref(s)  # 독립 JSON Schema
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert set(s["required"]) == {"blue", "red", "first_blood", "my_side"}
        kills = s["properties"]["blue"]["properties"]["kills"]
        assert kills["type"] == "integer" and (kills["minimum"], kills["maximum"]) == (0, 100)  # AGENTS.md 3절 범위
        assert s["properties"]["first_blood"]["enum"] == ["blue", "red"]


def test_missing_tool_is_an_error():
    spec = {"paths": {"/predict": {"post": {"operationId": "predict_pace", "requestBody": {
        "content": {"application/json": {"schema": {"type": "object"}}}}}}}}
    with pytest.raises(ValueError):
        load_tool_schemas(BASE, lambda url: spec)


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def _allowed(response: dict) -> set[str]:
    """응답에서 나올 수 있는 숫자 표기: 값 그대로, 반올림(0·1·2자리), 확률은 ×100, 문자열 안의 숫자."""
    out: set[str] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            for x in (abs(v), abs(v) * 100):
                out.update({f"{x:.0f}", f"{x:.1f}", f"{x:.2f}", f"{x:g}"})
                if float(x).is_integer():
                    out.add(str(int(x)))
        elif isinstance(v, str):
            out.update(_numbers(v))
        elif isinstance(v, dict):
            for w in v.values():
                walk(w)
        elif isinstance(v, list):
            for w in v:
                walk(w)

    walk(response)
    return out


def test_briefing_uses_only_server_numbers(io):
    get, post = io
    out = run_agent_briefing(BASE, SCENARIO, get, post)
    r, text = out["response"], out["briefing"]
    assert out["tool_call"] == {"name": "coach_targets", "input": SCENARIO["input"]}

    # 서버가 준 값이 그대로 들어간다
    assert f"{r['my_win_prob'] * 100:.1f}%" in text
    assert f"상위 {r['tier_percentile']['overall_top_pct']:.0f}%" in text
    c = r["coaching"][0]
    assert f"{c['name']} +{c['add']:g}" in text and f"{c['win_prob_after'] * 100:.1f}%" in text
    assert f"+{c['gain_pp']:.1f}%p" in text
    for it in r["build_advice"]["recommended_items"]:
        assert f"{it['name']}({it['gold']}G)" in text
    assert r["build_advice"]["skill_order"]["order"] in text
    assert r["model_version"] in text and r["build_advice"]["basis"] in text
    assert r["build_advice"]["enemy_top_threat"] == "제드" and "제드" in text

    # 지어낸 숫자가 없다
    invented = [n for n in _numbers(text) if n not in _allowed(r)]
    assert invented == []


def test_invented_number_would_be_caught(io):
    """검사 자체가 잡는지: 응답에 없는 숫자를 끼워 넣으면 걸린다."""
    get, post = io
    r = run_agent_briefing(BASE, SCENARIO, get, post)["response"]
    tampered = compose_briefing(r) + " 승률이 99.9%까지 오릅니다."
    assert "99.9" not in _allowed(r)
    assert [n for n in _numbers(tampered) if n not in _allowed(r)] == ["99.9"]


def test_no_goal_and_warnings(io):
    get, post = io
    r = copy.deepcopy(run_agent_briefing(BASE, SCENARIO, get, post)["response"])
    r["coaching"] = []
    r["warnings"] = ["kills 차이가 학습 범위 밖"]
    text = compose_briefing(r)
    assert "지금 페이스를 유지" in text and "학습 데이터 범위 밖" in text
    assert [n for n in _numbers(text) if n not in _allowed(r)] == []


def test_role_input_has_no_champion_name(io):
    get, post = io
    sc = copy.deepcopy(SCENARIO)
    sc["input"]["my_champion"], sc["input"]["enemy_top_threat"] = "AD", "AP"
    out = run_agent_briefing(BASE, sc, get, post)
    assert out["response"]["build_advice"]["enemy_top_threat"] is None
    assert "상대 위협(마법 피해, 마법 피해)" in out["briefing"]  # 이름이 없으면 유형으로


def test_unknown_champion_raises_422(io):
    get, post = io
    sc = copy.deepcopy(SCENARIO)
    sc["input"]["my_champion"] = "없는챔피언"
    with pytest.raises(Exception) as e:
        run_agent_briefing(BASE, sc, get, post)
    assert "422" in str(e.value)


def test_unknown_tool_in_scenario(io):
    get, post = io
    with pytest.raises(ValueError):
        run_agent_briefing(BASE, {**SCENARIO, "tool": "bet_odds"}, get, post)


def test_cli_prints_tools_call_and_briefing(io, monkeypatch, capsys):
    import client.agent_demo as d

    get, post = io
    monkeypatch.setattr(d, "http_get", get)
    monkeypatch.setattr(d, "http_post", post)
    assert main(["--server", BASE]) == 0
    out = capsys.readouterr().out
    for word in ("[도구 정의]", "predict_pace", "coach_targets", "[에이전트의 도구 호출]", "[AI 코치 브리핑]", "model_version="):
        assert word in out
    assert main(["--server", BASE, "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["tool_call"]["name"] == "coach_targets" and body["briefing"]

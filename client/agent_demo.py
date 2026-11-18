"""AI 코칭 에이전트 도구 호출 시연 (AGENTS.md 4절 "AI 에이전트 연동").

"AI에게 서비스를 제공한다면 어떻게 하나"를 보여 준다. 에이전트(언어 모델)는 승률을 스스로 어림하지 않고,
서버의 /openapi.json 에서 도구 정의를 받아 coach_targets 를 부른 뒤 응답의 숫자로만 말한다.

언어 모델 API 는 부르지 않는다(키 없이 발표장에서 돌고, 테스트할 수 있게). 언어 모델이 할 일 중
"어느 도구를 어떤 인자로 부를지"만 SCENARIO 에 적어 두고 재생한다. 나머지(도구 정의 만들기, 도구 실행,
응답으로 브리핑 쓰기)는 실제로 한다. 실제 언어 모델에 붙일 때는 load_tool_schemas 의 결과에서 path 를 빼고
도구 정의(name, description, input_schema)로 넘기면 된다.

  uv run python -m client.agent_demo --server http://127.0.0.1:8083
"""
import argparse
import copy
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable

TOOL_NAMES = ("predict_pace", "coach_targets")

# 사용자가 말로 물은 질문과, 언어 모델이 그 질문에서 뽑았을 도구 호출(재생용)
SCENARIO = {
    "question": "지금 블루팀인데 킬은 5대7, CS는 250대268, 드래곤 0대1로 밀리고 있어. "
                "내 챔피언은 아리, 상대 제드가 잘 컸는데 당장 뭘 해야 하고 무슨 템부터 사야 해?",
    "tool": "coach_targets",
    "input": {
        "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0},
        "red": {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0},
        "first_blood": "red",
        "my_side": "blue",
        "my_champion": "아리",
        "enemy_top_threat": "제드",
    },
}

STATE_KO = {"ahead": "우세", "even": "접전", "behind": "열세"}
THREAT_KO = {"AD": "물리 피해", "AP": "마법 피해", "HEAL": "회복", "TANK": "단단함"}

Get = Callable[[str], dict]
Post = Callable[[str, dict], dict]


# ── HTTP (테스트에서는 TestClient 로 바꿔 끼운다) ──
def http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def http_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


# ── 도구 정의 ──
def _inline_refs(node, components: dict, seen: tuple = ()):
    """{"$ref": "#/components/schemas/X"} 를 그 스키마로 바꿔 넣는다(독립 JSON Schema 로)."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            if name in seen:
                raise ValueError(f"순환 참조: {name}")
            merged = {**copy.deepcopy(components[name]), **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline_refs(merged, components, seen + (name,))
        return {k: _inline_refs(v, components, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, components, seen) for v in node]
    return node


def load_tool_schemas(base_url: str, get: Get | None = None) -> list[dict]:
    """/openapi.json → 함수 호출 도구 정의 [{name, description, input_schema, path}] (TOOL_NAMES 순서)."""
    spec = (get or http_get)(base_url.rstrip("/") + "/openapi.json")
    components = spec.get("components", {}).get("schemas", {})
    found = {}
    for path, ops in spec.get("paths", {}).items():
        op = ops.get("post")
        if not op or op.get("operationId") not in TOOL_NAMES:
            continue
        body = op["requestBody"]["content"]["application/json"]["schema"]
        found[op["operationId"]] = {
            "name": op["operationId"],
            "description": " ".join(x for x in (op.get("description"), spec.get("info", {}).get("description")) if x),
            "input_schema": _inline_refs(body, components),
            "path": path,
        }
    missing = [n for n in TOOL_NAMES if n not in found]
    if missing:
        raise ValueError(f"/openapi.json 에 도구가 없다: {missing}")
    return [found[n] for n in TOOL_NAMES]


# ── 브리핑 (응답 숫자만 쓴다. 문장 틀에 숫자를 직접 쓰지 않는다) ──
def _pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def _signed(v: float) -> str:
    return f"{int(v):+d}" if float(v).is_integer() else f"{v:+.1f}"


def compose_briefing(r: dict) -> str:
    b = r["build_advice"]
    side = "블루" if r["my_side"] == "blue" else "레드"
    parts = [f"지금 {side} 팀 승률은 {_pct(r['my_win_prob'])}로 {r['pace']}이고, "
             f"같은 티어 10분 경기 중 상위 {r['tier_percentile']['overall_top_pct']:.0f}% 위치입니다."]
    if r["warnings"]:
        parts.append("다만 학습 데이터 범위 밖 입력이 있어 수치를 조심해서 보세요.")

    factors = [f"{f['name']} 격차 {_signed(f['diff'])}({f['impact_pp']:+.1f}%p)" for f in r["top_factors"][:2]]
    parts.append(f"승률에 가장 크게 작용하는 것은 {', '.join(factors)}입니다.")

    if r["coaching"]:
        c = r["coaching"][0]
        parts.append(f"다음 {r['horizon_min']:g}분 목표는 {c['name']} +{c['add']:g}입니다. "
                     f"채우면 승률이 {_pct(c['win_prob_after'])}(+{c['gain_pp']:.1f}%p)로 오릅니다.")
    else:
        parts.append(f"다음 {r['horizon_min']:g}분은 지금 페이스를 유지하세요.")

    threat = b["enemy_top_threat"] or THREAT_KO.get(b["threat_type"], b["threat_type"])
    items = " → ".join(f"{it['name']}({it['gold']}G)" for it in b["recommended_items"])
    build = (f"빌드는 {STATE_KO[b['state']]} 상태와 상대 위협({threat}, "
             f"{THREAT_KO.get(b['threat_type'], b['threat_type'])})에 맞춰 {items} 순서로 사세요.")
    if b["skill_order"]["order"]:
        build += f" 스킬은 {b['skill_order']['order']} 순서로 올립니다."
    parts.append(build)

    parts.append(f"읽는 법: {r['how_to_read']}")
    parts.append(f"(출처: 모델 판 {r['model_version']}, 빌드 근거: {b['basis']})")
    return " ".join(parts)


def run_agent_briefing(base_url: str, scenario: dict = SCENARIO, get: Get | None = None,
                       post: Post | None = None) -> dict:
    """도구 정의 조회 → 시나리오의 도구 호출 실행 → 응답으로만 브리핑."""
    tools = load_tool_schemas(base_url, get)
    tool = next((t for t in tools if t["name"] == scenario["tool"]), None)
    if tool is None:
        raise ValueError(f"정의에 없는 도구: {scenario['tool']}")
    response = (post or http_post)(base_url.rstrip("/") + tool["path"], scenario["input"])
    return {"tools": tools, "tool_call": {"name": tool["name"], "input": scenario["input"]},
            "response": response, "briefing": compose_briefing(response)}


def _required(schema: dict) -> str:
    return ", ".join(schema.get("required", []))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI 코칭 에이전트 도구 호출 시연")
    ap.add_argument("--server", default="http://127.0.0.1:8083")
    ap.add_argument("--json", action="store_true", help="도구 정의·호출·응답 전체를 JSON 으로")
    a = ap.parse_args(argv)
    try:
        out = run_agent_briefing(a.server)
    except urllib.error.HTTPError as e:
        print(f"서버가 {e.code} 로 거절했다: {e.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"서버에 연결하지 못했다 ({a.server}): {e.reason}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("[도구 정의] GET /openapi.json")
    for t in out["tools"]:
        print(f"  {t['name']:<14} POST {t['path']:<9} 필수 인자: {_required(t['input_schema'])}")
    print(f"\n[사용자 질문]\n  {SCENARIO['question']}")
    print(f"\n[에이전트의 도구 호출] {out['tool_call']['name']}")
    print("  " + json.dumps(out["tool_call"]["input"], ensure_ascii=False))
    r = out["response"]
    print("\n[도구가 돌려준 근거 수치]")
    print(f"  my_win_prob={r['my_win_prob']:.4f}  overall_top_pct={r['tier_percentile']['overall_top_pct']:.1f}  "
          f"pace={r['pace']}  model_version={r['model_version']}")
    if r["coaching"]:
        c = r["coaching"][0]
        print(f"  coaching[0]: {c['name']} +{c['add']:g} → {c['win_prob_after']:.4f} (+{c['gain_pp']:.2f}%p)")
    print("  build: " + " → ".join(it["name"] for it in r["build_advice"]["recommended_items"])
          + f" | skill {r['build_advice']['skill_order']['order']}")
    print(f"\n[AI 코치 브리핑]\n  {out['briefing']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

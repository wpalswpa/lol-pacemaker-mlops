"""ONNX 분석 결과(Fact) 기반 경량 LLM 프로 감독 브리핑 모듈.

ONNX + 규칙표가 먼저 확정 수치(승률·요인·목표·아이템·상대 위협)를 계산한 뒤,
Qwen/Haiku 등 경량 LLM(OpenAI 호환 API)에 그 JSON만 근거로 전달해
할루시네이션 없이 2문장 이내 실전 운영 브리핑을 생성한다.
API 키가 없거나 타임아웃/오류 시에는 즉시 규칙표 기반 브리핑으로 폴백한다.
"""
import json
import os
import urllib.error
import urllib.request

SYSTEM_PROMPT = (
    "너는 LoL 프로팀 감독이다. 반드시 [분석 JSON]에 있는 승률(%), 티어 위치, "
    "3분 목표, 추천 아이템 이름, 상대 위협/운영 팁만 근거로 한국어 2문장 이내의 "
    "실전 운영 코칭을 작성하라. JSON에 없는 아이템이나 수치를 절대 지어내지 마라."
)


def fallback_briefing(coach_result: dict) -> str:
    """LLM 미설정 또는 장애 시 ONNX + 규칙표 기반 2문장 브리핑."""
    pct = round(coach_result["my_win_prob"] * 100)
    b = coach_result["build_advice"]
    tip = b.get("tactical_tip") or b["posture"]
    goal = coach_result["coaching"][0]["message"] if coach_result.get("coaching") else "현재 성장 페이스 유지"
    items = " -> ".join(it["name"] for it in b["recommended_items"])
    return f"현재 승률 {pct}%({b['state']})입니다. {tip} 핵심 목표는 '{goal}'이며, 권장 빌드는 [{items}] 순입니다."


def build_prompt_payload(coach_result: dict) -> dict:
    b = coach_result["build_advice"]
    return {
        "win_prob_pct": round(coach_result["my_win_prob"] * 100, 1),
        "state": b["state"],
        "my_champion": b.get("my_champion"),
        "enemy_top_threat": b.get("enemy_top_threat"),
        "threat_type": b.get("threat_type"),
        "tactical_tip": b.get("tactical_tip") or b.get("posture"),
        "top_goal": coach_result["coaching"][0]["message"] if coach_result.get("coaching") else None,
        "recommended_items": [it["name"] for it in b["recommended_items"]],
        "skill_order": b["skill_order"].get("order"),
    }


def generate_briefing(coach_result: dict) -> dict:
    """환경변수(LLM_API_URL, LLM_API_KEY, LLM_MODEL)로 경량 LLM 호출, 실패/미설정 시 규칙 폴백."""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    api_url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions").strip()
    model = os.getenv("LLM_MODEL", "qwen2.5:3b").strip()
    timeout = float(os.getenv("LLM_TIMEOUT_SEC", "3.0"))

    if not api_key and "localhost" not in api_url and "127.0.0.1" not in api_url and "host.docker.internal" not in api_url:
        return {
            "briefing": fallback_briefing(coach_result),
            "source": "fallback_rule",
            "llm_model": None,
        }

    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "max_tokens": 160,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[분석 JSON]\n{json.dumps(build_prompt_payload(coach_result), ensure_ascii=False)}"},
        ],
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(api_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data["choices"][0]["message"]["content"] or "").strip()
        if not text:
            raise ValueError("empty llm response")
        return {"briefing": text, "source": "llm", "llm_model": model}
    except Exception:
        return {
            "briefing": fallback_briefing(coach_result),
            "source": "fallback_rule",
            "llm_model": model,
        }

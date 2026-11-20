"""ONNX 분석 결과(Fact) 기반 경량 LLM 프로 감독 브리핑 모듈 (AGENTS.md 4절 POST /coach/briefing).

ONNX + 규칙표가 먼저 확정 수치(승률·요인·목표·아이템·상대 위협)를 계산한 뒤,
Qwen/Haiku 등 경량 LLM(OpenAI 호환 API)에 그 JSON만 근거로 전달해 2문장 이내 실전 운영 브리핑을 만든다.
돌아온 문장은 validate_briefing_output 으로 검사해, JSON 에 없는 숫자·아이템이나 3문장 이상이면 버린다.
API 키가 없거나 타임아웃/오류/검사 실패면 규칙표 기반 브리핑으로 답한다.
같은 상황의 반복 요청(오버레이 2초 주기)은 60초 TTL 캐시로 LLM 을 다시 부르지 않는다.
"""
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

SYSTEM_PROMPT = (
    "너는 LoL 프로팀 감독이다. 반드시 [분석 JSON]에 있는 승률(%), 티어 위치, "
    "3분 목표, 추천 아이템 이름, 상대 위협/운영 팁만 근거로 한국어 2문장 이내의 "
    "실전 운영 코칭을 작성하라. 숫자와 아이템 이름은 JSON 값 그대로 옮기고, "
    "JSON에 없는 아이템이나 수치를 절대 지어내지 마라."
)

STATE_KO = {"ahead": "우세", "even": "접전", "behind": "열세"}

BRIEFING_CACHE_TTL_SEC = 60.0
BRIEFING_CACHE_MAX = 128

# 아이템처럼 보이는 `X의 Y` 의 Y. 추상 명사(힘·도전·속삭임 등)는 넣지 않는다(오탐을 줄이려고)
ITEM_NOUNS = (
    "검", "대검", "단검", "장검", "유령검", "칼날", "도끼", "낫", "활", "석궁", "창", "지팡이", "홀", "망치",
    "방패", "갑옷", "갑주", "흉갑", "조끼", "망토", "장막", "투구", "가면", "신발", "장화", "군화",
    "반지", "보석", "수정", "펜던트", "목걸이", "부적", "인장", "모자", "죽음모자", "모래시계", "물약", "비약", "구",
)
_PARTICLES = ("으로", "부터", "까지", "에서", "이며", "이고", "처럼", "보다", "을", "를", "이", "가", "은", "는",
              "도", "만", "와", "과", "로", "에", "의", "랑")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ONE_V_ONE = re.compile(r"\d+\s*(?:대|:|vs)\s*\d+")
_OF_PHRASE = re.compile(r"([가-힣A-Za-z.]+의)\s+([가-힣]+)")
_SENT_END = re.compile(r"(?<=[.!?])\s+|\n+")


def _game_item_names() -> tuple[str, ...]:
    data = json.loads((Path(__file__).with_name("game_data.json")).read_text(encoding="utf-8"))
    return tuple(sorted({v["name"] for v in data["items"].values()}, key=len, reverse=True))


GAME_ITEM_NAMES = _game_item_names()


# ── 규칙 문장 ──
def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def fallback_briefing(coach_result: dict) -> str:
    """LLM 미설정·장애·검사 실패 때의 ONNX + 규칙표 브리핑. state 는 한국어, 문장은 마침표로 나눈다."""
    pct = round(coach_result["my_win_prob"] * 100)
    b = coach_result["build_advice"]
    tip = b.get("tactical_tip") or b["posture"]
    goal = coach_result["coaching"][0]["message"] if coach_result.get("coaching") else "현재 성장 페이스 유지"
    items = " -> ".join(it["name"] for it in b["recommended_items"])
    state = STATE_KO.get(b["state"], b["state"])
    return f"현재 승률 {pct}%({state})입니다. {_sentence(tip)} 핵심 목표는 '{goal}'이며, 권장 빌드는 [{items}] 순입니다."


def build_prompt_payload(coach_result: dict) -> dict:
    b = coach_result["build_advice"]
    return {
        "win_prob_pct": round(coach_result["my_win_prob"] * 100, 1),
        "state": STATE_KO.get(b["state"], b["state"]),
        "my_champion": b.get("my_champion"),
        "enemy_top_threat": b.get("enemy_top_threat"),
        "threat_type": b.get("threat_type"),
        "tactical_tip": b.get("tactical_tip") or b.get("posture"),
        "top_goal": coach_result["coaching"][0]["message"] if coach_result.get("coaching") else None,
        "horizon_min": coach_result.get("horizon_min"),
        "recommended_items": [it["name"] for it in b["recommended_items"]],
        "skill_order": b["skill_order"].get("order"),
    }


# ── 검사기 ──
def _numbers(text: str) -> list[float]:
    text = _ONE_V_ONE.sub(" ", text)
    return [float(m.group().replace(",", "")) for m in _NUM.finditer(text)]


def _allowed_numbers(payload) -> set[float]:
    found: list[float] = []

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            found.append(abs(float(v)))
        elif isinstance(v, str):
            found.extend(_numbers(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(payload)
    out = set()
    for x in found:
        out.add(round(x, 6))
        out.add(float(math.floor(x + 0.5)))  # 정수 반올림(0.5 올림)한 값도 인용으로 본다
    return out


def count_sentences(text: str) -> int:
    protected = text.replace("B.F.", "BF")
    return len([s for s in _SENT_END.split(protected.strip()) if s.strip()])


def _strip_particle(word: str) -> str:
    for p in _PARTICLES:
        if word.endswith(p) and len(word) > len(p):
            return word[: -len(p)]
    return word


def _is_item_noun(word: str) -> bool:
    for w in (word, _strip_particle(word)):
        if w in ITEM_NOUNS:
            return True
        if any(len(n) >= 2 and w.endswith(n) for n in ITEM_NOUNS):
            return True
    return False


def _item_claims(text: str) -> tuple[list[str], list[str]]:
    """(게임 데이터 아이템 이름이 그대로 나온 것, 아이템처럼 보이는 이름 후보)."""
    exact, rest = [], text
    for name in GAME_ITEM_NAMES:  # 긴 이름부터 지워 가며 찾는다(짧은 이름이 긴 이름 안에서 또 잡히지 않게)
        if name in rest:
            exact.append(name)
            rest = rest.replace(name, " ")
    like = []
    for m in _OF_PHRASE.finditer(text):
        if _is_item_noun(m.group(2)):
            like.append(f"{m.group(1)} {_strip_particle(m.group(2))}")
    for inner in re.findall(r"\[([^\]]*)\]", text):
        like.extend(x.strip() for x in re.split(r"->|→|,", inner) if x.strip())
    return exact, like


def validate_briefing_output(text: str, payload: dict) -> str | None:
    """통과하면 None, 걸리면 fallback_reason 문자열 (AGENTS.md 4절 검사 규칙, 이 순서)."""
    n = count_sentences(text)
    if n >= 3:
        return f"sentences: {n}"
    allowed = _allowed_numbers(payload)
    for x in _numbers(text):
        if round(x, 6) not in allowed:
            return f"number: {x:g}"
    names = [str(x) for x in payload.get("recommended_items") or []]
    exact, like = _item_claims(text)
    for name in exact:
        if name not in names:
            return f"item: {name}"
    for cand in like:
        if not any(cand == nm or nm in cand for nm in names):
            return f"item: {cand}"
    return None


# ── 캐시 ──
_clock = time.monotonic
_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cache_key(coach_result: dict) -> tuple:
    b = coach_result["build_advice"]
    return (b["state"], b.get("my_champion"), b.get("enemy_top_threat"),
            tuple(it["name"] for it in b["recommended_items"]), round(coach_result["my_win_prob"] * 100))


def _cache_get(key: tuple) -> dict | None:
    now = _clock()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        if now - hit[0] >= BRIEFING_CACHE_TTL_SEC:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return hit[1]


def _cache_put(key: tuple, outcome: dict) -> None:
    with _cache_lock:
        _cache[key] = (_clock(), outcome)
        _cache.move_to_end(key)
        while len(_cache) > BRIEFING_CACHE_MAX:
            _cache.popitem(last=False)


# ── LLM 호출 ──
def _call_llm(payload: dict, api_url: str, api_key: str, model: str, timeout: float) -> dict:
    """{"text": 원문 또는 None, "error": 실패 이유 또는 None}."""
    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "max_tokens": 160,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[분석 JSON]\n{json.dumps(payload, ensure_ascii=False)}"},
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
    except Exception as e:  # noqa: BLE001  어떤 실패든 규칙 문장으로 답한다
        return {"text": None, "error": f"llm_error: {type(e).__name__}"}
    return {"text": text, "error": None if text else "empty"}


def generate_briefing(coach_result: dict) -> dict:
    """환경변수(LLM_API_URL, LLM_API_KEY, LLM_MODEL)로 경량 LLM 호출, 실패/미설정/검사 실패 시 규칙 폴백."""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    api_url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions").strip()
    model = os.getenv("LLM_MODEL", "qwen2.5:3b").strip()
    timeout = float(os.getenv("LLM_TIMEOUT_SEC", "3.0"))

    def fallback(reason: str, llm_model: str | None) -> dict:
        return {"briefing": fallback_briefing(coach_result), "source": "fallback_rule",
                "llm_model": llm_model, "fallback_reason": reason}

    if not api_key and not _is_local_url(api_url):
        return fallback("not_configured", None)

    payload = build_prompt_payload(coach_result)
    key = cache_key(coach_result)
    outcome = _cache_get(key)
    if outcome is None:
        outcome = {**_call_llm(payload, api_url, api_key, model, timeout), "model": model}
        _cache_put(key, outcome)
    if outcome["error"]:
        return fallback(outcome["error"], outcome["model"])
    reason = validate_briefing_output(outcome["text"], payload)  # 캐시에서 꺼낸 문장도 지금 payload 로 다시 본다
    if reason:
        return fallback(reason, outcome["model"])
    return {"briefing": outcome["text"], "source": "llm", "llm_model": outcome["model"], "fallback_reason": None}


# ── 실시간 종합 전략 LLM 브리핑 (POST /strategy/briefing, 로컬 Ollama 포함) ──
STRATEGY_SYSTEM_PROMPT = (
    "너는 LoL 프로팀 실시간 전략 코치다. "
    "반드시 [실시간 전장 JSON]의 시간대·골드 격차·내 보유 골드·전 라인 유불리·생존 인원·오브젝트·상대 위협/조합·권장 아이템만 근거로 "
    "한국어 2문장 이내의 구체적이고 유동적인 실전 전략을 제시하라. "
    "일반론이나 10분 환산 표현은 쓰지 말고, 지금 이 순간 가장 중요한 행동과 다음 판단을 바로 말하라. "
    "JSON에 없는 수치나 아이템 이름은 절대 지어내지 마라."
)
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
STRATEGY_CACHE_TTL_SEC = 20.0
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_strategy_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_strategy_cache_lock = threading.Lock()


def clear_strategy_cache() -> None:
    with _strategy_cache_lock:
        _strategy_cache.clear()


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def build_strategy_prompt_payload(strategy_result: dict) -> dict:
    b = strategy_result["build_advice"]
    lanes_compact = [
        f"{l['lane_ko']}:{l['ally']['champion']} vs {l['enemy']['champion']}({l['status_ko']}, {l['gold_diff']:+d}G)"
        for l in strategy_result.get("lanes") or []
    ]
    return {
        "phase": strategy_result.get("phase_label"),
        "game_time_min": round(float(strategy_result.get("game_time_sec", 0.0)) / 60.0, 1),
        "win_prob_pct": round(float(strategy_result.get("win_prob", 0.5)) * 100.0, 1),
        "state": strategy_result.get("state_ko"),
        "team_gold_diff": strategy_result.get("team_gold_diff"),
        "kills": strategy_result.get("kills"),
        "alive": strategy_result.get("alive"),
        "key_factors": strategy_result.get("key_factors"),
        "lanes": lanes_compact,
        "ally_carry": strategy_result.get("ally_carry"),
        "enemy_comp": strategy_result.get("enemy_comp"),
        "objectives": strategy_result.get("objectives"),
        "recall_advice": (strategy_result.get("recall_advice") or {}).get("message"),
        "priority_strategies": [
            {"category": s["category"], "title": s["title"], "reason": s["reason"]}
            for s in (strategy_result.get("strategies") or [])[:3]
        ],
        "recommended_items": [it["name"] for it in b.get("recommended_items") or []],
        "my_champion": b.get("my_champion"),
    }


def strategy_cache_key(strategy_result: dict) -> tuple:
    b = strategy_result["build_advice"]
    lane_sig = tuple((l["position"], l["status"]) for l in strategy_result.get("lanes") or [])
    strat_sig = tuple(s["category"] for s in (strategy_result.get("strategies") or [])[:2])
    win_bucket = int(round(float(strategy_result.get("win_prob", 0.5)) * 50))  # 2% 단위
    return (
        strategy_result.get("phase"),
        strategy_result.get("state"),
        b.get("my_champion"),
        lane_sig,
        strat_sig,
        tuple(it["name"] for it in b.get("recommended_items") or []),
        win_bucket,
    )


def _strategy_cache_get(key: tuple) -> dict | None:
    now = _clock()
    with _strategy_cache_lock:
        hit = _strategy_cache.get(key)
        if hit is None:
            return None
        if now - hit[0] >= STRATEGY_CACHE_TTL_SEC:
            del _strategy_cache[key]
            return None
        _strategy_cache.move_to_end(key)
        return hit[1]


def _strategy_cache_put(key: tuple, outcome: dict) -> None:
    with _strategy_cache_lock:
        _strategy_cache[key] = (_clock(), outcome)
        _strategy_cache.move_to_end(key)
        while len(_strategy_cache) > BRIEFING_CACHE_MAX:
            _strategy_cache.popitem(last=False)


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "host.docker.internal", "::1"})


def _is_local_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


def _resolve_strategy_llm_config() -> tuple[str, str, str, float]:
    """환경변수 우선. LLM_API_URL 이 비어 있고 LLM_LOCAL_OLLAMA=on(또는 1)이면 로컬 Ollama 를 사용한다."""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    raw_url = os.getenv("LLM_API_URL", "").strip()
    local_opt = os.getenv("LLM_LOCAL_OLLAMA", "").strip().lower() in ("1", "true", "on", "yes")
    if raw_url:
        api_url = raw_url
    elif local_opt:
        api_url = DEFAULT_OLLAMA_URL
    else:
        api_url = "https://api.openai.com/v1/chat/completions"

    raw_model = os.getenv("LLM_MODEL", "").strip()
    if raw_model:
        model = raw_model
    elif "11434" in api_url:
        model = DEFAULT_OLLAMA_MODEL
    else:
        model = "qwen2.5:3b"
    timeout = float(os.getenv("LLM_STRATEGY_TIMEOUT_SEC", os.getenv("LLM_TIMEOUT_SEC", "6.0" if _is_local_url(api_url) else "3.0")))
    return api_url, api_key, model, timeout


def _call_strategy_llm(payload: dict, api_url: str, api_key: str, model: str, timeout: float) -> dict:
    body = json.dumps({
        "model": model,
        "temperature": 0.25,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": f"[실시간 전장 JSON]\n{json.dumps(payload, ensure_ascii=False)}"},
        ],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(api_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = _strip_think((data["choices"][0]["message"]["content"] or ""))
    except Exception as e:  # noqa: BLE001
        return {"text": None, "error": f"llm_error: {type(e).__name__}"}
    return {"text": text, "error": None if text else "empty"}


def _payload_strings(v) -> list[str]:
    out: list[str] = []
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, dict):
        for x in v.values():
            out.extend(_payload_strings(x))
    elif isinstance(v, (list, tuple)):
        for x in v:
            out.extend(_payload_strings(x))
    return out


def validate_strategy_briefing(text: str, payload: dict) -> str | None:
    """실시간 전략 브리핑 검증(H4): 2문장 이내(3문장 이상 거절), payload 밖 숫자 거절, 추천·payload 밖 아이템 거절."""
    if not text or not text.strip():
        return "empty"
    n = count_sentences(text)
    if n >= 3:
        return f"sentences: {n}"
    allowed_nums = _allowed_numbers(payload)
    for x in _numbers(text):
        if round(x, 6) not in allowed_nums:
            return f"number: {x:g}"
    names = [str(x) for x in payload.get("recommended_items") or []]
    joined = " ".join(_payload_strings(payload))
    for item_name in (*GAME_ITEM_NAMES, "제어 와드"):
        if item_name in joined and item_name not in names:
            names.append(item_name)
    exact, like = _item_claims(text)
    for name in exact:
        if name not in names:
            return f"item: {name}"
    for cand in like:
        if not any(cand == nm or nm in cand for nm in names) and cand not in joined:
            return f"item: {cand}"
    return None


def generate_strategy_briefing(strategy_result: dict) -> dict:
    """실시간 종합 전장 스냅샷으로 LLM 전략 브리핑 생성. 미설정·장애 시 동적 종합 전략 문장으로 폴백."""
    api_url, api_key, model, timeout = _resolve_strategy_llm_config()

    def fallback(reason: str, llm_model: str | None) -> dict:
        return {
            "briefing": strategy_result["summary_briefing"],
            "source": "dynamic_rule",
            "llm_model": llm_model,
            "fallback_reason": reason,
        }

    if not api_key and not _is_local_url(api_url):
        return fallback("not_configured", None)

    payload = build_strategy_prompt_payload(strategy_result)
    key = strategy_cache_key(strategy_result)
    outcome = _strategy_cache_get(key)
    if outcome is None:
        outcome = {**_call_strategy_llm(payload, api_url, api_key, model, timeout), "model": model}
        _strategy_cache_put(key, outcome)
    if outcome["error"]:
        return fallback(outcome["error"], outcome["model"])
    reason = validate_strategy_briefing(outcome["text"], payload)
    if reason:
        return fallback(reason, outcome["model"])
    return {
        "briefing": outcome["text"],
        "source": "llm",
        "llm_model": outcome["model"],
        "fallback_reason": None,
    }

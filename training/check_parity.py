"""원본 모델 서버와 대상 서버(ONNX 등)가 같은 답을 내는지 비교한다 (AGENTS.md 9절).

pytest 가 아니다. 서버가 떠 있어야 하므로 python -m 으로 실행한다. 표준 라이브러리만 쓴다.
  기준: PY_URL (기본 http://127.0.0.1:8080, MODEL_IMPL=sklearn 으로 띄운 원본)
  대상: TARGETS (기본 onnx=http://127.0.0.1:8083, 이름=주소 를 쉼표로)
  입력: 고정 3건 + 시드 0 무작위 20건 = 23건
  판정: /predict 의 win_prob_blue, top_factors[].impact_pp/100, /coach 의 coaching[].win_prob_after
        최대 오차 < 1e-4, 요인·목표의 종류와 순서 일치, 대상 /health 의 네 필드 존재. 하나라도 어긋나면 exit 1
"""
import json
import os
import random
import sys
import urllib.request

TOLERANCE = 1e-4
PY_URL = os.getenv("PY_URL", "http://127.0.0.1:8080")
TARGETS = dict(t.split("=", 1) for t in os.getenv("TARGETS", "onnx=http://127.0.0.1:8083").split(","))
HEALTH_KEYS = {"status", "model", "app_version", "model_version"}


def team(kills, assists, cs, lvl, dr, he, to):
    return {"kills": kills, "assists": assists, "cs": cs, "avg_level": lvl, "dragons": dr, "heralds": he,
            "towers": to}


def cases() -> list[dict]:
    fixed = [
        {"blue": team(6, 7, 268, 6.9, 0, 0, 0), "red": team(6, 7, 268, 6.9, 0, 0, 0),
         "first_blood": "blue", "my_side": "blue"},                     # 균형
        {"blue": team(10, 12, 290, 7.4, 1, 1, 1), "red": team(4, 5, 250, 6.6, 0, 0, 0),
         "first_blood": "blue", "my_side": "blue"},                     # 블루 우세
        {"blue": team(3, 4, 240, 6.6, 0, 0, 0), "red": team(9, 11, 280, 7.2, 1, 0, 1),
         "first_blood": "red", "my_side": "red"},                       # 레드 우세, 레드 기준
    ]
    rng = random.Random(0)
    rand = []
    for _ in range(20):
        sides = {}
        for side in ("blue", "red"):
            k = rng.randint(0, 15)
            sides[side] = team(k, rng.randint(0, 20), rng.randint(180, 330), round(rng.uniform(6.0, 8.0), 1),
                               rng.randint(0, 1), rng.randint(0, 1), rng.randint(0, 2))
        rand.append({**sides, "first_blood": rng.choice(["blue", "red"]), "my_side": rng.choice(["blue", "red"])})
    return fixed + rand


def call(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def numbers(pred: dict, coach: dict) -> tuple[list[str], list[float]]:
    """비교할 키 순서(종류·순서 확인용)와 값."""
    keys = ["win_prob_blue"] + [f"factor:{f['feature']}" for f in pred["top_factors"]] + \
           [f"target:{t['stat']}" for t in coach["coaching"]]
    vals = [pred["win_prob_blue"]] + [f["impact_pp"] / 100 for f in pred["top_factors"]] + \
           [t["win_prob_after"] for t in coach["coaching"]]
    return keys, vals


def main() -> int:
    data = cases()
    ref = [numbers(call(PY_URL, "/predict", c), call(PY_URL, "/coach", c)) for c in data]
    ok = True
    for name, url in TARGETS.items():
        h = call(url, "/health")
        missing = HEALTH_KEYS - set(h)
        worst, mismatched = 0.0, 0
        for c, (rk, rv) in zip(data, ref):
            tk, tv = numbers(call(url, "/predict", c), call(url, "/coach", c))
            if tk != rk:
                mismatched += 1
                continue
            worst = max(worst, max(abs(a - b) for a, b in zip(rv, tv)))
        passed = not missing and mismatched == 0 and worst < TOLERANCE
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} [{name}] {len(data)}건 최대 오차 {worst:.2e} (기준 {TOLERANCE:g}), "
              f"종류·순서 불일치 {mismatched}건, /health 빠진 필드 {sorted(missing) or '없음'}, "
              f"model_version={h.get('model_version')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

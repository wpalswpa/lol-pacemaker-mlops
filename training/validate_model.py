"""성능 게이트. 실패(exit 1)하면 CI 의 다음 단계(ONNX·승격)가 진행되지 않는다. 기준은 AGENTS.md 9절.

통과 조건 (고정 평가셋, 같은 시험지)
  1) Brier   < 규칙 기준선의 Brier     (확률이 실제 빈도와 더 맞는가: 이 서비스는 확률을 숫자로 보여 준다)
  2) LogLoss < 규칙 기준선의 LogLoss   (자신 있게 틀리는 답을 더 적게 내는가)
  3) 정확도  ≥ MIN_ACCURACY            (규칙과 정확도가 비슷하므로 고장 난 모델만 막는 하한)
  uv run python -m training.validate_model sklearn
"""
import json
import os
import sys

MIN_ACCURACY = 0.68

name = sys.argv[1]
m = json.load(open(os.path.join(os.getenv("ARTIFACTS", "artifacts"), f"metrics_{name}.json"), encoding="utf-8"))
b = m["baseline"]
checks = {
    f"Brier 가 규칙보다 낮은가 ({m['brier']:.4f} < {b['brier']:.4f})": m["brier"] < b["brier"],
    f"LogLoss 가 규칙보다 낮은가 ({m['logloss']:.4f} < {b['logloss']:.4f})": m["logloss"] < b["logloss"],
    f"정확도 하한 ({m['accuracy']:.4f} ≥ {MIN_ACCURACY}, 규칙 {b['accuracy']:.4f})": m["accuracy"] >= MIN_ACCURACY,
}
print(f"[{name}] 데이터 n={m['data']['n']} seed={m['data']['seed']}, 평가셋 {m['holdout_n']}건")
for desc, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + f"[{name}] {desc}")
sys.exit(0 if all(checks.values()) else 1)

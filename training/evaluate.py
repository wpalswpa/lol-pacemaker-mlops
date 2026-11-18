"""평가 지표. 학습 스크립트와 게이트가 같은 계산을 쓴다."""
import json
import os

import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from api.predictors.rule import predict_proba as rule_proba

ARTIFACTS = os.getenv("ARTIFACTS", "artifacts")


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-15, 1 - 1e-15)
    return {
        "accuracy": float(accuracy_score(y, p >= 0.5)),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
    }


def rule_metrics(X: np.ndarray, y: np.ndarray) -> dict:
    """같은 평가셋에서 규칙 기준선의 지표 (게이트의 비교 대상)."""
    return metrics(y, rule_proba(X))


def write_json(name: str, obj: dict) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    path = os.path.join(ARTIFACTS, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return path

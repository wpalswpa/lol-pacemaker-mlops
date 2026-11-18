"""규칙 기준선: 사람이 정한 골드 환산 규칙. 학습하지 않는다 (AGENTS.md 5절).

학습 모델은 이 규칙보다 확률을 정확하게 낼 때만 배포된다 (validate_model.py).
"""
import numpy as np

from api.contract import FEATURES

# 차이 1단위를 골드로 친 값. 게임 지식으로 정한 값이다.
GOLD_PER = {
    "kills": 300.0,    # 킬 기본 현상금
    "cs": 20.0,        # 초반 CS 1개 평균 골드
    "towers": 250.0,   # 포탑 1개 전역 골드
    "dragons": 300.0,  # 드래곤 1마리를 골드로 친 값
}
# 접전 근처에서 1,000골드당 승률 약 +10%p: 시그모이드 0 근처 기울기 1 / (4 × 2500)
SCALE = 2500.0

_COLS = [(FEATURES.index(f), w) for f, w in GOLD_PER.items()]


def predict_proba(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    gold = np.zeros(len(X))
    for i, w in _COLS:
        gold += w * X[:, i]
    return 1.0 / (1.0 + np.exp(-gold / SCALE))


def load(model_dir: str):
    """규칙은 모델 파일이 없다. model_dir 은 쓰지 않는다."""
    return predict_proba

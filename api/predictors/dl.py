"""PyTorch 모델. 티어 평균·표준편차를 버퍼로 모델 안에 넣는다 (WithNormalize).

딥러닝에는 make_pipeline 같은 장치가 없어서, 정규화를 모델의 일부로 만들어
state_dict 와 ONNX 파일에 함께 저장한다. 서빙 쪽은 정규화를 따로 구현하지 않는다.
"""
import os
from collections.abc import Sequence

import numpy as np
import torch
from torch import nn

from api.contract import FEATURES

N_FEATURES = len(FEATURES)
HIDDEN = 16


class WithNormalize(nn.Module):
    """원래 단위 입력 → (x − mean) / std → 내부 모델."""

    mean: torch.Tensor
    std: torch.Tensor

    def __init__(self, model: nn.Module, mean: Sequence[float], std: Sequence[float]):
        super().__init__()
        self.model = model
        self.register_buffer("mean", torch.as_tensor(mean, dtype=torch.float32))
        self.register_buffer("std", torch.as_tensor(std, dtype=torch.float32))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model((x - self.mean) / self.std)


def build_model(mean: Sequence[float] | None = None, std: Sequence[float] | None = None) -> WithNormalize:
    """[N, 8] 원래 단위 → [N, 1] 블루 승리 확률. mean/std 를 주지 않으면 불러오기용 빈 껍데기."""
    inner = nn.Sequential(nn.Linear(N_FEATURES, HIDDEN), nn.ReLU(), nn.Linear(HIDDEN, 1), nn.Sigmoid())
    return WithNormalize(inner, mean or [0.0] * N_FEATURES, std or [1.0] * N_FEATURES)


def load(model_dir: str):
    model = build_model()
    model.load_state_dict(torch.load(os.path.join(model_dir, "dl.pt"), weights_only=True))
    model.eval()

    def predict_proba(X: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            return model(torch.as_tensor(np.asarray(X, dtype=np.float32))).squeeze(1).numpy()

    return predict_proba

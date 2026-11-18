"""ONNX Runtime 서빙. scikit-learn·PyTorch 없이 돈다. 정규화는 모델 안에 있다.

입력 텐서 이름은 "X" (float32 [N, 8]). 출력은 파일마다 고정한 이름을 쓴다.
  model_sklearn.onnx → "probabilities" [N, 2] (1번 열이 블루 승)
  model_dl.onnx      → "win_prob" [N, 1]
"""
import os

import numpy as np
import onnxruntime as ort

OUTPUTS = {"probabilities": 1, "win_prob": 0}  # 출력 이름 → 블루 승리 확률 열


def load(model_dir: str):
    path = os.path.join(model_dir, os.getenv("ONNX_FILE", "model_sklearn.onnx"))
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    names = [o.name for o in sess.get_outputs()]
    out = next((n for n in OUTPUTS if n in names), None)
    if out is None:
        raise RuntimeError(f"알 수 없는 ONNX 출력 {names}. 기대: {list(OUTPUTS)}")
    col = OUTPUTS[out]

    def predict_proba(X: np.ndarray) -> np.ndarray:
        return sess.run([out], {"X": np.asarray(X, dtype=np.float32)})[0][:, col]

    return predict_proba

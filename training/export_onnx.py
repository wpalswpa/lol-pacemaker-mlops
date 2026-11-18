"""ONNX 변환 + 원본 대비 오차 확인 (AGENTS.md 9절: 평가셋 앞 200건, 최대 오차 < 1e-4).

텐서 이름을 고정한다. 서빙(api/predictors/onnx.py)과 다른 언어 서버가 이 이름을 쓴다.
  model_sklearn.onnx : 입력 "X" float32 [N, 8] → "probabilities" [N, 2]  (make_pipeline 이라 정규화 포함)
  model_dl.onnx      : 입력 "X" float32 [N, 8] → "win_prob" [N, 1]       (WithNormalize 라 정규화 포함)
  uv run --group train python -m training.export_onnx
"""
import logging
import os
import sys

import joblib
import numpy as np
import onnxruntime as ort
import torch
from skl2onnx import to_onnx

from api.contract import FEATURES
from api.predictors.dl import build_model
from training.data import holdout
from training.evaluate import ARTIFACTS

TOLERANCE = 1e-4
probe = holdout()[0][:200].astype(np.float32)

# scikit-learn → ONNX. zipmap 을 끄면 확률이 사전 목록이 아니라 [N, 2] 텐서로 나온다
# joblib 은 우리 학습이 방금 만든 파일만 연다 (AGENTS.md 1절)
sk_model = joblib.load(os.path.join(ARTIFACTS, "sklearn.joblib"))
onx = to_onnx(sk_model, probe[:1], options={"zipmap": False}, target_opset=17)
with open(os.path.join(ARTIFACTS, "model_sklearn.onnx"), "wb") as f:
    f.write(onx.SerializeToString())

# PyTorch → ONNX
logging.getLogger("torch.onnx").setLevel(logging.ERROR)
dl_model = build_model()
dl_model.load_state_dict(torch.load(os.path.join(ARTIFACTS, "dl.pt"), weights_only=True))
dl_model.eval()
torch.onnx.export(dl_model, (torch.zeros(1, len(FEATURES)),), os.path.join(ARTIFACTS, "model_dl.onnx"),
                  input_names=["X"], output_names=["win_prob"],
                  dynamic_shapes=({0: torch.export.Dim("batch")},), external_data=False, verbose=False)

# 검증: 원본 vs ONNX (블루 승리 확률)
with torch.no_grad():
    originals = {
        "model_sklearn.onnx": ("probabilities", 1, sk_model.predict_proba(probe)[:, 1]),
        "model_dl.onnx": ("win_prob", 0, dl_model(torch.as_tensor(probe)).squeeze(1).numpy()),
    }
ok = True
for fname, (out, col, want) in originals.items():
    sess = ort.InferenceSession(os.path.join(ARTIFACTS, fname), providers=["CPUExecutionProvider"])
    assert [i.name for i in sess.get_inputs()] == ["X"], sess.get_inputs()
    got = np.asarray(sess.run([out], {"X": probe})[0])[:, col]
    diff = float(np.abs(got - want).max())
    ok &= diff < TOLERANCE
    print(f"{fname}: 입력 X → {out}, {len(probe)}건 원본 vs ONNX 최대 오차 {diff:.2e}",
          "OK" if diff < TOLERANCE else "FAIL")
sys.exit(0 if ok else 1)

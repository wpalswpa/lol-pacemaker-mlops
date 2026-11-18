"""PyTorch 학습: WithNormalize(8 → 16 → 1) 신경망.

평균·표준편차를 학습 표본에서 계산해 버퍼로 모델 안에 넣는다. 서빙은 정규화를 따로 하지 않는다.
학습 표본 안에서 20% 를 검증용으로 떼어 조기 종료한다. 평가셋(시험지)은 반복 횟수를 고르는 데 쓰지 않는다.
산출물: artifacts/dl.pt, metrics_dl.json
  DATA_N=500 DATA_SEED=0 uv run --group train python -m training.train_dl
"""
import copy
import json
import os

import numpy as np
import torch
from torch import nn

from api.predictors.dl import build_model
from training.data import DATA_N, DATA_SEED, collect_data, holdout
from training.evaluate import ARTIFACTS, metrics, rule_metrics, write_json

PARAMS = {"max_epochs": 2000, "patience": 50, "lr": 0.01, "weight_decay": 1e-3, "hidden": 16, "val_frac": 0.2}

X, y, _ = collect_data()
Xt, yt = holdout()

torch.manual_seed(DATA_SEED)
rng = np.random.default_rng(DATA_SEED)
order = rng.permutation(len(X))
n_val = max(1, int(len(X) * PARAMS["val_frac"]))
val, tr = order[:n_val], order[n_val:]

mean = X[tr].mean(axis=0)
std = X[tr].std(axis=0)
std = np.where(std > 0, std, 1.0)  # 표본에 변화가 없는 열은 나누지 않는다
model = build_model(mean.tolist(), std.tolist())

opt = torch.optim.Adam(model.parameters(), lr=PARAMS["lr"], weight_decay=PARAMS["weight_decay"])
loss_fn = nn.BCELoss()
xt_, yt_ = torch.as_tensor(X[tr]), torch.as_tensor(y[tr], dtype=torch.float32).unsqueeze(1)
xv_, yv_ = torch.as_tensor(X[val]), torch.as_tensor(y[val], dtype=torch.float32).unsqueeze(1)

best, best_state, best_epoch, waited = float("inf"), copy.deepcopy(model.state_dict()), 0, 0
for epoch in range(1, PARAMS["max_epochs"] + 1):
    model.train()
    opt.zero_grad()
    loss_fn(model(xt_), yt_).backward()
    opt.step()
    model.eval()
    with torch.no_grad():
        v = float(loss_fn(model(xv_), yv_))
    if v < best - 1e-5:
        best, best_state, best_epoch, waited = v, copy.deepcopy(model.state_dict()), epoch, 0
    else:
        waited += 1
        if waited >= PARAMS["patience"]:
            break
model.load_state_dict(best_state)
model.eval()

os.makedirs(ARTIFACTS, exist_ok=True)
torch.save(model.state_dict(), os.path.join(ARTIFACTS, "dl.pt"))
with torch.no_grad():
    p = model(torch.as_tensor(Xt)).squeeze(1).numpy()
m = {
    "model": "dl",
    "data": {"n": DATA_N, "seed": DATA_SEED},
    "params": PARAMS,
    "best_epoch": best_epoch,
    "val_loss": best,
    "holdout_n": int(len(yt)),
    **metrics(yt, p),
    "baseline": rule_metrics(Xt, yt),
}
write_json("metrics_dl.json", m)
print(json.dumps(m, indent=2, ensure_ascii=False))

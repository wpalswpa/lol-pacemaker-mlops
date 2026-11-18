"""scikit-learn 학습: StandardScaler + LogisticRegression.

make_pipeline 이라 정규화(티어 평균·표준편차)가 모델 안에 묶여 저장된다.
산출물: artifacts/sklearn.joblib, metrics_sklearn.json, baseline.json(기준선·분위수·연쇄 기울기)
  DATA_N=500 DATA_SEED=0 uv run --group train python -m training.train_sklearn
"""
import json
import os

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from training.data import DATA_N, DATA_SEED, collect_data, compute_baseline, holdout
from training.evaluate import ARTIFACTS, metrics, rule_metrics, write_json

X, y, rows = collect_data()
Xt, yt = holdout()

pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
pipe.fit(X, y)
os.makedirs(ARTIFACTS, exist_ok=True)
joblib.dump(pipe, os.path.join(ARTIFACTS, "sklearn.joblib"))

m = {
    "model": "sklearn",
    "data": {"n": DATA_N, "seed": DATA_SEED},
    "holdout_n": int(len(yt)),
    **metrics(yt, pipe.predict_proba(Xt)[:, 1]),
    "baseline": rule_metrics(Xt, yt),
}
write_json("metrics_sklearn.json", m)
write_json("baseline.json", compute_baseline(rows, lambda A: pipe.predict_proba(A)[:, 1]))
print(json.dumps(m, indent=2, ensure_ascii=False))

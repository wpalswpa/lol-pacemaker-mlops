"""모델 판의 카드(model.json)를 쓴다. 서버의 /health 가 model_version 을 여기서 읽는다.
  MODEL_VERSION=m20261118-1 uv run python -m training.model_card
"""
import datetime
import json
import os
import re
import sys

from training.evaluate import ARTIFACTS, write_json

VERSION_RE = r"^m[0-9]{8}-[0-9]+$"  # server.sh·rollback.yml 과 같은 형식

ver = os.environ.get("MODEL_VERSION", "")
if not re.match(VERSION_RE, ver):
    sys.exit(f"MODEL_VERSION 형식이 아니다(예: m20261118-1): {ver!r}")


def _load(name: str) -> dict:
    with open(os.path.join(ARTIFACTS, name), encoding="utf-8") as f:
        return json.load(f)


sk, dl = _load("metrics_sklearn.json"), _load("metrics_dl.json")
keys = ("accuracy", "brier", "logloss")
card = {
    "model_version": ver,
    "served": "model_sklearn.onnx",
    "git_sha": os.getenv("GITHUB_SHA", "local"),
    "run_url": (f'{os.getenv("GITHUB_SERVER_URL")}/{os.getenv("GITHUB_REPOSITORY")}/actions/runs/'
                f'{os.getenv("GITHUB_RUN_ID")}') if os.getenv("GITHUB_RUN_ID") else "local",
    "data": sk["data"],
    "holdout_n": sk["holdout_n"],
    "metrics": {"sklearn": {k: sk[k] for k in keys}, "dl": {k: dl[k] for k in keys}, "rule": sk["baseline"]},
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}
write_json("model.json", card)
print(json.dumps(card, indent=2, ensure_ascii=False))

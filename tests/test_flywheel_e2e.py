"""데이터 플라이휠 E2E (AGENTS.md 3-5절): 저장 → 내보내기 → 학습 입력.

브리지(GameEnd)와 웹이 POST /matches 로 저장한 기록을 GET /matches/export?format=csv 로 받아 임시 파일에 쓰고,
training/data.py 가 원본 CSV 에 쓰는 변환(table_to_xy)으로 읽어 학습 계약(X float32 [N, 8], FEATURES 순서,
블루 − 레드, y = 블루 승 0·1)과 맞는지 본다. 이 부분은 numpy 만 써서 CI 에서도 돈다.
학습 라이브러리가 있으면 같은 파일을 _load() 로 읽고 학습과 같은 파이프라인으로 한 번 학습까지 해 본다.
기대값은 아래 표에서 손으로 계산했다(코드 출력을 옮기지 않았다).
"""
import copy
import csv
import json
import os

import numpy as np
import pytest

from api.tests.conftest import app_module as app_module  # noqa: F401
from api.tests.conftest import client as client  # noqa: F401
from training import data

A = ({"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0},
     {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0})
B = ({"kills": 10, "assists": 12, "cs": 300, "avg_level": 7.4, "dragons": 1, "heralds": 1, "towers": 1},
     {"kills": 4, "assists": 5, "cs": 260, "avg_level": 6.6, "dragons": 0, "heralds": 0, "towers": 0})
DIFF_A = [-2, -3, -18, -0.2, -1, 0, 0]       # 블루 − 레드: 킬, 어시스트, CS, 평균 레벨, 드래곤, 전령, 포탑
DIFF_B = [6, 7, 40, 0.8, 1, 1, 1]

# (팀 값, 첫 킬, 내 팀, 실제 승패(내 팀 기준), 게임 시각) → 기대 X 한 줄, blueWins
WEB = [
    (A, "red", "blue", True, 600, [0, *DIFF_A], 1),      # 1 내 팀 블루 승 → 블루 승
    (B, "blue", "blue", True, 600, [1, *DIFF_B], 1),     # 2
    (B, "blue", "red", False, 600, [1, *DIFF_B], 1),     # 3 내 팀 레드 패 → 블루 승
    (A, "red", "red", True, 600, [0, *DIFF_A], 0),       # 4 내 팀 레드 승 → 블루 패
    (B, "red", "blue", False, 600, [0, *DIFF_B], 0),     # 5
    (A, "red", "red", None, 600, None, None),            # 6 승패 모름 → 내보내지 않음
    (A, "blue", "blue", False, 1390, [1, *DIFF_A], 0),   # 7 오버레이 23분 기록(환산값이라고 가정)
]
# 8 브리지: 고정 자료(10:00, 블루 5/6/250/6.8/0/1/0, 레드 7/9/268/7.0/1/0/1, 첫 킬 레드, 내 팀 블루) + GameEnd Win
BRIDGE_X = [0, -2, -3, -18, -0.2, -1, 1, -1]
FIX = os.path.join(os.path.dirname(__file__), "..", "client", "tests", "fixtures", "allgamedata_10min.json")


@pytest.fixture
def flywheel(client, tmp_path):  # noqa: F811
    from api import db
    from client.bridge import OverlayBridge

    db.init_db("sqlite://")
    for (blue, red), fb, side, win, gt, _, _ in WEB:   # 웹 전적 저장 버튼과 같은 본문
        body = {"blue": blue, "red": red, "first_blood": fb, "my_side": side, "win": win, "game_time_sec": gt}
        assert client.post("/matches", json=body).status_code == 201

    game = json.load(open(FIX, encoding="utf-8"))
    game["gameData"]["gameTime"] = 600.0
    game.setdefault("events", {}).setdefault("Events", []).append(
        {"EventID": 99, "EventName": "GameEnd", "EventTime": 600.0, "Result": "Win"})

    def post(path):
        def call(body):
            res = client.post(path, json=body)
            assert res.status_code in (200, 201), res.text
            return res.json()
        return call
    bridge = OverlayBridge(lambda: copy.deepcopy(game), post("/coach"), save=post("/matches"))
    st = bridge.step()                                   # --overlay --save-match 의 한 번
    assert st["saved_match"]["id"] == 8 and st["saved_match"]["win"] is True

    res = client.get("/matches/export?format=csv")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    path = tmp_path / "matches_export.csv"
    path.write_text(res.text, encoding="utf-8")
    return path, client


def _columns(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {k: np.array([float(r[k]) for r in rows]) for k in rows[0]}


DECIDED = [w for w in WEB if w[3] is not None]
WANT_X = [w[5] for w in DECIDED] + [BRIDGE_X]
WANT_Y = [w[6] for w in DECIDED] + [1]


def test_export_csv_meets_training_contract(flywheel):
    path, client = flywheel  # noqa: F811
    col = _columns(path)
    assert col["matchId"].tolist() == [1, 2, 3, 4, 5, 7, 8]          # 6(승패 모름)은 빠진다, id 순
    assert col["gameTimeSec"].tolist() == [600, 600, 600, 600, 600, 1390, 600]
    X, y, teams = data.table_to_xy(col)                                # 원본 CSV 와 같은 변환
    assert X.dtype == np.float32 and X.shape == (7, len(data.FEATURES)) == (7, 8)
    assert y.dtype == np.int64 and y.tolist() == WANT_Y
    np.testing.assert_allclose(X, np.array(WANT_X, dtype=np.float32), atol=1e-6)
    assert teams["blue"][0].tolist() == [5, 6, 250, 6.8, 0, 0, 0]      # 팀 원값도 그대로 (기준선 계산에 쓰임)


def test_export_matches_what_server_stored_and_serves(flywheel):
    """학습에 들어갈 X 가 서버가 저장한 피처, 서빙이 같은 입력에 쓰는 to_features 와 같다 (학습·서빙 같은 정의)."""
    from api.contract import GameState, to_features

    path, client = flywheel  # noqa: F811
    X, _, _ = data.table_to_xy(_columns(path))
    stored = {m["id"]: m for m in client.get("/matches?limit=100").json()["matches"]}
    assert len(stored) == 8
    for row, mid in zip(X, [1, 2, 3, 4, 5, 7, 8]):
        f = stored[mid]["features"]
        np.testing.assert_allclose(row, f["values"], atol=1e-4)
        state = GameState(blue=f["blue"], red=f["red"], first_blood=f["first_blood"], my_side=f["my_side"])
        np.testing.assert_allclose(row, to_features(state), atol=1e-6)
    assert stored[8]["my_champion"] == "Ahri" and stored[8]["win"] is True   # 브리지가 올린 기록


def test_training_loader_reads_export(flywheel, monkeypatch):
    """학습 라이브러리가 있으면 실제 로더(_load: 읽기 + 고정 평가셋 분할)와 학습 파이프라인까지."""
    pytest.importorskip("pandas")
    pytest.importorskip("sklearn")
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    path, _ = flywheel
    monkeypatch.setattr(data, "DATA_PATH", str(path))
    monkeypatch.setattr(data, "_cache", {})
    d = data._load()
    assert d["X"].shape == (7, 8) and d["y"].tolist() == WANT_Y
    np.testing.assert_allclose(d["X"], np.array(WANT_X, dtype=np.float32), atol=1e-6)
    assert sorted([*d["pool"], *d["test"]]) == list(range(7))           # 학습 풀 + 평가셋 = 전부
    pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(d["X"], d["y"])
    p = pipe.predict_proba(d["X"])[:, 1]
    assert p.shape == (7,) and np.all((p > 0) & (p < 1))

"""학습 데이터: CSV 읽기, 고정 평가셋, DATA_N·DATA_SEED 표본, 기준선·분위수 계산.

- 고정 평가셋: 전체의 20% 를 random_state=42 층화 추출로 한 번 나눈다. 판이 바뀌어도 같은 시험지다.
- 학습 표본: 나머지 80%(학습 풀)에서 DATA_N 건을 DATA_SEED 로 뽑는다. 패치가 바뀐 상황을 흉내 내는 장치다.
- 피처 순서·변환은 api/contract.py 의 FEATURES 를 그대로 쓴다 (서빙과 같은 정의).
- 열 → (X, y) 변환은 table_to_xy 한 곳이다. 원본 CSV 와 전적 DB 내보내기(GET /matches/export)가 같은 함수를 지난다.
  이 함수는 numpy 만 쓰므로 학습 라이브러리가 없는 CI 테스트에서도 그대로 검사할 수 있다.
"""
import os
from collections.abc import Callable, Mapping

import numpy as np

from api.contract import FEATURES, TEAM_STATS

DATA_PATH = os.getenv("DATA_PATH", "data/high_diamond_ranked_10min.csv")
DATA_N = int(os.getenv("DATA_N", "500"))
DATA_SEED = int(os.getenv("DATA_SEED", "0"))
HOLDOUT_SEED = 42
HOLDOUT_FRAC = 0.2
TIER = "다이아몬드 솔로랭크 10분 (Kaggle 스냅샷)"
PS = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]

# 팀 지표 → CSV 열 이름 뒷부분 (앞에 blue/red 가 붙는다). CS 는 점수판처럼 미니언 + 정글 몬스터
CSV_COLUMNS = {
    "kills": ["Kills"],
    "assists": ["Assists"],
    "cs": ["TotalMinionsKilled", "TotalJungleMinionsKilled"],
    "avg_level": ["AvgLevel"],
    "dragons": ["Dragons"],
    "heralds": ["Heralds"],
    "towers": ["TowersDestroyed"],
}

_cache: dict = {}


def table_to_xy(col: Mapping) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """열 이름 → 값 배열(DataFrame 도 된다)을 모델 입력 X float32 [N, 8], 정답 y int64 [N] (블루 승 1), 팀 원값으로."""
    teams = {}
    for side in ("blue", "red"):
        teams[side] = np.column_stack(
            [sum(np.asarray(col[side + c], dtype=np.float64) for c in CSV_COLUMNS[s]) for s in TEAM_STATS])
    first_blood = np.asarray(col["blueFirstBlood"], dtype=np.float64)
    X = np.column_stack([first_blood, teams["blue"] - teams["red"]]).astype(np.float32)
    assert X.shape[1] == len(FEATURES)
    y = np.asarray(col["blueWins"], dtype=np.int64)
    return X, y, teams


def _load() -> dict:
    """CSV 를 한 번 읽어 팀 값·피처·정답·분할을 만든다."""
    if _cache:
        return _cache
    import pandas as pd  # 학습 그룹. 이 모듈을 가져오기만 할 때는 필요 없다
    from sklearn.model_selection import train_test_split

    X, y, teams = table_to_xy(pd.read_csv(DATA_PATH))
    pool, test = train_test_split(np.arange(len(y)), test_size=HOLDOUT_FRAC, stratify=y,
                                  random_state=HOLDOUT_SEED)
    _cache.update(X=X, y=y, blue=teams["blue"], red=teams["red"], pool=np.sort(pool), test=np.sort(test))
    return _cache


def collect_data(n: int = DATA_N, seed: int = DATA_SEED) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """학습 풀에서 n 건을 seed 로 뽑는다. (X, y, 고른 행 번호)"""
    d = _load()
    if not 0 < n <= len(d["pool"]):
        raise ValueError(f"DATA_N 은 1~{len(d['pool'])} 이어야 한다: {n}")
    rows = np.sort(np.random.default_rng(seed).choice(d["pool"], size=n, replace=False))
    return d["X"][rows], d["y"][rows], rows


def holdout() -> tuple[np.ndarray, np.ndarray]:
    """고정 평가셋 (X, y)."""
    d = _load()
    return d["X"][d["test"]], d["y"][d["test"]]


def _q(v: np.ndarray) -> list[float]:
    return [float(x) for x in np.percentile(v, PS)]


def compute_baseline(rows: np.ndarray, predict_proba: Callable[[np.ndarray], np.ndarray]) -> dict:
    """학습 표본으로 티어 기준선을 만든다 (AGENTS.md 4절). 서빙은 원본 CSV 없이 이것만 쓴다."""
    d = _load()
    X = d["X"][rows].astype(np.float64)
    team = np.vstack([d["blue"][rows], d["red"][rows]])  # 블루·레드를 합친 팀당 값

    quantiles: dict = {"ps": PS}
    for i, s in enumerate(TEAM_STATS, start=1):
        quantiles[s] = _q(np.concatenate([X[:, i], -X[:, i]]))  # 한 팀 기준 격차: 두 관점을 합친다
    p = np.asarray(predict_proba(X.astype(np.float32)), dtype=np.float64)
    quantiles["win_prob"] = _q(np.concatenate([p, 1 - p]))

    slope: dict = {}
    for i, s in enumerate(TEAM_STATS, start=1):
        var = float(np.var(X[:, i]))
        slope[s] = {}
        for j, f in enumerate(TEAM_STATS, start=1):
            if f != s:
                cov = float(np.mean((X[:, i] - X[:, i].mean()) * (X[:, j] - X[:, j].mean())))
                slope[s][f] = cov / var if var > 0 else 0.0

    return {
        "tier": TIER,
        "data": {"n": int(len(rows)), "seed": DATA_SEED},
        "feature_mean": {f: float(X[:, i].mean()) for i, f in enumerate(FEATURES)},
        "feature_std": {f: float(X[:, i].std()) for i, f in enumerate(FEATURES)},
        "feature_min": {f: float(X[:, i].min()) for i, f in enumerate(FEATURES)},
        "feature_max": {f: float(X[:, i].max()) for i, f in enumerate(FEATURES)},
        "team_mean": {s: float(team[:, i].mean()) for i, s in enumerate(TEAM_STATS)},
        "team_min": {s: float(team[:, i].min()) for i, s in enumerate(TEAM_STATS)},
        "team_max": {s: float(team[:, i].max()) for i, s in enumerate(TEAM_STATS)},
        "quantiles": quantiles,
        "slope": slope,
    }

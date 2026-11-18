"""training/compare_models.py 계산 검사 (AGENTS.md 9절 "모델 판 비교"). 학습 라이브러리 없이 돈다. 기대값은 손으로 계산했다."""
import pathlib
import re

import pytest

from api.contract import FEATURES
from training import compare_models as cm

RULE = {"accuracy": 0.715, "brier": 0.20, "logloss": 0.56, "auc": 0.79}


def test_min_accuracy_matches_gate():
    src = (pathlib.Path(__file__).resolve().parents[1] / "training" / "validate_model.py").read_text(encoding="utf-8")
    assert float(re.search(r"^MIN_ACCURACY = ([0-9.]+)", src, re.M).group(1)) == cm.MIN_ACCURACY


@pytest.mark.parametrize("model, rule, want", [
    (0.15, 0.20, 25.0),     # (0.20 − 0.15) / 0.20
    (0.25, 0.20, -25.0),    # 규칙보다 나쁘면 음수
    (0.56, 0.56, 0.0),
])
def test_improvement_pct(model, rule, want):
    assert cm.improvement_pct(model, rule) == pytest.approx(want)


@pytest.mark.parametrize("m, want", [
    ({"brier": 0.18, "logloss": 0.55, "accuracy": 0.70}, (True, True, True, True)),
    ({"brier": 0.18, "logloss": 0.55, "accuracy": 0.68}, (True, True, True, True)),     # 정확도는 이상(≥)
    ({"brier": 0.20, "logloss": 0.55, "accuracy": 0.70}, (False, True, True, False)),   # Brier 는 미만(<)만
    ({"brier": 0.18, "logloss": 0.57, "accuracy": 0.70}, (True, False, True, False)),
    ({"brier": 0.18, "logloss": 0.55, "accuracy": 0.6799}, (True, True, False, False)),
])
def test_gate(m, want):
    g = cm.gate(m, RULE)
    assert (g["brier"], g["logloss"], g["accuracy"], g["pass"]) == want


C1 = {f: 0.0 for f in FEATURES} | {"first_blood": 0.1, "kills": -0.9, "assists": 0.3, "cs": 0.9}
C2 = C1 | {"cs": 1.0, "kills": 0.2}


def test_rank_weights_abs_then_feature_order():
    # |계수| 0.9 가 둘이면 FEATURES 순서(kills 가 cs 앞), 0 인 넷도 FEATURES 순서
    got = [(w["rank"], w["feature"]) for w in cm.rank_weights(C1)]
    assert got == [(1, "kills"), (2, "cs"), (3, "assists"), (4, "first_blood"),
                   (5, "avg_level"), (6, "dragons"), (7, "heralds"), (8, "towers")]


def test_compare_weights():
    rows = {r["feature"]: r for r in cm.compare_weights(C1, C2)}
    # m2 순위: cs 1.0, assists 0.3, kills 0.2, first_blood 0.1, 나머지 0
    assert [r["feature"] for r in cm.compare_weights(C1, C2)][:4] == ["cs", "assists", "kills", "first_blood"]
    assert (rows["cs"]["rank_m1"], rows["cs"]["rank_m2"], rows["cs"]["rank_change"]) == (2, 1, 1)
    assert (rows["kills"]["rank_m1"], rows["kills"]["rank_m2"], rows["kills"]["rank_change"]) == (1, 3, -2)
    assert rows["kills"]["coef_change"] == pytest.approx(1.1)       # 0.2 − (−0.9)
    assert rows["assists"]["rank_change"] == 1 and rows["assists"]["coef_change"] == 0
    assert "coef" not in rows["cs"]


def test_report_and_markdown():
    m1 = {"accuracy": 0.70, "brier": 0.19, "logloss": 0.55, "auc": 0.78}
    m2 = {"accuracy": 0.72, "brier": 0.18, "logloss": 0.54, "auc": 0.80}
    v1 = cm.summarize_version("m1", 500, 0, m1, RULE, C1)
    v2 = cm.summarize_version("m2", 5000, 1, m2, RULE, C2)
    assert v1["improvement_vs_rule_pct"]["brier"] == pytest.approx(5.0)    # (0.20 − 0.19) / 0.20
    assert v2["improvement_vs_rule_pct"]["brier"] == pytest.approx(10.0)
    assert v1["gate"]["pass"] and v2["gate"]["pass"]
    rep = cm.build_report(v1, v2, RULE, 1976)
    assert rep["delta_m2_minus_m1"]["brier"] == pytest.approx(-0.01)
    assert rep["delta_m2_minus_m1"]["auc"] == pytest.approx(0.02)
    md = cm.to_markdown(rep)
    assert "| Brier 규칙 대비 개선율 | 0% | 5.00% | 10.00% | +5.00%p |" in md
    assert "| 성능 게이트 | 기준 | 통과 | 통과 | |" in md
    assert "m1 (n=500, seed 0)" in md and "m2 (n=5,000, seed 1)" in md
    assert "1계단 오름" in md and "2계단 내림" in md
    assert not re.search("[\\u2013\\u2014]", md)   # 줄표 없음

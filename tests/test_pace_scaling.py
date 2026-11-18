"""시간대별 페이스 환산 분석 (Issue #19, training/pace_scaling.py). 학습 라이브러리 없이 numpy 만.

기대값은 리포트 2절 성장 가정으로 손으로 셌다.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from training.pace_scaling import (ANCHORS, DEMOS, bounds, kill_sensitivity, ood_of_board, project,
                                   scaled_features)

EVEN_B, EVEN_R = ANCHORS["접전"]
AHEAD_B, AHEAD_R = ANCHORS["우세"]
DOC = Path(__file__).resolve().parent.parent / "docs" / "pace_scaling_analysis.md"


def test_project_is_identity_at_ten():
    assert project(AHEAD_B, AHEAD_R, 10) == (AHEAD_B, AHEAD_R)


def test_project_five_minutes():
    b, r = project(AHEAD_B, AHEAD_R, 5)
    # 킬 8 × 0.5^1.3 = 3.25 → 3, 어시 9 × 0.406 = 3.66 → 4, CS 285 × 3.9/8.9 = 124.9 → 125, 레벨 7.3 − 2.1 = 5.2
    assert b == {"kills": 3, "assists": 4, "cs": 125, "avg_level": pytest.approx(5.2), "dragons": 0, "heralds": 0,
                 "towers": 0}
    # 레드 킬 5 × 0.406 = 2.03 → 2, CS 255 × 0.4382 = 111.7 → 112, 레벨 6.8 − 2.1 = 4.7
    assert (r["kills"], r["cs"], r["avg_level"]) == (2, 112, pytest.approx(4.7))


def test_project_twenty_minutes_objectives_by_kill_share():
    b, r = project(AHEAD_B, AHEAD_R, 20)
    # 킬 몫 (8+1)/(8+5+2) = 0.6. 포탑 6 × 0.6 = 3.6 → 4, 6 × 0.4 = 2.4 → 2. 드래곤 1 + 2×0.6=1.2→1 = 2, 0 + 0.8 → 1
    assert (b["towers"], r["towers"], b["dragons"], r["dragons"]) == (4, 2, 2, 1)
    assert b["avg_level"] == pytest.approx(7.3 + 11.9 - 7.0)  # 레벨 곡선 20분 11.9


def test_scaled_features_use_bridge_scaling():
    row, st = scaled_features(*project(EVEN_B, EVEN_R, 5), "blue", 300.0)
    # 5분 접전: 킬 6 × 0.406 = 2.44 → 2, × 2 = 4 / CS 118 × 2 = 236. 차이는 0, 첫 킬 블루 = 1
    assert st["blue"]["kills"] == 4 and st["blue"]["cs"] == 236 and st["blue"]["avg_level"] == pytest.approx(4.9)
    assert row.tolist() == [1.0, 0, 0, 0, 0, 0, 0, 0]


def test_ood_and_bounds_on_tiny_table():
    X = np.zeros((100, 8))
    X[:, 1] = np.arange(100) - 50                # 킬 차이 −50~49 → 1% 분위수 −49.01, 99% 48.01
    team = np.tile([6, 6, 269, 7.0, 0, 0, 0], (200, 1)).astype(float)
    b = bounds(X, team)
    assert b["feature"]["kills"] == pytest.approx((-49.01, 48.01))
    row, st = scaled_features(*project(EVEN_B, EVEN_R, 20), "red", 1200.0)
    o = ood_of_board(row, st, b)
    assert "avg_level" not in o["features"]                  # 레벨 차이는 0 이라 범위 안
    assert {"blue.avg_level", "red.avg_level"} <= set(o["team"])  # 팀 레벨 11.9 는 7.0 하나뿐인 분포 밖


def test_kill_sensitivity_rounding_can_be_zero_late():
    lin = lambda rows: 1 / (1 + np.exp(-0.3 * rows[:, 1]))  # 킬 차이만 보는 가짜 모델  # noqa: E731
    b, r = project(EVEN_B, EVEN_R, 20)                       # 20분 킬 15. +1 → 16 × 0.5 = 8 (15 × 0.5 = 7.5 → 8)
    assert kill_sensitivity(lin, b, r, "blue", 1200.0) == 0.0
    b5, r5 = project(EVEN_B, EVEN_R, 5)                      # 5분 킬 2 → 4, +1 → 3 → 6: 차이 2
    assert kill_sensitivity(lin, b5, r5, "blue", 300.0) == pytest.approx((lin(np.array([[0, 2.0]]))[0] - 0.5) * 100)


def test_demos_match_overlay_html():
    html = (Path(__file__).resolve().parent.parent / "api" / "static" / "overlay.html").read_text(encoding="utf-8")
    for _, (sec, fb, blue, red) in DEMOS.items():
        assert f"t: {sec}," in html and f'fb: "{fb}"' in html
        assert "blue: { " + ", ".join(f"{k}: {v:g}" if k != "avg_level" else f"{k}: {v:.1f}" for k, v in blue.items()) + " }" in html


def test_report_has_required_sections():
    t = DOC.read_text(encoding="utf-8")
    assert "정확도 검증이 아니다" in t                         # 실제 시간대별 승패가 없다고 밝힌다
    assert "9,879경기" in t and "19,758행" in t and "15판" in t  # 분모와 표본 수
    assert "## 3. 시각별 분포 이탈 비율" in t and "## 4. 킬 1개당 승률 변화" in t
    for t_min in ("5분", "15분", "20분", "25분"):
        assert f"| {t_min} |" in t
    assert "7:30" in t and "12:00" in t and "20:00" in t      # 경고 기준이 결론에 있다
    assert "전 구간에서 정확하다\"고 말하지 않는다" in t
    data = json.loads(DOC.with_suffix(".json").read_text(encoding="utf-8"))
    assert set(data["times"]) == {"5", "10", "15", "20", "25"} and data["n_games"] == 9879

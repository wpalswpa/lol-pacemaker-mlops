"""요인 분해·백분위·코칭·티어 비교. 정의는 AGENTS.md 4절이다.

구현체(rule·sklearn·dl·onnx)에게서 predict_proba 하나만 받아 계산한다.
그래서 구현체가 바뀌어도 계산 방식은 같고, 확률이 같으면 요인·목표도 같다.
"""
import json
import os
from collections.abc import Callable, Sequence

import numpy as np

from api.build import advise
from api.contract import FEATURES, LIMITS, NAMES, TEAM_STATS, GameState, to_features

PredictProba = Callable[[np.ndarray], np.ndarray]

STEPS_PATH = os.path.join(os.path.dirname(__file__), "coach_steps.json")
TOP_N = 3


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pace_of(p: float) -> str:
    if p < 0.45:
        return "열세 페이스"
    if p <= 0.55:
        return "접전"
    return "우세 페이스"


def percentile_of(v: float, qs: Sequence[float], ps: Sequence[float]) -> float:
    """분위수 표(qs: ps 백분위 점의 값)에서 v 의 백분위(0~100).

    같은 값이 표에 있으면 그 값들이 차지한 백분위 점의 평균(동점 구간),
    없으면 이웃한 두 점 사이 선형 보간, 표 밖이면 0 또는 100.
    """
    q = np.asarray(qs, dtype=np.float64)
    p = np.asarray(ps, dtype=np.float64)
    same = np.isclose(q, v, rtol=0.0, atol=1e-9)
    if same.any():
        return float(p[same].mean())
    if v < q[0]:
        return 0.0
    if v > q[-1]:
        return 100.0
    i = int(np.searchsorted(q, v))  # q[i-1] < v < q[i]
    return float(p[i - 1] + (v - q[i - 1]) / (q[i] - q[i - 1]) * (p[i] - p[i - 1]))


class PaceService:
    def __init__(self, predict_proba: PredictProba, baseline: dict, steps: dict):
        self._predict = predict_proba
        self.baseline = baseline
        self.steps = steps
        self._mean = np.asarray([baseline["feature_mean"][f] for f in FEATURES], dtype=np.float32)
        self._q = baseline["quantiles"]

    # ── 공통 ──
    def _proba(self, rows: np.ndarray) -> np.ndarray:
        """여러 줄을 한 번에 예측한다 (요청 한 건에 모델 호출 한 번)."""
        return np.asarray(self._predict(rows.astype(np.float32)), dtype=np.float64).ravel()

    def _top(self, key: str, v: float) -> float:
        """상위 몇 % (작을수록 앞선다)."""
        return 100.0 - percentile_of(v, self._q[key], self._q["ps"])

    @staticmethod
    def _sign(state: GameState) -> int:
        return 1 if state.my_side == "blue" else -1

    def _warnings(self, state: GameState, x: np.ndarray) -> list[str]:
        b = self.baseline
        out = []
        for i, f in enumerate(FEATURES[1:], start=1):
            lo, hi = b["feature_min"][f], b["feature_max"][f]
            if not lo <= x[i] <= hi:
                out.append(f"{NAMES[f]} 차이 {x[i]:g}가 학습 데이터 범위({lo:g}~{hi:g}) 밖이다")
        for side in ("blue", "red"):
            team = getattr(state, side)
            for s in TEAM_STATS:
                v, lo, hi = getattr(team, s), b["team_min"][s], b["team_max"][s]
                if not lo <= v <= hi:
                    out.append(f"{side} {NAMES[s]} {v:g}가 학습 데이터 범위({lo:g}~{hi:g}) 밖이다")
        return out

    # ── /predict ──
    def predict(self, state: GameState) -> dict:
        x = to_features(state)
        sign = self._sign(state)
        # 0번 줄은 지금 상태, i번 줄은 피처 i 만 티어 평균으로 바꾼 상태
        rows = np.repeat(x[None, :], len(FEATURES) + 1, axis=0)
        for i in range(len(FEATURES)):
            rows[i + 1, i] = self._mean[i]
        p = self._proba(rows)
        p_blue = float(p[0])
        my_p = p_blue if sign == 1 else 1.0 - p_blue

        mine = getattr(state, state.my_side)
        enemy = getattr(state, "red" if state.my_side == "blue" else "blue")
        factors = []
        for i, f in enumerate(FEATURES):
            if f == "first_blood":
                my_v = 1.0 if state.first_blood == state.my_side else 0.0
                en_v = 1.0 - my_v
                tier = sign * (2 * float(self._mean[i]) - 1)
            else:
                my_v, en_v = float(getattr(mine, f)), float(getattr(enemy, f))
                tier = sign * float(self._mean[i])
            factors.append({
                "feature": f,
                "name": NAMES[f],
                "my_value": my_v,
                "enemy_value": en_v,
                "diff": my_v - en_v,
                "tier_avg_diff": tier,
                "impact_pp": sign * (p_blue - float(p[i + 1])) * 100,
            })
        # abs(impact) 내림차순, 같으면 FEATURES 순서 (sorted 는 안정 정렬)
        factors = sorted(factors, key=lambda r: -abs(r["impact_pp"]))[:TOP_N]

        team_mean = self.baseline["team_mean"]
        vs_tier = [
            {"stat": s, "name": NAMES[s], "mine": float(getattr(mine, s)), "tier_avg": float(team_mean[s]),
             "gap": float(getattr(mine, s)) - float(team_mean[s])}
            for s in TEAM_STATS
        ]
        overall = self._top("win_prob", my_p)
        percentile = {"overall_top_pct": overall}
        for s in TEAM_STATS:  # 내 팀 기준 격차의 티어 내 위치
            percentile[f"{s}_top_pct"] = self._top(s, sign * float(x[FEATURES.index(s)]))
        pace = pace_of(my_p)
        return {
            "win_prob_blue": p_blue,
            "my_side": state.my_side,
            "my_win_prob": my_p,
            "pace": pace,
            "top_factors": factors,
            "vs_tier": vs_tier,
            "tier": self.baseline["tier"],
            "tier_percentile": percentile,
            "message": f"승률 {my_p * 100:.0f}% · {pace} · 티어 상위 {overall:.0f}%",
            "warnings": self._warnings(state, x),
        }

    # ── /coach ──
    def coach(self, state: GameState) -> dict:
        """/predict 의 모든 근거 수치에 목표(coaching)를 더한다. 에이전트가 한 번만 불러도 되게."""
        base = self.predict(state)
        x = to_features(state)
        sign = self._sign(state)
        mine = getattr(state, state.my_side)
        slope = self.baseline["slope"]

        usable, rows = [], []
        for step in self.steps["steps"]:
            stat, add = step["stat"], float(step["add"])
            target_value = float(getattr(mine, stat)) + add
            if target_value > LIMITS[stat][1]:
                continue  # 허용 범위를 넘는 목표는 내지 않는다
            row = x.copy()
            row[FEATURES.index(stat)] += sign * add
            moved = {}
            for comp in step["with"]:  # 연쇄 반영: 과거 경기에서 함께 움직인 지표
                delta = add * float(slope[stat][comp])
                row[FEATURES.index(comp)] += sign * delta
                moved[comp] = delta
            usable.append((step, target_value, moved))
            rows.append(row)

        my_p = base["my_win_prob"]
        coaching = []
        if rows:
            p = self._proba(np.stack(rows))
            for (step, target_value, moved), pb in zip(usable, p):
                after = float(pb) if sign == 1 else 1.0 - float(pb)
                gain = (after - my_p) * 100
                if gain <= 0:
                    continue  # 승률을 올리지 않는 목표는 내지 않는다
                stat, name, add = step["stat"], NAMES[step["stat"]], float(step["add"])
                my_diff = sign * float(x[FEATURES.index(stat)])
                before_pct = base["tier_percentile"][f"{stat}_top_pct"]
                after_pct = self._top(stat, my_diff + add)
                coaching.append({
                    "stat": stat,
                    "name": name,
                    "add": add,
                    "target_value": target_value,
                    "with": moved,
                    "win_prob_after": after,
                    "gain_pp": gain,
                    "top_pct_before": before_pct,
                    "top_pct_after": after_pct,
                    "overall_top_pct_after": self._top("win_prob", after),
                    "message": f"{name} {add:g}{step['unit']} 더 → 상위 {before_pct:.0f}%에서 {after_pct:.0f}%로 "
                               f"(승률 {my_p * 100:.1f}% → {after * 100:.1f}%, +{gain:.1f}%p)",
                })
        coaching = sorted(coaching, key=lambda r: -r["gain_pp"])

        toast = [f"승률 {my_p * 100:.0f}% · 상위 {base['tier_percentile']['overall_top_pct']:.0f}%"]
        if coaching:
            c = coaching[0]
            tail = f" → 목표: {c['name']} +{c['add']:g} (+{c['gain_pp']:.1f}%p)"
            toast.append(f"{c['name']} +{c['add']:g} → {c['win_prob_after'] * 100:.0f}% "
                         f"(상위 {c['overall_top_pct_after']:.0f}%)")
        else:
            tail = " → 지금 페이스 유지"
        build = advise(my_p, state.my_champion, state.enemy_top_threat, enemy_items=state.enemy_items)
        if base["warnings"]:
            toast.append("학습 범위 밖 입력 있음")
        else:  # 경고가 없을 때만 빌드 한 줄 (카드는 최대 3줄)
            items = build["recommended_items"]
            line = f"{items[0]['name']} → {items[1]['name']}"
            order = build["skill_order"]["order"]
            toast.append(line + (f" · {order}" if order else ""))
        return {
            **base,
            "message": base["message"] + tail,
            "horizon_min": float(self.steps["horizon_min"]),
            "coaching": coaching,
            "build_advice": build,
            "toast": toast,
            "how_to_read": self.steps["how_to_read"],
        }

"""모델 판 비교: 학습 표본 수에 따른 성능·게이트·피처 가중치 변화 (AGENTS.md 9절 "모델 판 비교").

기본값은 model.yml 두 실행과 같은 조건이다. m1 = DATA_N 500·seed 0 (m20261118-1), m2 = 5,000·seed 1 (m20261118-2).
같은 고정 평가셋에서 sklearn 파이프라인(StandardScaler + LogisticRegression)을 다시 학습해 비교한다. artifacts/ 는 건드리지 않는다.

  uv run --group train python -m training.compare_models
  uv run --group train python -m training.compare_models --n1 500 --seed1 0 --n2 5000 --seed2 1 \
      --out docs/model_comparison.json --md docs/model_comparison.md
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from api.contract import FEATURES, NAMES

# training/validate_model.py 의 MIN_ACCURACY 와 같은 값 (그 파일은 import 하면 바로 게이트를 돌리므로 값만 옮겨 둔다.
# 두 값이 같은지는 tests/test_compare_models.py 가 확인한다)
MIN_ACCURACY = 0.68

LOSSES = ("brier", "logloss")


# ── 계산 (학습 라이브러리 없이 돈다. 테스트 대상) ──
def improvement_pct(model: float, rule: float) -> float:
    """규칙 기준선 대비 개선율 (규칙 − 모델) / 규칙 × 100. 손실 지표라 클수록 좋다."""
    return (rule - model) / rule * 100


def gate(m: dict, rule: dict) -> dict:
    """AGENTS.md 9절 성능 게이트와 같은 세 조건."""
    checks = {"brier": m["brier"] < rule["brier"], "logloss": m["logloss"] < rule["logloss"],
              "accuracy": m["accuracy"] >= MIN_ACCURACY}
    return {**checks, "pass": all(checks.values())}


def rank_weights(coef: dict[str, float]) -> list[dict]:
    """|계수| 내림차순, 같으면 FEATURES 순서. rank 는 1부터."""
    order = sorted(coef, key=lambda f: (-abs(coef[f]), FEATURES.index(f)))
    return [{"rank": i + 1, "feature": f, "name": NAMES.get(f, f), "coef": coef[f]} for i, f in enumerate(order)]


def compare_weights(c1: dict[str, float], c2: dict[str, float]) -> list[dict]:
    """두 판의 계수·순위 변화. m2 순위 순서로 돌려준다. rank_change > 0 이면 m2 에서 순위가 올랐다."""
    r1 = {w["feature"]: w["rank"] for w in rank_weights(c1)}
    out = []
    for w in rank_weights(c2):
        f = w["feature"]
        out.append({"feature": f, "name": w["name"], "coef_m1": c1[f], "coef_m2": c2[f], "rank_m1": r1[f],
                    "rank_m2": w["rank"], "coef_change": c2[f] - c1[f], "rank_change": r1[f] - w["rank"]})
    return out


def summarize_version(label: str, n: int, seed: int, m: dict, rule: dict, coef: dict[str, float]) -> dict:
    return {
        "label": label, "data": {"n": n, "seed": seed},
        "metrics": m,
        "improvement_vs_rule_pct": {k: improvement_pct(m[k], rule[k]) for k in LOSSES},
        "gate": gate(m, rule),
        "weights": rank_weights(coef),
    }


def build_report(v1: dict, v2: dict, rule: dict, holdout_n: int) -> dict:
    deltas = {k: v2["metrics"][k] - v1["metrics"][k] for k in ("accuracy", "brier", "logloss", "auc")}
    c1 = {w["feature"]: w["coef"] for w in v1["weights"]}
    c2 = {w["feature"]: w["coef"] for w in v2["weights"]}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holdout_n": holdout_n,
        "rule": rule,
        "versions": [v1, v2],
        "delta_m2_minus_m1": deltas,
        "weights_change": compare_weights(c1, c2),
    }


def to_markdown(rep: dict, top: int = 5) -> str:
    v1, v2 = rep["versions"]
    rule = rep["rule"]
    f4 = lambda x: f"{x:.4f}"  # noqa: E731
    ok = lambda b: "통과" if b else "실패"  # noqa: E731
    lines = [
        "# 모델 판 비교 (m1 vs m2)", "",
        f"고정 평가셋 {rep['holdout_n']:,}경기, 같은 시험지. 규칙 기준선과 성능 게이트 기준은 AGENTS.md 5절·9절. "
        f"`training/compare_models.py`가 만든 표다({rep['generated_at']}).", "",
        "| 지표 | 규칙 기준선 | " + " | ".join(f"{v['label']} (n={v['data']['n']:,}, seed {v['data']['seed']})"
                                              for v in (v1, v2)) + " | m2 − m1 |",
        "|---|---|---|---|---|",
    ]
    for k, name in (("accuracy", "정확도"), ("brier", "Brier (낮을수록 좋음)"), ("logloss", "LogLoss (낮을수록 좋음)"),
                    ("auc", "AUC")):
        base = f4(rule[k]) if k in rule else "-"
        lines.append(f"| {name} | {base} | {f4(v1['metrics'][k])} | {f4(v2['metrics'][k])} | "
                     f"{rep['delta_m2_minus_m1'][k]:+.4f} |")
    for k, name in (("brier", "Brier 규칙 대비 개선율"), ("logloss", "LogLoss 규칙 대비 개선율")):
        lines.append(f"| {name} | 0% | {v1['improvement_vs_rule_pct'][k]:.2f}% | {v2['improvement_vs_rule_pct'][k]:.2f}% | "
                     f"{v2['improvement_vs_rule_pct'][k] - v1['improvement_vs_rule_pct'][k]:+.2f}%p |")
    lines.append(f"| 성능 게이트 | 기준 | {ok(v1['gate']['pass'])} | {ok(v2['gate']['pass'])} | |")
    lines += ["", f"## 피처 가중치 (로지스틱 회귀 계수, 정규화 뒤 1표준편차당 로그 오즈, |계수| 상위 {top}개)", "",
              "| m2 순위 | 피처 | m1 계수 (순위) | m2 계수 | 변화 |", "|---|---|---|---|---|"]
    for w in rep["weights_change"][:top]:
        move = "같음" if w["rank_change"] == 0 else (f"{w['rank_change']}계단 오름" if w["rank_change"] > 0
                                                     else f"{-w['rank_change']}계단 내림")
        lines.append(f"| {w['rank_m2']} | {w['name']} | {w['coef_m1']:+.3f} ({w['rank_m1']}) | {w['coef_m2']:+.3f} | "
                     f"{w['coef_change']:+.3f}, {move} |")
    lines += ["", "표본이 작으면 계수가 흔들리고 확률 품질(Brier·LogLoss)이 규칙과 비슷해진다. 그래서 패치마다 다시 학습하되, "
              "같은 시험지의 게이트를 통과한 판만 올리고 옛 판을 남겨 되돌릴 수 있게 한다.", ""]
    return "\n".join(lines)


# ── 학습 (학습 라이브러리 필요) ──
def train_version(n: int, seed: int) -> tuple[dict, dict[str, float]]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from training.data import collect_data, holdout
    from training.evaluate import metrics

    X, y, _ = collect_data(n, seed)
    Xt, yt = holdout()
    pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    pipe.fit(X, y)
    p = pipe.predict_proba(Xt)[:, 1]
    m = {**metrics(yt, p), "auc": float(roc_auc_score(yt, p))}
    coef = dict(zip(FEATURES, (float(c) for c in pipe[-1].coef_[0])))
    return m, coef


def rule_reference() -> tuple[dict, int]:
    from sklearn.metrics import roc_auc_score

    from api.predictors.rule import predict_proba as rule_proba
    from training.data import holdout
    from training.evaluate import metrics

    Xt, yt = holdout()
    p = rule_proba(Xt)
    return {**metrics(yt, p), "auc": float(roc_auc_score(yt, p))}, int(len(yt))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="모델 판 비교 (m1 vs m2)")
    ap.add_argument("--n1", type=int, default=500)
    ap.add_argument("--seed1", type=int, default=0)
    ap.add_argument("--n2", type=int, default=5000)
    ap.add_argument("--seed2", type=int, default=1)
    ap.add_argument("--label1", default="m1")
    ap.add_argument("--label2", default="m2")
    ap.add_argument("--out", default="docs/model_comparison.json")
    ap.add_argument("--md", default="docs/model_comparison.md")
    a = ap.parse_args(argv)

    rule, holdout_n = rule_reference()
    versions = []
    for label, n, seed in ((a.label1, a.n1, a.seed1), (a.label2, a.n2, a.seed2)):
        m, coef = train_version(n, seed)
        versions.append(summarize_version(label, n, seed, m, rule, coef))
    rep = build_report(*versions, rule, holdout_n)
    md = to_markdown(rep)
    print(md)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
            f.write("\n")
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

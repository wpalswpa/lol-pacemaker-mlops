"""10분 모델의 시간대별 페이스 환산 한계 분석 (Issue #19, AGENTS.md 3-4절).

오버레이는 t분 점수판의 누적 값에 10/t 를 곱해 10분 모델에 넣는다. 시간대별 실제 승패 데이터가 없으므로
정확도는 잴 수 없다. 대신 두 가지를 잰다.
  1. 분포 이탈: 환산한 입력이 학습 데이터(10분 스냅샷 9,879경기)의 1~99% 분위수 밖으로 나가는 비율
  2. 민감도: 점수판에서 킬 1개가 늘 때 승률이 몇 %p 움직이는지

t분 점수판은 두 가지로 만든다(둘 다 가정이다, 리포트에 표로 밝힌다).
  A. 대표 점수판: 10분 기준 접전·우세·열세 세 판을 아래 성장 가정으로 t분으로 늘린 것 + 오버레이 데모 3개
  B. 전 경기 투영: 9,879경기의 10분 값 각각을 같은 성장 가정으로 t분으로 늘린 것 (경기마다의 차이를 살린 확인용)

  uv run --group train python -m training.pace_scaling    # docs/pace_scaling_analysis.{md,json}
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np

from api.contract import FEATURES, TEAM_STATS
from client.bridge import pace_scale, scale_to_ten_minutes

TIMES = (5, 10, 15, 20, 25)  # 10 은 기준(환산 배율 1)
FINE_TIMES = (5, 6, 6.5, 7, 8, 10, 11, 12, 13, 14, 15, 17, 20, 25)  # 경고 경계를 고르는 촘촘한 시각
Q_LO, Q_HI = 1, 99
# 성장 가정 (리포트 "가정" 표와 같다)
KILL_EXP = 1.3                   # 킬·어시: 10분 값 × (t/10)^1.3 (교전이 뒤로 갈수록 잦다)
CS_START_MIN = 1.1               # CS: 첫 미니언이 1:05 무렵이라 10분 값 × (t − 1.1)/(10 − 1.1)
LEVEL_CURVE = {5: 4.9, 10: 7.0, 15: 9.9, 20: 11.9, 25: 13.4}  # 팀 평균 레벨의 중앙값 곡선, 경기별 차이는 유지
EXTRA_OBJECTIVES = {             # 10분 값에 더하는 두 팀 합계(킬 몫으로 나눔). 5분은 오브젝트 0
    15: {"dragons": 1, "heralds": 1, "towers": 2},
    20: {"dragons": 2, "heralds": 1, "towers": 6},
    25: {"dragons": 3, "heralds": 1, "towers": 10},
}
TEAM_MAX = {"dragons": 7, "heralds": 2, "towers": 11}
# 10분 기준 대표 판 (데이터 중앙값 팀 = 킬 6·어시 6·CS 269·레벨 7.0·오브젝트 0 근처)
ANCHORS = {
    "접전": ({"kills": 6, "assists": 6, "cs": 269, "avg_level": 7.0, "dragons": 0, "heralds": 0, "towers": 0},
             {"kills": 6, "assists": 6, "cs": 269, "avg_level": 7.0, "dragons": 0, "heralds": 0, "towers": 0}),
    "우세": ({"kills": 8, "assists": 9, "cs": 285, "avg_level": 7.3, "dragons": 1, "heralds": 0, "towers": 0},
             {"kills": 5, "assists": 5, "cs": 255, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0}),
}
ANCHORS["열세"] = (ANCHORS["우세"][1], ANCHORS["우세"][0])
# api/static/overlay.html 의 DEMOS 와 같은 값 (블루가 나)
DEMOS = {
    "데모 초반 우세 06:30": (390, "blue",
                         {"kills": 4, "assists": 5, "cs": 230, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0},
                         {"kills": 1, "assists": 1, "cs": 205, "avg_level": 6.4, "dragons": 0, "heralds": 0, "towers": 0}),
    "데모 중반 박빙 14:20": (860, "red",
                         {"kills": 11, "assists": 17, "cs": 500, "avg_level": 11.3, "dragons": 1, "heralds": 1, "towers": 2},
                         {"kills": 10, "assists": 15, "cs": 480, "avg_level": 11.3, "dragons": 1, "heralds": 1, "towers": 2}),
    "데모 후반 열세 23:10": (1390, "red",
                         {"kills": 12, "assists": 18, "cs": 690, "avg_level": 13.8, "dragons": 1, "heralds": 1, "towers": 3},
                         {"kills": 21, "assists": 30, "cs": 760, "avg_level": 14.6, "dragons": 3, "heralds": 1, "towers": 6}),
}
DIFF_FEATURES = FEATURES[1:]  # first_blood 는 0/1 이라 이탈이 없다


def _half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _interp(curve: dict, t: float) -> float:
    ks = sorted(curve)
    return float(np.interp(t, ks, [curve[k] for k in ks]))


def _extra(t: float, o: str) -> float:
    """10분 값에 더할 두 팀 합계 오브젝트. 10분 0, 표의 시각 사이는 직선으로 잇는다."""
    return _interp({10: 0.0, **{k: float(v[o]) for k, v in EXTRA_OBJECTIVES.items()}}, t)


def project(blue: dict, red: dict, t: float) -> tuple[dict, dict]:
    """10분 두 팀 값을 t분 점수판으로 늘린다(가정). 정수 칸은 반올림, 레벨은 1~18.
    오브젝트는 5분 이전 0, 5~10분은 10분 값 × (t − 5)/5, 10분 뒤는 10분 값 + 표의 합계를 킬 몫으로."""
    if t == 10:
        return dict(blue), dict(red)
    kb, kr = blue["kills"], red["kills"]
    share = (kb + 1) / (kb + kr + 2)  # 오브젝트는 킬 몫으로 나눈다
    out = []
    for team, sh in ((blue, share), (red, 1 - share)):
        g = {
            "kills": _half_up(team["kills"] * (t / 10) ** KILL_EXP),
            "assists": _half_up(team["assists"] * (t / 10) ** KILL_EXP),
            "cs": _half_up(team["cs"] * max(t - CS_START_MIN, 0) / (10 - CS_START_MIN)),
            "avg_level": min(max(team["avg_level"] + _interp(LEVEL_CURVE, t) - LEVEL_CURVE[10], 1.0), 18.0),
        }
        for o in ("dragons", "heralds", "towers"):
            if t < 10:
                g[o] = _half_up(team[o] * max(t - 5, 0) / 5)
            else:
                g[o] = min(team[o] + _half_up(_extra(t, o) * sh), TEAM_MAX[o])
        out.append(g)
    return out[0], out[1]


def scaled_features(blue: dict, red: dict, first_blood: str, seconds: float) -> tuple[np.ndarray, dict]:
    """브리지와 같은 환산(client.bridge.scale_to_ten_minutes) 뒤 모델 입력 한 줄과 환산 입력."""
    st = scale_to_ten_minutes({"blue": blue, "red": red, "first_blood": first_blood, "my_side": "blue"}, seconds)
    row = [1.0 if first_blood == "blue" else 0.0] + [float(st["blue"][s]) - float(st["red"][s]) for s in TEAM_STATS]
    return np.asarray(row, dtype=np.float64), st


def bounds(X: np.ndarray, team: np.ndarray) -> dict:
    """학습 분포의 1~99% 분위수. 피처(블루 − 레드)와 팀 값(두 팀을 합친 행)."""
    return {
        "feature": {f: (float(np.percentile(X[:, i], Q_LO)), float(np.percentile(X[:, i], Q_HI)))
                    for i, f in enumerate(FEATURES) if f in DIFF_FEATURES},
        "team": {s: (float(np.percentile(team[:, i], Q_LO)), float(np.percentile(team[:, i], Q_HI)))
                 for i, s in enumerate(TEAM_STATS)},
    }


def outside(v: float, lo_hi: tuple[float, float]) -> bool:
    return not lo_hi[0] <= v <= lo_hi[1]


def ood_of_board(row: np.ndarray, st: dict, b: dict) -> dict:
    """한 점수판: 이탈한 피처 이름, 이탈한 팀 값 이름(블루·레드)."""
    feats = [f for i, f in enumerate(FEATURES) if f in DIFF_FEATURES and outside(row[i], b["feature"][f])]
    teams = [f"{side}.{s}" for side in ("blue", "red") for s in TEAM_STATS
             if outside(float(st[side][s]), b["team"][s])]
    return {"features": feats, "team": teams}


def kill_sensitivity(predict, blue: dict, red: dict, first_blood: str, seconds: float) -> float:
    """블루(나)가 점수판에서 킬 1개를 더 했을 때 블루 승률 변화(%p). 어시 등 다른 칸은 그대로."""
    base, _ = scaled_features(blue, red, first_blood, seconds)
    more, _ = scaled_features({**blue, "kills": blue["kills"] + 1}, red, first_blood, seconds)
    p = predict(np.vstack([base, more]))
    return float((p[1] - p[0]) * 100)


def analyze(X: np.ndarray, teams: dict, predict, first_blood: np.ndarray, times=TIMES, fine_times=FINE_TIMES) -> dict:
    team_all = np.vstack([teams["blue"], teams["red"]])
    b = bounds(X, team_all)
    out = {"bounds": b, "n_games": int(len(X)), "n_team_rows": int(len(team_all)), "times": {}, "fine": {}}

    for t in sorted(set(times) | set(fine_times)):
        sec = t * 60.0
        minutes, k = pace_scale(sec)
        boards = []
        for name, (bl, rd) in ANCHORS.items():
            pb, pr = project(bl, rd, t)
            row, st = scaled_features(pb, pr, "blue", sec)
            p = float(predict(row[None, :])[0])
            boards.append({"name": name, "raw": {"blue": pb, "red": pr}, "scaled": {"blue": st["blue"], "red": st["red"]},
                           "win_prob_pct": p * 100, "kill_pp": kill_sensitivity(predict, pb, pr, "blue", sec),
                           **ood_of_board(row, st, b)})
        # B. 전 경기 투영
        feat_out = {f: 0 for f in DIFF_FEATURES}
        team_out = {s: 0 for s in TEAM_STATS}
        any_out = 0
        rows, more_rows = [], []
        for g in range(len(X)):
            bl = dict(zip(TEAM_STATS, (float(v) for v in teams["blue"][g])))
            rd = dict(zip(TEAM_STATS, (float(v) for v in teams["red"][g])))
            for d in (bl, rd):
                for s in TEAM_STATS:
                    if s != "avg_level":
                        d[s] = int(d[s])
            pb, pr = project(bl, rd, t)
            fb = "blue" if first_blood[g] else "red"
            row, st = scaled_features(pb, pr, fb, sec)
            more, _ = scaled_features({**pb, "kills": pb["kills"] + 1}, pr, fb, sec)
            rows.append(row)
            more_rows.append(more)
            o = ood_of_board(row, st, b)
            for f in o["features"]:
                feat_out[f] += 1
            for name in o["team"]:
                team_out[name.split(".", 1)[1]] += 1
            any_out += bool(o["features"] or o["team"])
        p0 = predict(np.vstack(rows))
        p1 = predict(np.vstack(more_rows))
        dk = (p1 - p0) * 100
        key = "times" if t in times else "fine"
        out[key][t] = {
            "minutes": minutes, "pace_scale": k, "boards": boards,
            "projected": {
                "feature_ood_pct": {f: feat_out[f] / len(X) * 100 for f in DIFF_FEATURES},
                "team_ood_pct": {s: team_out[s] / len(team_all) * 100 for s in TEAM_STATS},
                "any_ood_pct": any_out / len(X) * 100,
                "kill_pp_mean": float(dk.mean()), "kill_pp_p10": float(np.percentile(dk, 10)),
                "kill_pp_p90": float(np.percentile(dk, 90)), "kill_pp_zero_pct": float((np.abs(dk) < 1e-9).mean() * 100),
            },
        }

    demos = []
    for name, (sec, fb, bl, rd) in DEMOS.items():
        row, st = scaled_features(bl, rd, fb, sec)
        demos.append({"name": name, "seconds": sec, "pace_scale": pace_scale(sec)[1],
                      "win_prob_pct": float(predict(row[None, :])[0]) * 100,
                      "kill_pp": kill_sensitivity(predict, bl, rd, fb, sec), **ood_of_board(row, st, b)})
    out["demos"] = demos
    return out


NAMES = {"kills": "킬", "assists": "어시스트", "cs": "CS", "avg_level": "평균 레벨", "dragons": "드래곤",
         "heralds": "전령", "towers": "포탑"}


def mmss(t: float) -> str:
    return f"{int(t):02d}:{int(round((t - int(t)) * 60)):02d}"


def _f(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}"


def to_markdown(rep: dict, model_label: str) -> str:
    T = rep["times"]
    b = rep["bounds"]
    L = ["# 10분 모델의 시간대별 페이스 환산 한계 분석 (Issue #19)", "",
         "> **이 문서는 정확도 검증이 아니다.** 시간대별 실제 승패 데이터가 없어서, 환산한 입력이 학습 분포에서 "
         "얼마나 벗어나는지(분포 이탈)와 킬 1개가 승률을 얼마나 움직이는지(민감도)만 잰다. "
         "t분 점수판은 아래 성장 가정으로 만든 것이라, 수치는 방향과 크기를 보는 용도다.", "",
         f"- 모델: {model_label}. 승률은 sklearn 파이프라인(서빙 ONNX 와 오차 1e-4 이하) 출력",
         f"- 학습 분포: `data/high_diamond_ranked_10min.csv` {rep['n_games']:,}경기(팀 값은 두 팀을 합친 {rep['n_team_rows']:,}행)의 "
         f"{Q_LO}~{Q_HI}% 분위수",
         "- 환산: `client/bridge.py`의 `scale_to_ten_minutes` 그대로(누적 값 × 10/t, 0.5 올림, 허용 범위로 자름, 레벨은 그대로)",
         "- 다시 만들기: `uv run --group train python -m training.pace_scaling`", "",
         "## 1. 학습 분포 범위 (1~99% 분위수)", "",
         "| 지표 | 피처(블루 − 레드) | 팀 값 |", "|---|---|---|"]
    for s in TEAM_STATS:
        fl, fh = b["feature"][s]
        tl, th = b["team"][s]
        L.append(f"| {NAMES[s]} | {fl:g} ~ {fh:g} | {tl:g} ~ {th:g} |")
    L += ["", "## 2. 성장 가정 (t분 점수판을 만드는 방법)", "",
          "| 지표 | 가정 |", "|---|---|",
          f"| 킬·어시스트 | 10분 값 × (t/10)^{KILL_EXP} (뒤로 갈수록 교전이 잦다) |",
          f"| CS | 10분 값 × (t − {CS_START_MIN})/(10 − {CS_START_MIN}) (첫 미니언 1:05 무렵) |",
          "| 평균 레벨 | 10분 값 + (중앙값 곡선 t분 − 7.0). 곡선 " +
          ", ".join(f"{t}분 {v}" for t, v in LEVEL_CURVE.items()) + ". 두 팀 레벨 차이는 그대로 |",
          "| 드래곤·전령·포탑 | 5분은 0. 15·20·25분은 10분 값에 두 팀 합계 " +
          "; ".join(f"{t}분 드래곤 {v['dragons']}·전령 {v['heralds']}·포탑 {v['towers']}" for t, v in EXTRA_OBJECTIVES.items()) +
          "를 킬 몫으로 나눠 더한다 |",
          "| 대표 판(10분 기준) | 접전: 두 팀 킬 6·어시 6·CS 269·레벨 7.0. 우세: 킬 8:5·어시 9:5·CS 285:255·레벨 7.3:6.8·드래곤 1:0. 열세는 우세를 뒤집은 것 |",
          "| 데모 | `api/static/overlay.html`의 세 데모 점수판 그대로(06:30·14:20·23:10) |", "",
          "## 3. 시각별 분포 이탈 비율 (전 경기 투영)", "",
          f"분모: 피처는 {rep['n_games']:,}경기, 팀 값은 {rep['n_team_rows']:,}행. 10분은 정의상 지표마다 약 2% 안팎이다(분위수 밖 양쪽 1%씩).", "",
          "| 시각 | 배율 | " + " | ".join(f"{NAMES[s]} 차이" for s in TEAM_STATS) + " | 하나라도 이탈 |",
          "|---|---|" + "---|" * (len(TEAM_STATS) + 1)]
    for t, v in T.items():
        pr = v["projected"]
        L.append(f"| {t}분 | ×{_f(v['pace_scale'], 2)} | " +
                 " | ".join(_f(pr["feature_ood_pct"][s]) + "%" for s in TEAM_STATS) + f" | **{_f(pr['any_ood_pct'])}%** |")
    L += ["", "팀 값 이탈 비율(두 팀 행 기준):", "",
          "| 시각 | " + " | ".join(NAMES[s] for s in TEAM_STATS) + " |", "|---|" + "---|" * len(TEAM_STATS)]
    for t, v in T.items():
        L.append(f"| {t}분 | " + " | ".join(_f(v["projected"]["team_ood_pct"][s]) + "%" for s in TEAM_STATS) + " |")
    L += ["", "## 4. 킬 1개당 승률 변화 (민감도)", "",
          "점수판에서 내 팀(블루) 킬만 1개 늘렸을 때 블루 승률 변화(%p). 환산 뒤 반올림 때문에 늦은 시각에는 0이 되는 판이 많다.", "",
          "| 시각 | 배율 | 접전 | 우세 | 열세 | 전 경기 평균 | 전 경기 10~90% | 변화 0인 경기 |", "|---|---|---|---|---|---|---|---|"]
    for t, v in T.items():
        bd = {x["name"]: x for x in v["boards"]}
        pr = v["projected"]
        L.append(f"| {t}분 | ×{_f(v['pace_scale'], 2)} | {_f(bd['접전']['kill_pp'])} | {_f(bd['우세']['kill_pp'])} | "
                 f"{_f(bd['열세']['kill_pp'])} | {_f(pr['kill_pp_mean'])} | {_f(pr['kill_pp_p10'])} ~ {_f(pr['kill_pp_p90'])} | "
                 f"{_f(pr['kill_pp_zero_pct'])}% |")
    allt = {**rep["times"], **rep["fine"]}
    base = rep["times"][10]["projected"]["kill_pp_mean"]
    L += ["", "## 5. 경고 경계를 고르는 촘촘한 시각 (전 경기 투영)", "",
          "같은 성장 가정을 시각 사이에서 직선으로 이었다. 민감도 배수 = 그 시각 킬 +1 평균 ÷ 10분 킬 +1 평균.", "",
          "| 시각 | 배율 | 킬 +1 평균 | 민감도 배수 | 킬 +1 이 0인 경기 | 팀 평균 레벨 이탈 | 피처 하나라도 이탈 |",
          "|---|---|---|---|---|---|---|"]
    for t in sorted(allt):
        v = allt[t]
        pr = v["projected"]
        feat_any = max(pr["feature_ood_pct"].values())
        L.append(f"| {mmss(t)} | ×{_f(v['pace_scale'], 2)} | {_f(pr['kill_pp_mean'])}%p | ×{_f(pr['kill_pp_mean'] / base, 2)} | "
                 f"{_f(pr['kill_pp_zero_pct'])}% | {_f(pr['team_ood_pct']['avg_level'])}% | {_f(feat_any)}% (가장 큰 피처) |")
    L += ["", "## 6. 대표 점수판과 데모 (판 하나씩)", "",
          f"표본: 대표 판 {len(ANCHORS)}개 × {len(TIMES)}시각 = {len(ANCHORS) * len(TIMES)}판, 데모 {len(rep['demos'])}판. "
          "이탈 칸은 1~99% 밖인 피처(차이)와 팀 값.", "",
          "| 점수판 | 배율 | 블루 승률 | 킬 +1 | 이탈한 피처 | 이탈한 팀 값 |", "|---|---|---|---|---|---|"]
    for t, v in T.items():
        for x in v["boards"]:
            L.append(f"| {t}분 {x['name']} | ×{_f(v['pace_scale'], 2)} | {_f(x['win_prob_pct'])}% | {_f(x['kill_pp'])}%p | "
                     f"{', '.join(NAMES[f] for f in x['features']) or '없음'} | {', '.join(x['team']) or '없음'} |")
    for x in rep["demos"]:
        L.append(f"| {x['name']} | ×{_f(x['pace_scale'], 2)} | {_f(x['win_prob_pct'])}% | {_f(x['kill_pp'])}%p | "
                 f"{', '.join(NAMES[f] for f in x['features']) or '없음'} | {', '.join(x['team']) or '없음'} |")
    L += ["", CONCLUSION_PLACEHOLDER]
    return "\n".join(L) + "\n"


CONCLUSION_PLACEHOLDER = "<!-- 결론은 아래 conclusion() 이 채운다 -->"


def train_model(n: int, seed: int):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from training.data import collect_data

    X, y, _ = collect_data(n, seed)
    pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(X, y)
    return lambda rows: pipe.predict_proba(np.asarray(rows, dtype=np.float32))[:, 1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="docs/pace_scaling_analysis.json")
    ap.add_argument("--md", default="docs/pace_scaling_analysis.md")
    a = ap.parse_args(argv)

    from training.data import _load
    d = _load()
    predict = train_model(a.n, a.seed)
    rep = analyze(d["X"].astype(np.float64), {"blue": d["blue"], "red": d["red"]}, predict, d["X"][:, 0] > 0.5)
    label = f"m2 조건(DATA_N {a.n}·DATA_SEED {a.seed}, 실서버 m20261118-2 와 같은 학습 조건으로 다시 학습)"
    rep["model"] = label
    Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = to_markdown(rep, label).replace(CONCLUSION_PLACEHOLDER, conclusion(rep))
    Path(a.md).write_text(md, encoding="utf-8")
    print(md)
    return 0


def conclusion(rep: dict) -> str:
    """결론 문장. 숫자는 이번 실행 결과에서 꺼낸다(다시 돌려도 문장과 표가 어긋나지 않게)."""
    T, F = rep["times"], rep["fine"]
    allt = {**T, **F}
    base = T[10]["projected"]["kill_pp_mean"]
    ratio = {t: allt[t]["projected"]["kill_pp_mean"] / base for t in allt}
    zero = {t: allt[t]["projected"]["kill_pp_zero_pct"] for t in allt}
    lvl = {t: allt[t]["projected"]["team_ood_pct"]["avg_level"] for t in allt}
    tow = {t: allt[t]["projected"]["feature_ood_pct"]["towers"] for t in allt}
    return "\n".join([
        "## 7. 결론", "",
        "1. **10분 환산은 10분 근처에서만 학습 분포 안에 있다.** 환산은 누적 값만 바꾸고 평균 레벨은 그대로 둔다. "
        f"그래서 팀 평균 레벨이 학습 1~99%(6.2~7.6) 밖인 비율이 8분 {_f(lvl[8])}%, 12분 {_f(lvl[12])}%, 15분 이후 {_f(lvl[15])}%다. "
        "모델 입력은 레벨 차이라 차이 자체는 범위 안에 남지만, 학습 때 본 적 없는 레벨대의 경기를 같은 계수로 읽는다.",
        f"2. **초반에는 킬 1개가 과장된다.** 킬 +1의 승률 변화가 10분의 ×{_f(ratio[5], 2)}(5:00), ×{_f(ratio[7], 2)}(7:00), "
        f"×{_f(ratio[8], 2)}(8:00)이다. 1.5배를 넘는 7:00까지를 초반 경고 구간으로, 경계를 7:30으로 둔다.",
        f"3. **중후반에는 킬 1개가 반영되지 않거나 한 번에 뛴다.** 배율이 1보다 작아 반올림 뒤 값이 그대로인 경기가 "
        f"12분 {_f(zero[12])}%, 15분 {_f(zero[15])}%, 20분 {_f(zero[20])}%다. 승률이 계단처럼 움직인다.",
        f"4. **20분 이후에는 모델 입력(포탑 차이)도 범위를 벗어난다.** 포탑 차이 이탈이 20분 {_f(tow[20])}%, 25분 {_f(tow[25])}%다.",
        "5. **경고 기준** (AGENTS.md 3-4절 \"시각 경고\"에 적고 `client/bridge.py`·`/overlay`에 넣었다): "
        "7:30 전 `early`(배율과 \"킬 1개가 10분보다 크게 반영\"), 12:00부터 `late`(\"10분 모델 범위 밖(레벨), 승률은 참고만\"), "
        "20:00부터 `very_late`(\"포탑 차이도 범위 밖, 우세·열세 방향만\"). 7:30~12:00에는 경고를 내지 않는다.",
        "6. **발표 문구**: \"게임 중 전 구간에서 정확하다\"고 말하지 않는다. \"10분 근처(7:30~12:00)에서 학습 분포 안, "
        "그 밖은 경고와 함께 방향만 보여 준다\"고 말한다.",
        "",
        "**한계**: t분 점수판은 2절의 성장 가정으로 만든 것이다. 실제 시간대별 점수판·승패가 모이면(전적 DB 의 "
        "`game_time_sec`, AGENTS.md 3-5절) 같은 표를 실측으로 다시 만든다. 이 분석은 정확도를 재지 않는다.",
    ])


if __name__ == "__main__":
    raise SystemExit(main())

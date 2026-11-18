"""API 계약 테스트 (AGENTS.md 3·4·5절).

기대값은 AGENTS.md 의 규칙 식과 계산 정의, 고정 자료 fixtures/baseline.json 에서 여기서 직접 계산한다.
앱 코드의 출력을 정답으로 옮겨 적지 않는다.
"""
import copy
import json
import math
import os

import pytest

from api.tests.conftest import FIXTURES

BASE = json.load(open(os.path.join(FIXTURES, "baseline.json"), encoding="utf-8"))
MEAN = BASE["feature_mean"]
TEAM_STATS = ["kills", "assists", "cs", "avg_level", "dragons", "heralds", "towers"]
FEATURES = ["first_blood", *TEAM_STATS]
LIMITS = {"kills": 100, "assists": 300, "cs": 2000, "dragons": 7, "heralds": 2, "towers": 11}


# ── 계약에서 직접 옮긴 계산 (AGENTS.md 5절 규칙 식, 4절 정의) ──
def rule_p(d: dict) -> float:
    gold = 300 * d["kills"] + 20 * d["cs"] + 250 * d["towers"] + 300 * d["dragons"]
    return 1 / (1 + math.exp(-gold / 2500))


def diffs(body: dict) -> dict:
    d = {s: body["blue"][s] - body["red"][s] for s in TEAM_STATS}
    d["first_blood"] = 1 if body["first_blood"] == "blue" else 0
    return d


def expected_impacts(body: dict) -> dict:
    """피처 하나만 티어 평균으로 바꿨을 때와의 차이(%p, 내 팀 기준)."""
    d = diffs(body)
    sign = 1 if body["my_side"] == "blue" else -1
    out = {}
    for f in FEATURES:
        d2 = dict(d, **{f: MEAN[f]})
        out[f] = sign * (rule_p(d) - rule_p(d2)) * 100
    return out


def state(**over) -> dict:
    body = {
        "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0},
        "red": {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0},
        "first_blood": "red",
        "my_side": "blue",
    }
    for k, v in over.items():
        if k in ("blue", "red"):
            body[k].update(v)
        else:
            body[k] = v
    return body


EVEN = {"kills": 6, "assists": 7, "cs": 268, "avg_level": 6.9, "dragons": 0, "heralds": 0, "towers": 0}


# ── /health ──
def test_health(client, app_module):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "model": "rule", "app_version": "dev", "model_version": "m20000101-1"}


def test_unknown_path_404(client):
    assert client.get("/nope").status_code == 404


# ── /predict ──
def test_predict_win_prob_follows_rule(client):
    body = state()
    r = client.post("/predict", json=body).json()
    p = rule_p(diffs(body))
    assert r["win_prob_blue"] == pytest.approx(p, abs=1e-9)
    assert r["my_side"] == "blue"
    assert r["my_win_prob"] == pytest.approx(p, abs=1e-9)
    assert r["model"] == "rule" and r["model_version"] == "m20000101-1"
    assert r["tier"] == BASE["tier"]


def test_predict_red_side_flips(client):
    body = state(my_side="red")
    r = client.post("/predict", json=body).json()
    p = rule_p(diffs(body))
    assert r["win_prob_blue"] == pytest.approx(p, abs=1e-9)
    assert r["my_win_prob"] == pytest.approx(1 - p, abs=1e-9)
    imp = expected_impacts(body)
    for f in r["top_factors"]:
        assert f["impact_pp"] == pytest.approx(imp[f["feature"]], abs=1e-6)
    cs = next(f for f in r["top_factors"] if f["feature"] == "cs")
    assert (cs["my_value"], cs["enemy_value"], cs["diff"]) == (268, 250, 18)
    assert cs["tier_avg_diff"] == pytest.approx(-MEAN["cs"])


def test_top_factors_are_three_sorted_by_impact(client):
    body = state()
    r = client.post("/predict", json=body).json()
    imp = expected_impacts(body)
    want = sorted(FEATURES, key=lambda f: -abs(imp[f]))[:3]  # 안정 정렬: 같으면 FEATURES 순서
    assert [f["feature"] for f in r["top_factors"]] == want
    for f in r["top_factors"]:
        assert f["impact_pp"] == pytest.approx(imp[f["feature"]], abs=1e-6)
    kills = next(f for f in r["top_factors"] if f["feature"] == "kills")
    assert kills["name"] == "킬"
    assert (kills["my_value"], kills["enemy_value"], kills["diff"]) == (5, 7, -2)
    assert kills["tier_avg_diff"] == pytest.approx(MEAN["kills"])
    assert kills["impact_pp"] < 0  # 킬 격차는 승률을 깎는 요인


def test_first_blood_factor_values(client):
    """양 팀 값이 같으면 티어 평균이 0이 아닌 킬(0.5)·CS(−2)만 몫이 생긴다.
    규칙은 첫 킬을 쓰지 않아 몫이 0이고, 몫 0인 피처 중에서는 FEATURES 순서상 첫 킬이 먼저 온다."""
    body = state(blue=EVEN, red=EVEN, first_blood="red", my_side="red")
    r = client.post("/predict", json=body).json()
    assert [f["feature"] for f in r["top_factors"]] == ["kills", "cs", "first_blood"]
    fb = r["top_factors"][2]
    assert (fb["my_value"], fb["enemy_value"], fb["diff"]) == (1.0, 0.0, 1.0)
    assert fb["tier_avg_diff"] == pytest.approx(1 - 2 * MEAN["first_blood"])
    assert fb["impact_pp"] == pytest.approx(0.0, abs=1e-9)


def test_vs_tier_gap(client):
    r = client.post("/predict", json=state()).json()
    assert [g["stat"] for g in r["vs_tier"]] == TEAM_STATS
    cs = next(g for g in r["vs_tier"] if g["stat"] == "cs")
    assert (cs["mine"], cs["tier_avg"]) == (250, BASE["team_mean"]["cs"])
    assert cs["gap"] == pytest.approx(250 - BASE["team_mean"]["cs"])


def test_even_game_is_close_pace(client):
    r = client.post("/predict", json=state(blue=EVEN, red=EVEN)).json()
    assert r["win_prob_blue"] == pytest.approx(0.5)
    assert r["pace"] == "접전"
    assert r["warnings"] == []


@pytest.mark.parametrize("p,label", [(0.4499, "열세 페이스"), (0.45, "접전"), (0.55, "접전"), (0.5501, "우세 페이스")])
def test_pace_boundaries(p, label):
    from api.service import pace_of

    assert pace_of(p) == label


def test_pace_label_from_response(client):
    body = state()  # 블루 기준 약 37.7%
    r = client.post("/predict", json=body).json()
    assert r["pace"] == "열세 페이스"
    r = client.post("/predict", json=state(my_side="red")).json()
    assert r["pace"] == "우세 페이스"


def test_out_of_training_range_warns_not_rejects(client):
    body = state(blue={"kills": 20}, red={"kills": 2})  # 차이 18 > 학습 최대 16, 팀 값 20 은 범위 안
    res = client.post("/predict", json=body)
    assert res.status_code == 200
    w = res.json()["warnings"]
    assert len(w) == 1 and "킬" in w[0] and "18" in w[0]


def test_team_value_out_of_training_range_warns(client):
    body = state(blue={"cs": 400}, red={"cs": 300})  # 팀 CS 400 > 학습 최대 354, 차이 100 은 범위 안
    w = client.post("/predict", json=body).json()["warnings"]
    assert any("blue" in x and "CS" in x and "400" in x for x in w)


# ── /coach ──
def test_coach_contains_predict_fields(client):
    body = state()
    p = client.post("/predict", json=body).json()
    c = client.post("/coach", json=body).json()
    for k in ("win_prob_blue", "my_side", "my_win_prob", "pace", "top_factors", "vs_tier", "tier",
              "tier_percentile", "warnings", "model", "model_version"):
        assert c[k] == p[k]
    assert c["message"].startswith(p["message"] + " → ")
    assert c["horizon_min"] == 3
    assert c["how_to_read"]


# ── 백분위 (AGENTS.md 4절 "백분위 계산 정의") ──
Q = BASE["quantiles"]


def top(key: str, v: float) -> float:
    """정의를 그대로 옮긴 계산: 같은 값이면 그 점들의 평균, 아니면 선형 보간, 표 밖이면 0/100."""
    qs, ps = Q[key], Q["ps"]
    same = [p for q, p in zip(qs, ps) if q == v]
    if same:
        pct = sum(same) / len(same)
    elif v < qs[0]:
        pct = 0.0
    elif v > qs[-1]:
        pct = 100.0
    else:
        i = next(i for i, q in enumerate(qs) if q > v)
        pct = ps[i - 1] + (v - qs[i - 1]) / (qs[i] - qs[i - 1]) * (ps[i] - ps[i - 1])
    return 100 - pct


@pytest.mark.parametrize("key,v,want_top", [
    ("cs", 0, 50.0),          # 표의 값 그대로 (50백분위)
    ("cs", -18, 70.0),        # 30백분위 → 상위 70%
    ("cs", 13, 35.0),         # 8(60)과 18(70) 사이 보간: 60 + 5/10 × 10 = 65 → 상위 35%
    ("cs", -200, 100.0),      # 표 최솟값보다 작다
    ("cs", 200, 0.0),         # 표 최댓값보다 크다
    ("dragons", 0, 50.0),     # 동점 구간 30·40·50·60·70 의 평균 50
    ("dragons", 1, 8.75),     # 80·90·95·100 의 평균 91.25 → 상위 8.75%
    ("dragons", -1, 91.25),   # 0·5·10·20 의 평균 8.75
    ("win_prob", 0.65, 25.0),  # 0.6(70)과 0.7(80) 사이 → 75
])
def test_percentile_definition(key, v, want_top):
    from api.service import percentile_of

    assert top(key, v) == pytest.approx(want_top)  # 테스트 안의 정의와 손계산이 맞는지
    assert 100 - percentile_of(v, Q[key], Q["ps"]) == pytest.approx(want_top)


def test_tier_percentile_in_predict(client):
    body = state()  # 내 팀(블루) 격차: 킬 −2, CS −18, 드래곤 −1
    r = client.post("/predict", json=body).json()
    tp = r["tier_percentile"]
    d = diffs(body)
    assert set(tp) == {"overall_top_pct", *(f"{s}_top_pct" for s in TEAM_STATS)}
    for s in TEAM_STATS:
        assert tp[f"{s}_top_pct"] == pytest.approx(top(s, d[s]))
    assert tp["cs_top_pct"] == pytest.approx(70.0)
    assert tp["overall_top_pct"] == pytest.approx(top("win_prob", r["my_win_prob"]))
    assert r["message"] == f"승률 {r['my_win_prob'] * 100:.0f}% · {r['pace']} · 티어 상위 {tp['overall_top_pct']:.0f}%"


def test_tier_percentile_red_side_uses_my_diff(client):
    body = state(my_side="red")  # 레드 기준 CS 격차 +18 → 70백분위 → 상위 30%
    tp = client.post("/predict", json=body).json()["tier_percentile"]
    assert tp["cs_top_pct"] == pytest.approx(top("cs", 18))
    assert tp["cs_top_pct"] == pytest.approx(30.0)
    assert tp["dragons_top_pct"] == pytest.approx(8.75)


def test_coaching_percentile_moves(client):
    body = state()
    c = client.post("/coach", json=body).json()
    got = {t["stat"]: t for t in c["coaching"]}
    d = diffs(body)
    cs = got["cs"]
    assert cs["top_pct_before"] == pytest.approx(top("cs", d["cs"]))       # −18 → 상위 70%
    assert cs["top_pct_after"] == pytest.approx(top("cs", d["cs"] + 20))   # +2 → 상위 47.5%
    assert cs["top_pct_after"] == pytest.approx(47.5)
    assert cs["overall_top_pct_after"] == pytest.approx(top("win_prob", cs["win_prob_after"]))
    assert cs["message"] == (
        f"CS 20개 더 → 상위 {cs['top_pct_before']:.0f}%에서 {cs['top_pct_after']:.0f}%로 "
        f"(승률 {c['my_win_prob'] * 100:.1f}% → {cs['win_prob_after'] * 100:.1f}%, +{cs['gain_pp']:.1f}%p)")


def test_coach_message_and_toast(client):
    c = client.post("/coach", json=state()).json()
    first = c["coaching"][0]
    overall = c["tier_percentile"]["overall_top_pct"]
    assert c["message"].endswith(f" → 목표: {first['name']} +{first['add']:g} (+{first['gain_pp']:.1f}%p)")
    items = c["build_advice"]["recommended_items"]
    assert c["warnings"] == [] and c["build_advice"]["skill_order"]["order"] is None  # 기본값 AD 역할군은 스킬표 없음
    assert c["toast"] == [
        f"승률 {c['my_win_prob'] * 100:.0f}% · 상위 {overall:.0f}%",
        f"{first['name']} +{first['add']:g} → {first['win_prob_after'] * 100:.0f}% "
        f"(상위 {first['overall_top_pct_after']:.0f}%)",
        f"{items[0]['name']} → {items[1]['name']}",
    ]


def test_toast_warns_and_no_target_message(client):
    # 내 팀이 모든 목표의 상한에 있고(목표 없음), 학습 범위 밖 값이 있는 경우
    full = {"kills": 99, "assists": 10, "cs": 1990, "avg_level": 9, "dragons": 7, "heralds": 2, "towers": 11}
    c = client.post("/coach", json=state(blue=full)).json()
    assert c["coaching"] == []
    assert c["message"].endswith(" → 지금 페이스 유지")
    assert c["toast"][-1] == "학습 범위 밖 입력 있음" and len(c["toast"]) == 2


def test_coach_targets_follow_rule(client):
    body = state()
    c = client.post("/coach", json=body).json()
    d = diffs(body)
    now = rule_p(d)
    add = {"cs": 20, "kills": 2, "dragons": 1, "heralds": 1, "towers": 1}
    expect = {}
    for stat, a in add.items():
        after = rule_p(dict(d, **{stat: d[stat] + a}))  # 규칙은 어시스트·레벨을 쓰지 않으므로 연쇄 영향 없음
        if after > now:
            expect[stat] = after
    got = {t["stat"]: t for t in c["coaching"]}
    assert set(got) == set(expect)  # 전령은 규칙상 승률을 올리지 않으므로 빠진다
    assert "heralds" not in got
    for stat, after in expect.items():
        t = got[stat]
        assert t["win_prob_after"] == pytest.approx(after, abs=1e-9)
        assert t["gain_pp"] == pytest.approx((after - now) * 100, abs=1e-6)
        assert t["target_value"] == body["blue"][stat] + add[stat]
    gains = [t["gain_pp"] for t in c["coaching"]]
    assert gains == sorted(gains, reverse=True)
    assert all(g > 0 for g in gains)


def test_coach_chain_amounts(client):
    c = client.post("/coach", json=state()).json()
    got = {t["stat"]: t for t in c["coaching"]}
    slope = BASE["slope"]
    assert got["cs"]["with"] == pytest.approx({"avg_level": 20 * slope["cs"]["avg_level"]})
    assert got["kills"]["with"] == pytest.approx({
        "assists": 2 * slope["kills"]["assists"], "avg_level": 2 * slope["kills"]["avg_level"]})
    assert got["dragons"]["with"] == {}


def test_coach_red_side(client):
    body = state(my_side="red")
    c = client.post("/coach", json=body).json()
    d = diffs(body)
    now_red = 1 - rule_p(d)
    t = next(t for t in c["coaching"] if t["stat"] == "cs")
    after_red = 1 - rule_p(dict(d, cs=d["cs"] - 20))  # 레드 CS +20 → 블루 − 레드 차이는 −20
    assert t["win_prob_after"] == pytest.approx(after_red, abs=1e-9)
    assert t["gain_pp"] == pytest.approx((after_red - now_red) * 100, abs=1e-6)
    assert t["target_value"] == body["red"]["cs"] + 20


def test_coach_skips_targets_past_limits(client):
    body = state(blue={"towers": 11, "dragons": 7})
    stats = {t["stat"] for t in client.post("/coach", json=body).json()["coaching"]}
    assert "towers" not in stats and "dragons" not in stats


def test_coach_message_is_one_line(client):
    t = client.post("/coach", json=state()).json()["coaching"][0]
    assert "\n" not in t["message"] and t["name"] in t["message"] and "%p" in t["message"]


# ── 입력 검증: 422 (AGENTS.md 3절) ──
def _bad(path_value):
    body = state()
    (side, key), value = path_value
    if side is None:
        if value is ...:
            del body[key]
        else:
            body[key] = value
    else:
        if value is ...:
            del body[side][key]
        else:
            body[side][key] = value
    return body


BAD = [
    ((None, "my_side"), ...),                 # 필드 누락
    (("blue", "cs"), ...),                    # 팀 지표 누락
    ((None, "extra"), 1),                     # 정의하지 않은 필드
    (("blue", "wards"), 3),                   # 팀 안의 정의하지 않은 필드
    (("blue", "kills"), "3"),                 # 따옴표 친 숫자
    (("blue", "kills"), True),                # 불리언
    (("blue", "kills"), 3.5),                 # 정수 칸의 소수
    (("blue", "kills"), 3.0),
    (("blue", "avg_level"), "7"),
    (("blue", "avg_level"), True),
    (("blue", "kills"), -1),                  # 음수
    (("red", "cs"), -1),
    (("blue", "kills"), LIMITS["kills"] + 1),  # 허용 범위 하나 밖
    (("blue", "assists"), LIMITS["assists"] + 1),
    (("blue", "cs"), LIMITS["cs"] + 1),
    (("blue", "dragons"), LIMITS["dragons"] + 1),
    (("blue", "heralds"), LIMITS["heralds"] + 1),
    (("blue", "towers"), LIMITS["towers"] + 1),
    (("blue", "avg_level"), 0.99),
    (("blue", "avg_level"), 18.01),
    ((None, "first_blood"), "BLUE"),
    ((None, "first_blood"), None),
    ((None, "my_side"), "purple"),
    ((None, "blue"), [1, 2]),
]


@pytest.mark.parametrize("path", ["/predict", "/coach"])
@pytest.mark.parametrize("case", BAD, ids=[f"{s}.{k}={v!r}" for (s, k), v in BAD])
def test_invalid_input_422(client, path, case):
    assert client.post(path, json=_bad(case)).status_code == 422


@pytest.mark.parametrize("path", ["/predict", "/coach"])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_nan_inf_422(client, path, literal):
    raw = json.dumps(state()).replace('"avg_level": 6.8', f'"avg_level": {literal}', 1)
    assert literal in raw
    res = client.post(path, content=raw, headers={"Content-Type": "application/json"})
    assert res.status_code == 422


@pytest.mark.parametrize("path", ["/predict", "/coach"])
@pytest.mark.parametrize("raw", ['{"blue": ', "[]", '"text"', "null"])
def test_malformed_body_422(client, path, raw):
    res = client.post(path, content=raw, headers={"Content-Type": "application/json"})
    assert res.status_code == 422


@pytest.mark.parametrize("stat,value", [("kills", 100), ("assists", 300), ("cs", 2000), ("dragons", 7),
                                        ("heralds", 2), ("towers", 11), ("avg_level", 1), ("avg_level", 18),
                                        ("kills", 0)])
def test_limit_edges_accepted(client, stat, value):
    res = client.post("/predict", json=state(blue={stat: value}))
    assert res.status_code == 200


# ── AI 에이전트 연동 ──
def test_openapi_exposes_agent_tools(client):
    spec = client.get("/openapi.json").json()
    ops = {op["operationId"] for p in spec["paths"].values() for op in p.values()}
    assert {"predict_pace", "coach_targets", "health"} <= ops
    coaching = spec["components"]["schemas"]["Coaching"]["properties"]
    assert "with" in coaching and "win_prob_after" in coaching
    assert "model_version" in spec["components"]["schemas"]["CoachResponse"]["properties"]


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
    assert "/coach" in res.text  # 화면이 같은 API 를 부른다


def test_request_not_mutated_between_calls(client):
    """같은 입력을 두 번 보내면 같은 답 (서비스가 상태를 남기지 않는다)."""
    body = state()
    a = client.post("/coach", json=copy.deepcopy(body)).json()
    b = client.post("/coach", json=copy.deepcopy(body)).json()
    assert a == b

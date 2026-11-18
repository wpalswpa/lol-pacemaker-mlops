"""scripts/benchmark_serving.py 계산 검사 (AGENTS.md 9절 "서빙 부하 실측"). 기대값은 정의에서 손으로 계산했다."""
import importlib.util
import json
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "benchmark_serving.py"
_spec = importlib.util.spec_from_file_location("benchmark_serving", _PATH)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


# 정렬하면 10 20 30 40 50. 위치 = (5-1) × q/100
@pytest.mark.parametrize("q, want", [
    (0, 10), (50, 30),      # 위치 0, 2
    (95, 48),               # 위치 3.8 → 40 + 10 × 0.8
    (99, 49.6),             # 위치 3.96 → 40 + 10 × 0.96
    (100, 50),
])
def test_percentile_linear_interpolation(q, want):
    assert bs.percentile([40, 10, 50, 20, 30], q) == pytest.approx(want)


def test_percentile_one_to_hundred():
    xs = [float(i) for i in range(100, 0, -1)]          # 1..100 을 거꾸로
    assert bs.percentile(xs, 50) == pytest.approx(50.5)  # 위치 49.5 → 50 과 51 사이
    assert bs.percentile(xs, 95) == pytest.approx(95.05)  # 위치 94.05 → 95 + 0.05
    assert bs.percentile(xs, 99) == pytest.approx(99.01)  # 위치 98.01 → 99 + 0.01


def test_percentile_edges():
    assert bs.percentile([], 50) is None
    assert bs.percentile([7.0], 99) == 7.0
    assert bs.percentile([0, 100], 95) == pytest.approx(95)
    with pytest.raises(ValueError):
        bs.percentile([1, 2], 101)


def test_summarize_counts_failures_and_rps():
    s = bs.summarize([(True, 10), (True, 20), (False, 30), (True, 40)], elapsed_s=2.0)
    assert (s["total"], s["ok"], s["failed"]) == (4, 3, 1)
    assert s["success_rate"] == 75.0
    assert s["p50_ms"] == 25.0        # 위치 1.5 → 20 과 30 사이 (실패한 30ms 도 지연에 넣는다)
    assert s["p95_ms"] == 38.5        # 위치 2.85 → 30 + 10 × 0.85
    assert s["mean_ms"] == 25.0 and s["max_ms"] == 40
    assert s["rps"] == 2.0            # 4건 / 2초


def test_summarize_empty():
    s = bs.summarize([], elapsed_s=0.0)
    assert s["total"] == 0 and s["success_rate"] is None and s["p50_ms"] is None and s["rps"] is None


@pytest.mark.parametrize("url, want", [
    ("http://127.0.0.1:8083/", "http://127.0.0.1:8083"),
    ("http://localhost:8000", "http://localhost:8000"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("https://example.com", "<원격 서버>"),
    ("http://lol.example.org:8080", "<원격 서버>"),
])
def test_mask_target(url, want):
    assert bs.mask_target(url) == want


def test_benchmark_phases_with_fake_sender(monkeypatch):
    calls = []

    def send(path):
        calls.append(path)
        return path == "/predict", 5.0   # /predict 는 모두 성공, /coach 는 모두 실패

    monkeypatch.setattr(bs, "health", lambda url, timeout: {"model": "onnx", "model_version": "m20261118-2"})
    rep = bs.benchmark("http://127.0.0.1:1", requests=7, concurrency=3, warmup=2, send=send)
    assert len(calls) == 2 * (2 + 7 + 7)                # 엔드포인트 2개 × (예열 2 + 순차 7 + 동시 7)
    rows = [(r["endpoint"], r["mode"], r["concurrency"], r["total"], r["ok"], r["failed"]) for r in rep["results"]]
    assert rows == [
        ("POST /predict", "순차", 1, 7, 7, 0), ("POST /predict", "동시", 3, 7, 7, 0),
        ("POST /coach", "순차", 1, 7, 0, 7), ("POST /coach", "동시", 3, 7, 0, 7),
    ]
    assert rep["target"] == "http://127.0.0.1:1"
    assert "모델 판 m20261118-2" in bs.table(rep)


def test_main_writes_json_and_exit_code(monkeypatch, tmp_path):
    fake = {"generated_at": "t", "target": "<원격 서버>", "health": None,
            "settings": {"requests": 1, "concurrency": 1, "warmup": 0, "timeout_s": 1},
            "results": [{"endpoint": "POST /predict", "mode": "순차", "concurrency": 1,
                         **bs.summarize([(False, 1.0)], 1.0)}]}
    monkeypatch.setattr(bs, "benchmark", lambda *a, **k: fake)
    out = tmp_path / "b.json"
    assert bs.main(["--url", "https://example.com", "--out", str(out)]) == 1   # 실패가 있으면 1
    assert json.loads(out.read_text(encoding="utf-8"))["results"][0]["failed"] == 1


def test_body_is_valid_contract_input():
    from api.contract import GameState
    GameState(**bs.BODY)

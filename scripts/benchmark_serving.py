"""서빙 지연시간·부하 실측 (AGENTS.md 9절 "서빙 부하 실측").

POST /predict, POST /coach 에 순차 N건과 동시 C 스레드 N건을 보내 성공률, 실패 건수, p50/p95/p99(ms), RPS 를 잰다.
표준 라이브러리만 쓴다(서버에 학습 라이브러리가 없어도, 어느 PC 에서든 돈다).

  uv run python scripts/benchmark_serving.py --url http://127.0.0.1:8083 --requests 100 --concurrency 20
  uv run python scripts/benchmark_serving.py --url https://<내 도메인> --out docs/benchmark_summary.json
"""
import argparse
import json
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ENDPOINTS = ("/predict", "/coach")
# AGENTS.md 3절 예시 입력 (고정)
BODY = {
    "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0},
    "red": {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0},
    "first_blood": "red",
    "my_side": "blue",
}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


# ── 계산 (테스트 대상) ──
def percentile(values: list[float], q: float) -> float | None:
    """선형 보간 백분위. 정렬한 값의 (n-1)*q/100 위치를 앞뒤 값 사이에서 보간한다(numpy 기본과 같다)."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError(f"q 는 0~100: {q}")
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summarize(samples: list[tuple[bool, float]], elapsed_s: float) -> dict:
    """samples: (성공 여부, 지연 ms). 성공·실패 모두 지연시간에 넣는다."""
    lat = [ms for _, ms in samples]
    total = len(samples)
    ok = sum(1 for good, _ in samples if good)
    r = lambda v: None if v is None else round(v, 3)  # noqa: E731
    return {
        "total": total,
        "ok": ok,
        "failed": total - ok,
        "success_rate": round(ok / total * 100, 2) if total else None,
        "p50_ms": r(percentile(lat, 50)),
        "p95_ms": r(percentile(lat, 95)),
        "p99_ms": r(percentile(lat, 99)),
        "mean_ms": r(sum(lat) / total) if total else None,
        "max_ms": r(max(lat)) if lat else None,
        "rps": round(total / elapsed_s, 2) if total and elapsed_s > 0 else None,
        "elapsed_s": round(elapsed_s, 3),
    }


def mask_target(url: str) -> str:
    """127.0.0.1·localhost 가 아니면 주소를 적지 않는다 (AGENTS.md 1절)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    return url.rstrip("/") if host in LOCAL_HOSTS else "<원격 서버>"


# ── 측정 ──
Send = Callable[[str], tuple[bool, float]]


def make_sender(base: str, timeout: float) -> Send:
    data = json.dumps(BODY).encode()

    def send(path: str) -> tuple[bool, float]:
        req = urllib.request.Request(base.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                res.read()
                good = res.status == 200
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            good = False
        return good, (time.perf_counter() - t0) * 1000

    return send


def run_phase(send: Send, path: str, n: int, concurrency: int) -> dict:
    t0 = time.perf_counter()
    if concurrency <= 1:
        samples = [send(path) for _ in range(n)]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            samples = list(pool.map(lambda _: send(path), range(n)))
    return summarize(samples, time.perf_counter() - t0)


def health(base: str, timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/health", timeout=timeout) as r:
            h = json.load(r)
        return {k: h.get(k) for k in ("status", "model", "app_version", "model_version")}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def benchmark(url: str, requests: int, concurrency: int, warmup: int = 5, timeout: float = 10.0,
              send: Send | None = None) -> dict:
    send = send or make_sender(url, timeout)
    results = []
    for path in ENDPOINTS:
        for _ in range(warmup):
            send(path)
        results.append({"endpoint": f"POST {path}", "mode": "순차", "concurrency": 1,
                        **run_phase(send, path, requests, 1)})
        results.append({"endpoint": f"POST {path}", "mode": "동시", "concurrency": concurrency,
                        **run_phase(send, path, requests, concurrency)})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": mask_target(url),
        "health": health(url, timeout),
        "settings": {"requests": requests, "concurrency": concurrency, "warmup": warmup, "timeout_s": timeout},
        "results": results,
    }


def _w(s: str) -> int:
    """터미널 칸 수 (한글은 두 칸)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int, right: bool) -> str:
    gap = " " * (w - _w(s))
    return gap + s if right else s + gap


def table(report: dict) -> str:
    cols = ("endpoint", "mode", "total", "ok", "failed", "success_rate", "p50_ms", "p95_ms", "p99_ms", "rps")
    head = ("대상", "방식", "건수", "성공", "실패", "성공률%", "p50 ms", "p95 ms", "p99 ms", "RPS")
    rows = [head] + [tuple("-" if r[c] is None else (f"{r[c]}" if c in ("endpoint", "mode") else f"{r[c]:g}")
                           for c in cols) for r in report["results"]]
    widths = [max(_w(row[i]) for row in rows) for i in range(len(cols))]
    line = lambda row: "  ".join(_pad(v, w, i > 1) for i, (v, w) in enumerate(zip(row, widths)))  # noqa: E731
    h = report["health"] or {}
    out = [f"대상 {report['target']} · 모델 {h.get('model', '?')} · 모델 판 {h.get('model_version', '?')} · "
           f"요청 {report['settings']['requests']}건, 동시 {report['settings']['concurrency']}", line(rows[0]),
           "  ".join("-" * w for w in widths)]
    return "\n".join(out + [line(r) for r in rows[1:]])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ONNX 서빙 지연시간·부하 실측")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--requests", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--out", default="docs/benchmark_summary.json")
    a = ap.parse_args(argv)
    if a.requests < 1 or a.concurrency < 1:
        ap.error("--requests, --concurrency 는 1 이상")
    report = benchmark(a.url, a.requests, a.concurrency, a.warmup, a.timeout)
    print(table(report))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n저장: {a.out}")
    return 0 if all(r["failed"] == 0 for r in report["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())

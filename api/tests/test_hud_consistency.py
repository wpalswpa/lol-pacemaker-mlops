"""2026-11-23 실게임 HUD 일관성·가독성·검증기·코어 후속 빌드 테스트 (AGENTS.md 3-4·4절).

기대값은 AGENTS.md 계약과 고정 자료(client/tests/fixtures/live_bot_0300.json)에서 손으로 계산한다.
"""
from __future__ import annotations

import copy
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from api import llm_coach
from client.bridge import extract_live_context

ROOT = Path(__file__).resolve().parents[2]
BOT_FIXTURE = ROOT / "client" / "tests" / "fixtures" / "live_bot_0300.json"


def _load_bot_1v5_ctx() -> dict:
    raw = json.loads(BOT_FIXTURE.read_text(encoding="utf-8"))
    return extract_live_context(raw)


# ── P0-1: 골드 기준(gold_basis) 통일 ──
def test_p0_1_asymmetric_1v5_uses_single_per_player_gold_basis(client):
    """손계산: 다리우스(1명) 450+50=500G, 봇 5명 450+0+400+475+400=1,725G → 1인당 500 vs 345, 차 +155G."""
    ctx = _load_bot_1v5_ctx()
    res = client.post("/strategy", json=ctx)
    assert res.status_code == 200, res.text
    out = res.json()

    assert out["gold_basis"] == "per_player"
    assert out["team_gold"] == {"ally": 500, "enemy": 345}
    assert out["team_gold_diff"] == 155
    assert out["team_gold"]["ally"] - out["team_gold"]["enemy"] == out["team_gold_diff"]


def test_p0_1_symmetric_5v5_uses_team_gold_basis(client):
    """5대5 대칭 게임은 팀 합계(team) 기준을 사용한다."""
    from api.tests.test_strategy import _base_live

    res = client.post("/strategy", json=_base_live(900.0))
    assert res.status_code == 200, res.text
    out = res.json()

    # 아군: 2800+3300+3800+5300+2500=17700, 상대: 4800+3200+3400+3000+2100=16500, 차 +1200
    assert out["gold_basis"] == "team"
    assert out["team_gold"] == {"ally": 17700, "enemy": 16500}
    assert out["team_gold_diff"] == 1200


# ── P0-2: 비어 있는 라인 unpaired + 아군 1명/비대칭에서 열세 복구·합류·아군 금지 ──
def test_p0_2_unpaired_lanes_and_no_ally_or_recovery_advice_in_1v5(client):
    ctx = _load_bot_1v5_ctx()
    out = client.post("/strategy", json=ctx).json()

    by_pos = {ln["position"]: ln for ln in out["lanes"]}
    for empty_pos in ("TOP", "JUNGLE", "BOTTOM", "UTILITY"):
        assert by_pos[empty_pos]["status"] == "unpaired"
    assert by_pos["MIDDLE"]["status"] in ("ahead", "even", "behind")
    assert out["ally_carry"] is None

    cats = [s["category"] for s in out["strategies"]]
    for forbidden_cat in ("열세 복구", "전 라인 운영", "스노우볼"):
        assert forbidden_cat not in cats
    for s in out["strategies"]:
        blob = f"{s['category']} {s['title']} {s['detail']}"
        assert "열세 복구" not in blob
        assert "합류" not in blob
        assert "아군" not in blob


# ── P0-3: dead 필드 추가 & 비대칭 수적 우위·열세는 사망 수 차이로만 ──
def test_p0_3_dead_counts_and_asymmetric_death_diff(client):
    # live_bot_0300.json 에서는 애니 1명 사망, 다리우스 생존 → dead={ally:0, enemy:1}
    ctx = _load_bot_1v5_ctx()
    out = client.post("/strategy", json=ctx).json()
    assert out["dead"] == {"ally": 0, "enemy": 1}
    assert all("1v5" not in f and "1v4" not in f for f in out["key_factors"])

    # 아무도 안 죽은 1v5 상황 → dead={0,0}, 수성 경보 없음, 1v5 생존 칩 없음
    alive_ctx = copy.deepcopy(ctx)
    for e in alive_ctx["enemies"]:
        e["is_dead"] = False
        e["respawn_timer"] = 0.0
    out_alive = client.post("/strategy", json=alive_ctx).json()
    assert out_alive["dead"] == {"ally": 0, "enemy": 0}
    assert all("생존" not in f for f in out_alive["key_factors"])
    assert "수성 경보" not in {s["category"] for s in out_alive["strategies"]}


# ── P0-4: 브리핑 검사기 (마크다운 제거, 단위별 숫자 대조, 아이템 능력치 태그) ──
def test_p0_4_strip_markdown_and_real_hallucination_cases(client):
    # 실제 사례 1: live_bot_0300.json (game_time_min=3.0, kills.ally=1 이 있어도 3초·1명은 단위 불일치로 거절)
    ctx = _load_bot_1v5_ctx()
    strat = client.post("/strategy", json=ctx).json()
    pl = llm_coach.build_strategy_prompt_payload(strat)

    case1 = "다리우스의 미드 라인에서 애니를 가로막아 킬 기회를 잡고, 3초 내에 1명 이상의 상대를 제압하라."
    r1 = llm_coach.validate_strategy_briefing(case1, pl)
    assert r1 is not None and r1.startswith("number:")

    # 3초만 빼고 "1명 이상"만 남겨도 명 단위가 payload 에 없으므로 거절
    case1_people = "다리우스의 미드 라인에서 애니를 가로막아 1명 이상의 상대를 제압하라."
    r1p = llm_coach.validate_strategy_briefing(case1_people, pl)
    assert r1p is not None and r1p.startswith("number:")

    # 실제 사례 2: 마크다운 **곡괭이** 제거 후 곡괭이(공격력) ≠ 방어력 불일치 거절
    pl2 = {
        "game_time_min": 3.0,
        "win_prob_pct": 52.0,
        "team_gold_diff": 55,
        "recall_advice": "보유 726G · 다음 목표 [판금 장화](남은 비용 1,200G)까지 474G",
        "priority_strategies": [{"category": "라인전 기본", "title": "초반 라인전 주도권", "reason": "3분"}],
        "recommended_items": ["판금 장화", "곡괭이", "칠흑의 양날 도끼"],
    }
    case2 = "3분 내에 474G 소모 후, **곡괭이**로 방어력을 확보."
    r2 = llm_coach.validate_strategy_briefing(case2, pl2)
    assert r2 is not None and r2.startswith("item_stat:")

    # 올바른 짝(판금 장화=방어력, 곡괭이=공격력) + 마크다운은 통과
    good = "**판금 장화**로 방어력을 챙기고 474G 모아 귀환하세요."
    assert llm_coach.validate_strategy_briefing(good, pl2) is None
    assert llm_coach.strip_markdown("**곡괭이** `방어` # 제목\n- 항목") == "곡괭이 방어 제목\n항목"


# ── P1-5: 14자 이내 short 필드 ──
def test_p1_5_short_fields_within_14_chars(client):
    ctx = _load_bot_1v5_ctx()
    out = client.post("/strategy", json=ctx).json()
    assert 1 <= len(out["tactical_badge_short"]) <= 14
    assert 1 <= len(out["recall_advice"]["short"]) <= 14
    assert 1 <= len(out["build_advice"]["tactical_tip_short"]) <= 14


# ── 코어 완성 후 후속 코어 추천 (사용자 피드백) ──
def test_post_core_items_continue_recommending_next_cores(client):
    from api.tests.test_strategy import _base_live

    # 다리우스(전사)가 신발(3047) + 1코어(3071 칠흑의 양날 도끼)를 이미 뽑은 상황
    d = _base_live(1200.0)
    d["me"]["champion"] = "Darius"
    d["me"]["items"] = [3047, 3071]
    d["me"]["current_gold"] = 1400
    d["allies"][2]["champion"] = "Darius"
    d["allies"][2]["items"] = [3047, 3071]
    out1 = client.post("/strategy", json=d).json()
    rec_ids = [it["item_id"] for it in out1["build_advice"]["recommended_items"]]
    assert len(rec_ids) == 3
    # 이미 완성한 1코어 하위템(곡괭이 1037)을 다시 추천하지 않고, 2코어 이후 새 코어를 추천한다
    assert 1037 not in rec_ids
    assert out1["recall_advice"]["next_item"] is not None
    assert out1["recall_advice"]["next_item"] not in ("판금 장화", "칠흑의 양날 도끼", "곡괭이")

    # 2코어까지 뽑아도 다음 코어를 계속 추천한다
    next_id = next(it["item_id"] for it in out1["build_advice"]["recommended_items"] if it["item_id"] not in d["me"]["items"])
    d2 = copy.deepcopy(d)
    d2["me"]["items"] = [3047, 3071, next_id]
    d2["allies"][2]["items"] = list(d2["me"]["items"])
    out2 = client.post("/strategy", json=d2).json()
    assert out2["recall_advice"]["next_item"] is not None
    assert out2["recall_advice"]["next_item"] != out1["recall_advice"]["next_item"]


# ── P1-5·P0·P1·P2: Playwright 380×560 실렌더 검증 (데모 3개 + 1대5 고정 자료, 접힘/펼침) ──
def _find_playwright_python() -> str | None:
    try:
        import playwright.sync_api  # noqa: F401
        return sys.executable
    except Exception:
        pass
    candidates = [
        shutil.which("python"),
        shutil.which("python3"),
        r"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe",
    ]
    for cand in candidates:
        if not cand or not Path(cand).exists():
            continue
        r = subprocess.run([cand, "-c", "import playwright.sync_api"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return cand
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_chrome_binary() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if Path(path).exists():
            return path
    return None


def _run_hud_check_via_cdp(api_port: int, bridge_port: int, check_js: str) -> dict:
    """Playwright 미설치 환경(예: ubuntu-latest CI)에서 표준 라이브러리만으로 헤드리스 Chrome CDP 검사를 수행한다."""
    import base64
    import os
    import struct
    import tempfile
    import urllib.request
    from urllib.parse import urlparse

    chrome = _find_chrome_binary()
    assert chrome is not None, "Neither Playwright nor headless Chrome/Chromium binary is available"
    cdp_port = _free_port()
    user_dir = tempfile.mkdtemp(prefix="lol_cdp_")
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=380,560",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={user_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=1) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                page_target = next((t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
                if page_target:
                    ws_url = page_target["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.15)
        assert ws_url is not None, "Headless Chrome DevTools endpoint did not start"

        parsed = urlparse(ws_url)
        sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or cdp_port), timeout=15)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                f"GET {parsed.path} HTTP/1.1\r\n"
                f"Host: {parsed.hostname}:{parsed.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(req.encode("ascii"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += sock.recv(4096)
            extra = buf.split(b"\r\n\r\n", 1)[1]

            def recv_exact(n: int) -> bytes:
                nonlocal extra
                while len(extra) < n:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise EOFError("WebSocket closed")
                    extra += chunk
                out_b, extra = extra[:n], extra[n:]
                return out_b

            def ws_send(obj: dict) -> None:
                payload = json.dumps(obj).encode("utf-8")
                mask = os.urandom(4)
                header = bytearray([0x81])
                length = len(payload)
                if length < 126:
                    header.append(0x80 | length)
                elif length < 65536:
                    header.append(0x80 | 126)
                    header.extend(struct.pack("!H", length))
                else:
                    header.append(0x80 | 127)
                    header.extend(struct.pack("!Q", length))
                header.extend(mask)
                masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                sock.sendall(bytes(header) + masked)

            def ws_recv() -> dict:
                while True:
                    b0, b1 = recv_exact(2)
                    opcode = b0 & 0x0F
                    length = b1 & 0x7F
                    if length == 126:
                        length = struct.unpack("!H", recv_exact(2))[0]
                    elif length == 127:
                        length = struct.unpack("!Q", recv_exact(8))[0]
                    if b1 & 0x80:
                        m = recv_exact(4)
                        data = bytes(b ^ m[i % 4] for i, b in enumerate(recv_exact(length)))
                    else:
                        data = recv_exact(length)
                    if opcode == 0x01:
                        return json.loads(data.decode("utf-8"))

            msg_id = [0]

            def cdp(method: str, params: dict | None = None) -> dict:
                msg_id[0] += 1
                cid = msg_id[0]
                ws_send({"id": cid, "method": method, "params": params or {}})
                while True:
                    msg = ws_recv()
                    if msg.get("id") == cid:
                        return msg.get("result") or {}

            def eval_js(expr: str):
                res = cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
                return (res.get("result") or {}).get("value")

            def wait_ready() -> None:
                for _ in range(80):
                    p = eval_js("document.getElementById('prob') && document.getElementById('prob').textContent")
                    if p and p != "--%":
                        return
                    time.sleep(0.1)
                raise AssertionError("HUD did not finish rendering")

            cdp("Page.enable")
            cdp("Runtime.enable")
            cdp("Emulation.setDeviceMetricsOverride", {"width": 380, "height": 560, "deviceScaleFactor": 1, "mobile": False})

            out: dict = {}
            url_live = f"http://127.0.0.1:{api_port}/overlay?bridge_url=http://127.0.0.1:{bridge_port}/bridge/state"
            cdp("Page.navigate", {"url": url_live})
            wait_ready()
            time.sleep(0.3)
            out["live_collapsed"] = eval_js(f"({check_js})()")

            eval_js("document.getElementById('expandBtn').click()")
            time.sleep(0.2)
            out["live_expanded"] = eval_js(f"({check_js})()")

            cdp("Page.reload")
            wait_ready()
            time.sleep(0.2)
            out["live_reloaded_expanded"] = bool(eval_js("document.body.classList.contains('expanded')"))

            eval_js("localStorage.removeItem('lol_hud_expanded_v2')")
            cdp("Page.navigate", {"url": f"http://127.0.0.1:{api_port}/overlay?bridge_url=http://127.0.0.1:9/bridge/state"})
            wait_ready()
            time.sleep(0.3)
            for demo in ("ahead", "even", "behind"):
                eval_js(f"document.querySelector(\"[data-demo='{demo}']\").click()")
                time.sleep(0.35)
                if eval_js("document.body.classList.contains('expanded')"):
                    eval_js("document.getElementById('expandBtn').click()")
                    time.sleep(0.15)
                out[f"demo_{demo}_collapsed"] = eval_js(f"({check_js})()")
                eval_js("document.getElementById('expandBtn').click()")
                time.sleep(0.15)
                out[f"demo_{demo}_expanded"] = eval_js(f"({check_js})()")
            return out
        finally:
            sock.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(user_dir, ignore_errors=True)


def test_playwright_hud_no_truncation_and_consistency_380x560(app_module):
    """380×560에서 데모 3개 + 1대5 고정 자료(접힘/펼침)의 모든 .hud-card 요소가 scrollWidth <= clientWidth."""
    import uvicorn
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    pw_py = _find_playwright_python()

    # 1) 실제 FastAPI 서버 임시 기동
    api_port = _free_port()
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=api_port, log_level="error")
    server = uvicorn.Server(config)
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()

    # 2) 1대5 고정 자료 브리지 스냅샷 준비
    from fastapi.testclient import TestClient

    bot_ctx = _load_bot_1v5_ctx()
    with TestClient(app_module.app) as tc:
        bot_strat = tc.post("/strategy", json=bot_ctx).json()

    bridge_state = {
        "connected": True,
        "in_game": True,
        "game_time_sec": bot_ctx["game_time_sec"],
        "level": bot_ctx["me"]["level"],
        "next_skill": "E",
        "next_skill_when": "next_level",
        "owned_items": [{"itemID": i} for i in bot_ctx["me"]["items"]],
        "live_context": bot_ctx,
        "strategy": bot_strat,
        "strategy_error": None,
        "hud_window": {"click_through": False, "lol_detected": True, "lol_window_mode_warning": False},
    }

    bridge_port = _free_port()

    class BridgeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(bridge_state, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    bridge_httpd = ThreadingHTTPServer(("127.0.0.1", bridge_port), BridgeHandler)
    threading.Thread(target=bridge_httpd.serve_forever, daemon=True).start()

    # 서버 준비 대기
    import urllib.request
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{api_port}/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.1)

    check_js = """() => {
  const bad = [];
  for (const card of document.querySelectorAll('.hud-card')) {
    if (card.hidden || card.offsetParent === null) continue;
    for (const el of [card, ...card.querySelectorAll('*')]) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || el.hidden || el.offsetParent === null) continue;
      if (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1) {
        bad.push({
          tag: el.tagName, id: el.id, cls: el.className,
          sw: el.scrollWidth, cw: el.clientWidth,
          text: (el.textContent || '').trim().slice(0, 50)
        });
      }
    }
  }
  const rect = document.body.getBoundingClientRect();
  return {
    bad,
    bodyH: Math.ceil(document.body.scrollHeight),
    bodyW: Math.ceil(rect.width),
    briefHidden: document.getElementById('briefBox') ? document.getElementById('briefBox').hidden : true,
    text: document.body.innerText,
    goldChip: document.getElementById('goldChip') ? document.getElementById('goldChip').textContent : '',
    goldBasis: document.getElementById('goldBasisTag') ? document.getElementById('goldBasisTag').textContent : '',
    diffGold: document.getElementById('diffGoldTxt') ? document.getElementById('diffGoldTxt').textContent : '',
    deathChipHidden: document.getElementById('deathChip') ? document.getElementById('deathChip').hidden : true,
    deathChipText: document.getElementById('deathChip') ? document.getElementById('deathChip').textContent : '',
  };
}"""

    check_script = f"""
import json, sys
from playwright.sync_api import sync_playwright

api_port = int(sys.argv[1])
bridge_port = int(sys.argv[2])
CHECK_JS = {check_js!r}

out = {{}}
with sync_playwright() as p:
    try:
        browser = p.chromium.launch(headless=True)
    except Exception:
        browser = p.chromium.launch(headless=True, channel="chrome")
    page = browser.new_page(viewport={{"width": 380, "height": 560}})

    url_live = f"http://127.0.0.1:{{api_port}}/overlay?bridge_url=http://127.0.0.1:{{bridge_port}}/bridge/state"
    page.goto(url_live, wait_until="networkidle")
    page.wait_for_function("document.getElementById('prob').textContent !== '--%'", timeout=8000)
    page.wait_for_timeout(300)
    out["live_collapsed"] = page.evaluate(CHECK_JS)

    page.click("#expandBtn")
    page.wait_for_timeout(200)
    out["live_expanded"] = page.evaluate(CHECK_JS)

    page.reload(wait_until="networkidle")
    page.wait_for_function("document.getElementById('prob').textContent !== '--%'", timeout=8000)
    out["live_reloaded_expanded"] = page.evaluate("document.body.classList.contains('expanded')")

    page.evaluate("localStorage.removeItem('lol_hud_expanded_v2')")
    page.goto(f"http://127.0.0.1:{{api_port}}/overlay?bridge_url=http://127.0.0.1:9/bridge/state", wait_until="networkidle")
    page.wait_for_function("document.getElementById('prob').textContent !== '--%'", timeout=8000)
    for demo in ("ahead", "even", "behind"):
        page.click(f"[data-demo='{{demo}}']")
        page.wait_for_timeout(350)
        if page.evaluate("document.body.classList.contains('expanded')"):
            page.click("#expandBtn")
            page.wait_for_timeout(150)
        out[f"demo_{{demo}}_collapsed"] = page.evaluate(CHECK_JS)
        page.click("#expandBtn")
        page.wait_for_timeout(150)
        out[f"demo_{{demo}}_expanded"] = page.evaluate(CHECK_JS)

    browser.close()

print(json.dumps(out, ensure_ascii=False))
"""
    try:
        if pw_py is not None:
            proc = subprocess.run(
                [pw_py, "-c", check_script, str(api_port), str(bridge_port)],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            assert proc.returncode == 0, f"Playwright failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            data = json.loads(proc.stdout)
        else:
            data = _run_hud_check_via_cdp(api_port, bridge_port, check_js)
    finally:
        server.should_exit = True
        bridge_httpd.shutdown()
        api_thread.join(timeout=3)

    # 모든 상태에서 .hud-card 내부 잘림 0건 & 비LLM 브리핑 숨김
    for key, snap in data.items():
        if not isinstance(snap, dict):
            continue
        assert snap["bad"] == [], f"{key} has truncated elements: {snap['bad']}"
        assert snap["briefHidden"] is True, f"{key} should hide non-LLM briefing box"

    # 기본 접힘 화면은 컴팩트(<= 230px)
    assert data["live_collapsed"]["bodyH"] <= 230, f"Collapsed HUD too tall: {data['live_collapsed']['bodyH']}"
    assert data["live_reloaded_expanded"] is True

    # 1대5 고정 자료 일관성 검증
    live_exp = data["live_expanded"]
    assert "1인당 +155G" in live_exp["goldChip"]
    assert "1인당 평균" in live_exp["goldBasis"]
    assert "블루 +155G" in live_exp["diffGold"]
    assert live_exp["deathChipHidden"] is False and "상대 사망 1" in live_exp["deathChipText"]
    assert "열세 복구" not in live_exp["text"]
    assert "합류" not in live_exp["text"]
    assert "S·1순위" not in live_exp["text"] and "A·2순위" not in live_exp["text"]

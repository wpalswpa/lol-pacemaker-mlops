"""로컬 게임 API → 페이스메이커 API 브리지 (AGENTS.md 3-1·3-2절).

쓰는 곳: 관전·리플레이·연습 모드. 랭크 게임 중에는 쓰지 않는다.
읽는 것: Riot 공식 로컬 API https://127.0.0.1:2999/liveclientdata/allgamedata 의 점수판과 이벤트만.

변환 규칙 (응답 필드 → 입력 계약)
  팀       allPlayers[].team  "ORDER" = blue, "CHAOS" = red
  kills    팀 선수 scores.kills 합       assists  scores.assists 합
  cs       팀 선수 scores.creepScore 합  avg_level 팀 선수 level 평균
  dragons  events 의 DragonKill, KillerName 이 그 팀 선수인 수
  heralds  events 의 HeraldKill, KillerName 이 그 팀 선수인 수
  towers   events 의 TurretKilled. 포탑 이름 Turret_T1_* 는 블루 포탑이므로 레드가 부순 것, T2 는 블루가 부순 것
  first_blood  events 의 FirstBlood Recipient 의 팀. 아직 없으면 내 상대 팀으로 둔다(내 승률을 낮게 잡는 쪽)
  my_side  activePlayer 이름과 같은 선수의 팀. 관전처럼 activePlayer 가 없으면 --side 로 준다
  선수 이름은 riotId, riotIdGameName, summonerName 중 무엇으로 와도 맞춘다.
  my_champion       activePlayer 와 같은 선수의 챔피언. --side 로 팀을 정했으면(관전) 넣지 않는다
  enemy_top_threat  상대 팀 최다 킬 선수의 챔피언. 동률이면 최다 CS, 그래도 같으면 목록 순서
  챔피언 이름       rawChampionName "game_character_displayname_<영문 ID>" 의 영문 ID, 없으면 championName.
                    둘 다 없으면 그 칸을 넣지 않는다(서버 기본값). /coach 가 422 면 두 칸을 빼고 한 번 더 부른다
참고 값 item_gold: 팀 아이템 가격 합(Data Dragon item.json 의 gold.total, 없으면 응답의 price). 로그에만 남기고
  모델 입력으로 쓰지 않는다(학습 데이터의 총골드와 정의가 다르다).

실제 게임 클라이언트의 응답으로는 아직 확인하지 않았다. 테스트는 공개 문서의 필드 구조로 만든 고정 자료로 한다.

알림 켜고 끄기 (실행 옵션이 환경 변수보다 앞선다, AGENTS.md 3-2절)
  --effect on|off  (LOL_EFFECT, 기본 on)  상태 색 테두리가 빛나는 토스트 카드
  --tts on|off     (LOL_TTS, 기본 off)    음성 안내
  둘 다 off 면 터미널에 찍는다. 예전 옵션 --card, --voice 는 --effect on, --tts on 과 같다.

  uv run python -m client.bridge --api http://127.0.0.1:8080 --effect on --tts on
  LOL_TTS=on uv run python -m client.bridge --file client/tests/fixtures/allgamedata_10min.json --effect off
"""
import argparse
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

TEN_MINUTES = 600.0
POLL_SECONDS = 2.0
TEAM = {"ORDER": "blue", "CHAOS": "red"}
STATS = ("kills", "assists", "cs", "avg_level", "dragons", "heralds", "towers")
RAW_PREFIX = "game_character_displayname_"
CHAMPION_FIELDS = ("my_champion", "enemy_top_threat")
# 토스트 카드 테두리 색 (build_advice.state). 웹 시뮬레이터의 테두리 이펙트와 같은 세 색
GLOW = {"ahead": "#3ddc97", "even": "#ffd34d", "behind": "#ff4d5e"}


def _names(p: dict) -> set[str]:
    out = set()
    for key in ("riotId", "riotIdGameName", "summonerName"):
        v = p.get(key)
        if v:
            out.add(v)
            out.add(v.split("#", 1)[0])
    return out


def champion_of(p: dict) -> str | None:
    """선수의 챔피언 이름. rawChampionName 의 영문 ID 우선, 없으면 championName."""
    raw = p.get("rawChampionName") or ""
    if raw.startswith(RAW_PREFIX) and raw[len(RAW_PREFIX):]:
        return raw[len(RAW_PREFIX):]
    return p.get("championName") or None


def top_threat(players: list[dict]) -> dict | None:
    """최다 킬, 동률이면 최다 CS, 그래도 같으면 목록 순서가 앞선 선수."""
    if not players:
        return None
    return max(enumerate(players),
               key=lambda ip: (int(ip[1]["scores"]["kills"]), int(ip[1]["scores"]["creepScore"]), -ip[0]))[1]


def _item_gold(players: list[dict], prices: dict[str, float] | None) -> float:
    total = 0.0
    for p in players:
        for it in p.get("items", []):
            price = (prices or {}).get(str(it.get("itemID"))) or it.get("price") or 0
            total += float(price) * int(it.get("count", 1))
    return total


def to_game_state(data: dict, side: str | None = None, prices: dict[str, float] | None = None) -> tuple[dict, dict]:
    """allgamedata 응답을 /coach 입력(dict)으로 바꾼다. (입력, 참고 정보)"""
    players = data["allPlayers"]
    team_of = {}
    by_team: dict[str, list[dict]] = {"blue": [], "red": []}
    for p in players:
        t = TEAM[p["team"]]
        by_team[t].append(p)
        for n in _names(p):
            team_of[n] = t

    me_player = None
    if side is None:
        me = _names(data.get("activePlayer") or {})
        me_player = next((p for p in players if _names(p) & me), None)
        if me_player is None:
            raise ValueError("내 팀을 알 수 없다(관전 모드라면 --side blue|red 를 준다)")
        side = TEAM[me_player["team"]]
    enemy = "red" if side == "blue" else "blue"

    events = (data.get("events") or {}).get("Events", [])
    obj = {t: {"dragons": 0, "heralds": 0, "towers": 0} for t in ("blue", "red")}
    first_blood = None
    for e in events:
        name = e.get("EventName")
        if name in ("DragonKill", "HeraldKill"):
            t = team_of.get(e.get("KillerName", ""))
            if t:
                obj[t]["dragons" if name == "DragonKill" else "heralds"] += 1
        elif name == "TurretKilled":
            turret = e.get("TurretKilled", "")
            if turret.startswith("Turret_T1_"):
                obj["red"]["towers"] += 1
            elif turret.startswith("Turret_T2_"):
                obj["blue"]["towers"] += 1
        elif name == "FirstBlood" and first_blood is None:
            first_blood = team_of.get(e.get("Recipient", ""))

    state: dict = {"first_blood": first_blood or enemy, "my_side": side}
    for t in ("blue", "red"):
        ps = by_team[t]
        state[t] = {
            "kills": sum(int(p["scores"]["kills"]) for p in ps),
            "assists": sum(int(p["scores"]["assists"]) for p in ps),
            "cs": sum(int(p["scores"]["creepScore"]) for p in ps),
            "avg_level": sum(float(p["level"]) for p in ps) / len(ps),
            **obj[t],
        }
    threat = top_threat(by_team[enemy])
    picks = {"my_champion": champion_of(me_player) if me_player else None,
             "enemy_top_threat": champion_of(threat) if threat else None}
    state.update({k: v for k, v in picks.items() if v})
    info = {
        "game_time": float(data["gameData"]["gameTime"]),
        "first_blood_seen": first_blood is not None,
        "item_gold": {t: _item_gold(by_team[t], prices) for t in ("blue", "red")},
        "champions": picks,
    }
    return state, info


def coach_with_fallback(coach: Callable[[dict], dict], state: dict) -> dict:
    """/coach 가 챔피언 이름 때문에 422 면(새 챔피언 등) 두 칸을 빼고 한 번만 다시 부른다."""
    try:
        return coach(state)
    except urllib.error.HTTPError as e:
        if e.code != 422 or not any(k in state for k in CHAMPION_FIELDS):
            raise
        return coach({k: v for k, v in state.items() if k not in CHAMPION_FIELDS})


def wait_and_coach(fetch: Callable[[], dict], coach: Callable[[dict], dict],
                   notify: Callable[[list[str], str | None], None],
                   side: str | None = None, prices: dict | None = None,
                   sleep: Callable[[float], None] = time.sleep, max_polls: int | None = None) -> dict | None:
    """10분이 될 때까지 시간만 확인하고, 10분이 넘으면 /coach 를 한 번만 불러 알린다."""
    polls = 0
    while True:
        data = fetch()
        if float(data["gameData"]["gameTime"]) >= TEN_MINUTES:
            state, info = to_game_state(data, side, prices)
            res = coach_with_fallback(coach, state)
            # 문구는 응답의 toast 그대로, 테두리 색은 build_advice.state
            notify(res["toast"], (res.get("build_advice") or {}).get("state"))
            print(json.dumps({"message": res["message"], "item_gold(참고)": info["item_gold"],
                              "champions": info["champions"], "model_version": res["model_version"]},
                             ensure_ascii=False))
            return res
        polls += 1
        if max_polls is not None and polls >= max_polls:
            return None
        sleep(POLL_SECONDS)


# ── 입출력 ──
def fetch_live(base: str, cafile: str | None, insecure_local: bool = False) -> dict:
    if not base.startswith(("https://127.0.0.1", "https://localhost")):
        raise ValueError("로컬 게임 API(127.0.0.1)만 읽는다")
    # 게임 클라이언트는 Riot 이 서명한 자체 인증서를 쓴다. Riot 개발자 문서의 riotgames.pem 으로 확인한다.
    # 확인을 끄는 것은 사용자가 --insecure-local 로 명시했을 때만, 그것도 이 PC(127.0.0.1) 주소에만 허용한다.
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
        ctx.check_hostname = False  # 인증서 이름이 127.0.0.1 과 다를 수 있다. 주소는 위에서 이 PC 로 제한했다
    elif insecure_local:
        ctx = ssl._create_unverified_context()
    else:
        raise ValueError("--cafile riotgames.pem 을 주거나, 이 PC 에서만 --insecure-local 을 명시한다")
    with urllib.request.urlopen(base + "/liveclientdata/allgamedata", timeout=3, context=ctx) as r:
        return json.load(r)


def post_coach(api: str, state: dict) -> dict:
    req = urllib.request.Request(api.rstrip("/") + "/coach", data=json.dumps(state).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def speak(lines: list[str]) -> None:
    """PC 내장 음성 합성으로 한 번 읽는다. 없으면 조용히 넘어간다."""
    text = ". ".join(lines).replace("·", ",")
    system = platform.system()
    if system == "Darwin" and shutil.which("say"):
        subprocess.run(["say", text], check=False)
    elif system == "Windows":
        ps = ("Add-Type -AssemblyName System.Speech; "
              "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:LOL_PACE_TEXT)")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False, env={"LOL_PACE_TEXT": text})
    elif shutil.which("espeak"):
        subprocess.run(["espeak", text], check=False)


def show_card(lines: list[str], seconds: float = 5.0, state: str | None = None) -> None:
    """우측 상단 작은 카드. 최대 3줄, seconds 뒤 사라진다(마지막 1초 동안 흐려짐). 클릭을 가로채지 않게 작게 둔다.
    테두리는 state 색(GLOW)으로 빛난다(처음 1.5초 동안 밝기가 오르내린다)."""
    lines = lines[:3]
    seconds = min(max(seconds, 3.0), 8.0)
    try:
        import tkinter as tk
    except ImportError:
        print("\n".join(lines))
        return
    glow = GLOW.get(state or "", "#5d6b7e")
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=glow, padx=3, pady=3)  # 바깥 3px 이 빛나는 테두리
    body = tk.Frame(root, bg="#11151c")
    body.pack(fill="both", expand=True)
    for i, line in enumerate(lines):
        tk.Label(body, text=line, fg="#f2f4f7" if i == 0 else "#9fb3c8", bg="#11151c",
                 font=("Helvetica", 14 if i == 0 else 12, "bold" if i == 0 else "normal"),
                 anchor="w", padx=14, pady=2 if i else 8).pack(fill="x")
    root.update_idletasks()
    w = root.winfo_reqwidth()
    root.geometry(f"+{root.winfo_screenwidth() - w - 24}+24")
    start = time.monotonic()

    def tick() -> None:
        left = seconds - (time.monotonic() - start)
        if left <= 0:
            root.destroy()
            return
        if left < 1.0:
            root.attributes("-alpha", max(left, 0.05))
        elif seconds - left < 1.5:  # 처음 1.5초: 테두리 맥박
            on = int((seconds - left) / 0.25) % 2 == 0
            root.configure(bg=glow if on else "#11151c")
        else:
            root.configure(bg=glow)
        root.after(50, tick)

    root.after(50, tick)
    root.mainloop()


_ON = {"on": True, "1": True, "true": True, "off": False, "0": False, "false": False}


def on_off(value: str) -> bool:
    v = value.strip().lower()
    if v not in _ON:
        raise ValueError(f"on 또는 off 여야 한다: {value!r}")
    return _ON[v]


def alert_switches(effect: str | None, tts: str | None, env: Mapping[str, str] = os.environ) -> tuple[bool, bool]:
    """(이펙트, 음성). 실행 옵션 > 환경 변수 LOL_EFFECT·LOL_TTS > 기본값(on, off)."""
    return (on_off(effect or env.get("LOL_EFFECT") or "on"),
            on_off(tts or env.get("LOL_TTS") or "off"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="10분에 한 번 페이스·목표를 알려 준다")
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    ap.add_argument("--live", default="https://127.0.0.1:2999")
    ap.add_argument("--cafile", help="Riot 로컬 API 인증서(riotgames.pem)")
    ap.add_argument("--insecure-local", action="store_true", help="인증서 확인 없이 127.0.0.1 만 읽기")
    ap.add_argument("--file", help="저장한 allgamedata JSON (게임 없이 시연)")
    ap.add_argument("--side", choices=["blue", "red"])
    ap.add_argument("--ddragon", help="Data Dragon item.json (아이템 골드 참고 값)")
    ap.add_argument("--effect", choices=["on", "off"], help="테두리 빛나는 토스트 카드 (기본 on, 환경 변수 LOL_EFFECT)")
    ap.add_argument("--tts", choices=["on", "off"], help="음성 안내 (기본 off, 환경 변수 LOL_TTS)")
    ap.add_argument("--card", dest="effect", action="store_const", const="on", help="--effect on 과 같다")
    ap.add_argument("--voice", dest="tts", action="store_const", const="on", help="--tts on 과 같다")
    ap.add_argument("--card-seconds", type=float, default=5.0)
    a = ap.parse_args(argv)
    try:
        effect_on, tts_on = alert_switches(a.effect, a.tts)
    except ValueError as e:
        ap.error(f"LOL_EFFECT·LOL_TTS: {e}")

    prices = None
    if a.ddragon:
        with open(a.ddragon, encoding="utf-8") as f:
            prices = {k: v["gold"]["total"] for k, v in json.load(f)["data"].items()}
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            saved = json.load(f)
        fetch = lambda: saved  # noqa: E731
        max_polls = 1
    else:
        fetch = lambda: fetch_live(a.live, a.cafile, a.insecure_local)  # noqa: E731
        max_polls = None

    def notify(lines: list[str], state: str | None = None) -> None:
        if tts_on:
            speak(lines)
        if effect_on:
            show_card(lines, a.card_seconds, state)
        if not (tts_on or effect_on):
            print("\n".join(lines))

    res = wait_and_coach(fetch, lambda s: post_coach(a.api, s), notify, a.side, prices, max_polls=max_polls)
    if res is None:
        print("아직 10분 전이다")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

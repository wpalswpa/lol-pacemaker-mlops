"""Riot Account-V1 + Match-V5 유저 전적 조회 (선택 기능, 표준 라이브러리만 사용)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_CACHE: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 300.0
_CACHE_MAX = 64

_QUEUE_NAME = {
    420: "솔로 랭크",
    430: "일반",
    440: "자유 랭크",
    450: "칼바람 나락",
    490: "빠른 대전",
    830: "AI 대전(입문)",
    840: "AI 대전(초급)",
    850: "AI 대전(중급)",
    870: "AI 대전(입문)",
    880: "AI 대전(초급)",
    890: "AI 대전(중급)",
    1700: "아레나",
    1900: "U.R.F.",
}


class RiotAPIError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _load_api_key() -> str:
    key = os.getenv("RIOT_API_KEY", "").strip()
    if key:
        return key
    # 로컬 개발용 .env 폴백 (.gitignore 대상, 서버에서는 compose.yml environment 사용)
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    if k.strip() == "RIOT_API_KEY":
                        val = v.strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            pass
    return ""


def parse_riot_id(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        raise RiotAPIError(422, "소환사 이름을 입력해 주세요 (예: Hide on bush#KR1).")
    if "#" in text:
        name, tag = text.split("#", 1)
        name, tag = name.strip(), tag.strip()
    else:
        name, tag = text, "KR1"
    if not name or not tag or len(name) > 40 or len(tag) > 10:
        raise RiotAPIError(422, "올바른 Riot ID 형식(닉네임#태그)으로 입력해 주세요.")
    return name, tag


def _riot_get(url: str, api_key: str, timeout: float = 8.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "X-Riot-Token": api_key,
            "Accept": "application/json",
            "User-Agent": "lol-pacemaker/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RiotAPIError(404, "해당 소환사를 찾지 못했습니다. 닉네임과 태그(#KR1 등)를 확인해 주세요.")
        if e.code in (401, 403):
            raise RiotAPIError(502, "Riot API 키가 만료되었거나 유효하지 않습니다.")
        if e.code == 429:
            raise RiotAPIError(429, "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
        raise RiotAPIError(502, f"Riot API 응답 오류 (HTTP {e.code})")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RiotAPIError(502, "Riot API 서버에 연결하지 못했습니다.")


def fetch_summoner_matches(riot_id: str, count: int = 5) -> dict[str, Any]:
    api_key = _load_api_key()
    if not api_key:
        raise RiotAPIError(503, "서버에 Riot API 키가 설정되지 않아 유저 전적 검색을 사용할 수 없습니다.")

    game_name, tag_line = parse_riot_id(riot_id)
    n = max(1, min(int(count), 10))
    cache_key = (game_name.lower(), tag_line.lower(), n)
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]

    q_name = urllib.parse.quote(game_name, safe="")
    q_tag = urllib.parse.quote(tag_line, safe="")
    acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{q_name}/{q_tag}"
    acc = _riot_get(acc_url, api_key)
    puuid = acc.get("puuid")
    if not puuid:
        raise RiotAPIError(404, "소환사 식별 정보(PUUID)를 찾지 못했습니다.")

    ids_url = f"https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/{urllib.parse.quote(puuid, safe='')}/ids?start=0&count={n}"
    match_ids = _riot_get(ids_url, api_key)
    if not isinstance(match_ids, list):
        match_ids = []

    matches: list[dict[str, Any]] = []
    wins = 0
    for mid in match_ids[:n]:
        m_url = f"https://asia.api.riotgames.com/lol/match/v5/matches/{urllib.parse.quote(str(mid), safe='')}"
        try:
            mdata = _riot_get(m_url, api_key)
        except RiotAPIError:
            continue
        info = mdata.get("info") or {}
        part = next((p for p in (info.get("participants") or []) if p.get("puuid") == puuid), None)
        if not part:
            continue
        win = bool(part.get("win"))
        if win:
            wins += 1
        kills = int(part.get("kills", 0) or 0)
        deaths = int(part.get("deaths", 0) or 0)
        assists = int(part.get("assists", 0) or 0)
        cs = int(part.get("totalMinionsKilled", 0) or 0) + int(part.get("neutralMinionsKilled", 0) or 0)
        dur = int(info.get("gameDuration", 0) or 0)
        qid = int(info.get("queueId", 0) or 0)
        items = [int(part.get(f"item{i}", 0) or 0) for i in range(7) if int(part.get(f"item{i}", 0) or 0) > 0]
        matches.append(
            {
                "match_id": str(mid),
                "champion": str(part.get("championName", "")),
                "position": str(part.get("teamPosition") or part.get("individualPosition") or ""),
                "win": win,
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "kda": round((kills + assists) / max(1, deaths), 2),
                "cs": cs,
                "cs_per_min": round(cs / max(1.0, dur / 60.0), 1),
                "duration_sec": dur,
                "queue": _QUEUE_NAME.get(qid, info.get("gameMode", "기타")),
                "game_end_ts": int(info.get("gameEndTimestamp", 0) or 0),
                "items": items[:6],
            }
        )

    total = len(matches)
    result = {
        "riot_id": f"{acc.get('gameName', game_name)}#{acc.get('tagLine', tag_line)}",
        "game_name": str(acc.get("gameName", game_name)),
        "tag_line": str(acc.get("tagLine", tag_line)),
        "total": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round((wins / total) * 100.0, 1) if total else 0.0,
        "matches": matches,
    }
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[cache_key] = (now, result)
    return result

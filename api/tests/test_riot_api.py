"""GET /matches/riot 소환사 전적 검색 단위 테스트 (네트워크 목)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from api import riot_api


def test_parse_riot_id_default_kr1_and_explicit():
    assert riot_api.parse_riot_id("Hide on bush") == ("Hide on bush", "KR1")
    assert riot_api.parse_riot_id("Faker#KR1") == ("Faker", "KR1")
    assert riot_api.parse_riot_id("  ShowMaker # KR1 ") == ("ShowMaker", "KR1")


def test_matches_riot_without_key_returns_503(client: TestClient, monkeypatch):
    riot_api._CACHE.clear()
    monkeypatch.setattr(riot_api, "_load_api_key", lambda: "")
    res = client.get("/matches/riot?riot_id=Faker%23KR1")
    assert res.status_code == 503
    assert "Riot API" in res.json()["detail"]


def test_matches_riot_success_and_cache(client: TestClient, monkeypatch):
    riot_api._CACHE.clear()
    monkeypatch.setattr(riot_api, "_load_api_key", lambda: "RGAPI-test-dummy-key")
    calls: list[str] = []

    def fake_get(url: str, api_key: str, timeout: float = 8.0):
        calls.append(url)
        if "/accounts/by-riot-id/" in url:
            return {"puuid": "puuid-123", "gameName": "Hide on bush", "tagLine": "KR1"}
        if "/matches/by-puuid/" in url:
            return ["KR_1001"]
        if "/matches/KR_1001" in url:
            return {
                "info": {
                    "gameDuration": 1800,
                    "queueId": 420,
                    "gameEndTimestamp": 1700000000000,
                    "participants": [
                        {
                            "puuid": "puuid-123",
                            "championName": "Ahri",
                            "teamPosition": "MIDDLE",
                            "win": True,
                            "kills": 8,
                            "deaths": 2,
                            "assists": 10,
                            "totalMinionsKilled": 210,
                            "neutralMinionsKilled": 15,
                            "item0": 3089,
                            "item1": 3020,
                        }
                    ],
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(riot_api, "_riot_get", fake_get)
    r1 = client.get("/matches/riot?riot_id=Hide%20on%20bush%23KR1&count=3")
    assert r1.status_code == 200
    body = r1.json()
    assert body["riot_id"] == "Hide on bush#KR1"
    assert body["total"] == 1 and body["wins"] == 1 and body["win_rate"] == 100.0
    assert body["matches"][0]["champion"] == "Ahri"
    assert body["matches"][0]["cs"] == 225
    assert body["matches"][0]["kda"] == 9.0
    assert body["matches"][0]["queue"] == "솔로 랭크"
    assert len(calls) == 3

    # 두 번째 호출은 캐시 사용
    r2 = client.get("/matches/riot?riot_id=Hide%20on%20bush%23KR1&count=3")
    assert r2.status_code == 200
    assert len(calls) == 3


def test_matches_riot_not_found_404(client: TestClient, monkeypatch):
    riot_api._CACHE.clear()
    monkeypatch.setattr(riot_api, "_load_api_key", lambda: "RGAPI-test-dummy-key")

    def fake_404(url: str, api_key: str, timeout: float = 8.0):
        raise riot_api.RiotAPIError(404, "해당 소환사를 찾지 못했습니다.")

    monkeypatch.setattr(riot_api, "_riot_get", fake_404)
    res = client.get("/matches/riot?riot_id=NoSuchUser999%23KR1")
    assert res.status_code == 404

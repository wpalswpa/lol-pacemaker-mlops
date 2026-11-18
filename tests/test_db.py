"""api/db.py 저장 계층 검사 (AGENTS.md 3-5절). 외부 DB 없이 SQLite 로 돌고, PostgreSQL 쪽은 SQL·변환만 가짜 연결로 본다."""
import csv
import io

import pytest

from api import db

FEATURES = {"names": ["first_blood", "kills", "assists", "cs", "avg_level", "dragons", "heralds", "towers"],
            "values": [0.0, -2.0, -3.0, -18.0, -0.2, -1.0, 0.0, 0.0],
            "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0},
            "red": {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0},
            "first_blood": "red", "my_side": "blue"}
COACH = {"pace": "열세 페이스", "state": "behind", "posture": "p", "top_goal": None, "item_ids": [3020, 1033, 3157],
         "skill_order": "Q > W > E", "model_version": "test"}


def _save(**kw):
    args = dict(game_time_sec=600, my_champion="Ahri", enemy_top_threat="Zed", my_win_prob=0.43, win=None,
                features=FEATURES, coach=COACH)
    return db.save_match(**(args | kw))


@pytest.fixture(autouse=True)
def memory_db():
    assert db.init_db("sqlite://") == "sqlite"
    yield


@pytest.mark.parametrize("url, want", [
    (None, db.DEFAULT_SQLITE), ("", db.DEFAULT_SQLITE),
    ("sqlite://", ":memory:"), ("sqlite://:memory:", ":memory:"), ("sqlite:///:memory:", ":memory:"),
    ("sqlite:///data/x.db", "data/x.db"), ("sqlite:////var/lib/x.db", "/var/lib/x.db"),
])
def test_sqlite_path(url, want):
    assert db._sqlite_path(url) == want


def test_unknown_scheme_is_config_error():
    with pytest.raises(ValueError):
        db.init_db("mysql://somewhere/x")


def test_file_db_and_unwritable_fallback(tmp_path):
    path = tmp_path / "sub" / "m.db"
    db.init_db(f"sqlite:///{path}")
    _save()
    assert path.exists() and db.list_matches()[0]["id"] == 1
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert db.init_db(f"sqlite:///{blocker}/m.db") == "sqlite"   # 폴더 자리에 파일 → 메모리로라도 뜬다
    assert db.list_matches() == []


def test_save_and_list_round_trip():
    a = _save(win=True)
    b = _save(my_champion="Jinx", win=False, game_time_sec=1390)
    assert (a["id"], b["id"]) == (1, 2)
    assert a["created_at"].endswith("+00:00")
    rows = db.list_matches()
    assert [r["id"] for r in rows] == [2, 1]                      # 최신순
    first = rows[1]
    assert first == {"id": 1, "created_at": a["created_at"], "game_time_sec": 600, "my_champion": "Ahri",
                     "enemy_top_threat": "Zed", "my_win_prob": 0.43, "win": True, "features": FEATURES,
                     "coach": COACH}
    assert rows[0]["win"] is False and rows[0]["game_time_sec"] == 1390


def test_list_limit_and_champion_filter():
    for champ in ("Ahri", "Jinx", "Ahri", "AD", "Ahri"):
        _save(my_champion=champ)
    assert [r["id"] for r in db.list_matches(limit=2)] == [5, 4]
    assert [r["id"] for r in db.list_matches(champion="Ahri")] == [5, 3, 1]
    assert [r["id"] for r in db.list_matches(limit=1, champion="Ahri")] == [5]
    assert db.list_matches(champion="Zed") == []


def test_export_only_decided_in_id_order():
    _save(win=None)
    _save(win=False)
    _save(win=True)
    assert [(r["id"], r["win"]) for r in db.export_matches()] == [(2, False), (3, True)]


def test_db_rejects_out_of_range():
    with pytest.raises(Exception):
        _save(my_win_prob=1.5)   # 표의 CHECK 제약 (API 를 거치지 않은 잘못된 값도 막는다)


# 블루 기준으로 바꾸기: 내 팀 블루가 졌으면 blueWins 0, 내 팀 레드가 이겼으면 blueWins 0
@pytest.mark.parametrize("my_side, win, blue_wins", [
    ("blue", True, 1), ("blue", False, 0), ("red", True, 0), ("red", False, 1),
])
def test_training_row(my_side, win, blue_wins):
    rec = {"id": 7, "game_time_sec": 600, "win": win, "features": FEATURES | {"my_side": my_side}}
    row = db.training_row(rec)
    assert list(row) == db.EXPORT_COLUMNS
    assert row["blueWins"] == blue_wins
    assert (row["matchId"], row["gameTimeSec"]) == (7, 600)
    assert (row["blueFirstBlood"], row["redFirstBlood"]) == (0, 1)       # 첫 킬 레드
    assert (row["blueKills"], row["redKills"], row["blueAssists"], row["redAssists"]) == (5, 7, 6, 9)
    assert (row["blueTotalMinionsKilled"], row["blueTotalJungleMinionsKilled"]) == (250, 0)   # CS 는 한 칸에
    assert (row["redTotalMinionsKilled"], row["redAvgLevel"], row["redDragons"]) == (268, 7.0, 1)
    assert (row["blueHeralds"], row["blueTowersDestroyed"]) == (0, 0)


def test_export_csv_is_read_by_training_loader(tmp_path, monkeypatch):
    """내보낸 CSV 를 training/data.py 가 그대로 읽어 같은 8개 피처를 만드는지 (학습 라이브러리가 있을 때만)."""
    pytest.importorskip("pandas")
    pytest.importorskip("sklearn")
    from training import data

    for i in range(10):
        _save(win=i % 2 == 0, features=FEATURES | {"my_side": "blue" if i < 5 else "red"})
    path = tmp_path / "export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=db.EXPORT_COLUMNS)
        w.writeheader()
        w.writerows(db.training_row(r) for r in db.export_matches())
    monkeypatch.setattr(data, "DATA_PATH", str(path))
    monkeypatch.setattr(data, "_cache", {})
    d = data._load()
    assert d["X"].shape == (10, 8)
    assert d["X"][0].tolist() == pytest.approx(FEATURES["values"], abs=1e-6)   # 첫 킬 레드 0, 블루 − 레드
    # 내 팀 블루(0~4): win 이 True·False 번갈아 → blueWins 1,0,1,0,1. 레드(5~9): win False,True,... → 반대
    assert d["y"].tolist() == [1, 0, 1, 0, 1, 1, 0, 1, 0, 1]


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class _FakeConn:
    """psycopg 연결 흉내: 보낸 SQL·인자를 적어 두고 정해 둔 줄을 돌려준다."""

    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=None):
        self.log.append((" ".join(sql.split()), args))
        return _FakeCursor(self.rows)


def test_postgres_store_sql_with_fake_connection():
    pytest.importorskip("psycopg")
    from datetime import datetime, timezone

    log = []
    when = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
    row = (3, when, 600, "Ahri", "Zed", 0.43, True, FEATURES, COACH)   # JSONB 는 dict 로 온다
    store = db.PostgresStore("postgresql://x", connect=lambda: _FakeConn(log, [row]))
    assert "JSONB" in log[0][0] and "GENERATED ALWAYS AS IDENTITY" in log[0][0]
    log.clear()
    db_conn = lambda: _FakeConn(log, [(3, when)])  # noqa: E731
    store._connect = db_conn
    assert store.save({"game_time_sec": 600, "my_champion": "Ahri", "enemy_top_threat": "Zed", "my_win_prob": 0.43,
                       "win": True, "features": FEATURES, "coach": COACH}) == {"id": 3, "created_at": "2026-11-18T08:00:00+00:00"}
    sql, args = log[-1]
    assert sql.startswith("INSERT INTO matches") and "RETURNING id, created_at" in sql
    assert args[:5] == (600, "Ahri", "Zed", 0.43, True)
    store._connect = lambda: _FakeConn(log, [row])
    got = store.list(5, "Ahri")
    assert log[-1] == (f"SELECT {', '.join(db.FIELDS)} FROM matches WHERE my_champion = %s ORDER BY id DESC LIMIT %s",
                       ["Ahri", 5])
    assert got[0]["created_at"] == "2026-11-18T08:00:00+00:00" and got[0]["features"] == FEATURES
    store.export()
    assert log[-1][0].endswith("WHERE win IS NOT NULL ORDER BY id")


def test_postgres_unreachable_is_db_unavailable():
    psycopg = pytest.importorskip("psycopg")

    def boom():
        raise psycopg.OperationalError("connection to server at secret-host failed")
    with pytest.raises(db.DBUnavailable) as e:
        db.PostgresStore("postgresql://x", connect=boom)
    assert "secret-host" not in str(e.value)   # 주소를 응답·기록에 옮기지 않는다
    assert e.value.__cause__ is None


def test_csv_header_matches_training_columns():
    buf = io.StringIO()
    csv.DictWriter(buf, fieldnames=db.EXPORT_COLUMNS).writeheader()
    head = buf.getvalue().strip().split(",")
    # training/data.py CSV_COLUMNS 가 읽는 열이 모두 있다
    for side in ("blue", "red"):
        for c in ("Kills", "Assists", "TotalMinionsKilled", "TotalJungleMinionsKilled", "AvgLevel", "Dragons",
                  "Heralds", "TowersDestroyed"):
            assert side + c in head
    assert {"blueFirstBlood", "blueWins"} <= set(head)

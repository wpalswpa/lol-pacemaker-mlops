"""전적 기록 DB (AGENTS.md 3-5절). PostgreSQL 과 SQLite 폴백이 같은 함수·같은 결과 모양을 쓴다.

  DATABASE_URL=postgresql://...  PostgreSQL (psycopg 3)
  DATABASE_URL=sqlite:///경로    그 파일,  sqlite:// 는 메모리
  없음                           data/matches.db (쓸 수 없으면 메모리)

개인 식별자는 받지 않는다. 이 모듈은 받은 값을 그대로 적고, 무엇을 받을지는 api/app.py 의 MatchIn 이 정한다.
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("lol.db")

DEFAULT_SQLITE = os.path.join("data", "matches.db")
TEAM_COLUMNS = {  # 팀 지표 → 원본 CSV 열 이름 뒷부분 (training/data.py 의 CSV_COLUMNS 와 같은 이름)
    "kills": "Kills", "assists": "Assists", "cs": "TotalMinionsKilled", "avg_level": "AvgLevel",
    "dragons": "Dragons", "heralds": "Heralds", "towers": "TowersDestroyed",
}
EXPORT_COLUMNS = ["matchId", "gameTimeSec", "blueWins"] + [
    c for side in ("blue", "red") for c in (
        f"{side}FirstBlood", f"{side}Kills", f"{side}Assists", f"{side}TotalMinionsKilled",
        f"{side}TotalJungleMinionsKilled", f"{side}AvgLevel", f"{side}Dragons", f"{side}Heralds",
        f"{side}TowersDestroyed")]
FIELDS = ("id", "created_at", "game_time_sec", "my_champion", "enemy_top_threat", "my_win_prob", "win",
          "features_json", "coach_json")


class DBUnavailable(Exception):
    """DB 에 연결하지 못했다. 메시지에 연결 주소·비밀번호를 넣지 않는다."""


def _iso(v: Any) -> str:
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else str(v)


def _record(row: tuple) -> dict:
    """DB 한 줄 → API 모양. JSON 칸은 엔진에 따라 문자열(SQLite)이거나 dict(JSONB)다."""
    r = dict(zip(FIELDS, row))
    load = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {
        "id": int(r["id"]), "created_at": _iso(r["created_at"]), "game_time_sec": int(r["game_time_sec"]),
        "my_champion": r["my_champion"], "enemy_top_threat": r["enemy_top_threat"],
        "my_win_prob": float(r["my_win_prob"]), "win": None if r["win"] is None else bool(r["win"]),
        "features": load(r["features_json"]), "coach": load(r["coach_json"]),
    }


class SQLiteStore:
    engine = "sqlite"

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)  # 요청 스레드가 여럿이라 lock 으로 묶는다
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    game_time_sec INTEGER NOT NULL CHECK (game_time_sec BETWEEN 0 AND 7200),
                    my_champion TEXT,
                    enemy_top_threat TEXT,
                    my_win_prob REAL NOT NULL CHECK (my_win_prob BETWEEN 0 AND 1),
                    win INTEGER CHECK (win IN (0, 1)),
                    features_json TEXT NOT NULL,
                    coach_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS matches_champion_idx ON matches (my_champion, id);""")

    def save(self, rec: dict) -> dict:
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO matches (created_at, game_time_sec, my_champion, enemy_top_threat, my_win_prob, win,"
                " features_json, coach_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (created, rec["game_time_sec"], rec["my_champion"], rec["enemy_top_threat"], rec["my_win_prob"],
                 None if rec["win"] is None else int(rec["win"]),
                 json.dumps(rec["features"], ensure_ascii=False), json.dumps(rec["coach"], ensure_ascii=False)))
            return {"id": int(cur.lastrowid), "created_at": created}

    def list(self, limit: int, champion: str | None) -> list[dict]:
        sql, args = f"SELECT {', '.join(FIELDS)} FROM matches", []
        if champion is not None:
            sql, args = sql + " WHERE my_champion = ?", [champion]
        with self.lock:
            rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return [_record(r) for r in rows]

    def export(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(f"SELECT {', '.join(FIELDS)} FROM matches WHERE win IS NOT NULL ORDER BY id").fetchall()
        return [_record(r) for r in rows]


class PostgresStore:
    engine = "postgresql"
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS matches (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            game_time_sec INTEGER NOT NULL CHECK (game_time_sec BETWEEN 0 AND 7200),
            my_champion TEXT,
            enemy_top_threat TEXT,
            my_win_prob DOUBLE PRECISION NOT NULL CHECK (my_win_prob BETWEEN 0 AND 1),
            win BOOLEAN,
            features_json JSONB NOT NULL,
            coach_json JSONB NOT NULL);
        CREATE INDEX IF NOT EXISTS matches_champion_idx ON matches (my_champion, id);"""

    def __init__(self, url: str, connect=None):
        import psycopg  # 서빙 이미지 기본 의존성. SQLite 만 쓰는 곳에서는 읽지 않는다

        self.url, self._psycopg = url, psycopg
        self._connect = connect or (lambda: psycopg.connect(url, connect_timeout=3))
        with self._conn() as conn:
            conn.execute(self.SCHEMA)

    def _conn(self):
        try:
            return self._connect()
        except self._psycopg.OperationalError:
            raise DBUnavailable("전적 DB에 연결하지 못했다") from None

    def _run(self, fn):
        try:
            with self._conn() as conn:  # 요청마다 짧게 연결한다 (max_connections=30, 기록은 드물다)
                return fn(conn)
        except self._psycopg.OperationalError:
            raise DBUnavailable("전적 DB에 연결하지 못했다") from None

    def save(self, rec: dict) -> dict:
        from psycopg.types.json import Jsonb

        def go(conn):
            row = conn.execute(
                "INSERT INTO matches (game_time_sec, my_champion, enemy_top_threat, my_win_prob, win, features_json,"
                " coach_json) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (rec["game_time_sec"], rec["my_champion"], rec["enemy_top_threat"], rec["my_win_prob"], rec["win"],
                 Jsonb(rec["features"]), Jsonb(rec["coach"]))).fetchone()
            return {"id": int(row[0]), "created_at": _iso(row[1])}
        return self._run(go)

    def list(self, limit: int, champion: str | None) -> list[dict]:
        sql, args = f"SELECT {', '.join(FIELDS)} FROM matches", []
        if champion is not None:
            sql, args = sql + " WHERE my_champion = %s", [champion]
        return self._run(lambda c: [_record(r) for r in c.execute(sql + " ORDER BY id DESC LIMIT %s",
                                                                    [*args, limit]).fetchall()])

    def export(self) -> list[dict]:
        sql = f"SELECT {', '.join(FIELDS)} FROM matches WHERE win IS NOT NULL ORDER BY id"
        return self._run(lambda c: [_record(r) for r in c.execute(sql).fetchall()])


def _sqlite_path(url: str | None) -> str:
    if not url:
        return DEFAULT_SQLITE
    rest = url[len("sqlite://"):]
    if rest in ("", ":memory:", "/:memory:"):
        return ":memory:"
    return rest[1:] if rest.startswith("/") else rest  # sqlite:///상대경로, sqlite:////절대경로


_store: SQLiteStore | PostgresStore | None = None
_init_lock = threading.Lock()


def init_db(url: str | None = None) -> str:
    """DATABASE_URL(또는 url)로 저장소를 열고 표를 만든다. 엔진 이름을 돌려준다.
    PostgreSQL 에 연결하지 못하면 DBUnavailable (다음 요청에서 다시 시도한다)."""
    global _store
    url = url if url is not None else os.getenv("DATABASE_URL", "")
    with _init_lock:
        if url.startswith(("postgresql://", "postgres://")):
            _store = PostgresStore(url)
        elif not url or url.startswith("sqlite://"):
            path = _sqlite_path(url)
            try:
                _store = SQLiteStore(path)
            except (OSError, sqlite3.Error) as e:  # 이미지 안처럼 쓸 수 없는 폴더면 메모리로라도 뜬다
                log.warning("전적 DB 파일을 열지 못해 메모리에 둔다: %s", type(e).__name__)
                _store = SQLiteStore(":memory:")
        else:
            raise ValueError("DATABASE_URL 은 postgresql://, postgres://, sqlite:// 중 하나로 시작해야 한다")
        return _store.engine


def _get():
    if _store is None:
        init_db()
    return _store


def engine() -> str | None:
    return None if _store is None else _store.engine


def save_match(*, game_time_sec: int, my_champion: str | None, enemy_top_threat: str | None, my_win_prob: float,
               win: bool | None, features: dict, coach: dict) -> dict:
    """한 경기를 적고 {"id", "created_at"} 를 돌려준다."""
    return _get().save({"game_time_sec": game_time_sec, "my_champion": my_champion,
                        "enemy_top_threat": enemy_top_threat, "my_win_prob": my_win_prob, "win": win,
                        "features": features, "coach": coach})


def list_matches(limit: int = 20, champion: str | None = None) -> list[dict]:
    """최신순 최대 limit 건. champion 을 주면 my_champion 이 그 값인 것만."""
    return _get().list(limit, champion)


def export_matches() -> list[dict]:
    """win 이 정해진 기록을 id 순으로 (재학습 입력용)."""
    return _get().export()


def training_row(rec: dict) -> dict:
    """기록 한 건 → 원본 Kaggle CSV 와 같은 열 이름의 한 줄 (AGENTS.md 4절 GET /matches/export)."""
    f = rec["features"]
    blue_won = rec["win"] if f["my_side"] == "blue" else not rec["win"]
    row = {"matchId": rec["id"], "gameTimeSec": rec["game_time_sec"], "blueWins": int(blue_won)}
    for side in ("blue", "red"):
        row[f"{side}FirstBlood"] = int(f["first_blood"] == side)
        for stat, col in TEAM_COLUMNS.items():
            row[side + col] = f[side][stat]
        row[f"{side}TotalJungleMinionsKilled"] = 0
    return {c: row[c] for c in EXPORT_COLUMNS}

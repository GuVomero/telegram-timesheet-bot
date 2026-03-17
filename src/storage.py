from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


DATA_DIR = Path("data")
LEGACY_DB_PATH = DATA_DIR / "clock_in.db"
MONTHLY_DB_RE = re.compile(r"^(0[1-9]|1[0-2])_(\d{4})\.db$")


@dataclass(slots=True)
class Punch:
    id: int
    chat_id: int
    user_id: int
    user_name: str
    action: str
    ts_utc: datetime


@dataclass(slots=True)
class KnownUser:
    user_id: int
    user_name: str


@dataclass(slots=True)
class DailyTarget:
    chat_id: int
    user_id: int
    user_name: str
    target_day: date
    target: timedelta


@dataclass(slots=True)
class WorkMode:
    chat_id: int
    user_id: int
    user_name: str
    target_day: date
    mode: str


def _ensure_db_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _db_path_for_ts(ts_utc: datetime) -> Path:
    normalized = _normalize_utc(ts_utc)
    return DATA_DIR / f"{normalized.month:02d}_{normalized.year}.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _db_path_for_year_month(year: int, month: int) -> Path:
    _ensure_db_dir()
    return DATA_DIR / f"{month:02d}_{year}.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS punches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            action TEXT NOT NULL,
            ts_utc TEXT NOT NULL
        )
        """
    )
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'punches'
        """
    ).fetchone()
    sql = row["sql"] if row is not None else ""
    if "CHECK(action IN ('IN', 'OUT'))" in sql:
        conn.execute("DROP TABLE IF EXISTS punches_new")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS punches_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                action TEXT NOT NULL,
                ts_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO punches_new (id, chat_id, user_id, user_name, action, ts_utc)
            SELECT id, chat_id, user_id, user_name, action, ts_utc
            FROM punches
            """
        )
        conn.execute("DROP TABLE punches")
        conn.execute("ALTER TABLE punches_new RENAME TO punches")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_punches_chat_user_ts
        ON punches (chat_id, user_id, ts_utc)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            target_day TEXT NOT NULL,
            target_minutes INTEGER NOT NULL,
            UNIQUE(chat_id, user_id, target_day)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_targets_chat_day
        ON daily_targets (chat_id, target_day)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_modes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            target_day TEXT NOT NULL,
            mode TEXT NOT NULL,
            UNIQUE(chat_id, user_id, target_day)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_work_modes_chat_day
        ON work_modes (chat_id, target_day)
        """
    )


def _month_key_from_filename(name: str) -> tuple[int, int] | None:
    match = MONTHLY_DB_RE.match(name)
    if not match:
        return None
    month = int(match.group(1))
    year = int(match.group(2))
    return year, month


def _list_monthly_db_paths(desc: bool = False) -> list[Path]:
    _ensure_db_dir()
    candidates: list[tuple[int, int, Path]] = []

    for path in DATA_DIR.glob("*.db"):
        key = _month_key_from_filename(path.name)
        if key is None:
            continue
        candidates.append((key[0], key[1], path))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=desc)
    return [item[2] for item in candidates]


def _iter_monthly_paths_between(start_utc: datetime, end_utc: datetime) -> list[Path]:
    start = _normalize_utc(start_utc)
    end = _normalize_utc(end_utc)
    if end <= start:
        return []

    paths: list[Path] = []
    year = start.year
    month = start.month

    while True:
        current = datetime(year, month, 1, tzinfo=timezone.utc)
        if current >= end:
            break
        paths.append(DATA_DIR / f"{month:02d}_{year}.db")
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return paths


def _row_to_punch(row: sqlite3.Row) -> Punch:
    return Punch(
        id=row["id"],
        chat_id=row["chat_id"],
        user_id=row["user_id"],
        user_name=row["user_name"],
        action=row["action"],
        ts_utc=datetime.fromisoformat(row["ts_utc"]),
    )


def init_db() -> None:
    # Inicializa o arquivo do mês atual para evitar primeiro write sem schema.
    now_utc = datetime.now(timezone.utc)
    with _connect(_db_path_for_ts(now_utc)) as conn:
        _ensure_schema(conn)


def add_punch(chat_id: int, user_id: int, user_name: str, action: str, ts_utc: datetime) -> None:
    normalized = _normalize_utc(ts_utc)
    db_path = _db_path_for_ts(normalized)

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO punches (chat_id, user_id, user_name, action, ts_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, user_name, action, normalized.isoformat()),
        )


def get_last_punch(chat_id: int, user_id: int) -> Punch | None:
    latest: Punch | None = None

    for path in _list_monthly_db_paths(desc=True):
        with _connect(path) as conn:
            _ensure_schema(conn)
            row = conn.execute(
                """
                SELECT *
                FROM punches
                WHERE chat_id = ? AND user_id = ?
                ORDER BY ts_utc DESC
                LIMIT 1
                """,
                (chat_id, user_id),
            ).fetchone()

        if row is None:
            continue

        candidate = _row_to_punch(row)
        if latest is None or candidate.ts_utc > latest.ts_utc:
            latest = candidate

    # Compatibilidade: lê também banco legado, se existir.
    if LEGACY_DB_PATH.exists():
        with _connect(LEGACY_DB_PATH) as conn:
            _ensure_schema(conn)
            row = conn.execute(
                """
                SELECT *
                FROM punches
                WHERE chat_id = ? AND user_id = ?
                ORDER BY ts_utc DESC
                LIMIT 1
                """,
                (chat_id, user_id),
            ).fetchone()
        if row is not None:
            candidate = _row_to_punch(row)
            if latest is None or candidate.ts_utc > latest.ts_utc:
                latest = candidate

    return latest


def list_punches_between(chat_id: int, start_utc: datetime, end_utc: datetime) -> Iterable[Punch]:
    start = _normalize_utc(start_utc)
    end = _normalize_utc(end_utc)

    rows_acc: list[Punch] = []

    for path in _iter_monthly_paths_between(start, end):
        if not path.exists():
            continue
        with _connect(path) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM punches
                WHERE chat_id = ?
                  AND ts_utc >= ?
                  AND ts_utc < ?
                """,
                (chat_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        rows_acc.extend(_row_to_punch(row) for row in rows)

    # Compatibilidade: inclui legado no filtro temporal.
    if LEGACY_DB_PATH.exists():
        with _connect(LEGACY_DB_PATH) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM punches
                WHERE chat_id = ?
                  AND ts_utc >= ?
                  AND ts_utc < ?
                """,
                (chat_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        rows_acc.extend(_row_to_punch(row) for row in rows)

    rows_acc.sort(key=lambda punch: (punch.user_id, punch.ts_utc))

    for punch in rows_acc:
        yield punch


def delete_punches_between(
    chat_id: int,
    user_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> int:
    start = _normalize_utc(start_utc)
    end = _normalize_utc(end_utc)
    deleted_total = 0

    for path in _iter_monthly_paths_between(start, end):
        if not path.exists():
            continue
        with _connect(path) as conn:
            _ensure_schema(conn)
            cursor = conn.execute(
                """
                DELETE FROM punches
                WHERE chat_id = ?
                  AND user_id = ?
                  AND ts_utc >= ?
                  AND ts_utc < ?
                """,
                (chat_id, user_id, start.isoformat(), end.isoformat()),
            )
            deleted_total += cursor.rowcount

    if LEGACY_DB_PATH.exists():
        with _connect(LEGACY_DB_PATH) as conn:
            _ensure_schema(conn)
            cursor = conn.execute(
                """
                DELETE FROM punches
                WHERE chat_id = ?
                  AND user_id = ?
                  AND ts_utc >= ?
                  AND ts_utc < ?
                """,
                (chat_id, user_id, start.isoformat(), end.isoformat()),
            )
            deleted_total += cursor.rowcount

    return deleted_total


def list_known_users(chat_id: int) -> list[KnownUser]:
    seen: dict[int, str] = {}

    paths = _list_monthly_db_paths(desc=False)
    if LEGACY_DB_PATH.exists():
        paths.append(LEGACY_DB_PATH)

    for path in paths:
        if not path.exists():
            continue
        with _connect(path) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT user_id, user_name, MAX(ts_utc) AS last_ts
                FROM punches
                WHERE chat_id = ?
                GROUP BY user_id, user_name
                ORDER BY last_ts DESC
                """,
                (chat_id,),
            ).fetchall()
        for row in rows:
            user_id = row["user_id"]
            user_name = row["user_name"]
            if user_id not in seen:
                seen[user_id] = user_name

    users = [KnownUser(user_id=user_id, user_name=user_name) for user_id, user_name in seen.items()]
    users.sort(key=lambda item: item.user_name.lower())
    return users


def set_daily_target(
    chat_id: int,
    user_id: int,
    user_name: str,
    target_day: date,
    target: timedelta,
) -> None:
    db_path = _db_path_for_year_month(target_day.year, target_day.month)
    minutes = int(target.total_seconds() // 60)
    if minutes < 0:
        minutes = 0

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO daily_targets (chat_id, user_id, user_name, target_day, target_minutes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id, target_day) DO UPDATE SET
                user_name = excluded.user_name,
                target_minutes = excluded.target_minutes
            """,
            (chat_id, user_id, user_name, target_day.isoformat(), minutes),
        )


def list_daily_targets_for_month(
    chat_id: int,
    year: int,
    month: int,
    user_ids: set[int] | None = None,
) -> list[DailyTarget]:
    db_path = _db_path_for_year_month(year, month)
    if not db_path.exists():
        return []

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        if user_ids:
            placeholders = ", ".join("?" for _ in user_ids)
            params: list[object] = [chat_id, *sorted(user_ids)]
            rows = conn.execute(
                f"""
                SELECT chat_id, user_id, user_name, target_day, target_minutes
                FROM daily_targets
                WHERE chat_id = ?
                  AND user_id IN ({placeholders})
                """,
                params,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT chat_id, user_id, user_name, target_day, target_minutes
                FROM daily_targets
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchall()

    out: list[DailyTarget] = []
    for row in rows:
        out.append(
            DailyTarget(
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                user_name=row["user_name"],
                target_day=date.fromisoformat(row["target_day"]),
                target=timedelta(minutes=int(row["target_minutes"])),
            )
        )
    return out


def set_work_mode(
    chat_id: int,
    user_id: int,
    user_name: str,
    target_day: date,
    mode: str,
) -> None:
    db_path = _db_path_for_year_month(target_day.year, target_day.month)
    normalized_mode = mode.strip().upper()

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        if normalized_mode == "STANDARD":
            conn.execute(
                """
                DELETE FROM work_modes
                WHERE chat_id = ?
                  AND user_id = ?
                  AND target_day = ?
                """,
                (chat_id, user_id, target_day.isoformat()),
            )
            return

        conn.execute(
            """
            INSERT INTO work_modes (chat_id, user_id, user_name, target_day, mode)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id, target_day) DO UPDATE SET
                user_name = excluded.user_name,
                mode = excluded.mode
            """,
            (chat_id, user_id, user_name, target_day.isoformat(), normalized_mode),
        )


def list_work_modes_for_month(
    chat_id: int,
    year: int,
    month: int,
    user_ids: set[int] | None = None,
) -> list[WorkMode]:
    db_path = _db_path_for_year_month(year, month)
    if not db_path.exists():
        return []

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        if user_ids:
            placeholders = ", ".join("?" for _ in user_ids)
            params: list[object] = [chat_id, *sorted(user_ids)]
            rows = conn.execute(
                f"""
                SELECT chat_id, user_id, user_name, target_day, mode
                FROM work_modes
                WHERE chat_id = ?
                  AND user_id IN ({placeholders})
                """,
                params,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT chat_id, user_id, user_name, target_day, mode
                FROM work_modes
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchall()

    out: list[WorkMode] = []
    for row in rows:
        out.append(
            WorkMode(
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                user_name=row["user_name"],
                target_day=date.fromisoformat(row["target_day"]),
                mode=row["mode"],
            )
        )
    return out

"""Thin SQLite persistence layer.

We use plain ``sqlite3`` over an ORM to keep the data model auditable: every
row a synthesis step depends on is visible and queryable. Pydantic objects
are serialized to JSON columns where the structure is nested.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable

from .config import settings


_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company_id      TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    role            TEXT NOT NULL,   -- 'sender' or 'target'
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    page_id         TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    url             TEXT NOT NULL,
    status_code     INTEGER,
    content_hash    TEXT,
    cleaned_chars   INTEGER,
    fetched_at      TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'website',
    UNIQUE(company_id, url)
);

CREATE TABLE IF NOT EXISTS sections (
    section_id      TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    page_id         TEXT NOT NULL,
    url             TEXT NOT NULL,
    heading         TEXT,
    text            TEXT NOT NULL,
    char_start      INTEGER,
    char_end        INTEGER,
    source          TEXT NOT NULL DEFAULT 'website'
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id     TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL,
    section_id         TEXT NOT NULL,
    kind               TEXT NOT NULL,
    text               TEXT NOT NULL,
    confidence         REAL NOT NULL,
    validation         TEXT,
    validation_score   REAL
);

CREATE TABLE IF NOT EXISTS icps (
    company_id     TEXT PRIMARY KEY,
    payload        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS value_props (
    company_id     TEXT PRIMARY KEY,
    payload        TEXT NOT NULL
);

-- A target can be evaluated for multiple personas; each (target, persona)
-- pair has its own strategy + emails + claim map. ``persona_id`` defaults
-- to '' so the composite primary key works on every row even when the
-- caller didn't supply a stored persona (the orchestrator always creates
-- one before persisting, but legacy rows may have an empty key).
CREATE TABLE IF NOT EXISTS strategies (
    target_company_id   TEXT NOT NULL,
    persona_id          TEXT NOT NULL DEFAULT '',
    sender_company_id   TEXT NOT NULL,
    persona             TEXT NOT NULL,
    payload             TEXT NOT NULL,
    PRIMARY KEY (target_company_id, persona_id)
);

CREATE TABLE IF NOT EXISTS emails (
    email_id            TEXT PRIMARY KEY,
    target_company_id   TEXT NOT NULL,
    persona_id          TEXT NOT NULL DEFAULT '',
    angle               TEXT NOT NULL,
    subject             TEXT NOT NULL,
    body                TEXT NOT NULL,
    payload             TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_emails_target_persona_angle
    ON emails(target_company_id, persona_id, angle);

-- A sender owns its target list directly (each sender IS the campaign).
-- The same target URL can belong to multiple senders, hence the composite PK.
CREATE TABLE IF NOT EXISTS sender_targets (
    sender_company_id   TEXT NOT NULL,
    target_company_id   TEXT NOT NULL,
    added_at            TEXT NOT NULL,
    PRIMARY KEY (sender_company_id, target_company_id)
);

CREATE TABLE IF NOT EXISTS personas (
    persona_id          TEXT PRIMARY KEY,
    target_company_id   TEXT NOT NULL,
    name                TEXT,
    role                TEXT NOT NULL,
    seniority           TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_map (
    claim_id            TEXT PRIMARY KEY,
    email_id            TEXT NOT NULL,
    angle               TEXT NOT NULL,
    text                TEXT NOT NULL,
    status              TEXT NOT NULL,
    nli_score           REAL,
    citations           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_cache (
    cache_key           TEXT PRIMARY KEY,   -- sha256(url)
    url                 TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    status_code         INTEGER,
    raw_path            TEXT NOT NULL,
    cleaned_path        TEXT NOT NULL,
    fetched_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    kind                TEXT NOT NULL,      -- 'sender' or 'target'
    company_id          TEXT,
    target_company_id   TEXT,
    metrics             TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sections_company ON sections(company_id);
CREATE INDEX IF NOT EXISTS idx_observations_company ON observations(company_id);
CREATE INDEX IF NOT EXISTS idx_observations_section ON observations(section_id);
CREATE INDEX IF NOT EXISTS idx_pages_company ON pages(company_id);
CREATE INDEX IF NOT EXISTS idx_claims_email ON claim_map(email_id);
CREATE INDEX IF NOT EXISTS idx_sender_targets_sender ON sender_targets(sender_company_id);
CREATE INDEX IF NOT EXISTS idx_sender_targets_target ON sender_targets(target_company_id);
CREATE INDEX IF NOT EXISTS idx_personas_target ON personas(target_company_id);
CREATE INDEX IF NOT EXISTS idx_emails_persona ON emails(persona_id);
CREATE INDEX IF NOT EXISTS idx_strategies_persona ON strategies(persona_id);
CREATE INDEX IF NOT EXISTS idx_runs_kind ON runs(kind);
CREATE INDEX IF NOT EXISTS idx_runs_company ON runs(company_id);
"""


# Migrations applied at startup. Two kinds:
#   1. Idempotent ALTERs (new columns) for tables we evolve in place.
#   2. Table rebuilds when we change the primary key (SQLite cannot ALTER
#      a primary key in place).
def _apply_migrations(conn: "sqlite3.Connection") -> None:
    def col_info(table: str) -> list[tuple]:
        return conn.execute(f"PRAGMA table_info({table})").fetchall()

    def has_col(table: str, col: str) -> bool:
        return any(r[1] == col for r in col_info(table))

    # --- strategies: composite PK (target_company_id, persona_id) ---
    cols = col_info("strategies")
    pk_cols = [r[1] for r in cols if r[5] > 0]
    if pk_cols == ["target_company_id"]:
        # Rebuild the table with the new PK and drop the orphan campaign_id.
        conn.executescript(
            """
            CREATE TABLE strategies__new (
                target_company_id   TEXT NOT NULL,
                persona_id          TEXT NOT NULL DEFAULT '',
                sender_company_id   TEXT NOT NULL,
                persona             TEXT NOT NULL,
                payload             TEXT NOT NULL,
                PRIMARY KEY (target_company_id, persona_id)
            );
            INSERT OR IGNORE INTO strategies__new
                (target_company_id, persona_id, sender_company_id, persona, payload)
            SELECT target_company_id, COALESCE(persona_id, ''),
                   sender_company_id, persona, payload
            FROM strategies;
            DROP TABLE strategies;
            ALTER TABLE strategies__new RENAME TO strategies;
            """
        )

    # --- emails: NOT NULL persona_id (default '') + UNIQUE per angle ---
    if has_col("emails", "campaign_id") or any(
        r[1] == "persona_id" and r[3] == 0 for r in col_info("emails")
    ):
        # Rebuild to drop campaign_id and tighten persona_id.
        conn.executescript(
            """
            CREATE TABLE emails__new (
                email_id            TEXT PRIMARY KEY,
                target_company_id   TEXT NOT NULL,
                persona_id          TEXT NOT NULL DEFAULT '',
                angle               TEXT NOT NULL,
                subject             TEXT NOT NULL,
                body                TEXT NOT NULL,
                payload             TEXT NOT NULL
            );
            INSERT OR IGNORE INTO emails__new
                (email_id, target_company_id, persona_id, angle, subject, body, payload)
            SELECT email_id, target_company_id, COALESCE(persona_id, ''),
                   angle, subject, body, payload
            FROM emails;
            DROP TABLE emails;
            ALTER TABLE emails__new RENAME TO emails;
            CREATE UNIQUE INDEX IF NOT EXISTS ux_emails_target_persona_angle
                ON emails(target_company_id, persona_id, angle);
            """
        )


_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                _conn = _connect()
                _conn.executescript(_SCHEMA)
                _apply_migrations(_conn)
                _conn.commit()
    return _conn


@contextmanager
def tx():
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ---------- helpers ----------

def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None) -> Any:
    return json.loads(s) if s else None


def fetchone(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, tuple(params)).fetchone()


def fetchall(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(get_conn().execute(sql, tuple(params)).fetchall())

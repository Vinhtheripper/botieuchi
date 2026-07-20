import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(os.getenv("DATABASE_PATH", Path(__file__).resolve().parents[1] / "survey.db")).expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS excel_sheets (name TEXT PRIMARY KEY, rows_json TEXT NOT NULL, imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS variables (code TEXT PRIMARY KEY, name_vi TEXT, name_en TEXT, group_name TEXT, skip_rule TEXT, channel TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, position INTEGER, phase TEXT, kind TEXT, text TEXT, variables_json TEXT DEFAULT '[]', options_json TEXT DEFAULT '[]', note TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS respondents (id TEXT PRIMARY KEY, name TEXT, email TEXT, consent INTEGER, product TEXT, platform TEXT, theme TEXT DEFAULT 'rose', started_at TEXT, completed_at TEXT, status TEXT DEFAULT 'active', revision INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS answers (id INTEGER PRIMARY KEY AUTOINCREMENT, respondent_id TEXT, question_id TEXT, option_id TEXT, value_json TEXT, scores_json TEXT, answered_at TEXT, UNIQUE(respondent_id, question_id));
CREATE TABLE IF NOT EXISTS skipped (respondent_id TEXT, question_id TEXT, variables_json TEXT, reason TEXT, created_at TEXT, UNIQUE(respondent_id, question_id));
CREATE TABLE IF NOT EXISTS admin_users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id TEXT, action TEXT NOT NULL, resource TEXT, resource_id TEXT, detail_json TEXT, ip TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS branch_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, source_question TEXT NOT NULL, operator TEXT NOT NULL DEFAULT 'equals', expected_value TEXT NOT NULL, target_question TEXT NOT NULL, action TEXT NOT NULL DEFAULT 'skip_to', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS question_timing (respondent_id TEXT, question_id TEXT, shown_at TEXT NOT NULL, answered_at TEXT, duration_ms INTEGER, PRIMARY KEY(respondent_id,question_id));
CREATE TABLE IF NOT EXISTS question_media (id INTEGER PRIMARY KEY AUTOINCREMENT, question_id TEXT NOT NULL, option_id TEXT, path TEXT NOT NULL, mime_type TEXT, original_name TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS team_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL, author_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_checkpoints (respondent_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, revision INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(respondent_id,idempotency_key));
CREATE TABLE IF NOT EXISTS analytics_cache (cache_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_questions_active_position ON questions(active, position);
CREATE INDEX IF NOT EXISTS idx_respondents_status_started ON respondents(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_answers_respondent ON answers(respondent_id);
CREATE INDEX IF NOT EXISTS idx_answers_question_option ON answers(question_id, option_id);
CREATE INDEX IF NOT EXISTS idx_skipped_respondent ON skipped(respondent_id);
CREATE INDEX IF NOT EXISTS idx_timing_respondent ON question_timing(respondent_id);
CREATE INDEX IF NOT EXISTS idx_branches_target_active ON branch_rules(target_question, active);
CREATE INDEX IF NOT EXISTS idx_media_question_option ON question_media(question_id, option_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry ON admin_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_checkpoints_revision ON sync_checkpoints(respondent_id,revision);
"""

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-20000")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=1000")
        con.executescript(SCHEMA)
        columns={x[1] for x in con.execute("PRAGMA table_info(respondents)")}
        if "theme" not in columns: con.execute("ALTER TABLE respondents ADD COLUMN theme TEXT DEFAULT 'rose'")
        if "revision" not in columns: con.execute("ALTER TABLE respondents ADD COLUMN revision INTEGER DEFAULT 0")

def rows(sql, params=()):
    with connect() as con:
        return [dict(x) for x in con.execute(sql, params).fetchall()]

def row(sql, params=()):
    with connect() as con:
        value = con.execute(sql, params).fetchone()
        return dict(value) if value else None

def decode(record, *fields):
    if not record: return record
    for field in fields:
        if field in record and isinstance(record[field], str):
            try: record[field] = json.loads(record[field])
            except json.JSONDecodeError: pass
    return record

"""SQLite storage. Tables: accounts, sessions, experiences, cases, requests, trials, outbox,
documents + passages (the article library), invites (researchers contacted by email)."""
import json, sqlite3, time
from pathlib import Path

DB_PATH = Path(__file__).parent / "farmline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, role TEXT, name TEXT, phone TEXT, email TEXT,
  pass_hash TEXT, salt TEXT, contact_pref TEXT DEFAULT 'text',
  address TEXT, city TEXT, county TEXT, state TEXT, zip TEXT, lat REAL, lon REAL,
  crops TEXT, farm_acres TEXT,
  institution TEXT, department TEXT, expertise TEXT, openalex_id TEXT, demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account_id INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, account_id INTEGER, title TEXT, crop TEXT,
  category TEXT, description TEXT, what_worked TEXT, season TEXT, days_to_fix INTEGER,
  allow_contact INTEGER DEFAULT 1, place TEXT, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, account_id INTEGER, source TEXT, crop TEXT,
  category TEXT, symptoms TEXT, severity TEXT, likely_issue TEXT, summary TEXT, first_steps TEXT,
  research_terms TEXT, solution TEXT, research TEXT, real_cases TEXT, experts TEXT, description TEXT, started TEXT, area_affected TEXT, trial_ok INTEGER DEFAULT 0,
  place TEXT, lat REAL, lon REAL, status TEXT DEFAULT 'open', resolution TEXT);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, case_id INTEGER, kind TEXT,
  target_account_id INTEGER, experience_id INTEGER, status TEXT DEFAULT 'waiting',
  reply_type TEXT, reply TEXT, replied REAL);
CREATE TABLE IF NOT EXISTS trials (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, account_id INTEGER, title TEXT, crop TEXT,
  problem TEXT, description TEXT, farmer_needs TEXT);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, url TEXT UNIQUE, title TEXT, source TEXT,
  kind TEXT, added_by INTEGER, words INTEGER);
CREATE TABLE IF NOT EXISTS passages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, case_id INTEGER, name TEXT, institution TEXT,
  email TEXT, openalex_id TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, account_id INTEGER, sender_id INTEGER, to_name TEXT,
  channel TEXT, to_addr TEXT, subject TEXT, body TEXT, sent INTEGER);
"""
JSON_COLS = {"symptoms": "[]", "first_steps": "[]", "solution": "null", "research": "null", "real_cases": "null", "experts": "null"}


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _row(r):
    d = dict(r)
    for k in JSON_COLS:
        if k in d:
            d[k] = json.loads(d[k] or JSON_COLS[k])
    d.pop("pass_hash", None), d.pop("salt", None)
    return d


def query(q, args=()):
    with conn() as c:
        return [_row(r) for r in c.execute(q, args)]


def one(q, args=()):
    rows = query(q, args)
    return rows[0] if rows else None


def raw_one(q, args=()):  # keeps password columns; only used by login
    with conn() as c:
        r = c.execute(q, args).fetchone()
        return dict(r) if r else None


def execute(q, args=()):
    with conn() as c:
        return c.execute(q, args).lastrowid


def insert(table, data):
    data = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in data.items()}
    data.setdefault("created", time.time())
    cols = ",".join(data)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(data))})", list(data.values()))


def update(table, row_id, data):
    data = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in data.items()}
    if data:
        sets = ",".join(f"{k}=?" for k in data)
        execute(f"UPDATE {table} SET {sets} WHERE id=?", [*data.values(), row_id])


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        case_cols = {r[1] for r in c.execute("PRAGMA table_info(cases)")}
        for col in ("real_cases", "experts"):  # databases made by older versions
            if col not in case_cols:
                c.execute(f"ALTER TABLE cases ADD COLUMN {col} TEXT")
        have = {r[1] for r in c.execute("PRAGMA table_info(outbox)")}
        for col, kind in (("sender_id", "INTEGER"), ("to_name", "TEXT")):  # databases made by older versions
            if col not in have:
                c.execute(f"ALTER TABLE outbox ADD COLUMN {col} {kind}")

"""SQLite：分片 → 片段（送转写的一段）→ 逐句原文 → 事件（全时记）/ 随口记 → 项目 → 觉察。"""
import json
import sqlite3
import threading
import time

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (           -- 录音器写的一分钟分片
  path TEXT PRIMARY KEY, source TEXT, start_ms INTEGER, dur_ms INTEGER,
  speech_ms INTEGER, status TEXT, created INTEGER
);
CREATE TABLE IF NOT EXISTS pieces (           -- 拼起来送去转写的一段人声
  id INTEGER PRIMARY KEY, source TEXT, start_ms INTEGER, end_ms INTEGER,
  speech_ms INTEGER, audio_path TEXT, timemap TEXT, kind TEXT DEFAULT 'stream',
  status TEXT, task_id TEXT, error TEXT, created INTEGER, done INTEGER
);
CREATE TABLE IF NOT EXISTS sentences (        -- 逐句原文，时间是绝对毫秒
  id INTEGER PRIMARY KEY, piece_id INTEGER, source TEXT, start_ms INTEGER, end_ms INTEGER,
  text TEXT, speaker TEXT, spk_local INTEGER, spk_score REAL, note_id INTEGER, event_id INTEGER
);
CREATE INDEX IF NOT EXISTS sentences_time ON sentences(start_ms);
CREATE INDEX IF NOT EXISTS sentences_event ON sentences(event_id);
CREATE VIRTUAL TABLE IF NOT EXISTS sentences_fts USING fts5(text, content='sentences', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS sentences_ai AFTER INSERT ON sentences BEGIN
  INSERT INTO sentences_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS sentences_ad AFTER DELETE ON sentences BEGIN
  INSERT INTO sentences_fts(sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS events (           -- 全时记：按话题切出来的一件件事
  id INTEGER PRIMARY KEY, start_ms INTEGER, end_ms INTEGER, status TEXT,
  category TEXT, title TEXT, summary TEXT, importance INTEGER,
  points TEXT, todos TEXT, decisions TEXT, highlights TEXT, people TEXT, memories TEXT,
  project_id INTEGER, speech_ms INTEGER, created INTEGER, updated INTEGER
);
CREATE INDEX IF NOT EXISTS events_time ON events(start_ms);
CREATE TABLE IF NOT EXISTS notes (            -- 随口记：快捷键按住说的那一条
  id INTEGER PRIMARY KEY, start_ms INTEGER, end_ms INTEGER, status TEXT,
  text TEXT, intent TEXT, title TEXT, answer TEXT, due TEXT, created INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS marks (            -- 标重点的时间点
  id INTEGER PRIMARY KEY, ts INTEGER UNIQUE, kind TEXT, event_id INTEGER
);
CREATE TABLE IF NOT EXISTS projects (         -- 跨天的同一件事
  id INTEGER PRIMARY KEY, name TEXT, summary TEXT, stage TEXT, risks TEXT,
  next_questions TEXT, commitments TEXT, created INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS digests (          -- 每天的总结（按上午/下午/晚上分段）
  day TEXT PRIMARY KEY, content TEXT, created INTEGER
);
CREATE TABLE IF NOT EXISTS insights (         -- 觉察：只看「我」说的话
  id INTEGER PRIMARY KEY, period TEXT, insight TEXT, evidence TEXT, suggestion TEXT, created INTEGER
);
CREATE TABLE IF NOT EXISTS voices (           -- 声纹：本人和起过名字的人
  name TEXT PRIMARY KEY, embedding TEXT, samples INTEGER, updated INTEGER
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def connect():
    ensure_dirs()
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


_ro = None
_ro_lock = threading.Lock()


def ro():
    """只读连接，MCP 和网页用。整个进程共用一个：以前每次查询新开一个、用完不关，
    网页服务开到 250 个左右就撞上打开文件上限（时间线报 Too many open files）"""
    global _ro
    with _ro_lock:
        if _ro is None:
            _ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30, check_same_thread=False)
            _ro.row_factory = sqlite3.Row
        return _ro


def now_ms():
    return int(time.time() * 1000)


def kv_get(con, k, default=None):
    r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(con, k, v):
    con.execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, ensure_ascii=False)))

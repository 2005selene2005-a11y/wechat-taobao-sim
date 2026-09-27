import sqlite3
import threading
from datetime import datetime

from . import config as C

_local = threading.local()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS wallet(owner TEXT PRIMARY KEY, balance REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, ts TEXT, owner TEXT, kind TEXT, amount REAL, note TEXT, ref INT);
CREATE TABLE IF NOT EXISTS packets(id INTEGER PRIMARY KEY, ts TEXT, type TEXT DEFAULT 'packet', sender TEXT, receiver TEXT,
    amount REAL, msg TEXT, status TEXT DEFAULT 'new', opened_at TEXT);
CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY, ts TEXT, sender TEXT, kind TEXT, text TEXT, ref INT, meta TEXT,
    status TEXT DEFAULT 'sent', read_at TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, title TEXT, sub TEXT, price REAL, orig REAL, emoji TEXT, color TEXT,
    shop TEXT, mall INT DEFAULT 0, cat TEXT, sold INT DEFAULT 0, tags TEXT DEFAULT '[]', active INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS cart(item_id INTEGER PRIMARY KEY, qty INT DEFAULT 1, ts TEXT);
CREATE TABLE IF NOT EXISTS ai_cart(id INTEGER PRIMARY KEY, ts TEXT, item_id INT, note TEXT);
CREATE TABLE IF NOT EXISTS fav(item_id INTEGER PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS foot(id INTEGER PRIMARY KEY, ts TEXT, item_id INT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, ts TEXT, item_id INT, title TEXT, price REAL, qty INT DEFAULT 1,
    status TEXT, buyer TEXT, gift INT DEFAULT 0, msg TEXT, done_at TEXT);
CREATE INDEX IF NOT EXISTS idx_msgs_read ON msgs(sender, read_at);
CREATE TABLE IF NOT EXISTS moments(id INTEGER PRIMARY KEY, ts TEXT, author TEXT, text TEXT, imgs_json TEXT, loc TEXT, status TEXT DEFAULT 'ok');
CREATE TABLE IF NOT EXISTS moment_likes(mid INT, who TEXT, ts TEXT, PRIMARY KEY(mid,who));
CREATE TABLE IF NOT EXISTS moment_comments(id INTEGER PRIMARY KEY, mid INT, who TEXT, text TEXT, reply_to TEXT, ts TEXT, status TEXT DEFAULT 'ok');
CREATE TABLE IF NOT EXISTS moment_jobs(id INTEGER PRIMARY KEY, mid INT, kind TEXT, due TEXT, round INT DEFAULT 1, status TEXT DEFAULT 'pending');
'''


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today():
    return now()[:10]


def conn():
    """每个线程复用一条连接。"""
    c = getattr(_local, 'c', None)
    if c is None:
        c = sqlite3.connect(str(C.DB_PATH), timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        _local.c = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.execute("INSERT OR IGNORE INTO wallet(owner,balance) VALUES('user',?)", (C.USER_INIT_BALANCE,))
    c.execute("INSERT OR IGNORE INTO wallet(owner,balance) VALUES('ai',?)", (C.AI_INIT_BALANCE,))
    c.commit()


def kv_get(k, default=None):
    r = conn().execute('SELECT v FROM kv WHERE k=?', (k,)).fetchone()
    return r[0] if r else default


def kv_set(k, v):
    c = conn()
    c.execute('INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)', (k, str(v)))
    c.commit()


def balance(owner):
    return conn().execute('SELECT balance FROM wallet WHERE owner=?', (owner,)).fetchone()[0]


def ledger(owner, kind, amount, note, ref=None):
    """记一笔账并更新余额（调用方负责 commit）。"""
    c = conn()
    c.execute('UPDATE wallet SET balance=round(balance+?,2) WHERE owner=?', (amount, owner))
    c.execute('INSERT INTO ledger(ts,owner,kind,amount,note,ref) VALUES(?,?,?,?,?,?)',
              (now(), owner, kind, amount, note, ref))


def add_msg(sender, kind, text='', ref=None, meta=None, commit=True):
    import json
    c = conn()
    cur = c.execute('INSERT INTO msgs(ts,sender,kind,text,ref,meta) VALUES(?,?,?,?,?,?)',
                    (now(), sender, kind, text, ref, json.dumps(meta, ensure_ascii=False) if meta else None))
    if commit:
        c.commit()
    return cur.lastrowid

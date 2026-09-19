"""
store.py -- where the levels runtime keeps what must outlive one function
invocation (a Vercel function's disk is read-only apart from an ephemeral /tmp):

    state       the JSON the /levels page renders
    m5_bars     the merged M5 history, gzip CSV, on the newest back-adjusted scale
    lc/<file>   the level-ledger cache files, so a refresh only extends the
                ledger by the new bars instead of rebuilding three years of it
    error       last refresh error (cleared on success), shown on the page
    lock        one refresh at a time

Production uses the app's existing Neon Postgres (NEON_DB_DATABASE_URL, the
same one lib/db.ts reads) through pg8000, table `levels_kv`, created on first
use. Without that variable, or with LEVELS_STORE_DIR set, a directory of files
stands in -- that is what local development and the tests use.
"""
import os
import time
from urllib.parse import unquote, urlparse

LOCK_TTL_S = 330            # a bit over the function's 300 s limit: a crashed refresh frees itself
KEYS_META = ("state", "error", "lock")

LAG_S = 20          # give TradingView a few seconds to have the hour's last bar


def bucket(t):
    """The hour a timestamp belongs to, shifted by LAG_S: state built before
    hh:00:20 does not count as containing hour hh's bars."""
    return int((t - LAG_S) // 3600)


def is_stale(built_at, now=None):
    now = time.time() if now is None else now
    return built_at is None or bucket(built_at) < bucket(now)


def meta(store):
    """What the page needs to decide whether to refresh."""
    now = time.time()
    built = store.updated_at("state")
    lock = store.updated_at("lock")
    err = store.get("error")
    return {"built_at": built, "now": now, "stale": is_stale(built, now),
            "building": lock is not None and now - lock < LOCK_TTL_S,
            "error": err.decode() if err else None}


class FileStore:
    """Directory-backed store for local development."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key):
        return os.path.join(self.root, key.replace("/", "__"))

    def get(self, key):
        try:
            with open(self._path(key), "rb") as f:
                return f.read()
        except OSError:
            return None

    def put(self, key, value):
        tmp = self._path(key) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(value)
        os.replace(tmp, self._path(key))

    def delete(self, key):
        try:
            os.remove(self._path(key))
        except OSError:
            pass

    def updated_at(self, key):
        try:
            return os.path.getmtime(self._path(key))
        except OSError:
            return None

    def acquire(self):
        age = self.updated_at("lock")
        if age is not None and time.time() - age < LOCK_TTL_S:
            return False
        self.put("lock", b"1")
        return True

    def release(self):
        self.delete("lock")


class PgStore:
    """Postgres-backed store (Neon). One short-lived connection per call: the
    function is stateless and a refresh spends its time computing, not querying."""

    def __init__(self, url):
        self.url = url
        self._ready = False

    def _connect(self):
        import pg8000.native
        u = urlparse(self.url)
        host = u.hostname.replace("-pooler", "")   # direct endpoint: no pgbouncer between us and 10 MB values
        conn = pg8000.native.Connection(
            user=unquote(u.username), password=unquote(u.password or ""), host=host,
            port=u.port or 5432, database=(u.path or "/postgres").lstrip("/"),
            ssl_context=True, timeout=120)
        if not self._ready:
            conn.run("create table if not exists levels_kv ("
                     "key text primary key, value bytea not null, "
                     "updated_at timestamptz not null default now())")
            self._ready = True
        return conn

    def _run(self, sql, **params):
        conn = self._connect()
        try:
            return conn.run(sql, **params)
        finally:
            conn.close()

    def get(self, key):
        rows = self._run("select value from levels_kv where key = :k", k=key)
        return bytes(rows[0][0]) if rows else None

    def put(self, key, value):
        self._run("insert into levels_kv (key, value, updated_at) values (:k, :v, now()) "
                  "on conflict (key) do update set value = excluded.value, updated_at = now()",
                  k=key, v=value)

    def delete(self, key):
        self._run("delete from levels_kv where key = :k", k=key)

    def updated_at(self, key):
        rows = self._run("select extract(epoch from updated_at) from levels_kv where key = :k", k=key)
        return float(rows[0][0]) if rows else None

    def acquire(self):
        rows = self._run(
            "insert into levels_kv (key, value, updated_at) values ('lock', decode('31', 'hex'), now()) "
            "on conflict (key) do update set updated_at = now() "
            "where levels_kv.updated_at < now() - (:ttl * interval '1 second') returning key",
            ttl=LOCK_TTL_S)
        return bool(rows)

    def release(self):
        self.delete("lock")


def default_store():
    directory = os.environ.get("LEVELS_STORE_DIR")
    url = os.environ.get("NEON_DB_DATABASE_URL")
    if directory or not url:
        from bootstrap import RUNTIME_DIR
        return FileStore(directory or os.path.join(RUNTIME_DIR, ".dev_store"))
    return PgStore(url)

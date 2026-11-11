import os
import time
import logging
import threading
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    from psycopg2 import pool as _pg_pool
    logging.info("[DB] Mode: PostgreSQL")
else:
    import sqlite3
    if os.path.isdir("/data"):
        _DB_PATH = "/data/bot.db"
        logging.info("[DB] SQLite → /data/bot.db  (Railway persistent volume)")
    else:
        _DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_dev.db")
        logging.info(f"[DB] SQLite → {_DB_PATH}  (local dev)")
    # main.py's own startup log imports this under its public name; only
    # bound here, same as _DB_PATH, since neither exists in Postgres mode.
    DB_PATH = _DB_PATH

# Postgres connections are pooled. Opening one is a network round trip plus
# authentication, which measured at over 99% of a simple query's time, and one
# button press runs several queries. SQLite is a local file and still opens a
# fresh connection per query.
# At least 1: a pool of 0 would leave every query waiting on the semaphore forever.
POOL_MAX = max(1, int(os.environ.get("DB_POOL_MAX", "10")))
_pool = None
_pool_lock = threading.Lock()
# psycopg2's pool raises when every connection is taken instead of waiting, so
# callers queue here first and a burst of activity waits rather than failing.
_pool_slots = threading.BoundedSemaphore(POOL_MAX)


def db_conn():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(_DB_PATH, check_same_thread=False)


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # minconn is also how many idle connections psycopg2 keeps: a
                # returned connection is closed whenever minconn are already
                # idle. At minconn=1 every query overlapping another opened and
                # closed its own connection, which is the cost pooling removes.
                _pool = _pg_pool.ThreadedConnectionPool(POOL_MAX, POOL_MAX, DATABASE_URL)
    return _pool


def _checkout():
    """Take a pooled connection that is known to still be alive.

    Railway can drop idle connections and the database restarts on redeploy.
    After a restart every idle connection is dead, so keep discarding until a
    live one turns up. Once the dead ones are gone the pool opens a fresh one,
    and if that fails too the database really is down.
    """
    pool = _get_pool()
    for attempt in range(POOL_MAX + 1):
        conn = pool.getconn()
        try:
            with conn.cursor() as probe:
                probe.execute("SELECT 1")
            conn.rollback()
            return conn
        except psycopg2.Error:
            pool.putconn(conn, close=True)
            if attempt == POOL_MAX:
                raise


class _CursorWrapper:
    """Converts %s placeholders to ? when running SQLite."""
    def __init__(self, cur, sqlite_mode):
        self._cur = cur
        self._sq = sqlite_mode

    def execute(self, sql, params=None):
        if self._sq:
            sql = sql.replace("%s", "?")
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(sql, params)

    def fetchone(self):  return self._cur.fetchone()
    def fetchall(self):  return self._cur.fetchall()

    @property
    def lastrowid(self): return self._cur.lastrowid


@contextmanager
def _cursor():
    if not USE_PG:
        conn = db_conn()
        try:
            yield _CursorWrapper(conn.cursor(), True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    with _pool_slots:
        conn = _checkout()
        broken = False
        try:
            yield _CursorWrapper(conn.cursor(), False)
            conn.commit()
        except Exception as e:
            # A connection that died mid-query must not go back into the pool.
            broken = bool(conn.closed) or isinstance(
                e, (psycopg2.OperationalError, psycopg2.InterfaceError))
            if not broken:
                try:
                    conn.rollback()
                except psycopg2.Error:
                    broken = True
            raise
        finally:
            _get_pool().putconn(conn, close=broken)


def init_db():
    conn = db_conn()
    cur = conn.cursor()

    if USE_PG:
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            chat_id        BIGINT PRIMARY KEY,
            username       TEXT,
            first_name     TEXT,
            joined_at      DOUBLE PRECISION,
            verified       INTEGER DEFAULT 0,
            secret_phrase  TEXT,
            warning_msg_id BIGINT
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS orders (
            id             SERIAL PRIMARY KEY,
            chat_id        BIGINT,
            invoice        INTEGER UNIQUE,
            total          DOUBLE PRECISION,
            crypto_amount  DOUBLE PRECISION,
            crypto_symbol  TEXT,
            crypto_address TEXT,
            payment_method TEXT,
            address        TEXT,
            delivery       TEXT,
            discount       TEXT,
            status         TEXT DEFAULT 'pending',
            created_at     DOUBLE PRECISION,
            expires_at     DOUBLE PRECISION,
            completed_at   DOUBLE PRECISION
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS order_items (
            id       SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id),
            name     TEXT,
            qty      DOUBLE PRECISION,
            price    DOUBLE PRECISION
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS cart_items (
            id      SERIAL PRIMARY KEY,
            chat_id BIGINT,
            name    TEXT,
            qty     DOUBLE PRECISION,
            price   DOUBLE PRECISION,
            unit    TEXT DEFAULT 'gr'
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS wishlist (
            chat_id     BIGINT,
            product_key TEXT,
            PRIMARY KEY (chat_id, product_key)
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id      SERIAL PRIMARY KEY,
            chat_id BIGINT,
            sender  TEXT,
            text    TEXT,
            ts      DOUBLE PRECISION
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id          SERIAL PRIMARY KEY,
            date_str    TEXT,
            stars       INTEGER,
            amount      TEXT,
            products    TEXT,
            review_text TEXT,
            created_at  DOUBLE PRECISION
        )''')

        migrations = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS crypto_amount  DOUBLE PRECISION",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS crypto_symbol  TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS crypto_address TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS address        TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery       TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS discount       TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS completed_at   DOUBLE PRECISION",
            "ALTER TABLE users  ADD COLUMN IF NOT EXISTS verified       INTEGER DEFAULT 0",
            "ALTER TABLE users  ADD COLUMN IF NOT EXISTS secret_phrase  TEXT",
            "ALTER TABLE users  ADD COLUMN IF NOT EXISTS warning_msg_id BIGINT",
        ]
        for sql in migrations:
            cur.execute(sql)

    else:
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            chat_id        INTEGER PRIMARY KEY,
            username       TEXT,
            first_name     TEXT,
            joined_at      REAL,
            verified       INTEGER DEFAULT 0,
            secret_phrase  TEXT,
            warning_msg_id INTEGER
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS orders (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id        INTEGER,
            invoice        INTEGER UNIQUE,
            total          REAL,
            crypto_amount  REAL,
            crypto_symbol  TEXT,
            crypto_address TEXT,
            payment_method TEXT,
            address        TEXT,
            delivery       TEXT,
            discount       TEXT,
            status         TEXT DEFAULT 'pending',
            created_at     REAL,
            expires_at     REAL,
            completed_at   REAL
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS order_items (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER REFERENCES orders(id),
            name     TEXT,
            qty      REAL,
            price    REAL
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS cart_items (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            name    TEXT,
            qty     REAL,
            price   REAL,
            unit    TEXT DEFAULT 'gr'
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS wishlist (
            chat_id     INTEGER,
            product_key TEXT,
            PRIMARY KEY (chat_id, product_key)
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            sender  TEXT,
            text    TEXT,
            ts      REAL
        )''')

        cur.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str    TEXT,
            stars       INTEGER,
            amount      TEXT,
            products    TEXT,
            review_text TEXT,
            created_at  REAL
        )''')

        migrations = [
            "ALTER TABLE orders ADD COLUMN crypto_amount  REAL",
            "ALTER TABLE orders ADD COLUMN crypto_symbol  TEXT",
            "ALTER TABLE orders ADD COLUMN crypto_address TEXT",
            "ALTER TABLE orders ADD COLUMN address        TEXT",
            "ALTER TABLE orders ADD COLUMN delivery       TEXT",
            "ALTER TABLE orders ADD COLUMN discount       TEXT",
            "ALTER TABLE orders ADD COLUMN completed_at   REAL",
            "ALTER TABLE users  ADD COLUMN verified       INTEGER DEFAULT 0",
            "ALTER TABLE users  ADD COLUMN secret_phrase  TEXT",
            "ALTER TABLE users  ADD COLUMN warning_msg_id INTEGER",
        ]
        for sql in migrations:
            try:
                cur.execute(sql)
            except Exception:
                pass

    conn.commit()
    cur.close()
    conn.close()


# =============================================================
# USERS
# =============================================================

def db_register_user(chat_id, username, first_name):
    with _cursor() as cur:
        if USE_PG:
            cur.execute(
                '''INSERT INTO users (chat_id, username, first_name, joined_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (chat_id) DO NOTHING''',
                (chat_id, username, first_name, time.time())
            )
        else:
            cur.execute(
                '''INSERT OR IGNORE INTO users (chat_id, username, first_name, joined_at)
                   VALUES (?, ?, ?, ?)''',
                (chat_id, username, first_name, time.time())
            )

def db_set_user_verified(chat_id, phrase):
    with _cursor() as cur:
        cur.execute(
            "UPDATE users SET verified = 1, secret_phrase = %s WHERE chat_id = %s",
            (phrase, chat_id)
        )

def db_load_verified_users():
    with _cursor() as cur:
        cur.execute("SELECT chat_id, secret_phrase FROM users WHERE verified = 1 AND secret_phrase IS NOT NULL")
        return cur.fetchall()

def db_user_count():
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]

def db_get_all_user_ids():
    with _cursor() as cur:
        cur.execute("SELECT chat_id FROM users")
        return [r[0] for r in cur.fetchall()]

def db_get_all_users():
    with _cursor() as cur:
        cur.execute("SELECT chat_id, username, first_name FROM users ORDER BY joined_at DESC")
        return cur.fetchall()

def db_set_warning_msg_id(chat_id, msg_id):
    with _cursor() as cur:
        cur.execute(
            "UPDATE users SET warning_msg_id = %s WHERE chat_id = %s",
            (msg_id, chat_id)
        )

def db_load_warning_msg_ids():
    with _cursor() as cur:
        cur.execute("SELECT chat_id, warning_msg_id FROM users WHERE warning_msg_id IS NOT NULL")
        return cur.fetchall()


# =============================================================
# ORDERS
# =============================================================

def db_save_order(chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
                  payment, address, delivery, discount, items):
    created_at = time.time()
    expires_at = created_at + (3 * 60 * 60)
    with _cursor() as cur:
        if USE_PG:
            cur.execute(
                '''INSERT INTO orders
                   (chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
                    payment_method, address, delivery, discount, status, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                   RETURNING id''',
                (chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
                 payment, address, delivery, discount, created_at, expires_at)
            )
            order_id = cur.fetchone()[0]
        else:
            cur.execute(
                '''INSERT INTO orders
                   (chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
                    payment_method, address, delivery, discount, status, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)''',
                (chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
                 payment, address, delivery, discount, created_at, expires_at)
            )
            order_id = cur.lastrowid
        for item in items:
            cur.execute(
                'INSERT INTO order_items (order_id, name, qty, price) VALUES (%s, %s, %s, %s)',
                (order_id, item["name"], item["qty"], item["price"])
            )
    return created_at, expires_at

def db_get_orders(chat_id):
    with _cursor() as cur:
        cur.execute(
            '''SELECT invoice, total, payment_method, status, created_at, expires_at, completed_at
               FROM orders WHERE chat_id = %s ORDER BY created_at DESC''',
            (chat_id,)
        )
        return cur.fetchall()

def db_get_order(invoice):
    with _cursor() as cur:
        cur.execute(
            '''SELECT id, chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
               payment_method, address, delivery, discount, status, created_at, expires_at, completed_at
               FROM orders WHERE invoice = %s''', (invoice,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            # ORDER BY id keeps lines in the order they were added. SQLite happens
            # to return that anyway; Postgres guarantees no order without it.
            'SELECT name, qty, price FROM order_items WHERE order_id = %s ORDER BY id',
            (row[0],)
        )
        items = cur.fetchall()
    return {
        "id": row[0], "chat_id": row[1], "invoice": row[2], "total": row[3],
        "crypto_amount": row[4], "crypto_symbol": row[5], "crypto_address": row[6],
        "payment": row[7], "address": row[8], "delivery": row[9], "discount": row[10],
        "status": row[11], "created_at": row[12], "expires_at": row[13], "completed_at": row[14],
        "items": [{"name": i[0], "qty": i[1], "price": i[2]} for i in items]
    }

def db_get_pending_orders():
    with _cursor() as cur:
        cur.execute(
            '''SELECT invoice, crypto_amount, crypto_symbol, crypto_address, chat_id, expires_at
               FROM orders WHERE status = 'pending' AND expires_at > %s''', (time.time(),)
        )
        return cur.fetchall()

def db_mark_expired():
    with _cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = 'expired' WHERE status = 'pending' AND expires_at < %s",
            (time.time(),)
        )

def db_complete_order(invoice):
    with _cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = 'completed', completed_at = %s WHERE invoice = %s",
            (time.time(), invoice)
        )

def db_get_all_orders():
    with _cursor() as cur:
        cur.execute(
            '''SELECT o.invoice, o.total, o.payment_method, o.status, o.chat_id, u.username,
               o.created_at, o.address, o.delivery, o.crypto_symbol, o.crypto_amount
               FROM orders o LEFT JOIN users u ON o.chat_id = u.chat_id
               ORDER BY o.created_at DESC'''
        )
        return cur.fetchall()

def db_get_order_items(invoice):
    with _cursor() as cur:
        cur.execute('SELECT id FROM orders WHERE invoice = %s', (invoice,))
        row = cur.fetchone()
        if not row:
            return []
        cur.execute(
            # ORDER BY id keeps lines in the order they were added. SQLite happens
            # to return that anyway; Postgres guarantees no order without it.
            'SELECT name, qty, price FROM order_items WHERE order_id = %s ORDER BY id',
            (row[0],)
        )
        return cur.fetchall()

def db_order_count():
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orders")
        return cur.fetchone()[0]

def db_completed_order_count():
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'")
        return cur.fetchone()[0]

def db_load_existing_expired():
    with _cursor() as cur:
        cur.execute("SELECT invoice FROM orders WHERE status = 'expired'")
        return [r[0] for r in cur.fetchall()]

def db_get_expired_orders():
    with _cursor() as cur:
        cur.execute("SELECT invoice, chat_id FROM orders WHERE status = 'expired'")
        return cur.fetchall()

def db_get_order_status_counts():
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
        pending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM orders WHERE status='completed'")
        completed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM orders WHERE status='expired'")
        expired = cur.fetchone()[0]
    return pending, completed, expired


# =============================================================
# CART
# =============================================================

def db_get_cart(chat_id):
    with _cursor() as cur:
        cur.execute(
            'SELECT name, qty, price, unit FROM cart_items WHERE chat_id = %s ORDER BY id',
            (chat_id,)
        )
        rows = cur.fetchall()
    return [{"name": r[0], "qty": r[1], "price": r[2], "unit": r[3]} for r in rows]

def db_add_to_cart(chat_id, name, qty, price, unit="gr"):
    with _cursor() as cur:
        cur.execute(
            'INSERT INTO cart_items (chat_id, name, qty, price, unit) VALUES (%s, %s, %s, %s, %s)',
            (chat_id, name, qty, price, unit)
        )

def db_remove_from_cart(chat_id, index):
    with _cursor() as cur:
        cur.execute(
            'SELECT id FROM cart_items WHERE chat_id = %s ORDER BY id', (chat_id,)
        )
        rows = cur.fetchall()
        if 0 <= index < len(rows):
            cur.execute('DELETE FROM cart_items WHERE id = %s', (rows[index][0],))

def db_clear_cart(chat_id):
    with _cursor() as cur:
        cur.execute('DELETE FROM cart_items WHERE chat_id = %s', (chat_id,))

def db_get_cart_total(chat_id):
    items = db_get_cart(chat_id)
    return round(sum(i["price"] for i in items), 2)


# =============================================================
# WISHLIST
# =============================================================

def db_get_wishlist(chat_id):
    with _cursor() as cur:
        cur.execute(
            'SELECT product_key FROM wishlist WHERE chat_id = %s', (chat_id,)
        )
        return set(r[0] for r in cur.fetchall())

def db_wishlist_has(chat_id, product_key):
    with _cursor() as cur:
        cur.execute(
            'SELECT 1 FROM wishlist WHERE chat_id = %s AND product_key = %s',
            (chat_id, product_key)
        )
        return bool(cur.fetchone())

def db_toggle_wishlist(chat_id, product_key):
    with _cursor() as cur:
        cur.execute(
            'SELECT 1 FROM wishlist WHERE chat_id = %s AND product_key = %s',
            (chat_id, product_key)
        )
        exists = cur.fetchone()
        if exists:
            cur.execute(
                'DELETE FROM wishlist WHERE chat_id = %s AND product_key = %s',
                (chat_id, product_key)
            )
            return False
        else:
            cur.execute(
                'INSERT INTO wishlist (chat_id, product_key) VALUES (%s, %s)',
                (chat_id, product_key)
            )
            return True


# =============================================================
# CHAT HISTORY
# =============================================================

def db_add_chat_message(chat_id, text, sender, ts=None):
    if ts is None:
        ts = time.time()
    with _cursor() as cur:
        cur.execute(
            'INSERT INTO chat_messages (chat_id, sender, text, ts) VALUES (%s, %s, %s, %s)',
            (chat_id, sender, text[:300], ts)
        )

def db_get_chat_history(chat_id):
    with _cursor() as cur:
        cur.execute(
            'SELECT sender, text, ts FROM chat_messages WHERE chat_id = %s ORDER BY ts',
            (chat_id,)
        )
        rows = cur.fetchall()
    return [{"from": r[0], "text": r[1], "ts": r[2]} for r in rows]

def db_clear_chat_history(chat_id):
    with _cursor() as cur:
        cur.execute('DELETE FROM chat_messages WHERE chat_id = %s', (chat_id,))


# =============================================================
# REVIEWS
# =============================================================

def db_add_review(date_str, stars, amount, products, review_text):
    with _cursor() as cur:
        cur.execute(
            '''INSERT INTO reviews (date_str, stars, amount, products, review_text, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)''',
            (date_str, stars, amount, products, review_text, time.time())
        )

def db_get_reviews():
    with _cursor() as cur:
        cur.execute(
            'SELECT date_str, stars, amount, products, review_text FROM reviews ORDER BY created_at DESC'
        )
        return cur.fetchall()

def db_review_count():
    with _cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM reviews')
        return cur.fetchone()[0]

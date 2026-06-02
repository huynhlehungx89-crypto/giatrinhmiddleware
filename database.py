import sqlite3
import os
from config import DATABASE_PATH

def get_db():
    """Get a database connection."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Return rows as dicts
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
    return conn

def init_db():
    """Initialize all database tables."""
    conn = get_db()
    cursor = conn.cursor()

    # --- USERS TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            is_active     INTEGER DEFAULT 1,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login    TIMESTAMP
        )
    """)

    # --- ODOO SETTINGS TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS odoo_settings (
            id          INTEGER PRIMARY KEY DEFAULT 1,
            url         TEXT,
            database    TEXT,
            username    TEXT,
            api_key     TEXT,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- IMPORT BATCHES TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS import_batches (
            id              TEXT PRIMARY KEY,
            filename        TEXT,
            source          TEXT DEFAULT 'VINMART',
            status          TEXT DEFAULT 'UPLOADED',
            total_rows      INTEGER DEFAULT 0,
            valid_orders    INTEGER DEFAULT 0,
            error_orders    INTEGER DEFAULT 0,
            success_orders  INTEGER DEFAULT 0,
            skipped_orders  INTEGER DEFAULT 0,
            failed_orders   INTEGER DEFAULT 0,
            uploaded_by     TEXT,
            uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            imported_at     TIMESTAMP,
            FOREIGN KEY (uploaded_by) REFERENCES users(id)
        )
    """)

    # --- IMPORT ORDERS TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS import_orders (
            id              TEXT PRIMARY KEY,
            batch_id        TEXT NOT NULL,
            order_ref       TEXT,
            store_code      TEXT,
            store_name      TEXT,
            order_date      TEXT,
            delivery_date   TEXT,
            line_count      INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'PENDING',
            error_message   TEXT,
            odoo_order_id   INTEGER,
            odoo_order_name TEXT,
            raw_data        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES import_batches(id)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized successfully.")

def seed_admin():
    """Create default admin user if no users exist."""
    from passlib.context import CryptContext
    import uuid
    from config import ADMIN_USERNAME, ADMIN_PASSWORD

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    conn = get_db()
    cursor = conn.cursor()

    # Check if any user exists
    existing = cursor.execute("SELECT id FROM users LIMIT 1").fetchone()
    if not existing:
        admin_id = str(uuid.uuid4())
        password_hash = pwd_context.hash(ADMIN_PASSWORD)
        cursor.execute("""
            INSERT INTO users (id, username, password_hash, role, is_active)
            VALUES (?, ?, ?, 'admin', 1)
        """, (admin_id, ADMIN_USERNAME, password_hash))
        conn.commit()
        print(f"✅ Default admin user created: {ADMIN_USERNAME}")
    else:
        print("✅ Users already exist, skipping seed.")

    conn.close()

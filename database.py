import sqlite3
import os

DB_PATH = "fpl_helper.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            fpl_team_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER NOT NULL,
            gameweek INTEGER,
            total_points INTEGER,
            overall_rank INTEGER,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS squad_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            player_fpl_id INTEGER NOT NULL,
            player_name TEXT,
            position TEXT,
            is_starter INTEGER DEFAULT 1,
            is_captain INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            player_out_name TEXT,
            player_in_name TEXT,
            gameweek INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)

    conn.commit()
    conn.close()
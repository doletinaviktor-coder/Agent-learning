"""
Perzistentná pamäť konverzácie cez SQLite.

Doteraz sme históriu držali v RAM (slovník v agent.py) — pri reštarte servera
sa vymazala. Teraz ju ukladáme do súboru `conversations.db`, takže agent si
pamätá konverzáciu aj po reštarte.

SQLite je vstavaná v Pythone (modul `sqlite3`) — žiadna inštalácia, žiadny
samostatný databázový server. Pre produkciu s viacerými používateľmi by si
neskôr prešiel na Postgres, ale princíp je rovnaký.
"""

import sqlite3
from contextlib import closing

DB_PATH = "conversations.db"


def init_db():
    """Vytvorí tabuľku `messages`, ak ešte neexistuje. Volá sa raz pri štarte."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,          -- telefónne číslo používateľa
                role        TEXT NOT NULL,          -- 'user' alebo 'assistant'
                content     TEXT NOT NULL,          -- samotný text správy
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def add_message(user_id: str, role: str, content: str):
    """Uloží jednu správu do DB."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        conn.commit()


def get_history(user_id: str, limit: int = 20):
    """Vráti posledných `limit` správ daného používateľa v poradí (najstaršia → najnovšia),
    pripravené rovno pre Claude API (zoznam {'role':..., 'content':...})."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    # rows sú od najnovšej; otočíme na chronologické poradie.
    messages = [{"role": role, "content": content} for role, content in reversed(rows)]

    # Claude vyžaduje, aby PRVÁ správa v zozname bola od 'user'. Ak nám orezanie
    # odseklo históriu tak, že začína odpoveďou asistenta, zahodíme úvodné
    # asistentské správy, kým nezačneme user-om.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    return messages

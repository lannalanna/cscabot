"""
Миграция пользователей из data/res.txt в таблицу users (data/bot.db).
Использует синхронный sqlite3 для быстрого однократного запуска.
"""
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, Optional


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(BASE_DIR, "data"))
RES_PATH = os.path.join(DATA_DIR, "res.txt")
DB_PATH = os.environ.get("BOT_DB_PATH", os.path.join(DATA_DIR, "bot.db"))


def parse_res_file() -> Dict[int, dict]:
    """
    Разбирает data/res.txt и возвращает словарь user_id -> {username, language_code}.
    """
    users: Dict[int, dict] = {}

    if not os.path.exists(RES_PATH):
        print(f"Файл {RES_PATH} не найден, мигрировать нечего.")
        return users

    language_re = re.compile(r"language_code='([^']*)'")

    with open(RES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split("\t")
            if len(parts) < 3:
                continue

            user_id_str = parts[1]
            username = (parts[2] or "").strip() or None

            try:
                user_id = int(user_id_str)
            except ValueError:
                continue

            language_code = None
            if len(parts) >= 4 and parts[3] == "status":
                tail = "\t".join(parts[4:])
                m = language_re.search(tail)
                if m:
                    language_code = (m.group(1) or "").strip() or None

            if user_id not in users:
                users[user_id] = {"username": username, "language_code": language_code}
            else:
                u = users[user_id]
                if u["language_code"] is None and language_code:
                    u["language_code"] = language_code
                if (u["username"] is None or u["username"] == "") and username:
                    u["username"] = username

    return users


def migrate_users_from_res() -> None:
    users = parse_res_file()
    if not users:
        print("Пользователи в res.txt не найдены или файл пуст.")
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    try:
        now = datetime.now().isoformat()
        count = 0
        for user_id, data in users.items():
            username = data.get("username")
            language_code = data.get("language_code")
            cur = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            exists = cur.fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, language_code = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (username, language_code, now, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users (id, username, language_code, source, invited_by, created_at, updated_at)
                    VALUES (?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (user_id, username, language_code, now, now),
                )
            count += 1
        conn.commit()
        print(f"Миграция завершена. Обновлено/создано пользователей: {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_users_from_res()

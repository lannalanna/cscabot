"""Вывод id и username всех пользователей из таблицы users (data/bot.db)."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("BOT_DB_PATH", os.path.join(BASE_DIR, "data", "bot.db"))

def main():
    if not os.path.exists(DB_PATH):
        print(f"БД не найдена: {DB_PATH}")
        return
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, username FROM users ORDER BY id").fetchall()
    conn.close()
    for uid, username in rows:
        print(f"{uid}\t{username or '(нет)'}")

if __name__ == "__main__":
    main()

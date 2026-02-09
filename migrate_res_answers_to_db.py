"""
Импорт ответов пользователей из data/res.txt в таблицы answers и progress.

Формат лога res.txt (tab-separated):
  Общие поля: datetime, user_id, username, [далее зависит от типа записи]

  Типы записей (4-е поле — action/topic):
  - Ответ на вопрос (новая версия):
    datetime, user_id, username, TOPIC, question_index, chosen_index, correct
    Пример: 2026-02-09 19:29:54.561199	6464413092	Nitsyyyy	functions	0	1	1
    Тема может быть из нескольких слов: "real numbers", "trigonometry (simple)", "complex numbers".

  - Ответ на вопрос (старая версия, без темы — считаем тему "sets"):
    datetime, user_id, username, question_index, chosen_index, correct
    Пример: 2025-12-25 08:56:20.028667	780221999	svetlana_shorina	0	1	1

  - Не ответы (пропускаем): start, next, status, message, explain, end, invite, log
"""
import os
import re
import sqlite3
from datetime import datetime
from typing import List, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(BASE_DIR, "data"))
RES_PATH = os.path.join(DATA_DIR, "res.txt")
DB_PATH = os.environ.get("BOT_DB_PATH", os.path.join(DATA_DIR, "bot.db"))

SKIP_ACTIONS = frozenset(("start", "next", "status", "message", "explain", "end", "invite", "log"))


def parse_answer_lines() -> List[Tuple[str, int, str, int, int, int]]:
    """
    Читает res.txt и возвращает список кортежей:
    (created_at, user_id, topic, question_index, chosen_index, correct).
    """
    rows = []
    if not os.path.exists(RES_PATH):
        print(f"Файл {RES_PATH} не найден.")
        return rows

    with open(RES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            user_id_str = parts[1]
            if not user_id_str.isdigit():
                continue
            user_id = int(user_id_str)
            created_at = parts[0].strip()

            # Старый формат: 6 полей — dt, id, username, j, chosen, correct (тема = sets)
            if len(parts) == 6 and parts[3].isdigit() and parts[4].isdigit() and parts[5].isdigit():
                topic = "sets"
                question_index = int(parts[3])
                chosen_index = int(parts[4])
                correct = 1 if parts[5].strip() == "1" else 0
                rows.append((created_at, user_id, topic, question_index, chosen_index, correct))
                continue

            # Новый формат: 7+ полей — dt, id, username, topic (или topic1 topic2), j, chosen, correct
            if len(parts) >= 7 and parts[4].isdigit() and parts[5].isdigit() and parts[6].isdigit():
                action_or_topic = parts[3].strip()
                if action_or_topic.lower() in SKIP_ACTIONS:
                    continue
                topic = " ".join(parts[3:-3]).strip() if len(parts) > 7 else action_or_topic
                question_index = int(parts[-3])
                chosen_index = int(parts[-2])
                correct = 1 if parts[-1].strip() == "1" else 0
                rows.append((created_at, user_id, topic, question_index, chosen_index, correct))

    return rows


def ensure_user_exists(conn: sqlite3.Connection, user_id: int) -> None:
    """Добавляет пользователя в users, если его ещё нет (минимальная запись для FK)."""
    cur = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
    if cur.fetchone():
        return
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, language_code, source, invited_by, created_at, updated_at) VALUES (?, NULL, NULL, NULL, NULL, ?, ?)",
        (user_id, now, now),
    )


def migrate_answers() -> None:
    answers = parse_answer_lines()
    if not answers:
        print("Ответы в res.txt не найдены или файл пуст.")
        return

    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    try:
        # Уникальные user_id — убедиться, что все есть в users
        user_ids = {r[1] for r in answers}
        for uid in user_ids:
            ensure_user_exists(conn, uid)
        conn.commit()

        inserted = 0
        for created_at, user_id, topic, question_index, chosen_index, correct in answers:
            conn.execute(
                """
                INSERT INTO answers (user_id, topic, question_index, chosen_index, correct, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, topic, question_index, chosen_index, correct, created_at),
            )
            inserted += 1
        conn.commit()
        print(f"Импортировано ответов: {inserted}")

        # Пересчёт progress по данным из answers
        conn.execute("DELETE FROM progress")
        conn.execute("""
            INSERT INTO progress (user_id, topic, last_question_index, total_answered, total_correct, updated_at)
            SELECT
                user_id,
                topic,
                MAX(question_index) + 1,
                COUNT(*),
                SUM(correct),
                ?
            FROM answers
            GROUP BY user_id, topic
        """, (datetime.now().isoformat(),))
        conn.commit()
        print("Таблица progress пересчитана по импортированным ответам.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_answers()

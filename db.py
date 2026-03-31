"""
Модуль для работы с базой данных SQLite.
Хранит информацию о пользователях, их ответах и прогрессе.
"""
import os
import aiosqlite
from datetime import datetime
from typing import Optional, List, Dict, Tuple


# Путь к базе данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Приоритет выбора пути к БД:
# 1) Переменная окружения BOT_DB_PATH (если задана)
# 2) Директория из BOT_DATA_DIR (если задана) + bot.db
# 3) Локальная ./data/bot.db рядом с модулем
_data_dir_from_env = os.environ.get("BOT_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.environ.get("BOT_DB_PATH", os.path.join(_data_dir_from_env, "bot.db"))


async def init_db() -> aiosqlite.Connection:
    """
    Инициализация базы данных: создание директории и таблиц.
    Возвращает соединение с БД.
    """
    # Создаём директорию data, если её нет
    db_dir = os.path.dirname(DB_PATH)
    os.makedirs(db_dir, exist_ok=True)
    
    # Подключаемся к БД
    conn = await aiosqlite.connect(DB_PATH)
    
    # Создаём таблицы
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            language_code TEXT,
            language TEXT,
            source TEXT,
            invited_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            question_index INTEGER NOT NULL,
            chosen_index INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            last_question_index INTEGER NOT NULL DEFAULT 0,
            total_answered INTEGER NOT NULL DEFAULT 0,
            total_correct INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, topic),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # Таблица оплат в Telegram Stars (XTR)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS star_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            star_count INTEGER NOT NULL,
            purpose TEXT,
            telegram_payment_charge_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # Индексы для ускорения запросов
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_answers_user_topic 
        ON answers(user_id, topic, question_index)
    """)
    
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_answers_user 
        ON answers(user_id, created_at)
    """)
    
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_star_payments_user_purpose
        ON star_payments(user_id, purpose)
    """)

    # Сохранённые рекомендации по проблемным темам после экзаменов
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_exam_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            exam_type TEXT NOT NULL,
            topics_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_exam_recommendations_user_exam
        ON user_exam_recommendations(user_id, exam_type, created_at)
    """)

    # Факт завершения экзаменов пользователем (не очищается clear_exam_answers)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_completed_exams (
            user_id INTEGER NOT NULL,
            exam_type TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, exam_type),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_completed_exams_user
        ON user_completed_exams(user_id, completed_at)
    """)
    
    # Состояние режима "Все задачи по математике" (для восстановления после перезапуска)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_math_all_state (
            user_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Миграция: добавляем поле language в users, если база старая
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN language TEXT")
    except Exception:
        # Колонка уже существует или ALTER не применим — это ок
        pass
    # Миграция: добавляем поле exam_language в users, если база старая
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN exam_language TEXT")
    except Exception:
        # Колонка уже существует или ALTER не применим — это ок
        pass
    
    await conn.commit()
    return conn


def parse_source(start_text: Optional[str]) -> Optional[str]:
    """
    Парсит текст команды /start и возвращает значение для поля source.
    """
    if not start_text:
        return None
    
    start_text = start_text.lower().strip()
    
    if 'stepik' in start_text:
        return 'stepik'
    elif 'cscagroup' in start_text:
        return 'cscagroup'
    elif start_text.startswith('invite'):
        return 'invite'
    # Можно добавить другие варианты здесь
    
    return None


async def ensure_user(conn: aiosqlite.Connection, user, start_text: Optional[str] = None) -> None:
    """
    Создаёт или обновляет пользователя в БД.
    
    Args:
        conn: соединение с БД
        user: объект пользователя из aiogram (message.from_user)
        start_text: текст команды после /start (например, "stepik" или "inviteabc123")
    """
    now = datetime.now().isoformat()
    new_source = parse_source(start_text)
    
    # Проверяем, существует ли пользователь
    cursor = await conn.execute(
        "SELECT id, source FROM users WHERE id = ?",
        (user.id,)
    )
    row = await cursor.fetchone()
    
    if row:
        _, old_source = row
        # Если у пользователя уже был непустой source, а новое значение пустое,
        # оставляем старое значение.
        effective_source = old_source if (old_source and not new_source) else new_source
        # Обновляем существующего пользователя
        await conn.execute("""
            UPDATE users 
            SET username = ?, language_code = ?, source = ?, invited_by = ?, updated_at = ?
            WHERE id = ?
        """, (
            user.username,
            user.language_code,
            effective_source,
            start_text if start_text else None,
            now,
            user.id
        ))
    else:
        # Создаём нового пользователя
        await conn.execute("""
            INSERT INTO users (id, username, language_code, language, source, invited_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username,
            user.language_code,
            user.language_code,
            new_source,
            start_text if start_text else None,
            now,
            now
        ))
    
    await conn.commit()


async def save_answer(
    conn: aiosqlite.Connection,
    user_id: int,
    topic: str,
    question_index: int,
    chosen_index: int,
    correct: bool
) -> None:
    """
    Сохраняет ответ пользователя и обновляет прогресс.
    
    Args:
        conn: соединение с БД
        user_id: ID пользователя
        topic: название темы
        question_index: индекс вопроса (j)
        chosen_index: выбранный вариант ответа (0-4)
        correct: правильность ответа (True/False)
    """
    now = datetime.now().isoformat()
    
    # Сохраняем ответ
    await conn.execute("""
        INSERT INTO answers (user_id, topic, question_index, chosen_index, correct, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        topic,
        question_index,
        chosen_index,
        1 if correct else 0,
        now
    ))
    
    # Обновляем прогресс
    # Сначала проверяем, есть ли запись прогресса для этой темы
    cursor = await conn.execute("""
        SELECT last_question_index, total_answered, total_correct
        FROM progress
        WHERE user_id = ? AND topic = ?
    """, (user_id, topic))
    
    row = await cursor.fetchone()
    
    if row:
        # Обновляем существующую запись
        old_last_index, old_answered, old_correct = row
        new_last_index = max(old_last_index, question_index) + 1
        new_answered = old_answered + 1
        new_correct = old_correct + (1 if correct else 0)
        
        await conn.execute("""
            UPDATE progress
            SET last_question_index = ?, total_answered = ?, total_correct = ?, updated_at = ?
            WHERE user_id = ? AND topic = ?
        """, (new_last_index, new_answered, new_correct, now, user_id, topic))
    else:
        # Создаём новую запись прогресса
        await conn.execute("""
            INSERT INTO progress (user_id, topic, last_question_index, total_answered, total_correct, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            topic,
            question_index + 1,  # следующий вопрос
            1,  # всего ответов
            1 if correct else 0,  # правильных
            now
        ))
    
    await conn.commit()


async def get_training_answers_today_counts(
    conn: aiosqlite.Connection, user_id: int
) -> Tuple[int, int]:
    """
    Статистика ответов в тренировке (таблица answers) с полуночи локального времени сервера.
    Возвращает (число неверных, всего ответов за сегодня).
    """
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    cursor = await conn.execute(
        """
        SELECT
            SUM(CASE WHEN correct = 0 THEN 1 ELSE 0 END),
            COUNT(*)
        FROM answers
        WHERE user_id = ? AND created_at >= ?
        """,
        (user_id, start),
    )
    row = await cursor.fetchone()
    if not row or row[1] is None or row[1] == 0:
        return 0, 0
    wrong = int(row[0] or 0)
    total = int(row[1])
    return wrong, total


async def get_progress(conn: aiosqlite.Connection, user_id: int) -> List[Dict]:
    """
    Возвращает прогресс пользователя по всем темам.
    
    Returns:
        Список словарей с ключами: topic, last_question_index, total_answered, total_correct
    """
    cursor = await conn.execute("""
        SELECT topic, last_question_index, total_answered, total_correct
        FROM progress
        WHERE user_id = ?
        ORDER BY topic
    """, (user_id,))
    
    rows = await cursor.fetchall()
    return [
        {
            'topic': row[0],
            'last_question_index': row[1],
            'total_answered': row[2],
            'total_correct': row[3]
        }
        for row in rows
    ]


async def get_user_stats(conn: aiosqlite.Connection, user_id: int) -> Dict:
    """
    Возвращает общую статистику пользователя.
    
    Returns:
        Словарь с ключами:
        - total_answered: всего ответов
        - total_correct: всего правильных
        - by_topic: список словарей по темам (topic, answered, correct, accuracy)
    """
    # Общая статистика
    cursor = await conn.execute("""
        SELECT 
            COUNT(*) as total_answered,
            SUM(correct) as total_correct
        FROM answers
        WHERE user_id = ?
    """, (user_id,))
    
    row = await cursor.fetchone()
    total_answered = row[0] or 0
    total_correct = row[1] or 0
    
    # Статистика по темам
    cursor = await conn.execute("""
        SELECT 
            topic,
            COUNT(*) as answered,
            SUM(correct) as correct
        FROM answers
        WHERE user_id = ?
        GROUP BY topic
        ORDER BY topic
    """, (user_id,))
    
    rows = await cursor.fetchall()
    by_topic = []
    for row in rows:
        topic, answered, correct = row
        accuracy = (correct / answered * 100) if answered > 0 else 0
        by_topic.append({
            'topic': topic,
            'answered': answered,
            'correct': correct,
            'accuracy': accuracy
        })
    
    return {
        'total_answered': total_answered,
        'total_correct': total_correct,
        'by_topic': by_topic
    }


def _get_subtopic_getter(kapibara: dict):
    """Возвращает функцию get_subtopic(topic, question_index) для использования в get_user_stats_with_subtopics."""
    def get_subtopic(topic: str, question_index: int) -> str:
        if topic not in kapibara:
            return "general"
        questions = kapibara[topic]
        if question_index < 0 or question_index >= len(questions):
            return "general"
        return (questions[question_index].get("subtopic") or "").strip() or "general"
    return get_subtopic


async def get_user_stats_with_subtopics(
    conn: aiosqlite.Connection,
    user_id: int,
    get_subtopic,
) -> Dict:
    """
    Статистика пользователя с разбивкой по темам и подтемам.
    get_subtopic(topic, question_index) -> str — функция, возвращающая подтему вопроса (из данных бота).
    """
    cursor = await conn.execute("""
        SELECT topic, question_index, correct
        FROM answers
        WHERE user_id = ?
        ORDER BY topic, question_index
    """, (user_id,))
    rows = await cursor.fetchall()

    # Агрегация по теме и подтеме
    by_topic_raw = {}  # topic -> { subtopic -> (answered, correct) }
    total_answered = 0
    total_correct = 0

    for topic, question_index, correct in rows:
        sub = get_subtopic(topic, question_index)
        if topic not in by_topic_raw:
            by_topic_raw[topic] = {}
        if sub not in by_topic_raw[topic]:
            by_topic_raw[topic][sub] = [0, 0]
        by_topic_raw[topic][sub][0] += 1
        by_topic_raw[topic][sub][1] += 1 if correct else 0
        total_answered += 1
        total_correct += 1 if correct else 0

    by_topic = []
    for topic in sorted(by_topic_raw.keys()):
        topic_answered = 0
        topic_correct = 0
        subtopics = []
        for sub in sorted(by_topic_raw[topic].keys()):
            ans, cor = by_topic_raw[topic][sub]
            topic_answered += ans
            topic_correct += cor
            acc = (cor / ans * 100) if ans > 0 else 0
            subtopics.append({
                "subtopic": sub,
                "answered": ans,
                "correct": cor,
                "accuracy": acc,
            })
        acc_t = (topic_correct / topic_answered * 100) if topic_answered > 0 else 0
        by_topic.append({
            "topic": topic,
            "answered": topic_answered,
            "correct": topic_correct,
            "accuracy": acc_t,
            "subtopics": subtopics,
        })

    return {
        "total_answered": total_answered,
        "total_correct": total_correct,
        "by_topic": by_topic,
    }


async def get_users_by_source(conn: aiosqlite.Connection, source: str) -> List[int]:
    """
    Возвращает список ID пользователей с указанным source.
    Используется для получения списка пользователей с особым поведением.
    
    Args:
        conn: соединение с БД
        source: значение поля source (например, 'stepik')
    
    Returns:
        Список user_id
    """
    cursor = await conn.execute("""
        SELECT id FROM users WHERE source = ?
    """, (source,))
    
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def get_all_users(conn: aiosqlite.Connection) -> List[Tuple[int, Optional[str], Optional[str], Optional[str]]]:
    """
    Возвращает список всех пользователей с основными полями.
    
    Returns:
        Список кортежей (id, username, source, invited_by)
    """
    cursor = await conn.execute("""
        SELECT id, username, source, invited_by
        FROM users
    """)
    rows = await cursor.fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def clear_user_stats(conn: aiosqlite.Connection, user_id: int) -> None:
    """
    Полностью очищает статистику пользователя:
    - удаляет все ответы из таблицы answers,
    - удаляет прогресс по темам из таблицы progress.
    """
    await conn.execute(
        "DELETE FROM answers WHERE user_id = ?",
        (user_id,),
    )
    await conn.execute(
        "DELETE FROM progress WHERE user_id = ?",
        (user_id,),
    )
    await conn.execute(
        "DELETE FROM user_math_all_state WHERE user_id = ?",
        (user_id,),
    )
    await conn.commit()


async def clear_exam_answers(conn: aiosqlite.Connection, user_id: int, exam_topic: str) -> None:
    """
    Удаляет все ответы пользователя по указанному экзамену (теме).
    """
    await conn.execute(
        "DELETE FROM answers WHERE user_id = ? AND topic = ?",
        (user_id, exam_topic),
    )
    await conn.commit()


async def get_exam_answers(conn: aiosqlite.Connection, user_id: int, exam_topic: str) -> Dict[int, Dict]:
    """
    Возвращает все ответы пользователя по экзамену.
    
    Args:
        conn: соединение с БД
        user_id: ID пользователя
        exam_topic: название темы экзамена (например, "exam_25jan")
    
    Returns:
        Словарь: {question_index: {"chosen_index": int, "correct": bool}}
        где question_index - индекс вопроса в экзамене (0-47)
    """
    cursor = await conn.execute("""
        SELECT question_index, chosen_index, correct
        FROM answers
        WHERE user_id = ? AND topic = ?
        ORDER BY question_index
    """, (user_id, exam_topic))
    
    rows = await cursor.fetchall()
    result = {}
    for row in rows:
        question_index, chosen_index, correct = row
        result[question_index] = {
            "chosen_index": chosen_index,
            "correct": bool(correct)
        }
    return result


async def save_star_payment(
    conn: aiosqlite.Connection,
    user_id: int,
    currency: str,
    total_amount: int,
    purpose: str,
    telegram_payment_charge_id: str,
) -> None:
    """
    Сохраняет информацию об оплате в Telegram Stars.
    
    Для валюты XTR total_amount передаётся в минимальных единицах (1 Star = 100 единиц),
    поэтому количество звёзд star_count = total_amount // 100.
    """
    now = datetime.now().isoformat()
    if currency == "XTR":
        star_count = total_amount // 100
    else:
        star_count = 0
    await conn.execute(
        """
        INSERT INTO star_payments (user_id, currency, total_amount, star_count, purpose, telegram_payment_charge_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, currency, total_amount, star_count, purpose, telegram_payment_charge_id, now),
    )
    await conn.commit()


async def user_has_access_by_payment(conn: aiosqlite.Connection, user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя хотя бы одна оплата, дающая доступ к режиму экзамена/Mock Exam.
    """
    cursor = await conn.execute(
        """
        SELECT 1
        FROM star_payments
        WHERE user_id = ? AND purpose = ?
        LIMIT 1
        """,
        (user_id, "exam_access"),
    )
    row = await cursor.fetchone()
    return row is not None


async def mark_user_exam_completed(
    conn: aiosqlite.Connection, user_id: int, exam_type: str
) -> None:
    """
    Фиксирует факт завершения экзамена пользователем.
    Повторный вызов для того же (user_id, exam_type) обновляет completed_at.
    """
    now = datetime.now().isoformat()
    await conn.execute(
        """
        INSERT INTO user_completed_exams (user_id, exam_type, completed_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, exam_type) DO UPDATE SET completed_at = excluded.completed_at
        """,
        (user_id, exam_type, now),
    )
    await conn.commit()


async def user_has_any_completed_exam(conn: aiosqlite.Connection, user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя хотя бы один завершённый экзамен.
    """
    cursor = await conn.execute(
        """
        SELECT 1
        FROM user_completed_exams
        WHERE user_id = ?
        LIMIT 1
        """,
        (user_id,),
    )
    row = await cursor.fetchone()
    return row is not None


async def get_user_created_at(conn: aiosqlite.Connection, user_id: int) -> Optional[str]:
    """
    Возвращает created_at пользователя из таблицы users (в формате ISO-строки) или None.
    """
    cursor = await conn.execute(
        "SELECT created_at FROM users WHERE id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def get_user_language(conn: aiosqlite.Connection, user_id: int) -> Optional[str]:
    """
    Возвращает сохранённый язык интерфейса пользователя из users.language или None.
    """
    cursor = await conn.execute(
        "SELECT language FROM users WHERE id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row and row[0] else None


async def set_user_language(conn: aiosqlite.Connection, user_id: int, language: str) -> None:
    """
    Сохраняет язык интерфейса пользователя в users.language.
    """
    now = datetime.now().isoformat()
    await conn.execute(
        "UPDATE users SET language = ?, updated_at = ? WHERE id = ?",
        (language, now, user_id),
    )
    await conn.commit()


async def get_user_exam_language(conn: aiosqlite.Connection, user_id: int) -> Optional[str]:
    """
    Возвращает сохранённый язык экзамена пользователя из users.exam_language или None.
    """
    cursor = await conn.execute(
        "SELECT exam_language FROM users WHERE id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row and row[0] else None


async def set_user_exam_language(conn: aiosqlite.Connection, user_id: int, language: str) -> None:
    """
    Сохраняет язык экзамена пользователя в users.exam_language.
    """
    now = datetime.now().isoformat()
    await conn.execute(
        "UPDATE users SET exam_language = ?, updated_at = ? WHERE id = ?",
        (language, now, user_id),
    )
    await conn.commit()


async def save_user_exam_recommendations(
    conn: aiosqlite.Connection,
    user_id: int,
    exam_type: str,
    topics_json: str,
) -> None:
    """
    Сохраняет рекомендации по темам после завершения экзамена.
    topics_json: JSON-строка со списком тем в порядке важности.
    """
    now = datetime.now().isoformat()
    await conn.execute(
        """
        INSERT INTO user_exam_recommendations (user_id, exam_type, topics_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, exam_type, topics_json, now),
    )
    await conn.commit()


async def get_user_exam_recommendations_rows(
    conn: aiosqlite.Connection, user_id: int
) -> List[Tuple[str, str]]:
    """
    Возвращает все сохранённые рекомендации пользователя:
    [(exam_type, topics_json), ...] от старых к новым.
    """
    cursor = await conn.execute(
        """
        SELECT exam_type, topics_json
        FROM user_exam_recommendations
        WHERE user_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [(row[0], row[1]) for row in rows]


async def get_user_answers_for_topics(
    conn: aiosqlite.Connection,
    user_id: int,
    topics: List[str],
) -> List[Tuple[str, int, int, int, str]]:
    """
    Возвращает ответы пользователя по указанным темам:
    [(topic, question_index, chosen_index, correct, created_at), ...]
    """
    if not topics:
        return []
    placeholders = ",".join(["?"] * len(topics))
    cursor = await conn.execute(
        f"""
        SELECT topic, question_index, chosen_index, correct, created_at
        FROM answers
        WHERE user_id = ? AND topic IN ({placeholders})
        ORDER BY created_at ASC, id ASC
        """,
        [user_id] + list(topics),
    )
    rows = await cursor.fetchall()
    return [(row[0], row[1], row[2], row[3], row[4]) for row in rows]


async def save_user_math_all_state(
    conn: aiosqlite.Connection, user_id: int, state_json: str
) -> None:
    now = datetime.now().isoformat()
    await conn.execute(
        """
        INSERT INTO user_math_all_state (user_id, state_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (user_id, state_json, now),
    )
    await conn.commit()


async def get_user_math_all_state(
    conn: aiosqlite.Connection, user_id: int
) -> Optional[str]:
    cursor = await conn.execute(
        "SELECT state_json FROM user_math_all_state WHERE user_id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None

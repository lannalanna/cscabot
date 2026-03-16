#!/usr/bin/env python
import asyncio
import logging
import traceback
import datetime
import json
import os
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

API_TOKEN = os.environ.get('BOT_TOKEN', '8162784129:AAHbZZ1JZONUH8sujANe4txembuBeRsXaCM')


API_TOKEN = os.environ.get('BOT_TOKEN', '8211322326:AAFbYxJ-qI0ERUJOUygYSbOzAfXK-vjt0us')
# Базовые пути и выбор директории данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def detect_data_dir() -> str:
    """
    Определяет директорию с данными по наличию файла data.txt.
    Последовательность проверки:
    1) Значение переменной окружения BOT_DATA_DIR (если задано и содержит data.txt)
    2) Абсолютная директория /data/ (если содержит data.txt)
    3) Локальная директория ./data/ рядом со скриптом (если содержит data.txt)
    Если файл не найден, возвращает локальную ./data/ и пишет предупреждение.
    """
    # 1. Явно заданная через окружение
    env_dir = os.environ.get("BOT_DATA_DIR")
    candidates = []
    if env_dir:
        candidates.append(env_dir)

    # 2. Абсолютная /data/
    candidates.append(os.path.join(os.sep, "data"))

    # 3. Локальная ./data/ рядом со скриптом
    candidates.append(os.path.join(BASE_DIR, "data"))

    for d in candidates:
        if not d:
            continue
        data_txt = os.path.join(d, "data.txt")
        if os.path.exists(data_txt):
            return d

    # Файл не найден ни в одной из кандидатов — используем локальную data/
    fallback = os.path.join(BASE_DIR, "data")
    logging.warning(
        "Файл data.txt не найден ни в /data/, ни в локальной data/. "
        "Используем директорию по умолчанию: %s",
        fallback,
    )
    return fallback


DATA_DIR = detect_data_dir()

# Гарантируем, что все модули (db, миграции и т.п.) используют ту же директорию данных
os.environ["BOT_DATA_DIR"] = DATA_DIR

# Создаём директорию данных, если её нет
os.makedirs(DATA_DIR, exist_ok=True)

# Импортируем db после установки BOT_DATA_DIR,
# чтобы он взял корректный путь к базе данных.
import db

# Ссылка для оплаты доступа к экзамену (можно переопределить через переменную окружения PAY_URL)
PAY_URL = os.environ.get("PAY_URL", "https://example.com/csca-pay")


topic_links = { 'Algebraic and geometric mean' : 'https://t.me/csca_math_exam/22',
                'parabola' : 'https://t.me/csca_math_exam/22',
                'trigonometry' : 'https://t.me/csca_math_exam/20',
                'hyperbola' : 'https://t.me/csca_math_exam/33',
                'sets' : 'https://t.me/csca_math_exam/10',
                'functions' : 'https://t.me/csca_math_exam/14',
                'geometry' : 'https://t.me/csca_math_exam/18',
                'sequences' : 'https://t.me/csca_math_exam/16',
                'inequalities' : 'https://t.me/csca_math_exam/12',
                'logarithnic functions' : 'https://t.me/csca_math_exam/24',
                'complex numbers': 'https://t.me/csca_math_exam/28',
                'physics' : 'https://t.me/csca_math_exam/55',
                'probability' : 'https://t.me/csca_math_exam/26'
                 }

# Маппинг старых имён тем на новые (для обратной совместимости callback_data)
TOPIC_ALIASES = {"algebra": "Algebraic and geometric mean"}

# Список пользователей с особым поведением (будет загружен из БД при старте)
stepik = set()
cscagroup = set()


usrh={}
usrok={}
usrno={}
topics = []

kapibara = {}

# Загружаем вопросы из одного агрегированного файла data.txt
try:
    data_path = os.path.join(DATA_DIR, 'data.txt')
    with open(data_path, 'r', encoding='utf-8') as f:
        kpb_raw = json.load(f)
    # В массиве могут попадаться не-словари (вложенные списки и т.п.) — оставляем только словари
    kpb = [x for x in (kpb_raw if isinstance(kpb_raw, list) else [kpb_raw]) if isinstance(x, dict)]

    # Собираем список тем
    for k in kpb:
        topic = k.get('topic')
        if topic and topic not in topics:
            topics.append(topic)

    # Фиксированный порядок тем в главном меню
    DESIRED_TOPIC_ORDER = [
        "sets",
        "inequalities",
        "functions",
        "trigonometry (simple)",
        "trigonometry",
        "geometry",
        "conic curves",
        "logarithmic functions",
        "Algebraic and geometric mean",
        "sequences",
        "complex numbers",
        "probability",
        "physics",
    ]
    order_index = {name: i for i, name in enumerate(DESIRED_TOPIC_ORDER)}
    topics.sort(key=lambda t: (order_index.get(t, len(DESIRED_TOPIC_ORDER)), t))

    # Группируем вопросы по темам (только элементы-словари)
    for top in topics:
        kapibara[top] = [x for x in kpb if x.get('topic', '') == top]
    kpb = {}
except FileNotFoundError:
    logging.warning("Файл data.txt не найден в директории %s, вопросы не загружены", DATA_DIR)
except Exception as e:
    logging.error(f"Ошибка загрузки data.txt: {e}")
    logging.error(traceback.format_exc())

for top in topics:
    for k in kapibara.get(top, []):
        if not isinstance(k, dict) or 'options' not in k:
            continue
        long = [x for x in k.get('options', []) if isinstance(x, str) and len(x) > 45]
        if len(long) > 0:
            k['long'] = "\n\n" + "\n".join(k.get('options', []))
            k['options'] = ["A.", "B.", "C.", "D."]

# Меню второго уровня: topic -> subtopics -> вопросы
# subtopics_by_topic[topic] = список уникальных подтем (строка)
# questions_by_topic_subtopic[(topic, subtopic)] = список индексов j в kapibara[topic]
subtopics_by_topic = {}
questions_by_topic_subtopic = {}
for top in topics:
    subs = set()
    for item in kapibara.get(top, []):
        if not isinstance(item, dict):
            continue
        sub = (item.get("subtopic") or "").strip() or "general"
        subs.add(sub)
    subtopics_by_topic[top] = sorted(subs)
    for j, item in enumerate(kapibara.get(top, [])):
        if not isinstance(item, dict):
            continue
        sub = (item.get("subtopic") or "").strip() or "general"
        key = (top, sub)
        if key not in questions_by_topic_subtopic:
            questions_by_topic_subtopic[key] = []
        questions_by_topic_subtopic[key].append(j)

# Экзамен 25 января: собираем все задачи с type == "jan" (по полю n, отсортированные)
EXAM_25JAN_ID = "exam_25jan"
exam_questions = []  # список кортежей (n, topic, j)
EXAM_TOTAL_DIFFICULTY = 0

for top in topics:
    for j, item in enumerate(kapibara.get(top, [])):
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "").strip().lower() == "jan":
            n_val = item.get("n") or 0
            exam_questions.append((n_val, top, j))

# Сортируем по n и считаем суммарную сложность экзамена
exam_questions.sort(key=lambda x: x[0])
for _, t_top, t_j in exam_questions:
    q = kapibara[t_top][t_j]
    try:
        diff = int(q.get("difficulty") or 1)
    except (TypeError, ValueError):
        diff = 1
    if diff < 1:
        diff = 1
    EXAM_TOTAL_DIFFICULTY += diff

# Состояние экзамена по пользователям:
# user_id -> {"answered": set(), "correct_count": int, "correct_difficulty": int}
exam_state = {}

# Экзамен 21 декабря: задачи с type == "dec" (по полю n, отсортированные)
EXAM_21DEC_ID = "exam_21dec"
exam_questions_dec = []  # список кортежей (n, topic, j)
EXAM_DEC_TOTAL_DIFFICULTY = 0

for top in topics:
    for j, item in enumerate(kapibara.get(top, [])):
        if (item.get("type") or "").strip().lower() == "dec":
            n_val = item.get("n") or 0
            exam_questions_dec.append((n_val, top, j))

exam_questions_dec.sort(key=lambda x: x[0])
for _, t_top, t_j in exam_questions_dec:
    q = kapibara[t_top][t_j]
    try:
        diff = int(q.get("difficulty") or 1)
    except (TypeError, ValueError):
        diff = 1
    if diff < 1:
        diff = 1
    EXAM_DEC_TOTAL_DIFFICULTY += diff

exam_state_dec = {}

# Экзамен 15 марта: задачи с type == "mar" (по полю n, отсортированные)
EXAM_MAR15_ID = "exam_mar15"
exam_questions_mar = []  # список кортежей (n, topic, j)
EXAM_MAR_TOTAL_DIFFICULTY = 0

for top in topics:
    for j, item in enumerate(kapibara.get(top, [])):
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "").strip().lower() == "mar":
            n_val = item.get("n") or 0
            exam_questions_mar.append((n_val, top, j))

exam_questions_mar.sort(key=lambda x: x[0])
for _, t_top, t_j in exam_questions_mar:
    arr = kapibara.get(t_top, [])
    if t_j < len(arr) and isinstance(arr[t_j], dict):
        q = arr[t_j]
        try:
            diff = int(q.get("difficulty") or 1)
        except (TypeError, ValueError):
            diff = 1
        if diff < 1:
            diff = 1
        EXAM_MAR_TOTAL_DIFFICULTY += diff

exam_state_mar = {}

# Общая конфигурация экзаменов jan / dec / mar (своя статистика у каждого)
def _exam_config():
    return {
        "jan": {
            "id": EXAM_25JAN_ID,
            "questions": exam_questions,
            "total_difficulty": EXAM_TOTAL_DIFFICULTY,
            "state": exam_state,
            "title_ru": "25 января",
            "title_en": "Jan 25",
            "short_ru": "Экзамен 25 янв",
            "short_en": "Exam Jan 25",
            "header_ru": "Экзамен 25 января",
            "header_en": "January 25 exam",
        },
        "dec": {
            "id": EXAM_21DEC_ID,
            "questions": exam_questions_dec,
            "total_difficulty": EXAM_DEC_TOTAL_DIFFICULTY,
            "state": exam_state_dec,
            "title_ru": "21 декабря",
            "title_en": "Dec 21",
            "short_ru": "Экзамен 21 дек",
            "short_en": "Exam Dec 21",
            "header_ru": "Экзамен 21 декабря",
            "header_en": "December 21 exam",
        },
        "mar": {
            "id": EXAM_MAR15_ID,
            "questions": exam_questions_mar,
            "total_difficulty": EXAM_MAR_TOTAL_DIFFICULTY,
            "state": exam_state_mar,
            "title_ru": "15 марта",
            "title_en": "Mar 15",
            "short_ru": "Экзамен 15 марта",
            "short_en": "Exam Mar 15",
            "header_ru": "Экзамен 15 марта",
            "header_en": "March 15 exam",
        },
    }

EXAM_CONFIG = _exam_config()

# Mock Exam: для каждой задачи type=jan подбираем случайную задачу с той же подтемой и сложностью
# Предпочитаем задачи не type=jan и не type=dec
EXAM_MOCK_ID = "exam_mock"
mock_questions = []  # список (topic, j) той же длины что exam_questions
MOCK_TOTAL_DIFFICULTY = 0

# Пулы по (subtopic, difficulty): preferred — без jan/dec, fallback — все
_preferred_pool = {}  # (subtopic, difficulty) -> [(topic, j), ...]
_fallback_pool = {}   # (subtopic, difficulty) -> [(topic, j), ...]
for top in topics:
    for j, item in enumerate(kapibara.get(top, [])):
        sub = (item.get("subtopic") or "").strip() or "general"
        try:
            diff = int(item.get("difficulty") or 1)
        except (TypeError, ValueError):
            diff = 1
        if diff < 1:
            diff = 1
        t = (item.get("type") or "").strip().lower()
        key = (sub, diff)
        if key not in _fallback_pool:
            _fallback_pool[key] = []
        _fallback_pool[key].append((top, j))
        if t not in ("jan", "dec"):
            if key not in _preferred_pool:
                _preferred_pool[key] = []
            _preferred_pool[key].append((top, j))

_used_for_mock = set()  # (topic, j) — каждая задача в mock_questions только один раз
for _, t_top, t_j in exam_questions:
    q = kapibara[t_top][t_j]
    sub = (q.get("subtopic") or "").strip() or "general"
    try:
        diff = int(q.get("difficulty") or 1)
    except (TypeError, ValueError):
        diff = 1
    if diff < 1:
        diff = 1
    key = (sub, diff)
    pool = _preferred_pool.get(key) or _fallback_pool.get(key)
    if pool:
        available = [p for p in pool if p not in _used_for_mock]
        if available:
            chosen = random.choice(available)
            _used_for_mock.add(chosen)
            mock_questions.append(chosen)
        else:
            mock_questions.append((t_top, t_j))
    else:
        mock_questions.append((t_top, t_j))
    c_top, c_j = mock_questions[-1]
    try:
        d = int(kapibara[c_top][c_j].get("difficulty") or 1)
    except (TypeError, ValueError):
        d = 1
    if d < 1:
        d = 1
    MOCK_TOTAL_DIFFICULTY += d

exam_state_mock = {}

# Связи приглашений: пригласивший -> множество приглашённых
invite_relations = {}  # type: dict[int, set[int]]

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
j=0

# Соединение с БД (будет инициализировано при старте)
db_conn = None

def log(usr, lg = []) :
   dt = datetime.datetime.now()
   id = usr.id
   username = usr.username
   # Признак источника: сейчас различаем stepik / не stepik
   try:
        if str(id) in stepik : 
           src = "stepik" 
        else  :
            if str(id) in cscagroup:
                 src = "cscagroup"
            else :
                 src = "" 
   except Exception:
       src = ""
   l = [dt, id, username, src] + lg
   # Сохраняем в файл для обратной совместимости
   res_file = os.path.join(DATA_DIR, "res.txt")
   try:
       with open(res_file, "a", encoding='utf-8') as f:
           f.write("\t".join([str(x) for x in l])+"\n")
   except:
       pass  # Если файл недоступен, продолжаем работу


def _make_invite_code(username: str, user_id: int) -> str:
   """Строит инвайт-код так же, как в makeinvite()."""
   uname = str(username or "")
   return uname[:3] + str(user_id)[:3]


async def _build_invite_relations_from_db():
   """Строит карту приглашений invite_relations на основе таблицы users."""
   global invite_relations
   invite_relations = {}
   if not db_conn:
       return
   try:
       users = await db.get_all_users(db_conn)
       # id -> (username, source, invited_by)
       users_map = {uid: (uname, source, invited_by) for uid, uname, source, invited_by in users}
       # код -> inviter_id
       code_to_inviter = {}
       for uid, (uname, source, invited_by) in users_map.items():
           code = _make_invite_code(uname, uid)
           code_to_inviter[code] = uid
       # разбираем приглашённых
       for invited_id, (inv_uname, source, invited_by) in users_map.items():
           if not invited_by:
               continue
           raw = str(invited_by).strip()
           if not raw.lower().startswith("invite"):
               continue
           code = raw[6:]  # после 'invite'
           inviter_id = code_to_inviter.get(code)
           if not inviter_id or inviter_id == invited_id:
               continue
           invite_relations.setdefault(inviter_id, set()).add(invited_id)
   except Exception as e:
       logging.error(f"Ошибка построения invite_relations: {e}")


def _register_invite_for_new_user(user_id: int, username, start_text):
   """Обновляет invite_relations при приходе нового пользователя по invite-ссылке."""
   global invite_relations
   if not start_text:
       return
   raw = start_text.strip()
   if not raw.lower().startswith("invite"):
       return
   code = raw[6:]
   try:
       # Кандидаты — известные пригласившие
       for inviter_id in invite_relations.keys():
           inviter_code = _make_invite_code(None, inviter_id)
           if inviter_code == code and inviter_id != user_id:
               invite_relations.setdefault(inviter_id, set()).add(user_id)
               return
   except Exception as e:
       logging.error(f"Ошибка регистрации инвайта для нового пользователя {user_id}: {e}")
def makeinvite(usr) : 
       id = usr.id
       username = str(usr.username)
       inv = username[:3]+str(id)[:3]
       lang = usr.language_code 
       link = "https://t.me/csca_mathbot?start=invite"+inv
       if lang == "ru" :
                message = "CSCA math bot бесплатный (пока). Чтобы продолжать пользоваться им неограниченно, отправьте ссылку-приглашения друзьям или опубликуйте ее в любом CSCA чате"
                message = message+"\n Персональная ссылка-приглашение "+link
                
       else :    
                message = "CSCA math bot is free. To continue using it without limits, send an invitation link to friends or post it in any CSCA chat."
                message = message+"\n Your personal invitation link "+link
                
       return message

async def start_kb(user_id: int = None) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру главного меню.
    Если передан user_id, добавляет кнопку "Продолжить" для тем с незавершённым прогрессом.
    """
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопку "Продолжить", если есть прогресс
    if user_id and db_conn:
        try:
            progress_list = await db.get_progress(db_conn, user_id)
            for prog in progress_list:
                topic = prog['topic']
                last_idx = prog['last_question_index']
                # Проверяем, что есть ещё вопросы
                if topic in kapibara and last_idx < len(kapibara[topic]):
                    builder.add(
                        InlineKeyboardButton(
                            text=f"▶️ Продолжить {topic[0].upper()+topic[1:]} ({last_idx}/{len(kapibara[topic])})",
                            callback_data=f'next_{topic}_{last_idx}'
                        )
                    )
        except Exception as e:
            logging.error(f"Ошибка получения прогресса для start_kb: {e}")
            pass  # Если ошибка при получении прогресса, просто показываем обычное меню
    
    # Добавляем все темы (меню первого уровня -> по нажатию покажем подтемы)
    for idx, top in enumerate(topics):
        builder.add(
            InlineKeyboardButton(
                text=top[0].upper() + top[1:],
                callback_data=f't_{idx}'
            )
        )
    # В конце списка тем добавляем пункты для экзаменов
    if exam_questions:
        builder.add(
            InlineKeyboardButton(
                text="📝 Экзамен 25 янв / Exam Jan 25",
                callback_data="exam_start_jan",
            )
        )
    if exam_questions_dec:
        builder.add(
            InlineKeyboardButton(
                text="📝 Экзамен 21 дек / Exam Dec 21",
                callback_data="exam_start_dec",
            )
        )
    if exam_questions_mar:
        builder.add(
            InlineKeyboardButton(
                text="📝 Экзамен 15 марта / Exam Mar 15",
                callback_data="exam_start_mar",
            )
        )
    if mock_questions:
        builder.add(
            InlineKeyboardButton(
                text="📝 Mock Exam / Пробный экзамен",
                callback_data="exam_mock_start",
            )
        )
    builder.adjust(1)
    return builder.as_markup()



def subtopic_kb(topic_idx: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора подтемы для темы topic_idx."""
    builder = InlineKeyboardBuilder()
    if topic_idx < 0 or topic_idx >= len(topics):
        builder.add(InlineKeyboardButton(text="◀️ Назад", callback_data="back_start"))
        builder.adjust(1)
        return builder.as_markup()
    topic = topics[topic_idx]
    subs = subtopics_by_topic.get(topic, [])
    builder = InlineKeyboardBuilder()
    # Кнопка «Все задачи по теме»
    builder.add(
        InlineKeyboardButton(
            text="📋 Все задачи / All questions",
            callback_data=f'next_{topic}_0'
        )
    )
    # Список подтем показываем только если хотя бы в одной подтеме >= 3 задач
    sub_counts = [len(questions_by_topic_subtopic.get((topic, sub_name), [])) for sub_name in subs]
    show_subtopics = sub_counts and max(sub_counts) >= 3
    if show_subtopics:
        for sub_idx, sub_name in enumerate(subs):
            count = sub_counts[sub_idx]
            label = sub_name[0].upper() + sub_name[1:] if sub_name else "General"
            builder.add(
                InlineKeyboardButton(
                    text=f"{label} ({count})",
                    callback_data=f's_{topic_idx}_{sub_idx}'
                )
            )
    builder.add(
        InlineKeyboardButton(
            text="◀️ Назад / Back",
            callback_data="back_start"
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def inline_kb(top, j : int, showvideo = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    k = kapibara[top][j]["options"]
    # Добавляем кнопки вопросов
    correct_h={0:'A', 1:'B' , 2:'C' , 3: 'D', 4:'E'}
    for i in range(len(k)):
        builder.add(
            InlineKeyboardButton(
                text=k[i].replace('. ','.     '),
                callback_data=f'qst_{top}_{j}_{i}'
            )
        )
  #  builder.row(
  #      InlineKeyboardButton(
  #          text='Получить подсказку',
  #          callback_data='explain'
  #      )
  #  )
    # Настраиваем размер клавиатуры
    builder.adjust(1)
    return builder.as_markup()

def inline_kb_next(top,j) :
   builder = InlineKeyboardBuilder()
   k=j+1
   builder.row(        
        InlineKeyboardButton(
            text='Следующий вопрос / Next task',           
            callback_data=f'next_{top}_{k}'
        )
    )
   builder.adjust(1)
   return builder.as_markup()


async def _get_exam_state(user_id: int):
   """Возвращает или инициализирует состояние экзамена 25 января для пользователя.
   При первом обращении загружает данные из БД."""
   state = exam_state.get(user_id)
   if state is None:
       # Инициализируем пустое состояние
       state = {
           "answered": set(),
           "correct_count": 0,
           "correct_difficulty": 0,
       }
       # Загружаем из БД, если есть соединение
       if db_conn:
           try:
               exam_answers = await db.get_exam_answers(db_conn, user_id, EXAM_25JAN_ID)
               answered_set = set()
               correct_count = 0
               correct_difficulty = 0
               for idx, answer_data in exam_answers.items():
                   answered_set.add(idx)
                   if answer_data["correct"]:
                       correct_count += 1
                       # Находим задачу и её сложность
                       if 0 <= idx < len(exam_questions):
                           _, top, j = exam_questions[idx]
                           q = kapibara[top][j]
                           try:
                               diff = int(q.get("difficulty") or 1)
                           except (TypeError, ValueError):
                               diff = 1
                           if diff < 1:
                               diff = 1
                           correct_difficulty += diff
               state["answered"] = answered_set
               state["correct_count"] = correct_count
               state["correct_difficulty"] = correct_difficulty
           except Exception as e:
               logging.error(f"Ошибка загрузки состояния экзамена из БД: {e}")
       exam_state[user_id] = state
   return state


def _find_next_exam_index(state):
   """Ищет индекс следующего экзаменационного вопроса или None, если все решены."""
   answered = state.get("answered", set())
   for idx in range(len(exam_questions)):
       if idx not in answered:
           return idx
   return None


def inline_kb_exam(idx: int) -> InlineKeyboardMarkup:
   """Клавиатура вариантов ответа для режима экзамена 25 января (exam_type=jan)."""
   return inline_kb_exam_by_type(idx, "jan")


async def _send_exam_question(call: CallbackQuery, user_id: int, idx: int):
   """Отправляет пользователю вопрос экзамена 25 января с номером idx."""
   if idx < 0 or idx >= len(exam_questions):
       kb = await start_kb(user_id)
       await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
       return
   _, top, j = exam_questions[idx]
   q = kapibara[top][j]
   if q.get("img"):
       photo_path = os.path.join(DATA_DIR, "images", q["img"])
       await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
   total = len(exam_questions)
   header = (
       f"Экзамен 25 января — вопрос {idx + 1} из {total}\n"
       f"January 25 exam — question {idx + 1} of {total}\n\n"
   )
   difficulty = q.get("difficulty")
   if isinstance(difficulty, int) and 1 <= difficulty <= 5:
       stars = "★" * difficulty + "☆" * (5 - difficulty)
       diff_line = f"Сложность / Difficulty: {stars}\n"
   else:
       diff_line = ""
   question_text = header + diff_line + q["english"] + "\n" + q.get("chinese", "") + q.get("long", "")
   reply = inline_kb_exam(idx)
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(question_text, reply_markup=reply)


async def _exam_wrong_by_topic(user_id: int, exam_type: str) -> dict:
   """
   Собирает по ответам в БД: тема -> число задач с ошибкой.
   exam_type:
     - 'jan' — экзамен 25 января
     - 'dec' — экзамен 21 декабря
   """
   wrong_by_topic: dict[str, int] = {}
   if not db_conn:
       return wrong_by_topic
   try:
       if exam_type == "jan":
           exam_id = EXAM_25JAN_ID
           questions = exam_questions
       elif exam_type == "dec":
           exam_id = EXAM_21DEC_ID
           questions = exam_questions_dec
       elif exam_type == "mar":
           exam_id = EXAM_MAR15_ID
           questions = exam_questions_mar
       else:
           return wrong_by_topic
       if not questions:
           return wrong_by_topic
       exam_answers = await db.get_exam_answers(db_conn, user_id, exam_id)
   except Exception as e:
       logging.error(f"Ошибка загрузки ответов экзамена ({exam_type}) для статистики ошибок: {e}")
       return wrong_by_topic
   for idx, answer_data in exam_answers.items():
       if answer_data.get("correct"):
           continue
       if idx < 0 or idx >= len(questions):
           continue
       _, top, _j = questions[idx]
       wrong_by_topic[top] = wrong_by_topic.get(top, 0) + 1
   return wrong_by_topic


# --- Общие хелперы для экзаменов jan / dec / mar ---
def _exam_cfg(exam_type: str):
    return EXAM_CONFIG.get(exam_type)


async def _get_exam_state_by_type(user_id: int, exam_type: str):
    """Состояние экзамена по типу (jan/dec/mar). Загружает из БД при первом обращении."""
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return {"answered": set(), "correct_count": 0, "correct_difficulty": 0}
    state = cfg["state"].get(user_id)
    if state is None:
        state = {"answered": set(), "correct_count": 0, "correct_difficulty": 0}
        if db_conn:
            try:
                exam_answers = await db.get_exam_answers(db_conn, user_id, cfg["id"])
                questions = cfg["questions"]
                for idx, answer_data in exam_answers.items():
                    state["answered"].add(idx)
                    if answer_data.get("correct") and 0 <= idx < len(questions):
                        state["correct_count"] += 1
                        _, top, j = questions[idx]
                        arr = kapibara.get(top, [])
                        if j < len(arr) and isinstance(arr[j], dict):
                            q = arr[j]
                            try:
                                diff = int(q.get("difficulty") or 1)
                            except (TypeError, ValueError):
                                diff = 1
                            if diff < 1:
                                diff = 1
                            state["correct_difficulty"] += diff
            except Exception as e:
                logging.error(f"Ошибка загрузки состояния экзамена ({exam_type}) из БД: {e}")
        cfg["state"][user_id] = state
    return state


def _find_next_exam_index_by_type(state, exam_type: str):
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return None
    questions = cfg["questions"]
    answered = state.get("answered", set())
    for idx in range(len(questions)):
        if idx not in answered:
            return idx
    return None


def inline_kb_exam_by_type(idx: int, exam_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    cfg = _exam_cfg(exam_type)
    if not cfg or idx < 0 or idx >= len(cfg["questions"]):
        builder.adjust(1)
        return builder.as_markup()
    _, top, j = cfg["questions"][idx]
    arr = kapibara.get(top, [])
    if j >= len(arr) or not isinstance(arr[j], dict):
        builder.adjust(1)
        return builder.as_markup()
    q = arr[j]
    for i, opt in enumerate(q.get("options", [])):
        builder.add(
            InlineKeyboardButton(
                text=opt.replace('. ', '.     '),
                callback_data=f'exam_q_{exam_type}_{idx}_{i}',
            )
        )
    builder.adjust(1)
    return builder.as_markup()


async def _send_exam_question_by_type(call: CallbackQuery, user_id: int, idx: int, exam_type: str):
    cfg = _exam_cfg(exam_type)
    if not cfg:
        kb = await start_kb(user_id)
        await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
        return
    questions = cfg["questions"]
    if idx < 0 or idx >= len(questions):
        kb = await start_kb(user_id)
        await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
        return
    _, top, j = questions[idx]
    arr = kapibara.get(top, [])
    if j >= len(arr) or not isinstance(arr[j], dict):
        kb = await start_kb(user_id)
        await call.message.answer("Вопрос не найден.", reply_markup=kb)
        return
    q = arr[j]
    if q.get("img"):
        photo_path = os.path.join(DATA_DIR, "images", q["img"])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
    total = len(questions)
    h_ru, h_en = cfg["header_ru"], cfg["header_en"]
    header = f"{h_ru} — вопрос {idx + 1} из {total}\n{h_en} — question {idx + 1} of {total}\n\n"
    difficulty = q.get("difficulty")
    if isinstance(difficulty, int) and 1 <= difficulty <= 5:
        diff_line = f"Сложность / Difficulty: {'★' * difficulty}{'☆' * (5 - difficulty)}\n"
    else:
        diff_line = ""
    question_text = header + diff_line + q.get("english", "") + "\n" + q.get("chinese", "") + q.get("long", "")
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(question_text, reply_markup=inline_kb_exam_by_type(idx, exam_type))


async def _send_exam_summary_by_type(call: CallbackQuery, user_id: int, exam_type: str):
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return
    state = await _get_exam_state_by_type(user_id, exam_type)
    questions = cfg["questions"]
    total_q = len(questions)
    total_d = cfg["total_difficulty"]
    k = (state.get("correct_difficulty", 0) / total_d * 100) if total_d else 0.0
    kb = _inline_kb_exam_finished_by_type(exam_type)
    wrong_by_topic = await _exam_wrong_by_topic(user_id, exam_type)
    lang = (call.from_user.language_code or "").lower()
    title_ru, title_en = cfg["title_ru"], cfg["title_en"]
    if lang.startswith("ru"):
        if wrong_by_topic:
            lines = ["\nОшибки по темам (неверный ответ):"]
            for topic_name in sorted(wrong_by_topic.keys()):
                n = wrong_by_topic[topic_name]
                lines.append(f"• {topic_name} — {n} задач(и)")
            stats = "\n".join(lines)
        else:
            stats = "\n\nНеверных ответов не было."
        msg = (
            f"Ваш результат экзамена {title_ru}:\n\n"
            f"Вы решили правильно {state.get('correct_count', 0)} из {total_q} задач и набрали {k:.2f} баллов."
            f"{stats}\n\n"
            "Спасибо! Вы можете продолжить тренироваться по обычным темам."
        )
    else:
        if wrong_by_topic:
            lines = ["\nMistakes by topic (wrong answer):"]
            for topic_name in sorted(wrong_by_topic.keys()):
                n = wrong_by_topic[topic_name]
                lines.append(f"• {topic_name} — {n} task(s)")
            stats = "\n".join(lines)
        else:
            stats = "\n\nNo incorrect answers."
        msg = (
            f"Your {title_en} exam result:\n\n"
            f"You solved {state.get('correct_count', 0)} out of {total_q} tasks correctly and scored {k:.2f} points."
            f"{stats}\n\n"
            "Thank you! You can continue training on regular topics."
        )
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(msg, reply_markup=kb)


def _format_exam_stats_line_by_type(state, exam_type: str) -> str:
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return ""
    total_q = len(cfg["questions"])
    total_d = cfg["total_difficulty"]
    k = (state.get("correct_difficulty", 0) / total_d * 100) if total_d else 0.0
    correct = state.get("correct_count", 0)
    return (
        f"Правильно {correct} из {total_q}, балл {k:.2f}.\n"
        f"Correct {correct} of {total_q}, score {k:.2f}."
    )


def _inline_kb_exam_entry_choice_by_type(exam_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Очистить статистику / Clear stats", callback_data=f"exam_clear_{exam_type}"),
        InlineKeyboardButton(text="Продолжить / Continue", callback_data=f"exam_continue_{exam_type}"),
    )
    return builder.as_markup()


def _inline_kb_exam_finished_by_type(exam_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Очистить статистику / Clear stats", callback_data=f"exam_clear_{exam_type}"),
        InlineKeyboardButton(text="Список тем / Topic list", callback_data="back_start"),
    )
    return builder.as_markup()


async def _send_exam_summary(call: CallbackQuery, user_id: int):
   """Показывает пользователю итоговую статистику экзамена 25 января и кнопки Очистить / Список тем."""
   state = await _get_exam_state(user_id)
   correct = state.get("correct_count", 0)
   total_q = len(exam_questions)
   if EXAM_TOTAL_DIFFICULTY:
       k = state.get("correct_difficulty", 0) / EXAM_TOTAL_DIFFICULTY * 100
   else:
       k = 0.0
   kb = _inline_kb_exam_finished()
   wrong_by_topic = await _exam_wrong_by_topic(user_id, "jan")
   lang = (call.from_user.language_code or "").lower()
   if lang.startswith("ru"):
       if wrong_by_topic:
           lines = ["\nОшибки по темам (неверный ответ):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {topic_name} — {n} задач(и)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nНеверных ответов не было."
       msg = (
           f"Ваш результат экзамена 25 января:\n\n"
           f"Вы решили правильно {correct} из {total_q} задач и набрали {k:.2f} баллов."
           f"{stats}\n\n"
           "Спасибо! Вы можете продолжить тренироваться по обычным темам."
       )
   else:
       if wrong_by_topic:
           lines = ["\nMistakes by topic (wrong answer):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {topic_name} — {n} task(s)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nNo incorrect answers."
       msg = (
           f"Your January 25 exam result:\n\n"
           f"You solved {correct} out of {total_q} tasks correctly and scored {k:.2f} points."
           f"{stats}\n\n"
           "Thank you! You can continue training on regular topics."
       )
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(msg, reply_markup=kb)


# --- Экзамен 21 декабря (type=dec) ---
async def _get_exam_dec_state(user_id: int):
   """Состояние экзамена 21 декабря для пользователя. Загружает из БД при первом обращении."""
   state = exam_state_dec.get(user_id)
   if state is None:
       state = {"answered": set(), "correct_count": 0, "correct_difficulty": 0}
       if db_conn:
           try:
               exam_answers = await db.get_exam_answers(db_conn, user_id, EXAM_21DEC_ID)
               answered_set = set()
               correct_count = 0
               correct_difficulty = 0
               for idx, answer_data in exam_answers.items():
                   answered_set.add(idx)
                   if answer_data["correct"] and 0 <= idx < len(exam_questions_dec):
                       correct_count += 1
                       _, top, j = exam_questions_dec[idx]
                       q = kapibara[top][j]
                       try:
                           diff = int(q.get("difficulty") or 1)
                       except (TypeError, ValueError):
                           diff = 1
                       if diff < 1:
                           diff = 1
                       correct_difficulty += diff
               state["answered"] = answered_set
               state["correct_count"] = correct_count
               state["correct_difficulty"] = correct_difficulty
           except Exception as e:
               logging.error(f"Ошибка загрузки состояния экзамена 21 дек из БД: {e}")
       exam_state_dec[user_id] = state
   return state


def _find_next_exam_dec_index(state):
   answered = state.get("answered", set())
   for idx in range(len(exam_questions_dec)):
       if idx not in answered:
           return idx
   return None


def inline_kb_exam_dec(idx: int) -> InlineKeyboardMarkup:
   """Клавиатура вариантов для экзамена 21 декабря (exam_type=dec)."""
   return inline_kb_exam_by_type(idx, "dec")


async def _send_exam_dec_question(call: CallbackQuery, user_id: int, idx: int):
   if idx < 0 or idx >= len(exam_questions_dec):
       kb = await start_kb(user_id)
       await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
       return
   _, top, j = exam_questions_dec[idx]
   q = kapibara[top][j]
   if q.get("img"):
       photo_path = os.path.join(DATA_DIR, "images", q["img"])
       await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
   total = len(exam_questions_dec)
   header = (
       f"Экзамен 21 декабря — вопрос {idx + 1} из {total}\n"
       f"December 21 exam — question {idx + 1} of {total}\n\n"
   )
   difficulty = q.get("difficulty")
   if isinstance(difficulty, int) and 1 <= difficulty <= 5:
       diff_line = f"Сложность / Difficulty: {'★' * difficulty}{'☆' * (5 - difficulty)}\n"
   else:
       diff_line = ""
   question_text = header + diff_line + q["english"] + "\n" + q.get("chinese", "") + q.get("long", "")
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(question_text, reply_markup=inline_kb_exam_dec(idx))


async def _send_exam_dec_summary(call: CallbackQuery, user_id: int):
   state = await _get_exam_dec_state(user_id)
   correct = state.get("correct_count", 0)
   total_q = len(exam_questions_dec)
   k = (state.get("correct_difficulty", 0) / EXAM_DEC_TOTAL_DIFFICULTY * 100) if EXAM_DEC_TOTAL_DIFFICULTY else 0.0
   kb = _inline_kb_exam_dec_finished()
   wrong_by_topic = await _exam_wrong_by_topic(user_id, "dec")
   lang = (call.from_user.language_code or "").lower()
   if lang.startswith("ru"):
       if wrong_by_topic:
           lines = ["\nОшибки по темам (неверный ответ):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {topic_name} — {n} задач(и)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nНеверных ответов не было."
       msg = (
           f"Ваш результат экзамена 21 декабря:\n\n"
           f"Вы решили правильно {correct} из {total_q} задач и набрали {k:.2f} баллов."
           f"{stats}\n\n"
           "Спасибо! Вы можете продолжить тренироваться по обычным темам."
       )
   else:
       if wrong_by_topic:
           lines = ["\nMistakes by topic (wrong answer):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {topic_name} — {n} task(s)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nNo incorrect answers."
       msg = (
           f"Your December 21 exam result:\n\n"
           f"You solved {correct} out of {total_q} tasks correctly and scored {k:.2f} points."
           f"{stats}\n\n"
           "Thank you! You can continue training on regular topics."
       )
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(msg, reply_markup=kb)


def _format_exam_dec_stats_line(state) -> str:
   correct = state.get("correct_count", 0)
   total_q = len(exam_questions_dec)
   k = (state.get("correct_difficulty", 0) / EXAM_DEC_TOTAL_DIFFICULTY * 100) if EXAM_DEC_TOTAL_DIFFICULTY else 0.0
   return (
       f"Правильно {correct} из {total_q}, балл {k:.2f}.\n"
       f"Correct {correct} of {total_q}, score {k:.2f}."
   )


def _inline_kb_exam_dec_entry_choice() -> InlineKeyboardMarkup:
   return _inline_kb_exam_entry_choice_by_type("dec")


def _inline_kb_exam_dec_finished() -> InlineKeyboardMarkup:
   return _inline_kb_exam_finished_by_type("dec")


# --- Mock Exam (задачи по подтеме/сложности как у jan, без jan/dec по возможности) ---
async def _get_exam_mock_state(user_id: int):
   state = exam_state_mock.get(user_id)
   if state is None:
       state = {"answered": set(), "correct_count": 0, "correct_difficulty": 0}
       if db_conn:
           try:
               exam_answers = await db.get_exam_answers(db_conn, user_id, EXAM_MOCK_ID)
               answered_set = set()
               correct_count = 0
               correct_difficulty = 0
               for idx, answer_data in exam_answers.items():
                   answered_set.add(idx)
                   if answer_data["correct"] and 0 <= idx < len(mock_questions):
                       correct_count += 1
                       top, j = mock_questions[idx]
                       q = kapibara[top][j]
                       try:
                           diff = int(q.get("difficulty") or 1)
                       except (TypeError, ValueError):
                           diff = 1
                       if diff < 1:
                           diff = 1
                       correct_difficulty += diff
               state["answered"] = answered_set
               state["correct_count"] = correct_count
               state["correct_difficulty"] = correct_difficulty
           except Exception as e:
               logging.error(f"Ошибка загрузки состояния Mock Exam из БД: {e}")
       exam_state_mock[user_id] = state
   return state


def _find_next_exam_mock_index(state):
   answered = state.get("answered", set())
   for idx in range(len(mock_questions)):
       if idx not in answered:
           return idx
   return None


def inline_kb_exam_mock(idx: int) -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   if idx < 0 or idx >= len(mock_questions):
       builder.adjust(1)
       return builder.as_markup()
   top, j = mock_questions[idx]
   q = kapibara[top][j]
   for i, opt in enumerate(q["options"]):
       builder.add(
           InlineKeyboardButton(
               text=opt.replace('. ', '.     '),
               callback_data=f'exam_mock_q_{idx}_{i}',
           )
       )
   builder.adjust(1)
   return builder.as_markup()


async def _send_exam_mock_question(call: CallbackQuery, user_id: int, idx: int):
   if idx < 0 or idx >= len(mock_questions):
       kb = await start_kb(user_id)
       await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
       return
   top, j = mock_questions[idx]
   q = kapibara[top][j]
   if q.get("img"):
       photo_path = os.path.join(DATA_DIR, "images", q["img"])
       await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
   total = len(mock_questions)
   header = (
       f"Mock Exam — вопрос {idx + 1} из {total}\n"
       f"Пробный экзамен — question {idx + 1} of {total}\n\n"
   )
   difficulty = q.get("difficulty")
   if isinstance(difficulty, int) and 1 <= difficulty <= 5:
       diff_line = f"Сложность / Difficulty: {'★' * difficulty}{'☆' * (5 - difficulty)}\n"
   else:
       diff_line = ""
   question_text = header + diff_line + q["english"] + "\n" + q.get("chinese", "") + q.get("long", "")
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(question_text, reply_markup=inline_kb_exam_mock(idx))


async def _send_exam_mock_summary(call: CallbackQuery, user_id: int):
   state = await _get_exam_mock_state(user_id)
   correct = state.get("correct_count", 0)
   total_q = len(mock_questions)
   k = (state.get("correct_difficulty", 0) / MOCK_TOTAL_DIFFICULTY * 100) if MOCK_TOTAL_DIFFICULTY else 0.0
   kb = _inline_kb_exam_mock_finished()
   msg = (
       f"Ваш результат Mock Exam (пробный экзамен):\n\n"
       f"Вы решили правильно {correct} из {total_q} задач и набрали {k:.2f} баллов.\n"
       "Спасибо! Вы можете продолжить тренироваться по обычным темам.\n\n"
       "Your Mock Exam result:\n\n"
       f"You solved {correct} out of {total_q} tasks correctly and scored {k:.2f} points.\n"
       "Thank you! You can continue training on regular topics."
   )
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(msg, reply_markup=kb)


def _format_exam_mock_stats_line(state) -> str:
   correct = state.get("correct_count", 0)
   total_q = len(mock_questions)
   k = (state.get("correct_difficulty", 0) / MOCK_TOTAL_DIFFICULTY * 100) if MOCK_TOTAL_DIFFICULTY else 0.0
   return (
       f"Правильно {correct} из {total_q}, балл {k:.2f}.\n"
       f"Correct {correct} of {total_q}, score {k:.2f}."
   )


def _inline_kb_exam_mock_entry_choice() -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   builder.row(
       InlineKeyboardButton(text="Очистить статистику / Clear stats", callback_data="exam_mock_clear"),
       InlineKeyboardButton(text="Продолжить / Continue", callback_data="exam_mock_continue"),
   )
   return builder.as_markup()


def _inline_kb_exam_mock_finished() -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   builder.row(
       InlineKeyboardButton(text="Очистить статистику / Clear stats", callback_data="exam_mock_clear"),
       InlineKeyboardButton(text="Список тем / Topic list", callback_data="back_start"),
   )
   return builder.as_markup()


def _get_subtopic_j(topic_idx: int, sub_idx: int, k: int):
    """Возвращает (topic, j) для k-го вопроса в подтеме."""
    if topic_idx < 0 or topic_idx >= len(topics):
        return None, None
    topic = topics[topic_idx]
    subs = subtopics_by_topic.get(topic, [])
    if sub_idx < 0 or sub_idx >= len(subs):
        return None, None
    sub_name = subs[sub_idx]
    j_list = questions_by_topic_subtopic.get((topic, sub_name), [])
    if k < 0 or k >= len(j_list):
        return None, None
    return topic, j_list[k]


def inline_kb_sub(topic_idx: int, sub_idx: int, k: int, showvideo: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура вариантов ответа для вопроса в режиме подтемы (callback qst_sub_)."""
    topic, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if topic is None:
        return None
    builder = InlineKeyboardBuilder()
    opts = kapibara[topic][j]["options"]
    for i in range(len(opts)):
        builder.add(
            InlineKeyboardButton(
                text=opts[i].replace('. ', '.     '),
                callback_data=f'qst_sub_{topic_idx}_{sub_idx}_{k}_{i}'
            )
        )
    builder.adjust(1)
    return builder.as_markup()


def inline_kb_next_sub(topic_idx: int, sub_idx: int, k: int) -> InlineKeyboardMarkup:
    """Кнопка «Следующий вопрос» в режиме подтемы."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='Следующий вопрос / Next task',
            callback_data=f'next_sub_{topic_idx}_{sub_idx}_{k + 1}'
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def inline_kb_explain_sub(topic_idx: int, sub_idx: int, k: int, showvideo: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура после неверного ответа в режиме подтемы (повторить + обсудить + следующий)."""
    topic, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if topic is None:
        return None
    k_item = kapibara[topic][j]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='Повторить / One more time!',
            callback_data=f'next_sub_{topic_idx}_{sub_idx}_{k}'
        )
    )
    topiclink = topic_links.get(k_item.get('subtopic', ''), '') or topic_links.get(k_item.get('topic'), '')
    if topiclink:
        builder.row(
            InlineKeyboardButton(
                text='Обсудить задачу / Ask a question',
                url=topiclink
            )
        )
    builder.row(
        InlineKeyboardButton(
            text='Следующий вопрос / Next question',
            callback_data=f'next_sub_{topic_idx}_{sub_idx}_{k + 1}'
        )
    )
    builder.adjust(1)
    return builder.as_markup()

def inline_kb_explain(top,j,k) :
   builder = InlineKeyboardBuilder()
   builder.row(
        InlineKeyboardButton(
            text='Повторить / One more time!',
            callback_data='next_'+top+'_'+str(j)   #'explain'
        )
    )
   topiclink  = topic_links.get(k.get('subtopic',''),'') or topic_links.get(k.get('topic'),'')
   if topiclink :
      
      builder.row(
        InlineKeyboardButton(
            text='Обсудить задачу / Ask a question' ,
            url=topiclink
        )
      )
   builder.row(
        InlineKeyboardButton(
            text='Следующий вопрос / Next question',
            callback_data='next_'+top+'_'+str(j+1)
        )
    )
   builder.adjust(1)
   return builder.as_markup()
    

@router.callback_query(F.data == "back_start")
async def back_to_start(call: CallbackQuery):
    """Возврат в главное меню."""
    await call.answer()
    kb = await start_kb(call.from_user.id)
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer("Выберите тему / Choose topic:", reply_markup=kb)


def _format_exam_stats_line(state) -> str:
    """Форматирует одну строку статистики экзамена (верно из N, балл)."""
    correct = state.get("correct_count", 0)
    total_q = len(exam_questions)
    if EXAM_TOTAL_DIFFICULTY:
        k = state.get("correct_difficulty", 0) / EXAM_TOTAL_DIFFICULTY * 100
    else:
        k = 0.0
    return (
        f"Правильно {correct} из {total_q}, балл {k:.2f}.\n"
        f"Correct {correct} of {total_q}, score {k:.2f}."
    )


def _inline_kb_exam_entry_choice() -> InlineKeyboardMarkup:
    """Клавиатура при входе в экзамен, если есть статистика: Очистить / Продолжить."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Очистить статистику / Clear stats", callback_data="exam25_clear"),
        InlineKeyboardButton(text="Продолжить / Continue", callback_data="exam25_continue"),
    )
    return builder.as_markup()


def _inline_kb_exam_finished() -> InlineKeyboardMarkup:
    """Клавиатура когда все задачи экзамена решены: Очистить статистику / Список тем."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Очистить статистику / Clear stats", callback_data="exam25_clear"),
        InlineKeyboardButton(text="Список тем / Topic list", callback_data="back_start"),
    )
    return builder.as_markup()


# --- Общий обработчик экзаменов jan / dec / mar ---
@router.callback_query(F.data.in_(["exam_start_jan", "exam_start_dec", "exam_start_mar"]))
async def on_exam_start(call: CallbackQuery):
    """Старт экзамена по типу: jan, dec, mar."""
    await call.answer()
    exam_type = call.data.replace("exam_start_", "")
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return
    user_id = call.from_user.id
    questions = cfg["questions"]
    if not questions:
        kb = await start_kb(user_id)
        await call.message.answer(
            f"Пока нет задач для экзамена {cfg['title_ru']}.",
            reply_markup=kb,
        )
        return
    state = await _get_exam_state_by_type(user_id, exam_type)
    next_idx = _find_next_exam_index_by_type(state, exam_type)
    if next_idx is None:
        log(call.from_user, [cfg["id"], "start", "summary"])
        await _send_exam_summary_by_type(call, user_id, exam_type)
        return
    if state.get("answered"):
        log(call.from_user, [cfg["id"], "start", "entry"])
        msg = (
            f"Режим «{cfg['short_ru']}» / Mode \"{cfg['short_en']}\".\n\n"
            + _format_exam_stats_line_by_type(state, exam_type)
            + "\n\nВыберите действие / Choose action:"
        )
        async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
            await call.message.answer(msg, reply_markup=_inline_kb_exam_entry_choice_by_type(exam_type))
        return
    log(call.from_user, [cfg["id"], "start", "question"])
    total = len(questions)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(
            f"Режим «{cfg['short_ru']}» / Mode \"{cfg['short_en']}\".\n"
            f"Всего {total} задач. Второй раз решить одну и ту же задачу нельзя.\n\n"
            "Разборы всех задач в https://stepik.org/a/268161\n"
            "Используйте промокод CSCABOT для скидки.\n\n"
            f"There are {total} tasks. You cannot solve the same task twice."
        )
    await _send_exam_question_by_type(call, user_id, next_idx, exam_type)


@router.callback_query(F.data.in_(["exam_clear_jan", "exam_clear_dec", "exam_clear_mar"]))
async def on_exam_clear(call: CallbackQuery):
    """Очистка статистики экзамена по типу."""
    await call.answer()
    exam_type = call.data.replace("exam_clear_", "")
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return
    user_id = call.from_user.id
    log(call.from_user, [cfg["id"], "clear"])
    if db_conn:
        try:
            await db.clear_exam_answers(db_conn, user_id, cfg["id"])
        except Exception as e:
            logging.error(f"Ошибка очистки экзамена ({exam_type}): {e}")
    if user_id in cfg["state"]:
        del cfg["state"][user_id]
    state = await _get_exam_state_by_type(user_id, exam_type)
    next_idx = _find_next_exam_index_by_type(state, exam_type)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(
            "Статистика экзамена очищена. Можете начать заново.\n"
            "Exam stats cleared. You can start again."
        )
    if next_idx is not None:
        await _send_exam_question_by_type(call, user_id, next_idx, exam_type)
    else:
        await _send_exam_summary_by_type(call, user_id, exam_type)


@router.callback_query(F.data.in_(["exam_continue_jan", "exam_continue_dec", "exam_continue_mar"]))
async def on_exam_continue(call: CallbackQuery):
    """Продолжить экзамен по типу."""
    await call.answer()
    exam_type = call.data.replace("exam_continue_", "")
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return
    log(call.from_user, [cfg["id"], "continue"])
    user_id = call.from_user.id
    state = await _get_exam_state_by_type(user_id, exam_type)
    next_idx = _find_next_exam_index_by_type(state, exam_type)
    if next_idx is None:
        await _send_exam_summary_by_type(call, user_id, exam_type)
        return
    await _send_exam_question_by_type(call, user_id, next_idx, exam_type)


# --- Mock Exam: вход, очистка, продолжение ---
@router.callback_query(F.data == "exam_mock_start")
async def on_exam_mock_start(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    # Доступ к Mock Exam только для:
    # - пользователей из списка stepik
    # - пользователей из списка cscagroup
    # - пользователей, пригласивших не менее трёх новых пользователей
    # - пользователей, оплативших доступ в Telegram Stars
    invites_count = len(invite_relations.get(user_id, set()))
    paid_access = False
    user_created_at = None
    total_answered = 0
    if db_conn:
        try:
            paid_access = await db.user_has_exam_access_by_payment(db_conn, user_id)
            # дата регистрации
            user_created_at = await db.get_user_created_at(db_conn, user_id)
            # всего решённых задач
            stats = await db.get_user_stats(db_conn, user_id)
            total_answered = stats.get("total_answered", 0)
        except Exception as e:
            logging.error(f"Ошибка проверки доступа к Mock Exam для пользователя {user_id}: {e}")

    should_redirect_to_pay = False

    # Базовые условия отсутствия привилегий
    no_privileges = (
        str(user_id) not in stepik
        and str(user_id) not in cscagroup
        and invites_count < 3
        and not paid_access
    )

    # Дополнительные условия: зарегистрирован > 3 дней назад и решил > 10 задач
    meets_activity_limits = False
    if user_created_at:
        try:
            from datetime import datetime, timedelta
            created_dt = datetime.fromisoformat(user_created_at)
            if datetime.now() - created_dt > timedelta(days=3) and total_answered > 10:
                meets_activity_limits = True
        except Exception as e:
            logging.error(f"Ошибка разбора created_at для пользователя {user_id}: {e}")

    if (no_privileges and meets_activity_limits) or (str(user_id) == "780221999" and not paid_access):
        should_redirect_to_pay = True

    if should_redirect_to_pay:
        log(call.from_user, ["mockexamreject"])
        # Показываем то же сообщение об оплате/условиях доступа, что и в команде /pay
        await pay(call.from_user)
        return
    if not mock_questions:
        kb = await start_kb(user_id)
        await call.message.answer("Mock Exam пока не настроен.", reply_markup=kb)
        return
    state = await _get_exam_mock_state(user_id)
    next_idx = _find_next_exam_mock_index(state)
    if next_idx is None:
        log(call.from_user, [EXAM_MOCK_ID, "start", "summary"])
        await _send_exam_mock_summary(call, user_id)
        return
    if state.get("answered"):
        log(call.from_user, [EXAM_MOCK_ID, "start", "entry"])
        msg = (
            "Режим «Mock Exam» / Пробный экзамен.\n\n"
            + _format_exam_mock_stats_line(state)
            + "\n\nВыберите действие / Choose action:"
        )
        async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
            await call.message.answer(msg, reply_markup=_inline_kb_exam_mock_entry_choice())
        return
    log(call.from_user, [EXAM_MOCK_ID, "start", "question"])
    total_m = len(mock_questions)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(
            f"Mock Exam / Пробный экзамен.\n"
            f"Неограниченный доступ для студентов курса 'Подготовка к CSCA' https://stepik.org/a/268161  и  участников групп подготовки по метематике https://t.me/+c1ksuGkuO1BiNDk6 и физике https://t.me/+dUnPAdJO1w4zZWUy \n\n"
            f"Всего {total_m} задач. Второй раз решить одну и ту же задачу нельзя.\n\n"
            f"There are {total_m} tasks. You cannot solve the same task twice."
        )
    await _send_exam_mock_question(call, user_id, next_idx)


@router.callback_query(F.data == "exam_mock_clear")
async def on_exam_mock_clear(call: CallbackQuery):
    await call.answer()
    log(call.from_user, [EXAM_MOCK_ID, "clear"])
    user_id = call.from_user.id
    if db_conn:
        try:
            await db.clear_exam_answers(db_conn, user_id, EXAM_MOCK_ID)
        except Exception as e:
            logging.error(f"Ошибка очистки Mock Exam: {e}")
    if user_id in exam_state_mock:
        del exam_state_mock[user_id]
    state = await _get_exam_mock_state(user_id)
    next_idx = _find_next_exam_mock_index(state)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(
            "Статистика Mock Exam очищена. Можете начать заново.\n"
            "Mock exam stats cleared. You can start again."
        )
    if next_idx is not None:
        await _send_exam_mock_question(call, user_id, next_idx)
    else:
        await _send_exam_mock_summary(call, user_id)


@router.callback_query(F.data == "exam_mock_continue")
async def on_exam_mock_continue(call: CallbackQuery):
    await call.answer()
    log(call.from_user, [EXAM_MOCK_ID, "continue"])
    user_id = call.from_user.id
    state = await _get_exam_mock_state(user_id)
    next_idx = _find_next_exam_mock_index(state)
    if next_idx is None:
        await _send_exam_mock_summary(call, user_id)
        return
    await _send_exam_mock_question(call, user_id, next_idx)


@router.callback_query(F.data.startswith("exam_q_"))
async def on_exam_answer(call: CallbackQuery):
    """Общий обработчик ответа по экзаменам jan / dec / mar. Формат: exam_q_{type}_{idx}_{ans_id}."""
    correct_h = {'A': '0', 'B': '1', 'C': '2', 'D': '3', 'E': '4'}
    await call.answer()
    data = call.data.split("_")
    # exam_q_jan_0_1 -> ["exam", "q", "jan", "0", "1"]
    if len(data) < 5:
        await call.message.answer("Ошибка формата ответа экзамена.")
        return
    exam_type = data[2]
    try:
        idx = int(data[3])
        ans_id = data[4]
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата ответа экзамена.")
        return
    cfg = _exam_cfg(exam_type)
    if not cfg:
        await call.message.answer("Неизвестный тип экзамена.")
        return
    user_id = call.from_user.id
    questions = cfg["questions"]
    if idx < 0 or idx >= len(questions):
        await call.message.answer("Экзаменационный вопрос не найден.")
        return
    state = await _get_exam_state_by_type(user_id, exam_type)
    if idx in state["answered"]:
        await call.message.answer("Вы уже решили эту задачу.")
        return
    _, top, j = questions[idx]
    arr = kapibara.get(top, [])
    if j >= len(arr) or not isinstance(arr[j], dict):
        await call.message.answer("Вопрос не найден.")
        return
    q = arr[j]
    correct = correct_h.get(q.get("answer", ""), "")
    try:
        ans_id_int = int(ans_id)
    except ValueError:
        await call.message.answer("Ошибка формата ответа экзамена.")
        return
    ansok = 1 if correct == ans_id else 0

    state["answered"].add(idx)
    if ansok:
        state["correct_count"] = state.get("correct_count", 0) + 1
        try:
            diff = int(q.get("difficulty") or 1)
        except (TypeError, ValueError):
            diff = 1
        if diff < 1:
            diff = 1
        state["correct_difficulty"] = state.get("correct_difficulty", 0) + diff

    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                user_id,
                cfg["id"],
                idx,
                ans_id_int,
                ansok == 1,
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа экзамена ({exam_type}): {e}")

    log(call.from_user, [cfg["id"], idx, ans_id, ansok])

    result_msg = "Правильно! / Correct!" if ansok else "Неправильно. / Incorrect."
    correct_now = state.get("correct_count", 0)
    total_q = len(questions)
    total_d = cfg["total_difficulty"]
    k_now = (state.get("correct_difficulty", 0) / total_d * 100) if total_d else 0.0
    stats_ru = f"Сейчас по экзамену: {correct_now} из {total_q} верно, набранный балл {k_now:.2f}."
    stats_en = f"Current exam stats: {correct_now} out of {total_q} correct, score {k_now:.2f}."
    lang = (call.from_user.language_code or "").lower()
    stats_msg = stats_ru if lang.startswith("ru") else stats_en

    # Если ответ неправильный и есть подсказка — отправляем тремя сообщениями:
    # 1) Неправильно  2) hint по языку  3) статистика
    if not ansok and (q.get("hint_en") or q.get("hint_ru")):
        hint = ""
        if lang.startswith("ru"):
            hint = (q.get("hint_ru") or "").strip() or (q.get("hint_en") or "").strip()
        else:
            hint = (q.get("hint_en") or "").strip() or (q.get("hint_ru") or "").strip()

        await call.message.answer("Неправильно. / Incorrect.")
        if hint:
            await call.message.answer(hint)
        await call.message.answer(stats_msg)
    else:
        full_msg = result_msg + "\n" + stats_msg

    next_idx = _find_next_exam_index_by_type(state, exam_type)
    if next_idx is None:
        if ansok or not (q.get("hint_en") or q.get("hint_ru")):
            await call.message.answer(full_msg)
        await _send_exam_summary_by_type(call, user_id, exam_type)
    else:
        if ansok or not (q.get("hint_en") or q.get("hint_ru")):
            await call.message.answer(full_msg)
        await _send_exam_question_by_type(call, user_id, next_idx, exam_type)


@router.callback_query(F.data.startswith("exam_mock_q_"))
async def on_exam_mock_answer(call: CallbackQuery):
    """Обработка ответа в режиме Mock Exam. callback_data = exam_mock_q_{idx}_{i} (при split 5 частей)."""
    correct_h = {'A': '0', 'B': '1', 'C': '2', 'D': '3', 'E': '4'}
    await call.answer()
    data = call.data.split("_")
    # exam_mock_q_0_1 -> ["exam", "mock", "q", "0", "1"] -> idx=data[3], ans_id=data[4]
    if len(data) < 5:
        await call.message.answer("Ошибка формата ответа экзамена.")
        return
    try:
        idx = int(data[3])
        ans_id = data[4]
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата ответа экзамена.")
        return
    user_id = call.from_user.id
    if idx < 0 or idx >= len(mock_questions):
        await call.message.answer("Экзаменационный вопрос не найден.")
        return
    state = await _get_exam_mock_state(user_id)
    if idx in state["answered"]:
        await call.message.answer("Вы уже решили эту задачу.")
        return
    top, j = mock_questions[idx]
    q = kapibara[top][j]
    correct = correct_h.get(q["answer"], "")
    try:
        ans_id_int = int(ans_id)
    except ValueError:
        await call.message.answer("Ошибка формата ответа экзамена.")
        return
    ansok = 1 if correct == ans_id else 0

    state["answered"].add(idx)
    if ansok:
        state["correct_count"] = state.get("correct_count", 0) + 1
        try:
            diff = int(q.get("difficulty") or 1)
        except (TypeError, ValueError):
            diff = 1
        if diff < 1:
            diff = 1
        state["correct_difficulty"] = state.get("correct_difficulty", 0) + diff

    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                user_id,
                EXAM_MOCK_ID,
                idx,
                ans_id_int,
                ansok == 1,
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа Mock Exam: {e}")
    log(call.from_user, [EXAM_MOCK_ID, idx, ans_id, ansok])

    result_msg = "Правильно! / Correct!" if ansok else "Неправильно. / Incorrect."
    correct_now = state.get("correct_count", 0)
    total_q = len(mock_questions)
    k_now = (state.get("correct_difficulty", 0) / MOCK_TOTAL_DIFFICULTY * 100) if MOCK_TOTAL_DIFFICULTY else 0.0
    stats_ru = f"Сейчас по экзамену: {correct_now} из {total_q} верно, набранный балл {k_now:.2f}."
    stats_en = f"Current exam stats: {correct_now} out of {total_q} correct, score {k_now:.2f}."
    full_msg = result_msg + "\n" + stats_ru + "\n" + stats_en

    next_idx = _find_next_exam_mock_index(state)
    if next_idx is None:
        await call.message.answer(full_msg)
        await _send_exam_mock_summary(call, user_id)
    else:
        await call.message.answer(full_msg)
        await _send_exam_mock_question(call, user_id, next_idx)


@router.callback_query(F.data == "random_any")
async def random_any_task(call: CallbackQuery):
    """Показать случайную задачу (кроме темы physics)."""
    await call.answer()
    # Собираем все (topic, j), кроме physics
    candidates = []
    for top in topics:
        if top == "physics":
            continue
        questions = kapibara.get(top, [])
        for j in range(len(questions)):
            candidates.append((top, j))
    if not candidates:
        await call.message.answer("Пока нет задач для выбора случайной.")
        return
    top, j = random.choice(candidates)

    # Определяем, показывать ли ссылку на видео (логика как в next)
    showvideo = 1
    user = call.from_user.username
    if user in ['evangecalista']:
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute(
                "SELECT source FROM users WHERE id = ?",
                (call.from_user.id,)
            )
            row = await cursor.fetchone()
            if row and row[0] == 'stepik':
                showvideo = 0
        except:
            pass

    k = kapibara[top][j]
    if k.get('img'):
        photo_path = os.path.join(DATA_DIR, "images", k['img'])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))

    total_questions = len(kapibara[top])
    question_num = f"Случайная задача (вопрос {j+1} из {total_questions}) / Random task {j+1} of {total_questions}\n\n"
    difficulty = k.get("difficulty")
    if isinstance(difficulty, int) and 1 <= difficulty <= 5:
        stars = "★" * difficulty + "☆" * (5 - difficulty)
        diff_line = f"Сложность / Difficulty: {stars}\n"
    else:
        diff_line = ""
    question_text = question_num + diff_line + k["english"] + "\n" + k.get("chinese", '') + k.get("long", '')
    reply = inline_kb(top, j, showvideo)
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(question_text, reply_markup=reply)


@router.callback_query(F.data.startswith('t_'))
async def on_topic_selected(call: CallbackQuery):
    """Выбрана тема — показываем список подтем."""
    await call.answer()
    try:
        topic_idx = int(call.data.split('_')[1])
    except (ValueError, IndexError):
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Ошибка. Выберите тему снова.", reply_markup=kb)
        return
    if topic_idx < 0 or topic_idx >= len(topics):
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Тема не найдена.", reply_markup=kb)
        return
    topic = topics[topic_idx]
    kb = subtopic_kb(topic_idx)
    title = topic[0].upper() + topic[1:]
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(f"📂 {title}\nВыберите подтему / Choose subtopic:", reply_markup=kb)


@router.callback_query(F.data.startswith('s_'))
async def on_subtopic_selected(call: CallbackQuery):
    """Выбрана подтема — показываем первый вопрос подтемы."""
    await call.answer()
    parts = call.data.split('_')
    if len(parts) < 3:
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Ошибка формата.", reply_markup=kb)
        return
    try:
        topic_idx = int(parts[1])
        sub_idx = int(parts[2])
    except ValueError:
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Ошибка формата.", reply_markup=kb)
        return
    topic, j = _get_subtopic_j(topic_idx, sub_idx, 0)
    if topic is None:
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Подтема не найдена.", reply_markup=kb)
        return
    # Показываем первый вопрос подтемы (логика как в next_sub_)
    showvideo = 1
    if call.from_user.username == 'evangecalista':
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (call.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == 'stepik':
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0
    k = kapibara[topic][j]
    if k.get('img'):
        photo_path = os.path.join(DATA_DIR, "images", k['img'])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
    sub_name = subtopics_by_topic[topic][sub_idx]
    total_in_sub = len(questions_by_topic_subtopic.get((topic, sub_name), []))
    # Показываем название выбранной подтемы вместо слова "подтема"
    question_num = (
        f"Вопрос 1 из {total_in_sub} ({sub_name}) / "
        f"Question 1 of {total_in_sub} ({sub_name})\n\n"
    )
    question_text = question_num + k["english"] + "\n" + k.get("chinese", '') + k.get("long", '')
    reply = inline_kb_sub(topic_idx, sub_idx, 0, showvideo)
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(question_text, reply_markup=reply)


@router.callback_query(F.data.startswith('next_sub_'))
async def on_next_sub(call: CallbackQuery):
    """Следующий вопрос в режиме подтемы."""
    parts = call.data.split('_')
    if len(parts) < 5:
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Ошибка формата.", reply_markup=kb)
        return
    try:
        topic_idx = int(parts[2])
        sub_idx = int(parts[3])
        k = int(parts[4])
    except ValueError:
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Ошибка формата.", reply_markup=kb)
        return
    topic = topics[topic_idx] if 0 <= topic_idx < len(topics) else None
    if topic is None:
        kb = await start_kb(call.from_user.id)
        await call.message.answer("Тема не найдена.", reply_markup=kb)
        return
    subtopics_for_topic = subtopics_by_topic.get(topic, [])
    sub_name = subtopics_for_topic[sub_idx] if sub_idx < len(subtopics_for_topic) else None
    j_list = questions_by_topic_subtopic.get((topic, sub_name), []) if sub_name else []
    if k >= len(j_list):
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            kb = await start_kb(call.from_user.id)
            await call.message.answer("Задачи по этой подтеме закончились. Выберите другую подтему или тему.", reply_markup=kb)
        return
    j = j_list[k]
    showvideo = 1
    if call.from_user.username == 'evangecalista':
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (call.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == 'stepik':
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0
    q = kapibara[topic][j]
    if q.get('img'):
        photo_path = os.path.join(DATA_DIR, "images", q['img'])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
    total_in_sub = len(j_list)
    # В заголовке также показываем имя подтемы
    if sub_name:
        question_num = (
            f"Вопрос {k + 1} из {total_in_sub} ({sub_name}) / "
            f"Question {k + 1} of {total_in_sub} ({sub_name})\n\n"
        )
    else:
        question_num = (
            f"Вопрос {k + 1} из {total_in_sub} / "
            f"Question {k + 1} of {total_in_sub}\n\n"
        )
    question_text = question_num + q["english"] + "\n" + q.get("chinese", '') + q.get("long", '')
    reply = inline_kb_sub(topic_idx, sub_idx, k, showvideo)
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(question_text, reply_markup=reply)


@router.callback_query(F.data.startswith('qst_sub_'))
async def on_answer_sub(call: CallbackQuery):
    """Обработка ответа в режиме подтемы."""
    correct_h = {'A': '0', 'B': '1', 'C': '2', 'D': '3', 'E': '4'}
    await call.answer()
    parts = call.data.replace('qst_sub_', '').split('_')
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        topic_idx = int(parts[0])
        sub_idx = int(parts[1])
        k = int(parts[2])
        ans_id = parts[3]
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    topic, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if topic is None:
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[topic][j]
    correct = correct_h.get(q["answer"], "")
    ansok = 1 if correct == ans_id else 0
    if ansok:
        msg_text = "Верно!  / Great!"
        j_list = questions_by_topic_subtopic.get((topic, subtopics_by_topic[topic][sub_idx]), [])
        if k + 1 < len(j_list):
            reply = inline_kb_next_sub(topic_idx, sub_idx, k)
            send_kb = None
        else:
            reply = None
            send_kb = await start_kb(call.from_user.id)
    else:
        msg_text = "Нет, это не так) / Sorry, you are wrong\n\n"
        reply = inline_kb_explain_sub(topic_idx, sub_idx, k)
        send_kb = None
    if db_conn:
        try:
            await db.save_answer(db_conn, call.from_user.id, topic, j, int(ans_id) if ans_id.isdigit() else 0, ansok == 1)
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа в БД: {e}")
    log(call.from_user, [topic, j, ans_id, ansok])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        if reply:
            await call.message.answer(msg_text, reply_markup=reply)
        else:
            await call.message.answer(msg_text + "\nЗадачи по подтеме закончились.", reply_markup=send_kb)


@router.callback_query(F.data.startswith('qst_'))
async def cmd_start(call: CallbackQuery):
    correct_h={'A' : '0' , 'B' : '1' , 'C' : '2', 'D':'3', 'E':'4'}
    await call.answer()
    ans = call.data.replace('qst_', '').split('_')
    if len(ans) < 3:
        await call.message.answer("Ошибка формата. Выберите тему заново.")
        return
    try:
        top = TOPIC_ALIASES.get(ans[0], ans[0])
        j = int(ans[1])
        ans_id = ans[2]
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата. Выберите тему заново.")
        return
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден. Выберите тему заново.")
        return
    k = kapibara[top][j]
    correct = correct_h.get(k["answer"], "")
    try:
        ans_id_int = int(ans_id)
    except ValueError:
        await call.message.answer("Ошибка формата. Выберите тему заново.")
        return
    ansok = 1 if correct == ans_id else 0
    if ansok:
        msg_text = "Верно!  / Great!"
        reply = inline_kb_next(top, j)
    else:
        msg_text = "Нет, это не так) / Sorry, you are wrong" + "\n\n"
        reply = inline_kb_explain(top, j, k)

    # Сохраняем ответ в БД
    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                call.from_user.id,
                top,
                j,
                ans_id_int,
                ansok == 1
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа в БД: {e}")
    
    log(call.from_user,[top,j,ans_id,ansok])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(msg_text, reply_markup=reply)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    log(message.from_user,['start', message.text])
    
    # Извлекаем аргумент команды (текст после /start)
    start_text = None
    if message.text and len(message.text.split()) > 1:
        start_text = ' '.join(message.text.split()[1:])
    
    # Сохраняем/обновляем пользователя в БД
    if db_conn:
        try:
            await db.ensure_user(db_conn, message.from_user, start_text)
            # Если пользователь пришёл по invite-ссылке, обновляем карту приглашений
            _register_invite_for_new_user(message.from_user.id, message.from_user.username, start_text)

            # Если пользователь вернулся по ссылке после успешной оплаты через ЮKassa,
            # помечаем его как оплатившего доступ (аналогично оплате Stars).
            st_low = (start_text or "").lower()
            if st_low.startswith("yookassa_paid"):
                # ожидаем формат типа: yookassa_paid_<payment_id>
                parts = st_low.split("_", 2)
                payment_id = parts[2] if len(parts) >= 3 else st_low
                try:
                    await db.save_star_payment(
                        db_conn,
                        user_id=message.from_user.id,
                        currency="RUB",
                        total_amount=10000,  # 100 рублей в копейках
                        purpose="exam_access",
                        telegram_payment_charge_id=payment_id,
                    )
                except Exception as e:
                    logging.error(f"Ошибка сохранения оплаты через ЮKassa для пользователя {message.from_user.id}: {e}")
        except Exception as e:
            logging.error(f"Ошибка сохранения пользователя в БД: {e}")
    
    # Сохраняем в файл для обратной совместимости
    usr = message.from_user
    dt = str(datetime.datetime.now())
    id = str(usr.id)  
    username = usr.username
    l = [dt, id, username]  
    usr_file = os.path.join(DATA_DIR, "usr.txt")
    try:
        with open(usr_file, "a", encoding='utf-8') as f:
            f.write("\t".join(l+[message.text or ''])+"\n")
    except:
        pass
    
    # Обновляем список stepik из БД
    if db_conn and 'stepik' in (start_text or '').lower():
        try:
            stepik_ids = await db.get_users_by_source(db_conn, 'stepik')
            stepik.update(str(uid) for uid in stepik_ids)
        except:
            pass

    # Обновляем список stepik из БД
    if db_conn and 'cscagroup' in (start_text or '').lower():
        try:
            cscagroup_ids = await db.get_users_by_source(db_conn, 'cscagroup')
            cscagroup.update(str(uid) for uid in cscagroup_ids)
        except:
            pass
    
    kb = await start_kb(message.from_user.id)
    lang = (message.from_user.language_code or "").lower()
    if lang.startswith("ru"):
        greet = """Привет! Я CSCA бот.  
Неограниченный доступ для студентов курса "Подготовка к CSCA" https://stepik.org/a/268161  и  
участников групп подготовки по метематике https://t.me/+c1ksuGkuO1BiNDk6
и физике https://t.me/+dUnPAdJO1w4zZWUy

"""
    else:
        greet = "Hi!! I am a CSCA math exam prep bot. I've got  a lot of practice problems and can verify your answers. "
    await message.answer(greet, reply_markup=kb)
   
   # await message.answer("Это тестовая версия бота. Нашел ошибку? Есть идея? Пиши @csca_math_exam или прямо здесь.", reply_markup=start_kb()) 
   # await message.answer("Видео-разборы задач в группе https://t.me/milgecru/385") 
    
   
    
    #j=0
    #k = kapibara[j]
   # await  message.answer(k["english"]+"\n" + k["chinese"],  reply_markup=inline_kb(j))


@router.callback_query(F.data.startswith('explain'))
async def cmd_start(call: CallbackQuery):
    
    ans = call.data.replace('explain_', '').split('_')
    top = TOPIC_ALIASES.get(ans[0], ans[0])
    try:
        j = int(ans[1])
    except (ValueError, IndexError):
        await call.answer("Ошибка формата.")
        return
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.answer("Вопрос не найден.")
        return
    log(call.from_user, ['explain', top, j])
    # Отключаем показ ссылок на видео, но не ломаем старые callback'и
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer("Видео‑разборы временно недоступны.")
        
       


@router.callback_query(F.data.startswith('next'))
async def cmd_start(call: CallbackQuery):
    
    showvideo = 1
    user = call.from_user.username
    
    # Проверяем, нужно ли скрывать видео для этого пользователя
    if user in ['evangecalista']:
        showvideo = 0
    elif db_conn:
        try:
            # Проверяем source пользователя в БД
            cursor = await db_conn.execute(
                "SELECT source FROM users WHERE id = ?",
                (call.from_user.id,)
            )
            row = await cursor.fetchone()
            if row and row[0] == 'stepik':
                showvideo = 0
        except:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0
    
    
    ans = call.data.replace('next_', '').split('_')
    top = TOPIC_ALIASES.get(ans[0], ans[0])
    j = int(ans[1])
    log(call.from_user,['next', top, j])
    
    #log(message.from_user,['log', str(ans) , top, str(j), str(kapibara[top])s ])
    if j == 0 :
          user_status = await bot.get_chat_member(chat_id="@csca_math_exam", user_id=call.message.chat.id)
          log(call.from_user,['status', str(user_status) ])
          
    if j == 7 :
          message=makeinvite(call.from_user)  
          async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"): 
             log(call.from_user,['invite'])
             await  call.message.answer(message)

          
          
    if top not in kapibara:
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            kb = await start_kb(call.from_user.id)
            await call.message.answer("Тема не найдена. Выберите тему из меню.", reply_markup=kb)
    elif j >= len(kapibara[top]):
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            log(call.from_user, ['end'])
            kb = await start_kb(call.from_user.id)
            await call.message.answer('Больше нет вопросов по этой теме. Скоро добавлю новые вопросы.', reply_markup=kb)
    else:
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            k = kapibara[top][j]
            if 'img' in k:
                photo_path = os.path.join(DATA_DIR, "images", k['img'])
                await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
            total_questions = len(kapibara[top])
            question_num = f"Вопрос {j+1} из {total_questions} / Question {j+1} of {total_questions}\n\n"
            question_text = question_num + k["english"] + "\n" + k.get("chinese", '') + k.get("long", '')
            await call.message.answer(question_text, reply_markup=inline_kb(top, j, showvideo))
    

@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Команда для просмотра статистики пользователя"""
    if not db_conn:
        await message.answer("Статистика временно недоступна.")
        return

    try:
        stats = await db.get_user_stats(db_conn, message.from_user.id)

        if stats["total_answered"] == 0:
            await message.answer("Вы ещё не ответили ни на один вопрос. Начните с команды /start!")
            return

        lang = message.from_user.language_code or "en"
        if lang == "ru":
            msg = "📊 Ваша статистика:\n\n"
            msg += f"Всего ответов: {stats['total_answered']}\n"
            msg += f"Правильных: {stats['total_correct']}\n"
            acc = (stats["total_correct"] / stats["total_answered"] * 100) if stats["total_answered"] > 0 else 0
            msg += f"Точность: {acc:.1f}%\n\n"
            msg += "По темам:\n"
            for t in stats["by_topic"]:
                msg += f"• {t['topic']}: {t['answered']} ответов, {t['correct']} правильных ({t['accuracy']:.1f}%)\n"
        else:
            msg = "📊 Your statistics:\n\n"
            msg += f"Total answers: {stats['total_answered']}\n"
            msg += f"Correct: {stats['total_correct']}\n"
            acc = (stats["total_correct"] / stats["total_answered"] * 100) if stats["total_answered"] > 0 else 0
            msg += f"Accuracy: {acc:.1f}%\n\n"
            msg += "By topics:\n"
            for t in stats["by_topic"]:
                msg += f"• {t['topic']}: {t['answered']} answers, {t['correct']} correct ({t['accuracy']:.1f}%)\n"

        kb = await start_kb(message.from_user.id)
        await message.answer(msg, reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        await message.answer("Ошибка при получении статистики. Попробуйте позже.")


@router.message(Command("clearstats"))
async def cmd_clear_stats(message: types.Message):
    """Команда для очистки статистики пользователя."""
    if not db_conn:
        await message.answer("Статистика временно недоступна.")
        return

    try:
        await db.clear_user_stats(db_conn, message.from_user.id)
        lang = message.from_user.language_code or "en"
        if lang == "ru":
            text = "Ваша статистика была очищена."
        else:
            text = "Your statistics have been cleared."
        kb = await start_kb(message.from_user.id)
        await message.answer(text, reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка очистки статистики: {e}")
        await message.answer("Не удалось очистить статистику. Попробуйте позже.")


@router.message(Command("exam25stats"))
async def cmd_exam25stats(message: types.Message):
    """Показать результаты экзамена 25 января для текущего пользователя."""
    if not exam_questions:
        await message.answer("Экзамен 25 января ещё не настроен.")
        return
    user_id = message.from_user.id
    state = await _get_exam_state(user_id)
    if not state or not state.get("answered"):
        await message.answer("Вы ещё не проходили экзамен 25 января.")
        return
    correct = state.get("correct_count", 0)
    total_q = len(exam_questions)
    if EXAM_TOTAL_DIFFICULTY:
        k = state.get("correct_difficulty", 0) / EXAM_TOTAL_DIFFICULTY * 100
    else:
        k = 0.0
    # Дублируем результат сразу на русском и английском
    ru_block = (
        "📊 Ваш результат экзамена 25 января:\n\n"
        f"Вы решили правильно {correct} из {total_q} задач "
        f"и набрали {k:.2f} баллов."
    )
    en_block = (
        "📊 Your January 25 exam result:\n\n"
        f"You solved {correct} out of {total_q} tasks correctly "
        f"and scored {k:.2f} points."
    )
    text = ru_block + "\n\n" + en_block
    kb = await start_kb(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(Command("exam21decstats"))
async def cmd_exam21decstats(message: types.Message):
    """Показать результаты экзамена 21 декабря для текущего пользователя."""
    if not exam_questions_dec:
        await message.answer("Экзамен 21 декабря ещё не настроен.")
        return
    user_id = message.from_user.id
    state = await _get_exam_dec_state(user_id)
    if not state or not state.get("answered"):
        await message.answer("Вы ещё не проходили экзамен 21 декабря.")
        return
    correct = state.get("correct_count", 0)
    total_q = len(exam_questions_dec)
    k = (state.get("correct_difficulty", 0) / EXAM_DEC_TOTAL_DIFFICULTY * 100) if EXAM_DEC_TOTAL_DIFFICULTY else 0.0
    ru_block = (
        "📊 Ваш результат экзамена 21 декабря:\n\n"
        f"Вы решили правильно {correct} из {total_q} задач "
        f"и набрали {k:.2f} баллов."
    )
    en_block = (
        "📊 Your December 21 exam result:\n\n"
        f"You solved {correct} out of {total_q} tasks correctly "
        f"and scored {k:.2f} points."
    )
    text = ru_block + "\n\n" + en_block
    kb = await start_kb(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(Command("exam_mock_stats"))
async def cmd_exam_mock_stats(message: types.Message):
    """Показать результаты Mock Exam для текущего пользователя."""
    if not mock_questions:
        await message.answer("Mock Exam ещё не настроен.")
        return
    user_id = message.from_user.id
    state = await _get_exam_mock_state(user_id)
    if not state or not state.get("answered"):
        await message.answer("Вы ещё не проходили Mock Exam.")
        return
    correct = state.get("correct_count", 0)
    total_q = len(mock_questions)
    k = (state.get("correct_difficulty", 0) / MOCK_TOTAL_DIFFICULTY * 100) if MOCK_TOTAL_DIFFICULTY else 0.0
    ru_block = (
        "📊 Ваш результат Mock Exam (пробный экзамен):\n\n"
        f"Вы решили правильно {correct} из {total_q} задач "
        f"и набрали {k:.2f} баллов."
    )
    en_block = (
        "📊 Your Mock Exam result:\n\n"
        f"You solved {correct} out of {total_q} tasks correctly "
        f"and scored {k:.2f} points."
    )
    text = ru_block + "\n\n" + en_block
    kb = await start_kb(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(Command("inviteusers"))
async def cmd_inviteusers(message: types.Message):
    """Показать список пользователей, по приглашениям которых пришли новые пользователи."""
    # Строим список (inviter_id, username, count) из invite_relations
    if not invite_relations:
        lang = (message.from_user.language_code or "en").lower()
        if lang.startswith("ru"):
            text = "Пока нет данных о приглашениях."
        else:
            text = "There is no invitation data yet."
        await message.answer(text)
        return

    # Сортируем по количеству приглашённых (по убыванию)
    items = sorted(
        ((inviter_id, len(invited)) for inviter_id, invited in invite_relations.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    lang = (message.from_user.language_code or "en").lower()
    if lang.startswith("ru"):
        lines = ["📨 Пользователи, по приглашениям которых пришли новые пользователи:\n"]
        for inviter_id, count in items:
            lines.append(f"- id {inviter_id}: пригласил(а) {count} пользовател(ей)")
    else:
        lines = ["📨 Users whose invitations brought new users:\n"]
        for inviter_id, count in items:
            lines.append(f"- id {inviter_id}: invited {count} user(s)")
    await message.answer("\n".join(lines))


@router.message(Command("pay"))
async def cmd_pay(message: types.Message):
    """Показать информацию об оплате доступа к режиму экзамена и выставить счёт в Telegram Stars."""
    pay(message.from_user)


async def pay(user) :
    lang = (user.language_code or "en").lower()
    # Строим персональную ссылку так же, как в makeinvite()
    try:
        uid = user.id
        username = str(user.username or "")
        inv = username[:3] + str(uid)[:3]
        invite_link = f"https://t.me/csca_mathbot?start=invite{inv}"
    except Exception:
        invite_link = "https://t.me/csca_mathbot"

    if lang.startswith("ru"):
        text = (
            "Режим экзамена недоступен.\n\n"
            "Если вы приобретали курс  https://stepik.org/a/268161, перейдите в бот по ссылке из первого урока.\n"
            "Если вы в группе «Готовим к CSCA», перейдите по прямой ссылке из группы.\n\n"
            f"Также вы можете разместить вашу персональную ссылку {invite_link} в любом чате о CSCA — "
            "доступ откроется после перехода по вашей ссылке трёх новых пользователей.\n\n"
            "Если ни один из этих способов вам не подходит, вы можете оплатить доступ "
            "100 Telegram Stars  по кнопк ниже."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
             #       InlineKeyboardButton(text="💳 Оплатить 100 руб. через ЮKassa", callback_data="pay_yookassa"),
                    InlineKeyboardButton(text="⭐ Оплатить 100 Telegram Stars", callback_data="pay_stars"),
                ]
            ]
        )
    else:
        text = (
            "The exam mode is currently unavailable.\n\n"
            "If you purchased the course https://stepik.org/a/268161, please open the bot using the link "
            "from the first lesson.\n"
            "If you are in the “Preparing for CSCA” group, use the direct link from that group.\n\n"
            f"You can also share your personal invitation link {invite_link} in any CSCA-related chat — "
            "access will be unlocked after three new users follow your link.\n\n"
            "If none of these options works for you, you can pay 100 Telegram Stars  "
            "using the button below."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                #    InlineKeyboardButton(text="💳 Pay 100 RUB via YooKassa", callback_data="pay_yookassa"),
                    InlineKeyboardButton(text="⭐ Pay 100 Telegram Stars", callback_data="pay_stars"),
                ]
            ]
        )

    await bot.send_message(chat_id=user.id, text=text, reply_markup=kb)


@router.callback_query(F.data == "pay_stars")
async def on_pay_stars(call: CallbackQuery):
    """Кнопка 'Оплатить 100 Telegram Stars' — отправляем инвойс в звёздах."""
    await call.answer()
    user = call.from_user
    lang = (user.language_code or "en").lower()

    if lang.startswith("ru"):
        title = "Доступ к режиму экзамена"
        description = "Оплата 100 Telegram Stars за неограниченный доступ ко всем функциям бота."
    else:
        title = "Exam mode access"
        description = "Get unlimited access to all bot functionality for 100 Telegram Stars."

    # 100 Stars (используем то же значение, что и раньше в боте)
    prices = [types.LabeledPrice(label="Exam access", amount=100)]
    await bot.send_invoice(
        chat_id=user.id,
        title=title,
        description=description,
        payload="exam_access_100stars",
        currency="XTR",
        prices=prices,
        provider_token="",
    )


@router.callback_query(F.data == "pay_yookassa")
async def on_pay_yookassa(call: CallbackQuery):
    """Кнопка 'Оплатить 100 руб. через ЮKassa' — отправляем инвойс в рублях через платёжного провайдера."""
    await call.answer()
    user = call.from_user
    lang = (user.language_code or "en").lower()

    if lang.startswith("ru"):
        title = "Доступ к режиму экзамена"
        description = "Оплата 100 рублей (через ЮKassa) за неограниченный доступ ко всем функциям бота."
    else:
        title = "Exam mode access"
        description = "Pay 100 RUB via YooKassa to get unlimited access to all bot functionality."

    # 100 рублей в копейках
    prices = [types.LabeledPrice(label="Exam access", amount=10000)]
    await bot.send_invoice(
        chat_id=user.id,
        title=title,
        description=description,
        payload="exam_access_100rub_yookassa",
        currency="RUB",
        prices=prices,
        #provider_token="390540012:LIVE:91710",
        provider_token="381764678:TEST:170949" 
    )


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    """Подтверждаем все pre_checkout запросы для оплат в звёздах."""
    try:
        await pre_checkout_query.answer(ok=True)
    except Exception as e:
        logging.error(f"Ошибка при answer_pre_checkout_query: {e}")


@router.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    """Обработка успешной оплаты в Telegram Stars и сохранение факта оплаты в БД."""
    if not db_conn:
        return
    sp = message.successful_payment
    try:
        await db.save_star_payment(
            db_conn,
            user_id=message.from_user.id,
            currency=sp.currency,
            total_amount=sp.total_amount,
            purpose="exam_access",
            telegram_payment_charge_id=sp.telegram_payment_charge_id,
        )
    except Exception as e:
        logging.error(f"Ошибка сохранения оплаты в БД: {e}")
        return

    lang = (message.from_user.language_code or "en").lower()
    if lang.startswith("ru"):
        text = "Оплата 100 Telegram Stars получена. Доступ к режиму экзамена и Mock Exam открыт."
    else:
        text = "Payment of 100 Telegram Stars received. Exam mode and Mock Exam are now unlocked for you."
    await message.answer(text)


@router.message()
async def cmd_start(message: Message):
    # Исправление бага: kapibara - это словарь, нужно брать первую тему
    if topics and topics[0] in kapibara and len(kapibara[topics[0]]) > 0:
        k = kapibara[topics[0]][0]
        question_text = k["english"]+"\n" + k.get("chinese","")
    else:
        question_text = "Выберите тему для начала."
    
    log(message.from_user,['message', message.text.replace("\n"," ") if message.text else "" ])
   # async with ChatActionSender(bot=bot, chat_id=message.chat.id, action="typing"):
   #  await message.answer( "Спасибо! Передам сообщение разработчикам")
   #   await message.answer( "Чтобы продолжить, ответь на любой предыдущий вопрос")
   #   await message.answer( "Или начни сначала")
   #   kb = await start_kb(message.from_user.id)
   #   await message.answer( question_text, reply_markup=kb)



# Запуск процесса поллинга новых апдейтов
async def main():
    global db_conn, stepik, cscagroup
    
    # Инициализируем БД
    try:
        db_conn = await db.init_db()
        logging.info("База данных инициализирована")
        
        # Загружаем список пользователей с source='stepik' из БД
        stepik_ids = await db.get_users_by_source(db_conn, 'stepik')
        stepik.update(str(uid) for uid in stepik_ids)
        logging.info(f"Загружено {len(stepik)} пользователей с source='stepik'")
        cscagroup_ids = await db.get_users_by_source(db_conn, 'cscagroup')
        cscagroup.update(str(uid) for uid in cscagroup_ids)
        logging.info(f"Загружено {len(cscagroup)} пользователей с source='cscagroup'")

        # Строим карту приглашений invite_relations
        await _build_invite_relations_from_db()
        logging.info(f"Построено {len(invite_relations)} записей invite_relations")
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")
        db_conn = None
    
    try:
        # Регистрируем команды бота в меню Telegram
        try:
            await bot.set_my_commands(
                [
                    types.BotCommand(command="start", description="Начать тренировку"),
                    types.BotCommand(command="stats", description="Показать мою статистику"),
                    types.BotCommand(command="clearstats", description="Очистить мою статистику"),
                    types.BotCommand(command="exam25stats", description="Результат экзамена 25 января"),
                    types.BotCommand(command="exam21decstats", description="Результат экзамена 21 декабря"),
                    types.BotCommand(command="exam_mock_stats", description="Результат Mock Exam"),

                ]
            )
        except Exception as e:
            logging.warning(f"Не удалось установить команды бота: {e}")

        await dp.start_polling(bot)
    finally:
        # Закрываем соединение с БД при остановке
        if db_conn:
            await db_conn.close()
            logging.info("Соединение с БД закрыто")

if __name__ == "__main__":
    # Настраиваем логирование
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())
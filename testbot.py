#!/usr/bin/env python
import asyncio
import logging
import traceback
import datetime
import json
import os
import random
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from langchain_amvera import AmveraLLM
from langchain_core.messages import SystemMessage, HumanMessage
#from dotenv import load_dotenv


API_TOKEN = os.environ.get('BOT_TOKEN', '8162784129:AAHbZZ1JZONUH8sujANe4txembuBeRsXaCM')

#Prod bot
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
    global API_TOKEN
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

# --- Подсказки (картинки) ---
HINTS_IMAGES_DIR = os.path.join(DATA_DIR, "images", "hints")
# cache_key: (bases_tuple, suf)
# value: ordered list of hint image paths (n=0..)
_HINT_IMAGE_CACHE: dict[tuple[tuple[str, ...], str], list[str]] = {}

# --- RAG контекст ---
_RAG_TEXT_CACHE: str | None = None


def load_rag_text() -> str:
    """
    Загружает контекст для RAG из файла `data/rag.txt`.
    Результат кешируется, чтобы не перечитывать файл на каждом сообщении.
    """
    global _RAG_TEXT_CACHE
    if _RAG_TEXT_CACHE is not None:
        return _RAG_TEXT_CACHE

    rag_path = os.path.join(DATA_DIR, "rag.txt")
    try:
        if not os.path.isfile(rag_path):
            _RAG_TEXT_CACHE = ""
            return _RAG_TEXT_CACHE

        with open(rag_path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
        # Ограничим размер, чтобы не раздувать prompt слишком сильно
        _RAG_TEXT_CACHE = txt[:20000]
    except Exception as e:
        logging.error(f"Ошибка чтения rag.txt: {e}")
        _RAG_TEXT_CACHE = ""

    return _RAG_TEXT_CACHE


# Совместимость со старым именем (если где-то ещё вызывается)
def _load_rag_text() -> str:
    return load_rag_text()


def _lang_suffix(lang_code: str) -> str:
    lang = (lang_code or "").lower()
    return "ru" if lang.startswith("ru") else "en"


def _derive_subtopic_id(task_id: str | None) -> str | None:
    """
    В идеале id подтемы совпадает с префиксом id задачи (например, "RI10" -> "RI").
    Если префикс не выделяется - возвращаем None.
    """
    if not task_id:
        return None
    m = re.match(r"^([A-Za-z]+)", str(task_id).strip())
    return m.group(1) if m else None


def _get_hint_image_path(q: dict, lang_code: str) -> list[str]:
    """
    Возвращает ВСЕ найденные подсказки в порядке номеров.

    Ищем файлы формата:
    - base = id подтемы ИЛИ id задачи (приоритет: id задачи, затем id подтемы)
    - suf = язык ('ru'/'en')
    - имя: {base}{suf}        -> номер n = 0
    - имя: {base}{suf}_{n}   -> номер n >= 1
    - расширение: .png/.jpg/.jpeg/.webp (необязательно)
    """
    if not isinstance(q, dict):
        return []
    if not os.path.isdir(HINTS_IMAGES_DIR):
        return []

    task_id = q.get("id")
    if not task_id:
        return []

    suf = _lang_suffix(lang_code)
    sub_id = _derive_subtopic_id(task_id)
    bases: list[str] = [str(task_id)]
    if sub_id and sub_id not in bases:
        bases.append(sub_id)

    cache_key = (tuple(bases), suf)
    if cache_key in _HINT_IMAGE_CACHE:
        return list(_HINT_IMAGE_CACHE[cache_key])

    try:
        files = os.listdir(HINTS_IMAGES_DIR)
    except Exception:
        return []

    ext_priority = {"": 0, ".png": 1, ".jpg": 2, ".jpeg": 3, ".webp": 4}

    def ext_of(fname: str) -> str:
        return os.path.splitext(fname)[1].lower()

    for base in bases:
        # для каждого base собираем n -> path
        found: dict[int, str] = {}
        for fname in files:
            if not fname.startswith(base + suf):
                continue
            # base+suf + optional _n + optional .ext
            # Пример: HFru, HFru_1.png
            pat = rf"^{re.escape(base)}{re.escape(suf)}(?:_(\d+))?(?:\.(?:png|jpg|jpeg|webp))?$"
            m = re.match(pat, fname)
            if not m:
                continue
            n_str = m.group(1)
            n_val = int(n_str) if n_str is not None else 0
            full = os.path.join(HINTS_IMAGES_DIR, fname)
            if not os.path.isfile(full):
                continue
            cur_ext = ext_of(fname)
            # если для одного n нашлось несколько расширений, берём приоритетное
            if n_val not in found or ext_priority.get(cur_ext, 999) < ext_priority.get(ext_of(os.path.basename(found[n_val])), 999):
                found[n_val] = full
        if found:
            ordered = [found[n] for n in sorted(found.keys())]
            _HINT_IMAGE_CACHE[cache_key] = ordered
            return list(ordered)

    _HINT_IMAGE_CACHE[cache_key] = []
    return []


def _has_hint_image(q: dict, lang_code: str) -> bool:
    """Общая проверка наличия картинки-подсказки для задачи."""
    return len(_get_hint_image_path(q, lang_code)) > 0

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

# Запасная ссылка «Перейти в чат» для ru, если тема не найдена в topic_links (кнопка после 👎 к решению из data.txt)
DEFAULT_CHAT_FALLBACK_RU = "https://t.me/csca_math_exam/1"

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

# Не загружаем выключенные задачи (topic="off" или type="off")
def _is_task_enabled(x: dict) -> bool:
    try:
        topic = (x.get("topic") or "").strip().lower()
        typ = (x.get("type") or "").strip().lower()
    except Exception:
        return True
    if topic == "off" or typ == "off":
        return False
    return True


# Загружаем вопросы из агрегированного data.txt + отдельного data_physics.txt
try:
    data_path = os.path.join(DATA_DIR, 'data.txt')
    with open(data_path, 'r', encoding='utf-8') as f:
        kpb_raw = json.load(f)
    # В массиве могут попадаться не-словари (вложенные списки и т.п.) — оставляем только словари
    kpb = [
        x
        for x in (kpb_raw if isinstance(kpb_raw, list) else [kpb_raw])
        if isinstance(x, dict) and _is_task_enabled(x)
    ]

    # Подгружаем физику из отдельного файла (если есть)
    physics_path = os.path.join(DATA_DIR, 'data_physics.txt')
    try:
        if os.path.exists(physics_path):
            with open(physics_path, 'r', encoding='utf-8') as pf:
                p_raw = json.load(pf)
            p_list = [
                x
                for x in (p_raw if isinstance(p_raw, list) else [p_raw])
                if isinstance(x, dict) and _is_task_enabled(x)
            ]
            kpb.extend(p_list)
    except Exception as e:
        logging.error(f"Ошибка загрузки data_physics.txt: {e}")
        logging.error(traceback.format_exc())

    # Подгружаем химию из отдельного файла (если есть)
    chemistry_path = os.path.join(DATA_DIR, 'data_chemestry.txt')
    try:
        if os.path.exists(chemistry_path):
            with open(chemistry_path, 'r', encoding='utf-8') as cf:
                c_raw = json.load(cf)
            c_list = [
                x
                for x in (c_raw if isinstance(c_raw, list) else [c_raw])
                if isinstance(x, dict) and _is_task_enabled(x)
            ]
            kpb.extend(c_list)
    except Exception as e:
        logging.error(f"Ошибка загрузки data_chemestry.txt: {e}")
        logging.error(traceback.format_exc())

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
        "arithmetic and geometric mean",
        "sequences",
        "complex numbers",
        "probability",
        "physics",
    ]
    order_index = {name: i for i, name in enumerate(DESIRED_TOPIC_ORDER)}
    topics.sort(key=lambda t: (order_index.get(t, len(DESIRED_TOPIC_ORDER)), t))

    # Фиксированный порядок подтем (по возрастанию средней сложности),
    # кроме темы "physics" (её оставляем как есть: сортировка по имени).
    FIXED_SUBTOPIC_ORDER_BY_TOPIC = {
        "Algebraic and geometric mean": ["arithmetic mean", "geometric mean"],
        "complex numbers": ["simple tasks", "hard tasks"],
        "conic curves": ["circle", "parabola", "ellipse", "hyperbola"],
        "functions": [
            "Function domain",
            "functions properties",
            "graphs",
            "inverse functions",
            "inequalities",
            "function equality",
        ],
        "geometry": [
            "coordinate geometry",
            "distance formula",
            "analytic geometry",
            "lines",
            "vectors",
        ],
        "inequalities": [
            "properties of inequalities",
            "absolute value",
            "real numbers",
            "rational inequalities",
            "quadratic inequalities",
        ],
        "logarithmic functions": ["logarithms"],
        "probability": ["Simple Probability"],
        "sequences": ["simple tasks", "hard tasks"],
        "sets": ["set operations"],
        "trigonometry": [
            "trigonometric values",
            "terminal side through point",
            "trigonometric identities",
            "Properties of trigonometric functions",
            "double angle formula",
            "trigonometric expressions",
            "half-angle formula",
            "sin and cos of sum",
        ],
    }

    # Группируем вопросы по темам (только элементы-словари)
    for top in topics:
        kapibara[top] = [x for x in kpb if x.get('topic', '') == top]
    kpb = {}
except FileNotFoundError:
    logging.warning("Файл data.txt не найден в директории %s, вопросы не загружены", DATA_DIR)
except Exception as e:
    logging.error(f"Ошибка загрузки data.txt: {e}")
    logging.error(traceback.format_exc())

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
    if top not in ("physics", "chemistry") and top in FIXED_SUBTOPIC_ORDER_BY_TOPIC:
        order = FIXED_SUBTOPIC_ORDER_BY_TOPIC[top]
        sub_order_index = {name: i for i, name in enumerate(order)}
        # Сначала подтемы из фиксированного порядка, затем любые "лишние" (если вдруг появятся).
        subtopics_by_topic[top] = sorted(
            subs,
            key=lambda s: (sub_order_index.get(s, 10**6), s),
        )
    else:
        subtopics_by_topic[top] = sorted(subs)
    for j, item in enumerate(kapibara.get(top, [])):
        if not isinstance(item, dict):
            continue
        sub = (item.get("subtopic") or "").strip() or "general"
        key = (top, sub)
        if key not in questions_by_topic_subtopic:
            questions_by_topic_subtopic[key] = []
        questions_by_topic_subtopic[key].append(j)


MATH_ALL_NON_RU_LINK = "https://t.me/+hN3O2vl9211mZmU6"


def _q_diff(q: dict) -> int:
    try:
        d = int(q.get("difficulty", 1))
    except (TypeError, ValueError):
        d = 1
    return max(1, min(5, d))


def _build_math_subtopic_avg() -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for top in topics:
        if top in ("physics", "chemistry"):
            continue
        for sub in subtopics_by_topic.get(top, []):
            j_list = questions_by_topic_subtopic.get((top, sub), [])
            if not j_list:
                continue
            vals = [_q_diff(kapibara[top][j]) for j in j_list if 0 <= j < len(kapibara[top])]
            if vals:
                out[(top, sub)] = sum(vals) / len(vals)
    return out


MATH_SUBTOPIC_AVG_DIFF = _build_math_subtopic_avg()
MATH_SUBTOPICS_SORTED = sorted(MATH_SUBTOPIC_AVG_DIFF.keys(), key=lambda ts: (MATH_SUBTOPIC_AVG_DIFF[ts], ts[0], ts[1]))

# --- После темы/подтемы: снова показывать задачи, где последний ответ неверный
#     или первый ответ после показа был неверный (не «с первого раза») ---
# (user_id, topic) -> { j: {"first": bool|None, "last": bool|None} }
_topic_answer_marks: dict[tuple[int, str], dict[int, dict]] = {}
_topic_in_review: dict[tuple[int, str], bool] = {}
_topic_linear_active: dict[tuple[int, str], bool] = {}
# Последняя показанная задача в раунде повторов (чтобы не дублировать подряд)
_topic_last_review_shown: dict[tuple[int, str], int] = {}

# (user_id, topic_idx, sub_idx) -> { k: {"first": bool|None, "last": bool|None} }
_sub_answer_marks: dict[tuple[int, int, int], dict[int, dict]] = {}
_sub_in_review: dict[tuple[int, int, int], bool] = {}
_sub_linear_active: dict[tuple[int, int, int], bool] = {}
_sub_last_review_shown: dict[tuple[int, int, int], int] = {}


def _parse_next_topic_callback(data: str) -> tuple[str, int]:
    if not data.startswith("next_"):
        raise ValueError("not next")
    raw = data[5:]
    top_raw, j_str = raw.rsplit("_", 1)
    j = int(j_str)
    top = TOPIC_ALIASES.get(top_raw, top_raw)
    if top not in kapibara:
        top = top_raw
    return top, j


def _parse_qst_topic_callback(data: str) -> tuple[str, int, str]:
    if not data.startswith("qst_"):
        raise ValueError("not qst")
    raw = data[4:]
    top_raw, j_str, ans_id = raw.rsplit("_", 2)
    j = int(j_str)
    top = TOPIC_ALIASES.get(top_raw, top_raw)
    if top not in kapibara:
        top = top_raw
    return top, j, ans_id


def _topic_marks(uid: int, top: str) -> dict[int, dict]:
    return _topic_answer_marks.setdefault((uid, top), {})


def _register_topic_question_displayed(uid: int, top: str, j: int, linear: bool) -> None:
    if not linear:
        return
    m = _topic_marks(uid, top)
    if j not in m:
        m[j] = {"first": None, "last": None}
    else:
        m[j]["first"] = None


def _topic_record_answer(uid: int, top: str, j: int, linear: bool, ansok: bool) -> None:
    if not linear:
        return
    cell = _topic_marks(uid, top).setdefault(j, {"first": None, "last": None})
    if cell["first"] is None:
        cell["first"] = bool(ansok)
    cell["last"] = bool(ansok)


def _topic_needs_review_j(uid: int, top: str, j: int) -> bool:
    cell = _topic_marks(uid, top).get(j)
    if not cell:
        return False
    first, last = cell.get("first"), cell.get("last")
    return last is False or first is False


def _topic_build_review_set(uid: int, top: str, n: int) -> set[int]:
    return {jj for jj in range(n) if _topic_needs_review_j(uid, top, jj)}


def _pick_next_review_index(review: set[int], last_shown: int | None) -> int | None:
    """
    Индекс следующей задачи в повторах: не совпадает с last_shown, если в review есть другие.
    Если осталась только одна задача и она же last_shown — None (завершить тему/подтему без второго показа подряд).
    """
    if not review:
        return None
    sorted_r = sorted(review)
    alt = [x for x in sorted_r if x != last_shown]
    if alt:
        return alt[0]
    return None


def _topic_clear_session(uid: int, top: str) -> None:
    _topic_answer_marks.pop((uid, top), None)
    _topic_in_review.pop((uid, top), None)
    _topic_linear_active.pop((uid, top), None)
    _topic_last_review_shown.pop((uid, top), None)


def _sub_key(uid: int, topic_idx: int, sub_idx: int) -> tuple[int, int, int]:
    return (uid, topic_idx, sub_idx)


def _sub_marks(uid: int, topic_idx: int, sub_idx: int) -> dict[int, dict]:
    return _sub_answer_marks.setdefault(_sub_key(uid, topic_idx, sub_idx), {})


def _register_sub_question_displayed(uid: int, topic_idx: int, sub_idx: int, k: int, linear: bool) -> None:
    if not linear:
        return
    m = _sub_marks(uid, topic_idx, sub_idx)
    if k not in m:
        m[k] = {"first": None, "last": None}
    else:
        m[k]["first"] = None


def _sub_record_answer(uid: int, topic_idx: int, sub_idx: int, k: int, linear: bool, ansok: bool) -> None:
    if not linear:
        return
    cell = _sub_marks(uid, topic_idx, sub_idx).setdefault(k, {"first": None, "last": None})
    if cell["first"] is None:
        cell["first"] = bool(ansok)
    cell["last"] = bool(ansok)


def _sub_needs_review_k(uid: int, topic_idx: int, sub_idx: int, k: int) -> bool:
    cell = _sub_marks(uid, topic_idx, sub_idx).get(k)
    if not cell:
        return False
    first, last = cell.get("first"), cell.get("last")
    return last is False or first is False


def _sub_build_review_set(uid: int, topic_idx: int, sub_idx: int, n: int) -> set[int]:
    return {kk for kk in range(n) if _sub_needs_review_k(uid, topic_idx, sub_idx, kk)}


def _sub_clear_session(uid: int, topic_idx: int, sub_idx: int) -> None:
    k = _sub_key(uid, topic_idx, sub_idx)
    _sub_answer_marks.pop(k, None)
    _sub_in_review.pop(k, None)
    _sub_linear_active.pop(k, None)
    _sub_last_review_shown.pop(k, None)


# --- Режим "Все задачи по математике" ---
_math_all_sessions: dict[int, dict] = {}
_pending_exam_language_selection: set[int] = set()

# Последняя показанная задача из kapibara (для LLM): user_id -> (topic, j, порядок индексов вариантов как на экране или None)
_last_seen_kapibara_question: dict[int, tuple[str, int, tuple[int, ...] | None]] = {}

# user_id -> (topic, j), для которых уже показывали ответ LLM по задаче
_llm_solution_shown_for_task: dict[int, set[tuple[str, int]]] = {}

# Режим контекста для вызова LLM из произвольного текста (on_any_message)
LLM_CONTEXT_ADDTEXT_ONLY = "addtext_only"  # addtext в системных сообщениях, без RAG
LLM_CONTEXT_RAG_ONLY = "rag_only"  # только RAG, без addtext-классификатора
LLM_CONTEXT_LAST_TASK = "last_task"  # условие и варианты последней показанной задачи


def _record_last_seen_question(
    user_id: int, top: str, j: int, option_order: list[int] | None = None
) -> None:
    oo = tuple(option_order) if option_order is not None else None
    _last_seen_kapibara_question[user_id] = (top, int(j), oo)


def _mark_llm_solution_shown(user_id: int, top: str, j: int) -> None:
    _llm_solution_shown_for_task.setdefault(user_id, set()).add((top, int(j)))


def _user_has_viewed_llm_solution_for_task(user_id: int, top: str, j: int) -> bool:
    """Пользователь уже получал от бота ответ LLM по этой задаче (topic, j в kapibara)."""
    return (top, int(j)) in _llm_solution_shown_for_task.get(user_id, set())


def _format_last_seen_task_for_llm(
    user_id: int, lang: str, exam_lang: str | None = None
) -> str | None:
    key = _last_seen_kapibara_question.get(user_id)
    if not key:
        return None
    if len(key) == 2:
        top, j = key
        order = None
    else:
        top, j, order = key
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        return None
    q = kapibara[top][j]
    stem_only = _task_text_for_exam_lang(q, exam_lang)
    opts = _task_options_for_display(q, exam_lang)
    tid = q.get("id") or ""
    opt_lines = ""
    if opts and not _options_any_line_over(opts):
        disp: list[int]
        if order is None:
            disp = list(range(len(opts)))
        else:
            disp = [i for i in order if 0 <= i < len(opts)]
            if len(disp) != len(opts):
                disp = list(range(len(opts)))
        lines_llm: list[str] = []
        for pos, i in enumerate(disp):
            letter = chr(ord("A") + pos)
            lines_llm.append(_option_button_text_shuffled(opts[i], letter))
        opt_lines = "\n".join(lines_llm)
        body = stem_only.strip()
    elif opts and _options_any_line_over(opts):
        body = _question_stem_plus_answer_lines_if_long(stem_only, opts).strip()
    else:
        body = stem_only.strip()
    if _normalize_lang(lang) == "ru":
        if opt_lines:
            return (
                f"Тема (данные): {top}\n"
                f"id задачи: {tid}\n\n"
                f"Условие:\n{body}\n\n"
                f"Варианты ответа:\n{opt_lines}"
            )
        return (
            f"Тема (данные): {top}\n"
            f"id задачи: {tid}\n\n"
            f"Условие и варианты ответа:\n{body}"
        )
    if opt_lines:
        return (
            f"Topic (data): {top}\n"
            f"Task id: {tid}\n\n"
            f"Problem:\n{body}\n\n"
            f"Answer options:\n{opt_lines}"
        )
    return (
        f"Topic (data): {top}\n"
        f"Task id: {tid}\n\n"
        f"Problem and answer choices:\n{body}"
    )


def _math_all_serialize_state(session: dict) -> str:
    payload = {
        "allowed_topics": sorted(session.get("allowed_topics", set())),
        "participated_subtopics": [list(x) for x in sorted(session.get("participated_subtopics", set()))],
        "current_subtopic": list(session["current_subtopic"]) if session.get("current_subtopic") else None,
        "previous_subtopic": list(session["previous_subtopic"]) if session.get("previous_subtopic") else None,
        "shown_history": [list(x) for x in session.get("shown_history", [])],
        "attempts": [
            [k[0], k[1], v]
            for k, v in sorted(session.get("attempts", {}).items(), key=lambda kv: (kv[0][0], kv[0][1]))
        ],
        "solved_first_try": [list(x) for x in sorted(session.get("solved_first_try", set()))],
        "solved_not_first_try": [list(x) for x in sorted(session.get("solved_not_first_try", set()))],
        "first_ok_prefix_counts": session.get("first_ok_prefix_counts", {}),
    }
    return json.dumps(payload, ensure_ascii=False)


def _math_all_deserialize_state(raw: str) -> dict | None:
    try:
        p = json.loads(raw or "{}")
    except Exception:
        return None
    if not isinstance(p, dict):
        return None
    try:
        session = {
            "allowed_topics": set(p.get("allowed_topics", [])),
            "participated_subtopics": set((x[0], x[1]) for x in p.get("participated_subtopics", []) if isinstance(x, list) and len(x) == 2),
            "current_subtopic": tuple(p["current_subtopic"]) if isinstance(p.get("current_subtopic"), list) and len(p.get("current_subtopic")) == 2 else None,
            "previous_subtopic": tuple(p["previous_subtopic"]) if isinstance(p.get("previous_subtopic"), list) and len(p.get("previous_subtopic")) == 2 else None,
            "shown_history": [tuple(x) for x in p.get("shown_history", []) if isinstance(x, list) and len(x) == 2],
            "attempts": {(x[0], int(x[1])): int(x[2]) for x in p.get("attempts", []) if isinstance(x, list) and len(x) == 3},
            "solved_first_try": set((x[0], x[1]) for x in p.get("solved_first_try", []) if isinstance(x, list) and len(x) == 2),
            "solved_not_first_try": set((x[0], x[1]) for x in p.get("solved_not_first_try", []) if isinstance(x, list) and len(x) == 2),
            "first_ok_prefix_counts": dict(p.get("first_ok_prefix_counts", {})),
        }
    except Exception:
        return None
    return session


async def _math_all_save_state(user_id: int) -> None:
    if not db_conn:
        return
    session = _math_all_sessions.get(user_id)
    if not session:
        return
    try:
        await db.save_user_math_all_state(db_conn, user_id, _math_all_serialize_state(session))
    except Exception as e:
        logging.error(f"Ошибка сохранения состояния math_all для пользователя {user_id}: {e}")


async def _math_all_load_state(user_id: int) -> dict | None:
    if not db_conn:
        return None
    try:
        raw = await db.get_user_math_all_state(db_conn, user_id)
    except Exception as e:
        logging.error(f"Ошибка загрузки состояния math_all для пользователя {user_id}: {e}")
        return None
    if not raw:
        return None
    return _math_all_deserialize_state(raw)


def _math_all_all_topics() -> list[str]:
    return [t for t in topics if t not in ("physics", "chemistry")]


def _math_all_task_id(topic: str, j: int) -> str:
    q = kapibara.get(topic, [{}])[j] if topic in kapibara and 0 <= j < len(kapibara[topic]) else {}
    tid = q.get("id") if isinstance(q, dict) else ""
    return str(tid or "")


def _math_all_subtopic(topic: str, j: int) -> str:
    q = kapibara[topic][j]
    return (q.get("subtopic") or "").strip() or "general"


def _math_all_subtopic_tasks(top: str, sub: str) -> list[int]:
    return questions_by_topic_subtopic.get((top, sub), [])


def _math_all_recent_distance(session: dict, key: tuple[str, int]) -> int | None:
    hist = session.get("shown_history", [])
    for i in range(len(hist) - 1, -1, -1):
        if hist[i] == key:
            return len(hist) - 1 - i
    return None


def _math_all_solved_prefix_count(session: dict, prefix: str) -> int:
    return session.get("first_ok_prefix_counts", {}).get(prefix, 0)


def _math_all_candidate_allowed(session: dict, key: tuple[str, int]) -> bool:
    top, j = key
    q = kapibara[top][j]
    qid = str(q.get("id") or "")
    # Жёстко запрещаем повтор одной и той же задачи подряд.
    hist = session.get("shown_history", [])
    if hist and hist[-1] == key:
        return False
    if key in session.get("solved_first_try", set()):
        return False
    pref = _derive_subtopic_id(qid) or qid
    if pref and _math_all_solved_prefix_count(session, pref) >= 2:
        return False

    # Если задача уже была решена не с первого раза, и показана менее 5 задач назад,
    # пропускаем её, когда в подтеме есть альтернатива той же сложности или на 1 выше.
    if key in session.get("solved_not_first_try", set()):
        dist = _math_all_recent_distance(session, key)
        if dist is not None and dist < 5:
            sub = _math_all_subtopic(top, j)
            d = _q_diff(q)
            for jj in _math_all_subtopic_tasks(top, sub):
                alt = (top, jj)
                if alt == key:
                    continue
                dq = _q_diff(kapibara[top][jj])
                if dq in (d, d + 1):
                    if alt not in session.get("solved_first_try", set()):
                        return False
                    ap = _derive_subtopic_id(_math_all_task_id(top, jj)) or _math_all_task_id(top, jj)
                    if ap and _math_all_solved_prefix_count(session, ap) >= 2:
                        continue
                    return False
    return True


def _math_all_pick_from_candidates(cands: list[tuple[str, int]]) -> tuple[str, int] | None:
    if not cands:
        return None
    min_d = min(_q_diff(kapibara[t][j]) for t, j in cands)
    best = [(t, j) for t, j in cands if _q_diff(kapibara[t][j]) == min_d]
    return random.choice(best) if best else None


def _math_all_pick_next_subtopic(session: dict) -> tuple[str, str] | None:
    all_subs = [ts for ts in MATH_SUBTOPICS_SORTED if ts[0] in session.get("allowed_topics", set())]
    if not all_subs:
        return None
    used = session.get("participated_subtopics", set())
    unseen = [ts for ts in all_subs if ts not in used]
    pool = unseen if unseen else all_subs
    top5 = pool[:5]
    prev = session.get("previous_subtopic")
    if prev:
        same_topic = [ts for ts in top5 if ts[0] == prev[0]]
        if same_topic:
            return same_topic[0]
    return top5[0] if top5 else None


# Экзамен 25 января: собираем все задачи с type == "jan" (по полю n, отсортированные)
EXAM_25JAN_ID = "exam_25jan"
exam_questions = []  # список кортежей (n, topic, j)
EXAM_TOTAL_DIFFICULTY = 0

def _exam_points_for_n(n_val: int) -> float:
    # n: 1..20 -> 1.5, 21..40 -> 2, 41..48 -> 3.75
    if 1 <= n_val <= 20:
        return 1.5
    if 21 <= n_val <= 40:
        return 2.0
    if 41 <= n_val <= 48:
        return 3.75
    # fallback (если n отсутствует/вне диапазона)
    return 0.0


def _exam_stars_for_n(n_val: int) -> str:
    """Звёзды по номеру задачи экзамена n: 1–20 → ★, 21–40 → ★★, 41–48 → ★★★."""
    try:
        n = int(n_val or 0)
    except (TypeError, ValueError):
        return ""
    if 1 <= n <= 20:
        return "★"
    if 21 <= n <= 40:
        return "★★"
    if 41 <= n <= 48:
        return "★★★"
    return ""


def _exam_log_task_id(q: dict | None) -> str:
    """Строка id задачи для логов экзамена / Mock Exam."""
    if not isinstance(q, dict):
        return ""
    tid = q.get("id")
    if tid is None:
        return ""
    return str(tid).strip()


for top in topics:
    for j, item in enumerate(kapibara.get(top, [])):
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "").strip().lower() == "jan":
            n_val = item.get("n") or 0
            exam_questions.append((n_val, top, j))

# Сортируем по n и считаем суммарный максимум баллов экзамена
exam_questions.sort(key=lambda x: x[0])
for n_val, _, _ in exam_questions:
    EXAM_TOTAL_DIFFICULTY += _exam_points_for_n(int(n_val or 0))

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
for n_val, _, _ in exam_questions_dec:
    EXAM_DEC_TOTAL_DIFFICULTY += _exam_points_for_n(int(n_val or 0))

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
for n_val, _, _ in exam_questions_mar:
    EXAM_MAR_TOTAL_DIFFICULTY += _exam_points_for_n(int(n_val or 0))

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

# Mock Exam: для каждой задачи из экзамена jan подбираем случайную задачу с той же подтемой и сложностью.
# В пул кандидатов не попадают задачи с type=jan и type=mar.
EXAM_MOCK_ID = "exam_mock"
mock_questions = []  # список (topic, j) той же длины что exam_questions
MOCK_TOTAL_DIFFICULTY = 0

_mock_substitute_pool = {}  # (subtopic, difficulty) -> [(topic, j), ...] без jan/mar
for top in topics:
    for j, item in enumerate(kapibara.get(top, [])):
        if not isinstance(item, dict):
            continue
        sub = (item.get("subtopic") or "").strip() or "general"
        try:
            diff = int(item.get("difficulty") or 1)
        except (TypeError, ValueError):
            diff = 1
        if diff < 1:
            diff = 1
        t = (item.get("type") or "").strip().lower()
        if t in ("jan", "mar"):
            continue
        key = (sub, diff)
        if key not in _mock_substitute_pool:
            _mock_substitute_pool[key] = []
        _mock_substitute_pool[key].append((top, j))

_used_for_mock = set()  # (topic, j) — каждая задача в mock_questions только один раз
for n_val, t_top, t_j in exam_questions:
    q = kapibara[t_top][t_j]
    sub = (q.get("subtopic") or "").strip() or "general"
    try:
        diff = int(q.get("difficulty") or 1)
    except (TypeError, ValueError):
        diff = 1
    if diff < 1:
        diff = 1
    key = (sub, diff)
    pool = _mock_substitute_pool.get(key) or []
    available = [p for p in pool if p not in _used_for_mock]
    if available:
        chosen = random.choice(available)
        _used_for_mock.add(chosen)
        mock_questions.append(chosen)
    else:
        mock_questions.append((t_top, t_j))
    # Баллы в Mock Exam — по номеру n (как в экзамене), а не по difficulty
    MOCK_TOTAL_DIFFICULTY += _exam_points_for_n(int(n_val or 0))

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

# Кэш языков для колонок ui_lang / exam_lang в log() (обновляется при чтении из БД и при смене)
_log_user_langs: dict[int, tuple[str, str]] = {}


def log(usr, lg=None):
   if lg is None:
       lg = []
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
                 src = ''
   except Exception:
       src = ""
   ui_l, ex_l = _log_user_langs.get(id, ("", ""))
   l = [dt, id, username, src, ui_l, ex_l] + list(lg)
   # Сохраняем в файл для обратной совместимости
   res_file = os.path.join(DATA_DIR, "res.txt")
   try:
       with open(res_file, "a", encoding='utf-8') as f:
           f.write("\t".join([str(x) for x in l])+"\n")
   except:
       pass  # Если файл недоступен, продолжаем работу


def _normalize_lang(lang: str) -> str:
   v = (lang or "").lower()
   return "ru" if v.startswith("ru") else "en"


def _sync_log_lang_ui(user_id: int, ui: str) -> None:
   ui_n = _normalize_lang(ui)
   exam = ""
   if user_id in _log_user_langs:
       _, exam = _log_user_langs[user_id]
   _log_user_langs[user_id] = (ui_n, exam)


def _sync_log_lang_exam(user_id: int, exam: str | None) -> None:
   ui = "en"
   if user_id in _log_user_langs:
       ui, _ = _log_user_langs[user_id]
   ex = exam if exam in ("en", "zh") else ""
   _log_user_langs[user_id] = (ui, ex)


async def _refresh_log_lang_cache(user) -> None:
   """Подгрузить язык интерфейса и экзамена из БД перед log(), если хендлер ещё не вызывал геттеры."""
   await _get_user_lang(user)
   await _get_user_exam_lang_by_id(user.id)


def _txt(lang: str, ru_text: str, en_text: str) -> str:
   return ru_text if _normalize_lang(lang) == "ru" else en_text


def _wrong_answer_training_extra_message(lang: str, wrong_today: int, total_today: int) -> str:
    """
    Текст после «Нет, это не так» в тренировке по теме/подтеме: ошибки за сегодня (с полуночи),
    при err_pct > 40 — напоминание про теорию; лимит ошибок за день — если wrong_today > 10.
    """
    if total_today <= 0:
        return ""
    err_pct = 100.0 * wrong_today / total_today
    if _normalize_lang(lang) == "ru":
        block = f"Ошибок сегодня: {wrong_today} из {total_today} решений."
        if err_pct > 40:
            block += "\nНеобходимо сначала выучить теорию."
        if wrong_today > 10:
            block += (
                "\n\nОграничение по ошибкам сегодня: не более 30. "
                f"\n{_wrong_answer_limit_phrase(lang)}"
            )
    else:
        block = f"Mistakes today: {wrong_today} out of {total_today} answers."
        if err_pct > 40:
            block += "\nYou need to learn the theory first."
        if wrong_today > 10:
            block += (
                "\n\nToday's mistake limit: no more than 30. "
                f"\n{_wrong_answer_limit_phrase(lang)}"
            )
    return block + "\n\n"


def _wrong_answer_limit_phrase(lang: str) -> str:
    if _normalize_lang(lang) == "ru":
        return random.choice(
            [
                "Не перебирай ответы.",
                "Решай задачи внимательнее.",
                "Вникай в суть задачи.",
                "Анализируй условия тщательнее.",
                "Решай последовательно и логически.",
                "Думай над каждым ответом.",
            ]
        )
    return random.choice(
        [
            "Don't guess answers.",
            "Solve more carefully.",
            "Focus on the actual problem.",
            "Analyze the conditions more thoroughly.",
            "Solve step by step and logically.",
            "Think through each option.",
        ]
    )


async def _wrong_answer_training_message(user_id: int, lang: str) -> str:
    """
    Единый текст неверного ответа для тренировочных режимов:
    базовая фраза + статистика за сегодня.
    """
    extra = ""
    if db_conn:
        try:
            w_t, t_t = await db.get_training_answers_today_counts(db_conn, user_id)
            extra = _wrong_answer_training_extra_message(lang, w_t, t_t)
        except Exception as e:
            logging.error(f"Ошибка статистики ответов за сегодня: {e}")
    return _txt(lang, "Нет, это не так 😢", "Sorry, you are wrong 😢") + "\n" + extra


def _capitalize_display_en(s: str) -> str:
    """Отображение темы/подтемы на английском (как раньше: первая буква заглавная)."""
    if not s:
        return ""
    return s[0].upper() + s[1:]


# Русские названия тем (ключи — как в data.txt / kapibara)
TOPIC_TITLE_RU: dict[str, str] = {
    "sets": "Множества",
    "inequalities": "Неравенства",
    "functions": "Функции",
    "trigonometry (simple)": "Тригонометрия (базовый уровень)",
    "trigonometry": "Тригонометрия",
    "geometry": "Геометрия",
    "conic curves": "Конические сечения",
    "logarithmic functions": "Логарифмические функции",
    "arithmetic and geometric mean": "Среднее арифметическое и геометрическое",
    "Algebraic and geometric mean": "Среднее арифметическое и геометрическое",
    "sequences": "Последовательности",
    "complex numbers": "Комплексные числа",
    "probability": "Вероятность",
    "physics": "Физика",
}

_AGM_SUBTOPICS_RU = {
    "arithmetic mean": "Среднее арифметическое",
    "geometric mean": "Среднее геометрическое",
    "general": "Общее",
}

_PHYSICS_SUBTOPICS_RU: dict[str, str] = {
    "Coulomb's law": "Закон Кулона",
    "Hooke's law": "Закон Гука",
    "Newton's second law": "Второй закон Ньютона",
    "average velocity": "Средняя скорость",
    "charge sharing and Coulomb's law": "Деление заряда и закон Кулона",
    "circular motion": "Движение по окружности",
    "conservation of momentum": "Сохранение импульса",
    "current division": "Деление тока",
    "distance vs displacement": "Путь и перемещение",
    "electric field": "Электрическое поле",
    "electric field strength": "Напряжённость электрического поля",
    "electric field superposition": "Суперпозиция электрического поля",
    "electric field symmetry": "Симметрия электрического поля",
    "electric force": "Электрическая сила",
    "electric potential difference": "Разность потенциалов",
    "electromagnetic induction": "Электромагнитная индукция",
    "force resultant": "Равнодействующая сила",
    "free fall": "Свободное падение",
    "friction": "Трение",
    "gravitational potential energy": "Потенциальная энергия в поле тяготения",
    "gravity": "Гравитация",
    "ideal gas law": "Уравнение состояния идеального газа",
    "impulse": "Импульс силы",
    "incline motion": "Движение по наклонной плоскости",
    "isobaric process": "Изобарный процесс",
    "kinematics": "Кинематика",
    "kinetic energy from force-time graph": "Кинетическая энергия по графику силы",
    "magnetic force on current": "Магнитная сила на ток",
    "magnetic force on wire": "Магнитная сила на проводник",
    "momentum": "Импульс",
    "motion graphs": "Графики движения",
    "projectile motion": "Движение тела, брошенного под углом",
    "projectile motion with friction": "Движение с трением",
    "reflection": "Отражение",
    "refraction": "Преломление",
    "resistors in parallel": "Резисторы параллельно",
    "resultant force and acceleration": "Равнодействующая и ускорение",
    "rotational motion": "Вращательное движение",
    "simple harmonic motion": "Гармонические колебания",
    "units": "Единицы измерения",
    "vectors and scalars": "Векторы и скаляры",
    "waves": "Волны",
    "work": "Работа",
    "work-energy in penetration": "Работа и энергия при проникновении",
    "work-energy theorem": "Теорема о кинетической энергии",
    "work-energy with friction and electric force": "Работа и энергия (трение и электросила)",
    "general": "Общее",
}

# Русские названия подтем: topic -> subtopic (как в данных) -> строка
SUBTOPIC_TITLE_RU: dict[str, dict[str, str]] = {
    "sets": {
        "set operations": "Операции над множествами",
        "numerical sets": "Числовые множества",
        "general": "Общее",
    },
    "inequalities": {
        "properties of inequalities": "Свойства неравенств",
        "absolute value": "Модуль",
        "real numbers": "Вещественные числа",
        "rational inequalities": "Рациональные неравенства",
        "quadratic inequalities": "Квадратичные неравенства",
        "general": "Общее",
    },
    "functions": {
        "Function domain": "Область определения",
        "functions properties": "Свойства функций",
        "graphs": "Графики",
        "inverse functions": "Обратные функции",
        "inequalities": "Неравенства",
        "function equality": "Равенство функций",
        "identical functions": "Тождественные функции",
        "general": "Общее",
    },
    "geometry": {
        "coordinate geometry": "Координатная геометрия",
        "distance formula": "Формула расстояния",
        "analytic geometry": "Аналитическая геометрия",
        "lines": "Прямые",
        "vectors": "Векторы",
        "general": "Общее",
    },
    "conic curves": {
        "circle": "Окружность",
        "parabola": "Парабола",
        "ellipse": "Эллипс",
        "hyperbola": "Гипербола",
        "general": "Общее",
    },
    "logarithmic functions": {"logarithms": "Логарифмы", "general": "Общее"},
    "probability": {"Simple Probability": "Элементарная вероятность", "general": "Общее"},
    "sequences": {
        "simple tasks": "Простые задачи",
        "hard tasks": "Сложные задачи",
        "general": "Общее",
    },
    "complex numbers": {
        "simple tasks": "Простые задачи",
        "hard tasks": "Сложные задачи",
        "complex numbers": "Комплексные числа",
        "general": "Общее",
    },
    "trigonometry": {
        "trigonometric values": "Табличные значения",
        "terminal side through point": "Сторона угла через точку",
        "trigonometric identities": "Тригонометрические тождества",
        "properties": "Свойство тригонометрических функций",
        "Properties of trigonometric functions": "Свойство тригонометрических функций",
        "double angle formula": "Формулы двойного угла",
        "trigonometric expressions": "Тригонометрические выражения",
        "half-angle formula": "Формулы половинного угла",
        "sin and cos of sum": "Синус и косинус суммы",
        "general": "Общее",
    },
    "arithmetic and geometric mean": _AGM_SUBTOPICS_RU,
    "Algebraic and geometric mean": _AGM_SUBTOPICS_RU,
    "physics": _PHYSICS_SUBTOPICS_RU,
}


def _topic_display(topic: str, lang: str) -> str:
    """Название темы для интерфейса с учётом языка."""
    if _normalize_lang(lang) == "ru":
        ru = TOPIC_TITLE_RU.get(topic)
        if ru:
            return ru
    return _capitalize_display_en(topic)


def _subtopic_display(topic: str, sub: str, lang: str) -> str:
    """Название подтемы для интерфейса с учётом языка."""
    sub_key = (sub or "").strip() or "general"
    if _normalize_lang(lang) == "ru":
        if sub_key == "general":
            return "Общее"
        inner = SUBTOPIC_TITLE_RU.get(topic, {})
        ru = inner.get(sub_key)
        if ru:
            return ru
        sub_l = sub_key.lower()
        for k, v in inner.items():
            if k.lower() == sub_l:
                return v
        if topic == "physics":
            ru_ph = _PHYSICS_SUBTOPICS_RU.get(sub_key)
            if ru_ph:
                return ru_ph
        return _capitalize_display_en(sub_key)
    # Английский: точечные переопределения для некоторых подтем.
    if topic == "trigonometry" and sub_key.lower() in {"properties", "properties of trigonometric functions"}:
        return "Properties of trigonometric functions"
    if sub_key == "general":
        return "General"
    return _capitalize_display_en(sub_key)


CORRECT_PHRASES_RU = [
   "Правильно!",
   "Верно!",
   "Отлично!",
   "Супер!",
   "Так держать!",
]

CORRECT_PHRASES_EN = [
   "Great!",
   "Correct!",
   "Excellent!",
   "Nice job!",
   "Well done!",
]

# Случайная реакция после верного ответа в режиме тренировки (тема / подтема)
TRAINING_CORRECT_REACTION_EMOJIS = [
    "❤️",
    "💥",
    "💐",
    "😀",
    "🌷",
    "🔥",
    "🐠",
    "😍",
    "🐈",
    "🥰",
    "👍",
    "🫶",
    "🌺",
    "⭐️",
    "🌟",
    "🎖",
    "🏅",
    "🏆",
    "💘",
    "🩷",
    "🦊",
    "🦄",
    "🍹",
    "🍾",
    "🍭",
    "🚀",
    "⛩",
    "❣️",
    "💕",
    "💖",
    "🚘",
    "🏎",
    "🛰",
]


def _correct_phrase(lang: str) -> str:
   if _normalize_lang(lang) == "ru":
       return random.choice(CORRECT_PHRASES_RU)
   return random.choice(CORRECT_PHRASES_EN)


def _correct_phrase_training(lang: str) -> str:
    """Фраза о верном ответе + случайный эмодзи (только тренировка по теме/подтеме)."""
    return f"{_correct_phrase(lang)} {random.choice(TRAINING_CORRECT_REACTION_EMOJIS)}"


async def _get_user_lang(user) -> str:
   """
   Приоритет языка:
   1) users.language (настройка пользователя),
   2) user.language_code из Telegram,
   3) en.
   """
   if db_conn:
       try:
           saved = await db.get_user_language(db_conn, user.id)
           if saved:
               lang = _normalize_lang(saved)
               _sync_log_lang_ui(user.id, lang)
               return lang
       except Exception as e:
           logging.error(f"Ошибка чтения языка пользователя {user.id}: {e}")
   lang = _normalize_lang(getattr(user, "language_code", "") or "en")
   _sync_log_lang_ui(user.id, lang)
   return lang


async def _get_user_lang_by_id(user_id: int) -> str:
   if db_conn:
       try:
           saved = await db.get_user_language(db_conn, user_id)
           if saved:
               lang = _normalize_lang(saved)
               _sync_log_lang_ui(user_id, lang)
               return lang
       except Exception as e:
           logging.error(f"Ошибка чтения языка пользователя {user_id}: {e}")
   _sync_log_lang_ui(user_id, "en")
   return "en"


async def _get_user_exam_lang_by_id(user_id: int) -> str | None:
   """
   Язык экзамена из users.exam_language:
   - 'en' -> показываем english
   - 'zh' -> показываем chinese
   - None -> показываем обе строки
   """
   if db_conn:
       try:
           saved = await db.get_user_exam_language(db_conn, user_id)
           if saved in ("en", "zh"):
               _sync_log_lang_exam(user_id, saved)
               return saved
       except Exception as e:
           logging.error(f"Ошибка чтения языка экзамена пользователя {user_id}: {e}")
   _sync_log_lang_exam(user_id, None)
   return None


def _task_text_for_exam_lang(q: dict, exam_lang: str | None) -> str:
   en = (q.get("english") or "").strip()
   zh = (q.get("chinese") or "").strip()
   if exam_lang == "en":
       stem = en or zh
   elif exam_lang == "zh":
       stem = zh or en
   else:
       if en and zh:
           stem = en + "\n" + zh
       else:
           stem = en or zh
   return stem


def _options_for_exam_lang(q: dict, exam_lang: str | None) -> list[str]:
    """
    Варианты для показа: при установленном языке экзамена — options_en / options_ch,
    если список есть и совпадает по длине с options; иначе как раньше — options.
    Индексы вариантов совпадают с полем options (callback_data не меняется).
    """
    base = q.get("options")
    if not isinstance(base, list) or not base:
        return []
    if exam_lang not in ("en", "zh"):
        return list(base)
    key = "options_en" if exam_lang == "en" else "options_ch"
    alt = q.get(key)
    if isinstance(alt, list) and len(alt) == len(base) and alt:
        return list(alt)
    return list(base)


def _task_options_for_display(q: dict, exam_lang: str | None) -> list[str]:
    """Варианты для UI с учётом языка экзамена (как для кнопок)."""
    return _options_for_exam_lang(q, exam_lang) or (q.get("options") or [])


_LONG_OPTION_INLINE_THRESHOLD = 45


def _options_any_line_over(opts: list[str], limit: int = _LONG_OPTION_INLINE_THRESHOLD) -> bool:
    return any(isinstance(x, str) and len(x) > limit for x in opts)


def _question_stem_plus_answer_lines_if_long(stem: str, opts_display: list[str]) -> str:
    """
    Если хотя бы один вариант длиннее limit — добавить все варианты отдельными строками A. … B. …
    """
    if not opts_display or not _options_any_line_over(opts_display):
        return stem
    
    def _strip_leading_option_letter_prefix(raw: str) -> str:
        """
        Убирает начальные префиксы вроде `A.` / `A)` / `B.` / ... (и повторы),
        чтобы не было дубля `A. A. Iron...` (мы добавляем `A.` снаружи).
        """
        t = (raw or "").lstrip()
        while True:
            m = re.match(r"^[A-Da-d]\s*[\.\)]\s*", t)
            if not m:
                break
            t = t[m.end():].lstrip()
        return t

    lines: list[str] = []
    if (stem or "").strip():
        lines.append(stem.rstrip())
    for i, s in enumerate(opts_display):
        letter = chr(ord("A") + i)
        t = _strip_leading_option_letter_prefix((s or "").strip())
        lines.append(f"{letter}. {t}" if t else f"{letter}.")
    return "\n".join(lines) if lines else stem


def _full_task_question_text(q: dict, exam_lang: str | None) -> str:
    """Текст задачи для сообщения: условие + при длинных вариантах — все варианты в теле."""
    stem = _task_text_for_exam_lang(q, exam_lang)
    opts = _task_options_for_display(q, exam_lang)
    return _question_stem_plus_answer_lines_if_long(stem, opts)


def _keyboard_option_labels_from_display(opts_display: list[str]) -> list[str]:
    """Подписи кнопок: полный текст или только A. B. C. при длинных вариантах."""
    if not opts_display:
        return []
    if _options_any_line_over(opts_display):
        return [f"{chr(ord('A') + i)}." for i in range(len(opts_display))]
    return list(opts_display)


def _language_switch_kb(current_lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if _normalize_lang(current_lang) == "ru":
        builder.row(InlineKeyboardButton(text="Switch to English", callback_data="set_lang_en"))
    else:
        builder.row(InlineKeyboardButton(text="Сменить язык на русский", callback_data="set_lang_ru"))
    return builder.as_markup()


def _exam_language_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="English", callback_data="set_exam_lang_en"),
        InlineKeyboardButton(text="中文", callback_data="set_exam_lang_zh"),
    )
    builder.adjust(2)
    return builder.as_markup()


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
async def makeinvite(usr) : 
       id = usr.id
       username = str(usr.username)
       inv = username[:3]+str(id)[:3]
       lang = await _get_user_lang(usr)
       link = "https://t.me/csca_mathbot?start=invite"+inv
       if lang.startswith("ru") :
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
    lang = await _get_user_lang_by_id(user_id) if user_id else "en"
    
    # Добавляем кнопку "Продолжить", если есть прогресс
    if user_id and db_conn:
        try:
            progress_list = await db.get_progress(db_conn, user_id)
            for prog in progress_list:
                topic = prog['topic']
                last_idx = prog['last_question_index']
                # Проверяем, что есть ещё вопросы
                if topic in kapibara and last_idx < len(kapibara[topic]):
                    td = _topic_display(topic, lang)
                    builder.add(
                        InlineKeyboardButton(
                            text=(
                                f"▶️ Продолжить {td} ({last_idx}/{len(kapibara[topic])})"
                                if lang == "ru"
                                else f"▶️ Continue {td} ({last_idx}/{len(kapibara[topic])})"
                            ),
                            callback_data=f'next_{topic}_{last_idx}'
                        )
                    )
        except Exception as e:
            logging.error(f"Ошибка получения прогресса для start_kb: {e}")
            pass  # Если ошибка при получении прогресса, просто показываем обычное меню

    builder.add(
        InlineKeyboardButton(
            text="🧮 Математика - задачи по темам" if lang == "ru" else "🧮 Math - tasks by topic",
            callback_data="menu_topics",
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="📚 Математика автоматический выбор задач" if lang == "ru" else "📚 Smart math tasks",
            callback_data="menu_all_math",
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="⚛️ Физика" if lang == "ru" else "⚛️ Physics",
            callback_data="menu_physics",
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🧪 Химия" if lang == "ru" else "🧪 Chemistry",
            callback_data="menu_chemistry",
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="📝 Пройти экзамен" if lang == "ru" else "📝 Take exam",
            callback_data="menu_exams",
        )
    )
    exam_lang = "en"
    if user_id and db_conn:
        try:
            saved_exam_lang = await db.get_user_exam_language(db_conn, user_id)
            if saved_exam_lang in ("en", "zh"):
                exam_lang = saved_exam_lang
        except Exception as e:
            logging.error(f"Ошибка чтения языка экзамена пользователя {user_id}: {e}")

    builder.add(
        InlineKeyboardButton(
            text=(
                f"🈯 Изменить язык экзамена ({'English' if exam_lang == 'en' else '中文'})"
                if lang == "ru"
                else f"🈯 Change exam language ({'English' if exam_lang == 'en' else '中文'})"
            ),
            callback_data="menu_exam_language",
        )
    )
    builder.add(
        InlineKeyboardButton(
            text="🌐 Switch to English" if lang == "ru" else "🌐 Сменить язык на русский",
            callback_data="set_lang_en" if lang == "ru" else "set_lang_ru",
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def topics_menu_kb(lang: str = "en") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, top in enumerate(topics):
        if top in ("physics", "chemistry"):
            continue
        builder.add(
            InlineKeyboardButton(
                text=_topic_display(top, lang),
                callback_data=f't_{idx}'
            )
        )
    builder.add(
        InlineKeyboardButton(
            text="◀️ Назад" if lang == "ru" else "◀️ Back",
            callback_data="back_start",
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def exams_menu_kb(lang: str = "en") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if exam_questions:
        builder.add(
            InlineKeyboardButton(
                text="📝 Экзамен 25 янв" if lang == "ru" else "📝 Exam Jan 25",
                callback_data="exam_start_jan",
            )
        )
    if exam_questions_dec:
        builder.add(
            InlineKeyboardButton(
                text="📝 Экзамен 21 дек" if lang == "ru" else "📝 Exam Dec 21",
                callback_data="exam_start_dec",
            )
        )
    if exam_questions_mar:
        builder.add(
            InlineKeyboardButton(
                text="📝 Экзамен 15 марта" if lang == "ru" else "📝 Exam Mar 15",
                callback_data="exam_start_mar",
            )
        )
    if mock_questions:
        builder.add(
            InlineKeyboardButton(
                text="📝 Пробный экзамен" if lang == "ru" else "📝 Mock Exam",
                callback_data="exam_mock_start",
            )
        )
    builder.add(
        InlineKeyboardButton(
            text="◀️ Назад" if lang == "ru" else "◀️ Back",
            callback_data="back_start",
        )
    )
    builder.adjust(1)
    return builder.as_markup()



def subtopic_kb(topic_idx: int, lang: str = "en") -> InlineKeyboardMarkup:
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
            text=_txt(lang, "📋 Все задачи", "📋 All questions"),
            callback_data=f'next_{topic}_0'
        )
    )
    # Список подтем показываем только если хотя бы в одной подтеме >= 3 задач
    sub_counts = [len(questions_by_topic_subtopic.get((topic, sub_name), [])) for sub_name in subs]
    show_subtopics = sub_counts and max(sub_counts) >= 3
    if show_subtopics:
        for sub_idx, sub_name in enumerate(subs):
            count = sub_counts[sub_idx]
            label = _subtopic_display(topic, sub_name or "general", lang)
            builder.add(
                InlineKeyboardButton(
                    text=f"{label} ({count})",
                    callback_data=f's_{topic_idx}_{sub_idx}'
                )
            )
    builder.add(
        InlineKeyboardButton(
            text=_txt(lang, "◀️ Назад", "◀️ Back"),
            callback_data="back_start"
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def _shuffled_option_indices(n: int) -> list[int]:
    """Случайный порядок отображения вариантов; в callback остаётся исходный индекс (0=A, 1=B, …)."""
    order = list(range(n))
    random.shuffle(order)
    return order


def _option_display_indices(
    topic: str,
    q: dict,
    n: int,
    *,
    opts_for_shuffle_check: list[str] | None = None,
) -> list[int]:
    """
    Порядок отображения вариантов ответа.
    Для вариантов, показываемых в тексте (любой >45 симв. в списке для языка экзамена), не перемешиваем.
    """
    opts = (
        opts_for_shuffle_check
        if opts_for_shuffle_check is not None
        else (q.get("options") or [])
    )
    if _options_any_line_over(opts):
        return list(range(n))
    return _shuffled_option_indices(n)


def _option_button_text_shuffled(raw: str, display_letter: str) -> str:
    """
    Текст кнопки после перемешивания: убираем ведущую метку A./B./… из данных,
    показываем display_letter (A, B, C …) по порядку сверху вниз.
    """
    s = (raw or "").strip()
    m = re.match(r"^([A-Za-z])[\.\)]\s*(.*)$", s, re.DOTALL)
    body = m.group(2).strip() if m else s
    if body:
        out = f"{display_letter}. {body}"
    else:
        out = f"{display_letter}."
    return out.replace(". ", ".     ")


def inline_kb(
    top,
    j: int,
    showvideo=1,
    lang_code: str = "en",
    *,
    option_order: list[int] | None = None,
    exam_lang: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    q = kapibara[top][j]
    opts_src = _task_options_for_display(q, exam_lang)
    k = _keyboard_option_labels_from_display(opts_src)
    order = (
        option_order
        if option_order is not None
        else _option_display_indices(
            top, q, len(k), opts_for_shuffle_check=opts_src
        )
    )
    # Добавляем кнопки вопросов (порядок на экране случайный; буквы A,B,… заново сверху вниз)
    for pos, i in enumerate(order):
        letter = chr(ord("A") + pos)
        builder.add(
            InlineKeyboardButton(
                text=_option_button_text_shuffled(k[i], letter),
                callback_data=f'qst_{top}_{j}_{i}'
            )
        )
  #  builder.row(
  #      InlineKeyboardButton(
  #          text='Получить подсказку',
  #          callback_data='explain'
  #      )
  #  )
    if _has_hint_image(q, lang_code):
        hint_text = "Показать подсказку" if _lang_suffix(lang_code) == "ru" else "Show hint"
        builder.add(
            InlineKeyboardButton(
                text=hint_text,
                callback_data=f"hint_{top}_{j}",
            )
        )

    # Настраиваем размер клавиатуры
    builder.adjust(1)
    return builder.as_markup()

def inline_kb_next(top: str, j: int, lang: str, user_id: int):
    """Следующий шаг по теме: линейный проход или продолжение блока повторов (callback j == n)."""
    n = len(kapibara[top])
    key = (user_id, top)
    in_review = _topic_in_review.get(key, False)
    review = _topic_build_review_set(user_id, top, n)
    if in_review and review:
        next_j = n
    elif j + 1 < n:
        next_j = j + 1
    else:
        next_j = n
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_txt(lang, "Следующий вопрос", "Next task"),
            callback_data=f"next_{top}_{next_j}",
        )
    )
    builder.adjust(1)
    return builder.as_markup()


async def _deliver_topic_question_to_chat(chat_id: int, from_user, top: str, j: int, showvideo: int) -> None:
    """Отправить задачу темы j в чат; учёт показа для логики «первый ответ после показа»."""
    uid = from_user.id
    linear = _topic_linear_active.get((uid, top), False)
    _register_topic_question_displayed(uid, top, j, linear)
    k = kapibara[top][j]
    lang_code = await _get_user_lang(from_user)
    if "img" in k:
        photo_path = os.path.join(DATA_DIR, "images", k["img"])
        await bot.send_photo(chat_id, photo=types.FSInputFile(photo_path))
    exam_lang = await _get_user_exam_lang_by_id(uid)
    question_text = _full_task_question_text(k, exam_lang)
    opts_n = _task_options_for_display(k, exam_lang)
    order = _option_display_indices(
        top, k, len(opts_n), opts_for_shuffle_check=opts_n
    )
    await bot.send_message(
        chat_id,
        question_text,
        reply_markup=inline_kb(
            top,
            j,
            showvideo,
            lang_code=lang_code,
            option_order=order,
            exam_lang=exam_lang,
        ),
    )
    _record_last_seen_question(uid, top, j, order)


async def _deliver_topic_question_message(call: CallbackQuery, top: str, j: int, showvideo: int) -> None:
    """Отправить задачу темы j и учесть показ для логики «первый ответ после показа»."""
    await _deliver_topic_question_to_chat(call.message.chat.id, call.from_user, top, j, showvideo)


async def _start_topic_training_from_message(message: Message, top: str, log_prefix: str) -> None:
    """Вход в тренировку по теме (физика/химия) из текстового сообщения — та же логика, что menu_physics / menu_chemistry."""
    lang = await _get_user_lang(message.from_user)
    if top not in kapibara or not kapibara[top]:
        kb = await start_kb(message.from_user.id)
        if top == "physics":
            await message.answer(_txt(lang, "Задач по физике пока нет.", "No physics tasks yet."), reply_markup=kb)
        else:
            await message.answer(_txt(lang, "Задач по химии пока нет.", "No chemistry tasks yet."), reply_markup=kb)
        return

    showvideo = 1
    if message.from_user.username == "evangecalista":
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (message.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == "stepik":
                showvideo = 0
        except Exception:
            pass
    elif str(message.from_user.id) in stepik:
        showvideo = 0

    uid = message.from_user.id
    _topic_clear_session(uid, top)
    _topic_linear_active[(uid, top)] = True
    await _get_user_exam_lang_by_id(message.from_user.id)
    log(message.from_user, [log_prefix, "next", top, 0])
    async with ChatActionSender(bot=bot, chat_id=uid, action="typing"):
        await _deliver_topic_question_to_chat(message.chat.id, message.from_user, top, 0, showvideo)


async def _deliver_subtopic_question_message(
    call: CallbackQuery,
    topic_idx: int,
    sub_idx: int,
    k: int,
    showvideo: int,
) -> None:
    """Показать k-й вопрос подтемы (k — индекс в j_list)."""
    uid = call.from_user.id
    topic = topics[topic_idx]
    subs = subtopics_by_topic.get(topic, [])
    sub_name = subs[sub_idx] if sub_idx < len(subs) else None
    j_list = questions_by_topic_subtopic.get((topic, sub_name), []) if sub_name else []
    if k < 0 or k >= len(j_list):
        return
    linear = _sub_linear_active.get(_sub_key(uid, topic_idx, sub_idx), False)
    _register_sub_question_displayed(uid, topic_idx, sub_idx, k, linear)
    j = j_list[k]
    q = kapibara[topic][j]
    if q.get("img"):
        photo_path = os.path.join(DATA_DIR, "images", q["img"])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
    lang_code = await _get_user_lang(call.from_user)
    # В режиме тренировки по подтеме номер задачи (Вопрос N из M) не показываем
    exam_lang = await _get_user_exam_lang_by_id(uid)
    question_text = _full_task_question_text(q, exam_lang)
    opts_n = _task_options_for_display(q, exam_lang)
    order = _option_display_indices(
        topic, q, len(opts_n), opts_for_shuffle_check=opts_n
    )
    await call.message.answer(
        question_text,
        reply_markup=inline_kb_sub(
            topic_idx,
            sub_idx,
            k,
            showvideo,
            lang_code=lang_code,
            option_order=order,
            exam_lang=exam_lang,
        ),
    )
    _record_last_seen_question(uid, topic, j, order)


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
                       # Находим задачу и начисляем баллы по n
                       if 0 <= idx < len(exam_questions):
                           n_val, top, j = exam_questions[idx]
                           correct_difficulty += _exam_points_for_n(int(n_val or 0))
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


def inline_kb_exam(
    idx: int, *, option_order: list[int] | None = None, exam_lang: str | None = None
) -> InlineKeyboardMarkup:
   """Клавиатура вариантов ответа для режима экзамена 25 января (exam_type=jan)."""
   return inline_kb_exam_by_type(
       idx, "jan", option_order=option_order, exam_lang=exam_lang
   )


async def _send_exam_question(call: CallbackQuery, user_id: int, idx: int):
   """Отправляет пользователю вопрос экзамена 25 января с номером idx."""
   if idx < 0 or idx >= len(exam_questions):
       kb = await start_kb(user_id)
       await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
       return
   n_val, top, j = exam_questions[idx]
   q = kapibara[top][j]
   lang = await _get_user_lang(call.from_user)
   if q.get("img"):
       photo_path = os.path.join(DATA_DIR, "images", q["img"])
       await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
   total = len(exam_questions)
   header = _txt(
       lang,
       f"Экзамен 25 января — вопрос {idx + 1} из {total}\n\n",
       f"January 25 exam — question {idx + 1} of {total}\n\n",
   )
   stars = _exam_stars_for_n(n_val)
   diff_line = (
       _txt(lang, f"Сложность: {stars}\n", f"Difficulty: {stars}\n") if stars else ""
   )
   exam_lang = await _get_user_exam_lang_by_id(user_id)
   question_text = header + diff_line + _full_task_question_text(q, exam_lang)
   opts_n = _task_options_for_display(q, exam_lang)
   order = _option_display_indices(
       top, q, len(opts_n), opts_for_shuffle_check=opts_n
   )
   reply = inline_kb_exam(idx, option_order=order, exam_lang=exam_lang)
   log(call.from_user, [EXAM_25JAN_ID, "question", idx, _exam_log_task_id(q)])
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(question_text, reply_markup=reply)


async def _exam_wrong_by_topic(user_id: int, exam_type: str) -> dict:
   """
   Собирает по ответам в БД: тема -> число задач с ошибкой.
   exam_type:
     - 'jan' — экзамен 25 января
     - 'dec' — экзамен 21 декабря
     - 'mar' — экзамен 15 марта
     - 'mock' — пробный экзамен
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
       elif exam_type == "mock":
           exam_id = EXAM_MOCK_ID
           # mock_questions: список (topic, j), где question_index == idx
           questions = mock_questions
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
       if exam_type == "mock":
           top, _j = questions[idx]
       else:
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
                        n_val, _, _ = questions[idx]
                        state["correct_difficulty"] += _exam_points_for_n(int(n_val or 0))
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


def inline_kb_exam_by_type(
    idx: int,
    exam_type: str,
    *,
    option_order: list[int] | None = None,
    exam_lang: str | None = None,
) -> InlineKeyboardMarkup:
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
    opts_src = _task_options_for_display(q, exam_lang)
    k = _keyboard_option_labels_from_display(opts_src)
    order = (
        option_order
        if option_order is not None
        else _option_display_indices(
            top, q, len(k), opts_for_shuffle_check=opts_src
        )
    )
    for pos, i in enumerate(order):
        letter = chr(ord("A") + pos)
        builder.add(
            InlineKeyboardButton(
                text=_option_button_text_shuffled(k[i], letter),
                callback_data=f'exam_q_{exam_type}_{idx}_{i}',
            )
        )
    builder.adjust(1)
    return builder.as_markup()


async def _send_exam_question_by_type(call: CallbackQuery, user_id: int, idx: int, exam_type: str):
    # Блокируем показ следующего экзаменационного вопроса для пользователя 7567696330
    if user_id == 7567696331:
        log(call.from_user, [exam_type, "next_blocked"])
        return

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
    n_val, top, j = questions[idx]
    arr = kapibara.get(top, [])
    if j >= len(arr) or not isinstance(arr[j], dict):
        kb = await start_kb(user_id)
        await call.message.answer("Вопрос не найден.", reply_markup=kb)
        return
    q = arr[j]
    lang = await _get_user_lang(call.from_user)
    if q.get("img"):
        photo_path = os.path.join(DATA_DIR, "images", q["img"])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
    total = len(questions)
    h_ru, h_en = cfg["header_ru"], cfg["header_en"]
    header = _txt(lang, f"{h_ru} — вопрос {idx + 1} из {total}\n\n", f"{h_en} — question {idx + 1} of {total}\n\n")
    stars = _exam_stars_for_n(n_val)
    diff_line = (
        _txt(lang, f"Сложность: {stars}\n", f"Difficulty: {stars}\n") if stars else ""
    )
    exam_lang = await _get_user_exam_lang_by_id(user_id)
    question_text = header + diff_line + _full_task_question_text(q, exam_lang)
    log(call.from_user, [cfg["id"], "question", idx, _exam_log_task_id(q)])
    opts = _task_options_for_display(q, exam_lang)
    order = _option_display_indices(
        top, q, len(opts), opts_for_shuffle_check=opts
    )
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(
            question_text,
            reply_markup=inline_kb_exam_by_type(
                idx, exam_type, option_order=order, exam_lang=exam_lang
            ),
        )
    _record_last_seen_question(user_id, top, j, order)


async def _send_exam_summary_by_type(call: CallbackQuery, user_id: int, exam_type: str):
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return
    state = await _get_exam_state_by_type(user_id, exam_type)
    questions = cfg["questions"]
    total_q = len(questions)
    total_d = cfg["total_difficulty"]
    k = (state.get("correct_difficulty", 0) / total_d * 100) if total_d else 0.0
    lang = await _get_user_lang(call.from_user)
    kb = _inline_kb_exam_finished_by_type(exam_type, lang)
    wrong_by_topic = await _exam_wrong_by_topic(user_id, exam_type)
    sorted_wrong = sorted(wrong_by_topic.items(), key=lambda kv: (-kv[1], kv[0]))
    problematic_topics = [topic_name for topic_name, _ in sorted_wrong]

    if db_conn:
        try:
            await db.save_user_exam_recommendations(
                db_conn,
                user_id=user_id,
                exam_type=exam_type,
                topics_json=json.dumps(problematic_topics, ensure_ascii=False),
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения рекомендаций экзамена ({exam_type}) для пользователя {user_id}: {e}")
    title_ru, title_en = cfg["title_ru"], cfg["title_en"]
    if lang.startswith("ru"):
        if wrong_by_topic:
            lines = ["\nОшибки по темам (неверный ответ):"]
            for topic_name, n in sorted_wrong:
                lines.append(f"• {_topic_display(topic_name, lang)} — {n} задач(и)")
            lines.append("\nРекомендации (по важности):")
            for i, (topic_name, n) in enumerate(sorted_wrong, start=1):
                lines.append(f"{i}. {_topic_display(topic_name, lang)} — приоритет {n}")
            stats = "\n".join(lines)
        else:
            stats = "\n\nНеверных ответов не было. Рекомендации не требуются."
        msg = (
            f"Ваш результат экзамена {title_ru}:\n\n"
            f"Вы решили правильно {state.get('correct_count', 0)} из {total_q} задач и набрали {k:.2f} баллов."
            f"{stats}"
        )
    else:
        if wrong_by_topic:
            lines = ["\nMistakes by topic (wrong answer):"]
            for topic_name, n in sorted_wrong:
                lines.append(f"• {_topic_display(topic_name, lang)} — {n} task(s)")
            lines.append("\nRecommendations (by priority):")
            for i, (topic_name, n) in enumerate(sorted_wrong, start=1):
                lines.append(f"{i}. {_topic_display(topic_name, lang)} — priority {n}")
            stats = "\n".join(lines)
        else:
            stats = "\n\nNo incorrect answers. No recommendations needed."
        msg = (
            f"Your {title_en} exam result:\n\n"
            f"You solved {state.get('correct_count', 0)} out of {total_q} tasks correctly and scored {k:.2f} points."
            f"{stats}"
        )
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(msg, reply_markup=kb)


def _format_exam_stats_line_by_type(state, exam_type: str, lang: str = "en") -> str:
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return ""
    total_q = len(cfg["questions"])
    total_d = cfg["total_difficulty"]
    k = (state.get("correct_difficulty", 0) / total_d * 100) if total_d else 0.0
    correct = state.get("correct_count", 0)
    return _txt(
        lang,
        f"Правильно {correct} из {total_q}, балл {k:.2f}.",
        f"Correct {correct} of {total_q}, score {k:.2f}.",
    )


def _inline_kb_exam_entry_choice_by_type(exam_type: str, lang: str = "en") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=_txt(lang, "Очистить статистику", "Clear stats"), callback_data=f"exam_clear_{exam_type}"),
        InlineKeyboardButton(text=_txt(lang, "Продолжить", "Continue"), callback_data=f"exam_continue_{exam_type}"),
    )
    return builder.as_markup()


def _inline_kb_exam_finished_by_type(exam_type: str, lang: str = "en") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=_txt(lang, "Очистить статистику", "Clear stats"), callback_data=f"exam_clear_{exam_type}"),
        InlineKeyboardButton(text=_txt(lang, "Список тем", "Topic list"), callback_data="back_start"),
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
   lang = await _get_user_lang(call.from_user)
   if lang.startswith("ru"):
       if wrong_by_topic:
           lines = ["\nОшибки по темам (неверный ответ):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {_topic_display(topic_name, lang)} — {n} задач(и)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nНеверных ответов не было."
       msg = (
           f"Ваш результат экзамена 25 января:\n\n"
           f"Вы решили правильно {correct} из {total_q} задач и набрали {k:.2f} баллов."
           f"{stats}"
       )
   else:
       if wrong_by_topic:
           lines = ["\nMistakes by topic (wrong answer):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {_topic_display(topic_name, lang)} — {n} task(s)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nNo incorrect answers."
       msg = (
           f"Your January 25 exam result:\n\n"
           f"You solved {correct} out of {total_q} tasks correctly and scored {k:.2f} points."
           f"{stats}"
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
                       n_val, _, _ = exam_questions_dec[idx]
                       correct_difficulty += _exam_points_for_n(int(n_val or 0))
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


def inline_kb_exam_dec(
    idx: int, *, option_order: list[int] | None = None, exam_lang: str | None = None
) -> InlineKeyboardMarkup:
   """Клавиатура вариантов для экзамена 21 декабря (exam_type=dec)."""
   return inline_kb_exam_by_type(
       idx, "dec", option_order=option_order, exam_lang=exam_lang
   )


async def _send_exam_dec_question(call: CallbackQuery, user_id: int, idx: int):
   if idx < 0 or idx >= len(exam_questions_dec):
       kb = await start_kb(user_id)
       await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
       return
   _, top, j = exam_questions_dec[idx]
   q = kapibara[top][j]
   lang = await _get_user_lang(call.from_user)
   if q.get("img"):
       photo_path = os.path.join(DATA_DIR, "images", q["img"])
       await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
   total = len(exam_questions_dec)
   header = _txt(
       lang,
       f"Экзамен 21 декабря — вопрос {idx + 1} из {total}\n\n",
       f"December 21 exam — question {idx + 1} of {total}\n\n",
   )
   difficulty = q.get("difficulty")
   if isinstance(difficulty, int) and 1 <= difficulty <= 5:
       diff_line = _txt(
           lang,
           f"Сложность: {'★' * difficulty}{'☆' * (5 - difficulty)}\n",
           f"Difficulty: {'★' * difficulty}{'☆' * (5 - difficulty)}\n",
       )
   else:
       diff_line = ""
   exam_lang = await _get_user_exam_lang_by_id(user_id)
   question_text = header + diff_line + _full_task_question_text(q, exam_lang)
   opts_n = _task_options_for_display(q, exam_lang)
   order = _option_display_indices(
       top, q, len(opts_n), opts_for_shuffle_check=opts_n
   )
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(
           question_text,
           reply_markup=inline_kb_exam_dec(idx, option_order=order, exam_lang=exam_lang),
       )
   _record_last_seen_question(user_id, top, j, order)


async def _send_exam_dec_summary(call: CallbackQuery, user_id: int):
   state = await _get_exam_dec_state(user_id)
   correct = state.get("correct_count", 0)
   total_q = len(exam_questions_dec)
   k = (state.get("correct_difficulty", 0) / EXAM_DEC_TOTAL_DIFFICULTY * 100) if EXAM_DEC_TOTAL_DIFFICULTY else 0.0
   kb = _inline_kb_exam_dec_finished()
   wrong_by_topic = await _exam_wrong_by_topic(user_id, "dec")
   lang = await _get_user_lang(call.from_user)
   if lang.startswith("ru"):
       if wrong_by_topic:
           lines = ["\nОшибки по темам (неверный ответ):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {_topic_display(topic_name, lang)} — {n} задач(и)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nНеверных ответов не было."
       msg = (
           f"Ваш результат экзамена 21 декабря:\n\n"
           f"Вы решили правильно {correct} из {total_q} задач и набрали {k:.2f} баллов."
           f"{stats}"
       )
   else:
       if wrong_by_topic:
           lines = ["\nMistakes by topic (wrong answer):"]
           for topic_name in sorted(wrong_by_topic.keys()):
               n = wrong_by_topic[topic_name]
               lines.append(f"• {_topic_display(topic_name, lang)} — {n} task(s)")
           stats = "\n".join(lines)
       else:
           stats = "\n\nNo incorrect answers."
       msg = (
           f"Your December 21 exam result:\n\n"
           f"You solved {correct} out of {total_q} tasks correctly and scored {k:.2f} points."
           f"{stats}"
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


# --- Mock Exam (задачи по подтеме/сложности как у jan; кандидаты без type=jan и type=mar) ---
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
                       # В Mock Exam баллы считаем по n позиции (как в exam_questions)
                       if 0 <= idx < len(exam_questions):
                           n_val, _, _ = exam_questions[idx]
                           correct_difficulty += _exam_points_for_n(int(n_val or 0))
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


def inline_kb_exam_mock(
    idx: int, *, option_order: list[int] | None = None, exam_lang: str | None = None
) -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   if idx < 0 or idx >= len(mock_questions):
       builder.adjust(1)
       return builder.as_markup()
   top, j = mock_questions[idx]
   q = kapibara[top][j]
   opts_src = _task_options_for_display(q, exam_lang)
   k = _keyboard_option_labels_from_display(opts_src)
   order = (
       option_order
       if option_order is not None
       else _option_display_indices(
           top, q, len(k), opts_for_shuffle_check=opts_src
       )
   )
   for pos, i in enumerate(order):
       letter = chr(ord("A") + pos)
       builder.add(
           InlineKeyboardButton(
               text=_option_button_text_shuffled(k[i], letter),
               callback_data=f'exam_mock_q_{idx}_{i}',
           )
       )
   builder.adjust(1)
   return builder.as_markup()


async def _send_exam_mock_question(call: CallbackQuery, user_id: int, idx: int):
   # Блокируем показ следующего вопроса Mock Exam для пользователя 7567696330
   if user_id == 7567696331:
       log(call.from_user, [EXAM_MOCK_ID, "next_blocked"])
       return

   if idx < 0 or idx >= len(mock_questions):
       kb = await start_kb(user_id)
       await call.message.answer("Экзаменационные задачи закончились.", reply_markup=kb)
       return
   top, j = mock_questions[idx]
   q = kapibara[top][j]
   lang = await _get_user_lang(call.from_user)
   if q.get("img"):
       photo_path = os.path.join(DATA_DIR, "images", q["img"])
       await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
   total = len(mock_questions)
   header = _txt(
       lang,
       f"Пробный экзамен — вопрос {idx + 1} из {total}\n\n",
       f"Mock Exam — question {idx + 1} of {total}\n\n",
   )
   n_val = 0
   if 0 <= idx < len(exam_questions):
       n_val, _, _ = exam_questions[idx]
   stars = _exam_stars_for_n(n_val)
   diff_line = (
       _txt(lang, f"Сложность: {stars}\n", f"Difficulty: {stars}\n") if stars else ""
   )
   exam_lang = await _get_user_exam_lang_by_id(user_id)
   question_text = header + diff_line + _full_task_question_text(q, exam_lang)
   log(call.from_user, [EXAM_MOCK_ID, "question", idx, _exam_log_task_id(q)])
   opts_n = _task_options_for_display(q, exam_lang)
   order = _option_display_indices(
       top, q, len(opts_n), opts_for_shuffle_check=opts_n
   )
   async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
       await call.message.answer(
           question_text,
           reply_markup=inline_kb_exam_mock(idx, option_order=order, exam_lang=exam_lang),
       )
   _record_last_seen_question(user_id, top, j, order)


async def _send_exam_mock_summary(call: CallbackQuery, user_id: int):
   state = await _get_exam_mock_state(user_id)
   correct = state.get("correct_count", 0)
   total_q = len(mock_questions)
   k = (state.get("correct_difficulty", 0) / MOCK_TOTAL_DIFFICULTY * 100) if MOCK_TOTAL_DIFFICULTY else 0.0
   lang = await _get_user_lang(call.from_user)
   kb = _inline_kb_exam_mock_finished()
   wrong_by_topic = await _exam_wrong_by_topic(user_id, "mock")
   sorted_wrong = sorted(wrong_by_topic.items(), key=lambda kv: (-kv[1], kv[0]))
   problematic_topics = [topic_name for topic_name, _ in sorted_wrong]

   if db_conn:
       try:
           await db.save_user_exam_recommendations(
               db_conn,
               user_id=user_id,
               exam_type="mock",
               topics_json=json.dumps(problematic_topics, ensure_ascii=False),
           )
       except Exception as e:
           logging.error(f"Ошибка сохранения рекомендаций mock для пользователя {user_id}: {e}")

   if lang.startswith("ru"):
       if sorted_wrong:
           rec_lines = ["\nРекомендации (по важности):"]
           for i, (topic_name, n) in enumerate(sorted_wrong, start=1):
               rec_lines.append(f"{i}. {_topic_display(topic_name, lang)} — приоритет {n}")
           rec_text = "\n".join(rec_lines)
       else:
           rec_text = "\n\nНеверных ответов не было. Рекомендации не требуются."
       msg = (
           f"Ваш результат Mock Exam (пробный экзамен):\n\n"
           f"Вы решили правильно {correct} из {total_q} задач и набрали {k:.2f} баллов."
           f"{rec_text}"
       )
   else:
       if sorted_wrong:
           rec_lines = ["\nRecommendations (by priority):"]
           for i, (topic_name, n) in enumerate(sorted_wrong, start=1):
               rec_lines.append(f"{i}. {_topic_display(topic_name, lang)} — priority {n}")
           rec_text = "\n".join(rec_lines)
       else:
           rec_text = "\n\nNo incorrect answers. No recommendations needed."
       msg = (
           f"Your Mock Exam result:\n\n"
           f"You solved {correct} out of {total_q} tasks correctly and scored {k:.2f} points."
           f"{rec_text}"
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


def inline_kb_sub(
    topic_idx: int,
    sub_idx: int,
    k: int,
    showvideo: int = 1,
    lang_code: str = "en",
    *,
    option_order: list[int] | None = None,
    exam_lang: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура вариантов ответа для вопроса в режиме подтемы (callback qst_sub_)."""
    topic, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if topic is None:
        return None
    q = kapibara[topic][j]
    builder = InlineKeyboardBuilder()
    opts_src = _task_options_for_display(q, exam_lang)
    opt_labels = _keyboard_option_labels_from_display(opts_src)
    order = (
        option_order
        if option_order is not None
        else _option_display_indices(
            topic, q, len(opt_labels), opts_for_shuffle_check=opts_src
        )
    )
    for pos, i in enumerate(order):
        letter = chr(ord("A") + pos)
        builder.add(
            InlineKeyboardButton(
                text=_option_button_text_shuffled(opt_labels[i], letter),
                callback_data=f'qst_sub_{topic_idx}_{sub_idx}_{k}_{i}'
            )
        )

    if _has_hint_image(q, lang_code):
        hint_text = "Показать подсказку" if _lang_suffix(lang_code) == "ru" else "Show hint"
        builder.add(
            InlineKeyboardButton(
                text=hint_text,
                callback_data=f"hint_sub_{topic_idx}_{sub_idx}_{k}",
            )
        )
    builder.adjust(1)
    return builder.as_markup()


def inline_kb_next_sub(topic_idx: int, sub_idx: int, k: int, lang: str, user_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Следующий вопрос» в режиме подтемы (линейно или продолжение повторов)."""
    if topic_idx < 0 or topic_idx >= len(topics):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=_txt(lang, "Назад", "Back"), callback_data="back_start"))
        return builder.as_markup()
    topic = topics[topic_idx]
    subs = subtopics_by_topic.get(topic, [])
    sub_name = subs[sub_idx] if sub_idx < len(subs) else None
    j_list = questions_by_topic_subtopic.get((topic, sub_name), []) if sub_name else []
    n = len(j_list)
    sk = _sub_key(user_id, topic_idx, sub_idx)
    in_review = _sub_in_review.get(sk, False)
    review = _sub_build_review_set(user_id, topic_idx, sub_idx, n)
    if in_review and review:
        next_k = n
    elif k + 1 < n:
        next_k = k + 1
    else:
        next_k = n
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_txt(lang, "Следующий вопрос", "Next task"),
            callback_data=f"next_sub_{topic_idx}_{sub_idx}_{next_k}",
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def inline_kb_explain_sub(topic_idx: int, sub_idx: int, k: int, showvideo: int = 1, lang_code: str = "en") -> InlineKeyboardMarkup:
    """Клавиатура после неверного ответа в режиме подтемы (повторить + обсудить + следующий)."""
    topic, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if topic is None:
        return None
    k_item = kapibara[topic][j]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_txt(lang_code, 'Повторить', 'One more time!'),
            callback_data=f'next_sub_{topic_idx}_{sub_idx}_{k}'
        )
    )
    topiclink = topic_links.get(k_item.get('subtopic', ''), '') or topic_links.get(k_item.get('topic'), '')
    if _normalize_lang(lang_code) != "ru":
        topiclink = "https://t.me/+hN3O2vl9211mZmU6"
    if topiclink:
        builder.row(
            InlineKeyboardButton(
                text=_txt(lang_code, 'Обсудить задачу', 'Ask a question'),
                url=topiclink
            )
        )

    hint_paths = _get_hint_image_path(k_item, lang_code)
    if hint_paths:
        hint_text = "Показать подсказку" if _lang_suffix(lang_code) == "ru" else "Show hint"
        builder.row(
            InlineKeyboardButton(
                text=hint_text,
                callback_data=f"hint_sub_{topic_idx}_{sub_idx}_{k}",
            )
        )
    if not k_item.get("img") and _has_solution_for_lang(k_item, lang_code):
        builder.row(
            InlineKeyboardButton(
                text=_txt(lang_code, "Решение", "Solution"),
                callback_data=f"sol_sub_{topic_idx}_{sub_idx}_{k}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=_txt(lang_code, 'Следующий вопрос', 'Next question'),
            callback_data=f'next_sub_{topic_idx}_{sub_idx}_{k + 1}'
        )
    )
    builder.adjust(1)
    return builder.as_markup()

def inline_kb_explain(top, j, k, lang_code: str = "en") :
   builder = InlineKeyboardBuilder()
   builder.row(
        InlineKeyboardButton(
            text=_txt(lang_code, 'Повторить', 'One more time!'),
            callback_data='next_'+top+'_'+str(j)   #'explain'
        )
    )
   topiclink  = topic_links.get(k.get('subtopic',''),'') or topic_links.get(k.get('topic'),'')
   if _normalize_lang(lang_code) != "ru":
      topiclink = "https://t.me/+hN3O2vl9211mZmU6"
   if topiclink :
      
      builder.row(
        InlineKeyboardButton(
            text=_txt(lang_code, 'Обсудить задачу', 'Ask a question') ,
            url=topiclink
        )
      )

   hint_paths = _get_hint_image_path(k, lang_code)
   if hint_paths:
       hint_text = "Показать подсказку" if _lang_suffix(lang_code) == "ru" else "Show hint"
       builder.row(
           InlineKeyboardButton(
               text=hint_text,
               callback_data=f"hint_{top}_{j}",
           )
       )
   if not k.get("img") and _has_solution_for_lang(k, lang_code):
       try:
           top_idx = topics.index(top)
           builder.row(
               InlineKeyboardButton(
                   text=_txt(lang_code, "Решение", "Solution"),
                   callback_data=f"sol_top_{top_idx}_{j}",
               )
           )
       except Exception:
           pass

   builder.row(
        InlineKeyboardButton(
            text=_txt(lang_code, 'Следующий вопрос', 'Next question'),
            callback_data='next_'+top+'_'+str(j+1)
        )
    )
   builder.adjust(1)
   return builder.as_markup()


def inline_kb_math_all(
    top: str,
    j: int,
    lang_code: str = "en",
    *,
    option_order: list[int] | None = None,
    exam_lang: str | None = None,
) -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   q = kapibara[top][j]
   opts_src = _task_options_for_display(q, exam_lang)
   k = _keyboard_option_labels_from_display(opts_src)
   order = (
       option_order
       if option_order is not None
       else _option_display_indices(
           top, q, len(k), opts_for_shuffle_check=opts_src
       )
   )
   top_idx = topics.index(top)
   for pos, i in enumerate(order):
       letter = chr(ord("A") + pos)
       builder.add(
           InlineKeyboardButton(
               text=_option_button_text_shuffled(k[i], letter),
               callback_data=f"math_q_{top_idx}_{j}_{i}",
           )
       )
   if _has_hint_image(q, lang_code):
       builder.add(
           InlineKeyboardButton(
               text=_txt(lang_code, "Показать подсказку", "Show hint"),
               callback_data=f"math_hint_{top_idx}_{j}",
           )
       )
   builder.adjust(1)
   return builder.as_markup()


def inline_kb_math_all_explain(top: str, j: int, q: dict, lang_code: str = "en") -> InlineKeyboardMarkup:
   """Кнопки после неверного ответа в режиме всех задач."""
   builder = InlineKeyboardBuilder()
   top_idx = topics.index(top)

   if _has_hint_image(q, lang_code):
       builder.row(
           InlineKeyboardButton(
               text=_txt(lang_code, "Показать подсказку", "Show hint"),
               callback_data=f"math_hint_{top_idx}_{j}",
           )
       )

   if not q.get("img") and _has_solution_for_lang(q, lang_code):
       builder.row(
           InlineKeyboardButton(
               text=_txt(lang_code, "Решение", "Solution"),
               callback_data=f"sol_math_{top_idx}_{j}",
           )
       )

   topiclink = topic_links.get(q.get("subtopic", ""), "") or topic_links.get(q.get("topic"), "")
   if _normalize_lang(lang_code) != "ru":
       topiclink = "https://t.me/+hN3O2vl9211mZmU6"
   if topiclink:
       builder.row(
           InlineKeyboardButton(
               text=_txt(lang_code, "Обсудить задачу", "Ask a question"),
               url=topiclink,
           )
       )

   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Следующий вопрос", "Next task"),
           callback_data=f"math_next_{top_idx}_{j}",
       )
   )
   builder.adjust(1)
   return builder.as_markup()


def _solution_text_for_lang(q: dict, lang_code: str) -> str:
    if _normalize_lang(lang_code) == "ru":
        txt = (q.get("solution_ru2") or q.get("solution_ru") or "").strip()
        if txt:
            return txt
    txt = (q.get("solution_en2") or q.get("solution_en") or "").strip()
    if txt:
        return txt
    return _txt(lang_code, "Решение пока отсутствует.", "Solution is not available yet.")


def _has_solution_for_lang(q: dict, lang_code: str) -> bool:
    """Есть ли текст решения именно на выбранном языке (без запасного варианта на другом)."""
    if _normalize_lang(lang_code) == "ru":
        return bool((q.get("solution_ru2") or q.get("solution_ru") or "").strip())
    return bool((q.get("solution_en2") or q.get("solution_en") or "").strip())


def _kb_solution_feedback_topic(top_idx: int, j: int, lang_code: str = "en") -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Все понятно! 👍", "All clear! 👍"),
           callback_data=f"solup_top_{top_idx}_{j}",
       )
   )
   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Ничего не понятно 👎", "Nothing is clear 👎"),
           callback_data=f"soldn_top_{top_idx}_{j}",
       )
   )
   builder.adjust(1)
   return builder.as_markup()


def _kb_solution_feedback_sub(topic_idx: int, sub_idx: int, k: int, lang_code: str = "en") -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Все понятно! 👍", "All clear! 👍"),
           callback_data=f"solup_sub_{topic_idx}_{sub_idx}_{k}",
       )
   )
   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Ничего не понятно 👎", "Nothing is clear 👎"),
           callback_data=f"soldn_sub_{topic_idx}_{sub_idx}_{k}",
       )
   )
   builder.adjust(1)
   return builder.as_markup()


def _kb_solution_feedback_math(top_idx: int, j: int, lang_code: str = "en") -> InlineKeyboardMarkup:
   builder = InlineKeyboardBuilder()
   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Все понятно! 👍", "All clear! 👍"),
           callback_data=f"solup_math_{top_idx}_{j}",
       )
   )
   builder.row(
       InlineKeyboardButton(
           text=_txt(lang_code, "Ничего не понятно 👎", "Nothing is clear 👎"),
           callback_data=f"soldn_math_{top_idx}_{j}",
       )
   )
   builder.adjust(1)
   return builder.as_markup()


def _kb_soldn_after_ai_unclear(
    lang: str,
    topiclink: str,
    next_callback_data: str,
    *,
    show_explain: bool = True,
) -> InlineKeyboardMarkup:
    """После «Решение сгенерировано нейросетью…»: Перейти в чат → [Объяснить подробнее, если не было LLM] → Следующая задача."""
    kb = InlineKeyboardBuilder()
    chat_url = (topiclink or "").strip()
    if not chat_url:
        chat_url = (
            DEFAULT_CHAT_FALLBACK_RU
            if _normalize_lang(lang) == "ru"
            else "https://t.me/+hN3O2vl9211mZmU6"
        )
    kb.row(
        InlineKeyboardButton(
            text=_txt(lang, "Перейти в чат", "Go to chat"),
            url=chat_url,
        )
    )
    if show_explain:
        kb.row(
            InlineKeyboardButton(
                text=_txt(lang, "Объяснить подробнее…", "Explain in more detail…"),
                callback_data="llm_explain_last",
            )
        )
    kb.row(
        InlineKeyboardButton(
            text=_txt(lang, "Следующая задача", "Next task"),
            callback_data=next_callback_data,
        )
    )
    return kb.as_markup()


def _discussion_url_for_task(q: dict, lang: str) -> str:
    """
    URL для кнопки «Перейти в чат» после 👎 к решению из data.txt.
    Не-ru — инвайт-ссылка; ru — тема из topic_links, иначе DEFAULT_CHAT_FALLBACK_RU.
    """
    fallback_en = "https://t.me/+hN3O2vl9211mZmU6"
    topiclink = topic_links.get(q.get("subtopic", ""), "") or topic_links.get(q.get("topic"), "")
    if _normalize_lang(lang) != "ru":
        return fallback_en
    return topiclink or DEFAULT_CHAT_FALLBACK_RU


def _get_llm_access_token() -> str:
    access_token = os.environ.get("LLM_TOKEN", "").strip()
    access_token = "--"
    return access_token


def _do_llm_request(
    mode: str,
    user_text: str,
    user_id: int,
    chat_id: int,
    from_user,
    lang: str,
    access_token: str,
    exam_lang: str | None = None,
) -> str:
    llm = AmveraLLM(model="gpt-4.1",  api_token=access_token)
    addtext = """Определи о чем вопрос и верни одно из чисел: 
1 задачи по математике, 
2 физика 
3 химия
4 не понятно как решить задачу
5 информация о CSCA"""
    short = _txt(lang, "Отвечай коротко и по делу.", "Answer briefly and to the point.")

    if mode == LLM_CONTEXT_ADDTEXT_ONLY:
        messages = [
            SystemMessage(content=addtext),
            SystemMessage(content=short),
            HumanMessage(content=user_text),
        ]
    elif mode == LLM_CONTEXT_RAG_ONLY:
        rag_text = load_rag_text()
        messages = [
            SystemMessage(content=rag_text),
            SystemMessage(content=short),
            HumanMessage(content=user_text),
        ]
    elif mode == LLM_CONTEXT_LAST_TASK:
        task_block = _format_last_seen_task_for_llm(user_id, lang, exam_lang)
        if not task_block:
            log(from_user, ["llm_skip", str(chat_id), mode, "__NO_LAST_TASK__"])
            return "__NO_LAST_TASK__"
        hint = _txt(
            lang,
            "Ниже условие задачи и варианты ответа. Расскажи решение подробно. Используй текст в unicode. Не используй latex",
            "Below is the problem statement and answer options. Explain the solution in detail. Do not use latex.Use unicode text",
        )
        messages = [
            SystemMessage(content=hint),
            SystemMessage(content=task_block),
            HumanMessage(content=user_text),
        ]
    else:
        messages = [
            SystemMessage(content=addtext),
            SystemMessage(content=short),
            HumanMessage(content=user_text),
        ]

    response = llm.invoke(messages)
    content = getattr(response, "content", None) or ""
    try:
        ans_one_line = (content or "").replace("\n", " ").strip()
        if len(ans_one_line) > 2000:
            ans_one_line = ans_one_line[:2000] + "..."
        log(
            from_user,
            [
                "llm_response",
                str(chat_id),
                mode,
                f"len={len(content or '')}",
                ans_one_line,
            ],
        )
    except Exception:
        log(from_user, ["llm_response", str(chat_id), mode, "len=?", "log_error"])
    return content


def _kb_llm_task_feedback(user_id: int, lang: str) -> InlineKeyboardMarkup | None:
    """
    Те же кнопки, что после показа решения из data.txt (solup/soldn),
    с учётом режима «вся математика» (math_next) при активной сессии.
    """
    key = _last_seen_kapibara_question.get(user_id)
    if not key or len(key) < 2:
        return None
    top, j = key[0], int(key[1])
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        return None
    try:
        top_idx = topics.index(top)
    except ValueError:
        return None
    sess = _math_all_sessions.get(user_id)
    if sess and sess.get("current_question") == (top, j):
        return _kb_solution_feedback_math(top_idx, j, lang)
    return _kb_solution_feedback_topic(top_idx, j, lang)


async def _math_all_allowed_topics(user_id: int) -> set[str]:
    allowed = set(_math_all_all_topics())
    if not db_conn:
        return allowed
    try:
        st = await db.get_user_stats(db_conn, user_id)
        for t in st.get("by_topic", []):
            top = t.get("topic")
            if top in allowed and float(t.get("accuracy", 0.0) or 0.0) > 85.0:
                allowed.discard(top)
    except Exception as e:
        logging.error(f"Ошибка чтения статистики пользователя {user_id}: {e}")
    return allowed


async def _math_all_recommended_topic(user_id: int, allowed_topics: set[str]) -> str | None:
    if not db_conn:
        return None
    try:
        rows = await db.get_user_exam_recommendations_rows(db_conn, user_id)
    except Exception as e:
        logging.error(f"Ошибка чтения рекомендаций экзаменов пользователя {user_id}: {e}")
        return None
    score: dict[str, int] = {}
    for _, topics_json in rows:
        try:
            arr = json.loads(topics_json or "[]")
        except Exception:
            arr = []
        if not isinstance(arr, list):
            continue
        for top in arr:
            if not isinstance(top, str):
                continue
            if top in allowed_topics and top != "physics":
                score[top] = score.get(top, 0) + 1
    if not score:
        return None
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


async def _math_all_seed_from_db_history(user_id: int, session: dict) -> None:
    if not db_conn:
        return
    topics_for_history = sorted(session.get("allowed_topics", set()))
    if not topics_for_history:
        return
    try:
        rows = await db.get_user_answers_for_topics(db_conn, user_id, topics_for_history)
    except Exception as e:
        logging.error(f"Ошибка чтения истории ответов для math_all: {e}")
        return

    seen_first: dict[tuple[str, int], bool] = {}
    at = session.setdefault("attempts", {})
    for top, q_idx, _chosen, correct, _created in rows:
        key = (top, int(q_idx))
        at[key] = at.get(key, 0) + 1
        if key not in seen_first:
            seen_first[key] = bool(correct)
        if bool(correct):
            if seen_first[key]:
                session.setdefault("solved_first_try", set()).add(key)
            else:
                session.setdefault("solved_not_first_try", set()).add(key)
        elif at[key] > 1:
            session.setdefault("solved_not_first_try", set()).add(key)

    # Правило "две задачи с тем же id-префиксом решены с первого раза".
    pc = session.setdefault("first_ok_prefix_counts", {})
    for top, j in session.get("solved_first_try", set()):
        qid = _math_all_task_id(top, j)
        pref = _derive_subtopic_id(qid) or qid
        if pref:
            pc[pref] = pc.get(pref, 0) + 1


def _math_all_pick_start_subtopic(allowed_topics: set[str], preferred_topic: str | None) -> tuple[str, str] | None:
    cands = [ts for ts in MATH_SUBTOPICS_SORTED if ts[0] in allowed_topics]
    if not cands:
        return None
    if preferred_topic:
        pref = [ts for ts in cands if ts[0] == preferred_topic]
        if pref:
            return pref[0]
    return cands[0]


def _math_all_pick_question_in_subtopic(
    session: dict,
    top: str,
    sub: str,
    first_pick: bool = False,
) -> tuple[str, int] | None:
    pool = []
    for j in _math_all_subtopic_tasks(top, sub):
        key = (top, j)
        if not _math_all_candidate_allowed(session, key):
            continue
        pool.append(key)
    if not pool:
        return None
    # Первая задача — самая простая в выбранной теме/подтеме.
    if first_pick:
        min_d = min(_q_diff(kapibara[t][jj]) for t, jj in pool)
        best = [(t, jj) for t, jj in pool if _q_diff(kapibara[t][jj]) == min_d]
        return sorted(best, key=lambda x: x[1])[0]
    return _math_all_pick_from_candidates(pool)


def _math_all_prev_subtopic_retry_candidate(session: dict) -> tuple[str, int] | None:
    prev = session.get("previous_subtopic")
    if not prev:
        return None
    top, sub = prev
    cands = []
    for j in _math_all_subtopic_tasks(top, sub):
        key = (top, j)
        if key not in session.get("solved_not_first_try", set()):
            continue
        dist = _math_all_recent_distance(session, key)
        if dist is None or dist < 4:
            continue
        if not _math_all_candidate_allowed(session, key):
            continue
        cands.append(key)
    return _math_all_pick_from_candidates(cands)


def _math_all_next_question(session: dict) -> tuple[str, int] | None:
    # Сначала пробуем вернуть задачу из предыдущей подтемы (правило "не с первого раза", >=4 задачи назад).
    retry_prev = _math_all_prev_subtopic_retry_candidate(session)
    if retry_prev:
        return retry_prev

    cur = session.get("current_subtopic")
    if cur:
        q = _math_all_pick_question_in_subtopic(session, cur[0], cur[1], first_pick=False)
        if q:
            return q

    # Переход к следующей подтеме по правилу top-5 простейших.
    nxt = _math_all_pick_next_subtopic(session)
    if not nxt:
        return None
    session["previous_subtopic"] = session.get("current_subtopic")
    session["current_subtopic"] = nxt
    session.setdefault("participated_subtopics", set()).add(nxt)
    q = _math_all_pick_question_in_subtopic(session, nxt[0], nxt[1], first_pick=False)
    if q:
        return q

    # Если выбранная подтема пуста по фильтрам, пробуем последовательно дальше.
    tried = {nxt}
    for ts in MATH_SUBTOPICS_SORTED:
        if ts in tried or ts[0] not in session.get("allowed_topics", set()):
            continue
        session["previous_subtopic"] = session.get("current_subtopic")
        session["current_subtopic"] = ts
        session.setdefault("participated_subtopics", set()).add(ts)
        qq = _math_all_pick_question_in_subtopic(session, ts[0], ts[1], first_pick=False)
        if qq:
            return qq
    return None


def _math_all_session_answer_update(session: dict, key: tuple[str, int], ansok: bool) -> None:
    at = session.setdefault("attempts", {})
    at[key] = at.get(key, 0) + 1
    if ansok and at[key] == 1:
        session.setdefault("solved_first_try", set()).add(key)
        qid = _math_all_task_id(key[0], key[1])
        pref = _derive_subtopic_id(qid) or qid
        if pref:
            pc = session.setdefault("first_ok_prefix_counts", {})
            pc[pref] = pc.get(pref, 0) + 1
    if ansok and at[key] > 1:
        session.setdefault("solved_not_first_try", set()).add(key)
    if not ansok and at[key] >= 1:
        session.setdefault("solved_not_first_try", set()).add(key)


async def _math_all_send_question(call: CallbackQuery, user_id: int, key: tuple[str, int]) -> None:
    top, j = key
    q = kapibara[top][j]
    lang = await _get_user_lang(call.from_user)
    if q.get("img"):
        photo_path = os.path.join(DATA_DIR, "images", q["img"])
        await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
    session = _math_all_sessions.setdefault(user_id, {})
    session["current_question"] = key
    session.setdefault("shown_history", []).append(key)
    qid = str(q.get("id") or "")
    log(call.from_user, ["math_all_question", top, j, qid])
    title_ru = f"Тема: {_topic_display(top, lang)} | Подтема: {_subtopic_display(top, _math_all_subtopic(top, j), lang)}"
    title_en = f"Topic: {_topic_display(top, lang)} | Subtopic: {_subtopic_display(top, _math_all_subtopic(top, j), lang)}"
    exam_lang = await _get_user_exam_lang_by_id(user_id)
    text = _txt(lang, title_ru + "\n\n", title_en + "\n\n") + _full_task_question_text(q, exam_lang)
    opts_n = _task_options_for_display(q, exam_lang)
    order = _option_display_indices(
        top, q, len(opts_n), opts_for_shuffle_check=opts_n
    )
    await call.message.answer(
        text,
        reply_markup=inline_kb_math_all(
            top, j, lang_code=lang, option_order=order, exam_lang=exam_lang
        ),
    )
    _record_last_seen_question(user_id, top, j, order)


@router.callback_query(F.data == "menu_all_math")
async def on_menu_all_math(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    lang = await _get_user_lang(call.from_user)
    loaded = await _math_all_load_state(user_id)
    if loaded:
        _math_all_sessions[user_id] = loaded
        next_q_loaded = _math_all_next_question(loaded)
        if next_q_loaded:
            async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
                await _math_all_send_question(call, user_id, next_q_loaded)
            return
    allowed_topics = await _math_all_allowed_topics(user_id)
    if not allowed_topics:
        kb = await start_kb(user_id)
        await call.message.answer(_txt(lang, "Нет доступных тем для тренировки.", "No available topics for training."), reply_markup=kb)
        return

    pref_topic = await _math_all_recommended_topic(user_id, allowed_topics)
    start_sub = _math_all_pick_start_subtopic(allowed_topics, pref_topic)
    if not start_sub:
        kb = await start_kb(user_id)
        await call.message.answer(_txt(lang, "Нет задач для тренировки.", "No tasks for training."), reply_markup=kb)
        return

    session = {
        "allowed_topics": allowed_topics,
        "participated_subtopics": {start_sub},
        "current_subtopic": start_sub,
        "previous_subtopic": None,
        "shown_history": [],
        "attempts": {},
        "solved_first_try": set(),
        "solved_not_first_try": set(),
        "first_ok_prefix_counts": {},
    }
    await _math_all_seed_from_db_history(user_id, session)
    _math_all_sessions[user_id] = session
    await _math_all_save_state(user_id)
    first_q = _math_all_pick_question_in_subtopic(session, start_sub[0], start_sub[1], first_pick=True)
    if not first_q:
        first_q = _math_all_next_question(session)
    if not first_q:
        kb = await start_kb(user_id)
        await call.message.answer(_txt(lang, "Не удалось подобрать задачу по правилам.", "Could not pick a task with current rules."), reply_markup=kb)
        return
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await _math_all_send_question(call, user_id, first_q)


@router.callback_query(F.data.startswith("math_q_"))
async def on_math_all_answer(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    user_id = call.from_user.id
    session = _math_all_sessions.get(user_id)
    if not session:
        kb = await start_kb(user_id)
        await call.message.answer(_txt(lang, "Сессия тренировки не найдена. Нажмите кнопку ещё раз.", "Training session not found. Start again."), reply_markup=kb)
        return
    parts = call.data.split("_")
    if len(parts) < 5:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
        ans_id = parts[4]
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top == "physics" or top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    key = (top, j)
    q = kapibara[top][j]
    correct_h = {"A": "0", "B": "1", "C": "2", "D": "3", "E": "4"}
    correct = correct_h.get(q.get("answer", ""), "")
    ansok = 1 if correct == ans_id else 0
    _math_all_session_answer_update(session, key, bool(ansok))
    await _math_all_save_state(user_id)
    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                user_id,
                top,
                j,
                int(ans_id) if str(ans_id).isdigit() else 0,
                ansok == 1,
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа math_all в БД: {e}")
    qid = str(q.get("id") or "")
    log(call.from_user, ["math_all_answer", top, j, ans_id, ansok, qid])

    if ansok:
        msg_text = _correct_phrase_training(lang)
    else:
        msg_text = await _wrong_answer_training_message(user_id, lang)

    # После неверного ответа показываем кнопки "подсказка / обсудить / следующий".
    if not ansok:
        await _math_all_save_state(user_id)
        async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
            await call.message.answer(
                msg_text,
                reply_markup=inline_kb_math_all_explain(top, j, q, lang_code=lang),
            )
        return

    next_q = _math_all_next_question(session)
    await _math_all_save_state(user_id)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(msg_text)
        if next_q:
            await _math_all_send_question(call, user_id, next_q)
        else:
            kb = await start_kb(user_id)
            await call.message.answer(_txt(lang, "Подходящих задач больше нет. Выберите другой режим.", "No more matching tasks. Choose another mode."), reply_markup=kb)


@router.callback_query(F.data.startswith("math_next_"))
async def on_math_all_next(call: CallbackQuery):
    """Следующая задача в режиме всех задач после кнопки из explain-клавиатуры."""
    await call.answer()
    user_id = call.from_user.id
    lang = await _get_user_lang(call.from_user)
    session = _math_all_sessions.get(user_id)
    if not session:
        kb = await start_kb(user_id)
        await call.message.answer(
            _txt(lang, "Сессия тренировки не найдена. Нажмите кнопку ещё раз.", "Training session not found. Start again."),
            reply_markup=kb,
        )
        return
    next_q = _math_all_next_question(session)
    await _math_all_save_state(user_id)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        if next_q:
            await _math_all_send_question(call, user_id, next_q)
        else:
            kb = await start_kb(user_id)
            await call.message.answer(
                _txt(lang, "Подходящих задач больше нет. Выберите другой режим.", "No more matching tasks. Choose another mode."),
                reply_markup=kb,
            )


@router.callback_query(F.data.startswith("math_hint_"))
async def on_math_all_hint(call: CallbackQuery):
    """Показывает картинку подсказки и повторяет текущую задачу в режиме всех задач."""
    await call.answer()
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    lang = await _get_user_lang(call.from_user)
    hint_paths = _get_hint_image_path(q, lang)
    log(call.from_user, ["math_hint_show", top, j, "hint_found" if hint_paths else "hint_not_found", str(q.get("id") or "")])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        for hp in hint_paths:
            await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(hp))
        if q.get("img"):
            photo_path = os.path.join(DATA_DIR, "images", q["img"])
            await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))
        title_ru = f"Тема: {_topic_display(top, lang)} | Подтема: {_subtopic_display(top, _math_all_subtopic(top, j), lang)}"
        title_en = f"Topic: {_topic_display(top, lang)} | Subtopic: {_subtopic_display(top, _math_all_subtopic(top, j), lang)}"
        exam_lang = await _get_user_exam_lang_by_id(call.from_user.id)
        text = _txt(lang, title_ru + "\n\n", title_en + "\n\n") + _full_task_question_text(q, exam_lang)
        opts_n = _task_options_for_display(q, exam_lang)
        order = _option_display_indices(
            top, q, len(opts_n), opts_for_shuffle_check=opts_n
        )
        await call.message.answer(
            text,
            reply_markup=inline_kb_math_all(
                top, j, lang_code=lang, option_order=order, exam_lang=exam_lang
            ),
        )
        _record_last_seen_question(call.from_user.id, top, j, order)

@router.callback_query(F.data.startswith("sol_math_"))
async def on_math_solution_show(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    if not _has_solution_for_lang(q, lang):
        await call.message.answer(
            _txt(lang, "Решения на выбранном языке нет.", "There is no solution in your selected language."),
        )
        return
    log(call.from_user, ["solution_show_math", top, j, str(q.get("id") or "")])
    await call.message.answer(
        _solution_text_for_lang(q, lang),
        reply_markup=_kb_solution_feedback_math(top_idx, j, lang),
    )


@router.callback_query(F.data.startswith("solup_math_"))
async def on_math_solution_up(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    log(call.from_user, ["solution_vote_up_math", top, j, str(q.get("id") or "")])
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=_txt(lang, "Следующая задача", "Next task"),
            callback_data=f"math_next_{top_idx}_{j}",
        )
    )
    await call.message.answer(_txt(lang, "Отлично", "Great"), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("soldn_math_"))
async def on_math_solution_down(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    log(call.from_user, ["solution_vote_down_math", top, j, str(q.get("id") or "")])
    topiclink = _discussion_url_for_task(q, lang)
    uid = call.from_user.id
    # Скрыть «Объяснить подробнее», если пользователь уже получал по этой задаче ответ LLM
    show_explain = not _user_has_viewed_llm_solution_for_task(uid, top, j)
    msg = _txt(
        lang,
        "Решение сгенерировано нейросетью, действительно ничего не понятно 😢\nНапиши в чат, там тебе помогут по-человечески. А я пока подумаю, как сделать решение понятнее.",
        "The solution is AI-generated, so it can indeed be unclear 😢 Write in the chat, people will help you there. And I will think about how to make the solution clearer",
    )
    await call.message.answer(
        msg,
        reply_markup=_kb_soldn_after_ai_unclear(
            lang, topiclink, f"math_next_{top_idx}_{j}", show_explain=show_explain
        ),
    )


@router.callback_query(F.data.startswith("sol_top_"))
async def on_topic_solution_show(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    if not _has_solution_for_lang(q, lang):
        await call.message.answer(
            _txt(lang, "Решения на выбранном языке нет.", "There is no solution in your selected language."),
        )
        return
    log(call.from_user, ["solution_show_topic", top, j, str(q.get("id") or "")])
    await call.message.answer(
        _solution_text_for_lang(q, lang),
        reply_markup=_kb_solution_feedback_topic(top_idx, j, lang),
    )


@router.callback_query(F.data.startswith("solup_top_"))
async def on_topic_solution_up(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    log(call.from_user, ["solution_vote_up_topic", top, j, str(q.get("id") or "")])
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=_txt(lang, "Следующая задача", "Next task"),
            callback_data=f"next_{top}_{j+1}",
        )
    )
    await call.message.answer(_txt(lang, "Отлично", "Great"), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("soldn_top_"))
async def on_topic_solution_down(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.message.answer("Ошибка формата.")
        return
    try:
        top_idx = int(parts[2])
        j = int(parts[3])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    if top_idx < 0 or top_idx >= len(topics):
        await call.message.answer("Вопрос не найден.")
        return
    top = topics[top_idx]
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    log(call.from_user, ["solution_vote_down_topic", top, j, str(q.get("id") or "")])
    topiclink = _discussion_url_for_task(q, lang)
    uid = call.from_user.id
    show_explain = not _user_has_viewed_llm_solution_for_task(uid, top, j)
    msg = _txt(
        lang,
        "Решение сгенерировано нейросетью, действительно ничего не понятно 😢\nНапиши в чат, там тебе помогут по-человечески. А я пока подумаю, как сделать решение понятнее.",
        "The solution is AI-generated, so it can indeed be unclear 😢 Write in the chat, people will help you there. And I will think about how to make the solution clearer",
    )
    await call.message.answer(
        msg,
        reply_markup=_kb_soldn_after_ai_unclear(
            lang, topiclink, f"next_{top}_{j+1}", show_explain=show_explain
        ),
    )


@router.callback_query(F.data.startswith("sol_sub_"))
async def on_sub_solution_show(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 5:
        await call.message.answer("Ошибка формата.")
        return
    try:
        topic_idx = int(parts[2])
        sub_idx = int(parts[3])
        k = int(parts[4])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    top, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if top is None:
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    if not _has_solution_for_lang(q, lang):
        await call.message.answer(
            _txt(lang, "Решения на выбранном языке нет.", "There is no solution in your selected language."),
        )
        return
    log(call.from_user, ["solution_show_sub", top, j, str(q.get("id") or "")])
    await call.message.answer(
        _solution_text_for_lang(q, lang),
        reply_markup=_kb_solution_feedback_sub(topic_idx, sub_idx, k, lang),
    )


@router.callback_query(F.data.startswith("solup_sub_"))
async def on_sub_solution_up(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 5:
        await call.message.answer("Ошибка формата.")
        return
    try:
        topic_idx = int(parts[2])
        sub_idx = int(parts[3])
        k = int(parts[4])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    top, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if top is None:
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    log(call.from_user, ["solution_vote_up_sub", top, j, str(q.get("id") or "")])
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=_txt(lang, "Следующая задача", "Next task"),
            callback_data=f"next_sub_{topic_idx}_{sub_idx}_{k+1}",
        )
    )
    await call.message.answer(_txt(lang, "Отлично", "Great"), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("soldn_sub_"))
async def on_sub_solution_down(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    parts = call.data.split("_")
    if len(parts) < 5:
        await call.message.answer("Ошибка формата.")
        return
    try:
        topic_idx = int(parts[2])
        sub_idx = int(parts[3])
        k = int(parts[4])
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата.")
        return
    top, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if top is None:
        await call.message.answer("Вопрос не найден.")
        return
    q = kapibara[top][j]
    log(call.from_user, ["solution_vote_down_sub", top, j, str(q.get("id") or "")])
    topiclink = _discussion_url_for_task(q, lang)
    uid = call.from_user.id
    show_explain = not _user_has_viewed_llm_solution_for_task(uid, top, j)
    msg = _txt(
        lang,
        "Решение сгенерировано нейросетью, действительно ничего не понятно 😢\nНапиши в чат, там тебе помогут по-человечески. А я пока подумаю, как сделать решение понятнее.",
        "The solution is AI-generated, so it can indeed be unclear 😢 Write in the chat, people will help you there. And I will think about how to make the solution clearer",
    )
    await call.message.answer(
        msg,
        reply_markup=_kb_soldn_after_ai_unclear(
            lang,
            topiclink,
            f"next_sub_{topic_idx}_{sub_idx}_{k+1}",
            show_explain=show_explain,
        ),
    )


@router.callback_query(F.data == "llm_explain_last")
async def on_llm_explain_last(call: CallbackQuery):
    """Подробное объяснение от LLM для последней показанной задачи (после 👎 к решению из data.txt)."""
    await call.answer()
    user_id = call.from_user.id
    lang = await _get_user_lang(call.from_user)
    access_token = _get_llm_access_token()
    if not access_token:
        await call.message.answer(
            "LLM token is not set. Set env var `LLM_TOKEN` to enable chat Q&A."
        )
        return
    user_text = _txt(
        lang,
        "Объясни решение этой задачи максимально подробно по шагам.",
        "Explain the solution to this problem step by step in full detail.",
    )
    exam_lang = await _get_user_exam_lang_by_id(user_id)
    ks_snap = _last_seen_kapibara_question.get(user_id)
    llm_task_snapshot: tuple[str, int] | None = None
    if ks_snap and len(ks_snap) >= 2:
        llm_task_snapshot = (ks_snap[0], int(ks_snap[1]))
    try:
        async with ChatActionSender(bot=bot, chat_id=call.message.chat.id, action="typing"):
            answer_text = await asyncio.to_thread(
                _do_llm_request,
                LLM_CONTEXT_LAST_TASK,
                user_text,
                user_id,
                call.message.chat.id,
                call.from_user,
                lang,
                access_token,
                exam_lang,
            )
    except Exception as e:
        logging.error(f"LLM request error (llm_explain_last): {e}")
        await call.message.answer(
            _txt(
                lang,
                "Ошибка при обращении к LLM. Попробуйте позже.",
                "Something went wrong while contacting the LLM. Please try again later.",
            )
        )
        return
    if answer_text == "__NO_LAST_TASK__":
        await call.message.answer(
            _txt(
                lang,
                "Сначала откройте задачу в боте (тема, подтема, экзамен или режим «вся математика»), затем снова нажмите кнопку.",
                "Open a task in the bot first, then tap the button again.",
            )
        )
        return
    if answer_text:
        try:
            ans_one_line = (answer_text or "").replace("\n", " ").strip()
            if len(ans_one_line) > 2000:
                ans_one_line = ans_one_line[:2000] + "..."
            log(
                call.from_user,
                [
                    "llm_explain_last_answer",
                    str(call.message.chat.id),
                    f"len={len(answer_text or '')}",
                    ans_one_line,
                ],
            )
        except Exception:
            pass
        if llm_task_snapshot:
            _mark_llm_solution_shown(user_id, llm_task_snapshot[0], llm_task_snapshot[1])
        fb = _kb_llm_task_feedback(user_id, lang)
        await call.message.answer(answer_text, reply_markup=fb)


@router.callback_query(F.data == "back_start")
async def back_to_start(call: CallbackQuery):
    """Возврат в главное меню."""
    await call.answer()
    kb = await start_kb(call.from_user.id)
    lang = await _get_user_lang(call.from_user)
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(_txt(lang, "Выберите тему:", "Choose topic:"), reply_markup=kb)


@router.callback_query(F.data == "menu_topics")
async def on_menu_topics(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    title = "Математика - задачи по темам:" if lang == "ru" else "Math - tasks by topic:"
    await call.message.answer(title, reply_markup=topics_menu_kb(lang))


@router.callback_query(F.data == "menu_physics")
async def on_menu_physics(call: CallbackQuery):
    """Отдельный вход в физику с прежней логикой показа задач по теме."""
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    top = "physics"
    if top not in kapibara or not kapibara[top]:
        kb = await start_kb(call.from_user.id)
        await call.message.answer(_txt(lang, "Задач по физике пока нет.", "No physics tasks yet."), reply_markup=kb)
        return

    showvideo = 1
    if call.from_user.username == "evangecalista":
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (call.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == "stepik":
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0

    uid = call.from_user.id
    _topic_clear_session(uid, top)
    _topic_linear_active[(uid, top)] = True
    await _get_user_exam_lang_by_id(call.from_user.id)
    log(call.from_user, ["menu_physics", "next", top, 0])
    async with ChatActionSender(bot=bot, chat_id=uid, action="typing"):
        await _deliver_topic_question_message(call, top, 0, showvideo)


@router.callback_query(F.data == "menu_chemistry")
async def on_menu_chemistry(call: CallbackQuery):
    """Отдельный вход в химию (аналогично физике)."""
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    top = "chemistry"
    if top not in kapibara or not kapibara[top]:
        kb = await start_kb(call.from_user.id)
        await call.message.answer(_txt(lang, "Задач по химии пока нет.", "No chemistry tasks yet."), reply_markup=kb)
        return

    showvideo = 1
    if call.from_user.username == "evangecalista":
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (call.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == "stepik":
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0

    uid = call.from_user.id
    _topic_clear_session(uid, top)
    _topic_linear_active[(uid, top)] = True
    await _get_user_exam_lang_by_id(call.from_user.id)
    log(call.from_user, ["menu_chemistry", "next", top, 0])
    async with ChatActionSender(bot=bot, chat_id=uid, action="typing"):
        await _deliver_topic_question_message(call, top, 0, showvideo)


@router.callback_query(F.data == "menu_exams")
async def on_menu_exams(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    title = "Выберите экзамен:" if lang == "ru" else "Choose exam:"
    await call.message.answer(title, reply_markup=exams_menu_kb(lang))


@router.callback_query(F.data.in_(["set_lang_ru", "set_lang_en"]))
async def on_set_language(call: CallbackQuery):
    await call.answer()
    new_lang = "ru" if call.data == "set_lang_ru" else "en"
    old_lang = await _get_user_lang(call.from_user)
    save_status = "no_db"
    if db_conn:
        try:
            await db.set_user_language(db_conn, call.from_user.id, new_lang)
            save_status = "saved"
        except Exception as e:
            save_status = "save_error"
            logging.error(f"Ошибка сохранения языка для пользователя {call.from_user.id}: {e}")

    _sync_log_lang_ui(call.from_user.id, new_lang)
    log(call.from_user, ["set_language", old_lang, new_lang, save_status])

    lang = await _get_user_lang(call.from_user)
    # Для новых пользователей в сценарии /start сначала выбираем язык экзамена.
    if call.from_user.id in _pending_exam_language_selection:
        await call.message.answer(
            _txt(lang, "Выберите язык экзамена:", "Choose exam language:"),
            reply_markup=_exam_language_kb(),
        )
        return

    # После смены языка запускаем тот же пользовательский сценарий, что и при /start:
    # показываем приветствие и главное меню с актуальным языком.
    kb = await start_kb(call.from_user.id)
    if lang.startswith("ru"):
        greet = """Привет! Я бот для подготовки к CSCA. 
Помогу сдать экзамен на отлично! Проходи тестовые экзамены, узнавай свои баллы или тренируйся по любой теме. Не знаешь, как решать? Встроенные справочные материалы и чат с обсуждением задач всегда к твоим услугам.

Бот создан с помощью нейросети. Нашел ошибку? Пиши https://t.me/csca_math_exam/107
"""
    else:
        greet = "Hi!! I'm your CSCA math exam preparation bot, ready to help you pass with confidence. You can take full-length practice tests to evaluate your score or focus on specific topics for targeted practice. I'll guide you step by step until you're fully prepared for exam day."
    await call.message.answer(greet, reply_markup=kb)


@router.callback_query(F.data.in_(["set_exam_lang_en", "set_exam_lang_zh"]))
async def on_set_exam_language(call: CallbackQuery):
    await call.answer()
    exam_lang = "en" if call.data == "set_exam_lang_en" else "zh"
    save_status = "no_db"
    if db_conn:
        try:
            await db.set_user_exam_language(db_conn, call.from_user.id, exam_lang)
            save_status = "saved"
        except Exception as e:
            save_status = "save_error"
            logging.error(f"Ошибка сохранения языка экзамена для пользователя {call.from_user.id}: {e}")
    _pending_exam_language_selection.discard(call.from_user.id)
    _sync_log_lang_exam(call.from_user.id, exam_lang)
    log(call.from_user, ["set_exam_language", exam_lang, save_status])
    logging.info(
        "exam_language_selected user_id=%s username=%s choice=%s save=%s callback=%s",
        call.from_user.id,
        getattr(call.from_user, "username", None),
        exam_lang,
        save_status,
        call.data,
    )

    kb = await start_kb(call.from_user.id)
    lang = await _get_user_lang(call.from_user)
    await call.message.answer(
        _txt(
            lang,
            f"Язык экзамена — {'English' if exam_lang == 'en' else '中文'}",
            f"Exam language — {'English' if exam_lang == 'en' else '中文'}",
        ),
        reply_markup=kb,
    )


@router.callback_query(F.data == "menu_exam_language")
async def on_menu_exam_language(call: CallbackQuery):
    await call.answer()
    lang = await _get_user_lang(call.from_user)
    await call.message.answer(
        _txt(lang, "Выберите язык экзамена:", "Choose exam language:"),
        reply_markup=_exam_language_kb(),
    )


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
async def _should_redirect_exam_access_to_pay(user_id: int) -> bool:
    """
    Единая проверка доступа к экзаменам (как в Mock Exam сейчас).
    Возвращает True, если нужно показать pay().
    """
    invites_count = len(invite_relations.get(user_id, set()))
    paid_access = False
    user_created_at = None
    total_answered = 0
    if db_conn:
        try:
            paid_access = await db.user_has_exam_access_by_payment(db_conn, user_id)
            user_created_at = await db.get_user_created_at(db_conn, user_id)
            stats = await db.get_user_stats(db_conn, user_id)
            total_answered = stats.get("total_answered", 0)
        except Exception as e:
            logging.error(f"Ошибка проверки доступа к экзаменам для пользователя {user_id}: {e}")

    no_privileges = (
        str(user_id) not in stepik
        and str(user_id) not in cscagroup
        and invites_count < 3
        and not paid_access
    )

    meets_activity_limits = False
    if user_created_at:
        try:
            from datetime import datetime, timedelta

            created_dt = datetime.fromisoformat(user_created_at)
            if datetime.now() - created_dt > timedelta(days=3) and total_answered > 30:
                meets_activity_limits = True
        except Exception as e:
            logging.error(f"Ошибка разбора created_at для пользователя {user_id}: {e}")

    return no_privileges and meets_activity_limits


@router.callback_query(F.data.in_(["exam_start_jan", "exam_start_dec", "exam_start_mar"]))
async def on_exam_start(call: CallbackQuery):
    """Старт экзамена по типу: jan, dec, mar."""
    await call.answer()
    exam_type = call.data.replace("exam_start_", "")
    cfg = _exam_cfg(exam_type)
    if not cfg:
        return
    user_id = call.from_user.id
    if await _should_redirect_exam_access_to_pay(user_id):
        log(call.from_user, [f"{cfg['id']}_reject"])
        await pay(call.from_user)
        return
    lang = await _get_user_lang(call.from_user)
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
            _txt(lang, f"Режим «{cfg['short_ru']}».", f"Mode \"{cfg['short_en']}\".")
            + "\n\n"
            + _format_exam_stats_line_by_type(state, exam_type, lang)
            + "\n\n"
            + _txt(lang, "Выберите действие:", "Choose action:")
        )
        async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
            await call.message.answer(msg, reply_markup=_inline_kb_exam_entry_choice_by_type(exam_type, lang))
        return
    total = len(questions)
    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        await call.message.answer(_txt(
            lang,
            f"Режим «{cfg['short_ru']}».\n"
            f"Всего {total} задач. Второй раз решить одну и ту же задачу нельзя.\n\n"
            "Разборы всех задач в https://stepik.org/a/268161\n"
            "Используйте промокод CSCABOT для скидки.",
            f"Mode \"{cfg['short_en']}\".\n"
            f"There are {total} tasks. You cannot solve the same task twice.\n\n"
            "Solutions for all tasks are available at https://stepik.org/a/268161\n"
            "Use promo code CSCABOT for a discount."
        ))
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
    if await _should_redirect_exam_access_to_pay(user_id):
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
    n_val, top, j = questions[idx]
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
        state["correct_difficulty"] = state.get("correct_difficulty", 0) + _exam_points_for_n(int(n_val or 0))

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

    log(call.from_user, [cfg["id"], idx, ans_id, ansok, _exam_log_task_id(q)])

    lang = await _get_user_lang(call.from_user)
    result_msg = _correct_phrase(lang) if ansok else _txt(lang, "Неправильно.", "Incorrect.")
    correct_now = state.get("correct_count", 0)
    total_q = len(questions)
    total_d = cfg["total_difficulty"]
    k_now = (state.get("correct_difficulty", 0) / total_d * 100) if total_d else 0.0
    stats_ru = f"Сейчас по экзамену: {correct_now} из {total_q} верно, набранный балл {k_now:.2f}."
    stats_en = f"Current exam stats: {correct_now} out of {total_q} correct, score {k_now:.2f}."
    stats_msg = stats_ru if lang.startswith("ru") else stats_en

    # Если ответ неправильный и есть подсказка — отправляем тремя сообщениями:
    # 1) Неправильно  2) hint по языку  3) статистика
    if not ansok and (q.get("solution_en") or q.get("solution_ru")):
        solution = ""
        if lang.startswith("ru"):
            solution = (q.get("solution_ru") or "").strip() or (q.get("solution_en") or "").strip()
        else:
            solution = (q.get("solution_en") or "").strip() or (q.get("solution_ru") or "").strip()

        await call.message.answer(_txt(lang, "Неправильно.", "Incorrect."))
        if solution:
            await call.message.answer(solution)
        await call.message.answer(stats_msg)
    else:
        full_msg = result_msg + "\n" + stats_msg

    next_idx = _find_next_exam_index_by_type(state, exam_type)
    if next_idx is None:
        if ansok or not (q.get("solution_en") or q.get("solution_ru")):
            await call.message.answer(full_msg)
        await _send_exam_summary_by_type(call, user_id, exam_type)
    else:
        if ansok or not (q.get("solution_en") or q.get("solution_ru")):
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
        # В Mock Exam баллы считаем по n позиции (как в exam_questions)
        if 0 <= idx < len(exam_questions):
            n_val, _, _ = exam_questions[idx]
            state["correct_difficulty"] = state.get("correct_difficulty", 0) + _exam_points_for_n(int(n_val or 0))

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
    log(call.from_user, [EXAM_MOCK_ID, idx, ans_id, ansok, _exam_log_task_id(q)])

    lang = await _get_user_lang(call.from_user)
    result_msg = _correct_phrase(lang) if ansok else _txt(lang, "Неправильно.", "Incorrect.")
    correct_now = state.get("correct_count", 0)
    total_q = len(mock_questions)
    k_now = (state.get("correct_difficulty", 0) / MOCK_TOTAL_DIFFICULTY * 100) if MOCK_TOTAL_DIFFICULTY else 0.0
    stats_ru = f"Сейчас по экзамену: {correct_now} из {total_q} верно, набранный балл {k_now:.2f}."
    stats_en = f"Current exam stats: {correct_now} out of {total_q} correct, score {k_now:.2f}."
    stats_msg = stats_ru if lang.startswith("ru") else stats_en
    full_msg = result_msg + "\n" + stats_msg

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
    _topic_linear_active[(call.from_user.id, top)] = False

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

    lang = await _get_user_lang(call.from_user)
    question_num = _txt(lang, "Случайная задача\n\n", "Random task\n\n")
    difficulty = k.get("difficulty")
    if isinstance(difficulty, int) and 1 <= difficulty <= 5:
        stars = "★" * difficulty + "☆" * (5 - difficulty)
        diff_line = _txt(lang, f"Сложность: {stars}\n", f"Difficulty: {stars}\n")
    else:
        diff_line = ""
    exam_lang = await _get_user_exam_lang_by_id(call.from_user.id)
    question_text = question_num + diff_line + _full_task_question_text(k, exam_lang)
    opts_n = _task_options_for_display(k, exam_lang)
    order = _option_display_indices(
        top, k, len(opts_n), opts_for_shuffle_check=opts_n
    )
    reply = inline_kb(
        top, j, showvideo, lang_code=lang, option_order=order, exam_lang=exam_lang
    )
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
    lang = await _get_user_lang(call.from_user)
    kb = subtopic_kb(topic_idx, lang)
    title = _topic_display(topic, lang)
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(
            f"📂 {title}\n" + _txt(lang, "Выберите подтему:", "Choose subtopic:"),
            reply_markup=kb,
        )


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
    uid = call.from_user.id
    _sub_clear_session(uid, topic_idx, sub_idx)
    _sub_linear_active[_sub_key(uid, topic_idx, sub_idx)] = True
    showvideo = 1
    if call.from_user.username == "evangecalista":
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (call.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == "stepik":
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await _deliver_subtopic_question_message(call, topic_idx, sub_idx, 0, showvideo)


@router.callback_query(F.data.startswith("next_sub_"))
async def on_next_sub(call: CallbackQuery):
    """Следующий вопрос в режиме подтемы (включая повторы после прохода)."""
    await call.answer()
    parts = call.data.split("_")
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
    uid = call.from_user.id
    sk = _sub_key(uid, topic_idx, sub_idx)
    n = len(j_list)
    lang_code = await _get_user_lang(call.from_user)

    if str(call.from_user.id) == "7567696331":
        log(call.from_user, ["blocked"])
        return

    showvideo = 1
    if call.from_user.username == "evangecalista":
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute("SELECT source FROM users WHERE id = ?", (call.from_user.id,))
            row = await cursor.fetchone()
            if row and row[0] == "stepik":
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0

    if k < n:
        _sub_linear_active[sk] = True
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            await _deliver_subtopic_question_message(call, topic_idx, sub_idx, k, showvideo)
        return

    review = _sub_build_review_set(uid, topic_idx, sub_idx, n)
    if review:
        _sub_in_review[sk] = True
        _sub_linear_active[sk] = True
        last = _sub_last_review_shown.get(sk)
        k_show = _pick_next_review_index(review, last)
        if k_show is None:
            _sub_clear_session(uid, topic_idx, sub_idx)
            async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
                kb = await start_kb(call.from_user.id)
                await call.message.answer(
                    _txt(
                        lang_code,
                        "Все задачи по этой подтеме выполнены.\n\nВыберите другую подтему или тему.",
                        "All tasks in this subtopic are done.\n\nChoose another subtopic or topic.",
                    ),
                    reply_markup=kb,
                )
            return
        _sub_last_review_shown[sk] = k_show
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            await _deliver_subtopic_question_message(call, topic_idx, sub_idx, k_show, showvideo)
        return

    if _sub_in_review.pop(sk, None):
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            kb = await start_kb(call.from_user.id)
            await call.message.answer(
                _txt(
                    lang_code,
                    "Все задачи по этой подтеме выполнены.\n\nВыберите другую подтему или тему.",
                    "All tasks in this subtopic are done.\n\nChoose another subtopic or topic.",
                ),
                reply_markup=kb,
            )
        _sub_clear_session(uid, topic_idx, sub_idx)
        return

    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        kb = await start_kb(call.from_user.id)
        await call.message.answer(
            "Задачи по этой подтеме закончились. Выберите другую подтему или тему.",
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith('qst_sub_'))
async def on_answer_sub(call: CallbackQuery):
    """Обработка ответа в режиме подтемы."""
    correct_h = {'A': '0', 'B': '1', 'C': '2', 'D': '3', 'E': '4'}
    await call.answer()
    lang_code = await _get_user_lang(call.from_user)
    await _get_user_exam_lang_by_id(call.from_user.id)
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
    uid = call.from_user.id
    sk = _sub_key(uid, topic_idx, sub_idx)
    linear = _sub_linear_active.get(sk, False)
    _sub_record_answer(uid, topic_idx, sub_idx, k, linear, bool(ansok))

    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                call.from_user.id,
                topic,
                j,
                int(ans_id) if ans_id.isdigit() else 0,
                ansok == 1,
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа в БД: {e}")

    j_list = questions_by_topic_subtopic.get((topic, subtopics_by_topic[topic][sub_idx]), [])
    n_sub = len(j_list)
    review = _sub_build_review_set(uid, topic_idx, sub_idx, n_sub)
    in_review = _sub_in_review.get(sk, False)

    if ansok:
        msg_text = _correct_phrase_training(lang_code)
        if in_review and not review:
            reply = None
            send_kb = await start_kb(uid)
            _sub_clear_session(uid, topic_idx, sub_idx)
        else:
            reply = inline_kb_next_sub(topic_idx, sub_idx, k, lang_code, uid)
            send_kb = None
    else:
        msg_text = await _wrong_answer_training_message(uid, lang_code)
        reply = inline_kb_explain_sub(topic_idx, sub_idx, k, lang_code=lang_code)
        send_kb = None
    log(call.from_user, [topic, j, ans_id, ansok, str(q.get("id") or "")])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        if reply:
            await call.message.answer(msg_text, reply_markup=reply)
        else:
            await call.message.answer(
                msg_text + "\n" + _txt(lang_code, "Задачи по подтеме закончились.", "Subtopic tasks are finished."),
                reply_markup=send_kb,
            )


@router.callback_query(F.data.startswith('qst_'))
async def on_answer_topic(call: CallbackQuery):
    correct_h = {"A": "0", "B": "1", "C": "2", "D": "3", "E": "4"}
    await call.answer()
    lang_code = await _get_user_lang(call.from_user)
    await _get_user_exam_lang_by_id(call.from_user.id)
    try:
        top, j, ans_id = _parse_qst_topic_callback(call.data)
    except (ValueError, IndexError):
        await call.message.answer("Ошибка формата. Выберите тему заново.")
        return
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден. Выберите тему заново.")
        return
    uid = call.from_user.id
    key = (uid, top)
    linear = _topic_linear_active.get(key, False)
    k = kapibara[top][j]
    correct = correct_h.get(k["answer"], "")
    try:
        ans_id_int = int(ans_id)
    except ValueError:
        await call.message.answer("Ошибка формата. Выберите тему заново.")
        return
    ansok = 1 if correct == ans_id else 0
    _topic_record_answer(uid, top, j, linear, bool(ansok))

    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                uid,
                top,
                j,
                ans_id_int,
                ansok == 1,
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа в БД: {e}")

    n = len(kapibara[top])
    review = _topic_build_review_set(uid, top, n)
    in_review = _topic_in_review.get(key, False)

    if ansok:
        msg_text = _correct_phrase_training(lang_code)
        if in_review and not review:
            reply = None
            _topic_clear_session(uid, top)
            msg_text += "\n\n" + _txt(
                lang_code,
                "Все задачи по этой теме выполнены.",
                "All tasks in this topic are done.",
            )
            async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
                kb = await start_kb(uid)
                await call.message.answer(msg_text, reply_markup=kb)
            log(call.from_user, [top, j, ans_id, ansok, str(k.get("id") or "")])
            return
        else:
            reply = inline_kb_next(top, j, lang_code, uid)
    else:
        msg_text = await _wrong_answer_training_message(uid, lang_code)
        reply = inline_kb_explain(top, j, k, lang_code=lang_code)

    log(call.from_user, [top, j, ans_id, ansok, str(k.get("id") or "")])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(msg_text, reply_markup=reply)


@router.callback_query(F.data.startswith('hint_sub_'))
async def on_hint_sub(call: CallbackQuery):
    """Показывает картинку подсказки (если есть) и заново выводит задачу подтемы."""
    await call.answer()
    user_id = call.from_user.id
    if user_id == 7567696331:
        await _refresh_log_lang_cache(call.from_user)
        log(call.from_user, ["hint_sub_blocked"])
        return

    parts = call.data.replace('hint_sub_', '').split('_')
    if len(parts) < 3:
        await call.message.answer("Ошибка формата.")
        return
    try:
        topic_idx = int(parts[0])
        sub_idx = int(parts[1])
        k = int(parts[2])
    except ValueError:
        await call.message.answer("Ошибка формата.")
        return

    topic, j = _get_subtopic_j(topic_idx, sub_idx, k)
    if topic is None or j is None:
        await call.message.answer("Вопрос не найден.")
        return

    q = kapibara[topic][j]
    lang_code = await _get_user_lang(call.from_user)
    await _get_user_exam_lang_by_id(call.from_user.id)
    skh = _sub_key(user_id, topic_idx, sub_idx)
    if _sub_linear_active.get(skh):
        _register_sub_question_displayed(user_id, topic_idx, sub_idx, k, True)
    hint_paths = _get_hint_image_path(q, lang_code)
    log(call.from_user, ["hint_sub_show", topic, sub_idx, k, "hint_found" if hint_paths else "hint_not_found", str(len(hint_paths)) + " hints"])

    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        for hp in hint_paths:
            await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(hp))
        if q.get("img"):
            photo_path = os.path.join(DATA_DIR, "images", q["img"])
            await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))

        exam_lang = await _get_user_exam_lang_by_id(user_id)
        question_text = _full_task_question_text(q, exam_lang)
        opts_n = _task_options_for_display(q, exam_lang)
        order = _option_display_indices(
            topic, q, len(opts_n), opts_for_shuffle_check=opts_n
        )
        reply = inline_kb_sub(
            topic_idx,
            sub_idx,
            k,
            showvideo=1,
            lang_code=lang_code,
            option_order=order,
            exam_lang=exam_lang,
        )
        await call.message.answer(question_text, reply_markup=reply)
        _record_last_seen_question(user_id, topic, j, order)


@router.callback_query(F.data.startswith('hint_') & ~F.data.startswith('hint_sub_'))
async def on_hint(call: CallbackQuery):
    """Показывает картинку подсказки (если есть) и заново выводит задачу."""
    if call.data.startswith('hint_sub_'):
        return

    await call.answer()
    user_id = call.from_user.id
    if user_id == 7567696331:
        await _refresh_log_lang_cache(call.from_user)
        log(call.from_user, ["hint_blocked"])
        return

    raw = call.data.replace('hint_', '')
    parts = raw.split('_')
    if len(parts) < 2:
        await call.message.answer("Ошибка формата.")
        return
    try:
        j = int(parts[-1])
        top_raw = "_".join(parts[:-1])
    except ValueError:
        await call.message.answer("Ошибка формата.")
        return

    top = TOPIC_ALIASES.get(top_raw, top_raw)
    if top not in kapibara or j < 0 or j >= len(kapibara[top]):
        await call.message.answer("Вопрос не найден.")
        return

    q = kapibara[top][j]
    lang_code = await _get_user_lang(call.from_user)
    await _get_user_exam_lang_by_id(user_id)
    if _topic_linear_active.get((user_id, top)):
        _register_topic_question_displayed(user_id, top, j, True)
    hint_paths = _get_hint_image_path(q, lang_code)
    log(call.from_user, ["hint_show", top, j, "hint_found" if hint_paths else "hint_not_found", str(len(hint_paths)) + " hints"])

    async with ChatActionSender(bot=bot, chat_id=user_id, action="typing"):
        for hp in hint_paths:
            await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(hp))
        if q.get("img"):
            photo_path = os.path.join(DATA_DIR, "images", q["img"])
            await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))

        exam_lang = await _get_user_exam_lang_by_id(user_id)
        question_text = _full_task_question_text(q, exam_lang)
        opts_n = _task_options_for_display(q, exam_lang)
        order = _option_display_indices(
            top, q, len(opts_n), opts_for_shuffle_check=opts_n
        )
        reply = inline_kb(
            top,
            j,
            showvideo=1,
            lang_code=lang_code,
            option_order=order,
            exam_lang=exam_lang,
        )
        await call.message.answer(question_text, reply_markup=reply)
        _record_last_seen_question(user_id, top, j, order)

@router.message(Command("start"))
async def on_start_command(message: types.Message):
    # Извлекаем аргумент команды (текст после /start)
    start_text = None
    if message.text and len(message.text.split()) > 1:
        start_text = ' '.join(message.text.split()[1:])
    user_status =  '' #await bot.get_chat_member(chat_id="@csca_math_exam", user_id=message.chat.id)
    # Для сценария первого входа определяем, есть ли пользователь в БД до upsert.
    is_new_user = False
    if db_conn:
        try:
            cursor = await db_conn.execute("SELECT 1 FROM users WHERE id = ? LIMIT 1", (message.from_user.id,))
            is_new_user = (await cursor.fetchone()) is None
        except Exception as e:
            logging.error(f"Ошибка проверки нового пользователя {message.from_user.id}: {e}")

    # Сохраняем/обновляем пользователя в БД
    if db_conn:
        try:
            await db.ensure_user(db_conn, message.from_user, start_text)
            # Если пользователь пришёл по invite-ссылке, обновляем карту приглашений
            _register_invite_for_new_user(message.from_user.id, message.from_user.username, start_text)

            # Если пользователь перешёл по собственной ссылке-приглашению, показываем статистику приглашённых
            if start_text and start_text.strip().lower().startswith("invite"):
                from_code = start_text.strip()[6:]  # после 'invite'
                own_code = _make_invite_code(message.from_user.username, message.from_user.id)
                if from_code == own_code:
                    invited_ids = invite_relations.get(message.from_user.id, set()) or set()
                    invited_count = len(invited_ids)
                    total_answers_by_invited = 0
                    if invited_ids:
                        try:
                            for invited_id in invited_ids:
                                stats = await db.get_user_stats(db_conn, invited_id)
                                total_answers_by_invited += stats.get("total_answered", 0)
                        except Exception as e:
                            logging.error(f"Ошибка получения статистики приглашённых для пользователя {message.from_user.id}: {e}")
                    # Сообщение пользователю о его вкладе
                    lang = await _get_user_lang(message.from_user)
                    if lang.startswith("ru"):
                        text = (
                            "📊 Ваша пригласительная статистика:\n\n"
                            f"По вашей ссылке пришло новых пользователей: {invited_count}.\n"
                            f"Суммарно они решили задач: {total_answers_by_invited}."
                        )
                    else:
                        text = (
                            "📊 Your invitation statistics:\n\n"
                            f"New users who joined via your link: {invited_count}.\n"
                            f"Total tasks they have solved: {total_answers_by_invited}."
                        )
                    await message.answer(text)

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
    
    lang = await _get_user_lang(message.from_user)
    await _get_user_exam_lang_by_id(message.from_user.id)
    if message.chat.id != -1003634233318:
        log(message.from_user, ['start', message.text])
    log(message.from_user, ['status', str(user_status)])

    if lang.startswith("ru"):
        greet = """Привет! Я бот для подготовки к CSCA. 
Помогу сдать экзамен на отлично! Проходи тестовые экзамены, узнавай свои баллы или тренируйся по любой теме. Запутался в решении? Встроенные справочные материалы и чат с обсуждением задач всегда к твоим услугам.

Бот создан с помощью нейросети. Нашел ошибку? Пиши https://t.me/csca_math_exam/107

"""
    else:
        greet = "Hi!! I'm your CSCA math exam preparation bot, ready to help you pass with confidence. You can take full-length practice tests to evaluate your score or focus on specific topics for targeted practice. I'll guide you step by step until you're fully prepared for exam day."
  
    if is_new_user:
        _pending_exam_language_selection.add(message.from_user.id)
        # На первом входе: приветствие + кнопка переключения интерфейса, затем выбор языка экзамена.
        await message.answer(greet, reply_markup=_language_switch_kb(lang))
        await message.answer(
            _txt(lang, "Выберите язык экзамена:", "Choose exam language:"),
            reply_markup=_exam_language_kb(),
        )
    else:
        kb = await start_kb(message.from_user.id)
        await message.answer(greet, reply_markup=kb)
   
   # await message.answer("Это тестовая версия бота. Нашел ошибку? Есть идея? Пиши @csca_math_exam или прямо здесь.", reply_markup=start_kb()) 
   # await message.answer("Видео-разборы задач в группе https://t.me/milgecru/385") 
    
   
    
    #j=0
    #k = kapibara[j]
   # await  message.answer(k["english"]+"\n" + k["chinese"],  reply_markup=inline_kb(j))


@router.callback_query(F.data.startswith('explain'))
async def on_explain(call: CallbackQuery):
    
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
    await _refresh_log_lang_cache(call.from_user)
    log(call.from_user, ['explain', top, j])
    # Отключаем показ ссылок на видео, но не ломаем старые callback'и
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer("Видео‑разборы временно недоступны.")
        
       


@router.callback_query(F.data.startswith("next") & ~F.data.startswith("next_sub_"))
async def on_next_question(call: CallbackQuery):
    if call.from_user.id == 7567696331:
        await call.answer()
        await _refresh_log_lang_cache(call.from_user)
        log(call.from_user, ["next_blocked"])
        return

    await call.answer()
    await _refresh_log_lang_cache(call.from_user)

    showvideo = 1
    user = call.from_user.username
    if user in ["evangecalista"]:
        showvideo = 0
    elif db_conn:
        try:
            cursor = await db_conn.execute(
                "SELECT source FROM users WHERE id = ?",
                (call.from_user.id,),
            )
            row = await cursor.fetchone()
            if row and row[0] == "stepik":
                showvideo = 0
        except Exception:
            pass
    elif str(call.from_user.id) in stepik:
        showvideo = 0

    try:
        top, j = _parse_next_topic_callback(call.data)
    except (ValueError, IndexError):
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            kb = await start_kb(call.from_user.id)
            await call.message.answer("Ошибка формата. Выберите тему из меню.", reply_markup=kb)
        return

    uid = call.from_user.id
    key = (uid, top)
    log(call.from_user, ["next", top, j])

    if j == 7:
        message = await makeinvite(call.from_user)
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            log(call.from_user, ["invite"])
            await call.message.answer(message)

    if top not in kapibara:
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            kb = await start_kb(call.from_user.id)
            await call.message.answer("Тема не найдена. Выберите тему из меню.", reply_markup=kb)
        return

    n = len(kapibara[top])
    lang_code = await _get_user_lang(call.from_user)

    if j == 0:
        _topic_clear_session(uid, top)

    if j < n:
        _topic_linear_active[key] = True
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            await _deliver_topic_question_message(call, top, j, showvideo)
        return

    review = _topic_build_review_set(uid, top, n)
    if review:
        _topic_in_review[key] = True
        _topic_linear_active[key] = True
        last = _topic_last_review_shown.get(key)
        j_show = _pick_next_review_index(review, last)
        if j_show is None:
            _topic_clear_session(uid, top)
            async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
                kb = await start_kb(call.from_user.id)
                await call.message.answer(
                    _txt(
                        lang_code,
                        "Все задачи по этой теме выполнены.\n\nВыберите другую тему или подтему.",
                        "All tasks in this topic are done.\n\nChoose another topic or subtopic.",
                    ),
                    reply_markup=kb,
                )
            return
        _topic_last_review_shown[key] = j_show
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            await _deliver_topic_question_message(call, top, j_show, showvideo)
        return

    if _topic_in_review.pop(key, None):
        async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
            kb = await start_kb(call.from_user.id)
            await call.message.answer(
                _txt(
                    lang_code,
                    "Все задачи по этой теме выполнены.\n\nВыберите другую тему или подтему.",
                    "All tasks in this topic are done.\n\nChoose another topic or subtopic.",
                ),
                reply_markup=kb,
            )
        _topic_clear_session(uid, top)
        return

    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        log(call.from_user, ["end"])
        kb = await start_kb(call.from_user.id)
        await call.message.answer(
            _txt(
                lang_code,
                "Все задачи по этой теме выполнены.\n\nВыберите другую тему или подтему.",
                "All tasks in this topic are done.\n\nChoose another topic or subtopic.",
            ),
            reply_markup=kb,
        )


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

        lang = await _get_user_lang(message.from_user)
        if lang == "ru":
            msg = "📊 Ваша статистика:\n\n"
            msg += f"Всего ответов: {stats['total_answered']}\n"
            msg += f"Правильных: {stats['total_correct']}\n"
            acc = (stats["total_correct"] / stats["total_answered"] * 100) if stats["total_answered"] > 0 else 0
            msg += f"Точность: {acc:.1f}%\n\n"
            msg += "По темам:\n"
            for t in stats["by_topic"]:
                msg += f"• {_topic_display(t['topic'], lang)}: {t['answered']} ответов, {t['correct']} правильных ({t['accuracy']:.1f}%)\n"
        else:
            msg = "📊 Your statistics:\n\n"
            msg += f"Total answers: {stats['total_answered']}\n"
            msg += f"Correct: {stats['total_correct']}\n"
            acc = (stats["total_correct"] / stats["total_answered"] * 100) if stats["total_answered"] > 0 else 0
            msg += f"Accuracy: {acc:.1f}%\n\n"
            msg += "By topics:\n"
            for t in stats["by_topic"]:
                msg += f"• {_topic_display(t['topic'], lang)}: {t['answered']} answers, {t['correct']} correct ({t['accuracy']:.1f}%)\n"

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
        _math_all_sessions.pop(message.from_user.id, None)
        lang = await _get_user_lang(message.from_user)
        if lang == "ru":
            text = "Ваша статистика была очищена."
        else:
            text = "Your statistics have been cleared."
        kb = await start_kb(message.from_user.id)
        await message.answer(text, reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка очистки статистики: {e}")
        await message.answer("Не удалось очистить статистику. Попробуйте позже.")


@router.message(Command("adminstat"))
async def cmd_adminstat(message: types.Message):
    """Админская сводка статистики за последние 7 дней."""
    if not db_conn:
        await message.answer("Статистика временно недоступна.")
        return

    try:
        lang_code = await _get_user_lang(message.from_user)
        is_ru = lang_code == "ru"

        now = datetime.datetime.now()
        today_start = datetime.datetime(now.year, now.month, now.day)
        window_start = today_start - datetime.timedelta(days=6)
        window_end = today_start + datetime.timedelta(days=1)

        def _median(values: list[int | float]) -> float:
            if not values:
                return 0.0
            arr = sorted(values)
            n = len(arr)
            mid = n // 2
            if n % 2 == 1:
                return float(arr[mid])
            return (float(arr[mid - 1]) + float(arr[mid])) / 2.0

        async def _day_counts_new_users(day_start: datetime.datetime, day_end: datetime.datetime) -> dict:
            ds = day_start.isoformat()
            de = day_end.isoformat()
            cursor = await db_conn.execute(
                """
                WITH nu AS (
                    SELECT id, language_code, source
                    FROM users
                    WHERE created_at >= ? AND created_at < ?
                )
                SELECT
                    COUNT(*) as total_new,
                    SUM(CASE WHEN language_code LIKE 'ru%' THEN 1 ELSE 0 END) as ru_new,
                    SUM(CASE WHEN language_code LIKE 'en%' THEN 1 ELSE 0 END) as en_new,
                    SUM(CASE WHEN source = 'stepik' THEN 1 ELSE 0 END) as stepik_new,
                    SUM(CASE WHEN source = 'cscagroup' THEN 1 ELSE 0 END) as cscagroup_new,
                    COUNT(DISTINCT CASE WHEN a.user_id IS NOT NULL THEN nu.id END) as solved_new
                FROM nu
                LEFT JOIN answers a
                    ON a.user_id = nu.id
                    AND a.created_at >= ? AND a.created_at < ?
                """,
                (ds, de, ds, de),
            )
            row = await cursor.fetchone()
            return {
                "total": int(row[0] or 0),
                "ru": int(row[1] or 0),
                "en": int(row[2] or 0),
                "stepik": int(row[3] or 0),
                "cscagroup": int(row[4] or 0),
                "solved_new": int(row[5] or 0),
            }

        async def _day_counts_visitors(day_start: datetime.datetime, day_end: datetime.datetime) -> dict:
            ds = day_start.isoformat()
            de = day_end.isoformat()
            cursor = await db_conn.execute(
                """
                WITH av AS (
                    SELECT id, language_code, source
                    FROM users
                    WHERE updated_at >= ? AND updated_at < ?
                )
                SELECT
                    COUNT(*) as total_visitors,
                    SUM(CASE WHEN language_code LIKE 'ru%' THEN 1 ELSE 0 END) as ru_visitors,
                    SUM(CASE WHEN language_code LIKE 'en%' THEN 1 ELSE 0 END) as en_visitors,
                    SUM(CASE WHEN source = 'stepik' THEN 1 ELSE 0 END) as stepik_visitors,
                    SUM(CASE WHEN source = 'cscagroup' THEN 1 ELSE 0 END) as cscagroup_visitors,
                    COUNT(DISTINCT CASE WHEN a.user_id IS NOT NULL THEN av.id END) as solved_visitors
                FROM av
                LEFT JOIN answers a
                    ON a.user_id = av.id
                    AND a.created_at >= ? AND a.created_at < ?
                """,
                (ds, de, ds, de),
            )
            row = await cursor.fetchone()
            return {
                "total": int(row[0] or 0),
                "ru": int(row[1] or 0),
                "en": int(row[2] or 0),
                "stepik": int(row[3] or 0),
                "cscagroup": int(row[4] or 0),
                "solved_visitors": int(row[5] or 0),
            }

        days: list[datetime.datetime] = [window_start + datetime.timedelta(days=i) for i in range(7)]

        out_lines: list[str] = []
        if is_ru:
            out_lines.append("📊 Admin statistics (последние 7 дней)")
            out_lines.append("")
            out_lines.append("1) Новые пользователи (created_at):")
        else:
            out_lines.append("📊 Admin statistics (last 7 days)")
            out_lines.append("")
            out_lines.append("1) New users (created_at):")

        for ds in days:
            de = ds + datetime.timedelta(days=1)
            c = await _day_counts_new_users(ds, de)
            day_lbl = ds.strftime("%Y-%m-%d")
            if is_ru:
                out_lines.append(
                    f"• {day_lbl}: всего {c['total']}, ru {c['ru']}, en {c['en']}, "
                    f"stepik {c['stepik']}, cscagroup {c['cscagroup']}, решили >=1 задачу {c['solved_new']}"
                )
            else:
                out_lines.append(
                    f"• {day_lbl}: total {c['total']}, ru {c['ru']}, en {c['en']}, "
                    f"stepik {c['stepik']}, cscagroup {c['cscagroup']}, solved >=1 {c['solved_new']}"
                )

        if is_ru:
            out_lines.append("")
            out_lines.append("2) Посещения/активность (updated_at):")
        else:
            out_lines.append("")
            out_lines.append("2) Visits/activity (updated_at):")

        for ds in days:
            de = ds + datetime.timedelta(days=1)
            c = await _day_counts_visitors(ds, de)
            day_lbl = ds.strftime("%Y-%m-%d")
            if is_ru:
                out_lines.append(
                    f"• {day_lbl}: всего {c['total']}, ru {c['ru']}, en {c['en']}, "
                    f"stepik {c['stepik']}, cscagroup {c['cscagroup']}, решили >=1 задачу {c['solved_visitors']}"
                )
            else:
                out_lines.append(
                    f"• {day_lbl}: total {c['total']}, ru {c['ru']}, en {c['en']}, "
                    f"stepik {c['stepik']}, cscagroup {c['cscagroup']}, solved >=1 {c['solved_visitors']}"
                )

        # 3) медиана/среднее решённых задач на пользователя за окно
        w_start = window_start.isoformat()
        w_end = window_end.isoformat()
        cur = await db_conn.execute(
            """
            SELECT COUNT(*) as solved_cnt
            FROM answers
            WHERE created_at >= ? AND created_at < ?
            GROUP BY user_id
            """,
            (w_start, w_end),
        )
        counts_rows = await cur.fetchall()
        solved_counts = [int(r[0] or 0) for r in counts_rows]
        median_val = _median(solved_counts)
        avg_val = (sum(solved_counts) / len(solved_counts)) if solved_counts else 0.0

        if is_ru:
            out_lines.append("")
            out_lines.append("3) Решённых задач на пользователя за 7 дней:")
            out_lines.append(f"• медиана: {median_val}")
            out_lines.append(f"• среднее: {avg_val:.2f}")
        else:
            out_lines.append("")
            out_lines.append("3) Solved tasks per user over 7 days:")
            out_lines.append(f"• median: {median_val}")
            out_lines.append(f"• average: {avg_val:.2f}")

        # 4) прохождения экзаменов: считаем уникальных пользователей, ответивших >=1 раз на экзамен за окно
        exam_map = [
            ("21 dec", EXAM_21DEC_ID),
            ("25 jan", EXAM_25JAN_ID),
            ("15 mar", EXAM_MAR15_ID),
        ]
        if "EXAM_MOCK_ID" in globals():
            exam_map.append(("mock", EXAM_MOCK_ID))

        exam_total_sum = 0
        exam_lines: list[str] = []
        exam_topics = [exam_id for _, exam_id in exam_map]
        for label, exam_id in exam_map:
            cur = await db_conn.execute(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM answers
                WHERE topic = ?
                  AND created_at >= ? AND created_at < ?
                """,
                (exam_id, w_start, w_end),
            )
            row = await cur.fetchone()
            cnt_users = int(row[0] or 0)
            exam_total_sum += cnt_users
            exam_lines.append(f"• {label}: {cnt_users}")

        # уникальные пользователи, которые отвечали хотя бы на один экзамен из списка
        placeholders = ",".join(["?"] * len(exam_topics))
        cur = await db_conn.execute(
            f"""
            SELECT COUNT(DISTINCT user_id)
            FROM answers
            WHERE topic IN ({placeholders})
              AND created_at >= ? AND created_at < ?
            """,
            (*exam_topics, w_start, w_end),
        )
        row = await cur.fetchone()
        exam_total_unique = int(row[0] or 0)

        if is_ru:
            out_lines.append("")
            out_lines.append("4) Прохождения экзаменов (уникальные пользователи):")
            out_lines.append(f"• всего уникальных (любой экзамен): {exam_total_unique}")
            out_lines.append(f"• всего (сумма по типам, с пересечениями): {exam_total_sum}")
            out_lines.extend(exam_lines)
        else:
            out_lines.append("")
            out_lines.append("4) Exam attempts (unique users):")
            out_lines.append(f"• total unique (any exam): {exam_total_unique}")
            out_lines.append(f"• total (sum by types, may overlap): {exam_total_sum}")
            out_lines.extend(exam_lines)

        await message.answer("\n".join(out_lines))
    except Exception as e:
        logging.error(f"Ошибка /adminstat: {e}")
        await message.answer("Ошибка при построении статистики. Попробуйте позже.")


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
        lang = await _get_user_lang(message.from_user)
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
    lang = await _get_user_lang(message.from_user)
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
    await pay(message.from_user)


async def pay(user) :
    lang = await _get_user_lang(user)
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
            "250 Telegram Stars  по кнопк ниже."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
             #       InlineKeyboardButton(text="💳 Оплатить 100 руб. через ЮKassa", callback_data="pay_yookassa"),
                    InlineKeyboardButton(text="⭐ Оплатить 250 Telegram Stars", callback_data="pay_stars"),
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
            "If none of these options works for you, you can pay 250 Telegram Stars  "
            "using the button below."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                #    InlineKeyboardButton(text="💳 Pay 100 RUB via YooKassa", callback_data="pay_yookassa"),
                    InlineKeyboardButton(text="⭐ Pay 250 Telegram Stars", callback_data="pay_stars"),
                ]
            ]
        )

    await bot.send_message(chat_id=user.id, text=text, reply_markup=kb)


@router.callback_query(F.data == "pay_stars")
async def on_pay_stars(call: CallbackQuery):
    """Кнопка «Оплатить 250 Telegram Stars» — отправляем инвойс в звёздах."""
    await call.answer()
    user = call.from_user
    lang = await _get_user_lang(user)

    if lang.startswith("ru"):
        title = "Доступ к режиму экзамена"
        description = "Оплата 250 Telegram Stars за неограниченный доступ ко всем функциям бота."
    else:
        title = "Exam mode access"
        description = "Get unlimited access to all bot functionality for 250 Telegram Stars."

    # 250 Stars (для XTR amount — число звёзд)
    prices = [types.LabeledPrice(label="Exam access", amount=250)]
    await bot.send_invoice(
        chat_id=user.id,
        title=title,
        description=description,
        payload="exam_access_250stars",
        currency="XTR",
        prices=prices,
        provider_token="",
    )


@router.callback_query(F.data == "pay_yookassa")
async def on_pay_yookassa(call: CallbackQuery):
    """Кнопка 'Оплатить 100 руб. через ЮKassa' — отправляем инвойс в рублях через платёжного провайдера."""
    await call.answer()
    user = call.from_user
    lang = await _get_user_lang(user)

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

    lang = await _get_user_lang(message.from_user)
    if lang.startswith("ru"):
        text = "Оплата 250 Telegram Stars получена. Доступ к режиму экзамена и Mock Exam открыт."
    else:
        text = "Payment of 250 Telegram Stars received. Exam mode and Mock Exam are now unlocked for you."
    await message.answer(text)


@router.message()
async def on_any_message(message: Message):
    chat_id = message.chat.id
    # Быстрое переключение языка: если в личном сообщении есть слово "english"
    text_l = (message.text or "").lower()
    if message.chat.type == "private" and "english" in text_l:
        old_lang = await _get_user_lang(message.from_user)
        save_status = "no_db"
        if db_conn:
            try:
                await db.set_user_language(db_conn, message.from_user.id, "en")
                save_status = "saved"
            except Exception as e:
                save_status = "save_error"
                logging.error(f"Ошибка сохранения языка по текстовому триггеру для пользователя {message.from_user.id}: {e}")
        _sync_log_lang_ui(message.from_user.id, "en")
        log(message.from_user, ["set_language_by_text", old_lang, "en", save_status, "trigger=english"])
        kb = await start_kb(message.from_user.id)
        await message.answer("Interface language switched to English.", reply_markup=kb)
        greet = "Hi!! I am a CSCA math exam prep bot. I've got  a lot of practice problems and can verify your answers. "
        await message.answer(greet, reply_markup=kb)
        return

    # Не отправляем команды в LLM
    if message.text and message.text.strip().startswith("/"):
        await _refresh_log_lang_cache(message.from_user)
        log(message.from_user, ['message', str(chat_id), message.text.replace("\n"," ") if message.text else "" ])
       
        return

    # Не логируем сообщения из группы -1003634233318
    if chat_id != -1003634233318:
        await _refresh_log_lang_cache(message.from_user)
        log(message.from_user, ['message', str(chat_id), message.text.replace("\n"," ") if message.text else "" ])
        _llm_text = message.text or ""
        if not (7 < len(_llm_text) < 300):
            return
        log(message.from_user, ['llm_len',str(len(_llm_text))])
        access_token = _get_llm_access_token()
        if not access_token:
            await message.answer(
                "LLM token is not set. Set env var `LLM_TOKEN` to enable chat Q&A."
            )
            return

        lang = await _get_user_lang(message.from_user)
        exam_lang = await _get_user_exam_lang_by_id(message.from_user.id)
        # Режим контекста LLM: addtext_only | rag_only | last_task (см. константы LLM_CONTEXT_*)
        llm_context_mode = LLM_CONTEXT_ADDTEXT_ONLY

        try:
            llm_task_snapshot: tuple[str, int] | None = None
            if llm_context_mode == LLM_CONTEXT_LAST_TASK:
                ks = _last_seen_kapibara_question.get(message.from_user.id)
                if ks and len(ks) >= 2:
                    llm_task_snapshot = (ks[0], int(ks[1]))
            async with ChatActionSender(bot=bot, chat_id=message.chat.id, action="typing"):
                answer_text = await asyncio.to_thread(
                    _do_llm_request,
                    llm_context_mode,
                    _llm_text,
                    message.from_user.id,
                    chat_id,
                    message.from_user,
                    lang,
                    access_token,
                    exam_lang,
                )
            if answer_text == "__NO_LAST_TASK__":
                await message.answer(
                    _txt(
                        lang,
                        "Сначала откройте задачу в боте (тема, подтема, экзамен или режим «вся математика»), затем снова напишите сообщение.",
                        "Open a task in the bot first (topic, subtopic, exam, or “all math” mode), then send your message again.",
                    )
                )
                return
            if answer_text:
                if llm_context_mode != LLM_CONTEXT_ADDTEXT_ONLY:
                    try:
                        ans_one_line = (answer_text or "").replace("\n", " ").strip()
                        if len(ans_one_line) > 2000:
                            ans_one_line = ans_one_line[:2000] + "..."
                        log(
                            message.from_user,
                            [
                                "llm_answer",
                                str(chat_id),
                                llm_context_mode,
                                f"len={len(answer_text or '')}",
                                ans_one_line,
                            ],
                        )
                    except Exception:
                        pass
                    fb = (
                        _kb_llm_task_feedback(message.from_user.id, lang)
                        if llm_context_mode == LLM_CONTEXT_LAST_TASK
                        else None
                    )
                    if (
                        llm_context_mode == LLM_CONTEXT_LAST_TASK
                        and llm_task_snapshot
                        and answer_text
                    ):
                        _mark_llm_solution_shown(
                            message.from_user.id,
                            llm_task_snapshot[0],
                            llm_task_snapshot[1],
                        )
                    await message.answer(answer_text, reply_markup=fb)
                else:
                    resp_stripped = (answer_text or "").strip()
                    first_line = resp_stripped.split("\n")[0].strip() if resp_stripped else ""
                    first_token = first_line.split()[0] if first_line.split() else ""
                    # Классификатор LLM: 1 — математика (меню тем), 2 — физика, 3 — химия
                    if first_token in ("1", "1."):
                        await message.answer(
                            _txt(
                                lang,
                                "Ниже — список тем по математике. Выберите тему: в задачах есть проверка ответов и доступ к решению после попытки.",
                                "Below is the math topic list. Pick a topic: you get answer checking and access to the solution after you try.",
                            ),
                            reply_markup=topics_menu_kb(lang),
                        )
                        log(message.from_user, ["llm_topics_menu", str(chat_id), "classifier=1"])
                    elif first_token in ("2", "2."):
                        await _start_topic_training_from_message(message, "physics", "menu_physics")
                        log(message.from_user, ["llm_classifier_physics", str(chat_id)])
                    elif first_token in ("3", "3."):
                        await _start_topic_training_from_message(message, "chemistry", "menu_chemistry")
                        log(message.from_user, ["llm_classifier_chemistry", str(chat_id)])
                    elif first_token in ("4", "4."):
                        log(message.from_user, ["llm_classifier_last_task", str(chat_id)])
                        ks4 = _last_seen_kapibara_question.get(message.from_user.id)
                        llm_task_snapshot_4: tuple[str, int] | None = None
                        if ks4 and len(ks4) >= 2:
                            llm_task_snapshot_4 = (ks4[0], int(ks4[1]))
                        async with ChatActionSender(
                            bot=bot, chat_id=message.chat.id, action="typing"
                        ):
                            second_answer = await asyncio.to_thread(
                                _do_llm_request,
                                LLM_CONTEXT_LAST_TASK,
                                _llm_text,
                                message.from_user.id,
                                chat_id,
                                message.from_user,
                                lang,
                                access_token,
                                exam_lang,
                            )
                        if second_answer == "__NO_LAST_TASK__":
                            await message.answer(
                                _txt(
                                    lang,
                                    "Сначала откройте задачу в боте (тема, подтема, экзамен или режим «вся математика»), затем снова напишите сообщение.",
                                    "Open a task in the bot first (topic, subtopic, exam, or “all math” mode), then send your message again.",
                                )
                            )
                            return
                        if second_answer:
                            try:
                                ans_one_line = (second_answer or "").replace("\n", " ").strip()
                                if len(ans_one_line) > 2000:
                                    ans_one_line = ans_one_line[:2000] + "..."
                                log(
                                    message.from_user,
                                    [
                                        "llm_followup_last_task",
                                        str(chat_id),
                                        f"len={len(second_answer or '')}",
                                        ans_one_line,
                                    ],
                                )
                            except Exception:
                                pass
                            if llm_task_snapshot_4:
                                _mark_llm_solution_shown(
                                    message.from_user.id,
                                    llm_task_snapshot_4[0],
                                    llm_task_snapshot_4[1],
                                )
                            fb = _kb_llm_task_feedback(message.from_user.id, lang)
                            await message.answer(second_answer, reply_markup=fb)
                    elif first_token in ("5", "5."):
                        log(message.from_user, ["llm_classifier_rag", str(chat_id)])
                        async with ChatActionSender(
                            bot=bot, chat_id=message.chat.id, action="typing"
                        ):
                            second_answer = await asyncio.to_thread(
                                _do_llm_request,
                                LLM_CONTEXT_RAG_ONLY,
                                _llm_text,
                                message.from_user.id,
                                chat_id,
                                message.from_user,
                                lang,
                                access_token,
                                None,
                            )
                        if second_answer:
                            try:
                                ans_one_line = (second_answer or "").replace("\n", " ").strip()
                                if len(ans_one_line) > 2000:
                                    ans_one_line = ans_one_line[:2000] + "..."
                                log(
                                    message.from_user,
                                    [
                                        "llm_followup_rag",
                                        str(chat_id),
                                        f"len={len(second_answer or '')}",
                                        ans_one_line,
                                    ],
                                )
                            except Exception:
                                pass
                            await message.answer(second_answer)
                    else:
                        try:
                            ans_one_line = (answer_text or "").replace("\n", " ").strip()
                            if len(ans_one_line) > 2000:
                                ans_one_line = ans_one_line[:2000] + "..."
                            log(
                                message.from_user,
                                ["llm_answer", str(chat_id), f"len={len(answer_text or '')}", ans_one_line],
                            )
                        except Exception:
                            pass
                        await message.answer(answer_text)
        except Exception as e:
            logging.error(f"LLM request error: {e}")
            await message.answer(
                _txt(
                    lang,
                    "Ошибка при обращении к LLM. Попробуйте позже.",
                    "Something went wrong while contacting the LLM. Please try again later.",
                )
            )

    # else: (группа) — без LLM ответа



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
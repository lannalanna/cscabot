"""HTTP API и статика для Telegram Mini App."""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import db  # noqa: E402
from miniapp.auth import user_from_init_data  # noqa: E402
from miniapp.loader import EXAM_TYPES, bank  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_db_conn = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_conn
    _db_conn = await db.init_db()
    yield
    if _db_conn:
        await _db_conn.close()


app = FastAPI(title="CSCA Mini App", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@dataclass
class WebUser:
    id: int
    username: str | None
    language_code: str | None


class FakeTGUser:
    def __init__(self, u: WebUser):
        self.id = u.id
        self.username = u.username
        self.language_code = u.language_code


async def get_web_user(
    x_telegram_init_data: str | None = Header(None, alias="X-Telegram-Init-Data"),
) -> WebUser:
    if not x_telegram_init_data:
        raise HTTPException(401, "X-Telegram-Init-Data required")
    try:
        raw = user_from_init_data(x_telegram_init_data)
    except Exception as e:
        raise HTTPException(401, str(e)) from e
    return WebUser(
        id=int(raw["id"]),
        username=raw.get("username"),
        language_code=raw.get("language_code"),
    )


async def _ensure_db_user(user: WebUser) -> None:
    await db.ensure_user(_db_conn, FakeTGUser(user))


def _norm_lang(code: str | None) -> str | None:
    s = (code or "").lower()
    if not s:
        return None
    if s.startswith("ru"):
        return "ru"
    if s.startswith("ar"):
        return "ar"
    if s.startswith("fa") or s.startswith("pe"):
        return "fa"
    if s.startswith("en"):
        return "en"
    return None


async def _ui_lang(user: WebUser) -> str:
    """Язык интерфейса: приоритет — сохранённый язык пользователя, затем язык Telegram."""
    if _db_conn is not None:
        try:
            saved = _norm_lang(await db.get_user_language(_db_conn, user.id))
            if saved:
                return saved
        except Exception:
            pass
    return _norm_lang(user.language_code) or "en"


async def _exam_lang(user_id: int) -> str:
    el = await db.get_user_exam_language(_db_conn, user_id)
    return el if el in ("en", "zh") else "en"


class LangBody(BaseModel):
    language: str


class ExamLangBody(BaseModel):
    exam_language: str


class AnswerBody(BaseModel):
    topic: str
    index: int
    chosen_index: int
    mode: str = "topic"  # topic | subtopic | exam
    subtopic: str | None = None
    exam_key: str | None = None
    exam_pos: int | None = None


def _t(lang: str, ru: str, en: str, ar: str | None = None, fa: str | None = None) -> str:
    if lang == "ru":
        return ru
    if lang == "ar":
        return ar if ar is not None else en
    if lang == "fa":
        return fa if fa is not None else en
    return en


@app.get("/api/health")
async def health():
    return {"ok": True, "topics": len(bank.topics)}


@app.get("/api/me")
async def me(user: WebUser = Depends(get_web_user)):
    await _ensure_db_user(user)
    lang = await _ui_lang(user)
    exam_lang = await _exam_lang(user.id)
    stats = await db.get_user_stats(_db_conn, user.id)
    progress = await db.get_progress(_db_conn, user.id)
    continue_list = []
    for p in progress:
        top = p["topic"]
        if top.startswith("exam_"):
            continue
        last_idx = p["last_question_index"]
        total = len(bank.kapibara.get(top) or [])
        if last_idx < total:
            continue_list.append(
                {
                    "topic": top,
                    "title": bank.topic_title(top, lang),
                    "last_index": last_idx,
                    "total": total,
                }
            )
    return {
        "user_id": user.id,
        "username": user.username,
        "ui_lang": lang,
        "exam_lang": exam_lang,
        "stats": stats,
        "continue": continue_list,
    }


@app.post("/api/settings/language")
async def set_language(body: LangBody, user: WebUser = Depends(get_web_user)):
    if body.language not in ("ru", "en", "ar", "fa"):
        raise HTTPException(400, "language must be ru, en, ar or fa")
    await _ensure_db_user(user)
    await db.set_user_language(_db_conn, user.id, body.language)
    return {"ok": True, "language": body.language}


@app.post("/api/settings/exam-language")
async def set_exam_language(body: ExamLangBody, user: WebUser = Depends(get_web_user)):
    if body.exam_language not in ("en", "zh"):
        raise HTTPException(400, "exam_language must be en or zh")
    await _ensure_db_user(user)
    await db.set_user_exam_language(_db_conn, user.id, body.exam_language)
    return {"ok": True, "exam_language": body.exam_language}


@app.get("/api/menu")
async def menu(user: WebUser = Depends(get_web_user)):
    lang = await _ui_lang(user)
    items = [
        {
            "id": "topics",
            "title": _t(lang, "Математика по темам", "Math by topic", "رياضيات حسب الموضوع", "ریاضی بر اساس موضوع"),
        },
        {
            "id": "physics",
            "title": _t(lang, "Физика", "Physics", "الفيزياء", "فیزیک"),
            "topic": "physics",
        },
        {
            "id": "chemistry",
            "title": _t(lang, "Химия", "Chemistry", "الكيمياء", "شیمی"),
            "topic": "chemistry",
        },
        {
            "id": "exams",
            "title": _t(lang, "Экзамены", "Exams", "الامتحانات", "آزمون‌ها"),
        },
        {
            "id": "stats",
            "title": _t(lang, "Статистика", "Statistics", "الإحصائيات", "آمار"),
        },
    ]
    return {"items": items}


@app.get("/api/topics")
async def topics(user: WebUser = Depends(get_web_user)):
    lang = await _ui_lang(user)
    out = []
    for top in bank.topics:
        if top in ("physics", "chemistry"):
            continue
        out.append(
            {
                "key": top,
                "title": bank.topic_title(top, lang),
                "count": len(bank.kapibara.get(top) or []),
            }
        )
    return {"topics": out}


@app.get("/api/topics/{topic}/subtopics")
async def subtopics(topic: str, user: WebUser = Depends(get_web_user)):
    if topic not in bank.kapibara:
        raise HTTPException(404, "topic not found")
    lang = await _ui_lang(user)
    subs = bank.subtopics_by_topic.get(topic, [])
    return {
        "subtopics": [
            {
                "key": s,
                "title": s,
                "count": len(bank.questions_by_topic_subtopic.get((topic, s), [])),
            }
            for s in subs
        ]
    }


def _serialize_question(
    q: dict,
    topic: str,
    index: int,
    exam_lang: str,
    *,
    exam_n: int | None = None,
) -> dict[str, Any]:
    opts = bank.task_options(q, exam_lang)
    stem = bank.task_text(q, exam_lang)
    body = bank.format_question_body(stem, opts)
    labels = opts
    if any(len(str(x)) > 45 for x in opts):
        labels = [f"{chr(ord('A') + i)}." for i in range(len(opts))]
    return {
        "topic": topic,
        "index": index,
        "id": q.get("id"),
        "text": body,
        "options": [{"index": i, "label": labels[i]} for i in range(len(opts))],
        "has_image": bank.image_path(q) is not None,
        "has_hint": bank.hint_image_path(q, "ru") is not None or bank.hint_image_path(q, "en") is not None,
        "has_solution": bool(
            q.get("solution_ru") or q.get("solution_en") or q.get("solution_ru2")
        ),
        "exam_n": exam_n,
        "stars": "★" if exam_n and 1 <= exam_n <= 20 else ("★★" if exam_n and 21 <= exam_n <= 40 else ("★★★" if exam_n and 41 <= exam_n <= 48 else "")),
    }


@app.get("/api/question")
async def question(
    topic: str = Query(...),
    index: int = Query(0, ge=0),
    subtopic: str | None = Query(None),
    user: WebUser = Depends(get_web_user),
):
    if topic not in bank.kapibara:
        raise HTTPException(404, "topic not found")
    if subtopic:
        indices = bank.questions_by_topic_subtopic.get((topic, subtopic), [])
        if index >= len(indices):
            raise HTTPException(404, "index out of range")
        j = indices[index]
    else:
        j = index
        if j >= len(bank.kapibara[topic]):
            raise HTTPException(404, "index out of range")
    q = bank.get_question(topic, j)
    if not q:
        raise HTTPException(404, "question not found")
    exam_lang = await _exam_lang(user.id)
    if subtopic:
        total = len(bank.questions_by_topic_subtopic.get((topic, subtopic), []))
    else:
        total = len(bank.kapibara[topic])
    return {
        "question": _serialize_question(q, topic, j, exam_lang),
        "position": index,
        "total": total,
        "subtopic": subtopic,
    }


@app.post("/api/answer")
async def submit_answer(body: AnswerBody, user: WebUser = Depends(get_web_user)):
    await _ensure_db_user(user)
    lang = await _ui_lang(user)

    if body.mode == "exam":
        if body.exam_key not in bank.exam_questions:
            raise HTTPException(400, "invalid exam_key")
        qs = bank.exam_questions[body.exam_key]
        pos = body.exam_pos or 0
        if pos < 0 or pos >= len(qs):
            raise HTTPException(404, "exam position out of range")
        n_val, topic, j = qs[pos]
        q = bank.get_question(topic, j)
        if not q:
            raise HTTPException(404, "question not found")
        correct = bank.check_answer(q, body.chosen_index)
        exam_id = EXAM_TYPES[body.exam_key]["id"]
        await db.save_answer(
            _db_conn, user.id, exam_id, pos, body.chosen_index, correct
        )
        next_pos = pos + 1 if pos + 1 < len(qs) else None
        return {
            "correct": correct,
            "message": _t(lang, "Правильно!", "Correct!", "صحيح!")
            if correct
            else _t(lang, "Неправильно.", "Incorrect.", "غير صحيح."),
            "finished": next_pos is None,
            "next_pos": next_pos,
            "total": len(qs),
        }

    topic = body.topic
    if topic not in bank.kapibara:
        raise HTTPException(404, "topic not found")

    if body.subtopic:
        indices = bank.questions_by_topic_subtopic.get((topic, body.subtopic), [])
        if body.index >= len(indices):
            raise HTTPException(404, "index out of range")
        j = indices[body.index]
        total = len(indices)
    else:
        j = body.index
        if j >= len(bank.kapibara[topic]):
            raise HTTPException(404, "index out of range")
        total = len(bank.kapibara[topic])

    q = bank.get_question(topic, j)
    if not q:
        raise HTTPException(404, "question not found")

    correct = bank.check_answer(q, body.chosen_index)
    await db.save_answer(_db_conn, user.id, topic, j, body.chosen_index, correct)

    next_index = body.index + 1 if body.index + 1 < total else None
    return {
        "correct": correct,
        "message": _t(lang, "Правильно!", "Correct!", "صحيح!")
        if correct
        else _t(lang, "Неправильно.", "Incorrect.", "غير صحيح."),
        "next_index": next_index,
        "finished": next_index is None,
        "total": total,
    }


@app.get("/api/solution")
async def solution(
    topic: str = Query(...),
    index: int = Query(..., ge=0),
    user: WebUser = Depends(get_web_user),
):
    q = bank.get_question(topic, index)
    if not q:
        raise HTTPException(404, "question not found")
    lang = await _ui_lang(user)
    text = bank.solution_text(q, lang)
    if not text:
        raise HTTPException(404, "no solution")
    return {"text": text}


@app.get("/api/hint")
async def hint_image(
    topic: str = Query(...),
    index: int = Query(..., ge=0),
    user: WebUser = Depends(get_web_user),
):
    q = bank.get_question(topic, index)
    if not q:
        raise HTTPException(404, "question not found")
    lang = await _ui_lang(user)
    path = bank.hint_image_path(q, lang)
    if not path:
        raise HTTPException(404, "no hint")
    return FileResponse(path)


@app.get("/api/image")
async def task_image(
    topic: str = Query(...),
    index: int = Query(..., ge=0),
):
    q = bank.get_question(topic, index)
    if not q:
        raise HTTPException(404, "question not found")
    path = bank.image_path(q)
    if not path:
        raise HTTPException(404, "no image")
    return FileResponse(path)


@app.get("/api/exams")
async def exams_list(user: WebUser = Depends(get_web_user)):
    lang = await _ui_lang(user)
    out = []
    for key, cfg in EXAM_TYPES.items():
        qs = bank.exam_questions.get(key) or []
        if not qs:
            continue
        title = cfg["title_ru"] if lang == "ru" else cfg["title_en"]
        out.append(
            {
                "key": key,
                "id": cfg["id"],
                "title": title,
                "count": len(qs),
            }
        )
    return {"exams": out}


@app.get("/api/exams/{exam_key}/question")
async def exam_question(
    exam_key: str,
    pos: int = Query(0, ge=0),
    user: WebUser = Depends(get_web_user),
):
    if exam_key not in bank.exam_questions:
        raise HTTPException(404, "exam not found")
    qs = bank.exam_questions[exam_key]
    if pos >= len(qs):
        raise HTTPException(404, "position out of range")
    n_val, topic, j = qs[pos]
    q = bank.get_question(topic, j)
    if not q:
        raise HTTPException(404, "question not found")
    exam_lang = await _exam_lang(user.id)
    cfg = EXAM_TYPES[exam_key]
    lang = await _ui_lang(user)
    return {
        "exam_key": exam_key,
        "exam_id": cfg["id"],
        "title": cfg["title_ru"] if lang == "ru" else cfg["title_en"],
        "position": pos,
        "total": len(qs),
        "question": _serialize_question(q, topic, j, exam_lang, exam_n=n_val),
        "topic": topic,
        "index": j,
    }


@app.get("/api/stats")
async def stats(user: WebUser = Depends(get_web_user)):
    await _ensure_db_user(user)
    return await db.get_user_stats(_db_conn, user.id)


if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

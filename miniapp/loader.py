"""Загрузка банка задач для Mini App (та же логика, что в testbot.py)."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def detect_data_dir() -> str:
    env_dir = os.environ.get("BOT_DATA_DIR")
    candidates = []
    if env_dir:
        candidates.append(env_dir)
    candidates.append(os.path.join(os.sep, "data"))
    candidates.append(os.path.join(BASE_DIR, "data"))
    for d in candidates:
        if d and os.path.exists(os.path.join(d, "data.txt")):
            return d
    return os.path.join(BASE_DIR, "data")


DATA_DIR = detect_data_dir()
os.environ.setdefault("BOT_DATA_DIR", DATA_DIR)

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
    "chemistry",
]

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
    "chemistry": "Химия",
}

CORRECT_LETTER_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}

EXAM_TYPES = {
    "jan": {"id": "exam_25jan", "type": "jan", "title_ru": "25 января", "title_en": "Jan 25"},
    "dec": {"id": "exam_21dec", "type": "dec", "title_ru": "21 декабря", "title_en": "Dec 21"},
    "mar": {"id": "exam_mar15", "type": "mar", "title_ru": "15 марта", "title_en": "Mar 15"},
    "apr": {"id": "exam_apr25", "type": "apr", "title_ru": "25 апреля", "title_en": "Apr 25"},
}


def _is_task_enabled(item: dict) -> bool:
    topic = (item.get("topic") or "").strip()
    typ = (item.get("type") or "").strip().lower()
    if topic == "off" or typ == "off":
        return False
    return True


def _load_json_tasks(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    items = raw if isinstance(raw, list) else [raw]
    return [x for x in items if isinstance(x, dict) and _is_task_enabled(x)]


class QuestionBank:
    def __init__(self) -> None:
        self.data_dir = DATA_DIR
        self.kapibara: dict[str, list[dict]] = {}
        self.topics: list[str] = []
        self.subtopics_by_topic: dict[str, list[str]] = {}
        self.questions_by_topic_subtopic: dict[tuple[str, str], list[int]] = {}
        self.exam_questions: dict[str, list[tuple[int, str, int]]] = {}
        self._reload()

    def _reload(self) -> None:
        kpb: list[dict] = []
        kpb.extend(_load_json_tasks(os.path.join(self.data_dir, "data.txt")))
        kpb.extend(_load_json_tasks(os.path.join(self.data_dir, "data_physics.txt")))
        kpb.extend(_load_json_tasks(os.path.join(self.data_dir, "data_chemestry.txt")))

        topics_set: set[str] = set()
        for k in kpb:
            t = k.get("topic")
            if t:
                topics_set.add(t)

        order_index = {name: i for i, name in enumerate(DESIRED_TOPIC_ORDER)}
        self.topics = sorted(
            topics_set, key=lambda t: (order_index.get(t, len(DESIRED_TOPIC_ORDER)), t)
        )
        self.kapibara = {top: [x for x in kpb if x.get("topic") == top] for top in self.topics}

        self.subtopics_by_topic = {}
        self.questions_by_topic_subtopic = {}
        for top in self.topics:
            subs: set[str] = set()
            for item in self.kapibara.get(top, []):
                sub = (item.get("subtopic") or "").strip() or "general"
                subs.add(sub)
            if top not in ("physics", "chemistry") and top in FIXED_SUBTOPIC_ORDER_BY_TOPIC:
                order = FIXED_SUBTOPIC_ORDER_BY_TOPIC[top]
                sub_order_index = {name: i for i, name in enumerate(order)}
                self.subtopics_by_topic[top] = sorted(
                    subs, key=lambda s: (sub_order_index.get(s, 10**6), s)
                )
            else:
                self.subtopics_by_topic[top] = sorted(subs)

            for j, item in enumerate(self.kapibara[top]):
                sub = (item.get("subtopic") or "").strip() or "general"
                self.questions_by_topic_subtopic.setdefault((top, sub), []).append(j)

        self.exam_questions = {}
        for key, cfg in EXAM_TYPES.items():
            typ = cfg["type"]
            qs: list[tuple[int, str, int]] = []
            for top in self.topics:
                for j, item in enumerate(self.kapibara.get(top, [])):
                    if (item.get("type") or "").strip().lower() == typ:
                        qs.append((int(item.get("n") or 0), top, j))
            qs.sort(key=lambda x: x[0])
            self.exam_questions[key] = qs

    def topic_title(self, topic: str, lang: str = "ru") -> str:
        if lang == "ru":
            return TOPIC_TITLE_RU.get(topic, topic.replace("_", " ").title())
        return topic.replace("_", " ").title()

    def get_question(self, topic: str, index: int) -> dict | None:
        arr = self.kapibara.get(topic) or []
        if 0 <= index < len(arr):
            return arr[index]
        return None

    def check_answer(self, q: dict, chosen_index: int) -> bool:
        letter = (q.get("answer") or "").strip().upper()
        correct_idx = CORRECT_LETTER_TO_INDEX.get(letter[:1] if letter else "", -1)
        return correct_idx == chosen_index

    @staticmethod
    def task_text(q: dict, exam_lang: str | None) -> str:
        en = (q.get("english") or "").strip()
        zh = (q.get("chinese") or "").strip()
        if exam_lang == "en":
            stem = en or zh
        elif exam_lang == "zh":
            stem = zh or en
        else:
            stem = (en + "\n" + zh) if en and zh else (en or zh)
        return stem

    @staticmethod
    def task_options(q: dict, exam_lang: str | None) -> list[str]:
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

    @staticmethod
    def format_question_body(stem: str, options: list[str]) -> str:
        limit = 45

        def over_limit() -> bool:
            return any(isinstance(x, str) and len(x) > limit for x in options)

        if not options or not over_limit():
            return stem

        def strip_prefix(raw: str) -> str:
            t = (raw or "").lstrip()
            while True:
                m = re.match(r"^[A-Ea-e]\s*[\.\)]\s*", t)
                if not m:
                    break
                t = t[m.end() :].lstrip()
            return t

        lines = [stem.rstrip()] if (stem or "").strip() else []
        for i, s in enumerate(options):
            letter = chr(ord("A") + i)
            t = strip_prefix((s or "").strip())
            lines.append(f"{letter}. {t}" if t else f"{letter}.")
        return "\n".join(lines)

    def solution_text(self, q: dict, ui_lang: str) -> str:
        lg = ui_lang if ui_lang in ("ru", "en", "ar") else "en"
        if lg == "ru":
            return (q.get("solution_ru2") or q.get("solution_ru") or q.get("solution_en") or "").strip()
        if lg == "ar":
            return (q.get("solution_ar2") or q.get("solution_en") or "").strip()
        return (q.get("solution_en2") or q.get("solution_en") or q.get("solution_ru") or "").strip()

    def hint_image_path(self, q: dict, ui_lang: str) -> str | None:
        tid = (q.get("id") or "").strip()
        if not tid:
            return None
        lang_suffix = "ru" if ui_lang == "ru" else "en"
        hints_dir = os.path.join(self.data_dir, "images", "hints")
        exts = (".png", ".jpg", ".jpeg", ".webp")
        candidates = [f"{tid}{lang_suffix}{e}" for e in exts]
        prefix = re.match(r"^([A-Za-z]+)", tid)
        if prefix:
            p = prefix.group(1)
            candidates.extend([f"{p}{lang_suffix}{e}" for e in exts])
        for name in candidates:
            path = os.path.join(hints_dir, name)
            if os.path.isfile(path):
                return path
        return None

    def image_path(self, q: dict) -> str | None:
        img = (q.get("img") or "").strip()
        if not img:
            return None
        path = os.path.join(self.data_dir, "images", img)
        return path if os.path.isfile(path) else None

    def exam_points(self, n_val: int) -> float:
        if 1 <= n_val <= 20:
            return 1.5
        if 21 <= n_val <= 40:
            return 2.0
        if 41 <= n_val <= 48:
            return 3.75
        return 0.0


bank = QuestionBank()

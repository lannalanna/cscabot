"""Генерация examtasks.pdf из examtasks.json (тема, подтема, задачи, отдельно ответы)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent
JSON_PATH = ROOT / "examtasks.json"
PDF_PATH = ROOT / "examtasks.pdf"

_MONTH_RU = {
    "dec": "декабрь",
    "jan": "январь",
    "mar": "март",
    "oct": "октябрь",
}


def _month_label(code: str) -> str:
    c = (code or "").strip().lower()
    return _MONTH_RU.get(c, c or "—")


def register_fonts() -> None:
    winds = os.environ.get("WINDIR", r"C:\Windows")
    arial = Path(winds) / "Fonts" / "arial.ttf"
    arial_bold = Path(winds) / "Fonts" / "arialbd.ttf"
    if not arial.is_file():
        raise SystemExit(
            f"Нужен шрифт Arial с поддержкой кириллицы: не найден {arial}. "
            "Укажите путь к .ttf вручную в скрипте."
        )
    pdfmetrics.registerFont(TTFont("ExamArial", str(arial)))
    if arial_bold.is_file():
        pdfmetrics.registerFont(TTFont("ExamArial-Bold", str(arial_bold)))
    else:
        pdfmetrics.registerFont(TTFont("ExamArial-Bold", str(arial)))


def ptext(raw: str) -> str:
    return escape(raw or "").replace("\n", "<br/>")


def main() -> None:
    register_fonts()
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "ExamTitle",
        parent=base["Heading1"],
        fontName="ExamArial-Bold",
        fontSize=16,
        spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "ExamH2",
        parent=base["Heading2"],
        fontName="ExamArial-Bold",
        fontSize=12,
        spaceAfter=6,
        spaceBefore=10,
    )
    body = ParagraphStyle(
        "ExamBody",
        parent=base["Normal"],
        fontName="ExamArial",
        fontSize=10,
        leading=14,
        spaceAfter=4,
    )
    meta = ParagraphStyle(
        "ExamMeta",
        parent=base["Normal"],
        fontName="ExamArial-Bold",
        fontSize=10,
        leading=14,
        spaceAfter=4,
    )

    story: list = []
    story.append(Paragraph(ptext("Задачи экзамена (декабрь, январь, март, октябрь)"), title))
    story.append(Paragraph(ptext("Часть 1. Условия (на английском)"), h2))
    story.append(Spacer(1, 0.15 * cm))

    for group in data:
        topic = group.get("topic", "")
        sub = group.get("subtopic", "")
        story.append(Paragraph(ptext(f"{topic} — {sub}"), h2))
        for task in group.get("tasks", []):
            exam = (task.get("type") or "").strip().lower()
            num = task.get("n")
            num_s = str(num) if num is not None else "—"
            story.append(
                Paragraph(
                    ptext(
                        f"Месяц экзамена: {_month_label(exam)}, номер: {num_s}"
                    ),
                    meta,
                )
            )
            en = (task.get("english") or "").strip()
            if not en:
                en = "(No English statement in source.)"
            story.append(Paragraph(ptext(en), body))
            for opt in task.get("options") or []:
                story.append(Paragraph(ptext(str(opt)), body))
            story.append(Spacer(1, 0.3 * cm))

    story.append(PageBreak())
    story.append(Paragraph(ptext("Часть 2. Ответы"), title))
    story.append(Spacer(1, 0.25 * cm))

    for group in data:
        topic = group.get("topic", "")
        sub = group.get("subtopic", "")
        story.append(Paragraph(ptext(f"{topic} — {sub}"), h2))
        answer_lines: list[str] = []
        for task in group.get("tasks", []):
            exam = (task.get("type") or "").strip().lower()
            num = task.get("n")
            num_s = str(num) if num is not None else "—"
            ans = task.get("answer") or ""
            mru = _month_label(exam).capitalize()
            answer_lines.append(f"{mru}, № {num_s} — {ans}")
        story.append(Paragraph(ptext("\n".join(answer_lines)), body))
        story.append(Spacer(1, 0.35 * cm))

    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    doc.build(story)
    print(f"Written {PDF_PATH}")


if __name__ == "__main__":
    main()

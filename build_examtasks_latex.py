"""Генерация examtasks.tex из examtasks.json (LaTeX, русские месяцы, формулы)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JSON_PATH = ROOT / "examtasks.json"
TEX_PATH = ROOT / "examtasks.tex"

MONTH_RU = {
    "dec": "декабрь",
    "jan": "январь",
    "mar": "март",
    "oct": "октябрь",
}

# Подстрочные индексы (цифры и частые буквы)
_SUB = {
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
    "ₙ": "n",
    "ₖ": "k",
    "ᵢ": "i",
    "ⱼ": "j",
    "ₘ": "m",
    "ₚ": "p",
    "ₛ": "s",
    "ₜ": "t",
    "ᵣ": "r",
}

# Надстрочные (²/³ — U+00B2 / U+00B3)
_SUP = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "ⁿ": "n",
}

_GREEK = {
    "α": r"\alpha ",
    "β": r"\beta ",
    "γ": r"\gamma ",
    "θ": r"\theta ",
    "π": r"\pi ",
    "ω": r"\omega ",
    "φ": r"\phi ",
    "ψ": r"\psi ",
    "σ": r"\sigma ",
    "λ": r"\lambda ",
    "μ": r"\mu ",
    "ρ": r"\rho ",
    "τ": r"\tau ",
    "η": r"\eta ",
    "δ": r"\delta ",
}


def _collect_sub(s: str, start: int) -> tuple[str, int]:
    parts: list[str] = []
    j = start
    while j < len(s) and s[j] in _SUB:
        parts.append(_SUB[s[j]])
        j += 1
    return "".join(parts), j


def _collect_sup(s: str, start: int) -> tuple[str, int]:
    parts: list[str] = []
    j = start
    while j < len(s) and s[j] in _SUP:
        parts.append(_SUP[s[j]])
        j += 1
    return "".join(parts), j


def _sub_sup_unicode(s: str) -> str:
    """a₁₀₀ → a_{100}, x⁴ → x^{4}, (aₙ) — без лишних пробелов."""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if i + 1 < n and s[i + 1] in _SUB and (c.isalnum() or c in ")]]}"):
            sub, j = _collect_sub(s, i + 1)
            out.append(f"{c}_{{{sub}}}")
            i = j
            continue
        if i + 1 < n and s[i + 1] in _SUP and (
            c.isalnum() or c in ")]}"
        ):
            sup, j = _collect_sup(s, i + 1)
            out.append(f"{c}^{{{sup}}}")
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _sqrt_unicode(s: str) -> str:
    s = re.sub(r"√\(([^)]+)\)", r"\\sqrt{\1}", s)
    s = re.sub(r"√\s*(\d+)", r"\\sqrt{\1}", s)
    s = re.sub(r"√\s+", r"\\sqrt", s)
    return s


def _symbol_unicode(s: str) -> str:
    for u, cmd in _GREEK.items():
        s = s.replace(u, cmd)
    reps = [
        ("≤", r"\leq "),
        ("≥", r"\geq "),
        ("≠", r"\neq "),
        ("×", r"\times "),
        ("·", r"\cdot "),
        ("∞", r"\infty "),
        ("∅", r"\emptyset "),
        ("∩", r"\cap "),
        ("∪", r"\cup "),
        ("∈", r"\in "),
        ("∉", r"\notin "),
        ("⊆", r"\subseteq "),
        ("⊈", r"\nsubseteq "),
        ("⊂", r"\subset "),
        ("ℝ", r"\mathbb{R}"),
        ("∠", r"\angle "),
        ("°", r"^\circ "),
        ("∘", r"^\circ "),
        ("…", r"\ldots "),
        ("−", "-"),
        ("–", "--"),
        ("—", "---"),
    ]
    for a, b in reps:
        s = s.replace(a, b)
    return s


def _brace_sets_for_math(s: str) -> str:
    """Только перечисления вида {1,2,3} и задатки множеств {x | ...} — не трогаем \\mathbb{R} и т.п."""

    def repl_tuple(m: re.Match[str]) -> str:
        inner = re.sub(r"\s+", "", m.group(1))
        return r"\{" + inner + r"\}"

    s = re.sub(r"\{((?:\s*\d+\s*,)+\s*\d+)\}", repl_tuple, s)
    s = re.sub(
        r"\{x\s*\|\s*([^}]+)\}",
        lambda m: r"\{x \mid " + m.group(1).strip() + r"\}",
        s,
        flags=re.IGNORECASE,
    )
    return s


def _escape_text_brace(s: str) -> str:
    return s.replace("{", r"\{").replace("}", r"\}")


_PUA_BASE = 0xE000


def _shield_latex_commands(s: str) -> tuple[str, dict[int, str]]:
    """
    Подменяет \\command... на символы U+E000+idx, чтобы latex_inline_sentence не рвал \\infty и др.
    Пары \\{ \\} оставляет как есть (два символа).
    """
    out: list[str] = []
    cmds: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n and s[i + 1] == "{":
            out.append("\\{")
            i += 2
            continue
        if s[i] == "\\" and i + 1 < n and s[i + 1] == "}":
            out.append("\\}")
            i += 2
            continue
        if s[i] == "\\" and i + 1 < n and s[i + 1].isalpha():
            j = i + 1
            while j < n and s[j].isalpha():
                j += 1
            end = j
            if end < n and s[end] == "{":
                depth = 1
                k = end + 1
                while k < n and depth:
                    if s[k] == "{":
                        depth += 1
                    elif s[k] == "}":
                        depth -= 1
                    k += 1
                end = k
            cmd = s[i:end]
            code = _PUA_BASE + len(cmds)
            cmds.append(cmd)
            out.append(chr(code))
            i = end
            continue
        out.append(s[i])
        i += 1
    mapping = {_PUA_BASE + k: v for k, v in enumerate(cmds)}
    return "".join(out), mapping


def latex_inline_sentence(s: str, pua_map: dict[int, str] | None = None) -> str:
    """Чередование \\text{...} и «сырых» латех-фрагментов: _{{..}}, ^{..}, подставленные команды."""
    mapping = pua_map or {}
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(s)

    def flush() -> None:
        if not buf:
            return
        chunk = "".join(buf)
        chunk = (
            chunk.replace("\\", r"\textbackslash{}")
            .replace("&", r"\&")
            .replace("%", r"\%")
            .replace("#", r"\#")
            .replace("_", r"\_")
            .replace("$", r"\$")
        )
        out.append(r"\text{" + _escape_text_brace(chunk) + "}")
        buf.clear()

    while i < n:
        o = ord(s[i])
        if _PUA_BASE <= o < _PUA_BASE + 4096 and o in mapping:
            flush()
            out.append(mapping[o])
            i += 1
            continue
        ch = s[i]
        if ch == "\\":
            flush()
            if i + 1 < n and s[i + 1] == "{":
                out.append(r"\{")
                i += 2
                continue
            if i + 1 < n and s[i + 1] == "}":
                out.append(r"\}")
                i += 2
                continue
            j = i + 1
            while j < n and s[j].isalpha():
                j += 1
            if j < n and s[j] == "{":
                depth = 1
                k = j + 1
                while k < n and depth:
                    if s[k] == "{":
                        depth += 1
                    elif s[k] == "}":
                        depth -= 1
                    k += 1
                out.append(s[i:k])
                i = k
            else:
                out.append(s[i:j])
                i = j
            continue
        if ch == "_" and i + 1 < n and s[i + 1] == "{":
            flush()
            depth = 1
            k = i + 2
            while k < n and depth:
                if s[k] == "{":
                    depth += 1
                elif s[k] == "}":
                    depth -= 1
                k += 1
            out.append(s[i:k])
            i = k
            continue
        if ch == "^" and i + 1 < n and s[i + 1] == "{":
            flush()
            depth = 1
            k = i + 2
            while k < n and depth:
                if s[k] == "{":
                    depth += 1
                elif s[k] == "}":
                    depth -= 1
                k += 1
            out.append(s[i:k])
            i = k
            continue
        buf.append(ch)
        i += 1
    flush()
    return "".join(out)


def _mix_text_and_math(s: str) -> str:
    s, pua_map = _shield_latex_commands(s)
    return latex_inline_sentence(s, pua_map)


def _union_letter_u(s: str) -> str:
    """Интервалы вида (a,b) U (c,d) → \\cup между скобками."""
    return re.sub(r"\)\s*U\s*\(", r") \\cup (", s)


def _infty_as_commands(s: str) -> str:
    """
    Целые фрагменты вида (-\\infty и (+\\infty как одна команда, иначе \\text{…} рвётся перед \\infty.
    """
    s = re.sub(r"\(\s*-\s*\\infty\s*", r"\\OPENNEGINF ", s)
    s = re.sub(r"\(\s*\+\s*\\infty\s*", r"\\OPENPOSINF ", s)
    s = re.sub(r",\s*-\s*\\infty\s*", r", \\NEGINFTAIL ", s)
    s = re.sub(r",\s*\+\s*\\infty\s*", r", \\PLUSINFTAIL ", s)
    s = re.sub(r"\+\s*\\infty\s*", r"\\PLUSINFTAIL ", s)
    return s


def _unicode_math_pipeline(s: str) -> str:
    s = _sub_sup_unicode(s)
    s = _sqrt_unicode(s)
    s = _symbol_unicode(s)
    s = _infty_as_commands(s)
    s = _union_letter_u(s)
    s = _brace_sets_for_math(s)
    s = re.sub(r"log\s+_", r"\\log_", s)
    s = re.sub(r"ln\s+", r"\\ln ", s)
    s = re.sub(r"sin\s+", r"\\sin ", s)
    s = re.sub(r"cos\s+", r"\\cos ", s)
    s = re.sub(r"tan\s+", r"\\tan ", s)
    s = s.replace("tg ", r"\\tan ")
    return s


def english_to_latex_math(s: str) -> str:
    """Условие на английском: unicode → LaTeX, смешанный \\text + формулы в одном $...$."""
    if not (s or "").strip():
        return r"\textit{(no English statement.)}"
    s = _unicode_math_pipeline(s.strip())
    return r"$\displaystyle " + _mix_text_and_math(s) + "$"


def option_to_latex(s: str) -> str:
    s = strip_cjk(str(s))
    if not s.strip():
        return r"\textit{[empty]}"
    s = _unicode_math_pipeline(s.strip())
    return r"$\displaystyle " + _mix_text_and_math(s) + "$"


def strip_cjk(s: str) -> str:
    """Убрать китайские символы из варианта (оставить только «английское»)."""
    return re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", "", s).strip()


def escape_tex_verbatim(s: str) -> str:
    """Для коротких меток без активной математики."""
    return (
        s.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("~", r"\textasciitilde{}")
        .replace("^", r"\textasciicircum{}")
    )


def month_ru(exam: str) -> str:
    e = (exam or "").strip().lower()
    return MONTH_RU.get(e, e or "—")


def main() -> None:
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    lines: list[str] = [
        r"\documentclass[11pt,a4paper]{article}",
        r"\usepackage[T2A]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[russian,english]{babel}",
        r"\usepackage{amsmath,amssymb,amsfonts}",
        r"\usepackage[a4paper,margin=2.2cm]{geometry}",
        r"\usepackage{enumitem}",
        r"\setlist[enumerate,1]{label=\arabic*., itemsep=0.35em}",
        r"\setlist[enumerate,2]{label=\alph*), itemsep=0.15em}",
        r"\newcommand{\OPENNEGINF}{(-\infty}",
        r"\newcommand{\OPENPOSINF}{(+\infty}",
        r"\newcommand{\NEGINFTAIL}{-\infty}",
        r"\newcommand{\PLUSINFTAIL}{+\infty}",
        r"\title{Exam tasks (dec, jan, mar, oct)}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
        "",
        r"\section*{Часть 1. Условия (декабрь, январь, март, октябрь)}",
        "",
    ]

    for group in data:
        topic = group.get("topic", "")
        sub = group.get("subtopic", "")
        lines.append(r"\subsection*{" + escape_tex_verbatim(f"{topic} — {sub}") + "}")
        lines.append(r"\begin{enumerate}")
        for task in group.get("tasks", []):
            exam = (task.get("type") or "").strip().lower()
            num = task.get("n")
            num_s = str(num) if num is not None else "—"
            hdr = f"{month_ru(exam).capitalize()}, № {num_s}"
            lines.append(
                r"\item \textbf{"
                + escape_tex_verbatim(hdr)
                + r"} \par \smallskip"
            )
            en = (task.get("english") or "").strip()
            lines.append(english_to_latex_math(en))
            lines.append(r"\begin{enumerate}")
            for opt in task.get("options") or []:
                lines.append(r"\item " + option_to_latex(str(opt)))
            lines.append(r"\end{enumerate}")
        lines.append(r"\end{enumerate}")
        lines.append("")

    lines.extend(
        [
            r"\clearpage",
            r"\section*{Часть 2. Ответы}",
            "",
        ]
    )

    for group in data:
        topic = group.get("topic", "")
        sub = group.get("subtopic", "")
        lines.append(r"\subsection*{" + escape_tex_verbatim(f"{topic} — {sub}") + "}")
        lines.append(r"\begin{itemize}")
        for task in group.get("tasks", []):
            exam = (task.get("type") or "").strip().lower()
            num = task.get("n")
            num_s = str(num) if num is not None else "—"
            ans = escape_tex_verbatim(str(task.get("answer") or ""))
            item = f"{month_ru(exam).capitalize()}, № {num_s} — {ans}"
            lines.append(r"\item " + escape_tex_verbatim(item))
        lines.append(r"\end{itemize}")
        lines.append("")

    lines.append(r"\end{document}")

    with open(TEX_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Written {TEX_PATH}")


if __name__ == "__main__":
    main()

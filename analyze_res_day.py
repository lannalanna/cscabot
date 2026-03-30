"""Разбор data/res.txt за день или диапазон дат: экзамены, темы, подтемы, физика/химия.

Аргументы CLI:
  python analyze_res_day.py [YYYY-MM-DD]
  python analyze_res_day.py YYYY-MM-DD YYYY-MM-DD   # конец включительно
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RES_PATH = os.path.join(DATA_DIR, "res.txt")

EXAM_IDS = {"exam_25jan", "exam_21dec", "exam_mar15", "exam_mock"}
EXAM_SKIP = {"question", "start", "clear", "continue", "next_blocked", "summary", "entry"}

RESERVED = {
    "next",
    "hint_show",
    "hint_sub_show",
    "solution_show_topic",
    "solution_show_sub",
    "solution_show_math",
    "solution_vote_up_topic",
    "solution_vote_down_topic",
    "solution_vote_up_sub",
    "solution_vote_down_sub",
    "solution_vote_up_math",
    "solution_vote_down_math",
    "menu_physics",
    "menu_chemistry",
    "set_language",
    "set_language_by_text",
    "mockexamreject",
    "blocked",
    "next_blocked",
    "hint_sub_blocked",
    "hint_blocked",
    "start",
    "status",
    "explain",
    "invite",
    "end",
    "message",
    "llm_answer",
    "math_all_question",
    "math_hint_show",
}


def _is_task_enabled(x: dict) -> bool:
    try:
        topic = (x.get("topic") or "").strip().lower()
        typ = (x.get("type") or "").strip().lower()
    except Exception:
        return True
    if topic == "off" or typ == "off":
        return False
    return True


def load_kpb():
    data_path = os.path.join(DATA_DIR, "data.txt")
    with open(data_path, encoding="utf-8") as f:
        kpb_raw = json.load(f)
    kpb = [
        x
        for x in (kpb_raw if isinstance(kpb_raw, list) else [kpb_raw])
        if isinstance(x, dict) and _is_task_enabled(x)
    ]
    for name in ("data_physics.txt", "data_chemestry.txt"):
        p = os.path.join(DATA_DIR, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        kpb.extend(
            [
                x
                for x in (raw if isinstance(raw, list) else [raw])
                if isinstance(x, dict) and _is_task_enabled(x)
            ]
        )
    return kpb


def share(c: int, w: int) -> float | None:
    t = c + w
    return (w / t) if t else None


def _parse_ymd(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d")


def _date_range_set(start_s: str, end_s: str | None) -> tuple[frozenset[str], str]:
    start = _parse_ymd(start_s)
    if end_s is None:
        ds = frozenset({start.strftime("%Y-%m-%d")})
        label = start.strftime("%Y-%m-%d")
        return ds, label
    end = _parse_ymd(end_s)
    if end < start:
        start, end = end, start
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    ds = frozenset(days)
    label = f"{days[0]} — {days[-1]} ({len(days)} дн.)"
    return ds, label


def main() -> None:
    if len(sys.argv) >= 3:
        date_set, period_label = _date_range_set(sys.argv[1], sys.argv[2])
    elif len(sys.argv) >= 2:
        date_set, period_label = _date_range_set(sys.argv[1], None)
    else:
        date_set, period_label = _date_range_set("2026-03-27", None)

    kpb = load_kpb()
    topics: list[str] = []
    for k in kpb:
        t = k.get("topic")
        if t and t not in topics:
            topics.append(t)

    kapibara: dict[str, list] = {}
    for top in topics:
        kapibara[top] = [x for x in kpb if x.get("topic") == top]

    def subtopic_for(topic: str, j: int) -> tuple[str | None, object]:
        arr = kapibara.get(topic) or []
        if not (0 <= j < len(arr)):
            return None, None
        q = arr[j]
        sub = (q.get("subtopic") or "").strip() or "general"
        return sub, q.get("id")

    by_topic: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_sub: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    by_exam: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    math_all_tot = [0, 0]
    physics_tot = [0, 0]
    chem_tot = [0, 0]
    user_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def add(cnt: list[int], ok: int) -> None:
        cnt[1 - ok] += 1

    with open(RES_PATH, encoding="utf-8") as f:
        for line in f:
            if len(line) < 10 or line[:10] not in date_set:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            uid = parts[1]
            kind = parts[4]

            if kind in EXAM_IDS:
                if len(parts) < 9:
                    continue
                ev = parts[5]
                if ev in EXAM_SKIP or ev == "question":
                    continue
                try:
                    ansok = int(parts[7])
                except ValueError:
                    continue
                add(by_exam[kind], ansok)
                add(user_day[uid], ansok)
                continue

            if kind == "math_all_answer":
                if len(parts) < 9:
                    continue
                top = parts[5]
                try:
                    j = int(parts[6])
                    ansok = int(parts[8])
                except ValueError:
                    continue
                add(math_all_tot, ansok)
                add(by_topic[top], ansok)
                sub, _ = subtopic_for(top, j)
                if sub:
                    add(by_sub[(top, sub)], ansok)
                if top == "physics":
                    add(physics_tot, ansok)
                elif top == "chemistry":
                    add(chem_tot, ansok)
                add(user_day[uid], ansok)
                continue

            if kind in RESERVED or kind in EXAM_IDS:
                continue
            if len(parts) < 9:
                continue
            try:
                j = int(parts[5])
                ansok = int(parts[7])
            except ValueError:
                continue
            if ansok not in (0, 1):
                continue
            top = kind
            if top not in kapibara:
                continue
            add(by_topic[top], ansok)
            sub, _ = subtopic_for(top, j)
            if sub:
                add(by_sub[(top, sub)], ansok)
            if top == "physics":
                add(physics_tot, ansok)
            elif top == "chemistry":
                add(chem_tot, ansok)
            add(user_day[uid], ansok)

    print(f"Период (фильтр по дате в начале строки): {period_label}\n")

    print("Экзамены (по типу)")
    print("exam_id\tверно\tневерно\tдоля_неверных")
    for k in sorted(by_exam.keys()):
        c, w = by_exam[k]
        sh = share(c, w)
        print(f"{k}\t{c}\t{w}\t{sh:.4f}" if sh is not None else f"{k}\t{c}\t{w}\t")

    c, w = math_all_tot
    sh = share(c, w)
    print("\nРежим «вся математика» (math_all)")
    print(f"верно={c} неверно={w} доля_неверных={sh:.4f}" if sh is not None else f"верно={c} неверно={w}")

    print("\nТемы (тренировка по теме/подтеме + math_all)")
    print("тема\tверно\tневерно\tдоля_неверных")
    for top in sorted(by_topic.keys()):
        c, w = by_topic[top]
        if c + w == 0:
            continue
        sh = share(c, w)
        print(f"{top}\t{c}\t{w}\t{sh:.4f}")

    print("\nФизика и химия (сумма: обычная тренировка + math_all, без экзаменов)")
    for name, tot in [("physics", physics_tot), ("chemistry", chem_tot)]:
        c, w = tot
        sh = share(c, w)
        if sh is None:
            print(f"{name}\t— нет ответов за период")
        else:
            print(f"{name}\t{c}\t{w}\t{sh:.4f}")

    print("\nПодтемы (сортировка по числу ответов, убывание)")
    print("тема | подтема\tверно\tневерно\tдоля_неверных")
    sub_rows = [(by_sub[k][0] + by_sub[k][1], k, by_sub[k]) for k in by_sub]
    sub_rows.sort(reverse=True)
    for _, k, (c, w) in sub_rows:
        sh = share(c, w)
        print(f"{k[0]} | {k[1]}\t{c}\t{w}\t{sh:.4f}")

    print("\nПользователи за период (все ответы: экзамены + тренировки + math_all)")
    print("user_id\tверно\tневерно\tдоля_неверных")
    for uid in sorted(user_day.keys(), key=lambda x: int(x)):
        c, w = user_day[uid]
        sh = share(c, w)
        print(f"{uid}\t{c}\t{w}\t{sh:.4f}")
    total = sum(user_day[u][0] + user_day[u][1] for u in user_day)
    all_correct = sum(user_day[u][0] for u in user_day)
    all_wrong = sum(user_day[u][1] for u in user_day)
    tws = share(all_correct, all_wrong)
    print(f"\nВсего ответов за период: {total}")
    if tws is not None:
        print(f"Сводка: верно={all_correct} неверно={all_wrong} доля_неверных={tws:.4f}")


if __name__ == "__main__":
    main()

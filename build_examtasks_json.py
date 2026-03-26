"""Собирает examtasks.json из data/data.txt: type ∈ {dec, jan, mar, oct}, группы по теме+подтеме, русские названия."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import testbot

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "data.txt"
OUT_PATH = ROOT / "examtasks.json"

EXAM_TYPES = frozenset({"dec", "jan", "mar", "oct"})


def _avg_n(items: list[dict]) -> float:
    vals: list[float] = []
    for it in items:
        n = it.get("n")
        if n is None:
            continue
        try:
            vals.append(float(n))
        except (TypeError, ValueError):
            continue
    if not vals:
        return float("inf")
    return sum(vals) / len(vals)


def main() -> None:
    with open(DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    filtered = [
        x
        for x in raw
        if isinstance(x, dict) and (x.get("type") or "").strip().lower() in EXAM_TYPES
    ]

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in filtered:
        t = (item.get("topic") or "").strip()
        s = (item.get("subtopic") or "").strip()
        groups[(t, s)].append(item)

    rows: list[tuple[float, str, str, list[dict]]] = []
    for (topic, subtopic), tasks in groups.items():
        rows.append((_avg_n(tasks), topic, subtopic, tasks))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    out: list[dict] = []
    for _, topic, subtopic, tasks in rows:
        topic_ru = testbot._topic_display(topic, "ru")
        sub_ru = testbot._subtopic_display(topic, subtopic, "ru")
        loc_tasks: list[dict] = []
        for q in tasks:
            qt = dict(q)
            qt["topic"] = testbot._topic_display(q.get("topic") or topic, "ru")
            qt["subtopic"] = testbot._subtopic_display(
                q.get("topic") or topic,
                q.get("subtopic") or subtopic,
                "ru",
            )
            loc_tasks.append(qt)
        out.append({"topic": topic_ru, "subtopic": sub_ru, "tasks": loc_tasks})

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    n_tasks = sum(len(r["tasks"]) for r in out)
    print(f"Written {OUT_PATH}: {len(out)} groups, {n_tasks} tasks")


if __name__ == "__main__":
    main()

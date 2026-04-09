"""Собрать из data.txt JSON: Тема → Подтемы → список задач."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_TXT = ROOT / "data.txt"
OUT = ROOT / "data_topics_subtopics.json"


def main() -> None:
    raw = json.loads(DATA_TXT.read_text(encoding="utf-8"))
    items = [x for x in (raw if isinstance(raw, list) else [raw]) if isinstance(x, dict)]

    topic_order: list[str] = []
    sub_order: dict[str, list[str]] = defaultdict(list)
    seen_sub: dict[str, set[str]] = defaultdict(set)
    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for q in items:
        topic = (q.get("topic") or "").strip() or "(нет темы)"
        sub = (q.get("subtopic") or "").strip() or "(без подтемы)"

        if topic not in buckets:
            topic_order.append(topic)
        if sub not in seen_sub[topic]:
            seen_sub[topic].add(sub)
            sub_order[topic].append(sub)
        buckets[topic][sub].append(q)

    structure = []
    for t in topic_order:
        подтемы = []
        for s in sub_order[t]:
            подтемы.append({"Подтема": s, "задачи": buckets[t][s]})
        structure.append({"Тема": t, "Подтемы": подтемы})

    out_obj = {"описание": "Сгруппировано из data.txt: тема, подтема, полные объекты задач.", "структура": structure}
    OUT.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("written", OUT, "topics", len(structure))


if __name__ == "__main__":
    main()

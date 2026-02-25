#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Анализ номеров n у задач с type=dec в data/data.txt"""
import json
from collections import Counter

with open("data/data.txt", "r", encoding="utf-8") as f:
    data = json.load(f)

dec = [item for item in data if (item.get("type") or "").strip().lower() == "dec"]
dec_with_n = [
    (item.get("n"), item.get("topic"), item.get("subtopic", ""), (item.get("english") or "")[:55])
    for item in dec
]

dec_with_n.sort(key=lambda x: (x[0] if x[0] is not None else 0, x[1] or ""))

# Safe ASCII excerpt (no superscripts etc)
def safe(s, maxlen=40):
    if not s:
        return ""
    s = (s or "").replace("\n", " ").strip()[:maxlen]
    return s.encode("ascii", "replace").decode("ascii")

print("=== Total type=dec:", len(dec))
print()
print("n  | topic                  | subtopic      | excerpt")
print("---|------------------------|---------------|------------------------")
for n, topic, sub, eng in dec_with_n:
    tn = (topic or "")[:22]
    sn = (sub or "")[:13]
    en = safe(eng, 45)
    print("%2s | %-22s | %-13s | %s" % (str(n), tn, sn, en))

ns = [x[0] for x in dec_with_n]
valid_ns = [n for n in ns if n is not None]
print()
print("=== Анализ номеров n ===")
print("Уникальные n (по возрастанию):", sorted(set(valid_ns)))
print("Минимум n:", min(valid_ns) if valid_ns else None)
print("Максимум n:", max(valid_ns) if valid_ns else None)

cnt = Counter(valid_ns)
dupes = [n for n, c in cnt.items() if c > 1]
print("Дубликаты n (номер встречается >1 раза):", dupes if dupes else "нет")
for d in dupes:
    tasks = [t for t in dec_with_n if t[0] == d]
    for t in tasks:
        print("   n=%s: %s | %s" % (d, (t[1] or "")[:25], safe(t[3], 50)))

if valid_ns:
    full_range = set(range(min(valid_ns), max(valid_ns) + 1))
    present = set(valid_ns)
    missing = sorted(full_range - present)
    print("Пропущенные n в диапазоне [%d..%d]:" % (min(valid_ns), max(valid_ns)), missing if missing else "нет")

no_n = [x for x in dec_with_n if x[0] is None]
if no_n:
    print("Tasks without n:", len(no_n))
    for t in no_n[:8]:
        print("   ", (t[1] or ""), "|", safe(t[3], 55))

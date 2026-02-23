#!/usr/bin/env python3
"""
Скрипт: таблица m = 1..48 и сумма сложностей задач с type=jan и n = 1, 2, ..., m.
Запуск из корня бота: python exam25_cumulative_table.py
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(BASE_DIR, "data"))
data_path = os.path.join(DATA_DIR, "data.txt")

with open(data_path, "r", encoding="utf-8") as f:
    kpb = json.load(f)

# Собираем все задачи с type=jan: n -> difficulty
n_to_diff = {}
for item in kpb:
    if (item.get("type") or "").strip().lower() == "jan":
        n_val = item.get("n") or 0
        try:
            diff = int(item.get("difficulty") or 1)
        except (TypeError, ValueError):
            diff = 1
        if diff < 1:
            diff = 1
        n_to_diff[n_val] = diff

# Для m = 1..48: сумма сложностей задач с n = 1, 2, ..., m
rows = []
cumsum = 0
for m in range(1, 49):
    d_m = n_to_diff.get(m, 1)
    cumsum += d_m
    rows.append((m, d_m, cumsum))

total_difficulty = rows[-1][2]  # полная сумма сложностей всех 48 задач

# Вывод таблицы: сумма сложностей и (сумма / полная сумма) * 100
out_lines = [
    "m | сложность задачи m | сумма сложностей 1..m | % от макс. балла",
    "---|---------------------|----------------------|------------------",
]
for m, d, s in rows:
    pct = (s / total_difficulty * 100) if total_difficulty else 0
    out_lines.append(f"{m:2} | {d:19} | {s:3} | {pct:6.2f}")

table = "\n".join(out_lines)
print(table)

# Сохраняем в файл
out_path = os.path.join(DATA_DIR, "exam25_cumulative_difficulty.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(table)
print(f"\nТаблица сохранена: {out_path}")

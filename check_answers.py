#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Проверка задач: согласованность поля answer с options и поиск подозрительных записей.
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
FILES = ["data2.txt", "data_jan2.txt", "data_physics.txt"]

ANSWER_LETTERS = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}


def check_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    errors = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append((i, "not a dict", item))
            continue
        opts = item.get("options") or []
        ans = (item.get("answer") or "").strip().upper()
        eng = (item.get("english") or "")[:60]
        topic = item.get("topic", "")

        # Нет ответа
        if not ans:
            errors.append((i, "no answer", eng, topic))
            continue
        # Ответ не A–E
        if ans not in ANSWER_LETTERS:
            errors.append((i, f"answer not A-E: '{ans}'", eng, topic))
            continue
        idx = ANSWER_LETTERS[ans]
        # Вариантов меньше, чем нужно
        if len(opts) <= idx:
            errors.append((i, f"answer {ans} but only {len(opts)} options", eng, topic))
            continue
        # Опция по индексу не начинается с правильной буквы (A., B., ...)
        opt_text = opts[idx].strip()
        expected_prefix = f"{ans}."
        if not opt_text.upper().startswith(expected_prefix) and not opt_text.startswith(f"{ans}."):
            errors.append((i, f"option[{idx}] doesn't start with '{ans}.': {opt_text[:50]}", eng, topic))
    return data, errors


def main():
    all_errors = []
    for fname in FILES:
        path = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(path):
            print(f"Пропуск (нет файла): {path}")
            continue
        data, errors = check_file(path)
        print(f"\n=== {fname} (всего {len(data)} задач) ===")
        if not errors:
            print("  Согласованность ответов и вариантов: нарушений не найдено.")
        else:
            for idx, err_type, *rest in errors:
                rest_str = " | ".join(str(x) for x in rest)
                print(f"  Задача {idx}: {err_type} | {rest_str}")
            all_errors.append((fname, errors))

    # Сводка
    total_err = sum(len(e[1]) for e in all_errors)
    if total_err:
        print(f"\nВсего нарушений: {total_err}")
    else:
        print("\nВсе проверенные файлы: нарушений согласованности нет.")
    return all_errors


if __name__ == "__main__":
    main()

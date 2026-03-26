#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Скрипт добавляет поле subtopic в data.txt и data_jan.txt на основе topic и текста вопроса.
"""
import json
import re
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def infer_subtopic(item):
    """Определяет subtopic по topic и тексту вопроса."""
    topic = (item.get("topic") or "").strip().lower()
    eng = (item.get("english") or "").lower()
    chn = (item.get("chinese") or "")
    text = eng + " " + chn

    # Уже есть осмысленный subtopic — оставляем (исправляем только явные опечатки)
    existing = item.get("subtopic", "").strip()
    if existing and existing not in ("sse",):
        if existing == "sse":
            return "ellipse"  # исправление опечатки
        return existing

    # По topic и ключевым словам
    if topic == "sets":
        if "∩" in chn or "intersection" in eng or "\\ a" in eng or "\\ b" in eng:
            return "set operations"
        if "∪" in chn or "union" in eng or " u " in eng or " a u b" in eng:
            return "set operations"
        if "∈" in chn or "belongs" in eng or "n," in eng or "q," in eng or "z," in eng or "r]" in eng:
            return "membership"
        if "interval" in eng or "(" in chn and ")" in chn and ("1" in chn or "2" in chn):
            return "intervals"
        return "set operations"

    if topic == "inequalities":
        if "|x" in text or "|x+" in chn or "absolute" in eng:
            return "absolute value"
        if "x²" in text or "quadratic" in eng or "x^2" in eng:
            return "quadratic inequalities"
        if "rational" in eng or "/" in chn and "≤" in chn or "≥" in chn:
            return "rational inequalities"
        return "inequalities"

    if topic == "functions":
        if "domain" in eng or "定义域" in chn:
            return "domain"
        if "inverse" in eng or "反函数" in chn:
            return "inverse functions"
        if "odd" in eng or "even" in eng or "奇" in chn or "偶" in chn:
            return "function properties"
        if "monotonic" in eng or "increasing" in eng or "decreasing" in eng or "增" in chn or "减" in chn:
            return "function monotonicity"
        if "graph" in eng or "图像" in chn:
            return "function graphs"
        if "exponential" in eng or "aˣ" in text or "a^x" in eng:
            return "exponential functions"
        if "cos x" in eng or "sin x" in eng or "y = cos" in eng or "y = sin" in eng:
            return "trigonometric functions"
        if "equality" in eng or "equal" in eng:
            return "function equality"
        return "functions"

    if topic == "geometry":
        if "distance" in eng or "距离" in chn or "|ab|" in eng:
            return "distance formula"
        if "line" in eng or "直线" in chn or "equation of l" in eng or "slope" in eng or "斜率" in chn:
            return "lines"
        if "circle" in eng or "圆" in chn or "x² + y²" in text:
            return "circle"
        if "quadrant" in eng or "象限" in chn:
            return "coordinate geometry"
        if "symmetr" in eng or "对称" in chn:
            return "coordinate geometry"
        if "vector" in eng or "向量" in chn:
            return "vectors"
        if "hyperbola" in eng or "双曲线" in chn:
            return "hyperbola"
        return "analytic geometry"

    if topic == "trigonometry (simple)":
        if "sin α" in text or "cos α" in text or "tan α" in text or "quadrant" in eng:
            return "trigonometric values"
        return "trigonometric values"

    if topic == "trigonometry":
        if "sin 2α" in text or "cos 2α" in text or "double" in eng:
            return "double angle formula"
        if "sin(α/2)" in text or "cos(α/2)" in text or "half-angle" in eng:
            return "half-angle formula"
        if "tan α" in eng and ("sin" in eng or "cos" in eng) and "2" not in eng:
            return "trigonometric expressions"
        if "sin(π" in text or "cos(π" in text or "identity" in eng or "correct" in eng:
            return "trigonometric identities"
        if "terminal side" in eng or "point (" in eng and ")" in eng:
            return "trigonometric functions"
        return "trigonometric functions"

    if topic == "sequences":
        d = item.get("difficulty")
        try:
            di = int(d) if d is not None else 0
        except (TypeError, ValueError):
            di = 0
        if di == 5:
            return "hard tasks"
        return "simple tasks"

    if topic == "probability":
        if "combined" in eng or "event" in eng:
            return "Probability of Combined Events"
        return "Simple Probability"

    if topic == "complex numbers":
        d = item.get("difficulty")
        try:
            di = int(d) if d is not None else 0
        except (TypeError, ValueError):
            di = 0
        if di == 5:
            return "hard tasks"
        return "simple tasks"

    if topic == "logarithmic functions" or "logarithmic" in topic:
        return "logarithms"

    if topic == "conic curves":
        if "parabola" in eng or "抛物线" in chn or "y² = 4x" in text:
            return "parabola"
        if "hyperbola" in eng or "双曲线" in chn and "x²" in text:
            return "hyperbola"
        if "ellipse" in eng or "椭圆" in chn:
            return "ellipse"
        if "circle" in eng or "圆" in chn or "x² + y²" in text:
            return "circle"
        return "conic curves"

    if "algebraic and geometric mean" in topic or "geometric mean" in topic:
        if "arithmetic" in eng or "算术" in chn:
            return "arithmetic mean"
        return "geometric mean"

    return topic.replace(" ", "_") if topic else "general"


def process_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    updated = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        old_sub = item.get("subtopic")
        new_sub = infer_subtopic(item)
        item["subtopic"] = new_sub
        if old_sub != new_sub:
            updated += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return len(data), updated


def main():
    for fname in ("data.txt", "data_jan.txt", "data2.txt", "data_jan2.txt"):
        path = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(path):
            print(f"Пропуск (нет файла): {path}")
            continue
        total, updated = process_file(path)
        print(f"{fname}: записей {total}, добавлено/изменено subtopic: {updated}")


if __name__ == "__main__":
    main()

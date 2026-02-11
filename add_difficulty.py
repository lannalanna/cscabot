import json
import os


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "data", "data.txt")


TOPIC_BASE_DIFFICULTY = {
    "sets": 1,
    "inequalities": 3,
    "functions": 3,
    "trigonometry (simple)": 2,
    "trigonometry": 3,
    "geometry": 3,
    "conic curves": 4,
    "logarithmic functions": 4,
    "Algebraic and geometric mean": 3,
    "sequences": 3,
    "complex numbers": 4,
    "probability": 3,
    "physics": 4,
}


def estimate_difficulty(item: dict) -> int:
    """Грубая эвристика сложности от 1 до 5."""
    topic = item.get("topic") or ""
    sub = (item.get("subtopic") or "").strip()
    q_type = (item.get("type") or "").strip()

    base = TOPIC_BASE_DIFFICULTY.get(topic, 3)

    # Коррекция по подтеме
    easier_subs = {
        "set operations",
        "trigonometric values",
        "properties",
        "functions properties",
        "Function domain",
        "domain",
        "circle",
    }
    harder_subs = {
        "rational inequalities",
        "quadratic inequalities",
        "hyperbola",
        "ellipse",
        "logarithms",
        "complex numbers",
        "sequence general term",
        "other sequences",
    }

    if sub in easier_subs:
        base -= 1
    elif sub in harder_subs:
        base += 1

    # Коррекция по типу
    if q_type == "choice":
        base -= 1
    elif q_type == "extra":
        # дополнительные задачи обычно не самые простые,
        # но не делаем их автоматически максимальными
        base += 0

    # Ограничиваем диапазон 1..5
    if base < 1:
        base = 1
    if base > 5:
        base = 5
    return int(base)


def main() -> None:
    if not os.path.exists(DATA_PATH):
        raise SystemExit(f"data.txt not found at {DATA_PATH}")

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise SystemExit("data.txt should contain a JSON array")

    for item in data:
        if not isinstance(item, dict):
            continue
        item["difficulty"] = estimate_difficulty(item)

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    main()


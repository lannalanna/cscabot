import json
import os
import glob


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")


def load_items(path):
    """Загружает задачи из файла (список словарей). Поддерживает JSON-массив и JSON-построчно."""
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt:
        return []

    # Пытаемся распарсить как один JSON (массив или объект)
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # Пытаемся распарсить как JSON-строки по одной на строку
    items = []
    for line in txt.splitlines():
        s = line.strip().rstrip(",")
        if not s or s in ("[", "]"):
            continue
        try:
            items.append(json.loads(s))
        except Exception:
            continue
    return items


def main():
    # Собираем исходные файлы:
    #  - все .txt в data/, кроме уже агрегированного data.txt
    #  - physics из корня, если есть
    #  - все тематические .json в корне
    source_files = []

    # Все txt в data/, кроме data.txt
    for p in glob.glob(os.path.join(DATA_DIR, "*.txt")):
        if os.path.basename(p).lower() == "data.txt":
            continue
        source_files.append(p)

    # Физика из корня
    phys = os.path.join(ROOT, "data_physics.txt")
    if os.path.exists(phys):
        source_files.append(phys)

    # Все json в корне (темы)
    for p in glob.glob(os.path.join(ROOT, "*.json")):
        source_files.append(p)

    all_items = []
    seen_keys = set()

    for path in source_files:
        for obj in load_items(path):
            if not isinstance(obj, dict):
                continue
            # Условная дедупликация по (topic, english, n, type)
            key = (
                obj.get("topic"),
                obj.get("english"),
                obj.get("n"),
                obj.get("type"),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_items.append(obj)

    # Сортировка: по теме, затем по подтеме, затем по типу и номеру
    def sort_key(o):
        topic = (o.get("topic") or "").lower()
        sub = (o.get("subtopic") or "general").lower()
        typ = (o.get("type") or "").lower()
        n = o.get("n")
        try:
            n_int = int(n)
        except Exception:
            n_int = 10 ** 9
        eng = (o.get("english") or "")
        return (topic, sub, typ, n_int, eng)

    all_items.sort(key=sort_key)

    out_path = os.path.join(DATA_DIR, "data.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=4)

    print("TOTAL_ITEMS", len(all_items))
    print("WROTE", out_path)


if __name__ == "__main__":
    main()


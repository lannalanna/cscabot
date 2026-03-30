import json
from collections import defaultdict

DATA_PATH = r"c:\Users\Sveta\Desktop\CSCA\bot\data\data.txt"


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    mp = defaultdict(set)
    for it in data:
        if not isinstance(it, dict):
            continue
        t = (it.get("topic") or "").strip()
        s = (it.get("subtopic") or "").strip() or "general"
        if t:
            mp[t].add(s)

    for t in sorted(mp.keys()):
        print(t)
        for s in sorted(mp[t]):
            print("  -", s)


if __name__ == "__main__":
    main()


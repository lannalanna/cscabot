import json
import re
from collections import defaultdict

path = r"c:\Users\Sveta\Desktop\CSCA\bot\data\data.txt"

digit_re = re.compile(r"\d+")


def norm(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    s = digit_re.sub("#", s)
    s = re.sub(r"\s+", " ", s)
    return s


def main():
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    keys = defaultdict(set)  # group_key -> set of n values
    for it in data:
        if not isinstance(it, dict):
            continue
        topic = (it.get("topic") or "").strip()
        if topic == "physics":
            continue
        sub = (it.get("subtopic") or "").strip() or "general"
        en = norm(it.get("english", ""))
        chi = norm(it.get("chinese", ""))
        opts = tuple(norm(o) for o in (it.get("options") or []))
        n = it.get("n")
        keys[(topic, sub, en, chi, opts)].add(n)

    bad = []
    for k, v in keys.items():
        # If within same digit-normalized group n differs, then using n in id would break the "digits-only => same id"
        if len(v) > 1:
            bad.append((k, sorted(list(v), key=lambda x: (x is None, x))))

    print("total groups:", len(keys))
    print("bad groups:", len(bad))
    for k, v in bad[:10]:
        print("topic:", k[0], "| sub:", k[1], "| n_values:", v[:8])


if __name__ == "__main__":
    main()


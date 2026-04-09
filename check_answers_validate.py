"""Проверка: поле answer (A/B/C/D) должно соответствовать одному из options."""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
path = os.path.join(DATA_DIR, "data.txt")

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

errors = []
for idx, item in enumerate(data):
    opts = item.get("options") or []
    ans = item.get("answer")
    if ans is None:
        errors.append((idx, "no_answer", item.get("topic"), item.get("subtopic"), None))
        continue
    ans = str(ans).strip().upper()
    if ans not in "ABCDE":
        errors.append((idx, "invalid_letter", item.get("topic"), item.get("subtopic"), ans))
        continue
    found = False
    for o in opts:
        s = str(o).strip()
        if s.startswith(ans + ".") or s.startswith(ans + " "):
            found = True
            break
    if not found:
        errors.append((idx, "letter_not_in_options", item.get("topic"), item.get("subtopic"), ans))

print("Total tasks:", len(data))
print("Errors:", len(errors))
for e in errors[:50]:
    print("  idx=%s %s topic=%s sub=%s ans=%s" % (e[0], e[1], e[2], e[3], e[4]))
if len(errors) > 50:
    print("  ... and", len(errors) - 50, "more")

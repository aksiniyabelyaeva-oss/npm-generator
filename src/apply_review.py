import json
import sys
from pathlib import Path

BOX = ("x0", "z0", "x1", "z1")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def apply(facade, rv):
    """Применяет исправления специалиста к одному фасаду."""
    delete = set(rv.get("delete", []))
    types = rv.get("type", {})
    edits = rv.get("edit", {})
    items = []
    for it in facade["items"]:
        if it["id"] in delete:
            continue
        key = str(it["id"])
        fixed = dict(it)
        if key in types:
            fixed["type"] = types[key]
        for k in BOX:
            if k in edits.get(key, {}):
                fixed[k] = edits[key][k]
        changed = key in types or key in edits
        fixed["source"] = "corrected" if changed else "auto"
        items.append(fixed)
    for add in rv.get("add", []):
        items.append({"type": add["type"], **{k: add[k] for k in BOX}, "source": "manual"})
    for i, it in enumerate(items, start=1):
        it["id"] = i
    return {"bbox": facade["bbox"], "items": items}


if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    raw = load(data_dir / "windows_raw.json")
    review = load(data_dir / "windows_review.json")

    checked = {name: apply(f, review.get(name, {})) for name, f in raw.items()}
    for name, f in checked.items():
        print(f"{name}: проёмов {len(f['items'])}")

    out = data_dir / "windows_checked.json"
    out.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Эталон сохранён: {out}")
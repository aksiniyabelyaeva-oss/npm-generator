import json
import sys
from datetime import date
from pathlib import Path

COORDS = ("x0", "z0", "x1", "z1")

if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    facade, item_id = sys.argv[2], int(sys.argv[3])
    changes = dict(arg.split("=", 1) for arg in sys.argv[4:])
    note = changes.pop("note", "")

    path = data_dir / "windows_checked.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    item = next(it for it in data[facade]["items"] if it["id"] == item_id)

    old = {k: item.get(k) for k in changes}
    for k, v in changes.items():
        item[k] = int(v) if k in COORDS else v
    item["source"] = "manual"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log_path = data_dir / "gold_fixes.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    log.append({"date": str(date.today()), "facade": facade, "id": item_id,
                "old": old, "new": {k: item[k] for k in changes}, "note": note})
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{facade} №{item_id}: {old} -> {{{', '.join(f'{k}: {item[k]}' for k in changes)}}}")
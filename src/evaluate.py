import json
import sys
from pathlib import Path


def box(it):
    return (it["x0"], it["z0"], it["x1"], it["z1"])


def overlap(a, b):
    """Какая доля меньшего прямоугольника перекрыта другим."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / smaller if smaller else 0


def compare(preds, truths):
    """Сопоставляет найденное с эталоном: каждому эталону — не больше одного найденного."""
    used = set()
    tp = type_ok = 0
    for p in preds:
        best, best_ov = None, 0.5
        for i, t in enumerate(truths):
            if i not in used and overlap(box(p), box(t)) >= best_ov:
                best, best_ov = i, overlap(box(p), box(t))
        if best is not None:
            used.add(best)
            tp += 1
            type_ok += p["type"] == truths[best]["type"]
    return tp, len(preds) - tp, len(truths) - tp, type_ok


if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    pred = json.loads((data_dir / "windows_raw.json").read_text(encoding="utf-8"))
    truth = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))

    TP = FP = FN = TYPE_OK = 0
    for name in truth:
        tp, fp, fn, ok = compare(pred[name]["items"], truth[name]["items"])
        print(f"{name}: верно {tp:2}   лишнее {fp:2}   пропущено {fn:2}")
        TP, FP, FN, TYPE_OK = TP + tp, FP + fp, FN + fn, TYPE_OK + ok

    precision = TP / (TP + FP)
    recall = TP / (TP + FN)
    f1 = 2 * precision * recall / (precision + recall)
    print(f"\nPrecision: {precision:.0%}   Recall: {recall:.0%}   F1: {f1:.0%}")
    print(f"Тип угадан верно: {TYPE_OK} из {TP}")
"""Сравнивает найденные окна с эталоном (windows_checked.json).

Запуск: python src/evaluate.py data/project_01 [raw | raw_framed]

Две метрики совпадения рамок:
- «перекрытие» — какая доля меньшей рамки накрыта другой. Мягкая: окно
  без фрамуги всё равно засчитывается, если лежит внутри эталона.
- IoU (пересечение / объединение) — строгая: ловит и неверный размер.
"""
import json
import sys
from pathlib import Path


def box(it):
    return (it["x0"], it["z0"], it["x1"], it["z1"])


def intersection(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def area(a):
    return (a[2] - a[0]) * (a[3] - a[1])


def overlap(a, b):
    """Какая доля меньшего прямоугольника перекрыта другим."""
    smaller = min(area(a), area(b))
    return intersection(a, b) / smaller if smaller else 0


def iou(a, b):
    """Пересечение, делённое на объединение: 1 — рамки совпали полностью."""
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union else 0


def compare(preds, truths, score):
    """Сопоставляет найденное с эталоном: каждому эталону — не больше одного найденного."""
    used = set()
    tp = type_ok = 0
    for p in preds:
        best, best_score = None, 0.5
        for i, t in enumerate(truths):
            s = score(box(p), box(t))
            if i not in used and s >= best_score:
                best, best_score = i, s
        if best is not None:
            used.add(best)
            tp += 1
            type_ok += p["type"] == truths[best]["type"]
    return tp, len(preds) - tp, len(truths) - tp, type_ok


def f1_line(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return f"Precision {precision:.0%}   Recall {recall:.0%}   F1 {f1:.0%}"


if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    pred_name = sys.argv[2] if len(sys.argv) > 2 else "raw"
    pred = json.loads((data_dir / f"windows_{pred_name}.json").read_text(encoding="utf-8"))
    truth = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))
    print(f"Сравнение windows_{pred_name}.json с windows_checked.json")

    for title, score in (("перекрытие ≥ 0.5", overlap), ("IoU ≥ 0.5", iou)):
        TP = FP = FN = TYPE_OK = 0
        print(f"\n[{title}]")
        for name in truth:
            tp, fp, fn, ok = compare(pred[name]["items"], truth[name]["items"], score)
            print(f"  {name}: верно {tp:2}   лишнее {fp:2}   пропущено {fn:2}")
            TP, FP, FN, TYPE_OK = TP + tp, FP + fp, FN + fn, TYPE_OK + ok
        print(f"  {f1_line(TP, FP, FN)}   тип угадан: {TYPE_OK} из {TP}")

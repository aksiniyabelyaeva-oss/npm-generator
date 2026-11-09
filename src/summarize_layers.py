import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
from ezdxf import bbox

PEN_SUFFIX = re.compile(r"_Ном\._пера__\d+$")


def base_layer(name):
    """Убирает суффикс пера ArchiCAD: 'Покрытия_Ном._пера__7' -> 'Покрытия'."""
    return PEN_SUFFIX.sub("", name)


def layer_texts(items, limit=5):
    """Первые несколько текстов на слое."""
    texts = []
    for e in items:
        if e.dxftype() in ("TEXT", "MTEXT"):
            texts.append(e.plain_text())
            if len(texts) == limit:
                break
    return texts


def layer_box(items):
    """Где лежит слой и какого он размера: ([min_x, min_y], [ширина, высота])."""
    try:
        box = bbox.extents(items, fast=True)
    except Exception:
        return None, None
    if not box.has_data:
        return None, None
    min_xy = [round(box.extmin.x, 1), round(box.extmin.y, 1)]
    size_xy = [round(box.size.x, 1), round(box.size.y, 1)]
    return min_xy, size_xy


def summarize(path):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    entities = defaultdict(list)
    for e in msp:
        entities[e.dxf.layer].append(e)

    result = {}
    for name, items in entities.items():
        types = Counter(e.dxftype() for e in items)
        closed = sum(1 for e in items if e.dxftype() == "LWPOLYLINE" and e.closed)
        min_xy, size_xy = layer_box(items)
        result[name] = {
            "base": base_layer(name),
            "count": len(items),
            "types": dict(types),
            "closed_polylines": closed,
            "texts": layer_texts(items),
            "min_xy": min_xy,
            "size_xy": size_xy,
        }
    return result


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    for path in sorted(folder.glob("*.dxf")):
        summary = summarize(path)
        out = path.with_suffix(".summary.json")
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: {len(summary)} слоёв -> {out.name}")
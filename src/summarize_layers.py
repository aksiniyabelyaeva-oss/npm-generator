import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf import units as dxf_units

PEN_SUFFIX = re.compile(r"_Ном\._пера__\d+$")


def base_layer(name):
    """Убирает суффикс пера ArchiCAD: 'Покрытия_Ном._пера__7' -> 'Покрытия'."""
    return PEN_SUFFIX.sub("", name)


def is_closed(e):
    """Замкнут ли контур: у новых и у старых полилиний это проверяется по-разному."""
    if e.dxftype() == "LWPOLYLINE":
        return e.closed
    if e.dxftype() == "POLYLINE":
        return e.is_closed
    return False


def entity_texts(e):
    """Тексты объекта: обычный текст или атрибуты блока."""
    if e.dxftype() in ("TEXT", "MTEXT"):
        return [e.plain_text()]
    if e.dxftype() == "INSERT":
        return [a.dxf.text for a in e.attribs]
    return []


def layer_texts(items, limit=5):
    texts = []
    for e in items:
        for text in entity_texts(e):
            if text.strip():
                texts.append(text)
        if len(texts) >= limit:
            break
    return texts[:limit]


def layer_box(items):
    """Где лежит слой и какого он размера."""
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

    layers = {}
    for name, items in entities.items():
        min_xy, size_xy = layer_box(items)
        layers[name] = {
            "base": base_layer(name),
            "count": len(items),
            "types": dict(Counter(e.dxftype() for e in items)),
            "closed": sum(1 for e in items if is_closed(e)),
            "texts": layer_texts(items),
            "min_xy": min_xy,
            "size_xy": size_xy,
        }

    return {
        "file": path.name,
        "units": dxf_units.unit_name(doc.units),
        "layers": layers,
    }


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    for path in sorted(folder.glob("*.dxf")):
        summary = summarize(path)
        out = path.with_suffix(".summary.json")
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: {len(summary['layers'])} слоёв, единицы: {summary['units']}")
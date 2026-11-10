import json
import re
import sys
from pathlib import Path
from statistics import median

import ezdxf

from place_openings import FACADE_SIDE, roof_band

MARK = re.compile(r"([±+\-−])\s?(\d+)[.,](\d{3})")
MARGIN_X = 6000   # отметки стоят сбоку от фасада, ищем в полосе 6 м
MARGIN_Y = 3000


def mark_value(text):
    text = text.replace("%%p", "±").replace("%%P", "±")
    m = MARK.search(text)
    if not m:
        return None
    sign = -1 if m.group(1) in "-−" else 1
    return sign * float(f"{m.group(2)}.{m.group(3)}")


def all_marks(msp):
    """Все отметки на листе: (значение, x, y, откуда взята, слой)."""
    marks = []
    for e in msp.query("TEXT MTEXT"):
        v = mark_value(e.plain_text())
        if v is not None:
            p = e.dxf.insert
            marks.append((v, p.x, p.y, "текст", e.dxf.layer))
    for e in msp.query("INSERT"):
        p = e.dxf.insert   # у блока точка вставки обычно на самом уровне
        found = False
        for a in e.attribs:
            v = mark_value(a.dxf.text)
            if v is not None:
                marks.append((v, p.x, p.y, "атрибут блока", e.dxf.layer))
                found = True
        if found:
            continue
        try:
            inner = list(e.virtual_entities())
        except Exception:
            continue
        for ve in inner:
            if ve.dxftype() in ("TEXT", "MTEXT"):
                v = mark_value(ve.plain_text())
                if v is not None:
                    marks.append((v, p.x, p.y, "текст в блоке", e.dxf.layer))
                    break
    return marks


def distance(x, y, fb):
    """Насколько точка за пределами рамки фасада по x и по y (0 — внутри)."""
    dx = max(fb[0] - x, 0, x - fb[2])
    dy = max(fb[1] - y, 0, y - fb[3])
    return dx, dy


def nearest_facade(x, y, boxes):
    """К какому фасаду относится отметка: ближайший по горизонтали."""
    best = None
    for name, fb in boxes.items():
        dx, dy = distance(x, y, fb)
        if dx <= MARGIN_X and dy <= MARGIN_Y and (best is None or dx < best[0]):
            best = (dx, name)
    return best[1] if best else None


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    data_dir = dxf_path.parent.parent
    building = json.loads((data_dir / "building.json").read_text(encoding="utf-8"))
    checked = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))
    msp = ezdxf.readfile(dxf_path).modelspace()
    z_eave = building["parts"][0]["z_eave"]

    boxes = {name: checked[name]["bbox"] for name in FACADE_SIDE}
    marks = all_marks(msp)

    print(f"Всего отметок на листе: {len(marks)}")
    print("Рамки фасадов, мм:")
    for name, fb in boxes.items():
        print(f"  {name}: x {fb[0]:.0f}…{fb[2]:.0f}  y {fb[1]:.0f}…{fb[3]:.0f}")
    print("Отметки (значение, где лежит, ближайший фасад и расстояние до него, м):")
    for v, x, y, src, layer in sorted(marks, key=lambda m: (m[1], m[2])):
        name, (dx, dy) = min(((n, distance(x, y, fb)) for n, fb in boxes.items()),
                             key=lambda t: t[1][0] + t[1][1])
        print(f"  {v:+7.3f}  x {x:9.0f}  y {y:9.0f}  {src:14} {layer:20} "
              f"-> {name}: dx {dx / 1000:6.1f}  dy {dy / 1000:6.1f}")

    groups = {name: [] for name in FACADE_SIDE}
    for v, x, y, src, layer in marks:
        name = nearest_facade(x, y, boxes)
        if name:
            groups[name].append((v, y, src))

    for name, side in FACADE_SIDE.items():
        roof = roof_band(msp, boxes[name])
        if roof is None:
            print(f"\n{name}: кровля не найдена")
            continue
        zero_y = roof[2] - z_eave * 1000
        print(f"\n{name} -> {side}: отметок {len(groups[name])}")
        diffs = []
        for v, y, src in sorted(groups[name]):
            rel = (y - zero_y) / 1000
            diffs.append(rel - v)
            print(f"  отметка {v:+7.3f}  наша высота {rel:+7.3f}  разница {rel - v:+6.3f}  ({src})")
        if diffs:
            print(f"  медиана разницы: {median(diffs):+.3f}")
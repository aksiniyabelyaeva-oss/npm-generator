import json
import sys
from collections import defaultdict
from pathlib import Path

import ezdxf
from ezdxf import bbox

from hatch_colors import hatch_color
from place_openings import FACADE_LAYER, FACADE_SIDE, ROOF_COLOR, roof_band

WALL_COLORS = {"#A48F7A", "#D2AA6D", "#89756B"}   # стены и пристройки
GAP = 50   # куски ближе 5 см считаем одним, мм


def boxes(msp, fb, colors):
    """Заливки нужных цветов внутри рамки фасада: (цвет, x0, y0, x1, y1)."""
    found = []
    for e in msp.query("HATCH"):
        if e.dxf.layer != FACADE_LAYER:
            continue
        color = hatch_color(e)
        if color not in colors:
            continue
        b = bbox.extents([e], fast=True)
        if not b.has_data:
            continue
        x0, y0, x1, y1 = b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y
        if fb[0] - 1 <= x0 and x1 <= fb[2] + 1 and fb[1] - 1 <= y0 and y1 <= fb[3] + 1:
            found.append((color, x0, y0, x1, y1))
    return found


def merge(intervals):
    """Склеивает пересекающиеся отрезки по x."""
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1] + GAP:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    data_dir = dxf_path.parent.parent
    building = json.loads((data_dir / "building.json").read_text(encoding="utf-8"))
    checked = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))
    msp = ezdxf.readfile(dxf_path).modelspace()
    z_eave = building["parts"][0]["z_eave"]

    for name, side in FACADE_SIDE.items():
        fb = checked[name]["bbox"]
        roof = roof_band(msp, fb)
        if roof is None:
            print(f"\n{name}: кровля не найдена")
            continue
        zero_y = roof[2] - z_eave * 1000

        def m_x(x):
            return (x - fb[0]) / 1000

        def m_z(y):
            return (y - zero_y) / 1000

        print(f"\n{name} -> {side}  (x — метры от левого края фасада, z — высота)")
        print("  Кровля, куски:")
        height = fb[3] - fb[1]
        for _, x0, y0, x1, y1 in sorted(boxes(msp, fb, {ROOF_COLOR}), key=lambda b: b[1]):
            if y1 >= fb[1] + 0.5 * height:
                print(f"    x {m_x(x0):6.2f}…{m_x(x1):6.2f}  ширина {(x1 - x0) / 1000:5.2f}"
                      f"  z {m_z(y0):+6.2f}…{m_z(y1):+6.2f}")

        groups = defaultdict(list)
        for color, x0, y0, x1, y1 in boxes(msp, fb, WALL_COLORS):
            groups[(color, round(m_z(y1), 1))].append((x0, x1))
        print("  Стены (цвет, верх) -> отрезки по x:")
        for (color, top), spans in sorted(groups.items(), key=lambda g: (-g[0][1], g[0][0])):
            parts = ", ".join(f"{m_x(a):.2f}…{m_x(b):.2f}" for a, b in merge(spans))
            print(f"    {color}  верх {top:+5.1f}  ->  {parts}")
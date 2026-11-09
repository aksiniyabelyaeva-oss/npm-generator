import sys

import ezdxf
from ezdxf import bbox

from hatch_colors import hatch_color

if __name__ == "__main__":
    path = sys.argv[1]
    x0, y0, x1, y1 = map(float, sys.argv[2:6])
    msp = ezdxf.readfile(path).modelspace()

    for e in msp:
        b = bbox.extents([e], fast=True)
        if not b.has_data:
            continue
        if x0 <= b.extmin.x and b.extmax.x <= x1 and y0 <= b.extmin.y and b.extmax.y <= y1:
            color = hatch_color(e) if e.dxftype() == "HATCH" else f"ACI {e.dxf.color}"
            paths = len(e.paths) if e.dxftype() == "HATCH" else "-"
            print(f"{e.dxftype():11} слой: {e.dxf.layer:16} цвет: {color:10} "
                  f"контуров: {paths}   размер: {b.size.x:5.0f} x {b.size.y:5.0f}   "
                  f"от x={b.extmin.x:.0f} y={b.extmin.y:.0f}")
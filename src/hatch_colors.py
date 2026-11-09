import sys
from collections import defaultdict

import ezdxf
from ezdxf import bbox, colors


def hatch_color(e):
    """Цвет штриховки: точный RGB, если задан, иначе номер цвета AutoCAD."""
    if e.dxf.hasattr("true_color"):
        r, g, b = colors.int2rgb(e.dxf.true_color)
        return f"#{r:02X}{g:02X}{b:02X}"
    return f"ACI {e.dxf.color}"


if __name__ == "__main__":
    msp = ezdxf.readfile(sys.argv[1]).modelspace()
    stats = defaultdict(list)
    for e in msp:
        if e.dxftype() != "HATCH":
            continue
        b = bbox.extents([e], fast=True)
        if b.has_data:
            stats[(e.dxf.layer, hatch_color(e))].append((b.size.x, b.size.y))

    for (layer, color), sizes in sorted(stats.items(), key=lambda kv: -len(kv[1])):
        ws = sorted(s[0] for s in sizes)
        hs = sorted(s[1] for s in sizes)
        mid = len(sizes) // 2
        print(f"{layer:18} {color:10} штук: {len(sizes):3}   "
              f"типичный размер: {ws[mid]:6.0f} x {hs[mid]:6.0f}   "
              f"самый большой: {max(ws):6.0f} x {max(hs):6.0f}")
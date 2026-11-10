import json
import sys
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
from ezdxf import bbox
from ezdxf import path as ezpath
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.patches import Rectangle

from hatch_colors import hatch_color

SASH_LAYER = "3_153 Серый тем"
PAD_X, PAD_DOWN, PAD_UP = 400, 400, 1800   # запас вокруг окна, мм


def overlaps(b, view):
    return not (b[2] < view[0] or b[0] > view[2] or b[3] < view[1] or b[1] > view[3])


def entity_box(e):
    b = bbox.extents([e], fast=True)
    if not b.has_data:
        return None
    return b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y


def pick_item(items, wanted):
    """Окно по номеру или, если номер не задан, самое нижнее обычное окно."""
    if wanted is not None:
        return next(it for it in items if it["id"] == wanted)
    windows = [it for it in items if it["type"] == "window"]
    return min(windows, key=lambda it: it["z0"])


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    facade = sys.argv[2] if len(sys.argv) > 2 else "facade_1"
    wanted = int(sys.argv[3]) if len(sys.argv) > 3 else None
    data_dir = dxf_path.parent.parent
    checked = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))
    item = pick_item(checked[facade]["items"], wanted)
    view = (item["x0"] - PAD_X, item["z0"] - PAD_DOWN,
            item["x1"] + PAD_X, item["z1"] + PAD_UP)

    msp = ezdxf.readfile(dxf_path).modelspace()
    fig, ax = plt.subplots(figsize=(7, 9))

    for e in msp.query("HATCH"):
        b = entity_box(e)
        if b is None or not overlaps(b, view):
            continue
        color = hatch_color(e)
        face = color if color.startswith("#") else "lightgray"
        for p in ezpath.from_hatch(e):
            pts = [(v.x, v.y) for v in p.flattening(10)]
            if len(pts) >= 3:
                ax.add_patch(MplPolygon(pts, closed=True, facecolor=face,
                                        edgecolor="none", alpha=0.6))

    for e in msp.query("LINE LWPOLYLINE POLYLINE ARC CIRCLE"):
        b = entity_box(e)
        if b is None or not overlaps(b, view):
            continue
        try:
            p = ezpath.make_path(e)
        except Exception:
            continue
        pts = [(v.x, v.y) for v in p.flattening(10)]
        if len(pts) >= 2:
            sash = e.dxf.layer == SASH_LAYER
            ax.plot([q[0] for q in pts], [q[1] for q in pts],
                    color="navy" if sash else "black", linewidth=1.2 if sash else 0.5)

    ax.add_patch(Rectangle((item["x0"], item["z0"]), item["x1"] - item["x0"],
                           item["z1"] - item["z0"], fill=False,
                           edgecolor="red", linewidth=2))
    ax.set_xlim(view[0], view[2])
    ax.set_ylim(view[1], view[3])
    ax.set_aspect("equal")
    ax.set_title(f"{facade}, окно {item['id']}: красное — наша рамка, синее — слой створок")
    out = data_dir / f"zoom_{facade}_{item['id']}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Сохранено: {out}")
import sys
from statistics import median

import ezdxf
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from find_glass import cluster, pane_boxes
from hatch_colors import hatch_color

LAYER = "3_стекла"
FRAME = "#F1ECE1"     # кремовый цвет наличников


def path_points(path):
    """Точки контура штриховки (прямые и дуговые участки)."""
    if hasattr(path, "vertices"):
        return [(v[0], v[1]) for v in path.vertices]
    pts = []
    for edge in path.edges:
        for name in ("start", "end", "start_point", "end_point"):
            p = getattr(edge, name, None)
            if p is not None:
                pts.append((p[0], p[1]))
    return pts


def box(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


if __name__ == "__main__":
    msp = ezdxf.readfile(sys.argv[1]).modelspace()

    boxes = pane_boxes(msp, LAYER)
    size = median(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    facades = cluster(boxes, gap=2 * size)
    facades.sort(key=lambda g: (g[0][0], g[0][1]))

    WALLS = {"#F1ECE1", "#A48F7A", "#D2AA6D", "#89756B"}   # наличники и стены

    def window_like(c):
        w, h = c[2] - c[0], c[3] - c[1]
        return 600 < w < 2500 and 900 < h < 3200 and 0.8 < h / w < 3.0

    def overlap(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        return ix * iy / min(area(a), area(b))

    candidates = []
    for e in msp:
        if e.dxf.layer != LAYER or e.dxftype() != "HATCH":
            continue
        color = hatch_color(e)
        if color not in WALLS:
            continue
        contours = [box(path_points(p)) for p in e.paths if path_points(p)]
        if not contours:
            continue
        outer = max(contours, key=area)
        if color == FRAME:
            candidates.append(outer)                         # сплошной наличник
        candidates += [c for c in contours if c is not outer]  # дырки в заливке
    candidates = [c for c in candidates if window_like(c)]

    windows = []                                             # убираем дубли
    for c in sorted(candidates, key=area, reverse=True):
        if all(overlap(c, w) < 0.5 for w in windows):
            windows.append(c)

    hatches = [box(path_points(p)) for e in msp
               if e.dxf.layer == LAYER and e.dxftype() == "HATCH"
               for p in e.paths if path_points(p)]

    for n, (fb, _) in enumerate(facades, start=1):
        inside = [w for w in windows if fb[0] <= w[0] <= fb[2] and fb[1] <= w[1] <= fb[3]]
        print(f"Фасад {n}: окон {len(inside)}")

        fig, ax = plt.subplots(figsize=(14, 7))
        for c in hatches:
            if fb[0] <= c[0] <= fb[2] and fb[1] <= c[1] <= fb[3]:
                ax.add_patch(Rectangle((c[0], c[1]), c[2] - c[0], c[3] - c[1],
                                       fill=False, color="lightgray", linewidth=0.6))
        for w in inside:
            ax.add_patch(Rectangle((w[0], w[1]), w[2] - w[0], w[3] - w[1],
                                   color="tab:blue", alpha=0.6))
        ax.set_xlim(fb[0], fb[2])
        ax.set_ylim(fb[1], fb[3])
        ax.set_aspect("equal")
        ax.set_title(f"Фасад {n}: найдено окон {len(inside)}")
        plt.savefig(f"facade_{n}.png", dpi=120)
        plt.close(fig)

    print(f"Всего окон: {len(windows)}. Картинки: facade_1.png ... facade_{len(facades)}.png")
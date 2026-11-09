import sys
from statistics import median

import ezdxf
import matplotlib.pyplot as plt
from ezdxf import bbox
from matplotlib.patches import Rectangle


def pane_boxes(msp, layer):
    """Прямоугольник каждой штриховки стекла: (x0, y0, x1, y1)."""
    boxes = []
    for e in msp:
        if e.dxf.layer == layer and e.dxftype() == "HATCH":
            b = bbox.extents([e], fast=True)
            if b.has_data:
                boxes.append((b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y))
    return boxes


def touches(a, b, gap):
    return a[0] <= b[2] + gap and a[2] >= b[0] - gap and a[1] <= b[3] + gap and a[3] >= b[1] - gap


def union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def cluster(boxes, gap):
    """Объединяет близкие прямоугольники в группы, пока группы меняются."""
    groups = [[b, [b]] for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if touches(groups[i][0], groups[j][0], gap):
                    groups[i][0] = union(groups[i][0], groups[j][0])
                    groups[i][1] += groups[j][1]
                    groups.pop(j)
                    merged = True
                    break
            if merged:
                break
    return groups


if __name__ == "__main__":
    path, layer = sys.argv[1], "3_стекла"
    msp = ezdxf.readfile(path).modelspace()
    boxes = pane_boxes(msp, layer)
    size = median(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    groups = cluster(boxes, gap=2 * size)
    groups.sort(key=lambda g: (g[0][0], g[0][1]))

    print(f"Стёкол: {len(boxes)}, типичный размер: {size:.0f}, групп: {len(groups)}")
    fig, ax = plt.subplots(figsize=(14, 9))
    for n, (gb, members) in enumerate(groups, start=1):
        w, h = gb[2] - gb[0], gb[3] - gb[1]
        print(f"Группа {n}: стёкол {len(members):3}   ширина {w:8.0f}   высота {h:7.0f}")
        for b in members:
            ax.add_patch(Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1], fill=False))
        ax.text(gb[0], gb[3], str(n), color="red", fontsize=14)
    ax.autoscale()
    ax.set_aspect("equal")
    plt.savefig("glass_plot.png", dpi=150)
    print("Сохранено: glass_plot.png")
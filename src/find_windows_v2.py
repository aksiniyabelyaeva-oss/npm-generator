import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import ezdxf
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

from find_glass import cluster, pane_boxes, touches, union

FACADE_LAYER = "3_стекла"         # по нему находим, где какой фасад
SASH_LAYER = "3_153 Серый тем"    # линии рам и створок
PANE_W = (300, 1500)              # ширина створки, мм
PANE_H = (450, 2500)              # высота створки, мм
MAX_PANE_RATIO = 3.0              # створка не вытянутее этого (отсекает фальцы кровли)
MAX_WIN_W = 2800                  # окно не шире, мм
MAX_WIN_H = 3600                  # окно не выше, мм
WIN_GAP = 200                     # створки ближе этого — одно окно, мм
TRANSOM_GAP = 150                 # фрамуга не дальше этого над окном, мм
TRANSOM_MAX_H = 1200              # фрамуга не выше, мм
FRAME_MARGIN = (5, 500)          # рама шире стекла на столько с каждой стороны, мм
COLORS = {"window": "tab:blue", "door": "tab:green", "dormer": "tab:orange"}

# Переключатели исправлений: включаем по одному и смотрим на метрики
USE_PANE_RATIO = True     # 1. отсекать вытянутые «створки» (фальцы кровли)
USE_INNER_CHECK = False   # 2. окно из одной створки должно иметь что-то внутри
USE_TRANSOM = True        # 3. продлевать окна вверх на фрамугу
USE_FRAME_CHECK = True    # 4. окно из одной створки должно быть окружено рамой


def segments(msp, layer):
    """Все отрезки слоя: из линий и полилиний."""
    segs = []
    for e in msp:
        if e.dxf.layer != layer:
            continue
        if e.dxftype() == "LINE":
            segs.append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)))
        elif e.dxftype() == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if e.closed and pts:
                pts.append(pts[0])
            segs += list(zip(pts, pts[1:]))
    return [s for s in segs if s[0] != s[1]]


def is_rect(poly):
    """Многоугольник почти совпадает со своим габаритным прямоугольником."""
    x0, y0, x1, y1 = poly.bounds
    w, h = x1 - x0, y1 - y0
    return w > 0 and h > 0 and poly.area / (w * h) > 0.9


def is_pane(poly):
    """Прямоугольник размером и формой как створка окна."""
    if not is_rect(poly):
        return False
    x0, y0, x1, y1 = poly.bounds
    w, h = x1 - x0, y1 - y0
    ratio_ok = h / w <= MAX_PANE_RATIO or not USE_PANE_RATIO
    return (PANE_W[0] < w < PANE_W[1] and PANE_H[0] < h < PANE_H[1]
            and 0.8 <= h / w and ratio_ok)


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def has_inner(box, rects):
    """Внутри прямоугольника есть другой, заметно меньший: рама, стекло, филёнка."""
    for r in rects:
        inside_box = (r[0] >= box[0] - 1 and r[2] <= box[2] + 1
                      and r[1] >= box[1] - 1 and r[3] <= box[3] + 1)
        if inside_box and area(r) < 0.9 * area(box):
            return True
    return False


def has_frame(box, frames):
    """Вокруг прямоугольника есть рама: кольцо шире на 1–30 см с каждой стороны."""
    lo, hi = FRAME_MARGIN
    for f in frames:
        margins = (box[0] - f[0], box[1] - f[1], f[2] - box[2], f[3] - box[3])
        if all(lo <= m <= hi for m in margins):
            return True
    return False


def group_boxes(boxes, gap):
    """Объединяет касающиеся прямоугольники в группы."""
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if touches(boxes[i], boxes[j], gap):
                parent[find(i)] = find(j)

    groups = defaultdict(list)
    for i, b in enumerate(boxes):
        groups[find(i)].append(b)
    result = []
    for members in groups.values():
        total = members[0]
        for b in members[1:]:
            total = union(total, b)
        result.append((total, members))
    return result


def is_window(group, rects, frames):
    """Похоже на окно: несколько створок или одна крупная в раме."""
    box, members = group
    w, h = box[2] - box[0], box[3] - box[1]
    if w > MAX_WIN_W or h > MAX_WIN_H:
        return False
    if len(members) >= 2:
        return True
    big_single = w >= 500 and h >= 900
    inner_ok = has_inner(box, rects) or not USE_INNER_CHECK
    frame_ok = has_frame(box, frames) or not USE_FRAME_CHECK
    return big_single and inner_ok and frame_ok


def add_transom(w, rects):
    """Продлевает окно вверх, если вплотную над ним стоит фрамуга."""
    width = w[2] - w[0]
    top = w[3]
    for r in rects:
        rw, rh = r[2] - r[0], r[3] - r[1]
        common = min(r[2], w[2]) - max(r[0], w[0])
        close_above = abs(r[1] - w[3]) <= TRANSOM_GAP
        same_width = common >= 0.7 * min(rw, width) and rw <= 1.3 * width
        if close_above and same_width and rh <= TRANSOM_MAX_H:
            top = max(top, r[3])
    return (w[0], w[1], w[2], top)


def guess_type(w, fb):
    """Предположение о типе проёма по положению на фасаде."""
    height = fb[3] - fb[1]
    rel_bottom = (w[1] - fb[1]) / height          # 0 — низ фасада, 1 — верх
    h, wd = w[3] - w[1], w[2] - w[0]
    if rel_bottom > 0.7:
        return "dormer"
    if rel_bottom < 0.25 and h >= 1900 and h / wd >= 1.8:
        return "door"
    return "window"


def inside(seg, fb):
    return all(fb[0] <= p[0] <= fb[2] and fb[1] <= p[1] <= fb[3] for p in seg)


def draw(n, fb, fsegs, items):
    fig, ax = plt.subplots(figsize=(14, 7))
    for (a, b) in fsegs:
        ax.plot([a[0], b[0]], [a[1], b[1]], color="lightgray", linewidth=0.4)
    for it in items:
        w, h = it["x1"] - it["x0"], it["z1"] - it["z0"]
        ax.add_patch(Rectangle((it["x0"], it["z0"]), w, h,
                               color=COLORS[it["type"]], alpha=0.5))
        ax.text(it["x0"] + w / 2, it["z0"] + h / 2, str(it["id"]),
                ha="center", va="center", fontsize=11, fontweight="bold")
    ax.set_xlim(fb[0], fb[2])
    ax.set_ylim(fb[1], fb[3])
    ax.set_aspect("equal")
    ax.set_title(f"Фасад {n}: синий — окно, зелёный — дверь, оранжевый — слуховое окно")
    plt.savefig(f"windows_v2_{n}.png", dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    msp = ezdxf.readfile(path).modelspace()

    boxes = pane_boxes(msp, FACADE_LAYER)
    size = median(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    facades = cluster(boxes, gap=2 * size)
    facades.sort(key=lambda g: (g[0][0], g[0][1]))
    segs = segments(msp, SASH_LAYER)
    print(f"Отрезков на слое рам: {len(segs)}")
    print(f"Переключатели: ratio={USE_PANE_RATIO}  inner={USE_INNER_CHECK}  "
          f"transom={USE_TRANSOM}  frame={USE_FRAME_CHECK}")

    result = {}
    for n, (fb, _) in enumerate(facades, start=1):
        fsegs = [s for s in segs if inside(s, fb)]
        if not fsegs:
            print(f"Фасад {n}: линий рам нет")
            continue
        noded = unary_union([LineString(s) for s in fsegs])
        polys = list(polygonize(noded))
        rects = [p.bounds for p in polys if is_rect(p)]
        frames = [p.bounds for p in polys if not is_rect(p)]
        panes = [p.bounds for p in polys if is_pane(p)]
        groups = [g for g in group_boxes(panes, WIN_GAP) if is_window(g, rects, frames)]
        windows = [add_transom(g[0], rects) if USE_TRANSOM else g[0] for g in groups]
        windows.sort(key=lambda w: (-round(w[1] / 1000), w[0]))   # сверху вниз, слева направо

        items = []
        for i, w in enumerate(windows, start=1):
            items.append({
                "id": i,
                "type": guess_type(w, fb),
                "x0": round(w[0]), "z0": round(w[1]),
                "x1": round(w[2]), "z1": round(w[3]),
            })
        result[f"facade_{n}"] = {"bbox": [round(v) for v in fb], "items": items}
        counts = {t: sum(1 for it in items if it["type"] == t) for t in COLORS}
        print(f"Фасад {n}: окон {counts['window']:2}   дверей {counts['door']}   "
              f"слуховых {counts['dormer']}")
        draw(n, fb, fsegs, items)

    out = path.parent.parent / "windows_raw.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {out}")
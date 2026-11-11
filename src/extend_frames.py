import json
import sys
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union

SASH_LAYER = "3_153 Серый тем"
SIDE_MAX = 300     # рама может выходить за створки вбок не больше чем на 30 см
BOTTOM_MAX = 300   # вниз — не больше 30 см
TOP_MAX = 1200     # вверх — до 1.2 м (там фрамуга)
TYPES = {"window", "door"}


def facade_lines(msp, fb):
    """Линии слоя створок в районе фасада."""
    area = box(*fb).buffer(500)
    lines = []
    for e in msp.query("LINE LWPOLYLINE POLYLINE"):
        if e.dxf.layer != SASH_LAYER:
            continue
        if e.dxftype() == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
            closed = False
        elif e.dxftype() == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            closed = e.closed
        else:
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            closed = e.is_closed
        if closed and len(pts) > 2:
            pts.append(pts[0])
        if len(pts) < 2:
            continue
        line = LineString(pts)
        if line.length > 0 and line.intersects(area):
            lines.append(line)
    return lines


def outline_bounds(lines):
    """Габариты всех замкнутых контуров, которые образуют линии."""
    faces = polygonize(unary_union(lines))
    return {tuple(round(v, 1) for v in Polygon(f.exterior).bounds) for f in faces}


def extend(item, bounds):
    """Самая большая рама вокруг окна в допустимых пределах (или None)."""
    best = None
    for x0, y0, x1, y1 in bounds:
        left, right = item["x0"] - x0, x1 - item["x1"]
        down, up = item["z0"] - y0, y1 - item["z1"]
        if min(left, right, down, up) < -5:
            continue   # контур не охватывает окно
        if left <= SIDE_MAX and right <= SIDE_MAX and down <= BOTTOM_MAX and up <= TOP_MAX:
            area = (x1 - x0) * (y1 - y0)
            if best is None or area > best[0]:
                best = (area, (x0, y0, x1, y1))
    return best[1] if best else None


def draw(lines, old_items, new_items, title, path):
    fig, ax = plt.subplots(figsize=(16, 8))
    for line in lines:
        xs, ys = line.xy
        ax.plot(xs, ys, color="gray", linewidth=0.3)
    for it in old_items:
        ax.add_patch(Rectangle((it["x0"], it["z0"]), it["x1"] - it["x0"], it["z1"] - it["z0"],
                               fill=False, edgecolor="blue", linewidth=0.8, linestyle="--"))
    for it in new_items:
        ax.add_patch(Rectangle((it["x0"], it["z0"]), it["x1"] - it["x0"], it["z1"] - it["z0"],
                               fill=False, edgecolor="red", linewidth=1.2))
        ax.text(it["x0"], it["z1"] + 60, str(it["id"]), color="red", fontsize=7)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_title(title)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else "checked"
    data_dir = dxf_path.parent.parent
    windows = json.loads((data_dir / f"windows_{name}.json").read_text(encoding="utf-8"))
    msp = ezdxf.readfile(dxf_path).modelspace()
    plots = data_dir / "plots"   # картинки — рядом с данными проекта
    plots.mkdir(exist_ok=True)

    result = {}
    for facade, block in windows.items():
        if not facade.startswith("facade_"):
            result[facade] = block
            continue
        lines = facade_lines(msp, block["bbox"])
        bounds = outline_bounds(lines)
        new_items, grown = [], []
        for it in block["items"]:
            new = dict(it)
            if it["type"] in TYPES:
                b = extend(it, bounds)
                if b:
                    new.update(x0=round(b[0]), z0=round(b[1]), x1=round(b[2]), z1=round(b[3]))
                    added = (new["z1"] - new["z0"]) - (it["z1"] - it["z0"])
                    if added > 50:
                        grown.append(added)
            new_items.append(new)
        result[facade] = {**block, "items": new_items}

        n = sum(1 for it in block["items"] if it["type"] in TYPES)
        avg = sum(grown) / len(grown) if grown else 0
        print(f"{facade}: окон и дверей {n}, выросли по высоте {len(grown)}, "
              f"в среднем на {avg:.0f} мм")
        draw(lines, block["items"], new_items,
             f"{facade}: синий пунктир — было, красное — по раме",
             plots / f"framed_{name}_{facade}.png")

    out = data_dir / f"windows_{name}_framed.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {out}, картинки plots/framed_{name}_facade_N.png")
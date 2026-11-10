import json
import sys
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox
from shapely.geometry import LineString, Polygon

from hatch_colors import hatch_color

FACADE_LAYER = "3_стекла"
ROOF_COLOR = "#45494E"
FACADE_SIDE = {"facade_1": "SE", "facade_4": "NW", "facade_2": "SW", "facade_5": "NE"}
# axis — вдоль какой локальной оси идёт стена (0 = x, 1 = y)
# sign — куда смотрит стена наружу (+ или −)
# flip — как ось листа «слева направо» ложится на ось стены
SIDES = {
    "SE": {"axis": 0, "sign": -1, "flip": 1},
    "NW": {"axis": 0, "sign": 1, "flip": -1},
    "SW": {"axis": 1, "sign": -1, "flip": -1},
    "NE": {"axis": 1, "sign": 1, "flip": 1},
}
ROOF_LOWER_RUN = 0.8   # как в preview_blender.py
RAY = 40.0             # длина луча, м


def wall_plane(outline, axis, sign):
    """Координата стены: самое длинное ребро вдоль этой стороны у края контура."""
    k = 1 - axis
    extreme = max(sign * p[k] for p in outline)
    lengths = {}
    for a, b in zip(outline, outline[1:] + outline[:1]):
        if abs(a[k] - b[k]) < 1e-3 and sign * a[k] >= extreme - 1.0:
            c = round(a[k], 3)
            lengths[c] = lengths.get(c, 0) + abs(a[axis] - b[axis])
    return max(lengths, key=lengths.get)


def wall_frame(outline):
    """Положение четырёх стен, середины и длины фасадов основного объёма."""
    planes = {s: wall_plane(outline, d["axis"], d["sign"]) for s, d in SIDES.items()}
    center = {0: (planes["SW"] + planes["NE"]) / 2, 1: (planes["SE"] + planes["NW"]) / 2}
    length = {0: planes["NE"] - planes["SW"], 1: planes["NW"] - planes["SE"]}
    return planes, center, length


def roof_band(msp, fb):
    """Тёмная заливка кровли в верхней части фасада: (лево, право, низ = карниз)."""
    height = fb[3] - fb[1]
    pieces = []
    for e in msp.query("HATCH"):
        if e.dxf.layer != FACADE_LAYER or hatch_color(e) != ROOF_COLOR:
            continue
        b = bbox.extents([e], fast=True)
        if not b.has_data:
            continue
        x0, y0, x1, y1 = b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y
        inside = fb[0] - 1 <= x0 and x1 <= fb[2] + 1 and fb[1] - 1 <= y0 and y1 <= fb[3] + 1
        if inside and y1 >= fb[1] + 0.7 * height and x1 - x0 >= 2000:
            pieces.append((x0, y0, x1, y1))
    if not pieces:
        return None
    return min(p[0] for p in pieces), max(p[2] for p in pieces), min(p[1] for p in pieces)


def point(axis, along, across):
    p = [0.0, 0.0]
    p[axis], p[1 - axis] = along, across
    return tuple(p)


def find_face(parts, d, plane, u, zc):
    """Первая стена на пути луча от зрителя: основной объём или пристройка."""
    axis, sign = d["axis"], d["sign"]
    start = point(axis, u, plane + sign * RAY)
    ray = LineString([start, point(axis, u, plane - sign * RAY)])
    best = None
    for part in parts:
        if not part["z_bottom"] <= zc <= part["z_eave"]:
            continue
        hit = ray.intersection(Polygon(part["outline"]).exterior)
        for g in getattr(hit, "geoms", [hit]):
            for c in getattr(g, "coords", []):
                dist = abs(c[1 - axis] - start[1 - axis])
                if best is None or dist < best[0]:
                    best = (dist, part["id"], c[1 - axis])
    return best


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    data_dir = dxf_path.parent.parent
    building = json.loads((data_dir / "building.json").read_text(encoding="utf-8"))
    checked = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))
    msp = ezdxf.readfile(dxf_path).modelspace()

    parts = building["parts"]
    main = parts[0]
    planes, center, length = wall_frame(main["outline"])
    z_eave, z_break = main["z_eave"], main["roof"]["z_break"]

    openings, unplaced = [], 0
    for name, side in FACADE_SIDE.items():
        fb, d = checked[name]["bbox"], SIDES[side]
        roof = roof_band(msp, fb)
        if roof is None:
            print(f"{name}: кровля не найдена, фасад пропущен")
            continue
        rx0, rx1, eave_y = roof
        zero_y = eave_y - z_eave * 1000
        cx = (rx0 + rx1) / 2

        def rel(y):
            return (y - zero_y) / 1000

        doors = [rel(it["z0"]) for it in checked[name]["items"] if it["type"] == "door"]
        print(f"{name} -> {side}: кровля {(rx1 - rx0) / 1000:.2f} м при стене "
              f"{length[d['axis']]:.2f} м, конёк {rel(fb[3]):+.2f} "
              f"(в building.json {main['roof']['z_ridge']:+.2f}), низ дверей: "
              f"{', '.join(f'{z:+.2f}' for z in doors) or 'нет'}")

        for it in checked[name]["items"]:
            s0, s1 = (it["x0"] - cx) / 1000, (it["x1"] - cx) / 1000
            base = center[d["axis"]]
            u0, u1 = sorted((base + d["flip"] * s0, base + d["flip"] * s1))
            z0, z1 = rel(it["z0"]), rel(it["z1"])
            if it["type"] == "dormer":
                share = min(1.0, max(0.0, (z0 - z_eave) / (z_break - z_eave)))
                part_id, face = main["id"], planes[side] - d["sign"] * ROOF_LOWER_RUN * share
            else:
                hit = find_face(parts, d, planes[side], (u0 + u1) / 2, (z0 + z1) / 2)
                if hit is None:
                    unplaced += 1
                    continue
                _, part_id, face = hit
            openings.append({
                "facade": name, "ref_id": it["id"], "side": side, "type": it["type"],
                "part": part_id, "face": round(face, 3),
                "u0": round(u0, 3), "u1": round(u1, 3),
                "z0": round(z0, 3), "z1": round(z1, 3),
            })

    building["openings"] = openings
    building["review"] = [r for r in building["review"] if not r.startswith("проёмы:")]
    building["review"].append(
        f"проёмы: размещено {len(openings)}, не размещено {unplaced}; развёртка (facade_3) "
        f"не использована; высоты привязаны к карнизу +{z_eave} по низу кровли на фасадах")
    (data_dir / "building.json").write_text(
        json.dumps(building, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nРазмещено по стенам:")
    for (side, part, kind), n in sorted(Counter(
            (o["side"], o["part"], o["type"]) for o in openings).items()):
        print(f"  {side}  {part:3}  {kind:7} {n}")
    print(f"Не размещено: {unplaced}. Сохранено в building.json")
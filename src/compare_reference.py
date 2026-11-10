import json
import math
from collections import Counter
from pathlib import Path

import bpy

JSON_PATH = r"C:\Users\user\Desktop\npm-generator\data\project_01\building.json"
BIN = 0.05       # шаг сетки при поиске стен и уровней, м
SEARCH = 2.0     # ищем стену эталона не дальше 2 м от нашей
MIDDLE = 0.4     # для поиска стен берём только середину, чтобы не мешали пристройки
WALL_BAND = 0.6  # для уровней берём вершины не дальше 0.6 м от стены эталона
SIDES = {"SE": (0, -1), "NW": (0, 1), "SW": (1, -1), "NE": (1, 1)}


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


def reference_object():
    """Импортированная сданная модель: самый большой меш вне коллекции Preview."""
    preview = bpy.data.collections.get("Preview")
    skip = set(preview.objects) if preview else set()
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o not in skip]
    return max(meshes, key=lambda o: len(o.data.vertices)) if meshes else None


def local_points(obj, data):
    """Все вершины эталона в координатах нашего здания (x, y, z от нашего нуля)."""
    ox, oy = data["origin_msk"]
    z0 = data.get("zero_abs", 0.0)
    a = math.radians(data["rotation_deg"])
    c, s = math.cos(a), math.sin(a)
    pts = []
    for v in obj.data.vertices:
        w = obj.matrix_world @ v.co
        dx, dy = w.x - ox, w.y - oy
        pts.append((dx * c + dy * s, -dx * s + dy * c, w.z - z0))
    return pts


def rotate(pts, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(x * c - y * s, x * s + y * c, z) for x, y, z in pts]


def score(pts):
    """Насколько точки выстроены в линии вдоль осей: чем больше, тем ровнее."""
    cx = Counter(round(p[0] / BIN) for p in pts)
    cy = Counter(round(p[1] / BIN) for p in pts)
    return sum(n * n for n in cx.values()) + sum(n * n for n in cy.values())


def best_angle(pts):
    """Сначала грубо (шаг 0.25°), потом точно (шаг 0.02°)."""
    coarse = max((i * 0.25 for i in range(-20, 21)), key=lambda a: score(rotate(pts, a)))
    fine = [coarse + i * 0.02 for i in range(-15, 16)]
    return max(fine, key=lambda a: score(rotate(pts, a)))


def wall_peak(pts, axis, plane, center, half):
    """Где у эталона стена рядом с нашей: самая плотная линия точек."""
    k = 1 - axis
    near = [p[k] for p in pts
            if abs(p[axis] - center) < half * MIDDLE and abs(p[k] - plane) < SEARCH]
    if not near:
        return None, 0
    b, n = Counter(round(v / BIN) for v in near).most_common(1)[0]
    return b * BIN, n


def level_peaks(values, top=14):
    """Высоты, на которых много вершин (полки): низ и верх окон, пояса, карниз."""
    cnt = Counter(round(v / BIN) for v in values)
    local = [(b, n) for b, n in cnt.items()
             if n >= cnt.get(b - 1, 0) and n >= cnt.get(b + 1, 0) and n >= 4]
    local = sorted(local, key=lambda t: -t[1])[:top]
    return sorted((b * BIN, n) for b, n in local)


def main():
    data = json.loads(Path(JSON_PATH).read_text(encoding="utf-8"))
    main_part = data["parts"][0]
    outline = main_part["outline"]
    ref = reference_object()
    lines = []
    if ref is None:
        lines.append("Сданная модель не найдена: импортируй FBX (Файл → Импорт → FBX)")
    else:
        all_pts = local_points(ref, data)
        z_max = main_part["z_eave"] - 0.3
        wall_pts = [p for p in all_pts if 0.5 < p[2] < z_max]
        best = best_angle(wall_pts)
        phi = -best
        rot = data["rotation_deg"]
        lines.append(f"Эталон: {ref.name}, вершин на высоте стен: {len(wall_pts)}")
        lines.append(f"Поворот эталона относительно нас: {phi:+.2f}°  "
                     f"(rotation_deg {rot:.3f} -> {rot + phi:.3f})")
        all_pts = rotate(all_pts, best)
        wall_pts = [p for p in all_pts if 0.5 < p[2] < z_max]

        ours = {s: wall_plane(outline, a, g) for s, (a, g) in SIDES.items()}
        center = {0: (ours["SW"] + ours["NE"]) / 2, 1: (ours["SE"] + ours["NW"]) / 2}
        half = {0: (ours["NE"] - ours["SW"]) / 2, 1: (ours["NW"] - ours["SE"]) / 2}
        theirs = {}
        lines.append("")
        for side, (axis, sign) in SIDES.items():
            peak, n = wall_peak(wall_pts, axis, ours[side], center[axis], half[axis])
            if peak is None:
                lines.append(f"{side}: стена эталона не найдена")
                continue
            theirs[side] = peak
            out = (peak - ours[side]) * sign
            where = "дальше наружу" if out > 0 else "глубже внутрь"
            lines.append(f"{side}: наша {ours[side]:+.2f}, эталон {peak:+.2f} -> "
                         f"стена эталона {where} на {abs(out):.2f} м  (точек на линии {n})")

        if len(theirs) == 4:
            dx = (theirs["SW"] + theirs["NE"]) / 2 - center[0]
            dy = (theirs["SE"] + theirs["NW"]) / 2 - center[1]
            lines.append("")
            lines.append(f"Длина (SW-NE): наша {ours['NE'] - ours['SW']:.2f}, "
                         f"эталон {theirs['NE'] - theirs['SW']:.2f}")
            lines.append(f"Ширина (SE-NW): наша {ours['NW'] - ours['SE']:.2f}, "
                         f"эталон {theirs['NW'] - theirs['SE']:.2f}")
            lines.append(f"Центр эталона сдвинут: вдоль длинного фасада {dx:+.2f} м "
                         f"(+ = к NE), поперёк {dy:+.2f} м (+ = к NW)")

        lines.append("")
        lines.append(f"Высоты от нашего нуля (zero_abs {data.get('zero_abs', 0.0)}):")
        lines.append(f"  верх эталона (конёк): {max(p[2] for p in all_pts):+.2f}, "
                     f"у нас {main_part['roof']['z_ridge']:+.2f}")
        lines.append(f"  низ эталона: {min(p[2] for p in all_pts):+.2f}")
        for side in ("SE", "NW", "SW", "NE"):
            if side not in theirs:
                continue
            axis, _ = SIDES[side]
            k = 1 - axis
            zs = [p[2] for p in all_pts
                  if abs(p[k] - theirs[side]) < WALL_BAND
                  and abs(p[axis] - center[axis]) < half[axis] * 0.9
                  and -1.0 < p[2] < 12.0]
            peaks = ", ".join(f"{z:+.2f} ({n})" for z, n in level_peaks(zs))
            our_levels = sorted({round(v, 2) for o in data.get("openings", [])
                                 if o["side"] == side and o["part"] == "P1"
                                 and o["type"] in ("window", "door")
                                 for v in (o["z0"], o["z1"])})
            lines.append(f"\n  {side}, полки эталона (высота, вершин): {peaks}")
            lines.append(f"  {side}, наши низ и верх окон и дверей: "
                         f"{', '.join(f'{z:+.2f}' for z in our_levels)}")

    text = "\n".join(lines)
    print(text)
    out_path = Path(JSON_PATH).parent / "compare_reference.txt"
    out_path.write_text(text, encoding="utf-8")
    block = bpy.data.texts.get("compare_result") or bpy.data.texts.new("compare_result")
    block.clear()
    block.write(text)


main()
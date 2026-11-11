"""Нарезает атлас сданной НПМ на материалы частной библиотеки.

Запуск через Blender:
    blender --background --python src/library_from_npm.py -- data/project_01/reference/файл.fbx project_01 137.5

Аргументы: FBX сданной модели, префикс имён (обычно имя проекта),
абсолютная отметка нуля здания (для статистики высот; можно не указывать).

Как работает:
1. Каждая грань модели лежит своим UV-островом в какой-то плитке атласа.
   Атлас режется «гильотиной» по линиям, которые не пересекает ни один остров —
   так находятся плитки, даже если цвета у соседних почти одинаковые.
2. Для каждой плитки считается, какие грани её используют: сколько площади,
   стены это или кровля (по нормалям), на какой высоте, сколько пикселей на метр.
   Это бесплатная разметка: по ней видно, что за материал в плитке.
3. Плитка (d, n, r, m) сохраняется в data/library_private/materials/<имя>/
   вместе с material.json — с описанием, реальным размером и статистикой.
"""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "library_private" / "materials"


def image_array(img):
    """Пиксели картинки Blender -> массив (строки сверху вниз), значения 0…1."""
    w, h = img.size
    arr = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(arr)
    return arr.reshape(h, w, 4)[::-1, :, :3]


def save_array(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new(path.stem, w, h, alpha=False)
    rgba = np.concatenate([arr[::-1], np.ones((h, w, 1))], axis=2).astype(np.float32)
    img.pixels.foreach_set(rgba.ravel())
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def uv_boxes(me, size):
    """Габарит UV-острова каждой грани в пикселях атласа (x вправо, y вниз)."""
    uv = me.uv_layers[0].data
    boxes = []
    for p in me.polygons:
        xs = [uv[i].uv.x * size for i in p.loop_indices]
        ys = [(1 - uv[i].uv.y) * size for i in p.loop_indices]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return np.array(boxes)


def guillotine(boxes, rect, idx, out, depth=0):
    """Режет прямоугольник по самой широкой полосе, которую не пересекает ни один остров."""
    best = None
    b = boxes[idx]
    for axis in (0, 1):
        lo, hi = int(rect[axis]), int(rect[axis + 2])
        if hi - lo < 4:
            continue
        cross = np.zeros(hi - lo + 1, int)
        for s, e in zip(b[:, axis], b[:, axis + 2]):
            si, ei = int(math.floor(s)) + 1 - lo, int(math.ceil(e)) - lo
            if ei > si:
                cross[max(si, 0):max(min(ei, len(cross)), 0)] += 1
        free = [p for p in range(lo + 1, hi) if cross[p - lo] == 0]
        runs, start = [], None
        for p in free + [None]:
            if start is None:
                start = prev = p
            elif p is None or p != prev + 1:
                runs.append((start, prev))
                start = prev = p
            else:
                prev = p
        for r0, r1 in runs:
            if r0 is None:
                continue
            mid = (r0 + r1) // 2
            if (b[:, axis + 2] <= mid).any() and (b[:, axis] >= mid).any():
                if best is None or r1 - r0 > best[2]:
                    best = (axis, mid, r1 - r0)
    if best is None or depth > 10:
        out.append((idx, tuple(rect)))
        return
    axis, mid, _ = best
    first, second = list(rect), list(rect)
    first[axis + 2] = second[axis] = mid
    guillotine(boxes, first, idx[b[:, axis + 2] <= mid], out, depth + 1)
    guillotine(boxes, second, idx[b[:, axis] >= mid], out, depth + 1)


def grid_lines(img, min_run=100, jump=0.04):
    """Позиции длинных резких границ на картинке: столбцы и строки.

    Граница засчитывается, если вдоль неё подряд не меньше min_run пикселей
    с перепадом яркости больше jump — так находятся края плиток, даже когда
    соседние плитки почти одного цвета.
    """
    gray = img.mean(axis=2)
    found = []
    for diff in (np.abs(np.diff(gray, axis=1)).T, np.abs(np.diff(gray, axis=0))):
        lines = []
        for i, row in enumerate(diff):
            hit = row > jump
            best = run = 0
            for v in hit:
                run = run + 1 if v else 0
                best = max(best, run)
            if best >= min_run:
                lines.append(i + 1)
        found.append(np.array(lines))
    return found                                   # (столбцы, строки)


def snap(bbox, bounds, cols, rows, size):
    """Край плитки: ближайшая длинная граница между островами и линией разреза."""
    x0, y0, x1, y1 = [int(round(v)) for v in bbox]
    bx0, by0, bx1, by1 = [int(round(v)) for v in bounds]
    if x1 - x0 < 16 or y1 - y0 < 16:               # точка-образец цвета: берём сам островок
        return max(0, x0), max(0, y0), min(size, max(x1, x0 + 4)), min(size, max(y1, y0 + 4))

    def pick(lines, lo, hi, fallback, last):
        inside = lines[(lines >= lo) & (lines <= hi)]
        if len(inside) == 0:
            return fallback
        return int(inside.max() if last else inside.min())

    return (pick(cols, bx0, x0, bx0, True), pick(rows, by0, y0, by0, True),
            pick(cols, x1, bx1, bx1, False), pick(rows, y1, by1, by1, False))


def usage(ob, idx, boxes, zero):
    """Кто пользуется плиткой: площадь, доля стен, высоты, пикселей на метр."""
    me, mw = ob.data, ob.matrix_world
    area = vertical = 0.0
    zs, density = [], []
    for i in idx:
        p = me.polygons[i]
        a = p.area
        area += a
        if abs(p.normal.z) < 0.3:
            vertical += a
        zs += [(mw @ me.vertices[v].co).z - zero for v in p.vertices]
        if a > 0.3:
            bw, bh = boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1]
            density.append(math.sqrt(max(bw * bh, 0)) / math.sqrt(a))
    density.sort()
    return {
        "faces": len(idx), "area_m2": round(area, 1),
        "vertical_share": round(vertical / area, 2) if area else 0,
        "z_range": [round(min(zs), 2), round(max(zs), 2)],
        "px_per_m": round(density[len(density) // 2], 1) if density else 0.0,
    }


def describe(crop, stats):
    """Догадка о материале по картинке и по тому, где он использован."""
    gray = crop.mean(axis=2)
    rows, cols = gray.mean(axis=1).std(), gray.mean(axis=0).std()
    lum = crop.mean() * 255
    if rows > 2 * cols and rows > 0.01:
        stretch, pattern = "u", "horizontal_lines"
    elif cols > 2 * rows and cols > 0.01:
        stretch, pattern = "v", "vertical_lines"
    else:
        stretch, pattern = "both", "uniform"
    if stats["vertical_share"] < 0.3 and stats["z_range"][1] > 8:
        role = "roof"
    elif stats["z_range"][1] < 0.2:
        role = "plinth"
    elif lum > 215:
        role = "trim"
    elif lum < 100 and stats["vertical_share"] > 0.7:
        role = "dark"
    elif stats["vertical_share"] < 0.3:
        role = "horizontal"
    else:
        role = "wall"
    if pattern == "horizontal_lines" and role == "wall":
        role = "wall_siding"
    if pattern == "vertical_lines" and role == "roof":
        role = "roof_seam"
    return role, stretch, pattern


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    fbx, prefix = Path(args[0]), args[1]
    zero = float(args[2]) if len(args) > 2 else None

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(fbx))
    ob = max((o for o in bpy.context.scene.objects if o.type == "MESH"),
             key=lambda o: len(o.data.polygons))
    zero = ob.matrix_world.translation.z if zero is None else zero
    mat = ob.data.materials[0]
    maps = {}
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            for key in "dnrmo":
                if f"_{key}_" in node.image.name or node.image.name.endswith(f"_{key}"):
                    maps[key] = image_array(node.image)
    size = maps["d"].shape[1]
    boxes = uv_boxes(ob.data, size)
    regions = []
    guillotine(boxes, (0, 0, size, size), np.arange(len(boxes)), regions)
    cols, rows = grid_lines(maps["d"])

    OUT.mkdir(parents=True, exist_ok=True)
    counts = {}
    for idx, bounds in sorted(regions, key=lambda r: (boxes[r[0]][:, 1].min(), boxes[r[0]][:, 0].min())):
        b = boxes[idx]
        rect = snap((b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()), bounds,
                    cols, rows, size)
        x0, y0, x1, y1 = rect
        crop = maps["d"][y0:y1, x0:x1]
        stats = usage(ob, idx, boxes, zero)
        role, stretch, pattern = describe(crop, stats)
        counts[role] = counts.get(role, 0) + 1
        name = f"{prefix}_{role}_{counts[role]}"
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        files = {}
        for key, arr in maps.items():
            save_array(arr[y0:y1, x0:x1], folder / f"{key}.png")
            files[key] = f"{key}.png"
        density = stats["px_per_m"]
        solid = density < 1 or (x1 - x0) < 64
        size_m = [round((x1 - x0) / density, 2), round((y1 - y0) / density, 2)] if not solid else [1, 1]
        color = "#" + "".join(f"{int(v * 255):02X}" for v in crop.reshape(-1, 3).mean(0))
        meta = {
            "name": name, "title": f"{role} из сданной НПМ {fbx.name}",
            "tags": [role, pattern, "from_reference"], "kind": "image", "maps": files,
            "size_m": size_m, "stretch": "both" if solid else stretch, "color": color,
            "source": f"{fbx.name}, плитка атласа x {x0}…{x1}, y {y0}…{y1}",
            "license": "частный: только для работы бюро", "usage": stats,
        }
        (folder / "material.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        print(f"{name:32} {color}  {x1 - x0:4}×{y1 - y0:<4} px  {size_m} м  "
              f"граней {stats['faces']:5}  стены {stats['vertical_share']:.2f}  "
              f"z {stats['z_range']}  {pattern}")
    print(f"Сохранено материалов: {len(regions)} в {OUT}")


main()

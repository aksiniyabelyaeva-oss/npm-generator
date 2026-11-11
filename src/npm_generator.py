"""Генератор НПМ: building.json -> FBX по требованиям МКА.

Запуск через Blender (обычно его делает run_project.py):
    blender --background --python src/npm_generator.py -- data/project_01/building.json projects/project_01.json

Что получается в data/<проект>/output/:
- <имя>.fbx — один объект SM_<имя>_Main, один материал M_<имя>_Main_1,
  текстуры встроены в файл;
- textures/T_<имя>_Main_{d,n,r,m}_1.png — атлас 2048×2048 и карты к нему.

Как устроена модель:
- стены по контуру каждой части, окна и двери — проёмы с откосами
  глубиной REVEAL и стеклом (или полотном двери) в глубине;
- стены уходят под землю на BURY, чтобы на рельефе не было щелей;
- мансардная крыша основного объёма, плоские крыши пристроек;
- слуховые окна — коробочки с двускатной крышей на нижнем скате;
- текстура — атлас из однотонных плиток (стены, карниз, кровля, стекло...),
  каждая грань целиком лежит внутри своей плитки.
"""
import json
import math
import sys
from pathlib import Path

import bpy  # bpy первым: после него становится доступен bmesh
import bmesh

FLOOR2_Z = 3.42        # смена цвета стены между этажами, м
ROOF_LOWER_RUN = 0.8   # вынос нижнего ската мансарды внутрь, м
REVEAL = 0.15          # глубина откоса окна, м
BURY = 1.0             # насколько стены уходят ниже z_bottom, м
DORMER_SIDE = 0.10     # стенка слухового окна вокруг рамы, м
DORMER_RISE = 0.35     # высота конька слухового окна над его стенами, м
ATLAS = 2048           # размер текстуры, px
GRID = 4               # атлас 4×4 плитки
UV_PAD = 0.12          # отступ UV внутри плитки (доля), чтобы не залезать на соседнюю

SIDES = {"SE": (0, -1), "NW": (0, 1), "SW": (1, -1), "NE": (1, 1)}

# плитка атласа: (номер, ключ цвета или #RRGGBB, шероховатость, металличность)
TILES = {
    "wall1": (0, "wall_floor1", 0.90, 0.0),
    "wall2": (1, "wall_floor2", 0.90, 0.0),
    "trim": (2, "trim", 0.85, 0.0),
    "plinth": (3, "plinth", 0.95, 0.0),
    "roof": (4, "roof", 0.55, 0.3),
    "annex": (5, "#8A756B", 0.90, 0.0),
    "glass": (6, "#2E3A42", 0.10, 0.0),
    "door": (7, "#4A3A30", 0.70, 0.0),
}


# ---------- геометрия: простые вектора в виде кортежей ----------

def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def mul(a, k):
    return tuple(x * k for x in a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    length = math.sqrt(dot(a, a))
    return mul(a, 1 / length) if length else a


def newell(pts):
    """Нормаль многоугольника (работает и для невыпуклых)."""
    n = [0.0, 0.0, 0.0]
    for p, q in zip(pts, pts[1:] + pts[:1]):
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    return norm(tuple(n))


def signed_area(ring):
    return sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(ring, ring[1:] + ring[:1])) / 2


def point(axis, along, across, z):
    p = [0.0, 0.0, z]
    p[axis], p[1 - axis] = along, across
    return tuple(p)


# ---------- сбор граней ----------

class Faces:
    """Копит грани: список точек, плитка атласа и куда должна смотреть нормаль."""

    def __init__(self):
        self.items = []

    def add(self, pts, tile, facing):
        if len(pts) < 3 or abs(signed_area_3d(pts)) < 1e-6:
            return
        if dot(newell(pts), facing) < 0:
            pts = list(reversed(pts))
        self.items.append((pts, tile))

    def rect(self, corner, u, v, tile, facing):
        """Прямоугольник: угол и два ребра-вектора."""
        self.add([corner, add(corner, u), add(add(corner, u), v), add(corner, v)], tile, facing)


def signed_area_3d(pts):
    n = newell(pts)
    total = (0.0, 0.0, 0.0)
    for p, q in zip(pts, pts[1:] + pts[:1]):
        total = add(total, cross(p, q))
    return dot(total, n) / 2


def wall_tile(z_mid, kind):
    if kind == "dormer":
        return "trim"
    if z_mid < 0 or kind == "porch":
        return "plinth"
    if kind == "annex":
        return "annex"
    return "wall1" if z_mid < FLOOR2_Z else "wall2"


def wall(faces, p, q, z0, z1, holes, kind):
    """Стена от точки p до q (в плане) с проёмами.

    holes: список (t0, t1, h0, h1, тип) — t в метрах от p вдоль стены.
    Стена режется на полосы по высоте; в каждой полосе соседние куски
    без проёмов сливаются в один прямоугольник.
    """
    edge = (q[0] - p[0], q[1] - p[1], 0.0)
    length = math.hypot(edge[0], edge[1])
    if length < 1e-3:
        return
    d = mul(edge, 1 / length)
    out = (d[1], -d[0], 0.0)           # наружу для контура против часовой стрелки
    base = (p[0], p[1], 0.0)

    def at(t, z):
        return add(add(base, mul(d, t)), (0.0, 0.0, z))

    # проём не может выходить за стену: обрезаем по длине и высоте
    holes = [(max(t0, 0.02), min(t1, length - 0.02), max(h0, z0 + 0.02), min(h1, z1 - 0.02), k)
             for t0, t1, h0, h1, k in holes]
    holes = [h for h in holes if h[1] - h[0] > 0.05 and h[3] - h[2] > 0.05]

    xs = sorted({0.0, length, *[min(max(h[i], 0.0), length) for h in holes for i in (0, 1)]})
    zs = {z0, z1, *[min(max(h[i], z0), z1) for h in holes for i in (2, 3)]}
    zs |= {z for z in (0.0, FLOOR2_Z) if z0 < z < z1}
    zs = sorted(zs)

    for za, zb in zip(zs, zs[1:]):
        if zb - za < 1e-4:
            continue
        zm = (za + zb) / 2
        run = None
        for xa, xb in zip(xs, xs[1:]):
            if xb - xa < 1e-4:
                continue
            xm = (xa + xb) / 2
            in_hole = any(h[0] < xm < h[1] and h[2] < zm < h[3] for h in holes)
            if in_hole:
                if run:
                    faces.rect(at(run[0], za), mul(d, run[1] - run[0]), (0, 0, zb - za),
                               wall_tile(zm, kind), out)
                run = None
            else:
                run = (run[0], xb) if run else (xa, xb)
        if run:
            faces.rect(at(run[0], za), mul(d, run[1] - run[0]), (0, 0, zb - za),
                       wall_tile(zm, kind), out)

    for t0, t1, h0, h1, kind_hole in holes:
        opening(faces, at(t0, h0), d, out, t1 - t0, h1 - h0, kind_hole)


def opening(faces, corner, d, out, w, h, kind):
    """Откосы и стекло (или полотно двери) в глубине проёма."""
    inward = mul(out, -REVEAL)
    up = (0.0, 0.0, h)
    along = mul(d, w)
    faces.rect(corner, along, inward, "trim", (0, 0, 1))                      # подоконник
    faces.rect(add(corner, up), along, inward, "trim", (0, 0, -1))           # верх
    faces.rect(corner, up, inward, "trim", d)                                 # левый откос
    faces.rect(add(corner, along), up, inward, "trim", mul(d, -1))           # правый откос
    faces.rect(add(corner, inward), along, up, "door" if kind == "door" else "glass", out)


def cap(faces, ring, z, tile, up=True):
    """Горизонтальная крышка по контуру (плоская крыша, верх крыльца)."""
    faces.add([(x, y, z) for x, y in ring], tile, (0, 0, 1 if up else -1))


def on_ring(pt, ring, tol=0.05):
    """Лежит ли точка на контуре (с допуском)."""
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ab = (b[0] - a[0], b[1] - a[1])
        ap = (pt[0] - a[0], pt[1] - a[1])
        l2 = ab[0] ** 2 + ab[1] ** 2
        t = max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1]) / l2)) if l2 else 0.0
        c = (a[0] + ab[0] * t, a[1] + ab[1] * t)
        if math.hypot(pt[0] - c[0], pt[1] - c[1]) < tol:
            return True
    return False


def edge_holes(p, q, part_id, openings, used):
    """Проёмы, которые лежат на стене p→q этой части."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    if abs(dy) < 1e-3:
        axis, across = 0, p[1]
    elif abs(dx) < 1e-3:
        axis, across = 1, p[0]
    else:
        return []                      # косая стена — проёмов на ней не ищем
    length = math.hypot(dx, dy)
    out = (dy / length, -dx / length)  # наружу для контура против часовой стрелки
    sign = 1 if out[1 - axis] > 0 else -1
    direction = 1 if (q[axis] - p[axis]) > 0 else -1
    lo, hi = sorted((p[axis], q[axis]))
    holes = []
    for i, o in enumerate(openings):
        if i in used or o["part"] != part_id or o["type"] == "dormer":
            continue
        if SIDES[o["side"]] != (axis, sign) or abs(o["face"] - across) > 0.05:
            continue
        if not lo - 0.01 <= (o["u0"] + o["u1"]) / 2 <= hi + 0.01:
            continue
        t = sorted(((o["u0"] - p[axis]) * direction, (o["u1"] - p[axis]) * direction))
        holes.append((max(t[0], 0.0), min(t[1], length), o["z0"], o["z1"], o["type"]))
        used.add(i)
    return holes


def prism_walls(faces, part, openings, used, z0, z1, kind, skip_ring=None):
    ring = [tuple(p) for p in part["outline"]]
    if signed_area(ring) < 0:
        ring = ring[::-1]
    for p, q in zip(ring, ring[1:] + ring[:1]):
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if skip_ring and on_ring(mid, skip_ring):
            continue                   # стена пристройки, прижатая к основному объёму
        wall(faces, p, q, z0, z1, edge_holes(p, q, part["id"], openings, used), kind)
    return ring


def mansard_roof(faces, outline, roof, z_eave):
    """Мансардная вальмовая крыша по габаритному прямоугольнику контура."""
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    r = ROOF_LOWER_RUN
    bx0, bx1, by0, by1 = x0 + r, x1 - r, y0 + r, y1 - r
    half = (by1 - by0) / 2
    cy = (by0 + by1) / 2
    zb, zr = roof["z_break"], roof["z_ridge"]
    e = [(x0, y0, z_eave), (x1, y0, z_eave), (x1, y1, z_eave), (x0, y1, z_eave)]
    b = [(bx0, by0, zb), (bx1, by0, zb), (bx1, by1, zb), (bx0, by1, zb)]
    r0, r1 = (bx0 + half, cy, zr), (bx1 - half, cy, zr)
    center = ((x0 + x1) / 2, (y0 + y1) / 2, z_eave)

    def away(pts):
        c = mul(tuple(map(sum, zip(*pts))), 1 / len(pts))
        return sub(c, center)

    for i in range(4):
        j = (i + 1) % 4
        quad = [e[i], e[j], b[j], b[i]]
        faces.add(quad, "roof", away(quad))
    for poly in ([b[0], b[1], r1, r0], [b[2], b[3], r0, r1], [b[3], b[0], r0], [b[1], b[2], r1]):
        faces.add(poly, "roof", away(poly))
    return {"planes": {"SE": y0, "NW": y1, "SW": x0, "NE": x1}}


def dormer(faces, o, plane, z_eave, z_break):
    """Слуховое окно: передняя стенка с проёмом, бока и двускатная крыша."""
    axis, sign = SIDES[o["side"]]
    out = point(axis, 0.0, float(sign), 0.0)
    ua, ub = o["u0"] - DORMER_SIDE, o["u1"] + DORMER_SIDE
    zb, zt = o["z0"] - DORMER_SIDE, o["z1"] + DORMER_SIDE
    front = o["face"]
    back = plane - sign * ROOF_LOWER_RUN          # линия излома: дальше коробка уходит в крышу
    if sign * (front - back) < 0.2:
        back = front - sign * 0.2
    d = point(axis, 1.0, 0.0, 0.0)

    # передняя стенка с проёмом: идём вдоль стены так, чтобы «наружу» совпало с out
    step = (-out[1], out[0])                       # направление p→q для такой стены
    forward = step[axis] > 0
    pu, qu = (ua, ub) if forward else (ub, ua)
    p = point(axis, pu, front, 0.0)[:2]
    q = point(axis, qu, front, 0.0)[:2]
    t0 = (o["u0"] - ua) if forward else (ub - o["u1"])
    wall(faces, p, q, zb, zt, [(t0, t0 + (o["u1"] - o["u0"]), o["z0"], o["z1"], "window")],
         "dormer")

    # бока
    for u, facing in ((ua, mul(d, -1)), (ub, d)):
        a = point(axis, u, front, zb)
        faces.rect(a, sub(point(axis, u, back, zb), a), (0, 0, zt - zb), "trim", facing)

    # двускатная крыша и фронтон
    um = (ua + ub) / 2
    zr = zt + DORMER_RISE
    fl, fr, fm = point(axis, ua, front, zt), point(axis, ub, front, zt), point(axis, um, front, zr)
    bl, br, bm = point(axis, ua, back, zt), point(axis, ub, back, zt), point(axis, um, back, zr)
    faces.add([fl, fr, fm], "trim", out)
    faces.add([fl, fm, bm, bl], "roof", add(mul(d, -1), (0, 0, 1)))
    faces.add([fm, fr, br, bm], "roof", add(d, (0, 0, 1)))


# ---------- атлас и материал ----------

def hex_rgb(value):
    h = value.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def tile_uv(tile, s, t):
    """Координаты (s, t) от 0 до 1 внутри плитки -> UV атласа."""
    index = TILES[tile][0]
    col, row = index % GRID, index // GRID
    size = 1 / GRID
    s = UV_PAD + (1 - 2 * UV_PAD) * s
    t = UV_PAD + (1 - 2 * UV_PAD) * t
    return ((col + s) * size, 1 - (row + 1) * size + t * size)


def make_textures(colors, out_dir, base):
    """Четыре карты атласа: d (цвет), n (нормали), r (шероховатость), m (металличность)."""
    tile_px = ATLAS // GRID
    maps = {"d": [], "n": [], "r": [], "m": []}
    rows = [[None] * GRID for _ in range(GRID)]
    for name, (index, color, rough, metal) in TILES.items():
        rgb = hex_rgb(color if color.startswith("#") else colors[color])
        rows[index // GRID][index % GRID] = (rgb, rough, metal)
    images = {}
    for key in maps:
        pixels = []
        for y in range(ATLAS):                       # Blender хранит строки снизу вверх
            row = rows[GRID - 1 - y // tile_px]
            line = []
            for col in range(GRID):
                cell = row[col]
                if cell is None:
                    value = {"d": (0.5, 0.5, 0.5), "n": (0.5, 0.5, 1.0),
                             "r": (0.9,) * 3, "m": (0.0,) * 3}[key]
                else:
                    rgb, rough, metal = cell
                    value = {"d": rgb, "n": (0.5, 0.5, 1.0),
                             "r": (rough,) * 3, "m": (metal,) * 3}[key]
                line.extend((*value, 1.0) * tile_px)
            pixels.extend(line)
        name = f"T_{base}_{key}_1"
        img = bpy.data.images.new(name, ATLAS, ATLAS, alpha=False)
        if key != "d":
            img.colorspace_settings.name = "Non-Color"
        img.pixels.foreach_set(pixels)
        path = out_dir / f"{name}.png"
        img.filepath_raw = str(path)
        img.file_format = "PNG"
        img.save()
        images[key] = img
    return images


def make_material(name, images):
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")

    def tex(key, x, y):
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = images[key]
        node.location = (x, y)
        return node

    nt.links.new(tex("d", -600, 300).outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(tex("m", -600, 0).outputs["Color"], bsdf.inputs["Metallic"])
    nt.links.new(tex("r", -600, -300).outputs["Color"], bsdf.inputs["Roughness"])
    normal = nt.nodes.new("ShaderNodeNormalMap")
    normal.location = (-250, -600)
    nt.links.new(tex("n", -600, -600).outputs["Color"], normal.inputs["Color"])
    nt.links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


# ---------- сборка ----------

def build_faces(data):
    faces = Faces()
    openings = data.get("openings", [])
    used = set()
    main = data["parts"][0]
    bottom = main["z_bottom"] - BURY

    ring = prism_walls(faces, main, openings, used, bottom, main["z_eave"], "main")
    roof = mansard_roof(faces, ring, main["roof"], main["z_eave"])

    for part in data["parts"][1:]:
        top = part["z_eave"]
        kind = "annex" if part.get("kind") == "annex" else "porch"
        pring = prism_walls(faces, part, openings, used, part["z_bottom"] - BURY, top,
                            kind, skip_ring=ring)
        cap(faces, pring, top, "roof" if kind == "annex" else "plinth")

    for i, o in enumerate(openings):
        if o["type"] == "dormer":
            dormer(faces, o, roof["planes"][o["side"]], main["z_eave"], main["roof"]["z_break"])
            used.add(i)
    return faces, len(openings) - len(used)


def to_mesh(faces, name, rotation_deg):
    """Грани -> меш Blender: общие вершины, UV по плиткам, треугольники."""
    a = math.radians(rotation_deg)
    c, s = math.cos(a), math.sin(a)
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    verts = {}

    def vert(p):
        key = tuple(round(v, 4) for v in p)
        if key not in verts:
            x, y, z = p
            verts[key] = bm.verts.new((x * c - y * s, x * s + y * c, z))
        return verts[key]

    for pts, tile in faces.items:
        try:
            face = bm.faces.new([vert(p) for p in pts])
        except ValueError:
            continue                                    # такая грань уже есть
        n = newell(pts)
        u_axis = norm(cross((0, 0, 1), n)) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
        v_axis = cross(n, u_axis)
        us = [dot(p, u_axis) for p in pts]
        vs = [dot(p, v_axis) for p in pts]
        du, dv = (max(us) - min(us)) or 1, (max(vs) - min(vs)) or 1
        for loop, uu, vv in zip(face.loops, us, vs):
            loop[uv_layer].uv = tile_uv(tile, (uu - min(us)) / du, (vv - min(vs)) / dv)
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    building_path, config_path = Path(args[0]), Path(args[1])
    data = json.loads(building_path.read_text(encoding="utf-8"))
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    names = cfg["npm"]
    base = names["object_name"]
    out_dir = building_path.parent / "output"
    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    faces, skipped = build_faces(data)
    mesh = to_mesh(faces, f"SM_{base}_Main", data["rotation_deg"])
    images = make_textures(data["parts"][0]["colors"], tex_dir, f"{base}_Main")
    mesh.materials.append(make_material(f"M_{base}_Main_1", images))

    obj = bpy.data.objects.new(f"SM_{base}_Main", mesh)
    bpy.context.scene.collection.objects.link(obj)
    ox, oy = data["origin_msk"]
    obj.location = (ox, oy, data["zero_abs"])

    fbx_path = out_dir / f"{names['file_name']}.fbx"
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(
        filepath=str(fbx_path), use_selection=True, object_types={"MESH"},
        axis_forward="Y", axis_up="Z", apply_unit_scale=True, global_scale=1.0,
        apply_scale_options="FBX_SCALE_UNITS",   # единицы — метры, как в сданных НПМ (UnitScaleFactor 100)
        mesh_smooth_type="FACE", add_leaf_bones=False, bake_anim=False,
        path_mode="COPY", embed_textures=True)

    tris = len(mesh.polygons)
    print(f"Объект: {obj.name}, треугольников: {tris}, граней до разбиения: {len(faces.items)}")
    print(f"Проёмов не встроено: {skipped}")
    print(f"Сохранено: {fbx_path}")
    print(f"Текстуры: {tex_dir}")


main()

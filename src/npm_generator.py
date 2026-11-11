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
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))   # чтобы найти library.py
import cc0  # noqa: E402
import library  # noqa: E402

FLOOR2_Z = 3.42        # смена цвета стены между этажами, м
ROOF_LOWER_RUN = 0.8   # вынос нижнего ската мансарды внутрь, м
REVEAL = 0.08          # глубина откоса окна (рама почти в плоскости стены), м
BURY = 2.0             # насколько стены уходят ниже самой низкой земли, м
DORMER_SIDE = 0.10     # стенка слухового окна вокруг рамы, м
DORMER_RISE = 0.35     # высота конька слухового окна над его стенами, м
# детали фасада (пока одинаковые для всех домов; дальше — из чертежей)
FRAME_W = 0.06         # ширина рамы окна, м
MULLION_W = 0.05       # ширина импоста и горбылька переплёта, м
SURROUND_W = 0.12      # ширина наличника, м
SURROUND_OUT = 0.03    # насколько наличник выступает из стены, м
SILL_OUT = 0.08        # вынос подоконного отлива, м
BELT = (-0.12, 0.13)   # межэтажный пояс: от и до относительно пола 2-го этажа, м
BELT_OUT = 0.05        # вынос пояса, м
CORNICE_H = 0.35       # высота карниза под кровлей, м
CORNICE_OUT = 0.12     # вынос карниза, м
OVERHANG = 0.30        # свес кровли, м
FASCIA = 0.25          # высота тёмной лобовой доски (водосточного жёлоба) по краю свеса, м
PILASTER_W = 0.35      # ширина угловой лопатки, м
PILASTER_OUT = 0.04    # вынос лопатки, м
ANNEX_PITCH = 28       # уклон вальмовой крыши пристройки, градусы
CANOPY = (1.0, 0.30)   # козырёк над дверью: вынос и запас по ширине с каждой стороны, м
ATLAS = 2048           # размер текстуры, px
UV_PAD = 2 / 682       # отступ UV от края плитки, чтобы не подмешивалась соседняя

SIDES = {"SE": (0, -1), "NW": (0, 1), "SW": (1, -1), "NE": (1, 1)}

# Роли поверхностей и материал по умолчанию: (материал библиотеки, цвет —
# ключ из colors в building.json или #RRGGBB, None — цвет материала).
# Проект может заменить любой материал в настройках: "materials": {роль: {...}}.
ROLE_DEFAULTS = {
    "wall1": ("plaster", "wall_floor1"),
    "wall2": ("plaster", "wall_floor2"),
    "trim": ("paint", "trim"),
    "plinth": ("plaster", "plinth"),
    "roof": ("roof_standing_seam", "roof"),
    "annex": ("wood_siding_horizontal", "#A08F78"),
    "glass": ("glass_dark", "#B9C0C3"),
    "door": ("paint", "#4A3A30"),
    "dormer": ("paint", "roof"),
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
        return "dormer"
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
    holes = [(max(t0, 0.02), min(t1, length - 0.02), max(h0, z0 + 0.02), min(h1, z1 - 0.02), k,
              rest[0] if rest else None)
             for t0, t1, h0, h1, k, *rest in holes]
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

    for t0, t1, h0, h1, kind_hole, sbox in holes:
        margins = None
        if sbox:                                   # наличник с чертежа: поля вокруг проёма
            margins = (t0 - sbox[0], sbox[1] - t1, h0 - sbox[2], sbox[3] - h1)
        elif not DRAWING_DETAILS and kind != "dormer":
            margins = (SURROUND_W, SURROUND_W, 0.0, SURROUND_W)
        opening(faces, at(t0, h0), d, out, t1 - t0, h1 - h0, kind_hole, margins,
                dark=kind == "dormer")
    return holes


def opening(faces, corner, d, out, w, h, kind, margins=None, dark=False):
    """Проём: откосы, стекло (или полотно двери), рама с переплётом,
    наличник вокруг (margins: слева, справа, снизу, сверху) и тёмный отлив.

    dark — для слуховых окон: рама и стекло тёмные, как у сданных НПМ.
    """
    frame_role = "dormer" if dark else "trim"
    glass_role = "dormer" if dark else ("door" if kind == "door" else "glass")
    inward = mul(out, -REVEAL)
    up = (0.0, 0.0, h)
    along = mul(d, w)
    faces.rect(corner, along, inward, frame_role, (0, 0, 1))                  # низ откоса
    faces.rect(add(corner, up), along, inward, frame_role, (0, 0, -1))       # верх откоса
    faces.rect(corner, up, inward, frame_role, d)                             # левый откос
    faces.rect(add(corner, along), up, inward, frame_role, mul(d, -1))       # правый откос
    faces.rect(add(corner, inward), along, up, glass_role, out)

    # рама и переплёт — у самой плоскости стены, чуть впереди стекла
    fc = add(corner, mul(out, -(REVEAL - 0.03)))
    fw = min(FRAME_W, w / 4, h / 4)
    faces.rect(fc, along, (0, 0, fw), frame_role, out)
    faces.rect(add(fc, (0, 0, h - fw)), along, (0, 0, fw), frame_role, out)
    faces.rect(add(fc, (0, 0, fw)), mul(d, fw), (0, 0, h - 2 * fw), frame_role, out)
    faces.rect(add(fc, add(mul(d, w - fw), (0, 0, fw))), mul(d, fw), (0, 0, h - 2 * fw),
               frame_role, out)
    if kind != "door":
        if w > 0.7:                                                            # импост
            faces.rect(add(fc, add(mul(d, (w - MULLION_W) / 2), (0, 0, fw))),
                       mul(d, MULLION_W), (0, 0, h - 2 * fw), frame_role, out)
        if h > 1.3:                                                            # фрамуга
            tc = add(fc, mul(out, 0.005))
            faces.rect(add(tc, add(mul(d, fw), (0, 0, 0.72 * h))), mul(d, w - 2 * fw),
                       (0, 0, MULLION_W), frame_role, out)

    # наличник — рамка на стене вокруг проёма
    left = right = bottom = top = 0.0
    if margins:
        left, right, bottom, top = (max(m, 0.0) for m in margins)
        sc = add(corner, mul(out, SURROUND_OUT))
        th = mul(out, -SURROUND_OUT)
        x0, x1, z0, z1 = -left, w + right, -bottom, h + top
        strips = [(x0, 0.0, z0, z1), (w, x1, z0, z1), (0.0, w, h, z1), (0.0, w, z0, 0.0)]
        for a, b, za, zb in strips:
            if b - a > 0.01 and zb - za > 0.01:
                faces.rect(add(sc, add(mul(d, a), (0, 0, za))), mul(d, b - a), (0, 0, zb - za),
                           frame_role, out)
        outer = add(sc, add(mul(d, x0), (0, 0, z0)))
        faces.rect(add(outer, (0, 0, z1 - z0)), mul(d, x1 - x0), th, frame_role, (0, 0, 1))
        faces.rect(outer, (0, 0, z1 - z0), th, frame_role, mul(d, -1))
        faces.rect(add(outer, mul(d, x1 - x0)), (0, 0, z1 - z0), th, frame_role, d)

    # отлив под окном — тёмный металл
    if kind != "door":
        ext = max(left, right, 0.04)
        sill = add(corner, add(mul(d, -ext), (0, 0, -bottom)))
        width = mul(d, w + 2 * ext)
        faces.rect(sill, width, mul(out, SILL_OUT), "roof", (0, 0, 1))
        faces.rect(add(sill, add(mul(out, SILL_OUT), (0, 0, -0.05))), width, (0, 0, 0.05),
                   "roof", out)


def relief(faces, item, role="trim"):
    """Выступающий элемент фасада с чертежа (тяга, лопатка): короб на стене."""
    axis, sign = SIDES[item["side"]]
    out = point(axis, 0.0, float(sign), 0.0)
    d = point(axis, 1.0, 0.0, 0.0)
    u0, u1, z0, z1 = item["u0"], item["u1"], item["z0"], item["z1"]
    if u1 - u0 < 0.01 or z1 - z0 < 0.01:
        return
    front = item["face"] + sign * item["depth"]
    a = point(axis, u0, front, z0)
    faces.rect(a, mul(d, u1 - u0), (0, 0, z1 - z0), role, out)
    back = mul(out, -item["depth"])
    faces.rect(add(a, (0, 0, z1 - z0)), mul(d, u1 - u0), back, role, (0, 0, 1))
    faces.rect(a, mul(d, u1 - u0), back, role, (0, 0, -1))
    faces.rect(a, (0, 0, z1 - z0), back, role, mul(d, -1))
    faces.rect(add(a, mul(d, u1 - u0)), (0, 0, z1 - z0), back, role, d)


def intervals_without(lo, hi, cuts):
    """Отрезок [lo, hi] без кусков cuts (список (a, b))."""
    parts = [(lo, hi)]
    for a, b in cuts:
        nxt = []
        for x0, x1 in parts:
            if b <= x0 or a >= x1:
                nxt.append((x0, x1))
                continue
            if a > x0:
                nxt.append((x0, a))
            if b < x1:
                nxt.append((b, x1))
        parts = nxt
    return [(a, b) for a, b in parts if b - a > 0.02]


def band(faces, p, q, za, zb, depth, holes, tile="trim", extend=True):
    """Горизонтальная тяга вдоль стены p→q (пояс, карниз): лицо, верх, низ, торцы.

    Там, где тягу пересекает проём, она прерывается.
    """
    edge = (q[0] - p[0], q[1] - p[1], 0.0)
    length = math.hypot(edge[0], edge[1])
    if length < 0.3:
        return
    d = mul(edge, 1 / length)
    out = (d[1], -d[0], 0.0)
    ext = depth if extend else 0.0
    cuts = [(h[0] - SURROUND_W, h[1] + SURROUND_W) for h in holes
            if h[2] < zb + SURROUND_W and h[3] + SURROUND_W > za]
    for a, b in intervals_without(-ext, length + ext, cuts):
        corner = add((p[0], p[1], za), mul(d, a))
        run = mul(d, b - a)
        front = add(corner, mul(out, depth))
        faces.rect(front, run, (0, 0, zb - za), tile, out)
        faces.rect(add(corner, (0, 0, zb)), run, mul(out, depth), tile, (0, 0, 1))
        faces.rect(corner, run, mul(out, depth), tile, (0, 0, -1))
        for end, facing in ((corner, mul(d, -1)), (add(corner, run), d)):
            faces.rect(end, mul(out, depth), (0, 0, zb - za), tile, facing)


def pilasters(faces, ring, z0, z1):
    """Угловые лопатки на выпуклых углах контура (где обе стены длиннее 1.5 м)."""
    n = len(ring)
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        e1 = (b[0] - a[0], b[1] - a[1])
        e2 = (c[0] - b[0], c[1] - b[1])
        if e1[0] * e2[1] - e1[1] * e2[0] <= 0:      # вогнутый угол
            continue
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        if l1 < 1.5 or l2 < 1.5:
            continue
        for start, edge, length, at_end in ((a, e1, l1, True), (b, e2, l2, False)):
            d = (edge[0] / length, edge[1] / length, 0.0)
            out = (d[1], -d[0], 0.0)
            t = length - PILASTER_W if at_end else 0.0
            base = add((start[0], start[1], z0), add(mul(d, t), mul(out, PILASTER_OUT)))
            faces.rect(base, mul(d, PILASTER_W), (0, 0, z1 - z0), "trim", out)
            side = add(base, mul(d, 0 if at_end else PILASTER_W))
            faces.rect(side, mul(out, -PILASTER_OUT), (0, 0, z1 - z0), "trim",
                       mul(d, -1) if at_end else d)


def soffit(faces, inner, outer, z):
    """Свес: тёмная лобовая доска по краю и подшивка снизу между стеной и краем."""
    (ix0, iy0, ix1, iy1), (ox0, oy0, ox1, oy1) = inner, outer
    zb = z - FASCIA
    i = [(ix0, iy0, zb), (ix1, iy0, zb), (ix1, iy1, zb), (ix0, iy1, zb)]
    o = [(ox0, oy0, zb), (ox1, oy0, zb), (ox1, oy1, zb), (ox0, oy1, zb)]
    center = ((ox0 + ox1) / 2, (oy0 + oy1) / 2, z)
    for k in range(4):
        faces.add([o[k], o[(k + 1) % 4], i[(k + 1) % 4], i[k]], "trim", (0, 0, -1))
        a, b = o[k], o[(k + 1) % 4]
        top_a, top_b = (a[0], a[1], z), (b[0], b[1], z)
        mid = ((a[0] + b[0]) / 2 - center[0], (a[1] + b[1]) / 2 - center[1], 0.0)
        faces.add([a, b, top_b, top_a], "roof", mid)


def hip_roof(faces, ring, z_eave):
    """Вальмовая крыша по габариту контура (для пристроек), со свесом."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    inner = (min(xs), min(ys), max(xs), max(ys))
    x0, y0, x1, y1 = inner[0] - OVERHANG, inner[1] - OVERHANG, inner[2] + OVERHANG, inner[3] + OVERHANG
    soffit(faces, inner, (x0, y0, x1, y1), z_eave)
    rise = math.tan(math.radians(ANNEX_PITCH))
    center = ((x0 + x1) / 2, (y0 + y1) / 2, z_eave)
    if x1 - x0 >= y1 - y0:
        half = (y1 - y0) / 2
        r0, r1 = (x0 + half, center[1], z_eave + half * rise), (x1 - half, center[1], z_eave + half * rise)
    else:
        half = (x1 - x0) / 2
        r0, r1 = (center[0], y0 + half, z_eave + half * rise), (center[0], y1 - half, z_eave + half * rise)
    e = [(x0, y0, z_eave), (x1, y0, z_eave), (x1, y1, z_eave), (x0, y1, z_eave)]
    if x1 - x0 >= y1 - y0:
        polys = ([e[0], e[1], r1, r0], [e[2], e[3], r0, r1], [e[3], e[0], r0], [e[1], e[2], r1])
    else:
        polys = ([e[1], e[2], r1, r0], [e[3], e[0], r0, r1], [e[0], e[1], r0], [e[2], e[3], r1])
    for poly in polys:
        c = mul(tuple(map(sum, zip(*poly))), 1 / len(poly))
        faces.add(poly, "roof", sub(c, center))


def canopy(faces, o):
    """Козырёк над дверью: наклонная плита с выносом CANOPY."""
    axis, sign = SIDES[o["side"]]
    depth, extra = CANOPY
    ua, ub = o["u0"] - extra, o["u1"] + extra
    z_wall, z_front = o["z1"] + SURROUND_W + 0.25, o["z1"] + SURROUND_W + 0.05
    wall_a, wall_b = point(axis, ua, o["face"], z_wall), point(axis, ub, o["face"], z_wall)
    front_a = point(axis, ua, o["face"] + sign * depth, z_front)
    front_b = point(axis, ub, o["face"] + sign * depth, z_front)
    out = point(axis, 0.0, float(sign), 0.0)
    faces.add([wall_a, wall_b, front_b, front_a], "roof", add(out, (0, 0, 3)))
    low = [add(v, (0, 0, -0.06)) for v in (wall_a, wall_b, front_b, front_a)]
    faces.add(low, "trim", (0, 0, -1))
    faces.add([front_a, front_b, low[2], low[3]], "trim", out)


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
        sbox = None
        if o.get("surround"):
            su0, su1, sz0, sz1 = o["surround"]
            ts = sorted(((su0 - p[axis]) * direction, (su1 - p[axis]) * direction))
            sbox = (ts[0], ts[1], sz0, sz1)
        holes.append((max(t[0], 0.0), min(t[1], length), o["z0"], o["z1"], o["type"], sbox))
        used.add(i)
    return holes


def prism_walls(faces, part, openings, used, z0, z1, kind, skip_ring=None):
    """Стены части по контуру с проёмами; у дома и пристроек — пояс, карниз, лопатки."""
    ring = [tuple(p) for p in part["outline"]]
    if signed_area(ring) < 0:
        ring = ring[::-1]
    for p, q in zip(ring, ring[1:] + ring[:1]):
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if skip_ring and on_ring(mid, skip_ring):
            continue                   # стена пристройки, прижатая к основному объёму
        holes = wall(faces, p, q, z0, z1, edge_holes(p, q, part["id"], openings, used), kind)
        if kind in ("main", "annex") and not DRAWING_DETAILS:
            if z1 > FLOOR2_Z + 1.0:
                band(faces, p, q, FLOOR2_Z + BELT[0], FLOOR2_Z + BELT[1], BELT_OUT, holes or [])
            band(faces, p, q, z1 - CORNICE_H, z1, CORNICE_OUT, holes or [])
    if kind in ("main", "annex") and not DRAWING_DETAILS:
        pilasters(faces, ring, 0.0, z1 - CORNICE_H)
    return ring


def mansard_roof(faces, outline, roof, z_eave):
    """Мансардная вальмовая крыша по габаритному прямоугольнику контура."""
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    planes = {"SE": min(ys), "NW": max(ys), "SW": min(xs), "NE": max(xs)}
    inner = (min(xs), min(ys), max(xs), max(ys))
    x0, x1, y0, y1 = min(xs) - OVERHANG, max(xs) + OVERHANG, min(ys) - OVERHANG, max(ys) + OVERHANG
    soffit(faces, inner, (x0, y0, x1, y1), z_eave)
    r = ROOF_LOWER_RUN + OVERHANG
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
    return {"planes": planes}


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
        faces.rect(a, sub(point(axis, u, back, zb), a), (0, 0, zt - zb), "dormer", facing)

    # двускатная крыша и фронтон
    um = (ua + ub) / 2
    zr = zt + DORMER_RISE
    fl, fr, fm = point(axis, ua, front, zt), point(axis, ub, front, zt), point(axis, um, front, zr)
    bl, br, bm = point(axis, ua, back, zt), point(axis, ub, back, zt), point(axis, um, back, zr)
    faces.add([fl, fr, fm], "dormer", out)
    faces.add([fl, fm, bm, bl], "roof", add(mul(d, -1), (0, 0, 1)))
    faces.add([fm, fr, br, bm], "roof", add(d, (0, 0, 1)))


# ---------- атлас и материал ----------

def read_png_blender(path):
    """PNG -> массив 0…1 (строки сверху вниз) средствами Blender."""
    img = bpy.data.images.load(str(path), check_existing=False)
    w, h = img.size
    arr = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(arr)
    bpy.data.images.remove(img)
    return arr.reshape(h, w, 4)[::-1, :, :3].astype(float)


class Atlas:
    """Атлас из материалов библиотеки: плитка на каждую роль поверхности."""

    def __init__(self, roles, colors, overrides):
        self.roles = roles
        self.grid = math.ceil(math.sqrt(len(roles)))
        self.tile_px = ATLAS // self.grid
        self.slots, self.meta, self.tiles, self.used = {}, {}, {}, {}
        for i, role in enumerate(roles):
            name, color = ROLE_DEFAULTS[role]
            params = {}
            if color:
                params["color"] = colors.get(color, color) if not color.startswith("#") else color
            custom = overrides.get(role, {})
            if custom:
                try:                      # своё имя, ambientcg:<id> или поиск по словам
                    name = cc0.resolve(custom, library.catalog())
                    params = dict(custom.get("params", {}))   # свой материал — свой цвет
                except Exception as err:
                    print(f"  «{role}»: не удалось получить {custom} ({err}) — беру {name}")
            try:
                maps, meta = library.tile(name, self.tile_px, params, read_png_blender)
            except KeyError:
                fallback = ROLE_DEFAULTS[role][0]
                print(f"  материала {name} нет в библиотеке — для «{role}» беру {fallback}")
                maps, meta = library.tile(fallback, self.tile_px, params, read_png_blender)
                name = fallback
            self.slots[role] = i
            self.meta[role] = meta
            self.tiles[role] = maps
            self.used[role] = name

    def uv(self, role, s, t):
        """(s, t) от 0 до 1 внутри плитки роли -> UV атласа."""
        i = self.slots[role]
        col, row = i % self.grid, i // self.grid
        size = self.tile_px / ATLAS
        s = UV_PAD + (1 - 2 * UV_PAD) * min(max(s, 0.0), 1.0)
        t = UV_PAD + (1 - 2 * UV_PAD) * min(max(t, 0.0), 1.0)
        return ((col + s) * size, 1 - (row + 1) * size + t * size)

    def save(self, out_dir, base):
        """Четыре карты атласа в PNG и в картинки Blender для материала."""
        images = {}
        for key in "dnrm":
            canvas = np.zeros((ATLAS, ATLAS, 3))
            canvas[...] = {"d": 0.5, "n": (0.5, 0.5, 1.0), "r": 0.9, "m": 0.0}[key]
            for role, maps in self.tiles.items():
                i = self.slots[role]
                y, x = (i // self.grid) * self.tile_px, (i % self.grid) * self.tile_px
                canvas[y:y + self.tile_px, x:x + self.tile_px] = maps[key]
            name = f"T_{base}_{key}_1"
            img = bpy.data.images.new(name, ATLAS, ATLAS, alpha=False)
            if key != "d":
                img.colorspace_settings.name = "Non-Color"
            rgba = np.concatenate([canvas[::-1], np.ones((ATLAS, ATLAS, 1))], axis=2)
            img.pixels.foreach_set(rgba.astype(np.float32).ravel())
            img.filepath_raw = str(out_dir / f"{name}.png")
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

DRAWING_DETAILS = False   # есть ли в building.json детали фасада с чертежа


def build_faces(data):
    global DRAWING_DETAILS
    DRAWING_DETAILS = bool(data.get("details"))
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
        if kind == "annex":
            hip_roof(faces, pring, top)
        else:
            cap(faces, pring, top, "plinth")

    for item in data.get("details", []):
        relief(faces, item)
    for o in openings:
        if o["type"] == "door" and o["part"] == main["id"]:
            canopy(faces, o)
    for i, o in enumerate(openings):
        if o["type"] == "dormer":
            dormer(faces, o, roof["planes"][o["side"]], main["z_eave"], main["roof"]["z_break"])
            used.add(i)
    return faces, len(openings) - len(used)


def clip(pts, axis_vec, lo, hi):
    """Часть многоугольника, где проекция на axis_vec лежит в [lo, hi]."""
    for bound, keep_above in ((lo, True), (hi, False)):
        out = []
        for p, q in zip(pts, pts[1:] + pts[:1]):
            dp, dq = dot(p, axis_vec) - bound, dot(q, axis_vec) - bound
            inside_p = dp >= -1e-9 if keep_above else dp <= 1e-9
            inside_q = dq >= -1e-9 if keep_above else dq <= 1e-9
            if inside_p:
                out.append(p)
            if inside_p != inside_q:
                t = dp / (dp - dq)
                out.append(add(p, mul(sub(q, p), t)))
        pts = out
        if len(pts) < 3:
            return []
    return pts


def chunks(pts, axis_vec, period, split):
    """Режет грань по линиям, кратным period (если split), чтобы каждый кусок
    помещался в одну плитку текстуры в реальном масштабе."""
    values = [dot(p, axis_vec) for p in pts]
    lo, hi = min(values), max(values)
    if not split or hi - lo <= period + 1e-6:
        k = math.floor(lo / period + 1e-6)
        return [(pts, k)]
    pieces = []
    k = math.floor(lo / period + 1e-6)
    while k * period < hi - 1e-6:
        part = clip(pts, axis_vec, k * period, (k + 1) * period)
        if part and abs(signed_area_3d(part)) > 1e-6:
            pieces.append((part, k))
        k += 1
    return pieces


def face_axes(n):
    """Оси текстуры на грани: u — по горизонтали, v — вверх по грани."""
    u_axis = norm(cross((0, 0, 1), n)) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    return u_axis, cross(n, u_axis)


def to_mesh(faces, name, rotation_deg, atlas):
    """Грани -> меш Blender: общие вершины, UV в реальном масштабе, треугольники.

    Текстура кладётся в реальном размере (size_m материала). Вдоль направления,
    где узор однороден (stretch), грань растягивается на всю плитку; поперёк —
    режется на куски по размеру плитки, чтобы рисунок не растягивался.
    """
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

    for pts, role in faces.items:
        meta = atlas.meta[role]
        size_u, size_v = meta["size_m"]
        stretch = meta.get("stretch", "both")
        n = newell(pts)
        u_axis, v_axis = face_axes(n)
        for piece_u, ku in chunks(pts, u_axis, size_u, stretch not in ("u", "both")):
            for piece, kv in chunks(piece_u, v_axis, size_v, stretch not in ("v", "both")):
                try:
                    face = bm.faces.new([vert(p) for p in piece])
                except ValueError:
                    continue                            # такая грань уже есть
                us = [dot(p, u_axis) for p in piece]
                vs = [dot(p, v_axis) for p in piece]
                for loop, uu, vv in zip(face.loops, us, vs):
                    if stretch in ("u", "both"):
                        su = (uu - min(us)) / ((max(us) - min(us)) or 1)
                    else:
                        su = (uu - ku * size_u) / size_u
                    if stretch in ("v", "both"):
                        sv = (vv - min(vs)) / ((max(vs) - min(vs)) or 1)
                    else:
                        sv = (vv - kv * size_v) / size_v
                    loop[uv_layer].uv = atlas.uv(role, su, sv)
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
    roles = sorted({role for _, role in faces.items}, key=list(ROLE_DEFAULTS).index)
    atlas = Atlas(roles, data["parts"][0]["colors"], cfg.get("materials", {}))
    for role in roles:
        print(f"  {role:7} <- {atlas.used[role]}")
    mesh = to_mesh(faces, f"SM_{base}_Main", data["rotation_deg"], atlas)
    images = atlas.save(tex_dir, f"{base}_Main")
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

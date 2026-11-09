import json
import math
import sys
from pathlib import Path

import ezdxf
from ezdxf.math import Vec2

from find_building import is_ring, polyline_points


def clean_ring(pts):
    """Убирает повторённую последнюю точку, если она совпадает с первой."""
    if pts[0].isclose(pts[-1], abs_tol=0.01):
        return pts[:-1]
    return pts


def signed_area(pts):
    s = 0.0
    for a, b in zip(pts, pts[1:] + pts[:1]):
        s += a.x * b.y - b.x * a.y
    return s / 2


def centroid(pts):
    """Центр тяжести многоугольника."""
    a = signed_area(pts)
    cx = cy = 0.0
    for p, q in zip(pts, pts[1:] + pts[:1]):
        cross = p.x * q.y - q.x * p.y
        cx += (p.x + q.x) * cross
        cy += (p.y + q.y) * cross
    return Vec2(cx / (6 * a), cy / (6 * a))


def main_angle(pts):
    """Угол самой длинной стены в градусах, от 0 до 180."""
    longest = max(zip(pts, pts[1:] + pts[:1]), key=lambda ab: (ab[1] - ab[0]).magnitude)
    d = longest[1] - longest[0]
    return math.degrees(math.atan2(d.y, d.x)) % 180


if __name__ == "__main__":
    path, layer = sys.argv[1], sys.argv[2]
    msp = ezdxf.readfile(path).modelspace()

    rings = []
    for e in msp:
        if e.dxf.layer == layer and e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            pts = polyline_points(e)
            if is_ring(e, pts):
                rings.append(clean_ring(pts))
    main = max(rings, key=lambda r: abs(signed_area(r)))

    origin = centroid(main)
    angle = main_angle(main)
    local = [(p - origin).rotate_deg(-angle) for p in main]
    xs, ys = [p.x for p in local], [p.y for p in local]

    building = {
        "origin_msk": [round(origin.x, 3), round(origin.y, 3)],
        "rotation_deg": round(angle, 3),
        "parts": [{
            "id": "P1",
            "outline": [[round(p.x, 3), round(p.y, 3)] for p in local],
        }],
        "review": ["высота, кровля и пристройки ещё не заполнены"],
    }

    out = Path(path).parent.parent / "building.json"
    out.write_text(json.dumps(building, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Контуров найдено: {len(rings)}, выбран самый большой")
    print(f"Площадь: {abs(signed_area(main)):.1f} м², вершин: {len(main)}")
    print(f"Размер в своих осях: {max(xs) - min(xs):.2f} x {max(ys) - min(ys):.2f} м")
    print(f"Привязка МСК: {origin.x:.2f}, {origin.y:.2f}   поворот: {angle:.2f}°")
    print(f"Сохранено: {out}")
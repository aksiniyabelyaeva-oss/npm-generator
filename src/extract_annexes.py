import json
import sys
from pathlib import Path

import ezdxf
from ezdxf.math import Vec2

from extract_contour import clean_ring, signed_area
from find_building import dist_to_polyline, is_ring, polyline_points

TOUCH = 0.3          # насколько близко к стене должен быть конец линии, м
ANNEX_EAVE = 6.40    # оценка высоты пристроек по фасадам
PORCH_TOP = 0.00     # верх крыльца примерно на уровне пола 1-го этажа


def nearest_edge(p, ring):
    """Номер стены здания, ближайшей к точке."""
    edges = list(zip(ring, ring[1:] + ring[:1]))
    return min(range(len(edges)), key=lambda i: dist_to_polyline(p, list(edges[i]), False))


if __name__ == "__main__":
    dxf_path, layer = sys.argv[1], sys.argv[2]
    json_path = Path(dxf_path).parent.parent / "building.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))

    origin = Vec2(data["origin_msk"])
    angle = data["rotation_deg"]
    main_part = data["parts"][0]
    main = [Vec2(p).rotate_deg(angle) + origin for p in main_part["outline"]]
    main_area = abs(signed_area(main))

    def to_local(p):
        return (p - origin).rotate_deg(-angle)

    parts = [main_part]
    review = [r for r in data["review"] if "пристройки" not in r]
    msp = ezdxf.readfile(dxf_path).modelspace()

    for e in msp:
        if e.dxf.layer != layer or e.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
            continue
        pts = polyline_points(e)
        if len(pts) < 2:
            continue

        if is_ring(e, pts):
            ring = clean_ring(pts)
            area = abs(signed_area(ring))
            if area > 0.9 * main_area:
                continue                      # это само здание
            kind, z_top = "porch", PORCH_TOP
        else:
            d0 = dist_to_polyline(pts[0], main, True)
            d1 = dist_to_polyline(pts[-1], main, True)
            if d0 > TOUCH or d1 > TOUCH:
                print(f"пропуск: открытая линия не касается здания ({d0:.1f} и {d1:.1f} м)")
                continue
            ring = pts
            area = abs(signed_area(ring))
            kind, z_top = "annex", ANNEX_EAVE
            if nearest_edge(pts[0], main) != nearest_edge(pts[-1], main):
                review.append(f"P{len(parts) + 1}: концы на разных стенах, проверить замыкание")

        part_id = f"P{len(parts) + 1}"
        parts.append({
            "id": part_id,
            "kind": kind,
            "outline": [[round(p.x, 3), round(p.y, 3)] for p in map(to_local, ring)],
            "z_bottom": main_part["z_bottom"],
            "z_eave": z_top,
        })
        print(f"{part_id}: {kind:6}  вершин: {len(ring)}  площадь: {area:.1f} м²")

    review.append(f"высоты пристроек ({ANNEX_EAVE}) и крыльца ({PORCH_TOP}) — оценка, уточнить по фасадам")
    data["parts"], data["review"] = parts, review
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {json_path}")
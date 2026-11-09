import sys

import ezdxf
from ezdxf.math import Vec2


def polyline_points(e):
    """Вершины полилинии в плане (x, y)."""
    if e.dxftype() == "LWPOLYLINE":
        return [Vec2(x, y) for x, y in e.get_points("xy")]
    if e.dxftype() == "POLYLINE":
        return [Vec2(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
    return []


def is_ring(e, pts):
    """Замкнут ли контур: по флагу или по совпадению первой и последней точки."""
    if len(pts) < 3:
        return False
    flag = e.closed if e.dxftype() == "LWPOLYLINE" else e.is_closed
    return flag or pts[0].isclose(pts[-1], abs_tol=0.01)


def dist_to_segment(p, a, b):
    """Расстояние от точки p до отрезка ab."""
    ab = b - a
    length2 = ab.dot(ab)
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, (p - a).dot(ab) / length2))
    return (a + ab * t - p).magnitude


def dist_to_polyline(p, pts, closed):
    segments = list(zip(pts, pts[1:]))
    if closed:
        segments.append((pts[-1], pts[0]))
    return min(dist_to_segment(p, a, b) for a, b in segments)


def polylines_on_layer(msp, layer):
    """Полилинии слоя, включая те, что спрятаны внутри блоков."""
    for e in msp:
        if e.dxf.layer != layer:
            continue
        if e.dxftype() == "INSERT":
            for sub in e.virtual_entities():
                if sub.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                    yield "в блоке", sub
        elif e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            yield "", e


if __name__ == "__main__":
    path, layer, word = sys.argv[1], "Здания", "РЕКОНСТРУКЦИЯ"
    msp = ezdxf.readfile(path).modelspace()

    markers = [Vec2(t.dxf.insert) for t in msp.query("TEXT MTEXT")
               if word in t.plain_text().upper()]
    for m in markers:
        print(f"Подпись: x={m.x:.1f}  y={m.y:.1f}")
    print()

    for where, e in polylines_on_layer(msp, layer):
        pts = polyline_points(e)
        if len(pts) < 2:
            continue
        closed = is_ring(e, pts)
        d = min(dist_to_polyline(m, pts, closed) for m in markers)
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        print(f"{'замкнут  ' if closed else 'открыт   '} вершин: {len(pts):3}  "
              f"x: {min(xs):.1f}..{max(xs):.1f}  y: {min(ys):.1f}..{max(ys):.1f}  "
              f"до подписи: {d:5.1f} м  {where}")
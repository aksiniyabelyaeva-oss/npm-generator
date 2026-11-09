import sys

import ezdxf
import matplotlib.pyplot as plt


def polyline_xy(e):
    if e.dxftype() == "LWPOLYLINE":
        pts = list(e.get_points("xy"))
        closed = e.closed
    elif e.dxftype() == "POLYLINE":
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        closed = e.is_closed
    else:
        return None
    if closed and pts:
        pts.append(pts[0])
    return pts


if __name__ == "__main__":
    path, layer = sys.argv[1], sys.argv[2]
    msp = ezdxf.readfile(path).modelspace()
    fig, ax = plt.subplots(figsize=(10, 10))

    number = 0
    for e in msp:
        if e.dxf.layer != layer:
            continue
        pts = polyline_xy(e)
        if pts:
            number += 1
            xs, ys = zip(*pts)
            ax.plot(xs, ys, linewidth=1.5)
            ax.text(xs[0], ys[0], str(number), fontsize=9)

    for t in msp.query("TEXT MTEXT"):
        x, y = t.dxf.insert.x, t.dxf.insert.y
        ax.plot(x, y, "r*", markersize=10)
        ax.text(x, y, t.plain_text(), color="red", fontsize=8)

    ax.set_aspect("equal")
    ax.set_title(f"Слой: {layer}")
    out = "layer_plot.png"
    plt.savefig(out, dpi=150)
    print(f"Сохранено: {out}")

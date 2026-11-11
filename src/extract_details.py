"""Детали фасада с цветного чертежа: белые тяги и лопатки, наличники, цвет стекла.

Запуск (после place_openings.py):
    python src/extract_details.py "data/project_01/dxf/фасады.dxf"

Фасад в DXF залит цветами: каждая заливка — участок определённого материала.
Скрипт берёт каждый контур заливки отдельно и переводит его в координаты
стены так же, как place_openings переводит окна (ноль по карнизу, центр по кровле).

- Цвет отделки (TRIM_COLOR): широкие низкие контуры — тяги (пояс, карниз),
  узкие высокие — лопатки. Пишутся в building["details"] с точными размерами.
- Белый контур, который охватывает окно, — наличник: building.openings[*].surround.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf import path as ezpath

from hatch_colors import hatch_color
from place_openings import (FACADE_LAYER, FACADE_SIDE, SIDES, choose_zeros, find_face,
                            roof_band, wall_frame)

TRIM_COLOR = "#F1ECE1"     # белая отделка: тяги, лопатки, карнизы
BAND = {"min_w": 1.0, "max_h": 0.6}
PILASTER = {"max_w": 1.0, "min_h": 1.5}
CORNICE_DEPTH, BELT_DEPTH, PILASTER_DEPTH = 0.15, 0.06, 0.04
SURROUND_MIN = 0.03        # наличник должен выходить за окно хотя бы на 3 см


def hatch_boxes(msp, fb):
    """Габарит каждого контура каждой заливки фасада: (цвет, x0, y0, x1, y1), мм."""
    for e in msp.query("HATCH"):
        if e.dxf.layer != FACADE_LAYER:
            continue
        b = ezbbox.extents([e], fast=True)
        if not b.has_data:
            continue
        if not (fb[0] - 1 <= b.extmin.x and b.extmax.x <= fb[2] + 1
                and fb[1] - 1 <= b.extmin.y and b.extmax.y <= fb[3] + 1):
            continue
        color = hatch_color(e)
        for p in ezpath.from_hatch(e):
            pts = list(p.flattening(20))
            if len(pts) < 3:
                continue
            xs, ys = [v.x for v in pts], [v.y for v in pts]
            yield color, min(xs), min(ys), max(xs), max(ys)


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    data_dir = dxf_path.parent.parent
    path = data_dir / "building.json"
    building = json.loads(path.read_text(encoding="utf-8"))
    checked = json.loads((data_dir / "windows_checked.json").read_text(encoding="utf-8"))
    msp = ezdxf.readfile(dxf_path).modelspace()

    parts = building["parts"]
    main = parts[0]
    planes, center, _ = wall_frame(main["outline"])
    boxes = {name: checked[name]["bbox"] for name in FACADE_SIDE}
    roofs = {n: r for n in FACADE_SIDE if (r := roof_band(msp, boxes[n])) is not None}
    print("Проверка нулей по рядам:")
    zeros = choose_zeros(boxes, roofs, main["z_eave"], main["roof"]["z_ridge"])

    details, surrounds = [], 0
    for name, side in FACADE_SIDE.items():
        if name not in roofs:
            continue
        d = SIDES[side]
        cx = (roofs[name][0] + roofs[name][1]) / 2
        base = center[d["axis"]]

        def to_wall(x0, y0, x1, y1):
            u0, u1 = sorted((base + d["flip"] * (x0 - cx) / 1000, base + d["flip"] * (x1 - cx) / 1000))
            return [u0, u1, (y0 - zeros[name]) / 1000, (y1 - zeros[name]) / 1000]

        shapes = [(color, to_wall(*box)) for color, *box in hatch_boxes(msp, boxes[name])]
        openings = [o for o in building.get("openings", [])
                    if o["facade"] == name and o["type"] in ("window", "door")]

        # наличники: самый большой контур не цвета стены, охватывающий окно
        wall_colors = {c for c in building["parts"][0]["colors"].values()}
        for o in openings:
            ob = [o["u0"], o["u1"], o["z0"], o["z1"]]
            area = (ob[1] - ob[0]) * (ob[3] - ob[2])
            around = [s for c, s in shapes
                      if c not in wall_colors and c != TRIM_COLOR
                      and s[0] <= ob[0] + 0.01 and s[1] >= ob[1] - 0.01
                      and s[2] <= ob[2] + 0.01 and s[3] >= ob[3] - 0.01
                      and (s[1] - s[0]) * (s[3] - s[2]) <= 3 * area]
            if around:
                s = max(around, key=lambda s: (s[1] - s[0]) * (s[3] - s[2]))
                margin = max(ob[0] - s[0], s[1] - ob[1], ob[2] - s[2], s[3] - ob[3])
                if margin >= SURROUND_MIN:
                    o["surround"] = [round(v, 3) for v in s]
                    surrounds += 1

        # тяги и лопатки цвета отделки
        for color, (u0, u1, z0, z1) in shapes:
            if color != TRIM_COLOR:
                continue
            w, h = u1 - u0, z1 - z0
            if w >= BAND["min_w"] and h <= BAND["max_h"]:
                kind = "band"
            elif h >= PILASTER["min_h"] and w <= PILASTER["max_w"]:
                kind = "pilaster"
            else:
                continue
            hit = find_face(parts, d, planes[side], (u0 + u1) / 2, (z0 + z1) / 2)
            if hit is None:
                continue
            _, part_id, face = hit
            part = next(p for p in parts if p["id"] == part_id)
            if kind == "pilaster":
                depth = PILASTER_DEPTH
            elif z1 >= part["z_eave"] - 0.6:
                depth = CORNICE_DEPTH
            else:
                depth = BELT_DEPTH
            details.append({"facade": name, "side": side, "part": part_id, "kind": kind,
                            "face": round(face, 3), "depth": depth,
                            "u0": round(u0, 3), "u1": round(u1, 3),
                            "z0": round(z0, 3), "z1": round(z1, 3)})

    building["details"] = details
    building["review"] = [r for r in building["review"] if not r.startswith("детали:")]
    building["review"].append(
        f"детали: с фасадов {len(details)} тяг и лопаток, {surrounds} наличников")
    path.write_text(json.dumps(building, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nДетали по стенам:")
    for (side, part, kind), n in sorted(Counter((x["side"], x["part"], x["kind"]) for x in details).items()):
        print(f"  {side}  {part:3}  {kind:9} {n}")
    for x in details:
        if x["kind"] == "band":
            print(f"  тяга {x['side']} {x['part']}: z {x['z0']:+.2f}…{x['z1']:+.2f}, "
                  f"u {x['u0']:+.2f}…{x['u1']:+.2f}, вынос {x['depth']} м")
    print(f"Наличников: {surrounds}")
    print(f"Сохранено: {path}")

"""Отметки земли у здания с топоплана -> рельеф для цоколя.

Запуск: python src/extract_ground.py data/project_01/dxf/топоплан.dxf [слой]

На топоплане отметки земли подписаны числами вида 137.57 (слой «Горизонтали»).
Берём все такие подписи в радиусе RADIUS от центра здания, переводим в местные
координаты и высоты от нуля здания (zero_abs).

В building.json записывается:
- ground.marks — [x, y, z] отметок в местных координатах;
- ground.min / ground.max — самая низкая и самая высокая земля у здания;
- z_bottom всех частей = самая низкая земля. Генератор опускает стены ещё на BURY
  ниже неё, чтобы на склоне модель нигде не «висела» над землёй.
"""
import json
import math
import re
import sys
from pathlib import Path

import ezdxf

RADIUS = 30.0          # м от центра здания
MARK = re.compile(r"^\s*(\d{2,3}[.,]\d{1,2})\s*$")


def texts(msp, layer):
    for e in msp.query("TEXT MTEXT INSERT"):
        if layer and e.dxf.layer != layer:
            continue
        if e.dxftype() == "INSERT":
            for a in e.attribs:
                yield a.dxf.text, a.dxf.insert
        else:
            yield e.plain_text(), e.dxf.insert


if __name__ == "__main__":
    dxf_path = Path(sys.argv[1])
    layer = sys.argv[2] if len(sys.argv) > 2 else "Горизонтали"
    data_dir = dxf_path.parent.parent
    path = data_dir / "building.json"
    building = json.loads(path.read_text(encoding="utf-8"))
    ox, oy = building["origin_msk"]
    zero = building["zero_abs"]
    a = math.radians(-building["rotation_deg"])
    c, s = math.cos(a), math.sin(a)

    msp = ezdxf.readfile(dxf_path).modelspace()
    marks = []
    for text, p in texts(msp, layer):
        m = MARK.match(text)
        if not m:
            continue
        value = float(m.group(1).replace(",", "."))
        if abs(value - zero) > 15:                 # не отметка земли (номер, размер...)
            continue
        dx, dy = p.x - ox, p.y - oy
        if math.hypot(dx, dy) > RADIUS:
            continue
        marks.append([round(dx * c - dy * s, 2), round(dx * s + dy * c, 2), round(value - zero, 2)])

    if not marks:
        print(f"Отметок земли на слое «{layer}» в радиусе {RADIUS} м нет — z_bottom не меняю")
        sys.exit(0)
    low = min(m[2] for m in marks)
    high = max(m[2] for m in marks)
    building["ground"] = {"marks": marks, "min": low, "max": high, "layer": layer}
    for part in building["parts"]:
        part["z_bottom"] = low
    building["review"] = [r for r in building["review"] if not r.startswith("земля:")]
    building["review"].append(
        f"земля: {len(marks)} отметок с топоплана в {RADIUS:.0f} м, от {low:+.2f} до {high:+.2f} "
        f"(абс. {low + zero:.2f}…{high + zero:.2f}); низ частей = самая низкая земля")
    path.write_text(json.dumps(building, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отметок земли: {len(marks)}, от {low:+.2f} до {high:+.2f} м от нуля "
          f"(абс. {low + zero:.2f}…{high + zero:.2f})")
    print(f"z_bottom частей = {low:+.2f}. Сохранено: {path}")

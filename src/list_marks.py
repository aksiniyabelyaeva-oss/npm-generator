import re
import sys

import ezdxf

from summarize_layers import entity_texts

MARK = re.compile(r"[±+\-]\s?\d+[.,]\d{3}")


if __name__ == "__main__":
    path = sys.argv[1]
    msp = ezdxf.readfile(path).modelspace()

    found = []
    for e in msp:
        for text in entity_texts(e):
            for m in MARK.findall(text):
                value = float(m.replace("±", "").replace(",", ".").replace(" ", ""))
                found.append((value, e.dxf.layer, e.dxf.insert.x, e.dxf.insert.y))

    for value, layer, x, y in sorted(found):
        print(f"{value:+8.3f}   слой: {layer:20}   x={x:10.0f}  y={y:10.0f}")
    print(f"\nВсего отметок: {len(found)}")
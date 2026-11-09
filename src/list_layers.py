import sys
from collections import Counter
from pathlib import Path

import ezdxf


def count_layers(path):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    counter = Counter()
    for entity in msp:
        counter[entity.dxf.layer] += 1
    return counter


def print_layers(path):
    print(f"\n=== {path.name} ===")
    for layer, count in count_layers(path).most_common():
        print(f"{count:6}  {layer}")


if __name__ == "__main__":
    target = Path(sys.argv[1])
    if target.is_dir():
        for path in sorted(target.glob("*.dxf")):
            print_layers(path)
    else:
        print_layers(target)

"""Читает служебные сведения из двоичного FBX без Blender.

Запуск: python src/fbx_info.py файл.fbx

Нужно валидатору: версия формата, двоичный ли файл, единицы и оси
(GlobalSettings). Геометрию не разбирает — только заголовок и настройки.
"""
import struct
import sys
import zlib

MAGIC = b"Kaydara FBX Binary  \x00"


def read_property(data, pos):
    """Одно значение свойства узла: (значение, новая позиция)."""
    kind = chr(data[pos])
    pos += 1
    simple = {"Y": "<h", "C": "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}
    if kind in simple:
        fmt = simple[kind]
        return struct.unpack_from(fmt, data, pos)[0], pos + struct.calcsize(fmt)
    if kind in "fdlib":
        length, encoding, size = struct.unpack_from("<III", data, pos)
        pos += 12
        raw = data[pos:pos + size]
        if encoding == 1:
            raw = zlib.decompress(raw)
        fmt = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "?"}[kind]
        return list(struct.unpack(f"<{length}{fmt}", raw)), pos + size
    if kind in "SR":
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        raw = data[pos:pos + size]
        value = raw.decode("utf-8", "replace") if kind == "S" else raw
        return value, pos + size
    raise ValueError(f"неизвестный тип свойства {kind!r} на позиции {pos}")


def read_node(data, pos, wide):
    """Узел FBX: (имя, свойства, дети, позиция после узла) или None в конце списка."""
    if wide:   # FBX 7.5+: 64-битные смещения
        end, count, _ = struct.unpack_from("<QQQ", data, pos)
        pos += 24
        null_len = 25
    else:
        end, count, _ = struct.unpack_from("<III", data, pos)
        pos += 12
        null_len = 13
    name_len = data[pos]
    pos += 1
    if end == 0:
        return None
    name = data[pos:pos + name_len].decode("ascii", "replace")
    pos += name_len
    props = []
    for _ in range(count):
        value, pos = read_property(data, pos)
        props.append(value)
    children = []
    while pos < end - null_len + 1 and pos < end:
        child = read_node(data, pos, wide)
        if child is None:
            break
        children.append(child[:3])
        pos = child[3]
    return name, props, children, end


def fbx_info(path):
    """Версия, двоичность и настройки сцены (единицы, оси) из FBX."""
    data = open(path, "rb").read()
    if not data.startswith(MAGIC):
        return {"binary": False}
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    pos, nodes = 27, []
    while pos < len(data):
        node = read_node(data, pos, wide)
        if node is None:
            break
        nodes.append(node[:3])
        pos = node[3]
    settings = {}
    for name, _, children in nodes:
        if name != "GlobalSettings":
            continue
        for cname, _, grand in children:
            if cname != "Properties70":
                continue
            for _, props, _ in grand:
                if len(props) >= 5:
                    settings[props[0]] = props[4]
    return {"binary": True, "version": version, "settings": settings,
            "top_nodes": [n[0] for n in nodes]}


if __name__ == "__main__":
    info = fbx_info(sys.argv[1])
    print(f"двоичный: {info['binary']}")
    if info["binary"]:
        print(f"версия: {info['version']}")
        for key in ("UpAxis", "UpAxisSign", "FrontAxis", "FrontAxisSign", "CoordAxis",
                    "CoordAxisSign", "UnitScaleFactor", "OriginalUnitScaleFactor"):
            print(f"  {key}: {info['settings'].get(key)}")

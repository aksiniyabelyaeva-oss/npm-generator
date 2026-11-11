"""Проверка готовой НПМ по требованиям ДГП.

Запуск через Blender (обычно его делает run_project.py):
    blender --background --python src/validate_npm.py -- data/project_01/output/файл.fbx projects/project_01.json

Основание: распоряжение ДИТ и ДГП Москвы от 18.08.2026 № ДГП-Р-56/26/64-16-473/26,
приложение 1 «Требования к параметрам низкополигональных трёхмерных моделей».
В каждой проверке указан пункт: «т1» — таблица 1, «т2 п. 3.7» — таблица 2, пункт 3.7.

Итог пишется в output/validation.txt. Ошибки — модель не примут,
предупреждения — стоит посмотреть. При ошибках код выхода 1.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # чтобы найти fbx_info.py

import bpy  # noqa: E402
import bmesh  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from fbx_info import fbx_info  # noqa: E402

GEOMETRY_SUFFIXES = ("Main", "MainGlass")                  # т2 п. 9.1 (для ОКС)
TEXTURE_SUFFIXES = ("d", "n", "o", "m", "r")               # т2 п. 9.2
TEXTURE_SIZES = {256, 512, 1024, 2048}                     # т2 п. 5.2
STUB_SIZE = 128                                            # заглушка, т2 п. 5.7
MAX_TEXTURE_MB = 3.0                                       # т2 п. 5.3
MAX_TRIS = 150000                                          # т2 п. 3.6
MAX_DEFECTS = 100                                          # т2 п. 3.7
DUP_TOL = 0.002                                            # т2 п. 3.7, м
MAX_NAME = 254                                             # т2 п. 10.1
LATIN = re.compile(r"^[A-Za-z0-9_]+$")                    # т2 п. 10.2


class Report:
    def __init__(self):
        self.lines, self.errors, self.warnings = [], 0, 0

    def check(self, condition, good, bad, level="error"):
        if condition:
            self.lines.append(f"[ OK ] {good}")
        elif level == "warn":
            self.warnings += 1
            self.lines.append(f"[ ?? ] {bad}")
        else:
            self.errors += 1
            self.lines.append(f"[ !! ] {bad}")

    def section(self, title):
        self.lines.append(f"\n— {title}")


def check_file(rep, path, cfg):
    rep.section("Файл")
    stem = path.stem
    rep.check(LATIN.match(stem) is not None and len(path.name) <= MAX_NAME,
              f"имя файла латиницей: {path.name}",
              f"имя файла {path.name}: только латиница, цифры, «_», до {MAX_NAME} символов (т2 п. 10.2)")
    rep.check(re.match(r"^\d{4}_[A-Za-z0-9_]+_\d{2}$", stem) is not None,
              "имя по схеме префикс_Address_номер (0507_…_01)",
              f"имя {stem}: нужно префикс(4 цифры)_Address_номер(01…20) (т2 п. 10.3.2)")
    expected = cfg.get("npm", {}).get("file_name")
    if expected:
        rep.check(stem == expected, "имя совпадает с настройками проекта",
                  f"имя файла {stem}, а в настройках {expected}", "warn")
    info = fbx_info(path)
    rep.check(info["binary"], "FBX двоичный", "FBX не двоичный (т1 п. 1.1)")
    if not info["binary"]:
        return
    rep.check(info["version"] == 7400, "FBX 7.4 (7400)", f"версия FBX {info['version']}, нужна 7400 (т1 п. 1.1)")
    s = info["settings"]
    rep.check(s.get("UnitScaleFactor") == 100.0, "единица — метр (UnitScaleFactor 100)",
              f"UnitScaleFactor {s.get('UnitScaleFactor')}, нужен метр (т1 п. 2)")
    rep.check(s.get("UpAxis") == 2 and s.get("UpAxisSign") == 1, "ось вверх — Z",
              f"ось вверх: UpAxis {s.get('UpAxis')}, знак {s.get('UpAxisSign')}", "warn")


def pierces(tri, other, eps=1e-4):
    """Проходит ли ребро треугольника other сквозь внутреннюю часть tri.

    Касание (ребро лежит на грани или упирается в неё концом) не считается:
    концы ребра должны быть строго по разные стороны плоскости, а точка
    прохода — строго внутри треугольника. Так считаются настоящие
    самопересечения, а не грани, прижатые друг к другу.
    """
    a, b, c = (v.co for v in tri.verts)
    n = (b - a).cross(c - a)
    if n.length < 1e-12:
        return False
    n.normalize()
    pts = [v.co for v in other.verts]
    for p, q in zip(pts, pts[1:] + pts[:1]):
        dp, dq = n.dot(p - a), n.dot(q - a)
        if dp * dq >= 0 or abs(dp) < eps or abs(dq) < eps:
            continue
        x = p + (q - p) * (dp / (dp - dq))
        v0, v1, v2 = b - a, c - a, x - a
        d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
        d20, d21 = v2.dot(v0), v2.dot(v1)
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-12:
            continue
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        if v > eps and w > eps and v + w < 1 - eps:
            return True
    return False


def mesh_defects(ob):
    """Дефекты по т2 п. 3.7 и справочно — проникновения.

    Дубли (допуск 2 мм): вершины в одной точке, рёбра с теми же концами,
    грани с теми же вершинами (лежат друг на друге и мерцают).
    Изолированные вершины и рёбра, рёбра нулевой длины.
    Проникновения (ребро одной грани проходит сквозь другую) — для справки:
    в сданных НПМ детали обычно врезаны друг в друга, это не дефект п. 3.7.
    """
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.transform(ob.matrix_world)

    def key(co):
        return (round(co.x / DUP_TOL), round(co.y / DUP_TOL), round(co.z / DUP_TOL))

    vert_keys = {v.index: key(v.co) for v in bm.verts}
    dup_verts = sum(n - 1 for n in Counter(vert_keys.values()).values() if n > 1)
    edge_keys = Counter(tuple(sorted(vert_keys[v.index] for v in e.verts)) for e in bm.edges)
    dup_edges = sum(n - 1 for n in edge_keys.values() if n > 1)
    face_keys = Counter(tuple(sorted(vert_keys[v.index] for v in f.verts)) for f in bm.faces)
    dup_faces = sum(n - 1 for n in face_keys.values() if n > 1)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    loose_edges = sum(1 for e in bm.edges if not e.link_faces)
    zero_edges = sum(1 for e in bm.edges if e.calc_length() < 1e-6)

    tree = BVHTree.FromBMesh(bm)
    faces = bm.faces
    faces.ensure_lookup_table()
    crossing = 0
    for a, b in tree.overlap(tree):
        if a >= b:
            continue
        fa, fb = faces[a], faces[b]
        if {v.index for v in fa.verts} & {v.index for v in fb.verts}:
            continue
        if pierces(fa, fb) or pierces(fb, fa):
            crossing += 1
    bm.free()
    return {"verts": dup_verts, "edges": dup_edges, "faces": dup_faces,
            "loose_verts": loose_verts, "loose_edges": loose_edges,
            "zero_edges": zero_edges, "crossing": crossing}


def check_scene(rep, cfg, zero_abs):
    rep.section("Сцена")
    objects = list(bpy.context.scene.objects)
    meshes = [o for o in objects if o.type == "MESH"]
    extra = [f"{o.name} ({o.type})" for o in objects if o.type != "MESH"]
    rep.check(not extra, "нет камер, света, пустышек", f"лишние объекты: {', '.join(extra)} (т2 п. 1.3)")
    rep.check(all(o.parent is None for o in objects), "нет иерархии и групп",
              "есть вложенность объектов (т2 п. 3.5)")
    rep.check(1 <= len(meshes) <= 40, f"объектов геометрии: {len(meshes)}",
              f"объектов геометрии {len(meshes)}, допустимо до 40 (т2 п. 1.4)")

    total = 0
    limit = cfg.get("npm", {}).get("max_triangles", 15000)
    address = cfg.get("npm", {}).get("object_name", "")
    for ob in meshes:
        name = ob.name
        rep.section(f"Объект {name}")
        m = re.match(r"^SM_([A-Za-z0-9_]+)_(\d{3})_(Main|MainGlass)$", name)
        rep.check(m is not None and len(name) <= MAX_NAME, "имя SM_Address_номер_Main",
                  f"имя {name}: нужно SM_Address_номер(001…020)_Main или _MainGlass (т2 п. 10.3.3)")
        if address and m:
            rep.check(f"{m.group(1)}_{m.group(2)}" == address, "адрес и номер совпадают с настройками",
                      f"в имени {m.group(1)}_{m.group(2)}, в настройках {address}", "warn")
        me = ob.data
        tris = sum(len(p.vertices) - 2 for p in me.polygons)
        total += tris
        ngons = sum(1 for p in me.polygons if len(p.vertices) != 3)
        rep.check(ngons == 0, "все грани — треугольники", f"{ngons} граней не треугольники (т2 п. 3.11)")
        dfx = mesh_defects(ob)
        dups = dfx["verts"] + dfx["edges"] + dfx["faces"]
        rep.check(dups <= MAX_DEFECTS,
                  f"дублей (допуск 2 мм): вершин {dfx['verts']}, рёбер {dfx['edges']}, граней {dfx['faces']}",
                  f"дублей {dups} (вершин {dfx['verts']}, рёбер {dfx['edges']}, граней {dfx['faces']}), "
                  f"допустимо не больше {MAX_DEFECTS} (т2 п. 3.7)")
        rep.check(dfx["faces"] == 0, "нет граней, лежащих друг на друге",
                  f"граней друг на друге: {dfx['faces']} — будут мерцать (т2 п. 3.7, 3.9)", "warn")
        rep.check(dfx["loose_verts"] == 0 and dfx["loose_edges"] == 0, "нет изолированных вершин и рёбер",
                  f"изолированных вершин {dfx['loose_verts']}, рёбер {dfx['loose_edges']} (т2 п. 3.7)")
        rep.check(dfx["zero_edges"] == 0, "нет рёбер нулевой длины",
                  f"рёбер нулевой длины {dfx['zero_edges']} (т2 п. 3.7)")
        rep.lines.append(f"[info] деталей, врезанных друг в друга (пар треугольников): {dfx['crossing']}")
        rep.check(len(me.uv_layers) >= 1, "есть UV-развёртка", "нет UV-развёртки")
        if me.uv_layers:
            uvs = [d.uv for d in me.uv_layers[0].data]
            inside = all(-1e-4 <= u.x <= 1 + 1e-4 and -1e-4 <= u.y <= 1 + 1e-4 for u in uvs)
            rep.check(inside, "UV внутри атласа 0…1", "UV выходят за 0…1 (атлас не тайлится)", "warn")
        mw = ob.matrix_world
        identity = all(abs(mw[i][j] - (1.0 if i == j else 0.0)) < 1e-6 for i in range(4) for j in range(4))
        rep.check(identity, "трансформации сброшены",
                  f"трансформации не сброшены: точка привязки ({mw.translation.x:.2f}, "
                  f"{mw.translation.y:.2f}, {mw.translation.z:.2f}) (т2 п. 3.14)", "warn")
        verts = [mw @ v.co for v in me.vertices]
        cx = sum(v.x for v in verts) / len(verts)
        cy = sum(v.y for v in verts) / len(verts)
        rep.check(abs(cx) < 60000 and abs(cy) < 60000 and (abs(cx) > 100 or abs(cy) > 100),
                  f"геометрия в МСК (центр {cx:.1f}, {cy:.1f})",
                  f"центр геометрии ({cx:.1f}, {cy:.1f}) не похож на МСК (т2 п. 8.2)")
        if zero_abs is not None:
            zmin = min(v.z for v in verts)
            rep.check(zmin > zero_abs - 30, f"абсолютные высоты: низ {zmin:.2f}",
                      f"низ модели {zmin:.2f} при нуле здания {zero_abs} — высоты не абсолютные (т2 п. 8.2)")
        for mat in me.materials:
            mname = mat.name if mat else "нет"
            ok = mat is not None and (re.match(r"^M_[A-Za-z0-9_]+_\d{3}_Main_\d+$", mname)
                                      or re.match(r"^M_Glass_0[1-7]$", mname))
            rep.check(bool(ok), f"материал {mname}",
                      f"материал {mname}: нужно M_Address_номер_Main_N или M_Glass_0N (т2 п. 10.3.5)")

    rep.section("Итого по геометрии")
    rep.check(total <= limit, f"треугольников {total} (ориентир проекта {limit})",
              f"треугольников {total}, больше ориентира проекта {limit}", "warn")
    rep.check(total <= MAX_TRIS, f"не больше {MAX_TRIS} в файле",
              f"треугольников {total}, предел {MAX_TRIS} в файле ОКС (т2 п. 3.6)")

    rep.section("Текстуры")
    images = [im for im in bpy.data.images if im.type == "IMAGE"]
    rep.check(len(images) > 0, f"текстурных карт: {len(images)}", "нет текстур: карта цвета _d обязательна (т2 п. 5.6)")
    pattern = re.compile(rf"^T_[A-Za-z0-9_]+_\d{{3}}_(Main)_({'|'.join(TEXTURE_SUFFIXES)})_(\d+)(\.png)?$")
    kinds = set()
    for im in images:
        name = im.name
        m = pattern.match(name)
        rep.check(m is not None, f"имя {name}",
                  f"имя {name}: нужно T_Address_номер_Main_<d|n|o|m|r>_N (т2 п. 10.3.4)")
        if m:
            kinds.add(m.group(2))
        w, h = im.size
        if w == h == STUB_SIZE:
            rep.check(True, f"{name}: заглушка {w}×{h}", "")
        else:
            rep.check(w == h and w in TEXTURE_SIZES, f"{name}: {w}×{h}",
                      f"{name}: {w}×{h}, допустимо 256, 512, 1024, 2048 (т2 п. 5.2)")
        if im.packed_file:
            mb = im.packed_file.size / 2 ** 20
            rep.check(mb <= MAX_TEXTURE_MB, f"{name}: {mb:.2f} МБ",
                      f"{name}: {mb:.2f} МБ, больше {MAX_TEXTURE_MB} МБ (т2 п. 5.3)")
        rep.check(im.channels <= 3 or not im.alpha_mode != "NONE" or im.depth <= 24,
                  f"{name}: без альфа-канала", f"{name}: есть альфа-канал (т2 п. 5.5)")
    rep.check("d" in kinds, "есть карта цвета _d", "нет карты цвета _d (т2 п. 5.6)")
    rep.check(("r" in kinds) == ("m" in kinds), "шероховатость и металличность — парой",
              "карты _r и _m используются только парой (т2 п. 5.6)")
    rep.check("o" not in kinds, "нет карты прозрачности (полупрозрачных деталей нет)",
              "есть карта прозрачности — допустима только при прозрачных элементах (т2 п. 5.8)", "warn")


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    fbx_path, config_path = Path(args[0]), Path(args[1])
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    zero_abs = cfg.get("manual", {}).get("zero_abs")

    rep = Report()
    rep.lines.append("Проверка НПМ по распоряжению ДИТ и ДГП от 18.08.2026 № ДГП-Р-56/26/64-16-473/26, прил. 1")
    check_file(rep, fbx_path, cfg)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(fbx_path))
    check_scene(rep, cfg, zero_abs)

    verdict = "НЕ ПРОЙДЕНА" if rep.errors else "ПРОЙДЕНА"
    rep.lines.append("")
    rep.lines.append(f"Проверка {verdict}: ошибок {rep.errors}, предупреждений {rep.warnings}")
    text = "\n".join(rep.lines)
    print(text)
    (fbx_path.parent / "validation.txt").write_text(text, encoding="utf-8")
    if rep.errors:
        sys.exit(1)


main()

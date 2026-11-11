"""Проверка готовой НПМ по требованиям к загрузке.

Запуск через Blender (обычно его делает run_project.py):
    blender --background --python src/validate_npm.py -- data/project_01/output/файл.fbx projects/project_01.json

Требования взяты из методических рекомендаций по 3D-моделям для АГР
(пересказ: https://smway.ru/metodicheskie-rekomendacii-agr-3d-modeli/) и из
устройства принятой НПМ этого проекта. Лимиты — в настройках проекта.

Итог пишется в output/validation.txt. Ошибки — модель не примут,
предупреждения — стоит посмотреть. При ошибках код выхода 1.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # чтобы найти fbx_info.py

import bpy  # noqa: E402

from fbx_info import fbx_info  # noqa: E402

GEOMETRY_SUFFIXES = ("_Main", "_Ground", "_Flora", "_Glass")
TEXTURE_SUFFIXES = ("d", "n", "o", "m", "r")
TEXTURE_SIZES = {128, 256, 512, 1024, 2048}
LATIN = re.compile(r"^[A-Za-z0-9_]+$")
RECOMMENDED_TRIS = 15000    # рекомендуемый предел на ОКС
MAX_TRIS = 150000           # предельно допустимо на ОКС в одном файле


class Report:
    def __init__(self):
        self.lines, self.errors, self.warnings = [], 0, 0

    def ok(self, text):
        self.lines.append(f"[ OK ] {text}")

    def warn(self, text):
        self.warnings += 1
        self.lines.append(f"[ ?? ] {text}")

    def error(self, text):
        self.errors += 1
        self.lines.append(f"[ !! ] {text}")

    def check(self, condition, good, bad, level="error"):
        if condition:
            self.ok(good)
        elif level == "warn":
            self.warn(bad)
        else:
            self.error(bad)


def check_file(rep, path, cfg):
    stem = path.stem
    rep.check(LATIN.match(stem) is not None, f"имя файла латиницей: {path.name}",
              f"в имени файла недопустимые символы: {path.name}")
    expected = cfg.get("npm", {}).get("file_name")
    if expected:
        rep.check(stem == expected, "имя файла совпадает с настройками",
                  f"имя файла {stem}, а в настройках {expected}", "warn")
    info = fbx_info(path)
    rep.check(info["binary"], "FBX двоичный", "FBX не двоичный (нужен binary)")
    if not info["binary"]:
        return
    rep.check(info["version"] == 7400, "версия FBX 7.4 (7400)",
              f"версия FBX {info['version']}, нужна 7400")
    s = info["settings"]
    rep.check(s.get("UnitScaleFactor") == 100.0, "единицы — метры (UnitScaleFactor 100)",
              f"UnitScaleFactor {s.get('UnitScaleFactor')}, ожидается 100 (метры)")
    rep.check(s.get("UpAxis") == 2 and s.get("UpAxisSign") == 1, "ось вверх — Z",
              f"ось вверх: UpAxis {s.get('UpAxis')}, знак {s.get('UpAxisSign')}")


def check_scene(rep, cfg, zero_abs):
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    rep.check(len(meshes) >= 1, f"объектов-мешей: {len(meshes)}", "в файле нет мешей")
    total = 0
    limit = cfg.get("npm", {}).get("max_triangles", RECOMMENDED_TRIS)
    for ob in meshes:
        name = ob.name
        rep.check(name.startswith("SM_") and name.endswith(GEOMETRY_SUFFIXES) and LATIN.match(name),
                  f"имя объекта {name}",
                  f"имя объекта {name}: нужно SM_<имя> с окончанием {'/'.join(GEOMETRY_SUFFIXES)}")
        me = ob.data
        tris = sum(len(p.vertices) - 2 for p in me.polygons)
        total += tris
        ngons = sum(1 for p in me.polygons if len(p.vertices) != 3)
        rep.check(ngons == 0, f"{name}: все грани — треугольники",
                  f"{name}: {ngons} граней не треугольники", "warn")
        degenerate = sum(1 for p in me.polygons if p.area < 1e-8)
        rep.check(degenerate == 0, f"{name}: нет вырожденных граней",
                  f"{name}: вырожденных граней {degenerate}", "warn")
        keys = [tuple(sorted(p.vertices)) for p in me.polygons]
        dup = len(keys) - len(set(keys))
        rep.check(dup == 0, f"{name}: нет дублирующихся граней",
                  f"{name}: дублирующихся граней {dup}")
        rep.check(len(me.uv_layers) >= 1, f"{name}: есть UV-развёртка", f"{name}: нет UV")
        if me.uv_layers:
            uvs = [d.uv for d in me.uv_layers[0].data]
            inside = all(-1e-4 <= u.x <= 1 + 1e-4 and -1e-4 <= u.y <= 1 + 1e-4 for u in uvs)
            rep.check(inside, f"{name}: UV внутри атласа 0…1",
                      f"{name}: UV выходят за 0…1 (атлас не тайлится)", "warn")
        loc = ob.matrix_world.translation
        in_msk = abs(loc.x) < 60000 and abs(loc.y) < 60000
        rep.check(in_msk, f"{name}: точка привязки в МСК ({loc.x:.2f}, {loc.y:.2f})",
                  f"{name}: точка привязки ({loc.x:.1f}, {loc.y:.1f}) не похожа на МСК")
        if zero_abs is not None:
            rep.check(abs(loc.z - zero_abs) < 0.01, f"{name}: высота привязки {loc.z:.2f} = нулю здания",
                      f"{name}: высота привязки {loc.z:.2f}, а ноль здания {zero_abs}", "warn")
        for mat in me.materials:
            rep.check(mat is not None and mat.name.startswith("M_") and LATIN.match(mat.name),
                      f"материал {mat.name if mat else None}",
                      f"материал {mat.name if mat else None}: нужно M_<имя>, латиницей")

    rep.check(total <= limit, f"треугольников всего {total} (рекомендуется до {limit})",
              f"треугольников {total} — больше рекомендованных {limit}", "warn")
    rep.check(total <= MAX_TRIS, f"не больше предельных {MAX_TRIS}",
              f"треугольников {total} — больше предельных {MAX_TRIS}")

    images = [im for im in bpy.data.images if im.type == "IMAGE"]
    rep.check(len(images) > 0, f"текстур: {len(images)}", "текстур нет", "warn")
    pattern = re.compile(rf"^T_[A-Za-z0-9_]+_({'|'.join(TEXTURE_SUFFIXES)})(_\d+)?(\.png)?$")
    for im in images:
        name = im.name
        rep.check(pattern.match(name) is not None, f"имя текстуры {name}",
                  f"имя текстуры {name}: нужно T_<имя>_<d|n|o|m|r>_N")
        w, h = im.size
        rep.check(w == h and w in TEXTURE_SIZES, f"{name}: {w}×{h}",
                  f"{name}: размер {w}×{h}, допустимо 128…2048, квадрат")
        fmt = (im.file_format or "").upper()
        rep.check(fmt == "PNG" or name.lower().endswith(".png") or im.filepath.lower().endswith(".png"),
                  f"{name}: PNG", f"{name}: формат {fmt}, нужен PNG")


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    fbx_path, config_path = Path(args[0]), Path(args[1])
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    zero_abs = cfg.get("manual", {}).get("zero_abs")

    rep = Report()
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

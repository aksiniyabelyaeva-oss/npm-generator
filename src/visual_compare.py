"""Метрика похожести: наша НПМ против сданной, по картинкам с четырёх сторон.

Запуск через Blender (обычно его делает run_project.py):
    blender --background --python src/visual_compare.py -- data/project_01/building.json projects/project_01.json

Для каждой стороны (SE, SW, NE, NW — камера смотрит на угол здания) обе модели
рендерятся одной камерой. Считаются:
- силуэт (IoU) — насколько совпадают очертания: пересечение / объединение;
- цвет — доля мест, где цвет почти тот же: картинки уменьшаются в 4 раза
  (чтобы мелкий сдвиг не считался ошибкой), и там, где видны обе модели,
  считается доля пикселей с разницей меньше COLOR_TOL по каждому каналу.

Результат: data/<проект>/plots/visual_<сторона>.png (сверху сданная, снизу наша)
и data/<проект>/visual_compare.json + строка-итог в консоли.
"""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

VIEWS = {"SE": (0, -1), "SW": (-1, 0), "NE": (1, 0), "NW": (0, 1)}
SIZE = (800, 480)
DISTANCE = 42.0
DOWN = 4               # во сколько раз уменьшать картинки перед сравнением цвета
COLOR_TOL = 0.05       # разница цвета, которую считаем «тот же цвет» (из 1.0)


def downsample(x, k):
    h, w = x.shape[0] // k * k, x.shape[1] // k * k
    return x[:h, :w].reshape(h // k, k, w // k, k, -1).mean(axis=(1, 3))


def import_fbx(path, name):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    objs = [o for o in bpy.data.objects if o not in before]
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    for o in objs:
        for c in o.users_collection:
            c.objects.unlink(o)
        coll.objects.link(o)
    return coll


def render(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path), check_existing=False)
    w, h = img.size
    arr = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(arr)
    bpy.data.images.remove(img)
    return arr.reshape(h, w, 4)[::-1]


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    building_path, config_path = Path(args[0]), Path(args[1])
    data = json.loads(building_path.read_text(encoding="utf-8"))
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    data_dir = building_path.parent
    ours = data_dir / "output" / f"{cfg['npm']['file_name']}.fbx"
    refs = sorted((data_dir / "reference").glob("*.fbx"))
    if not refs or not ours.exists():
        print("Нет сданной модели или нашей НПМ — сравнивать нечего")
        return
    plots = data_dir / "plots"
    plots.mkdir(exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "FLAT"            # без теней: сравниваем цвет поверхностей
    scene.display.shading.color_type = "TEXTURE"
    scene.render.film_transparent = True
    scene.render.resolution_x, scene.render.resolution_y = SIZE
    colls = {"ref": import_fbx(refs[0], "ref"), "ours": import_fbx(ours, "ours")}

    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.clip_end = 2000
    ox, oy = data["origin_msk"]
    target = Vector((ox, oy, data["zero_abs"] + 3.5))
    a = math.radians(data["rotation_deg"])

    results = {}
    tmp = plots / "_render.png"
    for side, (lx, ly) in VIEWS.items():
        d = Vector((lx * math.cos(a) - ly * math.sin(a), lx * math.sin(a) + ly * math.cos(a), 0))
        corner = Vector((-d.y, d.x, 0)) * 0.6
        cam.location = target + (d + corner).normalized() * DISTANCE + Vector((0, 0, 9))
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        shots = {}
        for name, coll in colls.items():
            for other, c in colls.items():
                c.hide_render = other != name
            shots[name] = render(tmp)
        ref_mask, our_mask = shots["ref"][..., 3] > 0.5, shots["ours"][..., 3] > 0.5
        both = ref_mask & our_mask
        iou = both.sum() / max((ref_mask | our_mask).sum(), 1)
        ref_small, our_small = downsample(shots["ref"], DOWN), downsample(shots["ours"], DOWN)
        visible = (ref_small[..., 3] > 0.99) & (our_small[..., 3] > 0.99)
        diff = np.abs(ref_small[..., :3] - our_small[..., :3]).max(axis=2)
        color = (diff[visible] < COLOR_TOL).mean() if visible.any() else 0.0
        results[side] = {"silhouette_iou": round(float(iou), 3), "color": round(float(color), 3)}
        # картинка: сверху сданная, снизу наша
        pair = np.concatenate([shots["ref"], shots["ours"]], axis=0)
        pair[..., :3] = pair[..., :3] * pair[..., 3:] + 0.12 * (1 - pair[..., 3:])
        pair[..., 3] = 1
        h, w = pair.shape[:2]
        img = bpy.data.images.new(f"visual_{side}", w, h, alpha=False)
        img.pixels.foreach_set(pair[::-1].astype(np.float32).ravel())
        img.filepath_raw = str(plots / f"visual_{side}.png")
        img.file_format = "PNG"
        img.save()
    tmp.unlink(missing_ok=True)

    mean_iou = sum(r["silhouette_iou"] for r in results.values()) / len(results)
    mean_color = sum(r["color"] for r in results.values()) / len(results)
    summary = {"views": results, "silhouette_iou": round(mean_iou, 3), "color": round(mean_color, 3)}
    (data_dir / "visual_compare.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    for side, r in results.items():
        print(f"  {side}: силуэт {r['silhouette_iou']:.0%}   цвет {r['color']:.0%}")
    print(f"Похожесть на сданную: силуэт {mean_iou:.0%}, цвет {mean_color:.0%}  "
          f"(картинки: plots/visual_*.png)")


main()

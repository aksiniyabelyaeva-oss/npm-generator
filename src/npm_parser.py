"""Разбирает сданную НПМ (FBX) в эталон reference.json.

Запуск через Blender:
blender --background --python src/npm_parser.py -- data/project_01/reference/файл.fbx
"""
import json
import sys
from collections import Counter
from pathlib import Path

import bpy  # bpy первым: после него становится доступен bmesh
import bmesh


def import_fbx(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(path))
    return [ob for ob in bpy.context.scene.objects if ob.type == "MESH"]


def levels(ob, base_z, top=8):
    """Высоты горизонтальных площадок с наибольшей площадью — карнизы, переломы кровли."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.transform(ob.matrix_world)
    areas = Counter()
    for f in bm.faces:
        if abs(f.normal.z) > 0.99:
            areas[round(f.calc_center_median().z - base_z, 1)] += f.calc_area()
    bm.free()
    best = sorted(areas.items(), key=lambda kv: -kv[1])[:top]
    return [{"z": z, "area_m2": round(a, 1)} for z, a in sorted(best)]


def describe(ob):
    me = ob.data
    me.calc_loop_triangles()
    pts = [ob.matrix_world @ v.co for v in me.vertices]
    base = ob.location.z
    images = sorted({n.image.name for m in me.materials if m and m.node_tree
                     for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image})
    return {
        "name": ob.name,
        "triangles": len(me.loop_triangles),
        "pivot_msk": [round(v, 3) for v in ob.location],
        "bbox_msk": {
            "x": [round(min(p.x for p in pts), 2), round(max(p.x for p in pts), 2)],
            "y": [round(min(p.y for p in pts), 2), round(max(p.y for p in pts), 2)],
        },
        "z_bottom": round(min(p.z for p in pts) - base, 2),
        "z_top": round(max(p.z for p in pts) - base, 2),
        "levels": levels(ob, base),
        "materials": [m.name for m in me.materials if m],
        "textures": images,
    }


if __name__ == "__main__":
    fbx = Path(sys.argv[sys.argv.index("--") + 1])
    meshes = import_fbx(fbx)
    reference = {
        "file": fbx.name,
        "zero_abs": round(meshes[0].location.z, 3),
        "objects": [describe(ob) for ob in meshes],
    }
    out = fbx.parent.parent / "reference.json"
    out.write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Эталон сохранён: {out}")

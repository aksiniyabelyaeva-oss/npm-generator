import json
import math
import sys
from pathlib import Path

import bpy  # bpy первым: после него становится доступен bmesh
import bmesh

# Путь для запуска из интерфейса Blender. При запуске из консоли
# (run_project.py) путь передаётся после «--» и этот не используется.
JSON_PATH = r"C:\Users\user\Desktop\npm-generator\data\project_01\building.json"
FLOOR2_Z = 3.42        # высота пола 2-го этажа: здесь меняется цвет стены
ROOF_LOWER_RUN = 0.8   # насколько нижний крутой скат мансарды уходит внутрь, м
PLACE_IN_MSK = True    # поставить модель в МСК, чтобы сравнить со сданной НПМ
GLASS_OUT = 0.05       # насколько стекло выступает из стены, м (чтобы было видно)
OPENING_COLORS = {"window": "#2E3A42", "dormer": "#2E3A42", "door": "#4A3A30"}
SIDE_AXES = {"SE": (0, -1), "NW": (0, 1), "SW": (1, -1), "NE": (1, 1)}


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def make_material(name, hex_color):
    h = hex_color.lstrip("#")
    rgb = [srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)]
    rgba = (*rgb, 1.0)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    if mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                node.inputs[0].default_value = rgba
    mat.diffuse_color = rgba
    return mat


def clean_collection(name):
    coll = bpy.data.collections.get(name)
    if coll:
        for ob in list(coll.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
    else:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def add_object(name, bm, mat, coll, root):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    me.materials.append(mat)
    coll.objects.link(ob)
    ob.parent = root
    return ob


def prism(name, outline, z0, z1, mat, coll, root):
    """Контур, вытянутый от высоты z0 до z1."""
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y, z0)) for x, y in outline])
    result = bmesh.ops.extrude_face_region(bm, geom=[face])
    top = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, z1 - z0), verts=top)
    return add_object(name, bm, mat, coll, root)


def box(name, lo, hi, mat, coll, root):
    """Прямоугольный параллелепипед между точками lo и hi."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    bm = bmesh.new()
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    v = [bm.verts.new(p) for p in corners]
    for f in [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
        bm.faces.new([v[i] for i in f])
    return add_object(name, bm, mat, coll, root)


def mansard_roof(name, outline, roof, z_eave, mat, coll, root):
    """Мансардная вальмовая крыша по габаритному прямоугольнику контура."""
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    d = ROOF_LOWER_RUN
    bx0, bx1, by0, by1 = x0 + d, x1 - d, y0 + d, y1 - d
    half = (by1 - by0) / 2
    cy = (by0 + by1) / 2
    zb, zr = roof["z_break"], roof["z_ridge"]

    bm = bmesh.new()
    v = bm.verts.new
    e = [v((x0, y0, z_eave)), v((x1, y0, z_eave)), v((x1, y1, z_eave)), v((x0, y1, z_eave))]
    b = [v((bx0, by0, zb)), v((bx1, by0, zb)), v((bx1, by1, zb)), v((bx0, by1, zb))]
    r0, r1 = v((bx0 + half, cy, zr)), v((bx1 - half, cy, zr))
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new([e[i], e[j], b[j], b[i]])
    bm.faces.new([b[0], b[1], r1, r0])
    bm.faces.new([b[2], b[3], r0, r1])
    bm.faces.new([b[3], b[0], r0])
    bm.faces.new([b[1], b[2], r1])
    return add_object(name, bm, mat, coll, root)


def opening_box(o, mat, coll, root):
    """Тонкая пластина стекла или двери на поверхности стены."""
    axis, sign = SIDE_AXES[o["side"]]
    k = 1 - axis
    lo, hi = [0.0, 0.0, o["z0"]], [0.0, 0.0, o["z1"]]
    lo[axis], hi[axis] = o["u0"], o["u1"]
    a, b = o["face"], o["face"] + sign * GLASS_OUT
    lo[k], hi[k] = min(a, b), max(a, b)
    return box(f"{o['facade']}_{o['ref_id']}_{o['type']}", lo, hi, mat, coll, root)


def make_root(data, coll):
    """Пустышка-родитель: через неё вся модель ставится в МСК."""
    root = bpy.data.objects.new("Preview_Root", None)
    coll.objects.link(root)
    if PLACE_IN_MSK:
        ox, oy = data["origin_msk"]
        root.location = (ox, oy, data.get("zero_abs", 0.0))
        root.rotation_euler = (0, 0, math.radians(data["rotation_deg"]))
    return root


def build(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    coll = clean_collection("Preview")
    root = make_root(data, coll)
    main = data["parts"][0]
    colors = main["colors"]
    plinth = make_material("Plinth", colors["plinth"])
    outline = main["outline"]

    prism("P1_Plinth", outline, main["z_bottom"], 0.0, plinth, coll, root)
    prism("P1_Floor1", outline, 0.0, FLOOR2_Z,
          make_material("Wall1", colors["wall_floor1"]), coll, root)
    prism("P1_Floor2", outline, FLOOR2_Z, main["z_eave"],
          make_material("Wall2", colors["wall_floor2"]), coll, root)
    mansard_roof("P1_Roof", outline, main["roof"], main["z_eave"],
                 make_material("Roof", colors["roof"]), coll, root)

    annex = make_material("Annex", "#8A756B")
    for part in data["parts"][1:]:
        mat = annex if part["kind"] == "annex" else plinth
        prism(f"{part['id']}_{part['kind']}", part["outline"],
              part["z_bottom"], part["z_eave"], mat, coll, root)

    for o in data.get("openings", []):
        mat = make_material(f"Opening_{o['type']}", OPENING_COLORS[o["type"]])
        opening_box(o, mat, coll, root)
    print(f"Предпросмотр построен, проёмов: {len(data.get('openings', []))}")



def json_path():
    """Путь к building.json: после «--» в командной строке или JSON_PATH."""
    if "--" in sys.argv and len(sys.argv) > sys.argv.index("--") + 1:
        return sys.argv[sys.argv.index("--") + 1]
    return JSON_PATH


def look_at_model(data):
    """Переносит 3D-вид к модели и увеличивает дальность видимости."""
    if PLACE_IN_MSK:
        ox, oy = data["origin_msk"]
        target = (ox, oy, data.get("zero_abs", 0.0) + 4.0)
    else:
        target = (0.0, 0.0, 4.0)
    areas = [a for screen in bpy.data.screens for a in screen.areas if a.type == "VIEW_3D"]
    for area in areas:
        space = area.spaces.active
        space.clip_start = 0.1
        space.clip_end = 100000.0
        space.region_3d.view_location = target
        space.region_3d.view_distance = 60.0
        area.tag_redraw()


def save_blend(path):
    """Без интерфейса: добавляет сданную модель и сохраняет preview.blend рядом с данными."""
    data_dir = Path(path).parent
    for ob in [o for o in bpy.data.objects if o.type == "MESH" and o.name == "Cube"]:
        bpy.data.objects.remove(ob, do_unlink=True)   # кубик из стартовой сцены
    fbx = sorted((data_dir / "reference").glob("*.fbx"))
    if fbx:
        bpy.ops.import_scene.fbx(filepath=str(fbx[0]))
        print(f"Эталон добавлен: {fbx[0].name}")
    out = data_dir / "preview.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print(f"Сохранено: {out}")


path = json_path()
build(path)
with open(path, encoding="utf-8") as f:
    look_at_model(json.load(f))
if bpy.app.background:
    save_blend(path)

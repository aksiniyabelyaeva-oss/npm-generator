import json

import bmesh
import bpy

# Путь к building.json на твоём компьютере
JSON_PATH = r"C:\Users\user\Desktop\npm-generator\data\project_01\building.json"
FLOOR2_Z = 3.42       # высота пола 2-го этажа: здесь меняется цвет стены
ROOF_LOWER_RUN = 0.8  # насколько нижний крутой скат мансарды уходит внутрь, м


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def make_material(name, hex_color):
    h = hex_color.lstrip("#")
    rgb = [srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)]
    rgba = (*rgb, 1.0)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            node.inputs[0].default_value = rgba   # вход 0 — базовый цвет
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


def add_object(name, bm, mat, coll):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    me.materials.append(mat)
    coll.objects.link(ob)
    return ob


def prism(name, outline, z0, z1, mat, coll):
    """Контур, вытянутый от высоты z0 до z1."""
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y, z0)) for x, y in outline])
    result = bmesh.ops.extrude_face_region(bm, geom=[face])
    top = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, z1 - z0), verts=top)
    return add_object(name, bm, mat, coll)


def mansard_roof(name, outline, roof, z_eave, mat, coll):
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

    for i in range(4):                       # нижние крутые скаты
        j = (i + 1) % 4
        bm.faces.new([e[i], e[j], b[j], b[i]])
    bm.faces.new([b[0], b[1], r1, r0])       # верхние длинные скаты
    bm.faces.new([b[2], b[3], r0, r1])
    bm.faces.new([b[3], b[0], r0])           # вальмы на торцах
    bm.faces.new([b[1], b[2], r1])
    return add_object(name, bm, mat, coll)


def build(path):
    data = json.load(open(path, encoding="utf-8"))
    coll = clean_collection("Preview")
    for part in data["parts"]:
        outline, colors = part["outline"], part["colors"]
        prism(f"{part['id']}_Plinth", outline, part["z_bottom"], 0.0,
              make_material("Plinth", colors["plinth"]), coll)
        prism(f"{part['id']}_Floor1", outline, 0.0, FLOOR2_Z,
              make_material("Wall1", colors["wall_floor1"]), coll)
        prism(f"{part['id']}_Floor2", outline, FLOOR2_Z, part["z_eave"],
              make_material("Wall2", colors["wall_floor2"]), coll)
        mansard_roof(f"{part['id']}_Roof", outline, part["roof"], part["z_eave"],
                     make_material("Roof", colors["roof"]), coll)
    print("Предпросмотр построен")


build(JSON_PATH)
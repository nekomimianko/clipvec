import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "blender"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import io_clip_vector as addon
src, out = sys.argv[sys.argv.index("--") + 1:][:2]
ctx = bpy.context
obj = addon.import_clip(ctx, src)
gp = obj.data
# 1) edit: double radius of first stroke in bottom layer, recolor 2nd stroke via material
d = gp.layers[0].frames[0].drawing
s0 = d.strokes[0]
for p in s0.points:
    p.radius *= 2.0
mat = bpy.data.materials.new("Blender Yellow"); bpy.data.materials.create_gpencil_data(mat)
mat.grease_pencil.color = (1.0, 0.6, 0.0, 1.0); gp.materials.append(mat)
d.strokes[1].material_index = len(gp.materials) - 1
# 2) new layer with blender-authored strokes
gl = gp.layers.new("Blender strokes")
dd = gl.frames.new(ctx.scene.frame_current).drawing
dd.add_strokes([60, 4])
spiral = dd.strokes[0]
for i, p in enumerate(spiral.points):
    t = i / 59
    a = t * 4 * math.pi
    r = 0.05 + 0.25 * t
    p.position = (0.25 + r * math.cos(a), 0.0, 0.1 + r * math.sin(a))
    p.radius = 0.002 + 0.01 * math.sin(t * math.pi)
    p.opacity = 1.0
mat2 = bpy.data.materials.new("Blender Violet"); bpy.data.materials.create_gpencil_data(mat2)
mat2.grease_pencil.color = (0.4, 0.0, 1.0, 1.0); gp.materials.append(mat2)
spiral.material_index = len(gp.materials) - 1
dd.set_types(type="BEZIER", indices=[1])
bz = dd.strokes[1]
knots = [(-0.4, -0.3), (-0.2, -0.1), (0.0, -0.35), (0.2, -0.1)]
for n in ("handle_left", "handle_right"):
    if n not in dd.attributes: dd.attributes.new(n, "FLOAT_VECTOR", "POINT")
for i, (x, z) in enumerate(knots):
    bz.points[i].position = (x, 0, z); bz.points[i].radius = 0.006; bz.points[i].opacity = 0.7
    dd.attributes["handle_left"].data[60 + i].vector = (x - 0.05, 0, z + 0.08)
    dd.attributes["handle_right"].data[60 + i].vector = (x + 0.05, 0, z - 0.08)
bz.material_index = len(gp.materials) - 1
addon.export_clip(ctx, obj, out, base=src)
print("exported", out)

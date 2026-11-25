import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "blender"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import io_clip_vector as addon
src, out = sys.argv[sys.argv.index("--") + 1:][:2]
ctx = bpy.context
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
obj = addon.import_clip(ctx, src, ppu=1000.0, center=True)
w, h, _ = obj["csp_canvas"]
cam = bpy.data.cameras.new("cam"); cam.type = "ORTHO"; cam.ortho_scale = max(w, h) / 1000.0
co = bpy.data.objects.new("cam", cam); ctx.collection.objects.link(co)
co.location = (0, -5, 0); co.rotation_euler = (math.pi / 2, 0, 0)
sc = ctx.scene; sc.camera = co
sc.render.resolution_x, sc.render.resolution_y = int(w / 2), int(h / 2)
sc.render.engine = "BLENDER_EEVEE"
sc.world = bpy.data.worlds.new("w"); sc.world.color = (1, 1, 1)
sc.render.film_transparent = False
sc.view_settings.view_transform = "Standard"
sc.render.filepath = out
bpy.ops.render.render(write_still=True)
print("rendered", out)

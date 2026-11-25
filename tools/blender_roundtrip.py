"""Run inside Blender: import a .clip, export (update + new), compare.
blender -b --factory-startup --python tools/blender_roundtrip.py -- in.clip out_prefix"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "blender"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import io_clip_vector as addon
from clipvec.document import ClipDocument

src, prefix = sys.argv[sys.argv.index("--") + 1:][:2]
ctx = bpy.context
obj = addon.import_clip(ctx, src, ppu=1000.0, center=True)
gp = obj.data
for gl in gp.layers:
    d = gl.frames[0].drawing if len(gl.frames) else None
    print("GP layer", gl.name, "strokes", len(d.strokes) if d else 0, "types", sorted({s.curve_type for s in d.strokes}) if d else [])
addon.export_clip(ctx, obj, prefix + "_update.clip", base=src)
addon.export_clip(ctx, obj, prefix + "_new.clip", base=None)
a = ClipDocument.load(src)
for tag in ("_update", "_new"):
    b = ClipDocument.load(prefix + tag + ".clip")
    worst = {"pos": 0.0, "rad": 0.0, "opa": 0.0}; bad = []
    for la, lb in zip(a.vector_layers(), b.vector_layers()):
        for k, (sa, sb) in enumerate(zip(la.strokes, lb.strokes)):
            if len(sa.points) != len(sb.points) or (sa.flags & 0x1F1) != (sb.flags & 0x1F1) or sa.color != sb.color:
                bad.append((la.name, k, "structure", hex(sa.flags), hex(sb.flags), len(sa.points), len(sb.points), sa.color, sb.color)); continue
            ra = [sa.radius * f for f in a.stroke_width_factors(sa)]; rb = [sb.radius * f for f in b.stroke_width_factors(sb)]
            oa = [sa.opacity * f for f in a.stroke_opacity_factors(sa)]; ob = [sb.opacity * f for f in b.stroke_opacity_factors(sb)]
            worst["pos"] = max(worst["pos"], max(max(abs(p.x - q.x), abs(p.y - q.y)) for p, q in zip(sa.points, sb.points)))
            worst["rad"] = max(worst["rad"], max(abs(x - y) for x, y in zip(ra, rb)))
            worst["opa"] = max(worst["opa"], max(abs(x - y) for x, y in zip(oa, ob)))
            if any(p.corner != q.corner for p, q in zip(sa.points, sb.points)):
                bad.append((la.name, k, "corner"))
    print(tag, "layers", len(a.vector_layers()), len(b.vector_layers()), "max err", worst, "problems", bad)
# record-level check for update mode (every byte of every stroke record)
b = ClipDocument.load(prefix + "_update.clip")
ident = sum(x.encode() == y.encode() for la, lb in zip(a.vector_layers(), b.vector_layers()) for x, y in zip(la.strokes, lb.strokes))
tot = sum(len(l.strokes) for l in a.vector_layers())
print("_update byte-identical stroke records", ident, "/", tot)

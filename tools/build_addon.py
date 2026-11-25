"""Build dist/io_clip_vector.zip (Blender extension / legacy add-on) with the
clipvec package vendored inside."""
import os, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(ROOT, "dist", "io_clip_vector.zip")
os.makedirs(os.path.dirname(out), exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for base, arc in ((os.path.join(ROOT, "blender", "io_clip_vector"), "io_clip_vector"),
                      (os.path.join(ROOT, "clipvec"), "io_clip_vector/clipvec")):
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for f in fn:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(dp, f)
                z.write(full, os.path.join(arc, os.path.relpath(full, base)).replace(os.sep, "/"))
print(out)

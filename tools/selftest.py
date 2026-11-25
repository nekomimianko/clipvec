"""Offline regression checks for clipvec (no CSP / Blender needed).
python tools/selftest.py"""
import glob
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clipvec import vector  # noqa: E402
from clipvec.container import ClipFile  # noqa: E402
from clipvec.document import ClipDocument  # noqa: E402
from clipvec.interchange import apply_dict, document_to_dict  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    fails += not cond


tmp = tempfile.mkdtemp()
samples = sorted(glob.glob(os.path.join(ROOT, "samples", "*.clip")))
for f in samples:
    name = os.path.basename(f)
    raw = open(f, "rb").read()
    c = ClipFile.parse(raw)
    check(c.serialize() == raw, f"{name}: container round trip is byte identical")
    doc = ClipDocument(c)
    for layer in doc.vector_layers():
        body = c.external_body(layer.external_id) if layer.external_id else b""
        check(vector.encode(vector.decode(body or b"")) == (body or b""), f"{name}: layer {layer.layer_id} vector body codec lossless")
    # lossless JSON round trip into the same file
    data = json.loads(json.dumps(document_to_dict(doc, lossless=True)))
    doc2 = ClipDocument.load(f)
    apply_dict(doc2, data)
    out = os.path.join(tmp, name)
    doc2.save(out)
    doc3 = ClipDocument.load(out)
    same = all(a.encode() == b.encode()
               for la, lb in zip(ClipDocument.load(f).vector_layers(), doc3.vector_layers())
               for a, b in zip(la.strokes, lb.strokes))
    check(same, f"{name}: lossless JSON -> .clip keeps every stroke record")

# from-scratch document
doc = ClipDocument.new(1280, 720, 144)
lid = doc.vector_layers()[0].layer_id
bid = doc.default_brush_id()
pts = [vector.Point(100 + i * 10, 360 + 100 * math.sin(i / 10), pressure=i / 100) for i in range(101)]
doc.set_strokes(lid, [vector.Stroke(pts, color=(10, 20, 30), radius=5, brush_id=bid)])
l2 = doc.add_vector_layer("second")
doc.set_strokes(l2, [vector.Stroke([vector.Point(10, 10), vector.Point(200, 300)], flags=vector.NORMAL | vector.STRAIGHT)])
out = os.path.join(tmp, "new.clip")
doc.save(out)
d = ClipDocument.load(out)
check(d.canvas == {"width": 1280.0, "height": 720.0, "dpi": 144.0}, "new document canvas size")
ls = d.vector_layers()
check([l.name for l in ls][1:] == ["second"] and len(ls) == 2, "new layer created above the first")
check(abs(ls[0].strokes[0].points[50].pressure - 0.5) < 1e-6, "pressure stored")
check(abs(d.stroke_width_factors(ls[0].strokes[0])[50] - 0.5) < 1e-6, "default brush width is linear in pressure")
mx = {r[0]: r[1] for r in d.db.q("select TableName, MaxIndex from ElemScheme")}
for t in ("Layer", "Offscreen", "MipmapInfo", "Mipmap", "LayerThumbnail", "VectorObjectList", "BrushStyle"):
    m = d.db.q(f"select max(MainId) from {t}")[0][0] or 0
    check(mx.get(t, 0) >= m, f"ElemScheme.MaxIndex[{t}] >= max(MainId)")
vs = {r["MainId"]: r["VectorNormalStrokeIndex"] for r in d.layer_rows()}
objs = {r["LayerId"]: r["MainId"] for r in d.db.q("select * from VectorObjectList")}
check(all(vs[k] == v for k, v in objs.items()), "Layer.VectorNormalStrokeIndex points at its VectorObjectList row")
print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)

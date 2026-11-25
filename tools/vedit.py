"""Helpers for raw vector-body experiments."""
import struct, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clipvec.container import ClipFile

def strokes(body):
    off = 0; out = []
    while off < len(body):
        hs, ps, pz, dp, n = struct.unpack_from(">5I", body, off)
        out.append((off, hs, pz, n)); off += hs + n * pz
    return out

def edit(src, dst, fn):
    c = ClipFile.load(src); db = c.open_db()
    for r in db.q("select VectorData from VectorObjectList"):
        body = bytearray(c.external_body(r[0]))
        fn(body, strokes(body))
        c.set_external_body(r[0], bytes(body))
    db.close(); c.save(dst)

def setf(body, o, v): struct.pack_into(">f", body, o, v)
def setd(body, o, v): struct.pack_into(">d", body, o, v)
def geti(body, o): return struct.unpack_from(">I", body, o)[0]
def seti(body, o, v): struct.pack_into(">I", body, o, v)

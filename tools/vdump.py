"""Dump raw vector records of every vector layer in a .clip for diffing.
usage: python tools/vdump.py file.clip [--points] [--words]"""
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clipvec.container import ClipFile  # noqa


def f32(b, o):
    return struct.unpack_from(">f", b, o)[0]


def main():
    path = sys.argv[1]
    show_words = "--words" in sys.argv
    c = ClipFile.load(path)
    db = c.open_db()
    for row in db.q("select LayerId, VectorData from VectorObjectList"):
        body = c.external_body(row["VectorData"])
        name = db.q("select LayerName from Layer where MainId=?", (row["LayerId"],))[0][0]
        print(f"# layer {row['LayerId']} {name!r} body={len(body) if body else None}")
        if not body:
            continue
        off = 0
        si = 0
        while off < len(body):
            hs, ps, pz, dp, n = struct.unpack_from(">5I", body, off)
            h = body[off:off + hs]
            words = [struct.unpack_from(">I", h, i)[0] for i in range(0, hs, 4)]
            print(f"S{si} hs={hs} ps={ps} pz={pz} n={n} flags={words[5]:#x} bbox={words[6:10]} "
                  f"col={[hex(w) for w in words[10:13]]} sub={[hex(w) for w in words[13:16]]} "
                  f"op={struct.unpack_from('>d', h, 64)[0]} w68={words[17]:#x} brush={words[18]} "
                  f"tail={[hex(w) for w in words[19:]]}")
            if hs >= 84:
                print("    rad@76=%r" % (struct.unpack_from(">d", h, 76)[0],), " rad@80=%r" % (struct.unpack_from(">d", h, 80)[0],) if hs >= 88 else "")
            if show_words:
                print("    H", " ".join(f"{i}:{w:08x}" for i, w in zip(range(0, hs, 4), words)))
            for k in range(n):
                p = body[off + hs + k * pz: off + hs + (k + 1) * pz]
                x, y = struct.unpack_from(">dd", p, 0)
                fl = struct.unpack_from(">I", p, 32)[0]
                fs = " ".join(f"{i}:{f32(p, i):.4g}" for i in range(36, 80, 4))
                extra = " ".join(f"{i}:{struct.unpack_from('>I', p, i)[0]:08x}" for i in range(80, 88, 4))
                ctl = ""
                if pz > 88:
                    ctl = " ctl=" + str([round(v, 3) for v in struct.unpack_from(">%dd" % ((pz - 88) // 8), p, 88)])
                print(f"   P{k} ({x:.2f},{y:.2f}) fl={fl:x} {fs} {extra}{ctl}")
            off += hs + n * pz
            si += 1


if __name__ == "__main__":
    main()

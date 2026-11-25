"""clipvec command line.

  python -m clipvec info   FILE.clip
  python -m clipvec export FILE.clip OUT.json [--lossless]
  python -m clipvec import IN.json OUT.clip [--base BASE.clip] [--append]
  python -m clipvec svg    FILE.clip OUT.svg
"""
import argparse
import json
import sys

from .document import ClipDocument
from .interchange import apply_dict, document_to_dict
from .svg import document_to_svg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clipvec", description="CLIP STUDIO PAINT vector layer I/O")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info"); p.add_argument("file")
    p = sub.add_parser("export"); p.add_argument("file"); p.add_argument("out"); p.add_argument("--lossless", action="store_true")
    p = sub.add_parser("import"); p.add_argument("json"); p.add_argument("out")
    p.add_argument("--base", help="existing .clip to update (default: new document sized from the JSON canvas)")
    p.add_argument("--append", action="store_true", help="append strokes instead of replacing layer contents")
    p = sub.add_parser("svg"); p.add_argument("file"); p.add_argument("out")
    a = ap.parse_args(argv)

    if a.cmd == "info":
        doc = ClipDocument.load(a.file)
        c = doc.canvas
        print(f"canvas {c['width']:g}x{c['height']:g} @ {c['dpi']:g} dpi")
        for l in doc.vector_layers():
            npts = sum(len(s.points) for s in l.strokes)
            print(f"  vector layer {l.layer_id:4d} {l.name!r}: {len(l.strokes)} strokes, {npts} points")
        brushes, _ = doc.brushes()
        for bid, b in brushes.items():
            print(f"  brush style {bid}: size effector {b.size}")
    elif a.cmd == "export":
        doc = ClipDocument.load(a.file)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(document_to_dict(doc, lossless=a.lossless), f, ensure_ascii=False, indent=1)
    elif a.cmd == "import":
        with open(a.json, encoding="utf-8") as f:
            data = json.load(f)
        if a.base:
            doc = ClipDocument.load(a.base)
        else:
            c = data.get("canvas", {})
            doc = ClipDocument.new(int(c.get("width", 1000)), int(c.get("height", 1000)), float(c.get("dpi", 72)))
            # the template's own empty layer takes the first imported layer
            first = doc.vector_layers()[0]
            if data.get("layers"):
                data["layers"][0] = dict(data["layers"][0], id=first.layer_id)
        apply_dict(doc, data, replace=not a.append)
        doc.save(a.out)
    elif a.cmd == "svg":
        doc = ClipDocument.load(a.file)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(document_to_svg(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())

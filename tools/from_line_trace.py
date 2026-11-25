#!/usr/bin/env python3
"""Convert line-trace-strokes JSON (x, y, r per point) to clipvec
interchange JSON, for `python -m clipvec import` into a real .clip.

Mapping:
- CSP drawn radius = stroke.radius x brush size effector x per-point
  width factor. Pressure is fixed at 1.0 (where the size effector is
  neutral), the stroke radius is the stroke's maximum r, and each
  point's width factor is r_i / max_r -- so the drawn radius equals
  the traced r at every point, with the taper kept as data.
- Curve type defaults to "spline" (CSP's uniform Catmull-Rom through
  the points); points where the direction changes by more than
  --corner-deg are flagged as corners so tips and hooks stay sharp.
  --curve straight keeps the exact polyline instead.
- Coordinates: the line-trace JSON uses pixel-CENTER coordinates,
  CSP canvas coordinates are corner-origin, hence the default
  --shift 0.5 (same half-pixel shift as the SVG export).

Usage:
  python tools/from_line_trace.py strokes.json -o strokes_clipvec.json
  python -m clipvec import strokes_clipvec.json out.clip
"""
import argparse, json, math
from pathlib import Path


def corner_flags(pts, threshold_deg):
    n = len(pts)
    flags = [False] * n
    for i in range(1, n - 1):
        v1 = (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        v2 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        a1 = math.atan2(v1[1], v1[0])
        a2 = math.atan2(v2[1], v2[0])
        da = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
        if math.degrees(da) > threshold_deg:
            flags[i] = True
    return flags


def convert(d, curve, shift, radius_offset, corner_deg, dpi):
    strokes = []
    for s in d["strokes"]:
        pts = s["points"]
        if not pts:
            continue
        rs = [max(0.2, p["r"] + radius_offset) for p in pts]
        r_max = max(rs)
        xy = [(p["x"], p["y"]) for p in pts]
        corners = corner_flags(xy, corner_deg) if curve == "spline" else [False] * len(pts)
        out_pts = []
        for (x, y), r, c in zip(xy, rs, corners):
            P = {"x": x + shift, "y": y + shift, "pressure": 1.0,
                 "width": r / r_max}
            if c:
                P["corner"] = True
            out_pts.append(P)
        strokes.append({"curve": curve, "color": [0, 0, 0],
                        "radius": r_max, "points": out_pts})
    return {"format": "clipvec", "version": 1,
            "canvas": {"width": d["image"]["width"],
                       "height": d["image"]["height"], "dpi": dpi},
            "layers": [{"name": "line-trace", "strokes": strokes}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strokes")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--curve", choices=["spline", "straight"], default="spline")
    ap.add_argument("--shift", type=float, default=0.5)
    ap.add_argument("--radius-offset", type=float, default=0.0)
    ap.add_argument("--corner-deg", type=float, default=65.0)
    ap.add_argument("--dpi", type=float, default=72.0)
    a = ap.parse_args()
    d = json.loads(Path(a.strokes).read_text())
    out = convert(d, a.curve, a.shift, a.radius_offset, a.corner_deg, a.dpi)
    Path(a.output).write_text(json.dumps(out))
    n = sum(len(s["points"]) for s in out["layers"][0]["strokes"])
    print(f"{a.output}: {len(out['layers'][0]['strokes'])} strokes, {n} points, curve={a.curve}")


if __name__ == "__main__":
    main()

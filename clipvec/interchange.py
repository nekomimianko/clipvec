"""App-neutral JSON interchange ("clipvec" format, version 1).

{
  "format": "clipvec", "version": 1,
  "canvas": {"width": 1000, "height": 1000, "dpi": 72},
  "layers": [                                   # bottom-most first
    {"id": 4, "name": "...", "visible": true, "opacity": 1.0,
     "strokes": [
       {"curve": "spline|straight|quadratic|cubic", "closed": false,
        "kind": "normal|ruler|frame", "flags": 8321,
        "color": [r, g, b], "sub_color": [r, g, b],   # 0..255 sRGB
        "opacity": 1.0,                               # stroke opacity
        "radius": 5.67,                               # brush radius, canvas px
        "brush_id": 1, "timestamp": 3752160547,   # stroke seed word
        "points": [
          {"x": .., "y": ..,                          # canvas px, y down
           "pressure": .., "tilt": .., "velocity": .., "random": ..,
           "width": 1.0, "opacity": 1.0,              # per-point factors
           "corner": false, "controls": [[x, y], ...],
           "render_radius": ..,                       # derived, px (read only)
           "render_opacity": ..}]                     # derived 0..1 (read only)
       }]}],
  "brushes": {"1": {"size": {...}, "opacity": {...}}}
}
Only x/y are required when importing; everything else has defaults. The
derived render_* values are what CSP draws (radius * brush size effector *
width factor); they are handy for apps that have no pressure concept.
"""
from __future__ import annotations

import base64
import dataclasses

from . import vector
from .brush import Effector
from .document import ClipDocument

CURVES = {"straight": vector.STRAIGHT, "quadratic": vector.QUADRATIC, "cubic": vector.CUBIC, "spline": vector.SPLINE}
KINDS = {"normal": vector.NORMAL, "ruler": vector.RULER, "frame": vector.FRAME}


def document_to_dict(doc: ClipDocument, lossless: bool = False) -> dict:
    brushes, graphs = doc.brushes()
    out = {"format": "clipvec", "version": 1, "canvas": doc.canvas, "layers": [], "brushes": {}}
    for bid, b in brushes.items():
        out["brushes"][str(bid)] = {"size": dataclasses.asdict(b.size), "opacity": dataclasses.asdict(b.opacity),
                                    "graphs": {str(k): graphs.get(k) for k in {b.size.pressure_graph, b.size.tilt_graph} if k}}
    for layer in doc.vector_layers():
        L = {"id": layer.layer_id, "name": layer.name, "visible": layer.visible, "opacity": layer.opacity,
             "parent": layer.parent, "strokes": []}
        for s in layer.strokes:
            wf = doc.stroke_width_factors(s)
            of = doc.stroke_opacity_factors(s)
            S = {"curve": s.curve, "closed": s.closed, "kind": s.kind, "flags": s.flags,
                 "color": list(s.color), "sub_color": list(s.sub_color), "opacity": s.opacity,
                 "radius": s.radius, "brush_id": s.brush_id, "timestamp": s.timestamp, "points": []}
            if lossless:
                if s.raw_header:
                    S["raw_header"] = base64.b64encode(s.raw_header).decode()
                S["bbox"] = list(s.bbox) if s.bbox else None
            for p, w, o in zip(s.points, wf, of):
                P = {"x": p.x, "y": p.y, "pressure": p.pressure, "tilt": p.tilt, "velocity": p.velocity,
                     "random": p.random, "width": p.width, "opacity": p.opacity, "corner": p.corner,
                     "render_radius": s.radius * w, "render_opacity": s.opacity * o}
                if p.controls:
                    P["controls"] = [list(c) for c in p.controls]
                if lossless:
                    if p.raw:
                        P["raw"] = base64.b64encode(p.raw).decode()
                    P["bbox"] = list(p.bbox) if p.bbox else None
                    P["seed"] = p.seed
                S["points"].append(P)
            L["strokes"].append(S)
        out["layers"].append(L)
    return out


def stroke_from_dict(S: dict, default_brush: int) -> vector.Stroke:
    flags = S.get("flags")
    if flags is None:
        flags = KINDS.get(S.get("kind", "normal"), vector.NORMAL) | CURVES.get(S.get("curve", "spline"), vector.SPLINE)
        if S.get("closed"):
            flags |= vector.CLOSED
        if S.get("curve", "spline") == "spline" and not S.get("closed"):
            flags |= vector.FREEHAND
    pts = []
    for P in S["points"]:
        raw = base64.b64decode(P["raw"]) if "raw" in P else None
        pts.append(vector.Point(
            float(P["x"]), float(P["y"]), pressure=float(P.get("pressure", 1.0)), tilt=float(P.get("tilt", 1.0)),
            velocity=float(P.get("velocity", 0.0)), width=float(P.get("width", 1.0)), opacity=float(P.get("opacity", 1.0)),
            random=float(P.get("random", 0.0)), corner=bool(P.get("corner", False)),
            seed=P.get("seed"), bbox=tuple(P["bbox"]) if P.get("bbox") else None,
            controls=[tuple(c) for c in P.get("controls", [])], raw=raw,
            flags=(int.from_bytes(raw[32:36], "big") if raw else 0)))
    s = vector.Stroke(pts, flags=flags, color=tuple(S.get("color", (0, 0, 0))),
                      sub_color=tuple(S.get("sub_color", (255, 255, 255))), opacity=float(S.get("opacity", 1.0)),
                      brush_id=int(S.get("brush_id", default_brush)), radius=float(S.get("radius", 2.0)),
                      timestamp=int(S.get("timestamp", 0)),
                      raw_header=base64.b64decode(S["raw_header"]) if "raw_header" in S else None)
    if s.raw_header:
        hs = int.from_bytes(s.raw_header[:4], "big")
        s.header_size = hs
        s.point_section_size = int.from_bytes(s.raw_header[4:8], "big")
    if S.get("bbox") and all(p.bbox for p in pts):
        s.bbox = tuple(S["bbox"])   # keep CSP's own boxes (lossless mode)
    else:
        s.update_bboxes()
    return s


def apply_dict(doc: ClipDocument, data: dict, replace: bool = True) -> None:
    """Write layers from `data` into `doc`. Layers are matched by "id" when
    it exists in the document, else by name, else created."""
    existing = {l.layer_id: l for l in doc.vector_layers()}
    by_name = {l.name: l for l in existing.values()}
    brushes, _ = doc.brushes()
    default_brush = None
    last = None
    for L in data.get("layers", []):
        layer = existing.get(L.get("id")) or by_name.get(L.get("name"))
        if layer is None:
            lid = doc.add_vector_layer(L.get("name", "Layer"), above=last)
        else:
            lid = layer.layer_id
        strokes = []
        for S in L.get("strokes", []):
            bid = S.get("brush_id")
            if bid is None or int(bid) not in brushes:
                if default_brush is None:
                    default_brush = doc.default_brush_id()
                    brushes, _ = doc.brushes()
                S = dict(S, brush_id=default_brush)
            strokes.append(stroke_from_dict(S, default_brush or 1))
        if not replace:
            strokes = doc.vector_layer(lid).strokes + strokes
        doc.set_strokes(lid, strokes)
        doc.set_layer_props(lid, name=L.get("name"), visible=L.get("visible"), opacity=L.get("opacity"))
        last = lid


def effector_from_dict(d: dict) -> Effector:
    return Effector(**d)

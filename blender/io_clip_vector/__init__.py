"""CLIP STUDIO PAINT .clip vector layers <-> Blender Grease Pencil (v3).

File > Import > CLIP STUDIO PAINT vector (.clip)
File > Export > CLIP STUDIO PAINT vector (.clip)

Every CSP value that Grease Pencil has no slot for (pen pressure, tilt,
velocity, width/opacity factors, brush id, stroke flags, ...) is kept in
`csp_*` attributes on the drawing so that an import -> edit -> export
round trip writes the original data back. Strokes drawn in Blender (no
`csp_valid` flag) get their width encoded as CSP pen pressure.
"""
bl_info = {
    "name": "CLIP STUDIO PAINT vector (.clip)",
    "author": "clipvec",
    "version": (0, 1, 0),
    "blender": (4, 3, 0),
    "location": "File > Import/Export",
    "description": "Import/export CLIP STUDIO PAINT vector layers with pen pressure as Grease Pencil",
    "category": "Import-Export",
}

import json
import math
import os
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

try:  # vendored copy (see tools/build_addon.py)
    from .clipvec.document import ClipDocument
    from .clipvec import vector as cv
except ImportError:  # development checkout: use the repository package
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from clipvec.document import ClipDocument  # noqa: E402
    from clipvec import vector as cv  # noqa: E402

CT_CATMULL, CT_POLY, CT_BEZIER = 0, 1, 2

POINT_FLOATS = ("csp_pressure", "csp_tilt", "csp_velocity", "csp_random", "csp_width", "csp_opacity")
CURVE_ATTRS = (("csp_valid", "INT"), ("csp_flags", "INT"), ("csp_brush", "INT"), ("csp_radius", "FLOAT"),
               ("csp_stroke_opacity", "FLOAT"), ("csp_color", "INT"), ("csp_sub_color", "INT"), ("csp_timestamp", "INT"))


# ---------------------------------------------------------------- colour
def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb8(c):
    c = max(0.0, min(1.0, c))
    v = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return int(round(v * 255))


def pack_rgb(c):
    return (c[0] << 16) | (c[1] << 8) | c[2]


def unpack_rgb(v):
    v &= 0xFFFFFF
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def to_i32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def get_material(gp, rgb):
    name = "CSP #%02x%02x%02x" % tuple(rgb)
    mat = bpy.data.materials.get(name)
    if mat is None or not mat.is_grease_pencil:
        mat = bpy.data.materials.new(name)
        bpy.data.materials.create_gpencil_data(mat)
        mat.grease_pencil.color = (*[srgb_to_linear(c) for c in rgb], 1.0)
        mat.grease_pencil.show_fill = False
        mat.grease_pencil.show_stroke = True
    for i, m in enumerate(gp.materials):
        if m == mat:
            return i
    gp.materials.append(mat)
    return len(gp.materials) - 1


# ---------------------------------------------------------------- geometry
class Mapping:
    """canvas px (x right, y down) <-> Grease Pencil local XZ plane."""

    def __init__(self, width, height, ppu, center):
        self.w, self.h, self.ppu, self.center = width, height, ppu, center

    def to_blender(self, x, y):
        if self.center:
            return ((x - self.w / 2) / self.ppu, 0.0, (self.h / 2 - y) / self.ppu)
        return (x / self.ppu, 0.0, -y / self.ppu)

    def to_canvas(self, v):
        if self.center:
            return (v[0] * self.ppu + self.w / 2, self.h / 2 - v[2] * self.ppu)
        return (v[0] * self.ppu, -v[2] * self.ppu)


def _cr_handles(pts, i, closed):
    """Cubic Bezier handles equal to CSP's uniform Catmull-Rom segment
    pts[i] -> pts[i+1] (hard corners use a reflected phantom point)."""
    n = len(pts)
    a, b = pts[i], pts[(i + 1) % n]
    if a.corner or (i == 0 and not closed):
        p0 = (2 * a.x - b.x, 2 * a.y - b.y)
    else:
        q = pts[i - 1]
        p0 = (q.x, q.y)
    j = i + 2
    if b.corner or (j >= n and not closed):
        p3 = (2 * b.x - a.x, 2 * b.y - a.y)
    else:
        q = pts[j % n]
        p3 = (q.x, q.y)
    c1 = (a.x + (b.x - p0[0]) / 6.0, a.y + (b.y - p0[1]) / 6.0)
    c2 = (b.x - (p3[0] - a.x) / 6.0, b.y - (p3[1] - a.y) / 6.0)
    return c1, c2


def _ensure_attr(d, name, dtype, domain):
    a = d.attributes.get(name)
    if a is None:
        a = d.attributes.new(name, dtype, domain)
    return a


# ---------------------------------------------------------------- import
def import_clip(context, filepath, ppu=1000.0, center=True, frame=None, layer_filter=None):
    doc = ClipDocument.load(filepath)
    c = doc.canvas
    m = Mapping(c["width"], c["height"], ppu, center)
    frame = context.scene.frame_current if frame is None else frame
    name = os.path.splitext(os.path.basename(filepath))[0]
    gp = bpy.data.grease_pencils.new(name)
    obj = bpy.data.objects.new(name, gp)
    context.collection.objects.link(obj)
    layer_map = {}
    for layer in doc.vector_layers():  # bottom-most first == GP layer order
        gl = gp.layers.new(layer.name or f"Layer {layer.layer_id}")
        gl.hide = not layer.visible
        gl.opacity = layer.opacity
        layer_map[gl.name] = layer.layer_id
        drawing = gl.frames.new(frame).drawing
        strokes = [s for s in layer.strokes if s.points]
        if not strokes:
            continue
        _fill_drawing(doc, drawing, strokes, m, gp)
    obj["csp_source"] = os.path.abspath(filepath)
    obj["csp_canvas"] = [float(c["width"]), float(c["height"]), float(c["dpi"])]
    obj["csp_ppu"] = float(ppu)
    obj["csp_center"] = bool(center)
    obj["csp_layers"] = json.dumps(layer_map, ensure_ascii=False)
    for o in list(context.selected_objects):
        o.select_set(False)
    try:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except RuntimeError:  # object not in the active view layer
        pass
    return obj


def _fill_drawing(doc, d, strokes, m, gp):
    d.add_strokes([len(s.points) for s in strokes])
    ctypes, bez_idx, cr_idx = [], [], []
    pos, rad, opa = [], [], []
    hl, hr = [], []
    pf = {k: [] for k in POINT_FLOATS}
    pflags, seeds = [], []
    cv_ = {k: [] for k, _ in CURVE_ATTRS}
    mats, cyc = [], []
    for si, s in enumerate(strokes):
        pts = s.points
        n = len(pts)
        wf = doc.stroke_width_factors(s)
        of = doc.stroke_opacity_factors(s)
        curve = s.flags & 0xF0
        has_corner = any(p.corner for p in pts[1:-1]) or (s.closed and any(p.corner for p in pts))
        if curve in (cv.QUADRATIC, cv.CUBIC) or (curve == cv.SPLINE and has_corner):
            kind = CT_BEZIER
            bez_idx.append(si)
        elif curve == cv.SPLINE and n > 2:
            kind = CT_CATMULL
            cr_idx.append(si)
        else:
            kind = CT_POLY
        ctypes.append(kind)
        # bezier handles per point: left (incoming), right (outgoing)
        L = [(p.x, p.y) for p in pts]
        R = [(p.x, p.y) for p in pts]
        if kind == CT_BEZIER:
            segs = n if s.closed else n - 1
            for i in range(segs):
                a, b = pts[i], pts[(i + 1) % n]
                if curve == cv.CUBIC and len(a.controls) >= 2:
                    c1, c2 = a.controls[0], a.controls[1]
                elif curve == cv.QUADRATIC and a.controls:
                    q = a.controls[0]
                    c1 = (a.x + 2 / 3 * (q[0] - a.x), a.y + 2 / 3 * (q[1] - a.y))
                    c2 = (b.x + 2 / 3 * (q[0] - b.x), b.y + 2 / 3 * (q[1] - b.y))
                elif curve == cv.SPLINE:
                    c1, c2 = _cr_handles(pts, i, s.closed)
                else:
                    c1, c2 = (a.x, a.y), (b.x, b.y)
                R[i] = c1
                L[(i + 1) % n] = c2
        for i, p in enumerate(pts):
            pos.extend(m.to_blender(p.x, p.y))
            hl.extend(m.to_blender(*L[i]))
            hr.extend(m.to_blender(*R[i]))
            rad.append(s.radius * wf[i] / m.ppu)
            opa.append(max(0.0, min(1.0, s.opacity * of[i])))
            pf["csp_pressure"].append(p.pressure); pf["csp_tilt"].append(p.tilt)
            pf["csp_velocity"].append(p.velocity); pf["csp_random"].append(p.random)
            pf["csp_width"].append(p.width); pf["csp_opacity"].append(p.opacity)
            pflags.append(p.flags | (1 if p.corner else 0))
            seeds.append(to_i32(s.timestamp if p.seed is None else p.seed))
        cv_["csp_valid"].append(1); cv_["csp_flags"].append(s.flags); cv_["csp_brush"].append(s.brush_id)
        cv_["csp_radius"].append(s.radius); cv_["csp_stroke_opacity"].append(s.opacity)
        cv_["csp_color"].append(pack_rgb(s.color)); cv_["csp_sub_color"].append(pack_rgb(s.sub_color))
        cv_["csp_timestamp"].append(to_i32(s.timestamp))
        mats.append(get_material(gp, s.color))
        cyc.append(s.closed)
    if cr_idx:
        d.set_types(type="CATMULL_ROM", indices=cr_idx)
    if bez_idx:
        d.set_types(type="BEZIER", indices=bez_idx)
    d.attributes["position"].data.foreach_set("vector", pos)
    _ensure_attr(d, "radius", "FLOAT", "POINT").data.foreach_set("value", rad)
    _ensure_attr(d, "opacity", "FLOAT", "POINT").data.foreach_set("value", opa)
    if bez_idx:
        _ensure_attr(d, "handle_left", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", hl)
        _ensure_attr(d, "handle_right", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", hr)
        zeros = [0] * (len(pos) // 3)  # FREE handles keep the exact CSP geometry
        _ensure_attr(d, "handle_type_left", "INT8", "POINT").data.foreach_set("value", zeros)
        _ensure_attr(d, "handle_type_right", "INT8", "POINT").data.foreach_set("value", zeros)
    for k, vals in pf.items():
        _ensure_attr(d, k, "FLOAT", "POINT").data.foreach_set("value", vals)
    _ensure_attr(d, "csp_pflags", "INT", "POINT").data.foreach_set("value", pflags)
    _ensure_attr(d, "csp_seed", "INT", "POINT").data.foreach_set("value", seeds)
    for k, t in CURVE_ATTRS:
        _ensure_attr(d, k, t, "CURVE").data.foreach_set("value", cv_[k])
    _ensure_attr(d, "material_index", "INT", "CURVE").data.foreach_set("value", mats)
    _ensure_attr(d, "cyclic", "BOOLEAN", "CURVE").data.foreach_set("value", cyc)
    d.tag_positions_changed()


# ---------------------------------------------------------------- export
def _read(d, name, domain_len, width=1, default=0.0):
    a = d.attributes.get(name)
    if a is None:
        return None
    key = {"FLOAT_VECTOR": "vector", "FLOAT_COLOR": "color", "BYTE_COLOR": "color"}.get(a.data_type, "value")
    buf = [default] * (domain_len * width)
    a.data.foreach_get(key, buf)
    return buf


def _material_rgb(obj, idx):
    mats = obj.data.materials
    if 0 <= idx < len(mats) and mats[idx] and mats[idx].is_grease_pencil:
        c = mats[idx].grease_pencil.color
        return [c[0], c[1], c[2]]
    return [0.0, 0.0, 0.0]


def drawing_to_strokes(doc, obj, d, m, default_brush_cb, resolve_brush=None):
    npts = len(d.attributes["position"].data)
    nstr = len(d.strokes)
    if nstr == 0:
        return []
    pos = _read(d, "position", npts, 3)
    rad = _read(d, "radius", npts, 1, 0.01) or [0.01] * npts
    opa = _read(d, "opacity", npts, 1, 1.0) or [1.0] * npts
    vcol = _read(d, "vertex_color", npts, 4)
    hl = _read(d, "handle_left", npts, 3)
    hr = _read(d, "handle_right", npts, 3)
    ctype = _read(d, "curve_type", nstr, 1, 1)
    cyclic = _read(d, "cyclic", nstr, 1, False)
    matidx = _read(d, "material_index", nstr, 1, 0)
    P = {k: _read(d, k, npts) for k in POINT_FLOATS}
    pflags = _read(d, "csp_pflags", npts, 1, 0)
    seeds = _read(d, "csp_seed", npts, 1, 0)
    C = {k: _read(d, k, nstr, 1, 0) for k, _ in CURVE_ATTRS}
    offs = [0] * (nstr + 1)
    d.curve_offsets.foreach_get("value", offs)  # nstr + 1 entries, last == npts
    brushes, graphs = doc.brushes()
    out = []
    for si in range(nstr):
        a, b = offs[si], offs[si + 1]
        n = b - a
        if n <= 0:
            continue
        valid = bool(C["csp_valid"] and C["csp_valid"][si])
        ct = int(ctype[si]) if ctype else CT_POLY
        closed = bool(cyclic[si]) if cyclic else False
        # colour: material mixed with vertex colour (per stroke, first point)
        base = _material_rgb(obj, int(matidx[si]) if matidx else 0)
        if vcol:
            vr, vg, vb, va = vcol[a * 4:a * 4 + 4]
            base = [base[0] + (vr - base[0]) * va, base[1] + (vg - base[1]) * va, base[2] + (vb - base[2]) * va]
        rgb = tuple(linear_to_srgb8(v) for v in base)
        radii_px = [rad[i] * m.ppu for i in range(a, b)]
        if valid:
            flags = C["csp_flags"][si] & 0xFFFFFFFF
            brush_id = C["csp_brush"][si]
            if brush_id not in brushes:
                brush_id = (resolve_brush(brush_id) if resolve_brush else None) or default_brush_cb()
                brushes, graphs = doc.brushes()
            R = C["csp_radius"][si] or max(radii_px) or 1.0
            s_op = C["csp_stroke_opacity"][si] or 1.0
            sub = unpack_rgb(C["csp_sub_color"][si])
            ts = C["csp_timestamp"][si] & 0xFFFFFFFF
            # keep curve family, but a curve re-typed in Blender wins
            curve = flags & 0xF0
            if ct == CT_POLY and curve in (cv.SPLINE, cv.QUADRATIC, cv.CUBIC):
                curve = cv.SPLINE
            elif ct == CT_BEZIER and curve not in (cv.SPLINE, cv.QUADRATIC, cv.CUBIC):
                curve = cv.CUBIC
            elif ct == CT_CATMULL:
                curve = cv.SPLINE
            flags = (flags & ~0xF0) | curve
            flags = (flags | cv.CLOSED) if closed else (flags & ~cv.CLOSED)
        else:
            brush_id = default_brush_cb()
            brushes, graphs = doc.brushes()
            R = max(radii_px) or 1.0
            s_op = 1.0
            sub = (255, 255, 255)
            ts = 0
            curve = cv.CUBIC if ct == CT_BEZIER else cv.SPLINE
            flags = cv.NORMAL | curve | (cv.CLOSED if closed else (cv.FREEHAND if curve == cv.SPLINE else 0))
        brush = brushes.get(brush_id)
        pts = []
        for k, i in enumerate(range(a, b)):
            x, y = m.to_canvas(pos[i * 3:i * 3 + 3])
            p = cv.Point(x, y)
            if valid and P["csp_pressure"]:
                p.pressure, p.tilt = P["csp_pressure"][i], P["csp_tilt"][i]
                p.velocity, p.random = P["csp_velocity"][i], P["csp_random"][i]
                p.width, p.opacity = P["csp_width"][i], P["csp_opacity"][i]
                p.flags = pflags[i] & ~1 if pflags else 0
                p.corner = bool(pflags[i] & 1) if pflags else False
                if seeds:
                    p.seed = seeds[i] & 0xFFFFFFFF
                # honour radius / opacity edits made in Blender
                eff = brush.width_factor(graphs, p) if brush else p.pressure
                want = radii_px[k]
                if abs(R * eff * p.width - want) > 1e-4 * max(R, 1.0):
                    if eff > 1e-6:
                        p.width = want / (R * eff)
                    else:  # brush gives zero width at this pressure: re-encode as pressure
                        p.pressure, p.width = max(0.0, min(1.0, want / R)), 1.0
                effo = brush.opacity_factor(graphs, p) if brush else 1.0
                if effo > 1e-6 and abs(s_op * effo * p.opacity - opa[i]) > 1e-4:
                    p.opacity = max(0.0, min(1.0, opa[i] / (s_op * effo)))
            else:
                p.pressure = max(0.0, min(1.0, radii_px[k] / R))
                p.opacity = max(0.0, min(1.0, opa[i]))
            pts.append(p)
        if curve in (cv.CUBIC, cv.QUADRATIC) and hl and hr:
            segs = n if closed else n - 1
            for k in range(segs):
                i, j = a + k, a + (k + 1) % n
                c1 = m.to_canvas(hr[i * 3:i * 3 + 3])
                c2 = m.to_canvas(hl[j * 3:j * 3 + 3])
                if curve == cv.QUADRATIC:
                    pts[k].controls = [(pts[k].x + 1.5 * (c1[0] - pts[k].x), pts[k].y + 1.5 * (c1[1] - pts[k].y))]
                else:
                    pts[k].controls = [c1, c2]
            if not closed:
                last = pts[-1]
                last.controls = [(last.x, last.y)] * (1 if curve == cv.QUADRATIC else 2)
        if curve == cv.SPLINE and not valid:
            pts[-1].corner = True  # CSP marks the end point of free-hand strokes
        s = cv.Stroke(pts, flags=flags, color=rgb, sub_color=sub, opacity=s_op, brush_id=brush_id,
                      radius=R, timestamp=ts)
        s.update_bboxes()
        out.append(s)
    return out


def export_clip(context, obj, filepath, base=None, frame=None, canvas=None, ppu=None, center=None,
                remove_missing=False):
    if obj is None or obj.type != "GREASEPENCIL":
        raise ValueError("select a Grease Pencil object")
    frame = context.scene.frame_current if frame is None else frame
    gp = obj.data
    ppu = float(obj.get("csp_ppu", 1000.0) if ppu is None else ppu)
    center = bool(obj.get("csp_center", True) if center is None else center)
    if base:
        doc = ClipDocument.load(base)
        cw, ch = doc.canvas["width"], doc.canvas["height"]
    else:
        cw, ch, dpi = canvas or obj.get("csp_canvas", (1000.0, 1000.0, 72.0))
        doc = ClipDocument.new(int(cw), int(ch), float(dpi))
    m = Mapping(cw, ch, ppu, center)
    layer_map = json.loads(obj.get("csp_layers", "{}")) if base else {}
    existing = {l.layer_id: l for l in doc.vector_layers()}
    template_first = None if base else doc.vector_layers()[0].layer_id
    brush_cache = []
    src_path = obj.get("csp_source")
    src_doc = [None]
    copied = {}

    def default_brush():
        if not brush_cache:
            brush_cache.append(doc.default_brush_id())
        return brush_cache[0]

    def resolve_brush(bid):
        """brush of the original file, copied into a new document"""
        if bid in copied:
            return copied[bid]
        if src_doc[0] is None and src_path and os.path.exists(src_path) and not base:
            src_doc[0] = ClipDocument.load(src_path)
        copied[bid] = doc.copy_brush_style(src_doc[0], bid) if src_doc[0] else None
        return copied[bid]

    written, below = set(), None
    for gl in gp.layers:  # bottom-most first
        fr = gl.get_frame_at(frame)
        strokes = drawing_to_strokes(doc, obj, fr.drawing, m, default_brush, resolve_brush) if fr else []
        lid = layer_map.get(gl.name)
        if lid not in existing:
            lid = None
        if lid is not None:  # restore unedited strokes bit-exactly
            pool = {}
            for o in existing[lid].strokes:
                pool.setdefault((o.timestamp, len(o.points)), []).append(o)
            for st in strokes:
                cands = pool.get((st.timestamp, len(st.points)))
                if cands:
                    cv.reconcile(st, cands.pop(0))
        if lid is None:
            if template_first is not None:
                lid, template_first = template_first, None
            else:
                lid = doc.add_vector_layer(gl.name, above=below)
                existing[lid] = doc.vector_layer(lid)
        doc.set_strokes(lid, strokes)
        doc.set_layer_props(lid, name=gl.name, visible=not gl.hide, opacity=gl.opacity)
        written.add(lid)
        below = lid
    if remove_missing:
        for lid in list(existing):
            if lid not in written:
                doc.remove_layer(lid)
    doc.save(filepath)
    return doc


# ---------------------------------------------------------------- operators
class IMPORT_OT_clip_vector(bpy.types.Operator, ImportHelper):
    """Import vector layers of a CLIP STUDIO PAINT file as Grease Pencil"""
    bl_idname = "import_scene.clip_vector"
    bl_label = "Import CLIP STUDIO vector"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".clip"
    filter_glob: StringProperty(default="*.clip", options={"HIDDEN"})
    ppu: FloatProperty(name="Pixels per Unit", default=1000.0, min=1e-3,
                       description="Canvas pixels per Blender unit")
    center: BoolProperty(name="Center Canvas", default=True, description="Put the canvas centre at the origin")

    def execute(self, context):
        try:
            import_clip(context, self.filepath, self.ppu, self.center)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"import failed: {e}")
            return {"CANCELLED"}
        return {"FINISHED"}


class EXPORT_OT_clip_vector(bpy.types.Operator, ExportHelper):
    """Export the active Grease Pencil object as CLIP STUDIO PAINT vector layers"""
    bl_idname = "export_scene.clip_vector"
    bl_label = "Export CLIP STUDIO vector"
    filename_ext = ".clip"
    filter_glob: StringProperty(default="*.clip", options={"HIDDEN"})
    mode: EnumProperty(name="Mode", items=[
        ("UPDATE", "Update source .clip", "Write into a copy of the imported .clip: raster layers, "
                                          "brushes and everything else are preserved"),
        ("NEW", "New document", "Create a new .clip containing only the vector layers")], default="UPDATE")
    remove_missing: BoolProperty(name="Remove deleted layers", default=False,
                                 description="Delete CSP vector layers that no longer exist in Blender")
    width: IntProperty(name="Width", default=1000, min=1)
    height: IntProperty(name="Height", default=1000, min=1)
    dpi: FloatProperty(name="DPI", default=72.0, min=1.0)
    ppu: FloatProperty(name="Pixels per Unit", default=1000.0, min=1e-3)

    def invoke(self, context, event):
        obj = context.active_object
        if obj is not None and "csp_canvas" in obj:
            self.width, self.height = int(obj["csp_canvas"][0]), int(obj["csp_canvas"][1])
            self.dpi = obj["csp_canvas"][2]
            self.ppu = obj.get("csp_ppu", 1000.0)
        if obj is None or "csp_source" not in obj or not os.path.exists(obj.get("csp_source", "")):
            self.mode = "NEW"
        return super().invoke(context, event)

    def execute(self, context):
        obj = context.active_object
        try:
            base = obj.get("csp_source") if self.mode == "UPDATE" else None
            canvas = None if base else (self.width, self.height, self.dpi)
            export_clip(context, obj, self.filepath, base=base, canvas=canvas, ppu=self.ppu,
                        remove_missing=self.remove_missing)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"export failed: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"wrote {self.filepath}")
        return {"FINISHED"}


def menu_import(self, context):
    self.layout.operator(IMPORT_OT_clip_vector.bl_idname, text="CLIP STUDIO PAINT vector (.clip)")


def menu_export(self, context):
    self.layout.operator(EXPORT_OT_clip_vector.bl_idname, text="CLIP STUDIO PAINT vector (.clip)")


classes = (IMPORT_OT_clip_vector, EXPORT_OT_clip_vector)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

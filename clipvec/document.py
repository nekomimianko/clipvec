"""High level read/write API for vector layers in .clip files.

    doc = ClipDocument.load("in.clip")
    for layer in doc.vector_layers():
        for stroke in layer.strokes: ...
    lid = doc.add_vector_layer("new")
    doc.set_strokes(lid, [Stroke([...])])
    doc.save("out.clip")
"""
from __future__ import annotations

import math
import os
import struct
import uuid
from dataclasses import dataclass

from . import brush as brushmod
from . import schema, vector
from .container import ClipFile, as_bytes

TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "empty_vector.clip")


def new_external_id() -> bytes:
    return b"extrnlid" + uuid.uuid4().hex.upper().encode("ascii")


@dataclass
class VectorLayer:
    layer_id: int
    name: str
    object_row: int | None     # VectorObjectList.MainId (None: empty layer)
    external_id: bytes | None
    strokes: list[vector.Stroke]
    visible: bool = True
    opacity: float = 1.0
    parent: int | None = None  # Layer.MainId of the containing folder


class ClipDocument:
    def __init__(self, clip: ClipFile):
        self.clip = clip
        self.db = clip.open_db()
        self._dirty_layers: set[int] = set()
        self._layers: dict[int, VectorLayer] | None = None
        self._brushes = None

    @classmethod
    def load(cls, path: str) -> "ClipDocument":
        return cls(ClipFile.load(path))

    @classmethod
    def new(cls, width: int, height: int, dpi: float = 72.0, template: str = TEMPLATE) -> "ClipDocument":
        """New document with one empty vector layer (from the bundled
        CSP-generated template, resized)."""
        doc = cls.load(template)
        doc.set_canvas_size(width, height, dpi)
        return doc

    # ------------------------------------------------------------ helpers
    @property
    def con(self):
        return self.db.con

    def _cols(self, table):
        return [r[1] for r in self.con.execute(f"PRAGMA table_info({table})")]

    def _has_table(self, table):
        return self.con.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None

    def _alloc(self, table: str) -> int:
        """Allocate a MainId. ElemScheme.MaxIndex is CSP's high-water mark of
        ids ever used for a table; it must be kept >= every MainId."""
        cur = self.con.execute(f"select max(MainId) from {table}").fetchone()[0] or 0
        row = self.con.execute("select MaxIndex from ElemScheme where TableName=?", (table,)).fetchone()
        mx = max(cur, row[0] if row and row[0] else 0) + 1
        if row:
            self.con.execute("update ElemScheme set MaxIndex=? where TableName=?", (mx, table))
        return mx

    def _insert(self, table: str, d: dict) -> None:
        d = {k: v for k, v in d.items() if k != "_PW_ID"}
        self.con.execute(f"insert into {table} ({','.join(d)}) values ({','.join('?' * len(d))})", list(d.values()))

    def _ensure_table(self, table: str) -> None:
        if not self._has_table(table):
            # CSP files carry the RemovedExternal-logging triggers only
            # sometimes; they are optional, so only tables and indexes are made
            order = {"CREATE TABLE": 0, "CREATE INDEX": 1}
            for sql in sorted(schema.TABLE_SQL[table], key=lambda q: order.get(" ".join(q.split()[:2]), 3)):
                if not sql.startswith("CREATE TRIGGER"):
                    self.con.execute(sql)

    def _add_external(self, ext: bytes, body: bytes) -> None:
        self.clip.add_external(ext, body)
        self.con.execute("insert into ExternalChunk (ExternalID, Offset) values (?, 0)", (ext.decode("ascii"),))

    def _drop_external(self, ext: bytes) -> None:
        self.clip.chunks = [c for c in self.clip.chunks if c.external_id != ext]
        self.con.execute("delete from ExternalChunk where ExternalID=? or ExternalID=?", (ext, ext.decode("ascii")))

    # ------------------------------------------------------------ canvas
    @property
    def canvas(self):
        r = self.db.q("select CanvasWidth, CanvasHeight, CanvasResolution from Canvas")[0]
        return {"width": r[0], "height": r[1], "dpi": r[2]}

    def set_canvas_size(self, width: int, height: int, dpi: float | None = None) -> None:
        """Resize an (empty) canvas: Canvas/Project rows and every Offscreen
        attribute (all bitmap caches are dropped, CSP re-creates them)."""
        width, height = int(width), int(height)
        con = self.con
        con.execute("update Canvas set CanvasWidth=?, CanvasHeight=?", (float(width), float(height)))
        con.execute("update Project set DefaultPageWidth=?, DefaultPageHeight=?", (float(width), float(height)))
        if dpi:
            con.execute("update Canvas set CanvasResolution=?", (float(dpi),))
            con.execute("update Project set DefaultPageResolution=?", (float(dpi),))
        if "ThumbnailCanvasWidth" in self._cols("LayerThumbnail"):
            con.execute("update LayerThumbnail set ThumbnailCanvasWidth=?, ThumbnailCanvasHeight=?", (width, height))
        thumb_offs = {r[0] for r in con.execute("select ThumbnailOffscreen from LayerThumbnail")}
        scale_of = {r[0]: r[1] for r in con.execute("select Offscreen, ThisScale from MipmapInfo")}
        for mid, attr, ext in con.execute("select MainId, Attribute, BlockData from Offscreen").fetchall():
            if mid in thumb_offs or mid not in scale_of:
                continue  # thumbnails keep their fixed size
            s = scale_of[mid] / 100.0
            w, h = max(1, math.ceil(width * s)), max(1, math.ceil(height * s))
            new_attr = _resize_attribute(attr, w, h)
            self._drop_external(as_bytes(ext))
            con.execute("update Offscreen set Attribute=?, BlockData=? where MainId=?", (new_attr, new_external_id(), mid))
        for mid, ext in con.execute("select MainId, BlockData from Offscreen").fetchall():
            if mid in thumb_offs:
                self._drop_external(as_bytes(ext))
                con.execute("update Offscreen set BlockData=? where MainId=?", (new_external_id(), mid))
        # preview PNG (shown by CLIP STUDIO's file browser): blank, same aspect
        if self._has_table("CanvasPreview"):
            k = min(1.0, 1024.0 / max(width, height))
            pw, ph = max(1, round(width * k)), max(1, round(height * k))
            con.execute("update CanvasPreview set ImageWidth=?, ImageHeight=?, ImageData=?", (pw, ph, _blank_png(pw, ph)))

    # ------------------------------------------------------------ layers
    def layer_rows(self):
        return self.db.q("select * from Layer order by _PW_ID")

    def layer_tree(self):
        """Yield (depth, row, parent_id) bottom-most first (= paint order; the
        CSP layer panel shows the reverse). Sibling chains start at the
        folder's LayerFirstChildIndex and follow LayerNextIndex upwards."""
        rows = {r["MainId"]: r for r in self.layer_rows()}
        root = self.db.q("select CanvasRootFolder from Canvas")[0][0]

        def walk(mid, depth):
            child = rows[mid]["LayerFirstChildIndex"]
            seen = set()
            while child and child not in seen:
                seen.add(child)
                r = rows[child]
                yield depth, r, mid
                if r["LayerFolder"]:
                    yield from walk(child, depth + 1)
                child = r["LayerNextIndex"]
        yield from walk(root, 0)

    @staticmethod
    def _is_vector_row(r) -> bool:
        keys = r.keys()
        return (r["LayerType"] == 0 and not r["LayerFolder"]
                and ("DrawToRenderOffscreenType" in keys and r["DrawToRenderOffscreenType"] == 10)
                and not ("TextLayerType" in keys and r["TextLayerType"] is not None))

    def vector_layers(self) -> list[VectorLayer]:
        """All vector layers, bottom-most first."""
        if self._layers is None:
            self._layers = {}
            objs = {}
            if self._has_table("VectorObjectList"):
                for r in self.db.q("select MainId, LayerId, VectorData from VectorObjectList order by _PW_ID"):
                    objs.setdefault(r["LayerId"], r)
            for depth, r, parent in self.layer_tree():
                o = objs.get(r["MainId"])
                if o is None and not self._is_vector_row(r):
                    continue
                ext = as_bytes(o["VectorData"]) if o is not None else None
                body = (self.clip.external_body(ext) or b"") if ext else b""
                self._layers[r["MainId"]] = VectorLayer(
                    layer_id=r["MainId"], name=r["LayerName"] or "", object_row=o["MainId"] if o is not None else None,
                    external_id=ext, strokes=vector.decode(body), visible=bool(r["LayerVisibility"]),
                    opacity=(r["LayerOpacity"] or 256) / 256.0, parent=parent)
        return list(self._layers.values())

    def vector_layer(self, layer_id: int) -> VectorLayer:
        self.vector_layers()
        return self._layers[layer_id]

    def set_strokes(self, layer_id: int, strokes: list[vector.Stroke]) -> None:
        self.vector_layer(layer_id).strokes = strokes
        self._dirty_layers.add(layer_id)

    def set_layer_props(self, layer_id: int, name: str | None = None, visible: bool | None = None,
                        opacity: float | None = None) -> None:
        if name is not None:
            self.con.execute("update Layer set LayerName=? where MainId=?", (name, layer_id))
            self.vector_layer(layer_id).name = name
        if visible is not None:
            self.con.execute("update Layer set LayerVisibility=? where MainId=?", (1 if visible else 0, layer_id))
        if opacity is not None:
            self.con.execute("update Layer set LayerOpacity=? where MainId=?", (int(round(max(0, min(1, opacity)) * 256)), layer_id))

    def remove_layer(self, layer_id: int) -> None:
        """Unlink a (vector) layer from the tree and delete its rows."""
        con = self.con
        nxt = con.execute("select LayerNextIndex from Layer where MainId=?", (layer_id,)).fetchone()[0]
        con.execute("update Layer set LayerNextIndex=? where LayerNextIndex=?", (nxt, layer_id))
        con.execute("update Layer set LayerFirstChildIndex=? where LayerFirstChildIndex=?", (nxt, layer_id))
        for (ext,) in con.execute("select BlockData from Offscreen where LayerId=?", (layer_id,)).fetchall():
            self._drop_external(as_bytes(ext))
        if self._has_table("VectorObjectList"):
            for (ext,) in con.execute("select VectorData from VectorObjectList where LayerId=?", (layer_id,)).fetchall():
                self._drop_external(as_bytes(ext))
            con.execute("delete from VectorObjectList where LayerId=?", (layer_id,))
        for t in ("Offscreen", "MipmapInfo", "Mipmap", "LayerThumbnail"):
            con.execute(f"delete from {t} where LayerId=?", (layer_id,))
        cur = con.execute("select CanvasCurrentLayer from Canvas").fetchone()[0]
        con.execute("delete from Layer where MainId=?", (layer_id,))
        if cur == layer_id:
            any_layer = con.execute("select MainId from Layer where LayerFolder=0 or LayerFolder is null limit 1").fetchone()
            con.execute("update Canvas set CanvasCurrentLayer=?", (any_layer[0] if any_layer else 0,))
        if self._layers:
            self._layers.pop(layer_id, None)
        self._dirty_layers.discard(layer_id)

    def add_vector_layer(self, name: str, template_layer_id: int | None = None, above: int | None = None) -> int:
        """Create a new vector layer by cloning the rows of an existing vector
        layer (Layer, Mipmap, MipmapInfo, Offscreen, LayerThumbnail). The new
        layer is placed directly above `above` (default: top of the template's
        folder). Returns the new Layer.MainId."""
        vls = self.vector_layers()
        if template_layer_id is None:
            if not vls:
                raise ValueError("document has no vector layer to use as a template")
            template_layer_id = vls[-1].layer_id
        con = self.con
        new_layer = self._alloc("Layer")

        off_map = {}
        for row in con.execute("select * from Offscreen where LayerId=? order by MainId", (template_layer_id,)).fetchall():
            d = dict(zip(self._cols("Offscreen"), row))
            nid = self._alloc("Offscreen")
            off_map[d["MainId"]] = nid
            d.update(MainId=nid, LayerId=new_layer, BlockData=new_external_id())
            self._insert("Offscreen", d)
        mi_rows = [dict(zip(self._cols("MipmapInfo"), r)) for r in
                   con.execute("select * from MipmapInfo where LayerId=? order by MainId", (template_layer_id,)).fetchall()]
        mi_map = {d["MainId"]: self._alloc("MipmapInfo") for d in mi_rows}
        for d in mi_rows:
            d.update(MainId=mi_map[d["MainId"]], LayerId=new_layer, Offscreen=off_map.get(d["Offscreen"], 0),
                     NextIndex=mi_map.get(d["NextIndex"], 0))
            self._insert("MipmapInfo", d)
        mm_map = {}
        for row in con.execute("select * from Mipmap where LayerId=?", (template_layer_id,)).fetchall():
            d = dict(zip(self._cols("Mipmap"), row))
            nid = self._alloc("Mipmap"); mm_map[d["MainId"]] = nid
            d.update(MainId=nid, LayerId=new_layer, BaseMipmapInfo=mi_map.get(d["BaseMipmapInfo"], 0))
            self._insert("Mipmap", d)
        th_map = {}
        for row in con.execute("select * from LayerThumbnail where LayerId=?", (template_layer_id,)).fetchall():
            d = dict(zip(self._cols("LayerThumbnail"), row))
            nid = self._alloc("LayerThumbnail"); th_map[d["MainId"]] = nid
            d.update(MainId=nid, LayerId=new_layer, ThumbnailOffscreen=off_map.get(d["ThumbnailOffscreen"], 0))
            self._insert("LayerThumbnail", d)

        d = dict(zip(self._cols("Layer"), con.execute("select * from Layer where MainId=?", (template_layer_id,)).fetchone()))
        d.update(MainId=new_layer, LayerName=name, LayerSelect=0, LayerUuid=_csp_uuid(), LayerFirstChildIndex=0)
        if "VectorNormalStrokeIndex" in d:
            d["VectorNormalStrokeIndex"] = 0
        for k in ("LayerRenderMipmap", "LayerLayerMaskMipmap"):
            if d.get(k):
                d[k] = mm_map.get(d[k], 0)
        for k in ("LayerRenderThumbnail", "LayerLayerMaskThumbnail"):
            if d.get(k):
                d[k] = th_map.get(d[k], 0)
        # sibling chains run bottom -> top: "above ref" = new.next=ref.next; ref.next=new
        ref = above if above is not None else self._top_sibling(template_layer_id)
        d["LayerNextIndex"] = con.execute("select LayerNextIndex from Layer where MainId=?", (ref,)).fetchone()[0]
        self._insert("Layer", d)
        con.execute("update Layer set LayerNextIndex=? where MainId=?", (new_layer, ref))
        parent = self._parent_of(ref)
        self._layers[new_layer] = VectorLayer(new_layer, name, None, None, [], parent=parent)
        self._dirty_layers.add(new_layer)
        return new_layer

    def _top_sibling(self, layer_id: int) -> int:
        cur, seen = layer_id, set()
        while cur not in seen:
            seen.add(cur)
            nxt = self.con.execute("select LayerNextIndex from Layer where MainId=?", (cur,)).fetchone()[0]
            if not nxt:
                return cur
            cur = nxt
        return cur

    def _parent_of(self, layer_id: int):
        for _, r, parent in self.layer_tree():
            if r["MainId"] == layer_id:
                return parent
        return None

    def _ensure_vector_object(self, layer: VectorLayer) -> None:
        if layer.object_row is not None:
            return
        self._ensure_table("VectorObjectList")
        canvas_id = self.con.execute("select CanvasId from Layer where MainId=?", (layer.layer_id,)).fetchone()[0]
        vid = self._alloc("VectorObjectList")
        ext = new_external_id()
        self.con.execute("insert into VectorObjectList (MainId, CanvasId, LayerId, VectorData) values (?,?,?,?)",
                         (vid, canvas_id, layer.layer_id, ext))
        # Layer.VectorNormalStrokeIndex -> VectorObjectList.MainId (the object CSP renders)
        self.con.execute("update Layer set VectorNormalStrokeIndex=? where MainId=?", (vid, layer.layer_id))
        self._add_external(ext, b"")
        layer.object_row, layer.external_id = vid, ext

    # ------------------------------------------------------------ brushes
    def brushes(self):
        """(dict brush_id -> BrushStyle, dict graph_id -> control points)"""
        if self._brushes is None:
            self._brushes = brushmod.load_brushes(self.db)
        return self._brushes

    def stroke_width_factors(self, stroke: vector.Stroke) -> list[float]:
        """Effective per-point width multiplier as CSP renders it:
        brush SizeEffector(pressure, tilt, ...) * point width factor."""
        brushes, graphs = self.brushes()
        b = brushes.get(stroke.brush_id)
        return [(b.width_factor(graphs, p) if b else p.pressure) * p.width for p in stroke.points]

    def stroke_opacity_factors(self, stroke: vector.Stroke) -> list[float]:
        brushes, graphs = self.brushes()
        b = brushes.get(stroke.brush_id)
        return [(b.opacity_factor(graphs, p) if b else 1.0) * p.opacity for p in stroke.points]

    def default_brush_id(self) -> int:
        """Brush style whose width is linear in pressure (min 0 %) and whose
        opacity is not pressure-driven - ideal for importing strokes whose
        radius/opacity were computed elsewhere. Created if missing."""
        brushes, graphs = self.brushes()
        for bid, b in sorted(brushes.items()):
            s = b.size
            if (s.flags & 0xF0) == brushmod.PRESSURE and s.pressure_min == 0 and (b.opacity.flags & 0xF0) == 0 \
                    and graphs.get(s.pressure_graph) == [(0.0, 0.0), (1.0, 1.0)]:
                return bid
        return self.add_brush_style()

    def add_brush_style(self, overrides: dict | None = None) -> int:
        self._ensure_table("BrushEffectorGraphData")
        self._ensure_table("BrushStyle")
        con = self.con
        canvas_id = con.execute("select MainId from Canvas").fetchone()[0]
        mgr = dict(zip(self._cols("BrushStyleManager"), con.execute("select * from BrushStyleManager").fetchone()))
        brushes, graphs = self.brushes()
        # linear graph
        gid = next((k for k, v in graphs.items() if v == [(0.0, 0.0), (1.0, 1.0)]), None)
        if gid is None:
            gid = self._alloc("BrushEffectorGraphData")
            g = dict(schema.LINEAR_GRAPH, MainId=gid, CanvasId=canvas_id, NextIndex=0)
            self._insert("BrushEffectorGraphData", g)
            self._append_chain("BrushEffectorGraphData", "FirstGraphData", mgr, gid)
        bid = self._alloc("BrushStyle")
        size = brushmod.Effector(flags=1 | brushmod.PRESSURE, pressure_min=0.0, pressure_graph=gid)
        row = dict(schema.DEFAULT_BRUSH_STYLE, MainId=bid, CanvasId=canvas_id, NextIndex=0, SizeEffector=size.encode())
        row.update(overrides or {})
        self._insert("BrushStyle", row)
        self._append_chain("BrushStyle", "FirstBrushStyle", mgr, bid)
        self._brushes = None
        return bid

    def copy_brush_style(self, src: "ClipDocument", brush_id: int) -> int | None:
        """Copy BrushStyle `brush_id` (and the effector graphs it references)
        from another document. Returns the new id, or None if missing."""
        if not src._has_table("BrushStyle"):
            return None
        row = src.con.execute("select * from BrushStyle where MainId=?", (brush_id,)).fetchone()
        if row is None:
            return None
        self._ensure_table("BrushEffectorGraphData")
        self._ensure_table("BrushStyle")
        d = dict(zip(src._cols("BrushStyle"), row))
        canvas_id = self.con.execute("select MainId from Canvas").fetchone()[0]
        mgr = None
        gmap = {}

        def copy_graph(gid):
            if not gid:
                return 0
            if gid in gmap:
                return gmap[gid]
            g = src.con.execute("select * from BrushEffectorGraphData where MainId=?", (gid,)).fetchone()
            if g is None:
                return 0
            gd = dict(zip(src._cols("BrushEffectorGraphData"), g))
            # reuse an identical graph if present
            same = self.con.execute("select MainId from BrushEffectorGraphData where ControlPoints=? and ControlNumber=?",
                                    (gd["ControlPoints"], gd["ControlNumber"])).fetchone()
            if same:
                gmap[gid] = same[0]
                return same[0]
            nid = self._alloc("BrushEffectorGraphData")
            gd.update(MainId=nid, CanvasId=canvas_id, NextIndex=0)
            self._insert("BrushEffectorGraphData", gd)
            self._append_chain("BrushEffectorGraphData", "FirstGraphData", mgr, nid)
            gmap[gid] = nid
            return nid

        for col in [c for c in d if c.endswith("Effector") and isinstance(d[c], (bytes, bytearray))]:
            e = brushmod.Effector.parse(d[col])
            if e.flags & brushmod.PRESSURE:
                e.pressure_graph = copy_graph(e.pressure_graph)
            if e.flags & brushmod.TILT:
                e.tilt_graph = copy_graph(e.tilt_graph)
            enc = e.encode()
            if len(enc) == len(d[col]):
                d[col] = enc
        nid = self._alloc("BrushStyle")
        d.update(MainId=nid, CanvasId=canvas_id, NextIndex=0)
        self._insert("BrushStyle", d)
        self._append_chain("BrushStyle", "FirstBrushStyle", mgr, nid)
        self._brushes = None
        return nid

    def _append_chain(self, table, first_col, mgr, new_id):
        con = self.con
        first = con.execute(f"select {first_col} from BrushStyleManager").fetchone()[0]
        if not first:
            con.execute(f"update BrushStyleManager set {first_col}=?", (new_id,))
            return
        cur, seen = first, set()
        while cur and cur not in seen:
            seen.add(cur)
            nxt = con.execute(f"select NextIndex from {table} where MainId=?", (cur,)).fetchone()
            if not nxt or not nxt[0]:
                break
            cur = nxt[0]
        con.execute(f"update {table} set NextIndex=? where MainId=?", (new_id, cur))

    # ------------------------------------------------------------ saving
    def _invalidate_render_cache(self, layer_id: int) -> None:
        """Point every Offscreen of the layer at a fresh, non-existent
        external id. CSP then re-renders the layer from its vector data
        instead of re-using a bitmap it cached under the old offscreen id."""
        for mid, ext in self.con.execute("select MainId, BlockData from Offscreen where LayerId=?", (layer_id,)).fetchall():
            self._drop_external(as_bytes(ext))
            self.con.execute("update Offscreen set BlockData=? where MainId=?", (new_external_id(), mid))

    def save(self, path: str) -> None:
        for lid in sorted(self._dirty_layers):
            layer = self._layers[lid]
            self._ensure_vector_object(layer)
            body = vector.encode(layer.strokes)
            if self.clip.external_body(layer.external_id) is None:
                self._add_external(layer.external_id, body)
            else:
                self.clip.set_external_body(layer.external_id, body)
            self._invalidate_render_cache(lid)
        # composite caches of the folders above changed layers are stale too
        if self._dirty_layers:
            for (fid,) in self.con.execute("select MainId from Layer where LayerFolder<>0").fetchall():
                self._invalidate_render_cache(fid)
        self._dirty_layers.clear()
        self.db.commit()
        tmp = path + ".tmp"
        self.clip.save(tmp)
        os.replace(tmp, path)


def _csp_uuid() -> str:
    # CSP writes e.g. '6502eb0d97-e54a-d794-1f36-0b52efd09b' (10-4-4-4-10 hex)
    h = uuid.uuid4().hex
    return f"{h[:10]}-{h[10:14]}-{h[14:18]}-{h[18:22]}-{h[22:32]}"


def _resize_attribute(attr: bytes, w: int, h: int) -> bytes:
    """Rewrite width/height/grid and the BlockSize table of an Offscreen
    Attribute blob (blocks are 256x256; empty block record = 104 bytes)."""
    a = bytearray(attr)
    pname = "Parameter".encode("utf-16-be")
    i = a.find(struct.pack(">I", 9) + pname)
    o = i + 4 + len(pname)
    gw, gh = math.ceil(w / 256), math.ceil(h / 256)
    struct.pack_into(">4I", a, o, w, h, gw, gh)
    bname = "BlockSize".encode("utf-16-be")
    j = a.find(struct.pack(">I", 9) + bname)
    if j < 0:
        return bytes(a)
    k = j + 4 + len(bname)
    unit, cnt, elem = struct.unpack_from(">3I", a, k)
    head = a[:k] + struct.pack(">3I", unit, gw * gh, elem)
    out = head + struct.pack(">I", 104) * (gw * gh)
    struct.pack_into(">I", out, 12, 34 + 4 * gw * gh)
    return bytes(out)


def _blank_png(w: int, h: int) -> bytes:
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(bytes([0]) + bytes([255]) * (w * 3) for _ in range(h))
    return (bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

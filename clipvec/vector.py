"""Vector stroke body (VectorObjectList.VectorData -> CHNKExta body) codec.

Body = concatenation of stroke records, all big-endian:

Stroke header (88 bytes as written by CSP 1.13 / "legacy" 88 layout; a 92 byte
variant exists in newer builds - see docs/CLIP_FORMAT_SPEC.md):
  0  u32 header_size (88|92|76|96)
  4  u32 point_section_size (72; 76 for 92-byte headers)
  8  u32 point_size (88 | 104 quadratic | 120 cubic)
 12  u32 default_point_size (88)
 16  u32 point_count
 20  u32 flags  (0x1 normal, 0x4 ruler, 0x8 frame, 0x10 straight, 0x20 quad,
                 0x40 cubic, 0x80 spline, 0x100 closed, 0x1000 frame feature,
                 0x2000 free-hand pen)
 24  i32 bbox left, top, right, bottom (canvas px, includes brush radius)
 40  u32 main colour R,G,B  (8-bit value replicated: 0xAB -> 0xABABABAB)
 52  u32 sub colour R,G,B
 64  f64 stroke opacity (0..1)
 72  u32 brush style id (-> BrushStyle.MainId)
 --- 88 layout
 76  f64 brush radius in canvas px (= size/2)
 84  u32 stroke seed (increments like a ms tick between strokes; copied
          to point@80)
 --- 92 layout
 76  u32 unknown extra
 80  f64 brush radius
 88  u32 stroke seed

Point record (88 bytes + 16 per bezier control):
  0  f64 x, 8 f64 y
 16  i32 bbox l,t,r,b   (segment from this point to the next, with radius)
 32  u32 flags (0x1 = corner / hard vertex, also set on the last point)
 36  f32 pressure (0..1, the tablet pressure after CSP's global curve;
          brush SizeEffector/OpacityEffector are applied at render time)
 40  f32 tilt   (1 = pen perpendicular, 0 = flat; = 1 - angle/90deg)
 44  f32 velocity (normalised drawing speed)
 48  f32 unknown (0)
 52  f32 unknown (1)
 56  f32 width factor   (multiplies rendered width; "線幅修正" etc.)
 60  f32 opacity factor (multiplies rendered opacity)
 64  f32 x3 unknown (0)
 76  f32 random value (0..1, per point; drives brush random effectors)
 80  u32 per-point seed (normally == the header seed; differs per point for
          brushes with random effectors)
 84  u32 0
 88  f64 x,y control point(s) for quadratic/cubic strokes (belong to the
          segment starting at this point)
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

NORMAL, RULER, FRAME = 0x1, 0x4, 0x8
STRAIGHT, QUADRATIC, CUBIC, SPLINE = 0x10, 0x20, 0x40, 0x80
CLOSED, FRAME_FEATURE, FREEHAND = 0x100, 0x1000, 0x2000
POINT_CORNER = 0x1


@dataclass
class Point:
    x: float
    y: float
    pressure: float = 1.0
    tilt: float = 1.0
    velocity: float = 0.0
    width: float = 1.0          # width factor
    opacity: float = 1.0        # opacity factor
    random: float = 0.0
    seed: int | None = None     # None -> use the stroke seed
    corner: bool = False
    controls: list[tuple[float, float]] = field(default_factory=list)
    flags: int = 0              # raw flags (corner bit mirrored in .corner)
    bbox: tuple[int, int, int, int] | None = None
    raw: bytes | None = None    # original record for lossless round-trip

    def encode(self, size: int, timestamp: int) -> bytes:
        b = bytearray(self.raw[:size]) if self.raw and len(self.raw) >= size else bytearray(size)
        if not self.raw:
            struct.pack_into(">f", b, 52, 1.0)
        flags = (self.flags & ~POINT_CORNER) | (POINT_CORNER if self.corner else 0)
        struct.pack_into(">dd", b, 0, self.x, self.y)
        struct.pack_into(">4i", b, 16, *(self.bbox or (0, 0, 0, 0)))
        struct.pack_into(">I", b, 32, flags)
        struct.pack_into(">fff", b, 36, self.pressure, self.tilt, self.velocity)
        struct.pack_into(">ff", b, 56, self.width, self.opacity)
        struct.pack_into(">f", b, 76, self.random)
        struct.pack_into(">I", b, 80, (timestamp if self.seed is None else self.seed) & 0xFFFFFFFF)
        for i, (cx, cy) in enumerate(self.controls[: (size - 88) // 16]):
            struct.pack_into(">dd", b, 88 + 16 * i, cx, cy)
        return bytes(b)


@dataclass
class Stroke:
    points: list[Point] = field(default_factory=list)
    flags: int = NORMAL | SPLINE | FREEHAND
    color: tuple[int, int, int] = (0, 0, 0)          # 0..255
    sub_color: tuple[int, int, int] = (255, 255, 255)
    opacity: float = 1.0
    brush_id: int = 1
    radius: float = 2.0                               # canvas px
    timestamp: int = 0
    bbox: tuple[int, int, int, int] | None = None
    header_size: int = 88
    point_section_size: int = 72
    extra92: int = 0                                  # unknown word of 92-layout
    color_raw: tuple[int, ...] | None = None          # lossless raw words
    sub_color_raw: tuple[int, ...] | None = None
    raw_header: bytes | None = None

    # ------------------------------------------------------------ properties
    @property
    def kind(self) -> str:
        return {NORMAL: "normal", RULER: "ruler", FRAME: "frame"}.get(self.flags & 0xD, "unknown")

    @property
    def curve(self) -> str:
        return {STRAIGHT: "straight", QUADRATIC: "quadratic", CUBIC: "cubic", SPLINE: "spline"}.get(self.flags & 0xF0, "unknown")

    @property
    def closed(self) -> bool:
        return bool(self.flags & CLOSED)

    @property
    def point_size(self) -> int:
        return {QUADRATIC: 104, CUBIC: 120}.get(self.flags & 0xF0, 88)

    # ------------------------------------------------------------ geometry
    def sample(self, per_segment: int = 8):
        """Evaluate the stroke centre line as CSP renders it.
        Returns list of (x, y, pressure, width, opacity, tilt)."""
        pts = self.points
        n = len(pts)
        if n == 0:
            return []
        if n == 1:
            p = pts[0]
            return [(p.x, p.y, p.pressure, p.width, p.opacity, p.tilt)]
        segs = n if self.closed else n - 1
        out = []
        curve = self.flags & 0xF0
        for i in range(segs):
            a, b = pts[i], pts[(i + 1) % n]
            for s in range(per_segment):
                t = s / per_segment
                if curve == QUADRATIC and a.controls:
                    (cx, cy) = a.controls[0]
                    x = (1 - t) ** 2 * a.x + 2 * (1 - t) * t * cx + t * t * b.x
                    y = (1 - t) ** 2 * a.y + 2 * (1 - t) * t * cy + t * t * b.y
                elif curve == CUBIC and len(a.controls) >= 2:
                    (c1x, c1y), (c2x, c2y) = a.controls[:2]
                    mt = 1 - t
                    x = mt ** 3 * a.x + 3 * mt * mt * t * c1x + 3 * mt * t * t * c2x + t ** 3 * b.x
                    y = mt ** 3 * a.y + 3 * mt * mt * t * c1y + 3 * mt * t * t * c2y + t ** 3 * b.y
                elif curve == SPLINE:
                    p0 = _prev(pts, i, self.closed)
                    p3 = _next(pts, i + 1, self.closed)
                    # hard corners break tangent continuity
                    if a.corner:
                        p0 = (2 * a.x - b.x, 2 * a.y - b.y)
                    if b.corner:
                        p3 = (2 * b.x - a.x, 2 * b.y - a.y)
                    x, y = _catmull_rom(p0, (a.x, a.y), (b.x, b.y), p3, t)
                else:
                    x, y = a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t
                lerp = lambda u, v: u + (v - u) * t  # noqa: E731
                out.append((x, y, lerp(a.pressure, b.pressure), lerp(a.width, b.width),
                            lerp(a.opacity, b.opacity), lerp(a.tilt, b.tilt)))
        last = pts[0] if self.closed else pts[-1]
        out.append((last.x, last.y, last.pressure, last.width, last.opacity, last.tilt))
        return out

    def update_bboxes(self, margin: float | None = None) -> None:
        """Recompute point/stroke bounding boxes (canvas px, inclusive)."""
        # brush size effectors scale the radius by <= 1.5 (tilt max); width
        # factors multiply on top
        wmax = max([1.0] + [p.width for p in self.points])
        m = (self.radius * wmax * 1.6 + 2) if margin is None else margin
        pts = self.points
        n = len(pts)
        allb = []
        samples = self.sample(8)
        for i, p in enumerate(pts):
            if i < n - 1 or self.closed:
                seg = samples[i * 8:(i + 1) * 8 + 1]
            else:
                seg = [(p.x, p.y)]
            xs = [s[0] for s in seg]
            ys = [s[1] for s in seg]
            p.bbox = (math.floor(min(xs) - m), math.floor(min(ys) - m), math.ceil(max(xs) + m), math.ceil(max(ys) + m))
            allb.append(p.bbox)
        if allb:
            self.bbox = (min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb))

    # ------------------------------------------------------------ codec
    def encode(self) -> bytes:
        if self.bbox is None or any(p.bbox is None for p in self.points):
            self.update_bboxes()
        hs = self.header_size
        psz = self.point_size
        h = bytearray(self.raw_header[:hs]) if self.raw_header and len(self.raw_header) >= hs else bytearray(hs)
        struct.pack_into(">5I", h, 0, hs, self.point_section_size, psz, 88, len(self.points))
        struct.pack_into(">I", h, 20, self.flags)
        struct.pack_into(">4i", h, 24, *self.bbox)
        struct.pack_into(">3I", h, 40, *(self.color_raw or tuple(_rep(c) for c in self.color)))
        struct.pack_into(">3I", h, 52, *(self.sub_color_raw or tuple(_rep(c) for c in self.sub_color)))
        struct.pack_into(">d", h, 64, self.opacity)
        struct.pack_into(">I", h, 72, self.brush_id)
        if hs == 88:
            struct.pack_into(">d", h, 76, self.radius)
            struct.pack_into(">I", h, 84, self.timestamp & 0xFFFFFFFF)
        elif hs == 92:
            struct.pack_into(">I", h, 76, self.extra92)
            struct.pack_into(">d", h, 80, self.radius)
            struct.pack_into(">I", h, 88, self.timestamp & 0xFFFFFFFF)
        out = bytearray(h)
        for p in self.points:
            out += p.encode(psz, self.timestamp)
        return bytes(out)


def _rep(c: int) -> int:
    c = max(0, min(255, int(round(c))))
    return c * 0x01010101


def _prev(pts, i, closed):
    if i > 0:
        return (pts[i - 1].x, pts[i - 1].y)
    if closed:
        return (pts[-1].x, pts[-1].y)
    a, b = pts[0], pts[1]
    return (2 * a.x - b.x, 2 * a.y - b.y)


def _next(pts, j, closed):
    n = len(pts)
    if j + 1 < n:
        return (pts[j + 1].x, pts[j + 1].y)
    if closed:
        return (pts[(j + 1) % n].x, pts[(j + 1) % n].y)
    a, b = pts[j], pts[j - 1]
    return (2 * a.x - b.x, 2 * a.y - b.y)


def _catmull_rom(p0, p1, p2, p3, t):
    t2, t3 = t * t, t * t * t
    return tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in range(2))


# ---------------------------------------------------------------- decoding
def decode(body: bytes) -> list[Stroke]:
    strokes = []
    off = 0
    while off + 20 <= len(body):
        hs, ps, psz, dps, n = struct.unpack_from(">5I", body, off)
        if hs < 20 or (n and psz == 0):
            raise ValueError(f"bad stroke header at {off}")
        end = off + hs + n * psz
        if end > len(body):
            raise ValueError(f"stroke at {off} overruns body")
        h = body[off:off + hs]
        flags = struct.unpack_from(">I", h, 20)[0] if hs >= 24 else 0
        s = Stroke(flags=flags, header_size=hs, point_section_size=ps, raw_header=bytes(h))
        if hs >= 76:
            s.bbox = struct.unpack_from(">4i", h, 24)
            s.color_raw = struct.unpack_from(">3I", h, 40)
            s.sub_color_raw = struct.unpack_from(">3I", h, 52)
            s.color = tuple(v >> 24 for v in s.color_raw)
            s.sub_color = tuple(v >> 24 for v in s.sub_color_raw)
            s.opacity = struct.unpack_from(">d", h, 64)[0]
            s.brush_id = struct.unpack_from(">I", h, 72)[0]
        if hs == 88:
            s.radius = struct.unpack_from(">d", h, 76)[0]
            s.timestamp = struct.unpack_from(">I", h, 84)[0]
        elif hs == 92:
            s.extra92 = struct.unpack_from(">I", h, 76)[0]
            s.radius = struct.unpack_from(">d", h, 80)[0]
            s.timestamp = struct.unpack_from(">I", h, 88)[0]
        elif hs == 96:  # frame border
            s.brush_id = struct.unpack_from(">I", h, 76)[0]
            s.radius = struct.unpack_from(">d", h, 84)[0]
        for k in range(n):
            pr = body[off + hs + k * psz: off + hs + (k + 1) * psz]
            x, y = struct.unpack_from(">dd", pr, 0)
            p = Point(x, y, raw=bytes(pr))
            if psz >= 88:
                p.bbox = struct.unpack_from(">4i", pr, 16)
                p.flags = struct.unpack_from(">I", pr, 32)[0]
                p.corner = bool(p.flags & POINT_CORNER)
                p.pressure, p.tilt, p.velocity = struct.unpack_from(">fff", pr, 36)
                p.width, p.opacity = struct.unpack_from(">ff", pr, 56)
                p.random = struct.unpack_from(">f", pr, 76)[0]
                p.seed = struct.unpack_from(">I", pr, 80)[0]
                p.controls = [struct.unpack_from(">dd", pr, 88 + 16 * i) for i in range((psz - 88) // 16)]
            s.points.append(p)
        strokes.append(s)
        off = end
    return strokes


def encode(strokes: list[Stroke]) -> bytes:
    return b"".join(s.encode() for s in strokes)


def reconcile(new: Stroke, orig: Stroke, tol: float = 1e-3) -> Stroke:
    """Merge a stroke that went through a lossy editor (float32 positions,
    unknown fields dropped) with its original record: values that did not
    change beyond `tol` are restored bit-exactly and unknown bytes are kept.
    Returns `new` (modified in place)."""
    if len(new.points) != len(orig.points):
        return new
    moved = False
    for p, q in zip(new.points, orig.points):
        p.raw = q.raw
        if q.raw and len(q.raw) >= 36:
            p.flags = (p.flags & POINT_CORNER) | (struct.unpack_from(">I", q.raw, 32)[0] & ~POINT_CORNER)
        if abs(p.x - q.x) <= tol and abs(p.y - q.y) <= tol:
            p.x, p.y = q.x, q.y
        else:
            moved = True
        if len(p.controls) == len(q.controls) and all(abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
                                                     for a, b in zip(p.controls, q.controls)):
            p.controls = list(q.controls)
        elif p.controls or q.controls:
            moved = True
        for attr in ("pressure", "tilt", "velocity", "random", "width", "opacity"):
            if abs(getattr(p, attr) - getattr(q, attr)) <= 1e-5:
                setattr(p, attr, getattr(q, attr))
            else:
                moved = True  # geometry extent may change -> new bboxes
        if p.seed is None:
            p.seed = q.seed
    new.raw_header = orig.raw_header
    new.header_size, new.point_section_size, new.extra92 = orig.header_size, orig.point_section_size, orig.extra92
    if abs(new.radius - orig.radius) <= 1e-9 * max(1.0, orig.radius) or abs(new.radius - orig.radius) <= 1e-6:
        new.radius = orig.radius
    if abs(new.opacity - orig.opacity) <= 1e-6:
        new.opacity = orig.opacity
    if new.color == orig.color:
        new.color_raw, new.sub_color_raw = orig.color_raw, orig.sub_color_raw
    if not moved and abs(new.radius - orig.radius) <= 1e-9:
        new.bbox = orig.bbox
        for p, q in zip(new.points, orig.points):
            p.bbox = q.bbox
    else:
        new.update_bboxes()
    return new

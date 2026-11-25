"""BrushStyle effector blobs and width evaluation.

Effector blob (SizeEffector / OpacityEffector / FlowEffector ..., big-endian):
    u32 flags   bit0 = 1 (always), 0x10 pressure, 0x20 tilt, 0x40 velocity,
                0x80 random
    if pressure: f32 min, u32 graph_id      (BrushEffectorGraphData.MainId)
    if tilt:     f32 min, u32 graph_id, f32 max
    if velocity: f32 min
    if random:   f32 min
Graph (BrushEffectorGraphData.ControlPoints): ControlNumber x (f64 x, f64 y).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

PRESSURE, TILT, VELOCITY, RANDOM = 0x10, 0x20, 0x40, 0x80


@dataclass
class Effector:
    flags: int = 1
    pressure_min: float = 0.0
    pressure_graph: int = 0
    tilt_min: float = 1.0
    tilt_graph: int = 0
    tilt_max: float = 1.0
    velocity_min: float = 1.0
    random_min: float = 1.0

    @classmethod
    def parse(cls, blob: bytes | None) -> "Effector":
        e = cls()
        if not blob or len(blob) < 4:
            return e
        e.flags = struct.unpack_from(">I", blob, 0)[0]
        o = 4
        try:
            if e.flags & PRESSURE:
                e.pressure_min, e.pressure_graph = struct.unpack_from(">fI", blob, o); o += 8
            if e.flags & TILT:
                e.tilt_min, e.tilt_graph, e.tilt_max = struct.unpack_from(">fIf", blob, o); o += 12
            if e.flags & VELOCITY:
                e.velocity_min = struct.unpack_from(">f", blob, o)[0]; o += 4
            if e.flags & RANDOM:
                e.random_min = struct.unpack_from(">f", blob, o)[0]; o += 4
        except struct.error:
            pass
        return e

    def encode(self) -> bytes:
        b = struct.pack(">I", self.flags)
        if self.flags & PRESSURE:
            b += struct.pack(">fI", self.pressure_min, self.pressure_graph)
        if self.flags & TILT:
            b += struct.pack(">fIf", self.tilt_min, self.tilt_graph, self.tilt_max)
        if self.flags & VELOCITY:
            b += struct.pack(">f", self.velocity_min)
        if self.flags & RANDOM:
            b += struct.pack(">f", self.random_min)
        return b

    def factor(self, graphs: dict[int, list], pressure=1.0, tilt=1.0, velocity=0.0, random=0.0) -> float:
        """Multiplier (0..~1.5) produced by this effector for one point.
        Pressure and tilt follow CSP's documented UI semantics; velocity and
        random are approximations (their exact curves are not stored)."""
        f = 1.0
        if self.flags & PRESSURE:
            g = eval_graph(graphs.get(self.pressure_graph), pressure)
            f *= self.pressure_min + (1.0 - self.pressure_min) * g
        if self.flags & TILT:
            # graph x: 0 = pen flat ... 1 = perpendicular; output 0..1 maps min..max
            g = eval_graph(graphs.get(self.tilt_graph), tilt)
            f *= self.tilt_min + (self.tilt_max - self.tilt_min) * g
        if self.flags & VELOCITY:
            v = max(0.0, min(1.0, velocity))
            f *= self.velocity_min + (1.0 - self.velocity_min) * (1.0 - v)
        if self.flags & RANDOM:
            f *= self.random_min + (1.0 - self.random_min) * random
        return f


def parse_graph(blob: bytes, count: int | None = None) -> list[tuple[float, float]]:
    n = len(blob) // 16 if count is None else count
    return [struct.unpack_from(">dd", blob, 16 * i) for i in range(n)]


def eval_graph(pts, x: float) -> float:
    """Piece-wise linear evaluation (CSP draws a smooth curve through the
    control points in its UI; linear is exact for the default graphs)."""
    if not pts:
        return x
    x = max(0.0, min(1.0, x))
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


@dataclass
class BrushStyle:
    id: int
    size: Effector = field(default_factory=Effector)
    opacity: Effector = field(default_factory=Effector)
    row: dict | None = None

    def width_factor(self, graphs, p) -> float:
        return self.size.factor(graphs, p.pressure, p.tilt, p.velocity, p.random)

    def opacity_factor(self, graphs, p) -> float:
        return self.opacity.factor(graphs, p.pressure, p.tilt, p.velocity, p.random)


def load_brushes(db) -> tuple[dict[int, BrushStyle], dict[int, list]]:
    tables = db.tables()
    graphs = {}
    if "BrushEffectorGraphData" in tables:
        for r in db.q("select MainId, ControlNumber, ControlPoints from BrushEffectorGraphData"):
            graphs[r[0]] = parse_graph(r[2] or b"", r[1])
    brushes = {}
    if "BrushStyle" in tables:
        for r in db.q("select * from BrushStyle"):
            d = dict(r)
            brushes[d["MainId"]] = BrushStyle(d["MainId"], Effector.parse(d.get("SizeEffector")),
                                              Effector.parse(d.get("OpacityEffector")), d)
    return brushes, graphs

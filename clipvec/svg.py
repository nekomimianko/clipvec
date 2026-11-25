"""SVG export that keeps pressure-driven width: every stroke becomes a
filled outline (left/right offset of the CSP centre line + round caps)."""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

from .document import ClipDocument


def _outline(samples, radii):
    n = len(samples)
    if n == 1:
        (x, y), r = samples[0], radii[0]
        return f"M{x - r:.2f},{y:.2f}a{r:.2f},{r:.2f} 0 1,0 {2 * r:.2f},0a{r:.2f},{r:.2f} 0 1,0 {-2 * r:.2f},0Z"
    left, right = [], []
    for i in range(n):
        x0, y0 = samples[max(i - 1, 0)]
        x1, y1 = samples[min(i + 1, n - 1)]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        x, y = samples[i]
        r = radii[i]
        left.append((x + nx * r, y + ny * r))
        right.append((x - nx * r, y - ny * r))
    d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in left)
    r_end = max(radii[-1], 0.01)
    d += f" A{r_end:.2f},{r_end:.2f} 0 0,0 {right[-1][0]:.2f},{right[-1][1]:.2f}"
    d += " L" + " L".join(f"{x:.2f},{y:.2f}" for x, y in reversed(right))
    r0 = max(radii[0], 0.01)
    d += f" A{r0:.2f},{r0:.2f} 0 0,0 {left[0][0]:.2f},{left[0][1]:.2f}Z"
    return d


def document_to_svg(doc: ClipDocument, per_segment: int = 8) -> str:
    c = doc.canvas
    w, h = c["width"], c["height"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" viewBox="0 0 {w:g} {h:g}">']
    for layer in doc.vector_layers():
        vis = "" if layer.visible else ' visibility="hidden"'
        out.append(f'<g id="layer{layer.layer_id}" data-name="{escape(layer.name)}" opacity="{layer.opacity:.3f}"{vis}>')
        for s in layer.strokes:
            wf = doc.stroke_width_factors(s)
            of = doc.stroke_opacity_factors(s)
            # per-point factors -> interpolated per sample (same parametrisation as Stroke.sample)
            pts = s.points
            s_orig = [(p.width, p.pressure) for p in pts]
            for p, f in zip(pts, wf):
                p._w = f
            samples = s.sample(per_segment)
            n = len(pts)
            radii = []
            segs = n if s.closed else n - 1
            for i in range(max(segs, 0)):
                a, b = pts[i]._w, pts[(i + 1) % n]._w
                for k in range(per_segment):
                    radii.append(s.radius * (a + (b - a) * k / per_segment))
            radii.append(s.radius * (pts[0]._w if s.closed else pts[-1]._w))
            radii = radii[:len(samples)] or [s.radius * wf[0]]
            opacity = s.opacity * (sum(of) / len(of) if of else 1.0)
            col = "#%02x%02x%02x" % tuple(s.color)
            xy = [(x, y) for x, y, *_ in samples]
            # split at hard corners so each piece gets round caps (= round joins)
            cuts = [i * per_segment for i, p in enumerate(pts) if p.corner and 0 < i < n - 1] if s.curve == "spline"                 else ([i * per_segment for i in range(1, n - 1)] if s.curve == "straight" else [])
            bounds = [0] + cuts + [len(xy) - 1]
            d = "".join(_outline(xy[a:b + 1], radii[a:b + 1]) for a, b in zip(bounds, bounds[1:]) if b >= a)
            out.append(f'<path d="{d}" fill="{col}" fill-opacity="{opacity:.3f}" fill-rule="nonzero"/>')
            del s_orig
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)

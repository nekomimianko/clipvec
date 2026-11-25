import sys, struct, numpy as np
sys.path.insert(0, '.')
from clipvec.container import ClipFile
from clipvec.raster import layer_render
path = sys.argv[1]
c = ClipFile.load(path); db = c.open_db()
if len(sys.argv) > 2:
    A = np.load(sys.argv[2]) * 255
    ox, oy = float(sys.argv[3]), float(sys.argv[4])
    A2 = np.zeros((1100, 1100)); 
    # shift so that canvas coords index A2
    import scipy.ndimage as nd
    A = nd.shift(A, (-oy, -ox), order=1)[:1100, :1100]
else:
    img = layer_render(c, db, 4); A = img[..., 3].astype(float)
body = c.external_body(db.q('select VectorData from VectorObjectList')[0][0])
off = 0; strokes = []
while off < len(body):
    hs, ps, pz, dp, n = struct.unpack_from('>5I', body, off)
    strokes.append(np.array([struct.unpack_from('>dd', body, off + hs + k * pz) for k in range(n)]))
    off += hs + n * pz

def centroid_near(p, d, r=6):
    # sample the line cross-section perpendicular to direction d at point p
    nrm = np.array([-d[1], d[0]]) / np.linalg.norm(d)
    ts = np.linspace(-r * 2, r * 2, 81)
    vals = []
    for t in ts:
        q = p + nrm * t
        x, y = q
        x0, y0 = int(np.floor(x)), int(np.floor(y))
        fx, fy = x - x0, y - y0
        v = (A[y0, x0] * (1 - fx) * (1 - fy) + A[y0, x0 + 1] * fx * (1 - fy) + A[y0 + 1, x0] * (1 - fx) * fy + A[y0 + 1, x0 + 1] * fx * fy)
        vals.append(v)
    vals = np.array(vals)
    if vals.sum() == 0: return None
    return (ts * vals).sum() / vals.sum()  # signed offset of real center from p along normal

def cr(p0, p1, p2, p3, t, alpha=0.0):
    if alpha == 0:
        return 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3)
    def tj(ti, a, b): return ti + np.linalg.norm(b - a) ** alpha
    t0 = 0; t1 = tj(t0, p0, p1); t2 = tj(t1, p1, p2); t3 = tj(t2, p2, p3)
    tt = t1 + (t2 - t1) * t
    A1 = (t1 - tt) / (t1 - t0) * p0 + (tt - t0) / (t1 - t0) * p1
    A2 = (t2 - tt) / (t2 - t1) * p1 + (tt - t1) / (t2 - t1) * p2
    A3 = (t3 - tt) / (t3 - t2) * p2 + (tt - t2) / (t3 - t2) * p3
    B1 = (t2 - tt) / (t2 - t0) * A1 + (tt - t0) / (t2 - t0) * A2
    B2 = (t3 - tt) / (t3 - t1) * A2 + (tt - t1) / (t3 - t1) * A3
    return (t2 - tt) / (t2 - t1) * B1 + (tt - t1) / (t2 - t1) * B2

def bspline(p0, p1, p2, p3, t):
    return ((1 - t) ** 3 * p0 + (3 * t ** 3 - 6 * t ** 2 + 4) * p1 + (-3 * t ** 3 + 3 * t ** 2 + 3 * t + 1) * p2 + t ** 3 * p3) / 6

for si, P in enumerate(strokes):
    n = len(P)
    # errors at stored points
    errs = []
    for k in range(1, n - 1):
        e = centroid_near(P[k], P[k + 1] - P[k - 1]); errs.append(e)
    print(f"S{si}: stored point offset from rendered centre: mean|e|={np.nanmean(np.abs([e for e in errs if e is not None])):.3f}")
    models = {'linear': lambda a, b, cc, d, t: b + (cc - b) * t, 'cr_uniform': lambda a, b, cc, d, t: cr(a, b, cc, d, t, 0),
              'cr_centripetal': lambda a, b, cc, d, t: cr(a, b, cc, d, t, 0.5), 'cr_chordal': lambda a, b, cc, d, t: cr(a, b, cc, d, t, 1.0),
              'bspline': bspline}
    res = {m: [] for m in models}
    for k in range(n - 1):
        p1 = P[k]; p2 = P[k + 1]; p0 = P[k - 1] if k > 0 else 2 * p1 - p2; p3 = P[k + 2] if k + 2 < n else 2 * p2 - p1
        if np.linalg.norm(p2 - p1) < 40: continue
        for m, f in models.items():
            for t in (0.25, 0.5, 0.75):
                q = f(p0, p1, p2, p3, t); q2 = f(p0, p1, p2, p3, t + 0.01)
                e = centroid_near(q, q2 - q)
                res[m].append(abs(e) if e is not None else 99)
    for m, v in res.items():
        if v: print(f"   {m:15s} mean|err| {np.mean(v):.3f} max {np.max(v):.3f} (n={len(v)})")

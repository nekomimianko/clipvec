import sys; sys.path.insert(0, '.')
from clipvec.container import ClipFile
a = ClipFile.load(sys.argv[1]).open_db(); b = ClipFile.load(sys.argv[2]).open_db()
skip = set(sys.argv[3:])
for t in sorted(set(a.tables()) | set(b.tables())):
    if t in skip: continue
    ca = a.columns(t) if t in a.tables() else []; cb = b.columns(t) if t in b.tables() else []
    if ca != cb: print(f"## {t} columns differ: -{set(ca)-set(cb)} +{set(cb)-set(ca)}")
    key = '_PW_ID' if '_PW_ID' in ca else None
    ra = {r[key] if key else i: dict(r) for i, r in enumerate(a.q(f'select * from {t}'))} if ca else {}
    rb = {r[key] if key else i: dict(r) for i, r in enumerate(b.q(f'select * from {t}'))} if cb else {}
    for k in sorted(set(ra) | set(rb), key=str):
        x, y = ra.get(k), rb.get(k)
        if x == y: continue
        if x is None or y is None: print(f"{t}[{k}] {'added' if x is None else 'removed'}: { {c:v for c,v in (y or x).items() if v not in (None,0,b'','')} }"); continue
        d = {c: (x.get(c), y.get(c)) for c in set(x) | set(y) if x.get(c) != y.get(c)}
        print(f"{t}[{k}]", {c: (v[0][:40] if isinstance(v[0], bytes) else v[0], v[1][:40] if isinstance(v[1], bytes) else v[1]) for c, v in d.items()})

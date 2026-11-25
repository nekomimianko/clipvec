"""Offscreen (tiled raster) decoding for .clip files.

BlockData external body = sequence of
    u32 block_len, u32 19, "BlockDataBeginChunk"(UTF-16BE), u32 index,
    12 bytes attrs (u16 channels? , u16 0, u32 256, u32 256), u32 has_data,
    [u32 outer_len(=inner+4), u32 inner_len (LITTLE endian), zlib],
    u32 17, "BlockDataEndChunk"(UTF-16BE)
followed by BlockStatus / BlockCheckSum tables.
A 5-channel 256x256 tile inflates to 65536 alpha bytes + 65536*4 BGRx bytes.
"""
import struct
import zlib

import numpy as np

BEGIN = "BlockDataBeginChunk".encode("utf-16-be")
END = "BlockDataEndChunk".encode("utf-16-be")
STATUS = "BlockStatus".encode("utf-16-be")
CHECKSUM = "BlockCheckSum".encode("utf-16-be")


def parse_attribute(attr: bytes):
    o = 16
    n = struct.unpack_from(">I", attr, o)[0]
    o += 4 + 2 * n  # "Parameter"
    w, h, gw, gh = struct.unpack_from(">4I", attr, o)
    return w, h, gw, gh


def parse_blocks(body: bytes):
    """returns list of (index, attrs12, payload bytes|None)"""
    i = 0
    out = []
    while i < len(body):
        if body[i:i + 4 + len(STATUS)] == struct.pack(">I", 11) + STATUS:
            cnt = struct.unpack_from(">I", body, i + 4 + len(STATUS) + 4)[0]
            i += 4 + len(STATUS) + 12 + cnt * 4
            continue
        if body[i:i + 4 + len(CHECKSUM)] == struct.pack(">I", 13) + CHECKSUM:
            cnt = struct.unpack_from(">I", body, i + 4 + len(CHECKSUM) + 4)[0]
            i += 4 + len(CHECKSUM) + 12 + cnt * 4
            continue
        blen = struct.unpack_from(">I", body, i)[0]
        assert body[i + 8:i + 8 + len(BEGIN)] == BEGIN, "bad block at %d" % i
        b = body[i + 8 + len(BEGIN): i + blen - (4 + len(END))]
        idx = struct.unpack_from(">I", b, 0)[0]
        attrs = b[4:16]
        has = struct.unpack_from(">I", b, 16)[0]
        payload = None
        if has:
            inner = struct.unpack_from("<I", b, 24)[0]
            payload = zlib.decompress(bytes(b[28:28 + inner]))
        out.append((idx, attrs, payload))
        i += blen
    return out


def decode_offscreen(attr: bytes, body: bytes | None) -> np.ndarray:
    """Return an RGBA uint8 array (h, w, 4) for a 5-channel offscreen."""
    w, h, gw, gh = parse_attribute(attr)
    img = np.zeros((gh * 256, gw * 256, 4), np.uint8)
    if body:
        for idx, attrs, payload in parse_blocks(body):
            if payload is None:
                continue
            by, bx = divmod(idx, gw)
            ch = struct.unpack_from(">H", attrs, 0)[0]
            if ch == 5 and len(payload) == 5 * 65536:
                a = np.frombuffer(payload, np.uint8, 65536).reshape(256, 256)
                bgrx = np.frombuffer(payload, np.uint8, 65536 * 4, 65536).reshape(256, 256, 4)
                tile = np.dstack([bgrx[..., 2], bgrx[..., 1], bgrx[..., 0], a])
            elif ch == 1 and len(payload) == 65536:
                a = np.frombuffer(payload, np.uint8).reshape(256, 256)
                tile = np.dstack([a, a, a, np.full_like(a, 255)])
            else:
                continue
            img[by * 256:(by + 1) * 256, bx * 256:(bx + 1) * 256] = tile
    return img[:h, :w]


def layer_render(clip, db, layer_id: int) -> np.ndarray | None:
    r = db.q("select LayerRenderMipmap from Layer where MainId=?", (layer_id,))
    if not r or not r[0][0]:
        return None
    mm = db.q("select BaseMipmapInfo from Mipmap where MainId=?", (r[0][0],))[0][0]
    off = db.q("select Offscreen from MipmapInfo where MainId=?", (mm,))[0][0]
    row = db.q("select Attribute, BlockData from Offscreen where MainId=?", (off,))[0]
    return decode_offscreen(row["Attribute"], clip.external_body(row["BlockData"]))


# ------------------------------------------------------------- encoding
# Inverse of the decoder above, following docs/CLIP_FORMAT_SPEC.md §6.

def _section(name: str, data: bytes) -> bytes:
    return struct.pack(">I", len(name)) + name.encode("utf-16-be") + data


def make_attribute(w: int, h: int, block_sizes: list[int]) -> bytes:
    """Build an Offscreen.Attribute BLOB for a w x h image whose tiles
    have the given BlockData record lengths."""
    gw = (w + 255) // 256
    gh = (h + 255) // 256
    param = struct.pack(">20I", w, h, gw, gh, 33, 1, 4, 5, 65536, 4, 1024,
                        1, 256, 65536, 256, 256, 8, 8, 0, 0)
    init = struct.pack(">5I", 20, 0, 0, 0, 4)
    bsz = struct.pack(">3I", 12, len(block_sizes), 4) + b"".join(
        struct.pack(">I", s) for s in block_sizes)
    p = _section("Parameter", param)
    i = _section("InitColor", init)
    b = _section("BlockSize", bsz)
    return struct.pack(">4I", 16, len(p), len(i), len(b)) + p + i + b


def encode_offscreen(rgba: np.ndarray):
    """Encode an RGBA uint8 array (h, w, 4) as (Attribute, BlockData body).

    Tiles are 256x256, 5 channels (alpha plane, then per-pixel B,G,R,x).
    Fully transparent tiles are stored as empty records. The layout is
    the exact inverse of decode_offscreen/parse_blocks above."""
    h, w = rgba.shape[:2]
    gw = (w + 255) // 256
    gh = (h + 255) // 256
    records = []
    checksums = []
    for by in range(gh):
        for bx in range(gw):
            idx = by * gw + bx
            tile = np.zeros((256, 256, 4), np.uint8)
            src = rgba[by * 256:(by + 1) * 256, bx * 256:(bx + 1) * 256]
            tile[:src.shape[0], :src.shape[1]] = src
            head = struct.pack(">IHHII", idx, 5, 0, 256, 256)
            if not tile.any():
                rec = (struct.pack(">I", 104) + struct.pack(">I", 19) + BEGIN
                       + head + struct.pack(">I", 0)
                       + struct.pack(">I", 17) + END)
                checksums.append(0)
            else:
                alpha = tile[..., 3].tobytes()
                bgrx = np.dstack([tile[..., 2], tile[..., 1], tile[..., 0],
                                  np.zeros((256, 256), np.uint8)]).tobytes()
                z = zlib.compress(alpha + bgrx, 1)
                inner = len(z)
                rec = (struct.pack(">I", 112 + inner) + struct.pack(">I", 19)
                       + BEGIN + head + struct.pack(">I", 1)
                       + struct.pack(">I", inner + 4)
                       + struct.pack("<I", inner) + z
                       + struct.pack(">I", 17) + END)
                checksums.append(zlib.adler32(struct.pack("<I", inner) + z)
                                 & 0xFFFFFFFF)
            records.append(rec)
    n = gw * gh
    status = (struct.pack(">I", 11) + STATUS
              + struct.pack(">3I", 12, n, 4) + struct.pack(f">{n}I", *([1] * n)))
    csum = (struct.pack(">I", 13) + CHECKSUM
            + struct.pack(">3I", 12, n, 4)
            + struct.pack(f">{n}I", *checksums))
    body = b"".join(records) + status + csum
    return make_attribute(w, h, [len(r) for r in records]), body


def halve(rgba: np.ndarray) -> np.ndarray:
    """One 2x box-downscale step (premultiplied average over the real
    pixels of each 2x2 block; edge blocks average fewer pixels).
    Output size is ceil(h/2) x ceil(w/2)."""
    h, w = rgba.shape[:2]
    nh, nw = (h + 1) // 2, (w + 1) // 2
    f = rgba.astype(np.float64)
    a = f[..., 3:4] / 255.0
    prem = np.concatenate([f[..., :3] * a, f[..., 3:4]], axis=2)
    pad = np.zeros((nh * 2, nw * 2, 4))
    pad[:h, :w] = prem
    cnt = np.zeros((nh * 2, nw * 2))
    cnt[:h, :w] = 1.0
    sums = pad.reshape(nh, 2, nw, 2, 4).sum(axis=(1, 3))
    cnts = cnt.reshape(nh, 2, nw, 2).sum(axis=(1, 3))
    alpha = sums[..., 3] / cnts
    asum = sums[..., 3:4]
    rgb = np.where(asum > 0, sums[..., :3] / np.maximum(asum, 1e-12), 0.0)
    out = np.concatenate([rgb, alpha[..., None]], axis=2)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)

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

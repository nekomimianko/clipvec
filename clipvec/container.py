"""CLIP STUDIO PAINT .clip container (CSFCHUNK) reader / writer.

Layout (all integers big-endian):
    "CSFCHUNK" u64 file_size u64 first_chunk_offset(=24)
    repeated: tag[8] u64 payload_len payload
        CHNKHead: u64 0x100, u64 abs offset of CHNKSQLi, u64 16, 16-byte file id
        CHNKExta: u64 40, "extrnlid"+32 hex, u64 body_len, body
        CHNKSQLi: complete SQLite 3 database
        CHNKFoot: empty
The SQLite table ExternalChunk(ExternalID, Offset) indexes every CHNKExta by
absolute file offset, so the writer must rewrite it whenever sizes change.
"""
from __future__ import annotations

import os
import sqlite3
import struct
import tempfile
from dataclasses import dataclass, field


@dataclass
class Chunk:
    tag: bytes
    payload: bytes
    offset: int = 0  # absolute offset in the source file

    @property
    def external_id(self) -> bytes | None:
        if self.tag != b"CHNKExta":
            return None
        n = struct.unpack_from(">Q", self.payload, 0)[0]
        return self.payload[8:8 + n]

    @property
    def external_body(self) -> bytes:
        n = struct.unpack_from(">Q", self.payload, 0)[0]
        blen = struct.unpack_from(">Q", self.payload, 8 + n)[0]
        start = 16 + n
        return self.payload[start:start + blen]


def make_external_payload(ext_id: bytes, body: bytes) -> bytes:
    return struct.pack(">Q", len(ext_id)) + ext_id + struct.pack(">Q", len(body)) + body


@dataclass
class ClipFile:
    chunks: list[Chunk] = field(default_factory=list)

    # ---------------------------------------------------------------- reading
    @classmethod
    def load(cls, path: str) -> "ClipFile":
        with open(path, "rb") as f:
            data = f.read()
        return cls.parse(data)

    @classmethod
    def parse(cls, data: bytes) -> "ClipFile":
        if data[:8] != b"CSFCHUNK":
            raise ValueError("not a CSFCHUNK .clip file")
        size, first = struct.unpack_from(">QQ", data, 8)
        pos = first
        chunks = []
        while pos < len(data):
            tag = data[pos:pos + 8]
            n = struct.unpack_from(">Q", data, pos + 8)[0]
            chunks.append(Chunk(tag, data[pos + 16:pos + 16 + n], pos))
            pos += 16 + n
            if tag == b"CHNKFoot":
                break
        return cls(chunks)

    def chunk(self, tag: bytes) -> Chunk:
        return next(c for c in self.chunks if c.tag == tag)

    def externals(self) -> dict[bytes, Chunk]:
        return {c.external_id: c for c in self.chunks if c.tag == b"CHNKExta"}

    def external_body(self, ext_id: bytes) -> bytes | None:
        c = self.externals().get(as_bytes(ext_id))
        return None if c is None else c.external_body

    def set_external_body(self, ext_id: bytes, body: bytes) -> None:
        c = self.externals()[as_bytes(ext_id)]
        c.payload = make_external_payload(as_bytes(ext_id), body)

    def add_external(self, ext_id: bytes, body: bytes) -> None:
        """Insert a new CHNKExta right before CHNKSQLi."""
        i = next(i for i, c in enumerate(self.chunks) if c.tag == b"CHNKSQLi")
        self.chunks.insert(i, Chunk(b"CHNKExta", make_external_payload(ext_id, body)))

    # ---------------------------------------------------------------- sqlite
    def sqlite_bytes(self) -> bytes:
        return self.chunk(b"CHNKSQLi").payload

    def open_db(self) -> "ClipDB":
        return ClipDB(self)

    # ---------------------------------------------------------------- writing
    def serialize(self) -> bytes:
        # 1) compute chunk offsets
        pos = 24
        offsets = []
        for c in self.chunks:
            offsets.append(pos)
            pos += 16 + len(c.payload)
        total = pos
        # 2) fix ExternalChunk offsets inside SQLite, CHNKHead SQLi offset
        ext_offsets = {c.external_id: o for c, o in zip(self.chunks, offsets) if c.tag == b"CHNKExta"}
        sql_chunk = self.chunk(b"CHNKSQLi")
        sql_chunk.payload = _rewrite_external_offsets(sql_chunk.payload, ext_offsets)
        # sqlite size may change -> recompute
        pos = 24
        offsets = []
        for c in self.chunks:
            offsets.append(pos)
            pos += 16 + len(c.payload)
        total = pos
        sql_off = offsets[self.chunks.index(sql_chunk)]
        head = self.chunk(b"CHNKHead")
        hp = bytearray(head.payload)
        struct.pack_into(">Q", hp, 8, sql_off)
        head.payload = bytes(hp)
        out = bytearray(b"CSFCHUNK" + struct.pack(">QQ", total, 24))
        for c in self.chunks:
            out += c.tag + struct.pack(">Q", len(c.payload)) + c.payload
        assert len(out) == total
        return bytes(out)

    def save(self, path: str) -> None:
        data = self.serialize()
        with open(path, "wb") as f:
            f.write(data)


def as_bytes(v) -> bytes:
    return v.encode('ascii') if isinstance(v, str) else bytes(v)


def _rewrite_external_offsets(db_bytes: bytes, ext_offsets: dict[bytes, int]) -> bytes:
    with _TempDB(db_bytes) as con:
        cur = con.execute("SELECT ExternalID, Offset FROM ExternalChunk")
        rows = cur.fetchall()
        changed = False
        for ext_id, off in rows:
            new = ext_offsets.get(as_bytes(ext_id))
            if new is not None and new != off:
                con.execute("UPDATE ExternalChunk SET Offset=? WHERE ExternalID=?", (new, ext_id))
                changed = True
        if not changed:
            return db_bytes
        return con.dump_bytes()


class _TempDB:
    """Opens SQLite bytes in a temp file (sqlite3.deserialize is not available
    everywhere) and allows dumping back to bytes."""

    def __init__(self, data: bytes):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self.con = sqlite3.connect(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def execute(self, *a):
        return self.con.execute(*a)

    def dump_bytes(self) -> bytes:
        self.con.commit()
        self.con.close()
        self.con = None
        with open(self.path, "rb") as f:
            return f.read()

    def close(self):
        if self.con is not None:
            self.con.close()
            self.con = None
        try:
            os.remove(self.path)
        except OSError:
            pass


class ClipDB:
    """Read/write access to the embedded SQLite. Call commit() to write the
    database back into the owning ClipFile."""

    def __init__(self, clip: ClipFile):
        self.clip = clip
        self._t = _TempDB(clip.sqlite_bytes())
        self.con = self._t.con
        self.con.row_factory = sqlite3.Row

    def q(self, sql, args=()):
        return self.con.execute(sql, args).fetchall()

    def columns(self, table):
        return [r[1] for r in self.con.execute(f"PRAGMA table_info({table})")]

    def tables(self):
        return [r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def commit(self):
        data = self._t.dump_bytes()
        self.clip.chunk(b"CHNKSQLi").payload = data
        self._t = _TempDB(data)
        self.con = self._t.con
        self.con.row_factory = sqlite3.Row

    def close(self):
        self._t.close()

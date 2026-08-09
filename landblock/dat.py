"""Minimal reader for Asheron's Call .dat files (portal / cell / language).

Two container generations, autodetected by open_dat():

* `Dat` -- the 2005 (Throne of Destiny) container, used through end of
  retail. Header at 0x140, 24-byte directory entries. Transcribed from
  ACEmulator's ACE.DatLoader.
* `OldDat` -- the original 1999-2005 container. Header at 0x12C, 12-byte
  directory entries {id, offset, size} with no compression flags or dates.
  Transcribed from the PhatSDK PRE_TOD branches (DATDisk.h/.cpp).

Both expose the same surface: .files, .get(id), .ids_in(lo, hi), .era.
"""
import struct

HEADER_OFFSET = 0x140
OLD_HEADER_OFFSET = 0x12C
MAGIC = 0x5442                 # 'BT'


def open_dat(path):
    """Open either dat generation, sniffing the header magic."""
    with open(path, 'rb') as f:
        f.seek(OLD_HEADER_OFFSET)
        old_magic = struct.unpack('<I', f.read(4))[0]
        f.seek(HEADER_OFFSET)
        new_magic = struct.unpack('<I', f.read(4))[0]
    if old_magic == MAGIC:
        return OldDat(path)
    if new_magic == MAGIC:
        return Dat(path)
    raise ValueError('%s: no dat header magic at 0x12C or 0x140' % path)


class Dat:
    era = 'tod'

    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.f.seek(HEADER_OFFSET)
        (self.file_type, self.block_size, self.file_size, self.data_set,
         self.data_subset, self.free_head, self.free_tail, self.free_count,
         self.btree, self.new_lru, self.old_lru, self.use_lru,
         self.master_map_id) = struct.unpack('<13I', self.f.read(52))
        self.engine_pack, self.game_pack = struct.unpack('<2I', self.f.read(8))
        self.version_major = self.f.read(16)
        self.version_minor = struct.unpack('<I', self.f.read(4))[0]
        self.files = {}
        self._read_dir(self.btree)

    DIR_SIZE = (4 * 0x3E) + 4 + (4 * 6 * 0x3D)

    def _read_blocks(self, offset, size):
        buf = bytearray()
        f = self.f
        f.seek(offset)
        nxt = struct.unpack('<I', f.read(4))[0]
        remaining = size
        chunk = self.block_size - 4
        while remaining > 0:
            if nxt == 0:
                buf += f.read(remaining)
                break
            buf += f.read(chunk)
            f.seek(nxt)
            nxt = struct.unpack('<I', f.read(4))[0]
            remaining -= chunk
        return bytes(buf[:size])

    def _read_dir(self, offset):
        data = self._read_blocks(offset, self.DIR_SIZE)
        branches = struct.unpack_from('<62I', data, 0)
        count = struct.unpack_from('<I', data, 62 * 4)[0]
        base = 62 * 4 + 4
        for i in range(count):
            _flags, oid, foff, fsize, date, it = struct.unpack_from('<6I', data, base + i * 24)
            self.files[oid] = (foff, fsize, date, it)
        if branches[0] != 0:
            for i in range(count + 1):
                self._read_dir(branches[i])

    def get(self, oid):
        off, size, _d, _i = self.files[oid]
        return self._read_blocks(off, size)

    def ids_in(self, lo, hi):
        return [i for i in self.files if lo <= i <= hi]


class OldDat:
    """The original (pre-Throne of Destiny) dat container, 1999-2005.

    Header at 0x12C: magic 'BT', block size, file size, iteration, free
    head/tail/count, B-tree root. Directory nodes hold 62 branch offsets, an
    entry count, then up to 61 entries of {object id, file offset, size}.
    A leaf is a node whose first branch offset is zero. File payloads chain
    through blocks exactly as in the later container; a next-block pointer
    with the high bit set marks a free block and means the chain is corrupt.
    """
    era = 'pretod'

    DIR_SIZE = (4 * 0x3E) + 4 + (4 * 3 * 0x3D)

    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.f.seek(OLD_HEADER_OFFSET)
        (self.file_type, self.block_size, self.file_size, self.iteration,
         self.free_head, self.free_tail, self.free_count,
         self.btree) = struct.unpack('<8I', self.f.read(32))
        if self.file_type != MAGIC:
            raise ValueError('%s: bad old-dat magic %08X' % (path, self.file_type))
        self.files = {}
        self._read_dir(self.btree)

    def _read_blocks(self, offset, size):
        buf = bytearray()
        f = self.f
        remaining = size
        while offset and remaining > 0:
            if offset & 0x80000000:
                raise ValueError('%s: free block in chain' % self.path)
            f.seek(offset)
            offset = struct.unpack('<I', f.read(4))[0]
            take = min(self.block_size - 4, remaining)
            buf += f.read(take)
            remaining -= take
        if remaining:
            raise ValueError('%s: block chain ended %d bytes short'
                             % (self.path, remaining))
        return bytes(buf)

    def _read_dir(self, offset):
        data = self._read_blocks(offset, self.DIR_SIZE)
        branches = struct.unpack_from('<62I', data, 0)
        count = struct.unpack_from('<I', data, 62 * 4)[0]
        if count > 61:
            raise ValueError('%s: directory node with %d entries'
                             % (self.path, count))
        base = 62 * 4 + 4
        for i in range(count):
            oid, foff, fsize = struct.unpack_from('<3I', data, base + i * 12)
            self.files[oid] = (foff, fsize)
        if branches[0]:
            for i in range(count + 1):
                self._read_dir(branches[i])

    def get(self, oid):
        off, size = self.files[oid]
        return self._read_blocks(off, size)

    def ids_in(self, lo, hi):
        return [i for i in self.files if lo <= i <= hi]


class Reader:
    """Little-endian struct reader with the dat's alignment conventions."""

    def __init__(self, buf, pos=0):
        self.b = buf
        self.p = pos

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def u16(self):
        v = struct.unpack_from('<H', self.b, self.p)[0]; self.p += 2; return v

    def i16(self):
        v = struct.unpack_from('<h', self.b, self.p)[0]; self.p += 2; return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.p)[0]; self.p += 4; return v

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.p)[0]; self.p += 4; return v

    def f32(self):
        v = struct.unpack_from('<f', self.b, self.p)[0]; self.p += 4; return v

    def f64(self):
        v = struct.unpack_from('<d', self.b, self.p)[0]; self.p += 8; return v

    def vec3(self):
        return (self.f32(), self.f32(), self.f32())

    def quat(self):
        return (self.f32(), self.f32(), self.f32(), self.f32())

    def align(self, n=4):
        r = self.p % n
        if r:
            self.p += n - r

    def pstring(self):
        n = self.u16()
        s = self.b[self.p:self.p + n]
        self.p += n
        return s.decode('latin-1')

    def skip(self, n):
        self.p += n

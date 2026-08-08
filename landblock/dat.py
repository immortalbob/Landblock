"""Minimal reader for Asheron's Call .dat files (portal / cell / language).

Format transcribed from ACEmulator's ACE.DatLoader. Works on 2005-era through
end-of-retail dats -- the container format did not change.
"""
import struct

HEADER_OFFSET = 0x140


class Dat:
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

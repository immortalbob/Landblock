"""End-to-end retail-geometry test: hand-build a ToD-encoded environment and
EnvCell, then read them back through Geometry exactly as a retail client run
would. Proves the 2005+ mesh/cell decoders and their dispatch still work."""
import struct, sys, math
sys.path.insert(0, '/home/claude/work/src/landblock_source')
from landblock.geom import Geometry, read_environment

def f(*v):  return struct.pack('<%df' % len(v), *v)
def u32(v): return struct.pack('<I', v)
def i32(v): return struct.pack('<i', v)
def u16(v): return struct.pack('<H', v)
def i16(v): return struct.pack('<h', v)
def u8(v):  return struct.pack('<B', v)
def align(b, n=4):
    return b + b'\x00' * ((-len(b)) % n)

def polygon_tod(vids, stip=0x0C, sides=1):
    b = u8(len(vids)) + u8(stip) + i32(sides) + i16(-1) + i16(-1)
    for v in vids: b += i16(v)
    return b                              # stip 0x0C suppresses both uv runs

FLOOR = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]     # CCW from above
WALL  = [(0, 0, 0), (10, 0, 0), (10, 0, 6), (0, 0, 6)]       # vertical

def cellstruct_tod():
    verts = FLOOR + WALL
    b = u32(1) + u32(0) + u32(0)          # 1 drawing poly... plus walls
    # rebuild properly: 2 polygons, 0 physics, 0 portals
    b = u32(2) + u32(0) + u32(0)
    b += i32(1) + u32(len(verts))         # vertex type 1
    for i, (x, y, z) in enumerate(verts):
        b += u16(i) + u16(0) + f(x, y, z) + f(0, 0, 1)
    b += u16(0) + polygon_tod([0, 1, 2, 3])            # floor
    b += u16(1) + polygon_tod([4, 5, 6, 7])            # wall
    b = align(b)                                       # no portals; align
    b += b'FAEL' + i32(0)                              # cell BSP: LEAF
    b += b'FAEL' + i32(0) + i32(0) + f(0, 0, 0, 1) + u32(0)   # physics LEAF
    b += u32(0)                                        # no drawing BSP
    return align(b)

env_payload = u32(0x0D000001) + u32(1) + u32(0) + cellstruct_tod()

cells = read_environment(env_payload)
verts, polys = cells[0]
assert len(verts) == 8 and len(polys) == 2, (len(verts), len(polys))
print('ToD environment decoded: %d verts, %d polys, consumed to the exact byte'
      % (len(verts), len(polys)))

envcell = (u32(0x01230100) + u32(0) + u32(0x01230100)
           + u8(1) + u8(0) + u16(0) + u16(0x0055)
           + u16(0x0001) + u16(0)
           + f(100.0, 200.0, -12.0)
           + f(1.0, 0.0, 0.0, 0.0))

class FakeDat:
    era = 'tod'
    def __init__(self, files): self.files = files
    def get(self, oid): return self.files[oid]

g = Geometry(FakeDat({0x01230100: envcell}), FakeDat({0x0D000001: env_payload}))
recs = g.load(0x0123)
assert len(recs) == 1
r = recs[0]
assert r.env_id == 0x0D000001 and r.struct_idx == 0, (hex(r.env_id), r.struct_idx)
assert len(r.floors) == 1, r.floors
got = sorted((round(p[0], 3), round(p[1], 3), round(p[2], 3)) for p in r.floors[0])
want = sorted((100.0 + x, 200.0 + y, -12.0 + z) for x, y, z in FLOOR)
assert got == want, (got, want)
assert len(r.walls) == 1, r.walls
assert g.unmapped(0x0123) == 0
assert not g.missing_env
print('ToD EnvCell decoded: floor at world %s, 1 wall, no mesh gaps' % (got[0],))

# and a deliberately mismatched pair must be reported, not silently holed
g2 = Geometry(FakeDat({0x01230100: envcell}), FakeDat({}))
recs2 = g2.load(0x0123)
assert recs2 and not recs2[0].floors
assert g2.unmapped(0x0123) == 1 and g2.missing_env == {0x0D000001}
print('mismatched pair: 1 cell reported unmapped, env 0x0D000001 flagged')

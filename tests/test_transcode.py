"""Round-trip: original-era records -> retail encoding -> retail decoders.

Needs the two dats named below; skips cleanly without them. The standard is
not "it parses" but "the retail decode agrees with the original decode field
for field", which is what verify_envcell and verify_environment assert.
"""
import os, sys, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from landblock.dat import open_dat, Dat, Reader
from landblock.geom import Geometry
from landblock.datwrite import write_tod_dat
from landblock.transcode import (envcell_to_tod, environment_to_tod,
                                 verify_envcell, verify_environment,
                                 relocate_envcell)

CELL = os.environ.get('OLD_CELL', 'dats/cell_partial/CELL.DAT')
PORTAL = os.environ.get('OLD_PORTAL', 'dats/portal/portal.dat')
DST = int(os.environ.get('NEW_LB', '0114'), 16)

if not (os.path.exists(CELL) and os.path.exists(PORTAL)):
    print('skipped: set OLD_CELL / OLD_PORTAL to a pre-ToD pair')
    raise SystemExit(0)

src_cell, src_portal = open_dat(CELL), open_dat(PORTAL)
assert src_cell.era == 'pretod'


def interiors_of(lb):
    return sorted(i for i in src_cell.files
                  if i >> 16 == lb and 0x0100 <= (i & 0xFFFF) < 0xFFFE)


# Any interior will do, so rather than name one -- which ties the test to a
# particular client -- take a mid-sized landblock this dat actually has.
if os.environ.get('OLD_LB'):
    LB = int(os.environ['OLD_LB'], 16)
    ids = interiors_of(LB)
    assert ids, 'landblock %04X has no interior cells' % LB
else:
    have = {}
    for i in src_cell.files:
        if 0x0100 <= (i & 0xFFFF) < 0xFFFE:
            have[i >> 16] = have.get(i >> 16, 0) + 1
    sized = sorted((n, lb) for lb, n in have.items() if 8 <= n <= 120)
    assert sized, 'no interior landblock of a usable size in %s' % CELL
    LB = sized[len(sized) // 2][1]
    ids = interiors_of(LB)

cells, envs = {}, set()
for cid in ids:
    moved, new_id = relocate_envcell(src_cell.get(cid), DST)
    tod = envcell_to_tod(moved)
    verify_envcell(moved, tod)
    cells[new_id] = tod
    r = Reader(src_cell.get(cid)); r.u32(); r.u32()
    ns = r.u8(); r.u8(); r.u16(); r.skip(2 * ns); r.align()
    envs.add(0x0D000000 | r.u16())
cells[(DST << 16) | 0xFFFE] = struct.pack('<4I', (DST << 16) | 0xFFFE, len(ids), 0, 0)

meshes = {}
for e in sorted(envs):
    out = environment_to_tod(src_portal.get(e))
    verify_environment(src_portal.get(e), out)
    meshes[e] = out

write_tod_dat('/tmp/_tc_cell.dat', cells)
write_tod_dat('/tmp/_tc_portal.dat', meshes)
c, p = open_dat('/tmp/_tc_cell.dat'), open_dat('/tmp/_tc_portal.dat')
assert isinstance(c, Dat) and c.era == 'tod' and p.era == 'tod'

def sig(cs):
    return [(x.cell_id & 0xFFFF, x.env_id, x.struct_idx,
             tuple(round(v, 5) for v in x.origin),
             tuple(round(v, 5) for v in x.rot),
             tuple(sorted(x.portals)),
             tuple(sorted(tuple(sorted(round(v, 5) for v in pt))
                          for poly in x.floors for pt in poly)))
            for x in sorted(cs, key=lambda y: y.cell_id & 0xFFFF)]

gn = Geometry(c, p)
new = gn.load(DST)
old = Geometry(src_cell, src_portal).load(LB)
assert not gn.missing_env
assert len(new) == len(old) == len(ids)
assert sig(new) == sig(old), 'geometry changed in conversion'
print('transcode: %d cells + %d meshes -> retail, geometry identical'
      % (len(ids), len(envs)))

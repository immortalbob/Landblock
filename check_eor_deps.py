#!/usr/bin/env python3
"""Do the 2001 dungeon's dependencies still exist in an end-of-retail client?

Run this against your own EOR dats -- nothing is uploaded, and it only reads.

    python3 check_eor_deps.py /path/to/client_portal.dat [/path/to/client_cell_1.dat]

It answers the two questions left open by the transcode work:

  1. Do the 20 room meshes, 19 surfaces and 26 static-object setups the
     dungeon needs still exist in retail? If yes, the dungeon needs no
     portal.dat changes at all -- only the 139 cell records go in.
  2. Which dungeon landblocks are free in the retail cell.dat, so the
     dungeon can be dropped somewhere that collides with nothing.

Needs landblock/dat.py on the path (or run it from the source tree).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from landblock.dat import open_dat
except ImportError:
    sys.exit('put this next to the landblock package, or add it to PYTHONPATH')

# captured from the Feb 2001 cell dat, landblock 0x02B9
MESHES = [0x0D00000C, 0x0D000044, 0x0D000050, 0x0D000052, 0x0D000063,
          0x0D000064, 0x0D000065, 0x0D00006B, 0x0D0000A0, 0x0D0000BD,
          0x0D0000C8, 0x0D0000CA, 0x0D0000CB, 0x0D000107, 0x0D00011B,
          0x0D00016D, 0x0D00016E, 0x0D0002C7, 0x0D0002C9, 0x0D0000D8]


def report(name, ids, dat):
    have = [i for i in ids if i in dat.files]
    miss = [i for i in ids if i not in dat.files]
    print('  %-22s %3d needed, %3d present, %3d missing'
          % (name, len(ids), len(have), len(miss)))
    if miss:
        print('     missing: %s' % ' '.join('%08X' % m for m in miss))
    return miss


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    portal = open_dat(sys.argv[1])
    print('portal: %s  era=%s  %d records' % (sys.argv[1], portal.era, len(portal.files)))
    print('  environments present: %d'
          % sum(1 for i in portal.files if i >> 24 == 0x0D))
    missing = report('room meshes (0x0D)', MESHES, portal)

    # surfaces and setups are read out of the dungeon's own cells, so they are
    # only listed here if you also pass the original cell dat
    old_cell = os.environ.get('OLD_CELL')
    if old_cell and os.path.exists(old_cell):
        from landblock.dat import Reader
        src = open_dat(old_cell)
        lb = int(os.environ.get('OLD_LB', '02B9'), 16)
        surfaces, setups = set(), set()
        for cid in (i for i in src.files
                    if i >> 16 == lb and 0x0100 <= (i & 0xFFFF) < 0xFFFE):
            r = Reader(src.get(cid))
            flags = r.u32(); r.u32()
            ns = r.u8(); npo = r.u8(); nv = r.u16()
            for _ in range(ns):
                surfaces.add(0x08000000 | r.u16())
            r.align(); r.u16(); r.u16(); r.skip(28); r.skip(8 * npo); r.align()
            r.skip(2 * nv); r.align()
            if flags & 2:
                for _ in range(r.u32()):
                    setups.add(r.u32()); r.skip(28)
        missing += report('surfaces (0x08)', sorted(surfaces), portal)
        missing += report('static setups (0x02)', sorted(setups), portal)
    else:
        print('  (set OLD_CELL=/path/to/2001/cell.dat to also check surfaces'
              ' and static setups)')

    print()
    if missing:
        print('VERDICT: %d dependencies are absent from this client -- those'
              ' assets would have to be carried over too.' % len(missing))
    else:
        print('VERDICT: every dependency survives into this client.')
        print('         The dungeon needs no portal.dat changes; only the 139'
              ' cell records go in.')

    if len(sys.argv) > 2:
        cell = open_dat(sys.argv[2])
        used = {i >> 16 for i in cell.files if 0x0100 <= (i & 0xFFFF) < 0xFFFE}
        free = [lb for lb in range(0x0100, 0x0300) if lb not in used]
        print('\ncell: %s  era=%s  %d landblocks with interiors'
              % (sys.argv[2], cell.era, len(used)))
        print('  free dungeon landblocks (0x0100-0x02FF): %d' % len(free))
        print('  first twenty: %s'
              % ' '.join('%04X' % f for f in free[:20]))


if __name__ == '__main__':
    main()

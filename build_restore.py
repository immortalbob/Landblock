#!/usr/bin/env python3
"""Build a retail patch restoring Dark Majesty dungeons.

    python3 build_restore.py --old-cell cell.dat --old-portal portal.dat \
                             --new-cell client_cell_1.dat \
                             --new-portal client_portal.dat \
                             --tier 3 --out-dir restore/

Placement rule: a dungeon keeps its original landblock only when that
landblock is empty in the target. Anything whose slot is occupied is moved to
a free landblock, so nothing already in the client is ever displaced.
"""
import argparse
import collections
import json
import os
import struct
import sys

# find the landblock package whether this script sits beside it, inside the
# source tree, or next to an unzipped release
_here = os.path.dirname(os.path.abspath(__file__))
for _c in (_here, os.path.join(_here, 'src', 'landblock_source'),
           os.path.join(_here, '..'), os.path.join(_here, 'landblock_source')):
    if os.path.isdir(os.path.join(_c, 'landblock')):
        sys.path.insert(0, _c)
        break
else:
    sys.exit('cannot find the landblock package. Unzip the release so that '
             'landblock/ and dungeon_diff.py sit beside this script.')
import dungeon_diff as dd                                     # noqa: E402
from landblock import transcode as T                          # noqa: E402
from landblock.datwrite import write_tod_dat, write_old_dat   # noqa: E402

TIERS = {
    1: [0x0363, 0x0364, 0x0365, 0x0366, 0x0367, 0x0368, 0x565F, 0xBD59, 0xBD5A],
    2: [0x013E, 0x0193, 0x01B7, 0x01E4, 0x536D],
    3: [0xEB1D, 0x018A, 0x01EE, 0xA9B2, 0x016C, 0x5648, 0x017A, 0x017D],
}

NEW_TEXTURE_BASE = 0x06008000        # confirmed free in retail


def cell_deps(oc, cells):
    envs, surfs, props = set(), set(), set()
    for cid in cells:
        a = dd.parse_cell(oc.get(cid), oc.era)
        envs.add(0x0D000000 | a['env'])
        for s in a['surfaces']:
            surfs.add(0x08000000 | s)
        for i in range(0, len(a['statics']), 32):
            props.add(struct.unpack_from('<I', a['statics'], i)[0])
    return envs, surfs, props


def free_landblocks(nc, want, avoid, base=0x5000):
    """Landblocks with terrain and no interior, preferring the 5000 band."""
    used = {i >> 16 for i in nc.files if 0x0100 <= (i & 0xFFFF) < 0xFFFE}
    land = {i >> 16 for i in nc.files if (i & 0xFFFF) == 0xFFFF}
    out = []
    for lb in range(base, 0x7000):
        if len(out) >= want:
            break
        if lb in used or lb in avoid or lb not in land:
            continue
        out.append(lb)
    if len(out) < want:
        raise SystemExit('could not find %d free landblocks' % want)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--old-cell', required=True)
    ap.add_argument('--old-portal', required=True)
    ap.add_argument('--new-cell', required=True)
    ap.add_argument('--new-portal',
                    help='target portal.dat; omit if using --new-portal-index')
    ap.add_argument('--new-portal-index',
                    help='text file of hex record ids present in the target portal, '
                         'one per line. Lets the build run somewhere the real '
                         'portal.dat is not, which matters at nearly a gigabyte.')
    ap.add_argument('--tier', type=int, default=1, choices=(1, 2, 3))
    ap.add_argument('--landblocks',
                    help='comma-separated hex landblock list, instead of a tier')
    ap.add_argument('--avoid', default='',
                    help='comma-separated hex landblocks another patch has already '
                         'claimed, so two patches never collide')
    ap.add_argument('--spare-base', default='5000',
                    help='hex landblock to start looking for free slots from')
    ap.add_argument('--out-dir', default='restore')
    ap.add_argument('--drop-missing-props', action='store_true',
                    help='drop static placements whose prop cannot be carried '
                         'over, instead of failing')
    args = ap.parse_args()

    oc = dd.Dat(args.old_cell); op = dd.Dat(args.old_portal)
    nc = dd.Dat(args.new_cell)
    if args.new_portal_index:
        class _Index(object):
            def __init__(self, path):
                self.files = set()
                for line in open(path):
                    line = line.strip()
                    if line:
                        self.files.add(int(line, 16))
        np_ = _Index(args.new_portal_index)
    elif args.new_portal:
        np_ = dd.Dat(args.new_portal)
    else:
        raise SystemExit('need --new-portal or --new-portal-index')
    oi = dd.interiors(oc)
    new_used = {i >> 16 for i in nc.files if 0x0100 <= (i & 0xFFFF) < 0xFFFE}

    # a patch has to be written in the target's own format. Converting the
    # other way -- retail records back to the original layout -- is not
    # implemented, so that combination is refused rather than half-done.
    target_era = nc.era
    convert = (oc.era == 'pretod' and target_era == 'tod')
    if oc.era == 'tod' and target_era == 'pretod':
        raise SystemExit('cannot put retail-format records into an original-era '
                         'dat: the reverse conversion is not implemented')
    if oc.era != target_era and not convert:
        raise SystemExit('unhandled era pair: source %s, target %s'
                         % (oc.era, target_era))

    if args.landblocks:
        todo = [int(x, 16) for x in args.landblocks.replace(' ', '').split(',') if x]
    else:
        todo = []
        for t in range(1, args.tier + 1):
            todo.extend(TIERS[t])
    claimed = {int(x, 16) for x in args.avoid.replace(' ', '').split(',') if x}

    # ---- placement: original slot only when it is genuinely empty
    occupied = [lb for lb in todo if lb in new_used or lb in claimed]
    spares = free_landblocks(nc, len(occupied), set(todo) | claimed,
                             base=int(args.spare_base, 16))
    placement = {}
    spare_iter = iter(spares)
    for lb in todo:
        placement[lb] = lb if (lb not in new_used and lb not in claimed) \
            else next(spare_iter)

    # ---- gather every dependency across the whole set
    all_envs, all_surfs, all_props = set(), set(), set()
    for lb in todo:
        e, s, p = cell_deps(oc, oi[lb])
        all_envs |= e; all_surfs |= s; all_props |= p
    miss_env = sorted(e for e in all_envs if e not in np_.files)
    miss_prop = sorted(p for p in all_props if p not in np_.files)
    # a mesh the target lacks is carried over, converted to the retail encoding
    carried_meshes = {}
    for e in miss_env:
        if e not in op.files:
            raise SystemExit('mesh %08X is absent from the source portal too' % e)
        if convert:
            out = T.environment_to_tod(op.get(e))
            T.verify_environment(op.get(e), out)
        else:
            out = op.get(e)
        carried_meshes[e] = out

    # a prop carries its own surface list, and those surfaces may be absent
    # too -- fold them in before any surface work starts, or the chain breaks
    for prop in miss_prop:
        if prop not in op.files:
            continue
        pb = op.get(prop)
        n = struct.unpack_from('<I', pb, 8)[0]
        all_surfs |= set(struct.unpack_from('<%dI' % n, pb, 12)) if n else set()
    miss_surf = sorted(s for s in all_surfs if s not in np_.files)

    portal = dict(carried_meshes)
    tex_id = NEW_TEXTURE_BASE

    # ---- surfaces, with their texture chains rebuilt for retail
    for surf_id in miss_surf:
        b = op.get(surf_id)
        if not convert:
            portal[surf_id] = b            # same generation: copy it through
            continue
        typ = struct.unpack_from('<I', b, 4)[0]
        if not (typ & 6):
            portal[surf_id] = T.surface_to_tod(b)             # solid colour
            continue
        img_id, _pal = struct.unpack_from('<2I', b, 8)
        img = op.get(img_id)
        # ImgTex: id, type, width, height, then width*height indices, then
        # the palette id -- so the palette sits at the very end of the record
        iw, ih = struct.unpack_from('<2I', img, 8)
        pal_id = struct.unpack_from('<I', img, 16 + iw * ih)[0]
        w, h, rgb = T.imgtex_to_rgb(img, op.get(pal_id))
        while tex_id in np_.files or tex_id in portal:
            tex_id += 1
        portal[tex_id] = T.render_surface_record(tex_id, w, h, rgb)
        portal[img_id] = T.surface_texture_record(img_id, [tex_id])
        portal[surf_id] = T.surface_to_tod(b, texture_id=img_id)
        tex_id += 1

    # ---- props
    dropped_props = set()
    for prop in miss_prop:
        if prop not in op.files:
            dropped_props.add(prop); continue
        try:
            out = T.gfxobj_to_tod(op.get(prop)) if convert else op.get(prop)
        except Exception as exc:
            if not args.drop_missing_props:
                raise SystemExit('cannot carry prop %08X: %s' % (prop, exc))
            dropped_props.add(prop); continue
        deps = struct.unpack_from('<%dI' % struct.unpack_from('<I', op.get(prop), 8)[0],
                                  op.get(prop), 12)
        gone = [d for d in deps if d not in np_.files and d not in portal]
        if gone:
            if not args.drop_missing_props:
                raise SystemExit('prop %08X needs absent surfaces %s'
                                 % (prop, ' '.join('%08X' % g for g in gone)))
            dropped_props.add(prop); continue
        portal[prop] = out

    # ---- cells
    cells = {}
    manifest = []
    dropped_orphans = {}
    for lb in todo:
        dst = placement[lb]
        # take only what the LandBlockInfo counts as live, and refuse to emit
        # anything that would not look like real client data
        keep = dd.live_cells(oc, lb, oi[lb])
        if len(keep) != len(oi[lb]):
            dropped_orphans[lb] = len(oi[lb]) - len(keep)
        problems = dd.validate_landblock(oc, lb, keep)
        if problems:
            raise SystemExit('%04X fails validation: %s' % (lb, '; '.join(problems)))
        n = 0
        for cid in sorted((lb << 16) | k for k in keep):
            buf = oc.get(cid)
            if dropped_props:
                buf = strip_props(buf, oc.era, dropped_props)
            moved, new_id = T.relocate_envcell(buf, dst, era=oc.era)
            if convert:
                rec = T.envcell_to_tod(moved)
                T.verify_envcell(moved, rec)
            else:
                rec = moved
            cells[new_id] = rec
            n += 1
        cells[(dst << 16) | 0xFFFE] = struct.pack('<4I', (dst << 16) | 0xFFFE, n, 0, 0)
        manifest.append(dict(source='%04X' % lb, target='%04X' % dst,
                             relocated=lb != dst, cells=n))

    os.makedirs(args.out_dir, exist_ok=True)
    cpath = os.path.join(args.out_dir, 'restore_cell.dat')
    ppath = os.path.join(args.out_dir, 'restore_portal.dat')
    if target_era == 'tod':
        write_tod_dat(cpath, cells, block_size=0x400, data_set=2)
        if portal:
            write_tod_dat(ppath, portal, block_size=0x400, data_set=1)
    else:
        write_old_dat(cpath, cells, block_size=nc.block_size)
        if portal:
            write_old_dat(ppath, portal, block_size=0x400)
    with open(os.path.join(args.out_dir, 'manifest.json'), 'w') as fh:
        json.dump(dict(tier=args.tier, source=os.path.basename(args.old_cell),
                       placement=manifest,
                       portal_records=sorted('%08X' % k for k in portal),
                       dropped_props=sorted('%08X' % p for p in dropped_props),
                       dropped_orphans={'%04X' % k: v for k, v in dropped_orphans.items()}),
                  fh, indent=2)

    print('%d dungeons, %d cell records, %d portal records  [%s source -> %s target%s]'
          % (len(todo), len(cells), len(portal), oc.era, target_era,
             ', converting' if convert else ', same format'))
    if carried_meshes:
        print('  room meshes carried over: %s'
              % ' '.join('%08X' % m for m in sorted(carried_meshes)))
    print('  kept original landblock : %d' % sum(1 for m in manifest if not m['relocated']))
    print('  relocated (slot in use) : %d' % sum(1 for m in manifest if m['relocated']))
    for m in manifest:
        if m['relocated']:
            print('     %s -> %s  (%d cells)' % (m['source'], m['target'], m['cells']))
    if dropped_props:
        print('  props dropped: %s' % ' '.join('%08X' % p for p in sorted(dropped_props)))
    if dropped_orphans:
        print('  orphan cells left behind (not counted by the LandBlockInfo):')
        for lb, n in sorted(dropped_orphans.items()):
            print('     %04X: %d cells' % (lb, n))
    print('  wrote %s and %s' % (cpath, ppath if portal else '(no portal changes)'))


def strip_props(buf, era, drop):
    """Remove static placements whose prop cannot travel."""
    c = dd.parse_cell(buf, era)
    if not (c['flags'] & 0x02):
        return buf
    keep = []
    for i in range(0, len(c['statics']), 32):
        if struct.unpack_from('<I', c['statics'], i)[0] not in drop:
            keep.append(c['statics'][i:i + 32])
    if len(keep) * 32 == len(c['statics']):
        return buf
    # rebuild the record in its original encoding with the shorter static list
    out = bytearray()
    out += struct.pack('<2I', c['flags'], c['cid'])
    out += struct.pack('<BBH', len(c['surfaces']), len(c['ports']) // 8,
                       len(c['stabs']) // 2)
    for s in c['surfaces']:
        out += struct.pack('<H', s)
    out += b'\x00' * ((-len(out)) % 4)
    out += struct.pack('<2H', c['env'], c['sidx'])
    out += c['frame'] + c['ports']
    out += b'\x00' * ((-len(out)) % 4)
    out += c['stabs']
    out += b'\x00' * ((-len(out)) % 4)
    out += struct.pack('<I', len(keep)) + b''.join(keep)
    out += b'\x00' * ((-len(out)) % 4)
    if c['restriction'] is not None:
        out += struct.pack('<I', c['restriction'])
    return bytes(out)


if __name__ == '__main__':
    main()

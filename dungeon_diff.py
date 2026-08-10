#!/usr/bin/env python3
"""Compare the dungeons of two Asheron's Call client eras.

    python3 dungeon_diff.py --old-cell cell.dat     --old-portal portal.dat \
                            --new-cell client_cell_1.dat \
                            --new-portal client_portal.dat \
                            --out changes.csv

Reports every landblock that the OLD dats contain and the new ones either
dropped or changed. Landblocks only the new dats have are ignored -- the
question is what happened to the old content, not what was added later.

Self-contained: reads both container generations itself, so it needs nothing
but Python.

WHY THIS IS NOT A BYTE DIFF
---------------------------
Between Dark Majesty and end of retail every surface id in the game was
renumbered. A cell that is otherwise untouched still stores different numbers
in its surface array, so a naive comparison calls 100% of dungeons "updated"
and tells you nothing.

So the tool learns the renumbering from the data before judging anything. It
collects every (old surface, new surface) pair across all shared cells; where
an old id maps overwhelmingly to one new id, that is renumbering, not
retexturing. Only surface changes that deviate from that dominant mapping
count as real. The mapping is reported so you can check it.

WHAT COUNTS AS A CHANGE
-----------------------
A cell is "changed" if any of these differ: the room mesh it uses (environment
id and cell-struct index), where it sits (position and rotation), its portal
links to neighbouring cells, its visibility list, its static objects, its
flags, or its surfaces after renumbering is accounted for.

    update % = (cells added + removed + changed) / cells in either era

0% means untouched. 100% means nothing survived unaltered. A landblock the new
dats do not have at all is reported as REMOVED at 100%.

`struct_pct` is the same figure with texture-only changes discounted, which
separates "this dungeon was rebuilt" from "this dungeon was repainted".

`old_lbi_cells` is what the old LandBlockInfo claims the landblock holds. Dat
files accumulate orphans: cells left behind when content was retired, which
the LandBlockInfo no longer counts. Where that column reads 0 against a
non-zero old_cells, the old side is leftover junk rather than a live dungeon,
and any comparison against it is meaningless -- those rows are marked
`orphaned` and can be filtered out.

Room meshes are compared separately, because a dungeon whose cells are
identical can still have been rebuilt underneath if the mesh it points at
changed. That is the `meshes_changed` column, and it does not inflate the
cell-based percentage.
"""
import argparse
import collections
import csv
import json
import os
import struct
import sys

MAGIC = 0x5442


# --------------------------------------------------------------- containers

class Dat:
    """Reads either dat generation: pre-ToD (header 0x12C) or retail (0x140)."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.f.seek(0x12C); old = struct.unpack('<I', self.f.read(4))[0]
        self.f.seek(0x140); new = struct.unpack('<I', self.f.read(4))[0]
        if old == MAGIC:
            self.era = 'pretod'
            self.f.seek(0x12C)
            (_m, self.block_size, self.size, _it,
             _fh, _ft, _fc, self.root) = struct.unpack('<8I', self.f.read(32))
            self.entry, self.dir_size = 12, 62 * 4 + 4 + 61 * 12
        elif new == MAGIC:
            self.era = 'tod'
            self.f.seek(0x140)
            v = struct.unpack('<13I', self.f.read(52))
            self.block_size, self.size, self.root = v[1], v[2], v[8]
            self.entry, self.dir_size = 24, 62 * 4 + 4 + 61 * 24
        else:
            raise SystemExit('%s: no dat header magic at 0x12C or 0x140' % path)
        self.files = {}
        self._read_dir(self.root)

    def _blocks(self, off, size):
        buf = bytearray()
        rem = size
        while off and rem > 0:
            if off & 0x80000000:
                raise ValueError('%s: free block in chain' % self.path)
            self.f.seek(off)
            off = struct.unpack('<I', self.f.read(4))[0]
            take = min(self.block_size - 4, rem)
            buf += self.f.read(take)
            rem -= take
        return bytes(buf)

    def _read_dir(self, off):
        d = self._blocks(off, self.dir_size)
        branches = struct.unpack_from('<62I', d, 0)
        n = struct.unpack_from('<I', d, 248)[0]
        for i in range(n):
            if self.entry == 12:
                oid, o, s = struct.unpack_from('<3I', d, 252 + i * 12)
            else:
                _f, oid, o, s, _dt, _it = struct.unpack_from('<6I', d, 252 + i * 24)
            self.files[oid] = (o, s)
        if branches[0]:
            for i in range(n + 1):
                self._read_dir(branches[i])

    def get(self, oid):
        off, size = self.files[oid]
        return self._blocks(off, size)


def interiors(dat):
    """landblock -> [cell id], for cells that make up interiors."""
    out = collections.defaultdict(list)
    for i in dat.files:
        if 0x0100 <= (i & 0xFFFF) < 0xFFFE:
            out[i >> 16].append(i)
    return out


# ------------------------------------------------------------------- cells

def parse_cell(buf, era):
    """Decode an EnvCell from either era into comparable fields."""
    p = 0

    def u32():
        nonlocal p
        v = struct.unpack_from('<I', buf, p)[0]; p += 4; return v

    def u16():
        nonlocal p
        v = struct.unpack_from('<H', buf, p)[0]; p += 2; return v

    def u8():
        nonlocal p
        v = buf[p]; p += 1; return v

    def align():
        nonlocal p
        p += (-p) % 4

    old = era == 'pretod'
    if old:
        flags = u32(); cid = u32()
    else:
        u32(); flags = u32(); cid = u32()
    nsurf = u8(); nport = u8(); nstab = u16()
    surfaces = tuple(u16() for _ in range(nsurf))
    if old:
        align()
    env = u16(); sidx = u16()
    frame = buf[p:p + 28]; p += 28
    ports = buf[p:p + 8 * nport]; p += 8 * nport
    if old:
        align()
    stabs = buf[p:p + 2 * nstab]; p += 2 * nstab
    if old:
        align()
    statics = b''
    if flags & 0x02:
        n = u32()
        statics = buf[p:p + 32 * n]; p += 32 * n
        if old:
            align()
    restriction = u32() if flags & 0x08 else None
    return dict(flags=flags, cid=cid, surfaces=surfaces, env=env, sidx=sidx,
                frame=frame, ports=ports, stabs=stabs, statics=statics,
                restriction=restriction)


def surfaces_only(buf, era):
    """Cheap first pass: just the surface array, for learning the renumbering."""
    p = 0 if era == 'pretod' else 4
    p += 8
    nsurf = buf[p]
    p += 4
    return struct.unpack_from('<%dH' % nsurf, buf, p) if nsurf else ()


def reaches_outside(cell):
    """Does this cell touch the outdoors?

    Two independent signals, either of which disqualifies a landblock from
    being a dungeon: the cell is flagged as visible from outside, or one of
    its portals leads outdoors rather than to another cell (CCellPortal sets
    bit 2 and the client reads the destination as -1).
    """
    if cell['flags'] & 0x01:
        return True
    ports = cell['ports']
    for i in range(0, len(ports), 8):
        if struct.unpack_from('<H', ports, i)[0] & 0x04:
            return True
    return False


def landblock_info(dat, lb):
    """(declared cell count, building count) from the LandBlockInfo record."""
    oid = (lb << 16) | 0xFFFE
    if oid not in dat.files:
        return -1, -1
    b = dat.get(oid)
    if len(b) < 12:
        return -1, -1
    _id, ncells, nobj = struct.unpack_from('<3I', b, 0)
    # each object is an id plus a Frame: origin vec3 (12) + rotation quat (16)
    p = 12 + 32 * nobj
    if p + 4 > len(b):
        return ncells, -1
    info = struct.unpack_from('<I', b, p)[0]
    return ncells, info & 0xFFFF


# ------------------------------------------------------------------ meshes

def _skip_polygon(r, old):
    npts = r.u8(); stip = r.u8()
    sides = r.i32()
    r.i16(); r.i16()
    r.skip(2 * npts)
    if not (stip & 0x04):
        r.skip(npts)
    if sides == 2 and not (stip & 0x08):
        r.skip(npts)
    if old:
        r.align()


class _R:
    __slots__ = ('b', 'p')

    def __init__(self, b): self.b = b; self.p = 0

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

    def skip(self, n): self.p += n

    def align(self): self.p += (-self.p) % 4


def _skip_bsp(r, kind, old):
    tag = r.b[r.p:r.p + 4][::-1].decode('latin-1')
    if tag == 'LEAF':
        r.skip(4); r.i32()
        if kind == 'physics':
            r.i32(); r.skip(16); r.skip(2 * r.u32())
            if old:
                r.align()
        return
    if tag == 'PORT':
        r.skip(4); r.skip(16)
        _skip_bsp(r, kind, old); _skip_bsp(r, kind, old)
        if kind == 'drawing':
            r.skip(16)
            np_ = r.u32(); npo = r.u32()
            r.skip(2 * np_); r.skip(4 * npo)
            if old:
                r.align()
        return
    r.skip(4); r.skip(16)
    if tag in ('BPnn', 'BPIn', 'BpIN', 'BpnN'):
        _skip_bsp(r, kind, old)
    elif tag in ('BPIN', 'BPnN'):
        _skip_bsp(r, kind, old); _skip_bsp(r, kind, old)
    if kind == 'cell':
        return
    r.skip(16)
    if kind == 'physics':
        return
    r.skip(2 * r.u32())
    if old:
        r.align()


def mesh_signatures(buf, old):
    """struct index -> a comparable signature of that room's geometry."""
    r = _R(buf)
    r.u32()
    n = r.u32()
    out = {}
    for _ in range(n):
        key = r.u32()
        start = r.p
        npoly = r.u32(); nphys = r.u32(); nport = r.u32()
        vtype = r.u32(); nvert = r.u32()
        verts = []
        if vtype == 1:
            for _v in range(nvert):
                r.u16(); nuv = r.u16()
                verts.append(bytes(r.b[r.p:r.p + 24])); r.skip(24)
                r.skip(8 * nuv)
        elif vtype in (2, 3):
            for _v in range(nvert):
                verts.append(bytes(r.b[r.p:r.p + 24])); r.skip(32)
        else:
            raise ValueError('vertex type %d' % vtype)
        polys = []
        for _q in range(npoly):
            pid = r.i16() if old else r.u16()
            a = r.p
            _skip_polygon(r, old)
            polys.append((pid, bytes(r.b[a:a + 8])))
        r.skip(2 * nport)
        r.align()
        _skip_bsp(r, 'cell', old)
        for _q in range(nphys):
            r.i16() if old else r.u16()
            _skip_polygon(r, old)
        _skip_bsp(r, 'physics', old)
        if r.u32():
            _skip_bsp(r, 'drawing', old)
        r.align()
        out[key] = hash((tuple(verts), tuple(polys), npoly, nphys, nport))
        del start
    return out


# ---------------------------------------------------------------- the diff

ASPECTS = ('mesh', 'placement', 'links', 'visibility', 'props', 'flags', 'textures')


def learn_surface_map(old_dat, new_dat, old_idx, new_idx, verbose=True):
    """Derive the global surface renumbering from the data itself."""
    pairs = collections.defaultdict(collections.Counter)
    shared = sorted(set(old_idx) & set(new_idx))
    for lb in shared:
        for cid in old_idx[lb]:
            if cid not in new_dat.files:
                continue
            a = surfaces_only(old_dat.get(cid), old_dat.era)
            b = surfaces_only(new_dat.get(cid), new_dat.era)
            if len(a) != len(b):
                continue
            for x, y in zip(a, b):
                pairs[x][y] += 1
    remap = {}
    confidence = {}
    for old_id, counter in pairs.items():
        top, n = counter.most_common(1)[0]
        remap[old_id] = top
        confidence[old_id] = n / sum(counter.values())
    if verbose:
        clean = sum(1 for v in confidence.values() if v > 0.99)
        print('surface renumbering learned from %d ids; %d map unambiguously'
              % (len(remap), clean), file=sys.stderr)
    return remap, confidence


def compare(args):
    old_cell = Dat(args.old_cell)
    new_cell = Dat(args.new_cell)
    old_idx = interiors(old_cell)
    new_idx = interiors(new_cell)
    print('old: %s (%s) %d landblocks with interiors' %
          (os.path.basename(args.old_cell), old_cell.era, len(old_idx)), file=sys.stderr)
    print('new: %s (%s) %d landblocks with interiors' %
          (os.path.basename(args.new_cell), new_cell.era, len(new_idx)), file=sys.stderr)

    remap, conf = learn_surface_map(old_cell, new_cell, old_idx, new_idx)

    # room meshes, compared once each rather than per landblock
    mesh_diff = {}
    if args.old_portal and args.new_portal:
        op, np_ = Dat(args.old_portal), Dat(args.new_portal)
        old_env = {i for i in op.files if i >> 24 == 0x0D}
        for eid in sorted(old_env):
            try:
                a = mesh_signatures(op.get(eid), op.era == 'pretod')
            except Exception:
                continue
            if eid not in np_.files:
                mesh_diff[eid] = None            # mesh gone entirely
                continue
            try:
                b = mesh_signatures(np_.get(eid), np_.era == 'pretod')
            except Exception:
                continue
            mesh_diff[eid] = {k: (k not in b or b[k] != v) for k, v in a.items()}
        print('compared %d room meshes' % len(mesh_diff), file=sys.stderr)

    rows = []
    for lb in sorted(old_idx):
        old_ids = {i & 0xFFFF for i in old_idx[lb]}
        old_cells = {i & 0xFFFF: old_cell.get(i) for i in old_idx[lb]}
        if len(old_ids) < args.min_cells:
            continue
        outside = False
        parsed_old = {}
        for k, buf in old_cells.items():
            c = parse_cell(buf, old_cell.era)
            parsed_old[k] = c
            if reaches_outside(c):
                outside = True

        # the LandBlockInfo is authoritative about whether a landblock has a
        # live interior; cells present but uncounted are retired leftovers
        lbi_cells, buildings = landblock_info(old_cell, lb)
        orphaned = 'yes' if lbi_cells == 0 else ''

        # A dungeon has no way in on foot: no cell visible from outdoors and
        # no portal leading outdoors. Anything with an outside connection is
        # a building or a cave -- both are surface structures, and telling
        # those two apart needs more than the cell data (a cave mouth is
        # registered in the LandBlockInfo as a building, same as a house).
        kind = 'dungeon' if not outside else ('building' if buildings > 0 else 'cave')

        if args.kind != 'all' and kind != args.kind:
            continue

        if lb not in new_idx:
            rows.append(dict(
                landblock='%04X' % lb, status='REMOVED', kind=kind,
                old_cells=len(old_ids), new_cells=0, added=0,
                removed=len(old_ids), changed=0, identical=0,
                update_pct=100.0, struct_pct=100.0, meshes_changed=0,
                old_lbi_cells=lbi_cells, buildings=buildings, orphaned=orphaned,
                **{a: 0 for a in ASPECTS}))
            continue

        new_ids = {i & 0xFFFF for i in new_idx[lb]}
        removed = old_ids - new_ids
        added = new_ids - old_ids
        common = old_ids & new_ids
        aspect = collections.Counter()
        changed = 0
        structural = 0
        for k in sorted(common):
            a = parsed_old[k]
            b = parse_cell(new_cell.get((lb << 16) | k), new_cell.era)
            hit = struct_hit = False
            if (a['env'], a['sidx']) != (b['env'], b['sidx']):
                aspect['mesh'] += 1; hit = struct_hit = True
            if a['frame'] != b['frame']:
                aspect['placement'] += 1; hit = struct_hit = True
            if a['ports'] != b['ports']:
                aspect['links'] += 1; hit = struct_hit = True
            if a['stabs'] != b['stabs']:
                aspect['visibility'] += 1; hit = struct_hit = True
            if a['statics'] != b['statics']:
                aspect['props'] += 1; hit = struct_hit = True
            if a['flags'] != b['flags']:
                aspect['flags'] += 1; hit = struct_hit = True
            mapped = tuple(remap.get(s, s) for s in a['surfaces'])
            if mapped != b['surfaces']:
                aspect['textures'] += 1; hit = True
            if hit:
                changed += 1
            if struct_hit:
                structural += 1
        total = len(old_ids | new_ids)
        pct = 100.0 * (changed + len(added) + len(removed)) / total if total else 0.0
        spct = 100.0 * (structural + len(added) + len(removed)) / total if total else 0.0

        mc = 0
        if mesh_diff:
            seen = set()
            for k, a in parsed_old.items():
                eid = 0x0D000000 | a['env']
                key = (eid, a['sidx'])
                if key in seen:
                    continue
                seen.add(key)
                d = mesh_diff.get(eid)
                if d is None and eid in mesh_diff:
                    mc += 1
                elif isinstance(d, dict) and d.get(a['sidx']):
                    mc += 1

        if pct == 0 and mc == 0 and not args.all:
            continue
        rows.append(dict(
            landblock='%04X' % lb, status='UPDATED', kind=kind,
            old_cells=len(old_ids), new_cells=len(new_ids), added=len(added),
            removed=len(removed), changed=changed,
            identical=len(common) - changed, update_pct=round(pct, 1),
            struct_pct=round(spct, 1), meshes_changed=mc,
            old_lbi_cells=lbi_cells, buildings=buildings, orphaned=orphaned,
            **{a: aspect[a] for a in ASPECTS}))

    rows.sort(key=lambda r: (-r['update_pct'], r['landblock']))
    cols = ['landblock', 'status', 'kind', 'update_pct', 'struct_pct', 'orphaned',
            'old_cells', 'new_cells', 'old_lbi_cells', 'buildings',
            'added', 'removed', 'changed', 'identical', 'meshes_changed'] + list(ASPECTS)
    with open(args.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    gone = [r for r in rows if r['status'] == 'REMOVED']
    upd = [r for r in rows if r['status'] == 'UPDATED']
    orph = [r for r in rows if r['orphaned']]
    print('\n%d landblocks reported: %d removed, %d updated'
          % (len(rows), len(gone), len(upd)))
    if orph:
        print('%d of them are orphaned in the old dat (LandBlockInfo says 0 cells)'
              ' -- retired content, not a live dungeon' % len(orph))
    kinds = collections.Counter(r['kind'] for r in rows if not r['orphaned'])
    print('by kind (excluding orphans):', dict(kinds))
    if upd:
        band = collections.Counter()
        for r in upd:
            p = r['update_pct']
            band['100%' if p >= 100 else
                 '75-99%' if p >= 75 else
                 '50-74%' if p >= 50 else
                 '25-49%' if p >= 25 else
                 '10-24%' if p >= 10 else
                 'under 10%'] += 1
        print('update bands:', dict(band))
    print('wrote %s' % args.out)
    return rows



def layout_signature(dat, lb, keys):
    """A hash of what a landblock contains, independent of where it lives.

    Two landblocks with the same signature hold the same dungeon registered
    twice, which happens a lot: in one 2001 client, 287 changed landblocks are
    only 46 distinct places. Restoring all of them would mean 241 duplicates.

    Built from decoded fields rather than raw bytes, so it is also comparable
    across the two container generations -- the same dungeon in the original
    and retail encodings has different bytes but identical content, and a
    byte hash would call them different every time.
    """
    import hashlib
    h = hashlib.md5()
    for k in sorted(keys):
        c = parse_cell(dat.get((lb << 16) | k), dat.era)
        h.update(struct.pack('<3I', k, c['flags'], c['env']))
        h.update(struct.pack('<I', c['sidx']))
        h.update(c['frame']); h.update(c['ports']); h.update(c['stabs'])
        h.update(c['statics'])
        for sfc in c['surfaces']:
            h.update(struct.pack('<H', sfc))
    return h.hexdigest()


def run_dedupe(cell_path, csv_path, min_struct=100.0, quiet=False):
    """Group the changed landblocks in a diff CSV by what they actually hold.

    Prints one representative per distinct layout, ready to paste into
    build_restore.py --landblocks.
    """
    dat = Dat(cell_path)
    idx = interiors(dat)
    rows = [r for r in csv.DictReader(open(csv_path))
            if not r['orphaned'] and float(r['struct_pct']) >= min_struct]
    groups = collections.defaultdict(list)
    for r in rows:
        lb = int(r['landblock'], 16)
        if lb not in idx:
            continue
        keys = [i & 0xFFFF for i in idx[lb]]
        groups[layout_signature(dat, lb, live_cells(dat, lb, idx[lb]))].append(r['landblock'])
    reps = sorted(min(v) for v in groups.values())
    if not quiet:
        cells = sum(len(live_cells(dat, int(x, 16), idx[int(x, 16)])) for x in reps)
        print('%d landblocks at or above %.0f%% structural change'
              % (len(rows), min_struct))
        print('   %d distinct layouts, %d cells in total' % (len(reps), cells))
        dupes = sorted((v for v in groups.values() if len(v) > 1), key=len, reverse=True)
        for v in dupes[:6]:
            print('   %d landblocks share one layout: %s%s'
                  % (len(v), ' '.join(sorted(v)[:8]), ' ...' if len(v) > 8 else ''))
        print()
        print('--landblocks ' + ','.join(reps))
    return reps


def run_validate(path, quiet=False):
    """Check every landblock in a cell dat against the invariants real client
    data holds to. Returns the number that fail.

    The list comes from what a client-format reader actually enforces --
    DungeonViewer's own failure messages name them: the LandBlockInfo must be
    there, every cell it implies must be there, and every cell a portal link
    or visibility entry names must be there.
    """
    dat = Dat(path)
    idx = interiors(dat)
    failures = []
    warnings = []
    for lb in sorted(idx):
        keys = sorted(i & 0xFFFF for i in idx[lb])
        problems = validate_landblock(dat, lb, keys)
        declared, _b = landblock_info(dat, lb)
        if declared < 0:
            problems.append('no LandBlockInfo')
        if problems:
            failures.append((lb, problems))
        elif declared != len(keys):
            # retail itself ships 38 landblocks like this, so it is survivable:
            # orphan cells the LandBlockInfo stopped counting. Worth knowing
            # about, not worth failing over.
            warnings.append((lb, 'LandBlockInfo declares %d cells, %d present'
                             % (declared, len(keys))))
    if not quiet:
        print('%s: %d landblocks with interiors' % (os.path.basename(path), len(idx)))
        print('   %d broken (a reader following a reference will not find it)'
              % len(failures))
        for lb, problems in failures[:30]:
            print('      %04X  %s' % (lb, '; '.join(problems)))
        if len(failures) > 30:
            print('      ... and %d more' % (len(failures) - 30))
        print('   %d carrying uncounted orphan cells (survivable; retail does it too)'
              % len(warnings))
        for lb, w in warnings[:10]:
            print('      %04X  %s' % (lb, w))
        if len(warnings) > 10:
            print('      ... and %d more' % (len(warnings) - 10))
    return len(failures)


def run_verify_restore(old_cell, new_cell, manifest_path, quiet=False):
    """Confirm a restored dungeon in the patched dat matches where it came from.

    A plain diff cannot answer this. Most dungeons are relocated -- the target
    landblock was occupied, so the restored copy lives at a new id while the
    target's own version stays where it was. Comparing by landblock id then
    reports the original slot as still changed, which looks like a failure and
    is not one.

    The manifest records where each dungeon went, so this follows it: source
    landblock in the old dat against target landblock in the patched one,
    compared by layout signature, which is era-independent.
    """
    oc = Dat(old_cell)
    nc = Dat(new_cell)
    m = json.load(open(manifest_path))
    oi = interiors(oc)
    ni = interiors(nc)
    good, bad = [], []
    for p in m['placement']:
        src, dst = int(p['source'], 16), int(p['target'], 16)
        if src not in oi:
            bad.append((p, 'source landblock is not in the old dat')); continue
        if dst not in ni:
            bad.append((p, 'nothing at the target landblock')); continue
        want = live_cells(oc, src, oi[src])
        got = sorted(i & 0xFFFF for i in ni[dst])
        if len(want) != len(got):
            bad.append((p, '%d cells expected, %d present' % (len(want), len(got))))
            continue
        problems = validate_landblock(nc, dst, got)
        if problems:
            bad.append((p, '; '.join(problems))); continue
        if layout_signature(oc, src, want) != layout_signature(nc, dst, got):
            bad.append((p, 'contents differ from the source')); continue
        good.append(p)
    if not quiet:
        print('%s -> %s, following %s'
              % (os.path.basename(old_cell), os.path.basename(new_cell),
                 os.path.basename(manifest_path)))
        print('   %d of %d restored dungeons are identical to their source'
              % (len(good), len(m['placement'])))
        moved = [p for p in good if p['source'] != p['target']]
        if moved:
            print('   %d of those were relocated, so their original landblock '
                  'still holds the target\'s own version' % len(moved))
        for p, why in bad:
            print('      %s -> %s  %s' % (p['source'], p['target'], why))
    return len(bad)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--validate', metavar='CELLDAT',
                    help='check one cell dat against the structural invariants '
                         'real client data holds to, and exit')
    ap.add_argument('--dedupe', nargs=2, metavar=('CELLDAT', 'CHANGES_CSV'),
                    help='group the changed landblocks in a diff CSV by what '
                         'they actually contain, and print one representative '
                         'per distinct layout')
    ap.add_argument('--min-struct', type=float, default=100.0,
                    help='structural change floor for --dedupe (default 100)')
    ap.add_argument('--verify-restore', nargs=3,
                    metavar=('OLD_CELL', 'PATCHED_CELL', 'MANIFEST'),
                    help='confirm every dungeon named in a build manifest '
                         'arrived intact, following relocations')
    ap.add_argument('--old-cell')
    ap.add_argument('--new-cell')
    ap.add_argument('--old-portal')
    ap.add_argument('--new-portal')
    ap.add_argument('--out', default='dungeon_changes.csv')
    ap.add_argument('--min-cells', type=int, default=1)
    ap.add_argument('--kind', default='all',
                    choices=('all', 'dungeon', 'building', 'cave'),
                    help='only report landblocks of this kind. A dungeon has'
                         ' no outside connection at all; buildings and caves'
                         ' do. Caves are told from buildings by the absence'
                         ' of a registered building, which is a weak test --'
                         ' treat that split as provisional.')
    ap.add_argument('--all', action='store_true',
                    help='also list landblocks that did not change')
    args = ap.parse_args()
    if args.validate:
        raise SystemExit(1 if run_validate(args.validate) else 0)
    if args.dedupe:
        run_dedupe(args.dedupe[0], args.dedupe[1], args.min_struct)
        raise SystemExit(0)
    if args.verify_restore:
        raise SystemExit(1 if run_verify_restore(*args.verify_restore) else 0)
    if not (args.old_cell and args.new_cell):
        ap.error('need --old-cell and --new-cell, or --validate')
    compare(args)


# ------------------------------------------------- landblock sanity checking

def live_cells(dat, lb, ids):
    """The cells that actually make up the landblock's interior.

    A dat accumulates orphans: cells left behind when content was retired,
    which the LandBlockInfo stops counting but nothing deletes. The
    LandBlockInfo is authoritative, so where it declares a count and that many
    cells are present contiguously from 0x100, that prefix is the live dungeon
    and anything past it is dead weight.

    Copying the dead weight produces a landblock with holes in its cell
    numbering and references pointing at cells that are not there. No native
    landblock looks like that -- all 3,409 in an end-of-retail cell dat number
    contiguously from 0x100 -- so a reader is entitled to assume otherwise,
    and one that follows a stab list into a hole will crash.
    """
    have = {i & 0xFFFF for i in ids}
    declared, _buildings = landblock_info(dat, lb)
    if declared > 0:
        prefix = set(range(0x100, 0x100 + declared))
        if prefix <= have:
            return sorted(prefix)
    return sorted(have)


def validate_landblock(dat, lb, keys):
    """Check a landblock against the invariants real client data holds to.

    Returns a list of problems; empty means it looks like something the game
    shipped. keys are 16-bit cell ids.
    """
    problems = []
    keys = sorted(keys)
    if not keys:
        return ['no cells']
    if keys != list(range(0x100, 0x100 + len(keys))):
        holes = keys[-1] - keys[0] + 1 - len(keys)
        problems.append('cell ids are not contiguous from 0x100 '
                        '(%03X..%03X with %d missing)' % (keys[0], keys[-1], holes))
    present = set(keys)
    dangling_p = dangling_s = 0
    for k in keys:
        c = parse_cell(dat.get((lb << 16) | k), dat.era)
        for j in range(0, len(c['ports']), 8):
            fl, _poly, other, _op = struct.unpack_from('<4H', c['ports'], j)
            if not (fl & 0x04) and other not in present:
                dangling_p += 1
        for j in range(0, len(c['stabs']), 2):
            if struct.unpack_from('<H', c['stabs'], j)[0] not in present:
                dangling_s += 1
    if dangling_p:
        problems.append('%d portal links point at a missing cell' % dangling_p)
    if dangling_s:
        problems.append('%d visibility entries point at a missing cell' % dangling_s)
    return problems

if __name__ == '__main__':
    main()

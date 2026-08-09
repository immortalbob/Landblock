#!/usr/bin/env python3
"""Merge records into an Asheron's Call dat, at full client size.

    # prove the machinery is lossless on your own files first
    python3 dat_merge.py --verify client_portal.dat

    # then merge a patch in
    python3 dat_merge.py --base client_cell_1.dat --patch patch_cell.dat \
                         --out client_cell_1.new.dat

Reads and writes both container generations. The source file is never
modified: output always goes to a new file.

WHY STREAMING
-------------
A patch is a few hundred KB but the file it goes into is not -- an
end-of-retail portal.dat is 927 MB across 79,694 records. Building the output
in memory first, which is fine for a fragment, would peak past 2 GB here.

So records are written straight through: walk the source, copy each block
chain to the output as it is read, and keep only `(id, offset, size)` per
record. The B-tree is bulk-loaded over those offsets at the end and its nodes
appended as ordinary blocks, because a directory node is just another block
chain. Peak memory is the index alone -- about 60 MB for a retail cell dat,
6 MB for a portal -- regardless of how big the file is.

The tree is built bottom-up with keys distributed evenly across nodes, so it
comes out balanced and every node stays within the 61-entry limit. It is a
real search tree, not merely sorted storage: `--verify` checks every id is
reachable by the ordered descent a client's `Lookup` performs, which is the
part that actually matters to the game.
"""
import argparse
import os
import struct
import sys

MAGIC = 0x5442
DATA_START = 0x1000
BRANCHES = 0x3E                  # 62 branch slots per node
MAX_ENTRIES = 0x3D               # 61 entries per node


# --------------------------------------------------------------- reading

class Dat:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.f.seek(0x12C); old = struct.unpack('<I', self.f.read(4))[0]
        self.f.seek(0x140); new = struct.unpack('<I', self.f.read(4))[0]
        if old == MAGIC:
            self.era = 'pretod'
            self.f.seek(0x12C)
            (_m, self.block_size, self.size, self.iteration,
             _fh, _ft, _fc, self.root) = struct.unpack('<8I', self.f.read(32))
            self.entry = 12
            self.header = None
        elif new == MAGIC:
            self.era = 'tod'
            self.f.seek(0x140)
            self.header = self.f.read(52 + 8 + 16 + 4)
            v = struct.unpack_from('<13I', self.header, 0)
            self.block_size, self.size, self.root = v[1], v[2], v[8]
            self.iteration = 0
            self.entry = 24
        else:
            raise SystemExit('%s: no dat header magic at 0x12C or 0x140' % path)
        self.dir_size = BRANCHES * 4 + 4 + MAX_ENTRIES * self.entry
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
        if rem:
            raise ValueError('%s: chain ended %d bytes short' % (self.path, rem))
        return bytes(buf)

    def _read_dir(self, off):
        d = self._blocks(off, self.dir_size)
        br = struct.unpack_from('<62I', d, 0)
        n = struct.unpack_from('<I', d, 248)[0]
        for i in range(n):
            if self.entry == 12:
                oid, o, s = struct.unpack_from('<3I', d, 252 + i * 12)
                meta = (0, 0)
            else:
                _f, oid, o, s, dt, it = struct.unpack_from('<6I', d, 252 + i * 24)
                meta = (dt, it)
            self.files[oid] = (o, s, meta)
        if br[0]:
            for i in range(n + 1):
                self._read_dir(br[i])

    def get(self, oid):
        o, s, _m = self.files[oid]
        return self._blocks(o, s)


# --------------------------------------------------------------- writing

class StreamWriter:
    """Writes a dat without holding its contents in memory."""

    def __init__(self, path, era='tod', block_size=0x400, template=None):
        self.path = path
        self.era = era
        self.block_size = block_size
        self.template = template
        self.entry = 12 if era == 'pretod' else 24
        self.dir_size = BRANCHES * 4 + 4 + MAX_ENTRIES * self.entry
        self.f = open(path, 'wb')
        self.f.write(b'\x00' * DATA_START)
        self.pos = DATA_START
        self.index = []                       # (oid, offset, size, meta)

    def _raw(self, data):
        """Write a block chain and return the offset of its first block."""
        start = self.pos
        room = self.block_size - 4
        n = max(1, (len(data) + room - 1) // room)
        for k in range(n):
            chunk = data[k * room:(k + 1) * room]
            nxt = self.pos + self.block_size if k + 1 < n else 0
            self.f.write(struct.pack('<I', nxt))
            self.f.write(chunk)
            pad = room - len(chunk)
            if pad:
                self.f.write(b'\x00' * pad)
            self.pos += self.block_size
        return start

    def add(self, oid, data, meta=(0, 0)):
        off = self._raw(data)
        self.index.append((oid, off, len(data), meta))

    # ------------------------------------------------------------ B-tree

    def _node(self, entries, children):
        buf = bytearray(self.dir_size)
        for i, c in enumerate(children):
            struct.pack_into('<I', buf, i * 4, c)
        struct.pack_into('<I', buf, BRANCHES * 4, len(entries))
        base = BRANCHES * 4 + 4
        for i, e in enumerate(entries):
            oid, off, size, meta = e
            if self.entry == 12:
                struct.pack_into('<3I', buf, base + i * 12, oid, off, size)
            else:
                struct.pack_into('<6I', buf, base + i * 24,
                                 0, oid, off, size, meta[0], meta[1])
        return self._raw(bytes(buf))

    def _build_tree(self, records):
        """Bulk-load a balanced B-tree; returns the root block offset.

        Leaves hold runs of records; one record between each pair of adjacent
        nodes is promoted to the parent as its separator, which is what makes
        this a search tree rather than a sorted pile.
        """
        if not records:
            return self._node([], [0] * BRANCHES)

        # leaves: n nodes hold n*MAX entries plus n-1 promoted separators
        total = len(records)
        n = max(1, -(-(total + 1) // (MAX_ENTRIES + 1)))
        per, extra = divmod(total - (n - 1), n)
        nodes, seps = [], []
        i = 0
        for j in range(n):
            size = per + (1 if j < extra else 0)
            nodes.append(self._node(records[i:i + size], [0] * BRANCHES))
            i += size
            if j < n - 1:
                seps.append(records[i]); i += 1
        assert i == total, (i, total)

        # internal levels: each parent takes up to MAX+1 children
        while len(nodes) > 1:
            p = -(-len(nodes) // (MAX_ENTRIES + 1))
            per, extra = divmod(len(nodes), p)
            parents, psep = [], []
            i = 0
            for j in range(p):
                k = per + (1 if j < extra else 0)
                kids = nodes[i:i + k]
                take = seps[i:i + k - 1]
                parents.append(self._node(take, kids + [0] * (BRANCHES - len(kids))))
                i += k
                if j < p - 1:
                    psep.append(seps[i - 1])
            nodes, seps = parents, psep
        return nodes[0]

    def close(self):
        self.index.sort(key=lambda r: r[0])
        root = self._build_tree(self.index)
        size = self.pos
        self.f.seek(0x12C if self.era == 'pretod' else 0x140)
        if self.era == 'pretod':
            self.f.write(struct.pack('<8I', MAGIC, self.block_size, size,
                                     1, 0, 0, 0, root))
        else:
            h = bytearray(self.template if self.template
                          else struct.pack('<13I', MAGIC, self.block_size, size,
                                           1, 0, 0, 0, 0, root, 0, 0, 0, 0)
                          + struct.pack('<2I', 0, 0) + b'\x00' * 20)
            struct.pack_into('<I', h, 0, MAGIC)
            struct.pack_into('<I', h, 4, self.block_size)
            struct.pack_into('<I', h, 8, size)
            struct.pack_into('<I', h, 20, 0)      # free head
            struct.pack_into('<I', h, 24, 0)      # free tail
            struct.pack_into('<I', h, 28, 0)      # free count
            struct.pack_into('<I', h, 32, root)
            self.f.write(bytes(h))
        self.f.close()
        return size, len(self.index)


# ---------------------------------------------------------------- merging

class Collision(Exception):
    """A patch record would land on top of one the base already has."""


def empty_lbi_ids(base, overlay):
    """Patch ids that only replace an *empty* LandBlockInfo.

    Registering a dungeon means writing the landblock's LandBlockInfo, and
    retail already carries one for most landblocks -- declaring zero cells,
    because the interior was removed. Overwriting that record destroys
    nothing: it is the registration itself, and it currently says "nothing
    here". Anything else that collides is real content and stays protected.
    """
    ok = set()
    for oid in set(overlay) & set(base.files):
        if (oid & 0xFFFF) != 0xFFFE:
            continue
        b = base.get(oid)
        if len(b) >= 8 and struct.unpack_from('<I', b, 4)[0] == 0:
            ok.add(oid)
    return ok


def merge(base_path, patch_paths, out_path, extra=None, quiet=False,
          overwrite=False, allow_empty_lbi=False):
    """Write base + patches to out_path.

    By default a patch record whose id already exists in the base is an
    error, not an overwrite. Silently replacing a record means silently
    destroying whatever content was there -- someone else's dungeon, a shared
    mesh -- and the failure would only show up in game. Pass overwrite=True
    when replacing is genuinely what you mean.
    """
    base = Dat(base_path)
    patches = [Dat(p) for p in (patch_paths or [])]
    overlay = {}
    for p in patches:
        if p.era != base.era:
            raise SystemExit('%s is %s but the base is %s -- convert first'
                             % (p.path, p.era, base.era))
        for oid in p.files:
            overlay[oid] = p
    if extra:
        for oid, data in extra.items():
            overlay[oid] = data

    permitted = empty_lbi_ids(base, overlay) if allow_empty_lbi else set()
    clash = sorted(set(overlay) & set(base.files) - permitted)
    if clash and not overwrite:
        raise Collision(
            '%d patch records already exist in %s, e.g. %s. Nothing written. '
            'Relocate them, or pass --overwrite if replacing is intended.'
            % (len(clash), os.path.basename(base_path),
               ' '.join('%08X' % c for c in clash[:8])))
    if permitted and not quiet:
        print('  replacing %d empty LandBlockInfo records: %s'
              % (len(permitted), ' '.join('%08X' % p for p in sorted(permitted))))

    w = StreamWriter(out_path, era=base.era, block_size=base.block_size,
                     template=base.header)
    replaced = added = copied = 0
    for oid in sorted(base.files):
        if oid in overlay:
            src = overlay.pop(oid)
            data = src if isinstance(src, bytes) else src.get(oid)
            w.add(oid, data, base.files[oid][2])
            replaced += 1
        else:
            w.add(oid, base.get(oid), base.files[oid][2])
            copied += 1
    for oid in sorted(overlay):
        src = overlay[oid]
        data = src if isinstance(src, bytes) else src.get(oid)
        w.add(oid, data)
        added += 1
    size, n = w.close()
    if not quiet:
        print('%s: %d records (%d copied, %d replaced, %d added), %.1f MB'
              % (os.path.basename(out_path), n, copied, replaced, added,
                 size / 2 ** 20))
    return dict(records=n, copied=copied, replaced=replaced, added=added, size=size)


# ----------------------------------------------------------- verification

def lookup(dat, oid):
    """Find a record the way the client does: ordered descent, no scanning."""
    off = dat.root
    while True:
        d = dat._blocks(off, dat.dir_size)
        br = struct.unpack_from('<62I', d, 0)
        n = struct.unpack_from('<I', d, 248)[0]
        keys = []
        for i in range(n):
            if dat.entry == 12:
                keys.append(struct.unpack_from('<I', d, 252 + i * 12)[0])
            else:
                keys.append(struct.unpack_from('<I', d, 252 + i * 24 + 4)[0])
        i = 0
        while i < len(keys) and oid > keys[i]:
            i += 1
        if i < len(keys) and keys[i] == oid:
            return True
        if not br[0]:
            return False
        off = br[i]


def verify(original, rebuilt, sample=0):
    a, b = Dat(original), Dat(rebuilt)
    print('  records: %d -> %d' % (len(a.files), len(b.files)))
    missing = set(a.files) - set(b.files)
    if missing:
        print('  MISSING %d records' % len(missing)); return False
    ids = sorted(a.files)
    if sample and sample < len(ids):
        step = len(ids) // sample
        ids = ids[::step]
    bad = 0
    for oid in ids:
        if a.get(oid) != b.get(oid):
            bad += 1
            if bad < 4:
                print('  payload differs for %08X' % oid)
    print('  payloads compared: %d, mismatches: %d' % (len(ids), bad))
    unreachable = [o for o in ids if not lookup(b, o)]
    print('  reachable by client-style lookup: %d of %d'
          % (len(ids) - len(unreachable), len(ids)))
    absent = 0xABCDEF01
    while absent in b.files:
        absent += 1
    ok = not bad and not unreachable and not lookup(b, absent)
    print('  absent key correctly not found: %s' % (not lookup(b, absent)))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base')
    ap.add_argument('--patch', action='append', default=[])
    ap.add_argument('--out')
    ap.add_argument('--verify', metavar='DAT',
                    help='round-trip this dat through the writer and check the '
                         'result is byte-identical and properly indexed')
    ap.add_argument('--allow-empty-lbi', action='store_true',
                    help='permit replacing a LandBlockInfo that declares zero\n'
                         ' cells. That record is the landblock registration and\n'
                         ' currently says the interior is empty, so writing it is\n'
                         ' how a restored dungeon gets registered. Real content\n'
                         ' stays protected.')
    ap.add_argument('--overwrite', action='store_true',
                    help='allow patch records to replace existing ones. Off by\n'
                         ' default: a collision is an error, because replacing\n'
                         ' a record destroys whatever was there.')
    ap.add_argument('--sample', type=int, default=0,
                    help='compare this many records rather than all of them')
    ap.add_argument('--keep', action='store_true',
                    help='keep the temporary file written by --verify')
    args = ap.parse_args()

    if args.verify:
        tmp = (args.out or args.verify + '.roundtrip')
        print('round-tripping %s (%.1f MB)'
              % (args.verify, os.path.getsize(args.verify) / 2 ** 20))
        merge(args.verify, [], tmp)
        ok = verify(args.verify, tmp, args.sample)
        if not args.keep and not args.out:
            os.remove(tmp)
        print('VERDICT:', 'lossless' if ok else 'FAILED')
        return 0 if ok else 1

    if not (args.base and args.out):
        ap.error('need --base and --out, or --verify')
    try:
        merge(args.base, args.patch, args.out, overwrite=args.overwrite,
              allow_empty_lbi=args.allow_empty_lbi)
    except Collision as exc:
        print('COLLISION: %s' % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

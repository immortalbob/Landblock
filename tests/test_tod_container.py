"""Regression test: synthesise a Throne-of-Destiny-format dat and read it back.

Exercises the retail container path end to end -- open_dat() sniffing, the
0x140 header, 24-byte directory entries, multi-block payload chains and
B-tree recursion into branch nodes -- without needing a retail client.
"""
import os, struct, sys, random
sys.path.insert(0, '/home/claude/work/src/landblock_source')
from landblock.dat import open_dat, Dat, OldDat

BS = 1024
DIR_SIZE = (4 * 0x3E) + 4 + (4 * 6 * 0x3D)

def build(path, payloads, entries_per_node=3):
    """payloads: {oid: bytes}. Builds a real multi-level B-tree."""
    blocks = []                       # list of bytearray, block i at offset base+i*BS
    def alloc(data):
        """write data as a chain of blocks, return head offset"""
        chunks = [data[i:i + BS - 4] for i in range(0, len(data), BS - 4)] or [b'']
        ids = [len(blocks) + k for k in range(len(chunks))]
        for k, ch in enumerate(chunks):
            nxt = (ids[k + 1] if k + 1 < len(chunks) else 0)
            blocks.append((nxt, ch))
        return ids[0]

    base = 0x1000
    def off(bid):
        return base + bid * BS

    items = sorted(payloads.items())
    # write file payloads first
    placed = {}
    for oid, data in items:
        placed[oid] = (alloc(data), len(data))

    # build B-tree bottom-up: chunk the sorted keys into leaves
    def make_node(entries, branches):
        buf = bytearray(DIR_SIZE)
        for i, b in enumerate(branches):
            struct.pack_into('<I', buf, i * 4, b)
        struct.pack_into('<I', buf, 62 * 4, len(entries))
        for i, oid in enumerate(entries):
            o, sz = placed[oid]
            struct.pack_into('<6I', buf, 62 * 4 + 4 + i * 24,
                             0, oid, off(o), sz, 0xDEADBEEF, 7)
        return bytes(buf)

    keys = [k for k, _ in items]
    groups = [keys[i:i + entries_per_node] for i in range(0, len(keys), entries_per_node)]
    if len(groups) == 1:
        root = alloc(make_node(groups[0], [0] * 62))
    else:
        # one branch level: leaves hold groups, root holds the separators
        leaves = [alloc(make_node(g, [0] * 62)) for g in groups]
        seps = [g[-1] for g in groups[:-1]]      # last key of each leaf but the last
        # a branch node has len(seps) entries and len(seps)+1 branches
        leaf_keys = []
        for g in groups[:-1]:
            leaf_keys.append(g[-1])
        # move separators out of their leaves so no key is duplicated
        trimmed = [g[:-1] for g in groups[:-1]] + [groups[-1]]
        leaves = [alloc(make_node(g, [0] * 62)) for g in trimmed]
        branches = [off(b) for b in leaves] + [0] * (62 - len(leaves))
        root = alloc(make_node(leaf_keys, branches))

    with open(path, 'wb') as f:
        f.write(b'\x00' * base)
        for nxt, ch in blocks:
            blk = struct.pack('<I', off(nxt) if nxt else 0) + ch
            f.write(blk + b'\x00' * (BS - len(blk)))
        size = f.tell()
    # header at 0x140
    with open(path, 'r+b') as f:
        f.seek(0x140)
        f.write(struct.pack('<13I', 0x5442, BS, size, 1, 0, 0, 0, 0,
                            off(root), 0, 0, 0, 0))
        f.write(struct.pack('<2I', 0, 0))
        f.write(b'V' * 16)
        f.write(struct.pack('<I', 0))
    return path

random.seed(11)
payloads = {}
for n in range(40):
    oid = 0x0D000000 + n * 7 + 1
    payloads[oid] = bytes(random.randrange(256) for _ in range(random.randrange(1, 5000)))
payloads[0x01000100] = b'short'
payloads[0xFFFF0001] = bytes(range(256)) * 12          # spans many blocks

p = build('/tmp/synthetic_tod.dat', payloads)
d = open_dat(p)
assert isinstance(d, Dat), type(d)
assert d.era == 'tod', d.era
assert len(d.files) == len(payloads), (len(d.files), len(payloads))
bad = [hex(k) for k in payloads if d.get(k) != payloads[k]]
assert not bad, 'payload mismatch: %s' % bad[:5]
sample = sorted(payloads)[5]
assert d.files[sample][2] == 0xDEADBEEF          # date field preserved
assert d.files[sample][3] == 7                   # iteration field preserved
print('ToD container: %d files, all payloads byte-identical, B-tree recursion OK'
      % len(d.files))

# and the sniffer must not mistake one era for the other
old = open_dat('/home/claude/work/dats/cell/cell.dat')
assert isinstance(old, OldDat) and old.era == 'pretod'
print('era sniffing: 0x140 -> Dat(tod), 0x12C -> OldDat(pretod)')

# _skip_bsp default arg must be behaviourally identical to the v1.1 signature
from landblock import geom
import inspect
sig = inspect.signature(geom._skip_bsp)
assert list(sig.parameters) == ['r', 'kind', 'old'], sig
assert sig.parameters['old'].default is False, sig
print('_skip_bsp(r, kind) still defaults to the retail path')

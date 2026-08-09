"""Write an original-era (pre-Throne of Destiny) dat file.

Rebuilds a whole dat from an {object id: payload} mapping rather than editing
one in place. In-place insertion would mean splitting B-tree nodes and
recycling the free-block chain; a rebuild needs neither, and every byte of
the result is accounted for, which matters more than the write cost when the
output is going in front of a game client.

Layout produced, matching what the client expects (see dat.OldDat):

    0x000..0x12C   zero
    0x12C          header: 'BT', block size, file size, iteration,
                   free head/tail/count, B-tree root
    0x1000..       fixed-size blocks, each a 4-byte "next block" offset
                   followed by payload; 0 terminates a chain

Directory nodes are ordinary block chains holding 62 branch offsets, an entry
count, then up to 61 {id, offset, size} entries. The tree is bulk-loaded from
sorted keys, so it comes out balanced and every node is full bar the last of
each rank.
"""
import struct

MAGIC = 0x5442
HEADER_OFFSET = 0x12C
DATA_START = 0x1000
BRANCHES = 0x3E                 # 62
MAX_ENTRIES = 0x3D              # 61
DIR_SIZE = (4 * BRANCHES) + 4 + (4 * 3 * MAX_ENTRIES)


class OldDatWriter:
    """Accumulates blocks, then emits a complete dat.

    write_dat() is the whole interface; the rest is bookkeeping.
    """

    def __init__(self, block_size=0x100):
        if block_size <= 4:
            raise ValueError('block size must exceed the 4-byte link')
        self.block_size = block_size
        self.blocks = []            # (next_block_index or None, payload bytes)

    def _offset(self, index):
        return DATA_START + index * self.block_size

    def _alloc(self, data):
        """Store data as a chain of blocks; return the head block's offset."""
        room = self.block_size - 4
        chunks = [data[i:i + room] for i in range(0, len(data), room)] or [b'']
        first = len(self.blocks)
        for k, chunk in enumerate(chunks):
            nxt = first + k + 1 if k + 1 < len(chunks) else None
            self.blocks.append((nxt, chunk))
        return self._offset(first)

    def _node(self, entries, branches, placed):
        buf = bytearray(DIR_SIZE)
        for i, b in enumerate(branches):
            struct.pack_into('<I', buf, i * 4, b)
        struct.pack_into('<I', buf, BRANCHES * 4, len(entries))
        base = BRANCHES * 4 + 4
        for i, oid in enumerate(entries):
            off, size = placed[oid]
            struct.pack_into('<3I', buf, base + i * 12, oid, off, size)
        return bytes(buf)

    def _build_tree(self, keys, placed):
        """Bulk-load a balanced B-tree over sorted keys; return root offset.

        Leaves take up to MAX_ENTRIES keys. One key between each pair of
        adjacent leaves is promoted to the parent as its separator, which is
        what makes the result a search tree rather than merely a stack of
        sorted nodes.
        """
        if not keys:
            return self._alloc(self._node([], [0] * BRANCHES, placed))

        def pack_rank(items):
            """items: list of keys. -> (node offsets, promoted separators)"""
            groups, seps, cur = [], [], []
            for k in items:
                cur.append(k)
                if len(cur) == MAX_ENTRIES:
                    groups.append(cur)
                    cur = []
            if cur:
                groups.append(cur)
            # promote the last key of every group but the final one
            if len(groups) > 1:
                promoted = [g.pop() for g in groups[:-1]]
                groups = [g for g in groups if g]
                seps = promoted
            return groups, seps

        groups, seps = pack_rank(keys)
        children = [self._alloc(self._node(g, [0] * BRANCHES, placed))
                    for g in groups]
        # walk up: each rank consumes the separators left by the rank below
        while len(children) > 1:
            nodes, rest = [], list(seps)
            idx = 0
            up = []
            while idx < len(rest):
                take = rest[idx:idx + MAX_ENTRIES]
                kids = children[idx:idx + len(take) + 1]
                if len(kids) < len(take) + 1:
                    take = take[:len(kids) - 1]
                nodes.append((take, kids))
                idx += len(take) + 1
            leftover = children[idx:]
            if leftover:
                if nodes:
                    # fold a trailing orphan into the previous node
                    take, kids = nodes[-1]
                    nodes[-1] = (take, kids + leftover)
                else:
                    nodes.append(([], leftover))
            new_children, new_seps = [], []
            for n, (take, kids) in enumerate(nodes):
                branches = list(kids) + [0] * (BRANCHES - len(kids))
                new_children.append(self._alloc(self._node(take, branches, placed)))
                if n < len(nodes) - 1:
                    # the key separating this node from the next moves up
                    consumed = sum(len(t) + 1 for t, _ in nodes[:n + 1]) - 1
                    if consumed < len(rest):
                        new_seps.append(rest[consumed])
            children, seps = new_children, new_seps
            if not seps and len(children) > 1:
                # cannot separate any further; collapse under one root
                branches = children + [0] * (BRANCHES - len(children))
                return self._alloc(self._node([], branches, placed))
        return children[0]

    def write_dat(self, path, files, iteration=1):
        """files: {object id: payload bytes}."""
        keys = sorted(files)
        placed = {}
        for oid in keys:
            data = files[oid]
            placed[oid] = (self._alloc(data), len(data))
        root = self._build_tree(keys, placed)

        size = DATA_START + len(self.blocks) * self.block_size
        with open(path, 'wb') as f:
            f.write(b'\x00' * DATA_START)
            for nxt, chunk in self.blocks:
                link = self._offset(nxt) if nxt is not None else 0
                blk = struct.pack('<I', link) + chunk
                f.write(blk + b'\x00' * (self.block_size - len(blk)))
            f.seek(HEADER_OFFSET)
            f.write(struct.pack('<8I', MAGIC, self.block_size, size,
                                iteration, 0, 0, 0, root))
        return size


def write_old_dat(path, files, block_size=0x100, iteration=1):
    """Convenience wrapper: build a complete pre-ToD dat at path."""
    return OldDatWriter(block_size).write_dat(path, files, iteration)


# --------------------------------------------------------------- retail dat

TOD_HEADER_OFFSET = 0x140
TOD_DIR_SIZE = (4 * BRANCHES) + 4 + (4 * 6 * MAX_ENTRIES)


class TodDatWriter(OldDatWriter):
    """The 2005-through-end-of-retail container.

    Same block chaining and same B-tree shape; the header sits at 0x140 and
    carries the data set, master map and version fields, and each directory
    entry grows from 12 bytes to 24 to make room for a leading flag word, a
    date and an iteration.
    """

    def __init__(self, block_size=0x400, data_set=1, data_subset=0):
        super().__init__(block_size)
        self.data_set = data_set
        self.data_subset = data_subset

    def _node(self, entries, branches, placed):
        buf = bytearray(TOD_DIR_SIZE)
        for i, b in enumerate(branches):
            struct.pack_into('<I', buf, i * 4, b)
        struct.pack_into('<I', buf, BRANCHES * 4, len(entries))
        base = BRANCHES * 4 + 4
        for i, oid in enumerate(entries):
            off, size = placed[oid]
            struct.pack_into('<6I', buf, base + i * 24,
                             0, oid, off, size, 0, 0)
        return bytes(buf)

    def write_dat(self, path, files, iteration=1, version=b'', minor=0):
        keys = sorted(files)
        placed = {}
        for oid in keys:
            placed[oid] = (self._alloc(files[oid]), len(files[oid]))
        root = self._build_tree(keys, placed)

        size = DATA_START + len(self.blocks) * self.block_size
        with open(path, 'wb') as f:
            f.write(b'\x00' * DATA_START)
            for nxt, chunk in self.blocks:
                link = self._offset(nxt) if nxt is not None else 0
                blk = struct.pack('<I', link) + chunk
                f.write(blk + b'\x00' * (self.block_size - len(blk)))
            f.seek(TOD_HEADER_OFFSET)
            f.write(struct.pack('<13I', MAGIC, self.block_size, size,
                                self.data_set, self.data_subset,
                                0, 0, 0, root, 0, 0, 0, 0))
            f.write(struct.pack('<2I', 0, 0))          # engine / game pack
            f.write((version or b'')[:16].ljust(16, b'\x00'))
            f.write(struct.pack('<I', minor))
        return size


def write_tod_dat(path, files, block_size=0x400, data_set=1, iteration=1):
    """Convenience wrapper: build a complete retail-format dat at path."""
    return TodDatWriter(block_size, data_set).write_dat(path, files, iteration)

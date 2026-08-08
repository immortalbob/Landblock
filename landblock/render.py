"""Renders one landblock's dungeon as an annotated map image."""
import collections
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import world as acworld
from .geom import slope_of

FONT_DIR = '/usr/share/fonts/truetype/dejavu'


def _font(size, bold=False):
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default()


STYLES = {
    'dungeon': dict(
        bg=(255, 255, 255), paper=(255, 255, 255),
        fills=[(247, 214, 168), (243, 196, 133), (238, 178, 106),
               (232, 160, 82), (226, 143, 62), (214, 128, 52), (198, 114, 44)],
        outline=(0, 0, 0), outline_w=2,
        ramp_hatch=(150, 96, 40), bridge=(176, 122, 62),
        text=(0, 0, 0), sub=(70, 70, 80),
    ),
    'maze': dict(
        bg=(255, 255, 255), paper=(255, 255, 255),
        fills=[(21, 96, 189)] * 8,
        outline=(255, 255, 255), outline_w=2,
        ramp_hatch=(255, 255, 255), bridge=(120, 170, 230),
        text=(0, 0, 0), sub=(70, 70, 80),
    ),
}

HOTSPOT_COLORS = {
    'acid': (86, 196, 86), 'fire': (224, 74, 60), 'cold': (120, 190, 232),
    'electric': (216, 196, 80), 'bludgeon': (170, 150, 140),
    'pierce': (196, 150, 96), 'slash': (200, 128, 96),
    'mana': (150, 140, 226), 'nether': (140, 90, 170),
}
HAZARD_DAMAGE = 10          # below this a hotspot is ambience, not a hazard


def hotspot_color(dt):
    for part in (dt or '').split('/'):
        if part in HOTSPOT_COLORS:
            return HOTSPOT_COLORS[part]
    return (150, 160, 150)

DAMAGE_BITS = {1: 'slash', 2: 'pierce', 4: 'bludgeon', 8: 'cold', 16: 'fire',
               32: 'acid', 64: 'electric', 512: 'mana', 1024: 'nether'}


def damage_name(v):
    """Damage type is a bitmask -- 5 is slash+bludgeon, not an unknown."""
    parts = [n for b, n in sorted(DAMAGE_BITS.items()) if v & b]
    return '/'.join(parts) if parts else 'unknown'


DAMAGE_TYPE = DAMAGE_BITS

MARKER = {
    'arrival':  ((40, 70, 220),   'D  portal drop point'),
    'exit':     ((214, 30, 190),  'E  portal / exit'),
    'door':     ((70, 70, 85),    'door'),
    'door_lock': ((205, 30, 30),  'locked door'),
    'door_link': ((120, 60, 200), 'door opened by lever / plate'),
    'chest':    ((205, 150, 25),  'chest'),
    'chest_lock': ((160, 100, 15), 'locked chest'),
    'trap':     ((235, 120, 20),  'trap'),
    'plate':    ((90, 140, 210),  'pressure plate'),
    'lever':    ((150, 60, 200),  'lever / switch'),
    'item':     ((20, 180, 180),  'item on ground'),
    'npc':      ((25, 150, 60),   'NPC'),
    'creature': ((215, 25, 25),   'creature spawn'),
    'lifestone':((90, 210, 210),  'lifestone'),
    'generator':((140, 140, 155), 'generator'),
    'obstacle': ((120, 120, 120), 'obstacle'),
    'scenery':  ((150, 150, 160), 'scenery object'),
    'gen_spawn': ((215, 25, 25), 'generator spawn point'),
    'void':     ((110, 110, 118), 'no floor (open shaft / solid)'),
}


WT = None          # filled from acworld.WEENIE_TYPE on first use

PICKUP_TYPES = {'Book', 'Key', 'Gem', 'Coin', 'Food', 'MeleeWeapon', 'Missile',
                'MissileLauncher', 'Ammunition', 'Caster', 'Clothing', 'Scroll',
                'SpellComponent', 'ManaStone', 'CraftTool', 'Stackable',
                'Lockpick', 'Healer', 'SkillAlterationDevice',
                'AttributeTransferDevice', 'Deed'}


def _wt(w):
    global WT
    if WT is None:
        WT = acworld.WEENIE_TYPE
    return WT.get(w.get('wtype'), '')


def overlap_fraction(cells, res=1.0, min_gap=2, floor_of=None):
    """Fraction of the plan where genuinely separate storeys stack.

    Adjacent z-bands are ignored: a hall whose floor slopes spans two bands and
    overlaps itself, which is not stacking. Only levels min_gap apart or more
    count, which is what a composite plan cannot show honestly.
    """
    pts = [p for c in cells for poly in c.floors for p in poly]
    if not pts:
        return 0.0
    minx = min(p[0] for p in pts); maxx = max(p[0] for p in pts)
    miny = min(p[1] for p in pts); maxy = max(p[1] for p in pts)
    w = max(1, int((maxx - minx) / res) + 2)
    h = max(1, int((maxy - miny) / res) + 2)
    by_level = collections.defaultdict(list)
    for c in cells:
        if floor_of is not None:
            key = floor_of.get(c.cell_id & 0xFFFF)
            if key is None:
                continue
        else:
            key = int(round(c.origin[2] / 6.0))
        by_level[key].extend(c.floors)
    if floor_of is not None:
        min_gap = 1
    masks = {}
    for lvl, polys in by_level.items():
        img = Image.new('L', (w, h), 0)
        d = ImageDraw.Draw(img)
        for poly in polys:
            d.polygon([((p[0] - minx) / res, (p[1] - miny) / res) for p in poly], fill=255)
        masks[lvl] = np.array(img) > 0
    painted = np.zeros((h, w), dtype=bool)
    stacked = np.zeros((h, w), dtype=bool)
    levels = sorted(masks)
    for lvl in levels:
        painted |= masks[lvl]
    for i, a in enumerate(levels):
        for b in levels[i + 1:]:
            if b - a >= min_gap:
                stacked |= masks[a] & masks[b]
    if not painted.any():
        return 0.0
    return float(stacked.sum() / painted.sum())


def drop_shell_floors(cells, insts, floor_of, res=1.0, cover=0.85):
    """Remove structural shells from a composite plan.

    A floor with nothing on it that sits almost entirely inside the footprint
    of other floors is a roof or an empty storey shell. Drawing it just buries
    whatever it covers, which is what makes a keep-over-a-hall unreadable.
    Returns the cells worth compositing.
    """
    by_floor = collections.defaultdict(list)
    for c in cells:
        f = floor_of.get(c.cell_id & 0xFFFF)
        if f is not None:
            by_floor[f].append(c)
    cell_floor = {c.cell_id & 0xFFFF: floor_of.get(c.cell_id & 0xFFFF) for c in cells}
    objects = collections.Counter(cell_floor.get(i['cell']) for i in insts)
    pts = [p for c in cells for poly in c.floors for p in poly]
    if not pts:
        return cells, set()
    minx = min(p[0] for p in pts); miny = min(p[1] for p in pts)
    w = int((max(p[0] for p in pts) - minx) / res) + 2
    h = int((max(p[1] for p in pts) - miny) / res) + 2

    def mask_of(group):
        img = Image.new('L', (w, h), 0)
        d = ImageDraw.Draw(img)
        for c in group:
            for poly in c.floors:
                d.polygon([((p[0] - minx) / res, (p[1] - miny) / res) for p in poly], fill=255)
        return np.array(img) > 0

    masks = {f: mask_of(g) for f, g in by_floor.items()}
    dropped = set()
    for f in sorted(masks, key=lambda k: -k):
        if objects.get(f, 0) > 0:
            continue
        others = np.zeros_like(masks[f])
        for g, m in masks.items():
            if g != f and g not in dropped:
                others |= m
        area = masks[f].sum()
        if area and (masks[f] & others).sum() / area >= cover:
            dropped.add(f)
    keep = [c for c in cells if floor_of.get(c.cell_id & 0xFFFF) not in dropped]
    return keep, dropped


def explode(cells, insts, floor_of, gap=2.0, res=1.5, iterations=300,
            min_overlap=0.30):
    """Slide overlapping floors apart -- by as little as possible.

    Overlap is tested on the actual footprints, not bounding boxes: dungeon
    floors interleave, and two L-shaped wings can share a bounding box while
    never touching. Pieces are translated only, the largest is pinned so the
    map stays put, and each push is one grid step, so a floor moves only as far
    as it must. Connections severed by the move come back as matching numbers.
    """
    from .geom import shift_cell

    pieces = collections.defaultdict(list)
    for c in cells:
        pieces[floor_of.get(c.cell_id & 0xFFFF, 0)].append(c)
    pts_all = [p for c in cells for poly in c.floors for p in poly]
    if not pts_all:
        return cells, insts, []
    minx = min(p[0] for p in pts_all); maxx = max(p[0] for p in pts_all)
    miny = min(p[1] for p in pts_all); maxy = max(p[1] for p in pts_all)
    pad = int(max(maxx - minx, maxy - miny) / res)
    w = int((maxx - minx) / res) + 2 * pad + 4
    h = int((maxy - miny) / res) + 2 * pad + 4
    g = max(1, int(round(gap / res)))

    masks, areas, cents = {}, {}, {}
    for k, group in pieces.items():
        img = Image.new('L', (w, h), 0)
        d = ImageDraw.Draw(img)
        for c in group:
            for poly in c.floors:
                d.polygon([((p[0] - minx) / res + pad, (p[1] - miny) / res + pad)
                           for p in poly], fill=255)
        m = np.array(img) > 0
        if not m.any():
            continue
        for _ in range(g):                      # dilate to enforce the gap
            m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
        ys, xs = np.nonzero(m)
        masks[k] = m
        areas[k] = int(m.sum())
        cents[k] = (float(xs.mean()), float(ys.mean()))
    keys = sorted(masks, key=lambda k: -areas[k])
    off = {k: [0, 0] for k in keys}
    anchor = keys[0] if keys else None

    # Only pieces that genuinely sit on top of something get moved. A floor
    # that merely clips a corner of another stays exactly where it belongs --
    # most of a sprawling dungeon is one contiguous mass and should read that
    # way. When a pair does conflict, the smaller of the two is the one that
    # gives way.
    movable = set()
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            inter = int((masks[a] & masks[b]).sum())
            if not inter:
                continue
            if inter / min(areas[a], areas[b]) >= min_overlap:
                movable.add(b if areas[b] <= areas[a] else a)
    if not movable:
        return cells, insts, []

    def hits(a, b):
        dx = off[b][0] - off[a][0]
        dy = off[b][1] - off[a][1]
        A, B = masks[a], masks[b]
        if abs(dx) >= w or abs(dy) >= h:
            return False
        return (A & np.roll(np.roll(B, dy, 0), dx, 1)).any()

    for _ in range(iterations):
        moved = False
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if a not in movable and b not in movable:
                    continue
                if not hits(a, b):
                    continue
                moved = True
                ax = cents[a][0] + off[a][0]; ay = cents[a][1] + off[a][1]
                bx = cents[b][0] + off[b][0]; by = cents[b][1] + off[b][1]
                ddx, ddy = bx - ax, by - ay
                if abs(ddx) < 1e-6 and abs(ddy) < 1e-6:
                    ddx = 1.0
                if abs(ddx) >= abs(ddy):
                    step = (1 if ddx > 0 else -1, 0)
                else:
                    step = (0, 1 if ddy > 0 else -1)
                if b in movable:
                    off[b][0] += step[0]; off[b][1] += step[1]
                if a in movable:
                    off[a][0] -= step[0]; off[a][1] -= step[1]
        if not moved:
            break

    # compaction: pull every piece back toward where it belongs, one step at a
    # time, for as long as it stays clear. Separation overshoots; this undoes
    # the part of the overshoot that was never needed.
    for _ in range(iterations):
        pulled = False
        for k in keys:
            if k not in movable:
                continue
            for axis in (0, 1):
                while off[k][axis] != 0:
                    step = -1 if off[k][axis] > 0 else 1
                    off[k][axis] += step
                    if any(hits(k, o) for o in keys if o != k):
                        off[k][axis] -= step
                        break
                    pulled = True
        if not pulled:
            break

    world_off = {k: (off[k][0] * res, off[k][1] * res) for k in off}
    cell_off = {}
    out_cells = []
    for k, group in pieces.items():
        dx, dy = world_off.get(k, (0.0, 0.0))
        for c in group:
            cell_off[c.cell_id & 0xFFFF] = (dx, dy)
            out_cells.append(shift_cell(c, dx, dy) if (dx or dy) else c)
    out_insts = []
    for i in insts:
        dx, dy = cell_off.get(i['cell'], (0.0, 0.0))
        out_insts.append(dict(i, x=i['x'] + dx, y=i['y'] + dy) if (dx or dy) else i)

    by_id = {c.cell_id & 0xFFFF: c for c in cells}
    raw = []
    for key, c in by_id.items():
        fa = floor_of.get(key)
        for _f, _p, other, _o in c.portals:
            o = by_id.get(other)
            if o is None or other <= key:
                continue
            fb = floor_of.get(other)
            if fa is None or fb is None or fa == fb:
                continue
            if world_off.get(fa, (0, 0)) == world_off.get(fb, (0, 0)):
                continue
            raw.append((key, other, (c.origin[0] + o.origin[0]) / 2,
                        (c.origin[1] + o.origin[1]) / 2))
    clusters = []
    for key, other, mx, my in raw:
        pair = (floor_of[key], floor_of[other])
        for cl in clusters:
            if cl['pair'] == pair and abs(cl['mx'] - mx) < 14 and abs(cl['my'] - my) < 14:
                cl['links'].append((key, other))
                break
        else:
            clusters.append(dict(mx=mx, my=my, links=[(key, other)], pair=pair))
    connectors = []
    for n, cl in enumerate(sorted(clusters, key=lambda c: (-c['my'], c['mx'])), start=1):
        key, other = cl['links'][0]
        a = by_id[key].origin
        b = by_id[other].origin
        oa = world_off.get(floor_of[key], (0.0, 0.0))
        ob = world_off.get(floor_of[other], (0.0, 0.0))
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        pa = (a[0] + (mx - a[0]) * 0.82 + oa[0], a[1] + (my - a[1]) * 0.82 + oa[1])
        pb = (b[0] + (mx - b[0]) * 0.82 + ob[0], b[1] + (my - b[1]) * 0.82 + ob[1])
        connectors.append((n, pa, pb))
    return out_cells, out_insts, connectors


def _connectors(by_id, floor_of, world_off):
    """Number every link severed by moving floors apart, both ends matching."""
    raw = []
    for key, c in by_id.items():
        fa = floor_of.get(key)
        for _f, _p, other, _o in c.portals:
            o = by_id.get(other)
            if o is None or other <= key:
                continue
            fb = floor_of.get(other)
            if fa is None or fb is None or fa == fb:
                continue
            if world_off.get(fa, (0.0, 0.0)) == world_off.get(fb, (0.0, 0.0)):
                continue
            raw.append((key, other, (c.origin[0] + o.origin[0]) / 2,
                        (c.origin[1] + o.origin[1]) / 2))
    clusters = []
    for key, other, mx, my in raw:
        pair = (floor_of[key], floor_of[other])
        for cl in clusters:
            if cl['pair'] == pair and abs(cl['mx'] - mx) < 14 and abs(cl['my'] - my) < 14:
                cl['links'].append((key, other))
                break
        else:
            clusters.append(dict(mx=mx, my=my, links=[(key, other)], pair=pair))
    out = []
    for n, cl in enumerate(sorted(clusters, key=lambda c: (-c['my'], c['mx'])), start=1):
        key, other = cl['links'][0]
        a = by_id[key].origin
        b = by_id[other].origin
        oa = world_off.get(floor_of[key], (0.0, 0.0))
        ob = world_off.get(floor_of[other], (0.0, 0.0))
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        out.append((n,
                    (a[0] + (mx - a[0]) * 0.82 + oa[0], a[1] + (my - a[1]) * 0.82 + oa[1]),
                    (b[0] + (mx - b[0]) * 0.82 + ob[0], b[1] + (my - b[1]) * 0.82 + ob[1])))
    return out


def flow_layout(cells, insts, floor_of, gap=24.0):
    """Lay every floor out separately, packed in reading order by depth.

    This is how the hand-drawn maps handle a dungeon whose floors sit on top of
    each other: stop pretending one plan can show it, place each floor in clear
    space, and join the cut corridors with matching numbers.
    """
    from .geom import shift_cell

    pieces = collections.defaultdict(list)
    for c in cells:
        f = floor_of.get(c.cell_id & 0xFFFF)
        if f is not None and c.floors:
            pieces[f].append(c)
    if len(pieces) < 2:
        return cells, insts, []

    box = {}
    for k, group in pieces.items():
        pts = [p for c in group for poly in c.floors for p in poly]
        box[k] = (min(p[0] for p in pts), min(p[1] for p in pts),
                  max(p[0] for p in pts), max(p[1] for p in pts))
    order = sorted(pieces, reverse=True)          # entry level first
    widths = [box[k][2] - box[k][0] for k in order]
    heights = [box[k][3] - box[k][1] for k in order]
    area = sum(w * h for w, h in zip(widths, heights))
    target = max(max(widths) + gap, (area * 1.6) ** 0.5)

    world_off = {}
    x = y = 0.0
    row_h = 0.0
    for k in order:
        w = box[k][2] - box[k][0]
        h = box[k][3] - box[k][1]
        if x > 0 and x + w > target:
            x = 0.0
            y -= row_h + gap
            row_h = 0.0
        world_off[k] = (x - box[k][0], y - box[k][3])
        x += w + gap
        row_h = max(row_h, h)

    out_cells, cell_off = [], {}
    for k, group in pieces.items():
        dx, dy = world_off[k]
        for c in group:
            cell_off[c.cell_id & 0xFFFF] = (dx, dy)
            out_cells.append(shift_cell(c, dx, dy))
    for c in cells:
        if (c.cell_id & 0xFFFF) not in cell_off:
            out_cells.append(c)
    out_insts = [dict(i, x=i['x'] + cell_off.get(i['cell'], (0, 0))[0],
                      y=i['y'] + cell_off.get(i['cell'], (0, 0))[1]) for i in insts]
    by_id = {c.cell_id & 0xFFFF: c for c in cells}
    return out_cells, out_insts, _connectors(by_id, floor_of, world_off)


def classify(world, wcid):
    """Map a weenie to a marker kind. Keyed off WeenieType names so the
    numbering can shift between client eras without silently mis-typing."""
    w = world.weenies.get(wcid)
    if not w:
        return 'item', None
    t = _wt(w)
    name = world.name(wcid)
    low = name.lower()
    if t == 'Portal' or t == 'HousePortal':
        return 'exit', None
    if t == 'Door':
        return ('door_lock' if w['bools'].get('Locked') else 'door'), None
    if t in ('Chest', 'Container', 'Storage'):
        return ('chest_lock' if w['bools'].get('Locked') else 'chest'), None
    if t == 'PressurePlate':
        return ('trap' if 'trap' in low else 'plate'), None
    if t == 'Switch':
        if 'trap' in low:
            return 'trap', None
        if 'lever' in low or 'switch' in low or 'button' in low:
            return 'lever', None
        return 'scenery', None
    if t == 'LifeStone':
        return 'lifestone', None
    if t == 'HotSpot':
        dt = damage_name(w['ints'].get('DamageType', 0))
        # damage separates a lava floor from warm air rising through a grate
        return 'hotspot', (dt, w['ints'].get('Damage', 0), name)
    if t in ('Creature', 'Vendor', 'Cow'):
        if t == 'Vendor' or w['bools'].get('Attackable') is False:
            return 'npc', None
        return 'creature', None
    if 'generator' in low or low.startswith('linkable') or ' gen ' in low or low.endswith(' gen') or low.endswith('gen!'):
        return 'generator', None
    if t in PICKUP_TYPES:
        return 'item', None
    if 'locked door' in low or low == 'door':
        return 'door', None
    return 'scenery', None


def compute_floors(cells, arrival_cells=()):
    """Group raw z-bands into physical floors.

    A hall whose floor slopes spans two z-bands but is one floor; a keep with
    four storeys over a hall shares one footprint but is five floors. Neither
    is visible from z alone, so this walks the cell portal graph: flat cells
    whose surfaces touch are the same floor, and a ramp is attached to the
    floor at its lower end -- it is how you leave a floor, not part of two.
    """
    by_id = {c.cell_id & 0xFFFF: c for c in cells}
    # cells with no walkable floor (ceiling caps) are not part of any floor
    parent = {k: k for k, c in by_id.items() if c.floors}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def is_ramp(c):
        return any(sl['rise'] >= 1.0 for sl in c.slopes)

    for key, c in by_id.items():
        if not c.floors or is_ramp(c):
            continue
        for _f, _p, other, _o in c.portals:
            o = by_id.get(other)
            if o is None or not o.floors or is_ramp(o):
                continue
            if abs(o.zmin - c.zmin) <= 1.5:
                union(key, other)
    # ramps join the floor at their lower end
    for key, c in by_id.items():
        if not c.floors or not is_ramp(c):
            continue
        best = None
        for _f, _p, other, _o in c.portals:
            o = by_id.get(other)
            if o is None or not o.floors:
                continue
            d = abs(o.zmin - c.zmin)
            if best is None or d < best[0]:
                best = (d, other)
        if best:
            union(best[1], key)

    # absorb fragments: a two-cell stub is a doorway or a stair landing, never
    # a floor of its own. Merge it into whichever floor it connects to most.
    for _pass in range(8):
        sizes = collections.Counter(find(k) for k in parent)
        merged = False
        for key in list(parent):
            root = find(key)
            if sizes[root] >= 10:
                continue
            votes = collections.Counter()
            for member in [k for k in parent if find(k) == root]:
                for _f, _p, other, _o in by_id[member].portals:
                    if other in parent and find(other) != root:
                        votes[find(other)] += 1
            if votes:
                union(votes.most_common(1)[0][0], root)
                merged = True
        if not merged:
            break

    # One hall can have a lava bed, a walkway and a gallery at 6 m spacing --
    # a single space seen at three heights, not three floors. Where two groups
    # share most of their footprint and sit within a storey of each other,
    # they are the same place and get merged.
    for _pass in range(4):
        roots = collections.defaultdict(list)
        for key in parent:
            roots[find(key)].append(key)
        if len(roots) < 2:
            break
        pts = [p for k in parent for poly in by_id[k].floors for p in poly]
        if not pts:
            break
        ox, oy = min(p[0] for p in pts), min(p[1] for p in pts)
        gw = int(max(p[0] for p in pts) - ox) + 3
        gh = int(max(p[1] for p in pts) - oy) + 3
        masks, zmid = {}, {}
        for r, members in roots.items():
            img = Image.new('L', (gw, gh), 0)
            d = ImageDraw.Draw(img)
            for k in members:
                for poly in by_id[k].floors:
                    d.polygon([(p[0] - ox, p[1] - oy) for p in poly], fill=255)
            m = np.array(img) > 0
            if m.any():
                masks[r] = m
                zmid[r] = sum(by_id[k].zmin for k in members) / len(members)
        merged = False
        rs = list(masks)
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                if find(a) == find(b):
                    continue
                inter = int((masks[a] & masks[b]).sum())
                if not inter:
                    continue
                if inter / min(masks[a].sum(), masks[b].sum()) >= 0.5 \
                        and abs(zmid[a] - zmid[b]) <= 8.0:
                    union(a, b)
                    merged = True
        if not merged:
            break

    groups = collections.defaultdict(list)
    for key in parent:
        groups[find(key)].append(key)
    order = sorted(groups, key=lambda r: -sum(by_id[k].zmin for k in groups[r]) / len(groups[r]))
    base = 0
    for rank, root in enumerate(order):
        if any(a in groups[root] for a in arrival_cells):
            base = rank
            break
    floor_of = {}
    for rank, root in enumerate(order):
        for k in groups[root]:
            floor_of[k] = base - rank
    return floor_of


class LevelMap:
    def __init__(self, cells, insts, world, links, floor_of=None):
        self.cells = cells
        self.by_cell = {c.cell_id & 0xFFFF: c for c in cells}
        self.insts = insts
        self.world = world
        self.links = links
        self.floor_of = floor_of or {}
        self.levels = collections.defaultdict(list)   # floor -> [cell]
        for c in cells:
            if not c.floors:          # ceiling caps belong to no floor
                continue
            self.levels[self.level_of(c)].append(c)
        self.bridges = self._bridges()
        self.ramps = self._ramps()

    def level_of(self, c):
        key = c.cell_id & 0xFFFF
        if key in self.floor_of:
            return self.floor_of[key]
        return int(round(c.origin[2] / 6.0))

    def components(self, group):
        """Split a level into connected wings using the cell portal graph."""
        ids = {c.cell_id & 0xFFFF for c in group}
        by = {c.cell_id & 0xFFFF: c for c in group}
        seen = set()
        out = []
        for start in sorted(ids):
            if start in seen:
                continue
            stack = [start]
            comp = []
            seen.add(start)
            while stack:
                cur = stack.pop()
                comp.append(by[cur])
                for _fl, _poly, other, _op in by[cur].portals:
                    if other in ids and other not in seen:
                        seen.add(other)
                        stack.append(other)
            out.append(comp)
        return out

    def _ramps(self):
        """Per-polygon slopes, so a room with one ramp segment is not shaded whole."""
        out = {}
        for c in self.cells:
            if c.slopes:
                out[c.cell_id] = c.slopes
        return out

    def _bridges(self):
        """Cells whose footprint sits directly above another cell's floor."""
        boxes = []
        for c in self.cells:
            if not c.floors:
                continue
            xs = [p[0] for poly in c.floors for p in poly]
            ys = [p[1] for poly in c.floors for p in poly]
            boxes.append((c, min(xs), min(ys), max(xs), max(ys)))
        out = set()
        for c, x0, y0, x1, y1 in boxes:
            # only narrow (corridor-like) spans count as a bridge; a room that
            # merely sits above another room is just a different level
            if min(x1 - x0, y1 - y0) > 13:
                continue
            for o, ox0, oy0, ox1, oy1 in boxes:
                if o is c or o.zmin >= c.zmin - 3:
                    continue
                if min(ox1 - ox0, oy1 - oy0) < 13:
                    continue
                if x0 < ox1 - 1 and ox0 < x1 - 1 and y0 < oy1 - 1 and oy0 < y1 - 1:
                    out.add(c.cell_id)
                    break
        return out


def render(lb, cells, insts, links, world, path, style='dungeon',
           scale=8, show_generators=False, show_obstacles=False, show_voids=False,
           void_gap=12.0,
           debug_cells=False, bbox=None, chrome=True, tone_level=None,
           connectors=(), show_walls=True, label_repeat_max=3,
           npc_label_max=10,
           title=None, subtitle=None, header_lines=None, label_items=True):
    st = STYLES[style]
    arrival_cells = {ep['dest'][0] & 0xFFFF for ep in world.entry_portals(lb)
                     if ep['dest']}
    lm = LevelMap(cells, insts, world, links,
                  floor_of=compute_floors(cells, arrival_cells))
    pts_all = [p for c in cells for poly in c.floors for p in poly]
    if not pts_all:
        return None
    if bbox:
        minx, maxx, miny, maxy = bbox
    else:
        minx = min(p[0] for p in pts_all) - 6
        maxx = max(p[0] for p in pts_all) + 6
        miny = min(p[1] for p in pts_all) - 6
        maxy = max(p[1] for p in pts_all) + 6

    # pre-pass: roster and generator-spawned creatures, so the margins can be
    # sized to fit them before anything is drawn
    roster = collections.defaultdict(collections.Counter)
    gen_spawns = []
    link_children = {k for kids in (links or {}).values() for k in kids}
    for i in insts:
        kind, _x = classify(world, i['wcid'])
        w = world.weenies.get(i['wcid'], {})
        if kind in ('creature', 'npc'):
            ct = acworld.CREATURE_TYPE.get(w.get('ints', {}).get('CreatureType', 0), 'Other')
            roster[ct][world.name(i['wcid'])] += 1
        elif kind == 'generator' and w.get('gen') and not (links or {}).get(i['guid']):
            for g in w['gen']:
                gw = world.weenies.get(g['wcid'], {})
                if _wt(gw) not in ('Creature', 'Vendor', 'Cow'):
                    continue
                ct = acworld.CREATURE_TYPE.get(gw.get('ints', {}).get('CreatureType', 0), 'Other')
                roster[ct][world.name(g['wcid'])] += 1
                gen_spawns.append((g['wcid'], i['x'] if not g['cell'] else g['x'],
                                   i['y'] if not g['cell'] else g['y']))
    bosses = []
    seen_boss = set()
    for wcid, x, y in gen_spawns:
        w = world.weenies.get(wcid, {})
        if w.get('ints', {}).get('Level', 0) >= 80 and wcid not in seen_boss:
            seen_boss.add(wcid)
            bosses.append((wcid, x, y))
    counts_by_wcid = collections.Counter(i['wcid'] for i in insts)
    for i in insts:
        w = world.weenies.get(i['wcid'], {})
        if (classify(world, i['wcid'])[0] == 'creature'
                and counts_by_wcid[i['wcid']] == 1
                and w.get('ints', {}).get('Level', 0) >= 90
                and i['wcid'] not in seen_boss):
            seen_boss.add(i['wcid'])
            bosses.append((i['wcid'], i['x'], i['y']))

    roster_rows = sum(1 + len(v) for v in roster.values())
    if chrome:
        head_h = 54 + 36 * (1 + len(header_lines or []))
        pad_l = 60
        if roster:
            probe0 = ImageDraw.Draw(Image.new('RGB', (1, 1)))
            f_r = _font(30)
            widest = 0
            for ct, names in roster.items():
                widest = max(widest, probe0.textlength('%s:' % ct, font=_font(34, True)))
                for nm, n in names.most_common(6):
                    widest = max(widest, 16 + probe0.textlength('- %s  x%d' % (nm, n), font=f_r))
            for wcid, _gx, _gy in bosses[:3]:
                for item in (world.weenies.get(wcid, {}).get('loot') or [])[:5]:
                    widest = max(widest, 16 + probe0.textlength('- %s' % item, font=f_r))
            pad_l = int(min(max(460, widest + 70), 900))
        # the legend wraps, so size the bottom margin to the number of entries
        kinds = set()
        for i in insts:
            k, extra = classify(world, i['wcid'])
            if k == 'hotspot':
                kinds.add('hot:%s' % extra[2])
            elif k in ('chest', 'chest_lock'):
                kinds.add('chest:%s' % world.name(i['wcid']))
            elif k == 'trap':
                kinds.add('trap')
            else:
                kinds.add(k)
        rows = max(1, (len(kinds) + 2) // 3)
        pad_r, pad_b = 60, 150 + 46 * rows
    else:
        head_h, pad_l, pad_r, pad_b = 30, 12, 12, 12
    W = int((maxx - minx) * scale) + pad_l + pad_r
    H = int((maxy - miny) * scale) + head_h + pad_b
    if chrome:
        # the chrome sets a floor on the width: a short dungeon with a long
        # title or a long legend row must not have its text clipped
        probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        need = [980]
        title_txt = title or (world.dungeon_name(lb) or 'Landblock 0x%04X' % lb)
        need.append(pad_l + int(probe.textlength(title_txt, font=_font(44, True))) + pad_r)
        if subtitle:
            need.append(pad_l + int(probe.textlength(subtitle, font=_font(34, True))) + pad_r)
        for line in (header_lines or []):
            need.append(pad_l + int(probe.textlength(line, font=_font(30))) + pad_r)
        need.append(120 + int(probe.textlength(
            'geometry: client_cell_1.dat + portal.dat 0x0D meshes | objects: '
            'ACE-World  |  landblock 0x0000', font=_font(20))))
        W = max(W, *need)

    def T(x, y):
        return (pad_l + (x - minx) * scale, head_h + (maxy - y) * scale)

    canvas = Image.new('RGB', (W, H), st['paper'])
    arr = np.array(canvas)

    hotspot_cells = {}
    vent_cells = {}
    for i in insts:
        kind, extra = classify(world, i['wcid'])
        if kind == 'hotspot':
            dt, dmg, nm = extra
            if dmg < HAZARD_DAMAGE:
                vent_cells.setdefault(i['cell'], (dt, nm))
            else:
                hotspot_cells.setdefault(i['cell'], (dt, nm))


    level_masks = []
    for lvl in sorted(lm.levels, reverse=True):
        group = lm.levels[lvl]
        mask = _poly_mask(W, H, [poly for c in group for poly in c.floors], T)
        tone_key = abs(lvl if tone_level is None else tone_level)
        tone = st['fills'][min(tone_key, len(st['fills']) - 1)]
        arr[mask] = tone
        for c in group:
            if not c.floors:
                continue
            key = c.cell_id & 0xFFFF
            base = tone
            key = c.cell_id & 0xFFFF
            if key in hotspot_cells:
                hot = hotspot_color(hotspot_cells[key][0])
                base = tuple(int(t * 0.34 + h * 0.66) for t, h in zip(tone, hot))
            elif c.cell_id in lm.bridges:
                base = st['bridge']
            ramps = c.slopes
            if base is not tone:
                m2, bx, by = _poly_box(c.floors, T, W, H)
                if m2 is not None:
                    sub = arr[by:by + m2.shape[0], bx:bx + m2.shape[1]]
                    sub[m2] = base
                    if c.cell_id in lm.bridges:
                        yy, xx = np.mgrid[by:by + m2.shape[0], bx:bx + m2.shape[1]]
                        sub[m2 & (((xx + yy) // 7) % 2 == 0)] = \
                            tuple(int(v * 0.72) for v in base)
            for sl in ramps:
                m2, bx, by = _poly_box([sl['pts']], T, W, H)
                if m2 is None or not m2.any():
                    continue
                # gradient runs dark (bottom of the incline) to light (top);
                # the arrow drawn later points uphill
                yy, xx = np.mgrid[by:by + m2.shape[0], bx:bx + m2.shape[1]]
                ang = sl['angle']
                proj = (xx * math.cos(ang) - yy * math.sin(ang)).astype(np.float32)
                sel = proj[m2]
                lo, hi = float(sel.min()), float(sel.max())
                t = np.clip((proj - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
                b = np.array(base, dtype=np.float32)
                shade = (b[None, None, :] * (0.66 + 0.44 * t)[..., None]).clip(0, 255)
                sub = arr[by:by + m2.shape[0], bx:bx + m2.shape[1]]
                sub[m2] = shade[m2]
                if sl['kind'] == 'stairs':
                    step = ((proj // max(3, int(scale * 0.8))) % 2 == 0)
                    sm = m2 & step
                    sub[sm] = (sub[sm] * 0.9).astype(np.uint8)
        level_masks.append((lvl, mask))

    # damage floors are hatched over the finished plan, so a lava hall still
    # reads through the rooms stacked above it -- same trick the paper maps use
    by_key = {c.cell_id & 0xFFFF: c for c in cells}
    for cell_key, dt in hotspot_cells.items():
        c = by_key.get(cell_key)
        if c is None or not c.floors:
            continue
        m2, bx, by = _poly_box(c.floors, T, W, H)
        if m2 is None:
            continue
        yy, xx = np.mgrid[by:by + m2.shape[0], bx:bx + m2.shape[1]]
        hatch = m2 & (((xx + yy) // 4) % 3 == 0)
        col = np.array(hotspot_color(dt[0]), dtype=np.float32)
        sub = arr[by:by + m2.shape[0], bx:bx + m2.shape[1]]
        sub[m2] = (sub[m2] * 0.42 + col * 0.58).astype(np.uint8)
        sub[hatch] = (col * 0.82).astype(np.uint8)

    # low-damage hotspots are grates and vents you walk over, not hazards --
    # drawn as a grid, the way the hand-drawn maps mark them
    for cell_key, (dt, nm) in vent_cells.items():
        c = next((c for c in cells if (c.cell_id & 0xFFFF) == cell_key), None)
        if c is None or not c.floors:
            continue
        m2, bx, by = _poly_box(c.floors, T, W, H)
        if m2 is None or not m2.any():
            continue
        yy, xx = np.mgrid[by:by + m2.shape[0], bx:bx + m2.shape[1]]
        step = max(3, int(scale * 0.55))
        grid = m2 & ((xx % step < 2) | (yy % step < 2))
        sub = arr[by:by + m2.shape[0], bx:bx + m2.shape[1]]
        wash = np.array(hotspot_color(dt), dtype=np.float32)
        sub[m2] = (sub[m2] * 0.72 + wash * 0.28).astype(np.uint8)
        sub[grid] = (58, 52, 48)

    # cells with no walkable floor -- only a downward cap -- are open shafts or
    # solid fill. The client renders nothing there when seen from above.
    void_count = 0
    if show_voids:
        # Depth test, not a plain overlap test: a cap only punches through where
        # it is the topmost surface at that pixel. A cap with another floor
        # above it is an interior ceiling and stays invisible.
        NEG = np.float32(-1e9)
        floor_z = np.full((H, W), NEG, dtype=np.float32)
        for c in cells:
            if not c.floors:
                continue
            m2, bx, by = _poly_box(c.floors, T, W, H)
            if m2 is None:
                continue
            sub = floor_z[by:by + m2.shape[0], bx:bx + m2.shape[1]]
            np.maximum(sub, np.float32(c.zmax), out=sub, where=m2)
        # A level that is almost entirely caps is a roof or a ceiling layer for
        # the level below, not a set of holes -- skip those, or the roof erases
        # the whole building.
        per_level = collections.defaultdict(lambda: [0, 0])
        for c in cells:
            lv = lm.level_of(c)
            per_level[lv][0] += 1
            if not c.floors and c.voids:
                per_level[lv][1] += 1
        roof_levels = {lv for lv, (tot, caps) in per_level.items()
                       if tot and caps / tot >= 0.8}
        cap_z = np.full((H, W), NEG, dtype=np.float32)
        for c in cells:
            if c.floors or not c.voids:
                continue
            if lm.level_of(c) in roof_levels:
                continue
            m2, bx, by = _poly_box(c.voids, T, W, H)
            if m2 is None:
                continue
            sub = cap_z[by:by + m2.shape[0], bx:bx + m2.shape[1]]
            np.maximum(sub, np.float32(c.origin[2]), out=sub, where=m2)
            void_count += 1
        # A cap far above a floor is the building's roof, not a hole punched in
        # that floor -- only punch through when the cap sits close over it (or
        # over nothing at all).
        gap = cap_z - floor_z
        void = ((cap_z > NEG + 1)
                & (cap_z > floor_z)
                & ((floor_z < NEG + 1) | (gap <= void_gap)))
        if void.any():
            yy, xx = np.mgrid[0:H, 0:W]
            arr[void] = (arr[void] * 0.18
                         + np.array((126, 126, 134), np.float32) * 0.82).astype(np.uint8)
            cross = void & ((((xx - yy) // 3) % 4 == 0) | (((xx + yy) // 3) % 4 == 0))
            arr[cross] = (92, 92, 100)
            arr[_edge(void, 1)] = (58, 58, 64)

    # outlines last, so walls stay crisp where they meet a hatched floor
    for _lvl, mask in level_masks:
        arr[_edge(mask, st['outline_w'])] = st['outline']

    canvas = Image.fromarray(arr)
    dr = ImageDraw.Draw(canvas)

    # room walls, straight from the mesh. Without these a floor is one blob:
    # the cut-outs, the bridges over a lava bed and the rooms inside a hall all
    # disappear into the outer boundary.
    if show_walls:
        wcol = (58, 52, 58)
        for c in cells:
            if not c.floors:
                continue
            for a, b in c.walls:
                dr.line([T(*a), T(*b)], fill=wcol, width=max(2, scale // 5))

    if debug_cells:
        f_dbg = _font(max(9, scale))
        for c in cells:
            polys = c.floors or c.voids
            if not polys:
                continue
            pts = [p for poly in polys for p in poly]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            x, y = T(cx, cy)
            kind = 'F' if c.floors else 'C'
            col = (20, 90, 20) if c.floors else (170, 20, 120)
            for poly in polys:
                dr.line([T(p[0], p[1]) for p in poly] + [T(polys[0][0][0], polys[0][0][1])],
                        fill=col, width=1)
            dr.text((x - 16, y - 7), '%s%03X' % (kind, c.cell_id & 0xFFF),
                    fill=col, font=f_dbg)
    f_title = _font(44, True)
    f_sub = _font(34, True)
    f_head = _font(30)
    f_lvl = _font(24, True)
    f_lab = _font(16, True)
    f_leg = _font(34)

    # ---- level labels: one per connected wing of each level
    painted = np.zeros((H, W), dtype=bool)
    for _lvl, mask in level_masks:
        painted |= mask
    lvl_labels = []
    for lvl, group in (sorted(lm.levels.items()) if chrome else []):
        for comp in lm.components(group):
            if len(comp) < 4:            # skip stair stubs and single cells
                continue
            pts = [p for c in comp for poly in c.floors for p in poly]
            if len(pts) < 8:
                continue
            xs = [T(p[0], p[1])[0] for p in pts]
            ys = [T(p[0], p[1])[1] for p in pts]
            lvl_labels.append(((min(xs), min(ys), max(xs), max(ys)), 'LVL %d' % lvl))
    placed_boxes = []
    _place_level_tags(dr, lvl_labels, f_lvl, painted, W, H, placed_boxes, st['text'])
    for c in cells:
        for sl in c.slopes:
            if sl['rise'] < 0.8:
                continue
            pts = sl['pts']
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            _arrow(dr, T(cx, cy), sl['angle'], min(scale * 2.2, 20), st['ramp_hatch'])

    # ---- vertical connectors between levels (numbers replace them once the
    # floors have been slid apart)
    for c in (() if connectors else cells):
        for _fl, _poly, other, _op in c.portals:
            o = lm.by_cell.get(other)
            if o is None or not o.floors or not c.floors:
                continue
            if abs(o.origin[2] - c.origin[2]) < 4:
                continue
            if (c.cell_id & 0xFFFF) > other:
                continue
            a = T(*_centroid(c)[:2])
            b = T(*_centroid(o)[:2])
            dr.line([a, b], fill=(90, 90, 100), width=1)

    # ---- connector tags for corridors cut by the explode step
    f_conn = _font(max(16, int(scale * 2.1)), True)
    for n, a, b in connectors:
        for px, py in (T(*a), T(*b)):
            dr.text((px - 6, py - 11), str(n), fill=(190, 40, 40), font=f_conn,
                    stroke_width=3, stroke_fill=(255, 255, 255))

    # ---- markers
    counts = collections.Counter()
    trap_names = collections.Counter()
    chest_names = collections.Counter()
    labels = []
    repeats = collections.Counter()
    npc_total = 0
    for i in insts:
        repeats[world.name(i['wcid'])] += 1
        if classify(world, i['wcid'])[0] == 'npc':
            npc_total += 1
    by_guid = {i['guid']: i for i in insts}
    # number every switch-operated door and tag its openers with the same index
    door_no = {}
    opener_no = {}
    numbered = []
    for i in sorted(insts, key=lambda q: (-q['y'], q['x'])):
        if classify(world, i['wcid'])[0] not in ('door', 'door_lock'):
            continue
        kids = []
        for k in (links or {}).get(i['guid'], []):
            ki = next((q for q in insts if q['guid'] == k), None)
            if ki is not None and classify(world, ki['wcid'])[0] in ('lever', 'plate'):
                kids.append(ki)
        if kids:
            n = len(numbered) + 1
            door_no[i['guid']] = n
            numbered.append(i)
            for ki in kids:
                opener_no.setdefault(ki['guid'], []).append(n)
    child_of = {}
    for parent, kids in (links or {}).items():
        for k in kids:
            child_of[k] = parent

    def opener_kids(inst):
        """Levers / plates wired to this object."""
        out = []
        for k in (links or {}).get(inst['guid'], []):
            ki = by_guid.get(k)
            if ki is None:
                continue
            kind, _ = classify(world, ki['wcid'])
            if kind in ('lever', 'plate'):
                out.append((kind, ki))
        return out

    for i in insts:
        wcid = i['wcid']
        kind, extra = classify(world, wcid)
        if kind == 'hotspot':
            if extra[1] >= HAZARD_DAMAGE:
                counts['hotspot:%s|%s' % (extra[0], extra[2])] += 1
            continue
        if kind == 'generator' and not show_generators:
            counts['generator'] += 1
            continue
        if kind == 'scenery' and not show_obstacles:
            counts['scenery'] += 1
            continue
        nm = world.name(wcid)
        w = world.weenies.get(wcid, {})
        px, py = T(i['x'], i['y'])

        if kind in ('door', 'door_lock'):
            openers = opener_kids(i)
            if kind == 'door_lock':
                key = 'door_lock'
            elif openers:
                key = 'door_link'
            else:
                key = 'door'
            counts[key] += 1
            _door_glyph(dr, px, py, i, scale, MARKER[key][0])
            for okind, oi in openers:
                ox, oy = T(oi['x'], oi['y'])
                _dashed(dr, (px, py), (ox, oy), MARKER['door_link'][0])
            if key == 'door_lock':
                _key_glyph(dr, px, py, scale, MARKER[key][0])
            if i['guid'] in door_no and label_items:
                labels.append((px, py, 'D%d' % door_no[i['guid']],
                               MARKER['door_link'][0]))
            continue

        counts[kind] += 1
        col = MARKER[kind][0]
        if kind in ('chest', 'chest_lock'):
            r = scale * 0.8
            dr.rectangle([px - r, py - r, px + r, py + r], fill=col,
                         outline=(30, 30, 30), width=2)
        elif kind in ('trap', 'plate', 'lever'):
            r = scale * 0.7
            dr.polygon([(px, py - r), (px + r, py), (px, py + r), (px - r, py)],
                       fill=col, outline=(255, 255, 255))
            if i['guid'] in opener_no and label_items:
                labels.append((px, py,
                               '/'.join('L%d' % n for n in opener_no[i['guid']]),
                               MARKER['door_link'][0]))
        else:
            r = 3.8 if kind == 'creature' else 6.0
            if kind in ('arrival', 'exit'):
                r = 9
            dr.ellipse([px - r, py - r, px + r, py + r], fill=col,
                       outline=(255, 255, 255), width=2)

        if kind == 'trap':
            trap_names[nm] += 1
        if kind in ('chest', 'chest_lock'):
            chest_names[(nm, kind)] += 1
        # one Wailing Statue is worth naming on the plan; fifty-five is not,
        # and neither are forty-six uniquely named Wardens -- the roster has them
        want_label = (kind in ('exit', 'npc') and repeats[nm] <= label_repeat_max
                      and not (kind == 'npc' and npc_total > npc_label_max))
        if want_label and label_items:
            lab = 'E' if kind == 'exit' else nm
            labels.append((px, py, lab, col))

    # creatures that only exist through a generator profile
    for wcid, gx, gy in gen_spawns:
        px, py = T(gx, gy)
        dr.ellipse([px - 5, py - 5, px + 5, py + 5], outline=MARKER['creature'][0],
                   width=2)
        counts['gen_spawn'] += 1
    for wcid, gx, gy in bosses:
        px, py = T(gx, gy)
        dr.ellipse([px - 8, py - 8, px + 8, py + 8], fill=(120, 20, 20),
                   outline=(255, 255, 255), width=2)

    # arrival points: portals elsewhere that land here
    cell_ids = {c.cell_id & 0xFFFF for c in cells}
    for ep in world.entry_portals(lb):
        dest = ep['dest']
        if dest is None or (dest[0] & 0xFFFF) not in cell_ids:
            continue
        px, py = T(dest[1], dest[2])
        dr.ellipse([px - 9, py - 9, px + 9, py + 9], fill=MARKER['arrival'][0],
                   outline=(255, 255, 255), width=2)
        counts['arrival'] += 1
        labels.append((px, py, 'D', MARKER['arrival'][0]))

    if show_obstacles:
        for c in cells:
            for did, pos, rot in c.statics:
                from .geom import qrot
                wx, wy, wz = qrot(c.rot, pos)
                px, py = T(c.origin[0] + wx, c.origin[1] + wy)
                dr.ellipse([px - 2, py - 2, px + 2, py + 2], fill=MARKER['obstacle'][0])
                counts['obstacle'] += 1

    _draw_labels(dr, labels, f_lab, W, H, placed=placed_boxes)

    if not chrome:
        dr.text((16, 4), title or '', fill=st['text'], font=_font(32, True))
        canvas.save(path)
        return dict(cells=len(cells), levels=sorted(lm.levels), objects=len(insts),
                    ramps=len(lm.ramps), bridges=len(lm.bridges), counts=dict(counts))

    # ---- header
    dr.rectangle([0, 0, W, head_h - 8], fill=(255, 255, 255))
    t = title or (world.dungeon_name(lb) or 'Landblock 0x%04X' % lb)
    dr.text((pad_l, 14), t, fill=st['text'], font=f_title)
    y = 66
    if subtitle:
        dr.text((pad_l, y), subtitle, fill=st['sub'], font=f_sub)
        y += 42
    for line in (header_lines or []):
        dr.text((pad_l, y), line, fill=st['sub'], font=f_head)
        y += 36
    # roster down the left margin, the way the paper maps do it
    ry = head_h + 16
    for ct, names in sorted(roster.items(), key=lambda kv: -sum(kv[1].values())):
        if ry > H - pad_b - 60:
            break
        dr.text((24, ry), '%s:' % ct, fill=(180, 30, 30), font=f_sub)
        ry += 38
        for nm, n in names.most_common(6):
            dr.text((40, ry), '- %s  x%d' % (nm, n), fill=(120, 40, 40), font=f_head)
            ry += 34
        ry += 10
    # boss loot goes under the roster in the left column, where nothing else
    # is competing for the space
    for wcid, _gx, _gy in bosses[:3]:
        loot = world.weenies.get(wcid, {}).get('loot')
        if not loot or ry > H - pad_b - 60:
            continue
        dr.text((24, ry), '%s drops:' % world.name(wcid), fill=(180, 30, 30), font=f_sub)
        ry += 38
        for item in loot[:5]:
            dr.text((40, ry), '- %s' % item, fill=(30, 110, 140), font=f_head)
            ry += 34
        ry += 10

    # ---- legend + scale + north
    for _k, (dt, nm) in vent_cells.items():
        counts['vent:%s|%s' % (dt, nm)] += 1
    counts['void'] = void_count
    _legend(dr, counts, hotspot_cells, lm, f_leg, 60, H - pad_b + 30, W,
            trap_names, chest_names)
    _scalebar(dr, W - 40, H - 74, scale, f_leg, st['text'])
    _north(dr, 60, 60, st['text'])
    dr.text((60, H - 32),
            'geometry: client_cell_1.dat + portal.dat 0x0D meshes | objects: ACE-World  |  '
            'landblock 0x%04X' % lb, fill=(120, 120, 130), font=_font(20))
    canvas.save(path)
    return dict(cells=len(cells), levels=sorted(lm.levels), objects=len(insts),
                ramps=len(lm.ramps), bridges=len(lm.bridges), counts=dict(counts))


# ------------------------------------------------------------------- helpers
def _centroid(c):
    pts = [p for poly in c.floors for p in poly]
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n,
            sum(p[2] for p in pts) / n)


def _poly_box(polys, T, W, H, margin=2):
    """Rasterise polygons into a small local box: (mask, x0, y0)."""
    pts = [T(p[0], p[1]) for poly in polys for p in poly]
    if not pts:
        return None, 0, 0
    x0 = max(int(min(p[0] for p in pts)) - margin, 0)
    y0 = max(int(min(p[1] for p in pts)) - margin, 0)
    x1 = min(int(max(p[0] for p in pts)) + margin, W)
    y1 = min(int(max(p[1] for p in pts)) + margin, H)
    if x1 <= x0 or y1 <= y0:
        return None, 0, 0
    img = Image.new('L', (x1 - x0, y1 - y0), 0)
    d = ImageDraw.Draw(img)
    for poly in polys:
        d.polygon([(T(p[0], p[1])[0] - x0, T(p[0], p[1])[1] - y0) for p in poly], fill=255)
    return np.array(img) > 0, x0, y0


def _poly_mask(W, H, polys, T):
    img = Image.new('L', (W, H), 0)
    d = ImageDraw.Draw(img)
    for poly in polys:
        d.polygon([T(p[0], p[1]) for p in poly], fill=255)
    return np.array(img) > 0


def _edge(mask, width=2):
    er = mask.copy()
    for _ in range(width):
        e = er.copy()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            e &= np.roll(er, (dy, dx), (0, 1))
        er = e
    return mask & ~er


def _door_glyph(dr, px, py, inst, scale, col):
    """A doorway drawn where the door actually stands, across the opening."""
    yaw = 2.0 * math.atan2(inst.get('az', 0.0), inst.get('aw', 1.0))
    half = scale * 1.5
    dx, dy = math.cos(yaw) * half, -math.sin(yaw) * half
    dr.line([(px - dx, py - dy), (px + dx, py + dy)], fill=col, width=max(3, scale // 3))
    # end pips, so a door reads as a door and not as a wall segment
    nx, ny = -dy / half * (scale * 0.45), dx / half * (scale * 0.45)
    for sx, sy in ((px - dx, py - dy), (px + dx, py + dy)):
        dr.line([(sx - nx, sy - ny), (sx + nx, sy + ny)], fill=col, width=2)


def _key_glyph(dr, px, py, scale, col):
    """Small key hung off a locked door."""
    s = max(6.5, scale * 0.95)
    cx, cy = px + s * 1.15, py - s * 1.15
    dr.ellipse([cx - s * 0.45, cy - s * 0.45, cx + s * 0.45, cy + s * 0.45],
               outline=col, width=max(2, int(s * 0.28)))
    dr.line([(cx + s * 0.4, cy + s * 0.15), (cx + s * 1.5, cy + s * 0.95)],
            fill=col, width=max(2, int(s * 0.28)))
    dr.line([(cx + s * 1.15, cy + s * 0.62), (cx + s * 0.85, cy + s * 0.95)],
            fill=col, width=max(2, int(s * 0.24)))
    dr.line([(cx + s * 1.45, cy + s * 0.92), (cx + s * 1.15, cy + s * 1.25)],
            fill=col, width=max(2, int(s * 0.24)))


def _lever_glyph(dr, px, py, scale, col, plate=False):
    """Small lever handle (or a plate square) beside a switch-operated door."""
    s = max(6.5, scale * 0.95)
    cx, cy = px + s * 1.2, py - s * 1.2
    if plate:
        dr.rectangle([cx - s * 0.7, cy - s * 0.45, cx + s * 0.7, cy + s * 0.45],
                     outline=col, width=max(2, int(s * 0.26)))
        dr.line([(cx - s * 0.35, cy), (cx + s * 0.35, cy)], fill=col,
                width=max(2, int(s * 0.22)))
    else:
        dr.line([(cx - s * 0.55, cy + s * 0.7), (cx + s * 0.55, cy - s * 0.7)],
                fill=col, width=max(2, int(s * 0.3)))
        dr.ellipse([cx + s * 0.25, cy - s * 1.0, cx + s * 0.9, cy - s * 0.35],
                   fill=col)
        dr.line([(cx - s * 0.85, cy + s * 0.7), (cx - s * 0.25, cy + s * 0.7)],
                fill=col, width=max(2, int(s * 0.26)))


def _dashed(dr, a, b, col, dash=7, gap=5):
    ax, ay = a
    bx, by = b
    dist = math.hypot(bx - ax, by - ay)
    if dist < 1 or dist > 700:
        return
    ux, uy = (bx - ax) / dist, (by - ay) / dist
    t = 0.0
    while t < dist:
        e = min(t + dash, dist)
        dr.line([(ax + ux * t, ay + uy * t), (ax + ux * e, ay + uy * e)],
                fill=col, width=2)
        t = e + gap


def _arrow(dr, p, ang, size, col):
    x, y = p
    dx, dy = math.cos(ang) * size, -math.sin(ang) * size
    dr.line([(x - dx * 0.6, y - dy * 0.6), (x + dx * 0.6, y + dy * 0.6)], fill=col, width=3)
    for s in (0.6, -0.6):
        dr.line([(x + dx * 0.6, y + dy * 0.6),
                 (x + dx * 0.6 - dx * 0.45 + dy * 0.35 * s,
                  y + dy * 0.6 - dy * 0.45 - dx * 0.35 * s)], fill=col, width=3)


def _place_level_tags(dr, entries, font, painted, W, H, placed, colour):
    """Put LVL tags in the white space beside a wing rather than on top of it."""
    pad = 5
    for (x0, y0, x1, y1), text in sorted(entries, key=lambda e: -(e[0][2] - e[0][0])):
        tw = dr.textlength(text, font=font)
        th = 24
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        best = None
        for gap in range(8, 220, 8):
            cands = [(cx - tw / 2, y0 - gap - th),      # above
                     (cx - tw / 2, y1 + gap),           # below
                     (x0 - gap - tw, cy - th / 2),      # left
                     (x1 + gap, cy - th / 2)]
            for bx, by in cands:
                bx, by = int(bx), int(by)
                if bx < 2 or by < 2 or bx + tw > W - 2 or by + th > H - 2:
                    continue
                region = painted[max(by - pad, 0):by + th + pad,
                                 max(bx - pad, 0):int(bx + tw) + pad]
                if region.any():
                    continue
                rect = (bx - pad, by - pad, bx + tw + pad, by + th + pad)
                if any(not (rect[2] < q[0] or q[2] < rect[0] or
                            rect[3] < q[1] or q[3] < rect[1]) for q in placed):
                    continue
                best = (bx, by, rect)
                break
            if best:
                break
        if not best:                       # nowhere clear: fall back to on top
            bx, by = int(cx - tw / 2), int(y0 - th - 6)
            best = (bx, by, (bx - pad, by - pad, bx + tw + pad, by + th + pad))
        bx, by, rect = best
        # leader line back to the wing, so a tag in the margin is unambiguous
        ax = min(max(bx + tw / 2, x0), x1)
        ay = min(max(by + th / 2, y0), y1)
        lx = bx + tw / 2
        ly = by + th / 2
        if abs(lx - ax) > 4 or abs(ly - ay) > 4:
            dr.line([(lx, ly), (ax, ay)], fill=(168, 168, 178), width=1)
        dr.text((bx, by), text, fill=colour, font=font,
                stroke_width=3, stroke_fill=(255, 255, 255))
        placed.append(rect)


def _draw_labels(dr, labels, font, W, H, box=False, placed=None):
    if placed is None:
        placed = []
    for px, py, text, col in labels:
        tw = dr.textlength(text, font=font)
        offsets = ((-tw / 2, -20), (-tw / 2, 6), (10, -9), (-tw - 12, -9)) if box else \
                  ((10, -9), (10, 4), (-tw - 12, -9), (-tw - 12, 4),
                   (10, -24), (10, 18), (-tw - 12, -24), (-tw - 12, 18),
                   (10, -38), (10, 32))
        for ox, oy in offsets:
            bx0, by0 = px + ox, py + oy
            bx1, by1 = bx0 + tw, by0 + 17
            if bx0 < 2 or bx1 > W - 2 or by0 < 2 or by1 > H - 2:
                continue
            if any(not (bx1 < q[0] or q[2] < bx0 or by1 < q[1] or q[3] < by0) for q in placed):
                continue
            dr.text((bx0, by0), text, fill=col, font=font,
                    stroke_width=3, stroke_fill=(255, 255, 255))
            placed.append((bx0 - 3, by0 - 2, bx1 + 3, by1 + 1))
            break


def _legend(dr, counts, hotspots, lm, font, x0, y0, W, trap_names=None,
            chest_names=None, row=46, sw=26):
    x, y = x0, y0
    items = []
    shapes = {'void': 'box',
              'door': 'door', 'door_lock': 'key', 'door_link': 'lever',
              'chest': 'box', 'chest_lock': 'box',
              'trap': 'diamond', 'plate': 'diamond', 'lever': 'diamond'}
    skip = {'chest', 'chest_lock'}
    for key, (col, label) in MARKER.items():
        n = counts.get(key, 0)
        if n and key not in skip:
            items.append((col, '%s (%d)' % (label, n), shapes.get(key, 'dot')))
    for (nm, kind), n in sorted((chest_names or {}).items(), key=lambda kv: -kv[1]):
        items.append((MARKER[kind][0],
                      '%s%s (%d)' % (nm, ' [locked]' if kind == 'chest_lock' else '', n),
                      'box'))
    for k, v in counts.items():
        if k.startswith('hotspot:'):
            dt, nm = k.split(':', 1)[1].split('|', 1)
            items.append((hotspot_color(dt), '%s - %s damage (%d)' % (nm, dt, v), 'box'))
        elif k.startswith('vent:'):
            dt, nm = k.split(':', 1)[1].split('|', 1)
            items.append((hotspot_color(dt), '%s - minor %s (%d)' % (nm, dt, v), 'grid'))
    if trap_names:
        names = ', '.join('%s x%d' % (n, c) for n, c in trap_names.most_common(4))
        items.append((MARKER['trap'][0], 'traps: ' + names, 'diamond'))
    if lm.ramps:
        items.append(((150, 96, 40), 'ramp / stairs, arrow points uphill (%d)' % sum(len(v) for v in lm.ramps.values()), 'arrow'))
    if lm.bridges:
        items.append(((176, 122, 62), 'bridge / overpass (%d)' % len(lm.bridges), 'box'))
    for col, label, shape in items:
        tw = dr.textlength(label, font=font)
        if x + sw + 12 + tw > W - 60:
            x = x0
            y += row
        cy = y + row // 2 - 2
        if shape == 'dot':
            dr.ellipse([x, cy - 10, x + 20, cy + 10], fill=col,
                       outline=(255, 255, 255), width=2)
        elif shape == 'box':
            dr.rectangle([x, cy - 10, x + 20, cy + 10], fill=col, outline=(60, 60, 60))
        elif shape == 'diamond':
            dr.polygon([(x + 10, cy - 12), (x + 22, cy), (x + 10, cy + 12), (x - 2, cy)],
                       fill=col, outline=(255, 255, 255))
        elif shape == 'grid':
            base = tuple(int(226 * 0.72 + c * 0.28) for c in col)
            dr.rectangle([x, cy - 10, x + 20, cy + 10], fill=base, outline=(60, 60, 60))
            for o in (6, 13):
                dr.line([(x + o, cy - 10), (x + o, cy + 10)], fill=(58, 52, 48))
                dr.line([(x, cy - 10 + o), (x + 20, cy - 10 + o)], fill=(58, 52, 48))
        elif shape == 'key':
            _key_glyph(dr, x, cy + 6, 11, col)
        elif shape == 'lever':
            _lever_glyph(dr, x, cy + 7, 11, col)
        elif shape == 'door':
            dr.line([(x + 1, cy + 10), (x + 19, cy - 8)], fill=col, width=5)
            dr.line([(x - 2, cy + 6), (x + 5, cy + 13)], fill=col, width=3)
            dr.line([(x + 15, cy - 12), (x + 22, cy - 5)], fill=col, width=3)
        else:
            _arrow(dr, (x + 11, cy), 0.0, 18, col)
        dr.text((x + sw + 12, y + 6), label, fill=(50, 50, 60), font=font)
        x += sw + 56 + tw


def _scalebar(dr, right, y, scale, font, col):
    length = 24 * scale     # one land cell = 24 units
    label = '24 m  (1 land cell)'
    x = right - length - dr.textlength(label, font=font) - 14
    dr.line([(x, y), (x + length, y)], fill=col, width=3)
    for xx in (x, x + length):
        dr.line([(xx, y - 6), (xx, y + 6)], fill=col, width=3)
    dr.text((x + length + 8, y - 9), label, fill=col, font=font)


def _north(dr, x, y, col):
    dr.line([(x, y + 26), (x, y - 20)], fill=col, width=4)
    dr.polygon([(x, y - 32), (x - 10, y - 14), (x + 10, y - 14)], fill=col)
    dr.text((x - 9, y + 28), 'N', fill=col, font=_font(28, True))

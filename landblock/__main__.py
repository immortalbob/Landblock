#!/usr/bin/env python3
"""landblock -- dungeon maps for Asheron's Call, generated from game data.

    python -m landblock --cell client_cell_1.dat --portal client_portal.dat \
                        --world /path/to/ACE-World/Database \
                        --out maps --all --dungeons-only

Dungeon geometry and room meshes are read straight from the client dat files;
every object, name and roster comes from an ACE-World database. The dat formats
are identical from 2005 through end of retail, so the same code runs on either.
"""
import argparse
import collections
import csv
import os
import sys
import time

from . import world as acworld
from . import render as acrender
from .dat import open_dat
from .geom import Geometry


def surface_entry(world, lb, geom, depth=8):
    """Walk the portal graph outward to the closest thing to a surface entrance.

    Prefers a portal actually standing outdoors. Many dungeon chains start
    inside a building, so we also accept a landblock that has outdoor object
    placements, and fall back to the last landblock reached.
    """
    seen = set()
    frontier = [lb]
    fallback = None
    while frontier and depth:
        nxt = []
        for cur in frontier:
            if cur in seen:
                continue
            seen.add(cur)
            for ep in world.entry_portals(cur):
                for (plb, pcell, px, py, pz) in ep['from']:
                    if pcell < 0x100:
                        return plb, pcell, px, py, True
                    if fallback is None:
                        fallback = (plb, pcell, px, py, False)
                    insts, _ = world.instances(plb)
                    outdoor = [i for i in insts if i['cell'] < 0x100]
                    if outdoor:
                        o = outdoor[0]
                        return plb, o['cell'], o['x'], o['y'], True
                    nxt.append(plb)
        frontier = nxt
        depth -= 1
    return fallback


def header_for(world, lb, geom, cells, insts):
    lines = []
    eps = world.entry_portals(lb)
    src = None
    for ep in eps:
        if ep['from']:
            src_lb = ep['from'][0][0]
            src = world.dungeon_name(src_lb) or ('landblock 0x%04X' % src_lb)
            break
    lvl = max([ep['min_level'] or 0 for ep in eps] or [0])
    if lvl:
        lines.append('Level requirement: %d+' % lvl)
    quests = [ep['quest'] for ep in eps if ep['quest']]
    if quests:
        lines.append('Requires quest flag: %s' % quests[0])
    surf = surface_entry(world, lb, geom)
    if surf:
        exact = surf[4]
        lines.append('%s: %s (landblock 0x%04X)'
                     % ('Surface entrance' if exact else 'Entry chain reaches',
                        acworld.coord_string(surf[0], surf[2], surf[3]), surf[0]))
        ls = world.nearest_lifestone(surf[0], surf[1], surf[2], surf[3])
        if ls:
            (mx, my), dist = ls
            lines.append('Nearest lifestone: %.1f%s %.1f%s'
                         % (abs(my), 'N' if my >= 0 else 'S',
                            abs(mx), 'E' if mx >= 0 else 'W'))
    exits = []
    for i in insts:
        w = world.weenies.get(i['wcid'], {})
        if acworld.WEENIE_TYPE.get(w.get('wtype')) != 'Portal':
            continue
        mn = w.get('ints', {}).get('MinLevel')
        label = world.name(i['wcid']) + (' (%d+)' % mn if mn else '')
        if label not in exits:
            exits.append(label)
    if exits:
        lines.append('Exits (E): ' + ', '.join(exits[:5]))
    sub = 'From: %s' % src if src else None
    return sub, lines


HOUSING_TYPES = {'House', 'SlumLord', 'Hook', 'Storage', 'HousePortal', 'Deed'}


def is_dungeon(world, lb, ann=None):
    """A dungeon has no physical way in.

    Caves and buildings sit in a landblock that also has objects standing on
    the surface; housing has hooks, storage and a slumlord. A dungeon has
    neither -- you arrive by portal or by a transport spell.
    """
    insts, _links = world.instances(lb)
    if not insts:
        # no object data to judge by -- fall back to the spreadsheet
        return bool(ann.is_dungeon(lb)) if ann is not None else False
    if any(i['cell'] < 0x100 for i in insts):
        return False
    for i in insts:
        w = world.weenies.get(i['wcid'])
        if w and acworld.WEENIE_TYPE.get(w['wtype']) in HOUSING_TYPES:
            return False
    return True


def render_per_level(lb, cells, insts, links, world, path, args, sub, lines):
    """One panel per level, all sharing a transform, so nothing overlaps."""
    from PIL import Image, ImageDraw, ImageFont
    pts = [p for c in cells for poly in c.floors for p in poly]
    bbox = (min(p[0] for p in pts) - 6, max(p[0] for p in pts) + 6,
            min(p[1] for p in pts) - 6, max(p[1] for p in pts) + 6)
    arr_cells = {ep['dest'][0] & 0xFFFF for ep in world.entry_portals(lb) if ep['dest']}
    floor_of = acrender.compute_floors(cells, arr_cells)
    by_level = collections.defaultdict(list)
    for c in cells:
        by_level[floor_of.get(c.cell_id & 0xFFFF,
                              int(round(c.origin[2] / 6.0)))].append(c)
    cell_level = dict(floor_of)
    tiles = []
    tmp = path + '.tmp.png'
    for lvl in sorted(by_level, reverse=True):
        sub_cells = by_level[lvl]
        sub_insts = [i for i in insts if cell_level.get(i['cell']) == lvl]
        info = acrender.render(lb, sub_cells, sub_insts, links, world, tmp,
                               style=args.style, scale=args.scale,
                               show_generators=args.generators,
                               show_obstacles=args.obstacles,
                               label_items=not args.no_labels,
                               show_voids=False, bbox=bbox, chrome=False,
                               tone_level=lvl,
                               title='LVL %d   (%d cells, %d objects)'
                                     % (lvl, len(sub_cells), len(sub_insts)))
        if info:
            im = Image.open(tmp).copy()
            # trim the shared-bbox canvas down to what this level actually
            # covers; every panel keeps the same scale, so they stay comparable
            grey = im.convert('L')
            bb = grey.point(lambda v: 0 if v > 246 else 255).getbbox()
            if bb:
                pad = 26
                bb = (max(bb[0] - pad, 0), max(bb[1] - pad, 0),
                      min(bb[2] + pad, im.width), min(bb[3] + pad, im.height))
                im = im.crop(bb)
            tiles.append((lvl, im))
    if not tiles:
        return None
    os.remove(tmp)
    tw = max(t[1].width for t in tiles)
    th = max(t[1].height for t in tiles)
    cols = max(1, min(4, len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    head = 150
    sheet = Image.new('RGB', (cols * tw + 24, head + rows * th + 24), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    try:
        f_t = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
        f_s = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 18)
    except OSError:
        f_t = f_s = ImageFont.load_default()
    name = world.dungeon_name(lb) or 'Landblock 0x%04X' % lb
    dr.text((16, 14), name, fill=(20, 20, 30), font=f_t)
    y = 58
    for line in ([sub] if sub else []) + lines:
        dr.text((16, y), line, fill=(80, 80, 90), font=f_s)
        y += 24
    for n, (lvl, im) in enumerate(tiles):
        cx = 12 + (n % cols) * tw + (tw - im.width) // 2
        cy = head + (n // cols) * th + (th - im.height) // 2
        sheet.paste(im, (cx, cy))
        d2 = ImageDraw.Draw(sheet)
        d2.rectangle([12 + (n % cols) * tw + 4, head + (n // cols) * th + 4,
                      12 + (n % cols) * tw + tw - 6, head + (n // cols) * th + th - 6],
                     outline=(222, 222, 228))
    sheet.save(path)
    return dict(cells=len(cells), levels=sorted(by_level), objects=len(insts),
                ramps=sum(len(c.slopes) for c in cells), bridges=0, counts={})


def _write_html(out, manifest):
    rows = []
    for r in sorted(manifest, key=lambda r: (-r['cells'])):
        rows.append(
            '<tr><td><a href="%s">%s</a></td><td>%s</td><td>%s</td>'
            '<td>%d</td><td>%d</td><td>%d</td></tr>'
            % (r['file'], r['landblock'], r['name'] or '&mdash;', r['coords'],
               r['cells'], r['levels'], r['objects']))
    style = ("body{font:15px/1.5 system-ui;margin:2rem;max-width:70rem}"
             "table{border-collapse:collapse;width:100%}"
             "td,th{padding:.35rem .6rem;border-bottom:1px solid #ddd;text-align:left}"
             "tr:hover{background:#f6f6f6}")
    html = ('<!doctype html><meta charset="utf-8"><title>Dungeon maps</title>'
            '<style>' + style + '</style>'
            '<h1>Generated dungeon maps</h1><p>' + str(len(manifest)) + ' landblocks.</p>'
            '<table><tr><th>Landblock<th>Name<th>Coords<th>Cells<th>Levels<th>Objects</tr>'
            + ''.join(rows) + '</table>')
    open(os.path.join(out, 'index.html'), 'w').write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cell', required=True)
    ap.add_argument('--portal', required=True)
    ap.add_argument('--world', default=None,
                    help='ACE-World .../Database directory. Optional: without '
                         'it maps are geometry only (no objects, names or '
                         'entry data) and files are named by landblock id -- '
                         'the only choice for original-era dats, which have '
                         'no matching world database')
    ap.add_argument('--out', default='maps')
    ap.add_argument('--landblock', action='append', default=[],
                    help='hex landblock id, repeatable')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--style', default='dungeon', choices=list(acrender.STYLES))
    ap.add_argument('--scale', type=int, default=8)
    ap.add_argument('--min-cells', type=int, default=4)
    ap.add_argument('--generators', action='store_true')
    ap.add_argument('--obstacles', action='store_true')
    ap.add_argument('--voids', action='store_true',
                    help='shade cells that appear to have no walkable floor '
                         '(heuristic; unreliable on stacked multi-storey dungeons)')
    ap.add_argument('--layout', default='auto', choices=('auto', 'composite', 'panels', 'flow', 'stack'),
                    help='auto draws one plan when levels barely stack, and one '
                         'panel per level when they do')
    ap.add_argument('--overlap-threshold', type=float, default=0.30,
                    help='overlap above which floors are separated')
    ap.add_argument('--stack-max-floors', type=int, default=6,
                    help='separated floors above this count are packed to a\n'
                         ' sheet instead of stacked in one column')
    ap.add_argument('--explode', action='store_true',
                    help='experimental: slide overlapping floors apart and mark the\n'
                         ' severed corridors with matching numbers')
    ap.add_argument('--explode-gap', type=float, default=2.0)
    ap.add_argument('--flow-gap', type=float, default=24.0)
    ap.add_argument('--no-walls', dest='walls', action='store_false',
                    help='do not draw interior room walls from the meshes')
    ap.add_argument('--explode-threshold', type=float, default=0.30,
                    help='fraction of a floor that must sit under another'
                         ' before it is slid aside')
    ap.add_argument('--keep-shells', action='store_true',
                    help='keep empty structural storeys that cover other floors')
    ap.add_argument('--void-gap', type=float, default=12.0,
                    help='max height a ceiling cap may sit above a floor and still'
                         ' punch through it (world units; 6 = one level)')
    ap.add_argument('--debug-cells', action='store_true',
                    help='overlay every cell id: F=has floor, C=cap only')
    ap.add_argument('--no-labels', action='store_true',
                    help='markers only, no text labels (denser dungeons stay readable)')
    ap.add_argument('--cache', default=None)
    ap.add_argument('--annotations', default=None,
                    help='AC Landblocks spreadsheet (.xlsx): community names, '
                         'categories, access methods and hand-logged drop points')
    ap.add_argument('--patches', default=None,
                    help='ACE-World patches .../Database/Patches directory; '
                         'patch files replace the base file for that key')
    ap.add_argument('--dungeons-only', action='store_true',
                    help='only landblocks with no physical surface entrance: '
                         'no outdoor object placements and no housing, i.e. '
                         'reachable only by portal or transport spell')
    ap.add_argument('--skip-existing', action='store_true',
                    help='resume a batch run without redoing finished maps')
    args = ap.parse_args()

    if args.dungeons_only and not args.world:
        ap.error('--dungeons-only needs --world: object data is what tells a '
                 'dungeon from a cave, building or house')

    acworld.load_enums(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'enums.json'))
    t0 = time.time()
    cell_dat = open_dat(args.cell)
    portal_dat = open_dat(args.portal)
    if cell_dat.era != portal_dat.era:
        ap.error('cell and portal dats are from different eras (%s vs %s)'
                 % (cell_dat.era, portal_dat.era))
    if cell_dat.era == 'pretod':
        print('original-era (pre-ToD) dats detected')
    geom = Geometry(cell_dat, portal_dat)
    if args.world:
        world = acworld.World(args.world, cache=args.cache, patch_core=args.patches)
    else:
        world = acworld.NullWorld()
        print('no world database: geometry-only maps, named by landblock id')
    ann = None
    if args.annotations:
        from .annotations import Annotations
        ann = Annotations(args.annotations)
        print('annotations: %d names, %d categories, %d with access recorded'
              % (len(ann.names), len(ann.category), len(ann.access)))
    print('loaded dats + world index in %.1fs' % (time.time() - t0))

    todo = [int(x, 16) for x in args.landblock]
    if args.all:
        idx = geom.landblocks_with_interiors()
        todo = sorted(lb for lb, cs in idx.items() if len(cs) >= args.min_cells)
    if args.dungeons_only:
        todo = [lb for lb in todo if is_dungeon(world, lb, ann)]
        print('dungeons only: %d landblocks' % len(todo))
    os.makedirs(args.out, exist_ok=True)

    done = 0
    skipped = []
    manifest = []
    incomplete = []
    t0 = time.time()
    for lb in todo:
        cells = geom.load(lb)
        if not cells:
            skipped.append((lb, 'no interior cells'))
            continue
        # a cell whose room mesh is absent from this portal.dat draws as
        # nothing at all, so say so on the map rather than leaving a hole
        gap = geom.unmapped(lb)
        if gap:
            incomplete.append((lb, gap, len(cells)))
        if not any(c.floors for c in cells):
            # sub-cells holding only prop collision boxes (poles, ladders,
            # water planes) -- not a walkable interior
            skipped.append((lb, 'no room meshes in this portal.dat'
                                if gap == len(cells) else 'no floor geometry'))
            continue
        insts, links = world.instances(lb)
        sub, lines = header_for(world, lb, geom, cells, insts)
        if gap:
            lines = lines + ['INCOMPLETE: %d of %d cells not drawn -- their room '
                             'meshes are absent from this portal.dat' % (gap, len(cells))]
        name = world.dungeon_name(lb)
        drops = ()
        if ann is not None:
            name = name or ann.name(lb)          # ACE naming wins
            lines = lines + ann.header_lines(lb)
            # only hand-logged drops the game data does not already give us
            known = {ep['dest'][0] & 0xFFFF for ep in world.entry_portals(lb)
                     if ep['dest']}
            drops = tuple(d for d in ann.drops.get(lb, ()) if d[0] not in known)
        slug = ''.join(ch if ch.isalnum() else '_' for ch in (name or '')).strip('_')
        fn = os.path.join(args.out, '%04X%s.png' % (lb, '_' + slug if slug else ''))
        if args.skip_existing and os.path.exists(fn):
            lv = {int(round(c.origin[2] / 6.0)) for c in cells}
            manifest.append(dict(
                landblock='%04X' % lb, name=name or '',
                coords=acworld.coord_string(lb, 96, 96), cells=len(cells),
                levels=len(lv), objects=len(insts),
                ramps=sum(len(c.slopes) for c in cells), bridges=0,
                file=os.path.basename(fn)))
            continue
        arr_cells = {ep['dest'][0] & 0xFFFF for ep in world.entry_portals(lb)
                     if ep['dest']}
        floor_of = acrender.compute_floors(cells, arr_cells)
        shed = set()
        if not args.keep_shells:
            cells, shed = acrender.drop_shell_floors(cells, insts, floor_of)
            floor_of = acrender.compute_floors(cells, arr_cells)
        layout = args.layout
        if layout == 'auto':
            # A plan is only honest where floors do not sit on each other. Past
            # that, separate them -- in a single column when there are few
            # floors, packed to a sheet when there are many, because a column
            # of twenty-six floors is a scroll, not a map.
            if acrender.overlap_fraction(cells, floor_of=floor_of) > args.overlap_threshold:
                sheets = len(set(acrender.group_floors(cells, floor_of).values()))
                layout = 'stack' if sheets <= args.stack_max_floors else 'flow'
            else:
                layout = 'composite'
        connectors = ()
        if layout in ('flow', 'stack'):
            cells, insts, connectors = acrender.flow_layout(
                cells, insts, floor_of, gap=args.flow_gap,
                columns=1 if layout == 'stack' else 0)
            layout = 'composite'
        elif layout == 'composite' and args.explode:
            cells, insts, connectors = acrender.explode(
                cells, insts, floor_of, gap=args.explode_gap,
                min_overlap=args.explode_threshold)
        try:
            if layout == 'panels':
                info = render_per_level(lb, cells, insts, links, world, fn,
                                        args, sub, lines)
                done += 1
                if info:
                    print('0x%04X %-30s panels=%d cells=%d'
                          % (lb, (name or '')[:30], len(info['levels']), info['cells']))
                continue
            info = acrender.render(lb, cells, insts, links, world, fn,
                                   connectors=connectors, title=name,
                                   extra_drops=drops,
                                   style=args.style, scale=args.scale,
                                   show_generators=args.generators,
                                   show_obstacles=args.obstacles,
                                   label_items=not args.no_labels,
                                   show_walls=args.walls,
                                   show_voids=args.voids,
                                   void_gap=args.void_gap,
                                   debug_cells=args.debug_cells,
                                   subtitle=sub, header_lines=lines)
        except Exception as exc:
            skipped.append((lb, 'render error: %s' % exc))
            continue
        done += 1
        if info:
            manifest.append(dict(
                landblock='%04X' % lb, name=name or '',
                coords=acworld.coord_string(lb, 96, 96),
                cells=info['cells'], levels=len(info['levels']),
                objects=info['objects'], ramps=info['ramps'],
                bridges=info['bridges'], file=os.path.basename(fn),
                **{k: v for k, v in info['counts'].items() if not k.startswith('hotspot')}))
        if info and (len(todo) < 20 or done % 100 == 0):
            print('0x%04X %-34s cells=%-4d levels=%-22s objects=%-4d ramps=%-3d bridges=%d'
                  % (lb, (name or '')[:34], info['cells'], info['levels'],
                     info['objects'], info['ramps'], info['bridges']))
    if manifest:
        keys = sorted({k for row in manifest for k in row})
        head = ['landblock', 'name', 'coords', 'cells', 'levels', 'objects',
                'ramps', 'bridges', 'file']
        cols = head + [k for k in keys if k not in head]
        with open(os.path.join(args.out, 'index.csv'), 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, restval=0)
            w.writeheader()
            w.writerows(manifest)
        _write_html(args.out, manifest)
    print('rendered %d maps in %.1fs -> %s' % (done, time.time() - t0, args.out))
    if geom.missing_env:
        blocked = sum(1 for lb, g, n in incomplete if g == n)
        print('MESH GAP: this portal.dat is missing %d environments the cell dat '
              'references' % len(geom.missing_env))
        print('   %d landblocks lost every cell, %d lost some -- the dats are '
              'probably from different patches'
              % (blocked, len(incomplete) - blocked))
    if skipped:
        print('skipped %d landblocks:' % len(skipped))
        for lb, why in skipped[:20]:
            print('   0x%04X  %s' % (lb, why))


if __name__ == '__main__':
    main()

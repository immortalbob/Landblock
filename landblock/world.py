"""World data: weenie defaults + landblock instances from an ACE-World SQL tree.

Works against ACE-World-16PY and the end-of-retail ACE-World repo -- both use
the same generated-SQL layout and the same inline /* PropertyName */ comments,
which is what we key off rather than hard-coded property numbers.
"""
import json
import os
import re
import math

WEENIE_TYPE = {}
CREATURE_TYPE = {}

_W_HEADER = re.compile(
    r"INSERT INTO `weenie` \(`class_Id`.*?VALUES \((\d+), '([^']*)', (\d+)", re.S)
_PROP = re.compile(r"\((\d+), *(\d+), *((?:'(?:[^']|'')*')|[-\w.]+) *\)? *(?:/\* ([^*]+)\*/)?")
_DEST = re.compile(
    r"INSERT INTO `weenie_properties_position`.*?VALUES \((\d+), (\d+), 0x([0-9A-Fa-f]{8}), "
    r"(-?[\d.eE+]+), (-?[\d.eE+]+), (-?[\d.eE+]+)", re.S)
_INSTANCE = re.compile(
    r"VALUES \(0x([0-9A-Fa-f]{8}), *(\d+), *0x([0-9A-Fa-f]{8}), *"
    r"(-?[\d.eE+]+), *(-?[\d.eE+]+), *(-?[\d.eE+]+), *"
    r"(-?[\d.eE+]+), *(-?[\d.eE+]+), *(-?[\d.eE+]+), *(-?[\d.eE+]+), *"
    r"(True|False)")
_LINK = re.compile(r"\(0x([0-9A-Fa-f]{8}), *0x([0-9A-Fa-f]{8}), *'")
_CREATE = re.compile(r"/\* Create ([^(]+) \((\d+)\) for (\w+) \*/")
_GEN = re.compile(r"\((\d+), *(-?[\d.]+), *(\d+), *[-\d.]+, *[-\d.]+, *[-\d.]+, *[-\d.]+, *[-\d.]+, *[-\d.]+, *\d+, *[-\d.]+, *(\d+), *(-?[\d.eE+]+), *(-?[\d.eE+]+), *(-?[\d.eE+]+)")

DAMAGE_TYPE = {1: 'Slash', 2: 'Pierce', 4: 'Bludgeon', 8: 'Cold', 16: 'Fire',
               32: 'Acid', 64: 'Electric', 512: 'Mana', 1024: 'Nether'}

WANT_INT = {'ItemType', 'CreatureType', 'Level', 'MinLevel', 'MaxLevel',
            'ResistLockpick', 'ItemUseable', 'Value', 'Damage', 'DamageType',
            'PortalBitmask', 'Active', 'Tier'}
WANT_BOOL = {'Locked', 'Attackable', 'Stuck', 'IsHot', 'UiHidden', 'Ethereal',
             'NpcLooksLikeObject', 'Inscribable', 'DefaultOpen'}
WANT_STR = {'Name', 'ShortDesc', 'LongDesc', 'Use', 'QuestRestriction',
            'LockCode', 'KeyCode', 'AppraisalPortalDestination'}


def _unq(v):
    if v.startswith("'"):
        return v[1:-1].replace("''", "'")
    return v


class World:
    source_desc = 'objects: ACE-World'

    def __init__(self, sql_root, cache=None, patch_core=None):
        """sql_root is an ACE-World .../Database directory.

        patch_core, if given, is a Patches directory laid out the same way.
        A patch file replaces the base file for that weenie or landblock
        outright -- the patch SQL opens with a DELETE for the key it owns --
        so later roots simply win.
        """
        self.root = sql_root
        self.core = os.path.join(sql_root, '3-Core')
        self.patch_core = patch_core if patch_core and os.path.isdir(patch_core) else None
        self.weenies = {}
        self.portal_dests = {}     # dest landblock -> [wcid,...]
        self.placements = {}       # wcid -> [(lb, cell, x, y, z)]  (portals/lifestones only)
        cache = cache or os.path.join(sql_root, '.landblock-index.json')
        if os.path.exists(cache):
            self._load_cache(cache)
        else:
            self._build()
            self._save_cache(cache)

    # ------------------------------------------------------------------ build
    def _weenie_roots(self):
        roots = [os.path.join(self.core, '9 WeenieDefaults', 'SQL')]
        if self.patch_core:
            # the patch tree has no SQL/ level
            roots.append(os.path.join(self.patch_core, '9 WeenieDefaults'))
        return roots

    def _build(self):
        self.patched = 0
        for wroot in self._weenie_roots():
            if not os.path.isdir(wroot):
                continue
            for dirpath, _dirs, files in os.walk(wroot):
                for fn in files:
                    if not fn.endswith('.sql'):
                        continue
                    before = len(self.weenies)
                    self._parse_weenie(os.path.join(dirpath, fn))
                    if len(self.weenies) == before:
                        self.patched += 1
        for wcid, w in self.weenies.items():
            if w.get('dest'):
                lb = w['dest'][0] >> 16
                self.portal_dests.setdefault(lb, []).append(wcid)
        interesting = {wcid for wcid, w in self.weenies.items()
                       if w['wtype'] in (7, 26, 61)}     # Portal, LifeStone, HousePortal
        for lb in sorted(self.landblocks()):
            for inst in self._parse_landblock(self.landblock_file(lb)):
                if inst['wcid'] in interesting:
                    self.placements.setdefault(inst['wcid'], []).append(
                        (lb, inst['cell'], inst['x'], inst['y'], inst['z']))

    def _parse_weenie(self, path):
        txt = open(path, encoding='latin-1').read()
        m = _W_HEADER.search(txt)
        if not m:
            return
        wcid = int(m.group(1))
        rec = {'wcid': wcid, 'cls': m.group(2), 'wtype': int(m.group(3)),
               'ints': {}, 'bools': {}, 'strs': {}, 'dest': None}
        for block, table in (('weenie_properties_int', 'ints'),
                             ('weenie_properties_bool', 'bools'),
                             ('weenie_properties_string', 'strs')):
            i = txt.find('INSERT INTO `%s`' % block)
            if i < 0:
                continue
            end = txt.find(';', i)
            for pm in _PROP.finditer(txt[i:end]):
                label = (pm.group(4) or '').strip()
                label = label.split(' - ')[0].strip()
                val = _unq(pm.group(3))
                if table == 'ints' and label in WANT_INT:
                    try:
                        rec['ints'][label] = int(val)
                    except ValueError:
                        pass
                elif table == 'bools' and label in WANT_BOOL:
                    rec['bools'][label] = (val == 'True')
                elif table == 'strs' and label in WANT_STR:
                    rec['strs'][label] = val
        i = txt.find('INSERT INTO `weenie_properties_create_list`')
        if i >= 0:
            end = txt.find(';', i)
            loot = []
            for cm in _CREATE.finditer(txt[i:end]):
                if cm.group(3) in ('ContainTreasure', 'Contain', 'Wield', 'WieldTreasure'):
                    nm = cm.group(1).strip()
                    if nm and nm != 'nothing' and nm not in loot:
                        loot.append(nm)
            if loot:
                rec['loot'] = loot[:8]
        i = txt.find('INSERT INTO `weenie_properties_generator`')
        if i >= 0:
            end = txt.find(';', i)
            gens = []
            for gm in _GEN.finditer(txt[i:end]):
                spawn = int(gm.group(3))
                if not spawn:
                    continue
                gens.append({'wcid': spawn, 'cell': int(gm.group(4)),
                             'x': float(gm.group(5)), 'y': float(gm.group(6)),
                             'z': float(gm.group(7))})
            if gens:
                rec['gen'] = gens[:24]
        for dm in _DEST.finditer(txt):
            if dm.group(2) == '2':      # PositionType.Destination
                rec['dest'] = (int(dm.group(3), 16), float(dm.group(4)),
                               float(dm.group(5)), float(dm.group(6)))
                break
        self.weenies[wcid] = rec

    def _parse_landblock(self, path):
        txt = open(path, encoding='latin-1').read()
        out = []
        for m in _INSTANCE.finditer(txt):
            out.append({'guid': int(m.group(1), 16), 'wcid': int(m.group(2)),
                        'cell': int(m.group(3), 16) & 0xFFFF,
                        'full_cell': int(m.group(3), 16),
                        'x': float(m.group(4)), 'y': float(m.group(5)),
                        'z': float(m.group(6)),
                        'aw': float(m.group(7)), 'ax': float(m.group(8)),
                        'ay': float(m.group(9)), 'az': float(m.group(10)),
                        'link_child': m.group(11) == 'True'})
        return out

    # ------------------------------------------------------------------ cache
    def _save_cache(self, path):
        try:
            json.dump({'weenies': {str(k): v for k, v in self.weenies.items()},
                       'portal_dests': {str(k): v for k, v in self.portal_dests.items()},
                       'placements': {str(k): v for k, v in self.placements.items()}},
                      open(path, 'w'))
        except OSError:
            pass

    def _load_cache(self, path):
        d = json.load(open(path))
        self.weenies = {int(k): v for k, v in d['weenies'].items()}
        self.portal_dests = {int(k): v for k, v in d['portal_dests'].items()}
        self.placements = {int(k): v for k, v in d['placements'].items()}

    # ----------------------------------------------------------------- access
    def landblock_dirs(self):
        dirs = [os.path.join(self.core, '6 LandBlockExtendedData', 'SQL')]
        if self.patch_core:
            dirs.append(os.path.join(self.patch_core, '6 LandBlockExtendedData'))
        return [d for d in dirs if os.path.isdir(d)]

    def landblocks(self):
        """Every landblock with object data, base or patched."""
        out = set()
        for d in self.landblock_dirs():
            for fn in os.listdir(d):
                if fn.endswith('.sql') and len(fn) == 8:
                    try:
                        out.add(int(fn[:4], 16))
                    except ValueError:
                        pass
        return out

    def landblock_file(self, lb):
        path = None
        for d in self.landblock_dirs():        # later dirs win
            p = os.path.join(d, '%04X.sql' % lb)
            if os.path.exists(p):
                path = p
        return path or os.path.join(self.core, '6 LandBlockExtendedData',
                                    'SQL', '%04X.sql' % lb)

    def instances(self, lb):
        p = self.landblock_file(lb)
        if not os.path.exists(p):
            return [], {}
        insts = self._parse_landblock(p)
        txt = open(p, encoding='latin-1').read()
        links = {}
        i = 0
        while True:
            i = txt.find('INSERT INTO `landblock_instance_link`', i)
            if i < 0:
                break
            end = txt.find(';', i)
            for lm in _LINK.finditer(txt[i:end]):
                links.setdefault(int(lm.group(1), 16), []).append(int(lm.group(2), 16))
            i = end
        return insts, links

    def name(self, wcid):
        w = self.weenies.get(wcid)
        if not w:
            return 'wcid %d' % wcid
        return w['strs'].get('Name', w['cls'])

    def dungeon_name(self, lb):
        """Name a landblock after whichever portal leads into it."""
        best = None
        for wcid in self.portal_dests.get(lb, []):
            nm = self.name(wcid)
            if nm and not nm.lower().startswith(('portal', 'gateway')):
                for suffix in (' Portal', ' Portals', ' Portal Gem'):
                    if nm.endswith(suffix):
                        nm = nm[:-len(suffix)]
                        break
                lvl = self.weenies[wcid]['ints'].get('MinLevel', 0)
                if best is None or lvl > best[1]:
                    best = (nm, lvl)
        return best[0] if best else None

    def entry_portals(self, lb):
        """Portals elsewhere whose Destination lands in this landblock."""
        out = []
        for wcid in self.portal_dests.get(lb, []):
            w = self.weenies[wcid]
            out.append({'wcid': wcid, 'name': self.name(wcid),
                        'min_level': w['ints'].get('MinLevel'),
                        'max_level': w['ints'].get('MaxLevel'),
                        'quest': w['strs'].get('QuestRestriction'),
                        'dest': w['dest'],
                        'from': self.placements.get(wcid, [])})
        return out

    def nearest_lifestone(self, lb, cell, x, y):
        """Nearest surface lifestone to a point, as map coordinates."""
        best = None
        lsx, lsy = self._global(lb, x, y)
        for wcid, places in self.placements.items():
            if self.weenies[wcid]['wtype'] != 26:
                continue
            for plb, pcell, px, py, pz in places:
                if pcell >= 0x100:
                    continue
                gx, gy = self._global(plb, px, py)
                d = math.hypot(gx - lsx, gy - lsy)
                if best is None or d < best[0]:
                    best = (d, gx, gy, self.name(wcid))
        if not best:
            return None
        return map_coords_global(best[1], best[2]), best[0] / 240.0

    @staticmethod
    def _global(lb, x, y):
        return (((lb >> 8) & 0xFF) * 192.0 + x, (lb & 0xFF) * 192.0 + y)


class NullWorld:
    """Stand-in when no ACE world database is available.

    Original-era dats have no matching object database, so the maps are
    geometry only: floors, walls, ramps and cell portals, but no spawns,
    chests, names or entry chains. Every lookup answers 'nothing known'.
    """
    source_desc = 'geometry only, no object data'
    weenies = {}
    portal_dests = {}
    placements = {}

    def instances(self, lb):
        return [], {}

    def name(self, wcid):
        return 'wcid %d' % wcid

    def dungeon_name(self, lb):
        return None

    def entry_portals(self, lb):
        return []

    def nearest_lifestone(self, lb, cell, x, y):
        return None


def map_coords_global(gx, gy):
    return (gx / 240.0 - 102.0, gy / 240.0 - 102.0)


def coord_string(lb, x, y):
    gx, gy = World._global(lb, x, y)
    mx, my = map_coords_global(gx, gy)
    return '%.1f%s %.1f%s' % (abs(my), 'N' if my >= 0 else 'S',
                              abs(mx), 'E' if mx >= 0 else 'W')


def load_enums(path):
    global WEENIE_TYPE, CREATURE_TYPE
    d = json.load(open(path))
    WEENIE_TYPE = {int(k): v for k, v in d['weenie_type'].items()}
    CREATURE_TYPE = {int(k): v for k, v in d['creature_type'].items()}

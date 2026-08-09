"""Optional community annotations from the AC Landblocks spreadsheet.

The spreadsheet is hand-verified work: it names dungeons the game data cannot
name, records how each one is reached (portal, gem, recall spell, NPC), and
sorts them into categories no heuristic can infer -- retired, seasonal, admin,
inaccessible.

It fills gaps rather than overriding: where the ACE world data has an opinion,
that wins, because it is what the server actually runs. The spreadsheet
supplies the name when no portal names the place, the drop point when no portal
weenie reaches it, and the verdict when there is no object data to judge by.

The spreadsheet is hand-verified work: it names dungeons the game data cannot
name, records how each one is reached (portal, gem, recall spell, NPC), and
sorts them into categories no heuristic can infer -- retired, seasonal, admin,
inaccessible. Where it and the derived data disagree about a name or a
category, the spreadsheet wins; it was checked by people who went there.
"""
import re

# coordinates in the sheet are sometimes run together by the export, so take
# a bounded number of digits rather than a greedy [\d.]+
NUM = r'(-?\d+(?:\.\d{1,6})?)'
CELL_RE = re.compile(r'0x([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})\s+'
                     + NUM + r'\s*' + NUM + r'\s*' + NUM)

# sheet -> (category, name column, location column)
SHEETS = [
    ('Dungeon Portals',           'dungeon',    'Dungeon Name', 'Drop Loc'),
    ('Caves',                     'cave',       'Dungeon Name', 'Drop Coordinates'),
    ('Seasonal Dungeons',         'seasonal',   'Dungeon Name', 'Drop Coordinates'),
    ('Admin Dungeons',            'admin',      'Dungeon Name', 'Drop Coordinates'),
    ('Retired Dungeons',          'retired',    'Dungeon Name', 'Drop Coordinates'),
    ('Training Academy Dungeons', 'academy',    'Dungeon Name', 'Drop Coordinates'),
    ('Unknown Dungeons',          'unknown',    'Dungeon Name', 'Drop Coordinates'),
    ('Inaccessible Areas',        'inaccessible', 'Dungeon Name', 'Drop Coordinates'),
    ('Housing Dungeons',          'housing',    'Type',         'Drop Coordinates'),
    ('Portal Gems',               'dungeon',    'Drop Name',    'Drop Loc'),
    ('Dungeon NPC',               'dungeon',    'Dungeon Name', 'Drop Loc'),
]

# which category wins when a landblock appears on several sheets
RANK = {'inaccessible': 6, 'retired': 5, 'seasonal': 4, 'admin': 3,
        'academy': 2, 'housing': 2, 'cave': 1, 'unknown': 1, 'dungeon': 0}

# which sheet's NAME wins, which is the opposite order: a landblock that is
# both a dungeon and holds an inaccessible room is still called the dungeon
NAME_RANK = {'dungeon': 6, 'cave': 5, 'seasonal': 4, 'retired': 3,
             'academy': 2, 'admin': 2, 'housing': 1, 'unknown': 1,
             'inaccessible': 0}


class Annotations:
    def __init__(self, path):
        import pandas as pd
        self.path = path
        self.names = {}        # landblock -> name
        self.category = {}     # landblock -> category
        self.drops = {}        # landblock -> [(cell, x, y, z, label)]
        self.access = {}       # landblock -> set of how you get in
        self.notes = {}        # landblock -> note
        self._namerank = {}
        xl = pd.ExcelFile(path)
        for sheet, cat, namecol, loccol in SHEETS:
            if sheet not in xl.sheet_names:
                continue
            df = xl.parse(sheet, header=0, dtype=str)
            if namecol not in df.columns or loccol not in df.columns:
                continue
            for _, row in df.iterrows():
                self._row(row, cat, namecol, loccol, df.columns)
        self._access_from(xl, 'Portal Gems', 'gem')
        self._access_from(xl, 'Recalls', 'recall spell')
        self._access_from(xl, 'Dungeon NPC', 'NPC')
        self._access_from(xl, 'RandomSpawnPortals', 'random portal')

    # ------------------------------------------------------------------ build
    def _row(self, row, cat, namecol, loccol, columns):
        loc = row.get(loccol)
        if not isinstance(loc, str):
            return
        m = CELL_RE.match(loc.strip())
        if not m:
            return
        lb = int(m.group(1), 16)
        cell = int(m.group(2), 16)
        name = row.get(namecol)
        name = name.strip() if isinstance(name, str) and name.strip() else None
        if name and NAME_RANK.get(cat, 0) > self._namerank.get(lb, -1):
            self.names[lb] = name
            self._namerank[lb] = NAME_RANK.get(cat, 0)
        if RANK.get(cat, 0) >= RANK.get(self.category.get(lb), -1):
            self.category[lb] = cat
        self.drops.setdefault(lb, [])
        entry = (cell, float(m.group(3)), float(m.group(4)), float(m.group(5)), name or '')
        if entry not in self.drops[lb]:
            self.drops[lb].append(entry)
        note = row.get('Notes')
        if isinstance(note, str) and note.strip():
            self.notes.setdefault(lb, note.strip())
        if 'Spell Name' in columns and isinstance(row.get('Spell Name'), str):
            self.access.setdefault(lb, set()).add('spell')
        if 'Gem Name' in columns and isinstance(row.get('Gem Name'), str):
            self.access.setdefault(lb, set()).add('gem')
        if 'Portal Name' in columns and isinstance(row.get('Portal Name'), str):
            self.access.setdefault(lb, set()).add('portal')

    def _access_from(self, xl, sheet, label):
        import pandas as pd
        if sheet not in xl.sheet_names:
            return
        df = xl.parse(sheet, header=0, dtype=str)
        for col in df.columns:
            if 'Loc' not in str(col) and 'location' not in str(col):
                continue
            for v in df[col]:
                if not isinstance(v, str):
                    continue
                m = CELL_RE.match(v.strip())
                if m:
                    self.access.setdefault(int(m.group(1), 16), set()).add(label)

    # ----------------------------------------------------------------- access
    def name(self, lb):
        return self.names.get(lb)

    def is_dungeon(self, lb):
        """None when the sheet has no opinion, else True or False."""
        cat = self.category.get(lb)
        if cat is None:
            return None
        return cat in ('dungeon', 'seasonal', 'retired', 'unknown')

    def header_lines(self, lb):
        out = []
        cat = self.category.get(lb)
        if cat and cat != 'dungeon':
            out.append('Category: %s (community spreadsheet)' % cat)
        acc = self.access.get(lb)
        if acc:
            out.append('Access: ' + ', '.join(sorted(acc)))
        note = self.notes.get(lb)
        if note:
            out.append('Note: ' + note[:110])
        return out

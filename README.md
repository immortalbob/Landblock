# landblock

Generates annotated dungeon maps for Asheron's Call, straight from the game
data. Nothing is hand-authored: floor plans and walls come out of the client
`.dat` files, and every object, name, level requirement, monster roster and
loot list is derived from an ACE-World database.

![example](docs/example.png)

The dat plumbing underneath grew into a toolkit of its own. It reads and
writes both container generations, converts records from the original format
to the retail one, compares two client eras to find what changed between
them, and merges records into a full-size client dat. Those are documented
under [Working with the dats directly](#working-with-the-dats-directly).

Runs on any client era, all the way back to release. Both dat generations are
read: the 2005 (Throne of Destiny) container used through end of retail, and
the original 1999–2005 container — header at 0x12C, 12-byte directory
entries, and cell/mesh records that differ in the small print (field order,
embedded ids, 4-byte padding), transcribed from the PhatSDK `PRE_TOD`
branches. `open_dat()` autodetects the generation. The parsers are strict —
they raise rather than guess — so a format drift would surface immediately
instead of quietly producing wrong maps. Both original-era clients tested
parse byte-exact end to end: December 2000 gives 478 of 478 environment
meshes and 115,550 of 115,550 interior cells, September 2004 (the last
Dark Majesty build before the format change) 665 of 665 and 408,047 of
408,047.

---

## Quick start

```bash
pip install -r requirements.txt

python -m landblock \
    --cell    /path/to/client_cell_1.dat \
    --portal  /path/to/client_portal.dat \
    --world   /path/to/ACE-World/Database \
    --patches /path/to/ACE-World-Patches/Database/Patches \
    --out     maps \
    --all --dungeons-only
```

That writes one PNG per dungeon plus `index.csv` and `index.html`.

A single dungeon, by landblock id:

```bash
python -m landblock --cell ... --portal ... --world ... \
                    --out maps --landblock 01F5
```

### Original-era dats (maps only)

`--world` is optional. Without it the maps are geometry only — floors,
walls, ramps and level separation, but no spawns, names, chests or entry
chains — and each file is named by its 4-character landblock id
(`01A9.png`). That is the only mode that makes sense for original-era dats:
no object database from that era survives, and dungeon names drifted as
content moved over the years, so nothing modern can be trusted to label
them.

```bash
python -m landblock --cell cell.dat --portal portal.dat \
                    --out maps2000 --all --min-cells 1
```

A December 2000 cell dat holds 992 landblocks with interiors; a September
2004 one holds 2,656. Everything with cells is rendered — with no object
data there is no way to tell a dungeon from a cave or a building interior,
so nothing is filtered (`--dungeons-only` requires `--world` for the same
reason).

**Use a matching pair.** A cell dat names its rooms by id and the portal dat
supplies the mesh, so a later cell against an earlier portal asks for rooms
that did not exist yet. The 2004 cell references 638 environments; a 2000
portal has 473 of them, which silently costs 68,162 cells and blanks 670
landblocks outright. Any cell whose mesh is absent is counted, stamped on
the map as `INCOMPLETE: n of m cells not drawn`, and summarised at the end
of the run, so a mismatched pair announces itself rather than quietly
producing maps with rooms missing.

### What you need

| Input | Where it comes from | Required |
|---|---|---|
| `client_cell_1.dat` / `cell.dat` | your AC client install (any era) | yes — dungeon geometry |
| `client_portal.dat` / `portal.dat` | your AC client install (same era) | yes — room meshes, terrain palette |
| ACE-World `Database/` | github.com/ACEmulator/ACE-World | no — omit for geometry-only maps (original-era dats have no world database) |
| ACE-World patches | the matching patches repo | optional, strongly recommended |
| `Landblocks.xlsx` | the community landblock spreadsheet | optional, `--annotations` |

The two dats live in your client folder. Point `--world` at the directory that
contains `3-Core/`. If you are using the 16 P.Y. world database, add
`--patches` as well: base 16PY covers about 2,685 landblocks and the patches
add roughly 579 more along with 6,400 updated weenies.

First run builds a weenie index (about 15 s for 44,000 weenies) and caches it
next to the SQL tree, so later runs start in about a second. Use
`--cache somewhere.json` to put it elsewhere.

A full end-of-retail run is about 1,000 dungeons, 25 minutes, and 200 MB.

### Community annotations

`--annotations Landblocks.xlsx` layers hand-verified work over the derived
data. The spreadsheet knows things the game files cannot say: what a dungeon is
called when no portal points at it, how you get in when the answer is a gem or
a recall spell, and which landblocks are retired, seasonal, admin-only or
flat-out inaccessible.

The spreadsheet fills gaps rather than overriding. Where the ACE world data has
an opinion it wins, because it is what the server actually runs; the sheet
supplies the name when no portal names the place, the drop point when no portal
weenie reaches it, and the verdict when there is no object data to judge by. Its
categories, access methods and notes are added to the header either way.

---

## Options

```
--all                    every landblock with interior cells
--landblock HEX          one landblock, repeatable (e.g. --landblock 01F5)
--dungeons-only          skip caves, buildings and housing (see below)
--annotations FILE       community spreadsheet: names, categories, drop points
--min-cells N            ignore trivial interiors (default 8)

--layout composite       one plan per dungeon
--layout stack           floors in a single column, cut corridors numbered
--layout flow            floors packed to a square sheet, cuts numbered
--layout panels          one titled panel per floor, on a grid
--layout auto            composite, or separated when floors sit on each other
--overlap-threshold N    overlap above which floors are separated (default 0.30)
--stack-max-floors N     above this, separated floors pack to a sheet (default 6)

--scale N                pixels per metre (default 8)
--style dungeon|maze     orange plan, or the blue maze look
--no-walls               skip interior walls
--no-labels              markers only, no text
--generators             show monster and item generators
--obstacles              show static scenery from the cell meshes
--voids                  shade cells that appear to have no walkable floor
--debug-cells            overlay every cell id: F = has floor, C = cap only
--skip-existing          resume an interrupted batch
--allow-mixed-era        permit a cell dat and portal dat from different
                         container generations (see below)
```

`--allow-mixed-era` exists for one honest case: cells converted from the
original format to the retail one still name the same meshes, so a converted
cell dat pairs correctly with the period portal it came from. Every other
mismatch is a mistake, which is why it is refused by default.

---

## What "dungeon" means here

`--dungeons-only` keeps landblocks with **no physical way in**. A cave or a
building sits in a landblock that also has objects standing on the surface; a
house has hooks, storage and a slumlord. A dungeon has neither — you arrive by
portal or by a transport spell.

On end-of-retail data that splits 3,409 interiors into:

| | count |
|---|---|
| dungeons | 1,016 |
| caves and buildings | 993 |
| housing | 700 |
| under 8 cells | 599 |
| no object data | 101 |

Of the dungeons, 880 have a portal pointing at them and 136 do not — those are
the spell-only ones.

---

## What ends up on a map

**Plan** — floors drawn from real floor polygons, interior walls from the
vertical faces of the same meshes. Ramps and stairs are shaded dark-to-light
with an arrow pointing uphill. Damage floors are coloured by damage type and
named for the weenie that creates them, so a lava bed reads as
`"Mag-Ma!" - fire damage` and a grate reads as `Hot Air - minor fire`. Bridges
and overpasses are hatched.

**Markers** — portal drop points (**D**) and exits (**E**), locked doors with a
key glyph, doors wired to a lever or plate numbered **D1/L1** with a dashed
line to the opener, chests, traps, pressure plates, levers, items on the
ground, NPCs, creature spawns and lifestones.

**Chrome** — dungeon name, entry chain, level requirement, quest flag, surface
entrance coordinates, nearest lifestone, exits, a monster roster grouped by
creature type, boss drop lists, legend, north arrow and a scale bar in metres.

Names that repeat more than three times are not labelled on the plan — a
dungeon with 55 Wailing Statues gets one roster line instead of 55 labels — and
when a dungeon holds more than ten NPCs none are labelled.

---

## How the interesting parts work

**Floors, not z-bands.** Levels come from the cell portal graph, not from
`z / 6`. Flat cells whose surfaces touch are one floor. A ramp belongs to the
floor at its lower end, because a ramp is how you leave a floor rather than
part of two. Fragments under ten cells — doorway stubs, stair landings — are
absorbed into whichever floor they connect to most. Groups are then merged when
they share most of their footprint and sit within one storey of each other: a
hall can have a lava bed, a walkway and a gallery at 6 m spacing, and that is
one space seen at three heights, not three floors. On Aerfalle Keep this turns
twelve z-bands into six floors. Floors are numbered relative to the arrival
point, so the drop is always LVL 0.

**Walls.** Every vertical polygon in a cell projects to a segment in plan.
Doorway quads are excluded by matching each polygon against the cell's portal
list — without that filter every cell boundary draws as a wall and an open hall
comes out as a grid.

**Metres.** One world unit is one metre. ACE compares `Location.DistanceTo`
directly against a missile cap documented as `85.0f / MetersToYards`, and a
terrain cell's `squareLength` is 24, so the scale bar reads 24 m.

**Separating floors.** Most dungeons read fine as one plan, because their
floors sit beside each other rather than on top. Some do not: Acid Ziggurat has
six floors sharing 64% of one footprint, and composited it is a solid mass of
overlapping walls.

`--layout auto` measures that overlap and separates the floors when it passes
0.30, joining the cut corridors with matching red numbers.

Floors are not separated one per panel, though. Splitting is only needed where
floors sit on top of each other, so they are first binned into the fewest
sheets that have no overlap *inside* a sheet -- greedy colouring over the
overlaps graph, largest floor first. A wing here and a wing there that never
touch get drawn together at their true positions, which is what a hand-drawn
map does. Black Death Catacombs has 26 floors and needs only 8 sheets; Mines of
Despair has 9 floors and needs 3.

The sheets are then stacked in a column when there are six or fewer, or packed
to a roughly square page when there are more.

**Shells.** A floor with nothing on it that sits almost entirely inside the
footprint of other floors is a roof or an empty storey, and drawing it just
buries what it covers. Those are dropped before compositing; `--keep-shells`
disables that.

"Nothing on it" is a claim about object data, so the test only runs where
there is object data to make it. Without a world database every floor looks
empty and the rule would delete real storeys rather than shells — on the
February 2001 cell dat that silently cost 889 cells across 19 landblocks,
including five of the seven floors of `02D7`. Geometry-only runs therefore
keep every floor, and so do landblocks no world database covers.

---

## Using it as a library

```python
from landblock import open_dat, Geometry, World, render_map, load_enums

load_enums('landblock/enums.json')
geom  = Geometry(open_dat('client_cell_1.dat'), open_dat('client_portal.dat'))
world = World('/path/to/ACE-World/Database')

cells        = geom.load(0x01F5)
insts, links = world.instances(0x01F5)
render_map(0x01F5, cells, insts, links, world, 'aerfalle.png')
```

`open_dat` alone is a usable reader for any AC dat file of either generation,
and `read_environment` / `read_environment_old` parse `0x0D` meshes.

---

## Working with the dats directly

Three command-line tools sit alongside the map generator. All of them read
either container generation, and none of them ever writes to an input file.

### Comparing two client eras

```bash
python3 dungeon_diff.py --old-cell cell.dat     --old-portal portal.dat \
                        --new-cell client_cell_1.dat \
                        --new-portal client_portal.dat \
                        --kind dungeon --out changes.csv
```

Reports every landblock the old dats have that the new ones dropped or
changed, with an update percentage. Landblocks only the new dats have are
ignored — the question is what happened to the old content.

This cannot be a byte diff. Between Dark Majesty and end of retail every
surface id in the game was renumbered, so an untouched cell still stores
different numbers and a naive comparison calls 100% of dungeons "updated".
The tool therefore learns the renumbering from the data first: it collects
every (old surface, new surface) pair across all shared cells, and where an
old id maps overwhelmingly to one new id that is renumbering rather than
retexturing. Only deviations count. On a 2004-to-retail run it learns 660
ids, 625 of them unambiguous.

Two percentages come out. `update_pct` counts any difference; `struct_pct`
discounts texture-only changes, which separates "this dungeon was rebuilt"
from "this dungeon was repainted" — on one landblock those read 99.3% and
54.9%. Room meshes are compared separately, in `meshes_changed`, because a
dungeon whose cells are identical can still have been rebuilt underneath.

`--kind dungeon` keeps landblocks with no outside connection at all: no cell
flagged visible from outdoors and no portal leading outdoors. `building` and
`cave` are told apart by whether the LandBlockInfo registers a building, which
is a weak test — a cave mouth is registered the same way a house is — so treat
that split as provisional.

`old_lbi_cells` reading 0 against a non-zero `old_cells` marks orphaned
records: cells left behind when content was retired, which the LandBlockInfo
no longer counts. Comparing against those is meaningless, and they are
flagged so you can filter them out.

### Merging records into a client dat

```bash
# prove the machinery is lossless on your own files first
python3 dat_merge.py --verify client_portal.dat

python3 dat_merge.py --base client_portal.dat --patch mypatch.dat \
                     --out client_portal.new.dat
```

A patch is a few hundred KB but the file it goes into is not — an
end-of-retail portal.dat is 927 MB across 79,694 records. Records are
therefore written straight through: walk the source, copy each block chain to
the output as it is read, and keep only `(id, offset, size)` per record. The
B-tree is bulk-loaded over those offsets at the end and its nodes appended as
ordinary blocks. Peak memory is the index alone — 64 MB for that 927 MB
portal, about 350 MB for a retail cell dat with 805,348 records.

`--verify` round-trips a dat through the writer and checks the result is
byte-identical, that every id is reachable by the ordered descent a client's
`Lookup` performs, and that an absent key is correctly not found. That last
part matters: a tree can enumerate correctly and still be unnavigable.

**A patch record whose id already exists is an error, not an overwrite.**
Silently replacing a record destroys whatever was there — someone else's
dungeon, a shared mesh — and the failure would only show up in game.
`--allow-empty-lbi` permits one narrow exception: replacing a LandBlockInfo
that declares zero cells, which is the registration a restored dungeon needs
and currently says "nothing here". `--overwrite` disables the guard entirely
and is rarely what you want.

Note that the output is compacted as a side effect of being rebuilt rather
than edited in place: records land contiguously, the B-tree is packed near
capacity instead of half-full, and the free-block list is not carried over.
A retail cell dat comes out 14 MB smaller while holding 24,000 more records.
Every field that constitutes the dat's identity — data set and subset, master
map id, engine and game pack versions, the version stamp — is preserved
verbatim; only size, B-tree root and the free-list fields change, because
those describe the physical file.

### Converting records between eras

`landblock/transcode.py` re-encodes original-format records into the retail
layout. The two generations carry the same fields in the same order; retail
repeats the cell id up front and omits the 4-byte padding the original format
inserts after the surface list, the portal list, the stab list, the statics,
every polygon, and the polygon-index lists inside BSP nodes. So converting a
mesh is a walk that copies each field through and drops padding at those
points — the BSP trees are traversed, never interpreted.

```python
from landblock import open_dat, envcell_to_tod, environment_to_tod
from landblock.transcode import verify_envcell, verify_environment

src = open_dat('cell.dat')                      # original era
out = envcell_to_tod(src.get(0x02B90100))
verify_envcell(src.get(0x02B90100), out)        # raises unless every field agrees
```

`relocate_envcell` moves a cell to another landblock. Only the embedded cell
id carries the landblock; portal links and stab entries are 16-bit and
landblock-relative, so that is a four-byte edit per cell.

Textures need real work rather than a copy, because retail restructured the
chain. Originally a Surface pointed straight at an indexed image plus a
palette; retail inserts a level — Surface → SurfaceTexture → RenderSurface —
and RenderSurface holds plain D3D-format pixels with no palette at all.
`imgtex_to_rgb`, `render_surface_record`, `surface_texture_record` and
`surface_to_tod` do that conversion. `gfxobj_to_tod` handles static props,
whose internal counts switch to retail's variable-length integer encoding.

Verified across every dat tested: 546,064 cells and 665 meshes convert and
re-verify field-for-field, including UV runs, per-polygon surface indices,
stippling, cull mode and full BSP trees.

### Restoring content

`build_restore.py` assembles a patch that puts dungeons from an older client
into a newer one. It is the least general of the tools — the tier lists near
the top are specific to one analysis and you will want `--landblocks` instead
— but the placement rule is the point:

> A dungeon keeps its original landblock only where that landblock is empty
> in the target. Anything whose slot is occupied moves to a free landblock,
> so nothing already in the client is displaced.

`--avoid` takes landblocks another patch has already claimed, so two patches
built independently never collide. `--new-portal-index` accepts a text file
of hex ids instead of the portal itself, which lets the build run somewhere
the real portal.dat is not.

---

## Files

| | |
|---|---|
| `tests/` | ToD container, geometry and transcode regression tests |
| `landblock/dat.py` | dat containers, both generations: header, B-tree directory, block chains |
| `landblock/geom.py` | EnvCells, `0x0D` meshes, floors, walls, ramps — ToD and original encodings |
| `landblock/world.py` | weenie index and landblock instances, base + patches |
| `landblock/annotations.py` | optional community spreadsheet loader |
| `landblock/render.py` | floor grouping, layout and drawing |
| `landblock/datwrite.py` | dat writers for both generations, B-tree bulk load |
| `landblock/transcode.py` | original-era records re-encoded to the retail layout |
| `landblock/__main__.py` | command line |
| `landblock/enums.json` | WeenieType and CreatureType, exported from ACE |
| `dungeon_diff.py` | compare two client eras, standalone |
| `dat_merge.py` | streaming merge and round-trip verification, standalone |
| `build_restore.py` | assemble a restoration patch |

`dungeon_diff.py` and `dat_merge.py` are self-contained — they carry their own
dat reader and need nothing but Python, so they can be dropped next to a
client install on their own.

---

## Verifying against a new client

The mesh parser raises unless every environment file consumes to the exact
byte, which makes it a usable smoke test:

```python
from landblock import open_dat, read_environment, read_environment_old
d = open_dat('client_portal.dat')
parse = read_environment_old if d.era == 'pretod' else read_environment
for i in sorted(x for x in d.files if 0x0D000000 <= x <= 0x0D00FFFF):
    parse(d.get(i))                 # raises on any mismatch
```

End-of-retail passes 772 of 772; December 2000 passes 478 of 478; September
2004 passes 665 of 665.

`tests/` covers the retail decoders without a retail client, by building
valid ToD-format containers and meshes in memory and reading them back.

---

## Known limits

* Multi-landblock dungeons render as separate maps rather than being stitched.
* Town names are not available: ACE ships the `points_of_interest` schema but
  no rows, so "nearest town" cannot be derived.
* Nearest lifestone is straight-line distance, not travel distance.
* A handful of landblocks have geometry but no object data in any world
  database, and render as bare plans.
* `HAZARD_DAMAGE` in `render.py` decides whether a hotspot is a hazard or
  ambient scenery. At the default of 10, end-of-retail "Hot Air" (12 and 20
  damage) paints as a fire hazard rather than the grate it looks like in game.
* Everything the writing and converting side does is verified against the
  format and against real client data — never against a running client. No
  output of `dat_merge.py` has been loaded by the game.
* `dat_merge.py` rebuilds rather than inserting in place, so the output is
  compacted and its block layout differs from the input. Contents are
  identical; byte-for-byte file comparison between the two is not meaningful.
* Retail's `RenderSurface` header carries a field whose meaning is unknown.
  Converted textures use the value most common for their size and format; it
  does not affect how the pixels are read.
* Telling caves from buildings in `dungeon_diff.py` rests on whether the
  LandBlockInfo registers a building, and a cave mouth registers the same way
  a house does. Dungeons are separated reliably; that split is not.

## Credits

Dungeon geometry and meshes are read from the Asheron's Call client data files.
Object placements come from the ACEmulator ACE-World database. The optional
annotations come from the community Landblocks spreadsheet compiled after
retail by Immortalbob, Beale, Sylence, Justin, Howard (Oberon), Proximal,
High-Voltage, Cpl Brown, Zarto and Crimson Mage, with thanks to OptimShi and
Pea. The 2005+ formats were
transcribed from ACEmulator's `ACE.DatLoader`; the original 1999–2005 formats
from the `PRE_TOD` branches of the PhatSDK as preserved in the ClassicDereth
project (github.com/bDekaru/ClassicDereth), descended from Pea's phatac.
Layout conventions are modelled
on the hand-drawn maps of the Asheron's Call mapping community.

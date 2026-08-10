# How to diff two clients and patch dungeons from one into the other

Start to finish, from an empty folder. Every command is a single line for
Windows `cmd.exe` — it uses `^` for continuation, never `\`, so do not wrap
them. On Linux or macOS use `python3` the same way.

Nothing here modifies your input dats. Every step writes new files.

---

## 0. Set up the folder

Unzip the release into a new folder, then put four dat files beside it: the
pair you are taking dungeons **from**, and the pair you are putting them
**into**.

```
mkdir C:\ac\work
cd /d C:\ac\work
```

Unzip `landblock_source.zip` here. You should end up with:

```
landblock\            the package
dungeon_diff.py       compare, deduplicate, validate
dat_merge.py          merge and verify
build_restore.py      assemble a patch
```

Copy your dats in and give them names you will not confuse:

```
copy "C:\path\to\old\cell.dat"           old_cell.dat
copy "C:\path\to\old\portal.dat"         old_portal.dat
copy "C:\path\to\new\client_cell_1.dat"  new_cell.dat
copy "C:\path\to\new\client_portal.dat"  new_portal.dat
```

Only the map step needs libraries:

```
pip install pillow numpy
```

Each dat pair must be **from the same client**. A cell dat names its rooms by
id and the portal dat supplies them, so a mismatched pair silently loses
rooms. Old and new may be any two eras, in either container generation — the
tools detect which is which.

---

## 1. Check the inputs before trusting them

```
python3 dungeon_diff.py --validate old_cell.dat
python3 dungeon_diff.py --validate new_cell.dat
```

Two counts come back. **Broken** landblocks name a cell that is not present —
a viewer that follows one of those references crashes, so anything you take
from a broken landblock needs care. **Orphan-carrying** landblocks are fine;
retail itself ships 38 of them.

Then prove the merge machinery is lossless on your own file:

```
python3 dat_merge.py --verify new_portal.dat
```

Expect `VERDICT: lossless`. It round-trips the whole dat and checks every
record survives byte-identical and stays reachable. Needs free space equal to
the file, and about 15 seconds for a 900 MB portal.

---

## 2. Diff

```
python3 dungeon_diff.py --old-cell old_cell.dat --old-portal old_portal.dat --new-cell new_cell.dat --new-portal new_portal.dat --kind dungeon --out changes.csv
```

This reports every landblock the **old** client has that the new one dropped
or changed. Landblocks only the new client has are ignored.

Expect output like:

```
old: old_cell.dat (pretod) 1480 landblocks with interiors
new: new_cell.dat (tod) 3409 landblocks with interiors
surface renumbering learned from 574 ids; 412 map unambiguously
393 landblocks reported: 29 removed, 364 updated
update bands: {'100%': 241, '75-99%': 4, ...}
```

The renumbering line matters. Every surface id in the game was renumbered
between eras, so a naive comparison calls everything changed; the tool learns
the mapping from your data first and only counts deviations from it.

**Reading `changes.csv`.** Sort by `struct_pct`, not `update_pct`.

| column | meaning |
|---|---|
| `struct_pct` | how much changed, ignoring texture-only differences |
| `update_pct` | how much changed including retexturing |
| `status` | `REMOVED` means the new client dropped it entirely |
| `orphaned` | old side is retired junk; skip these rows |
| `meshes_changed` | the rooms themselves were rebuilt, not just rearranged |

`struct_pct` at 100 means the new client rebuilt the dungeon completely, so
the old version survives nowhere else — that is what is worth restoring. A
big gap between the two percentages means it was mostly repainted, and the
new client's version is probably the better one.

---

## 3. Collapse duplicates

One layout is often registered at many landblocks, so the diff overstates how
much distinct content changed.

```
python3 dungeon_diff.py --dedupe old_cell.dat changes.csv
```

```
270 landblocks at or above 100% structural change
   42 distinct layouts, 11150 cells in total
   170 landblocks share one layout: 021E 021F 0220 0221 ...

--landblocks 010B,0114,0117,015F,0160,016A,016B,...
```

Copy that final line. Lower the bar with `--min-struct 50` if 100% yields
little.

---

## 4. Build the patch

Paste the list from step 3 after `--landblocks`:

```
python3 build_restore.py --old-cell old_cell.dat --old-portal old_portal.dat --new-cell new_cell.dat --new-portal new_portal.dat --landblocks 010B,0114,0117,015F,0160,016A --out-dir myset
```

```
6 dungeons, 1910 cell records, 0 portal records  [pretod source -> pretod target, same format]
  kept original landblock : 4
  relocated (slot in use) : 2
     010B -> 5000  (169 cells)
     0117 -> 5001  (768 cells)
  wrote myset/restore_cell.dat and (no portal changes)
```

What it does, and why:

- **A dungeon keeps its original landblock only if that landblock is empty in
  the target.** Anything whose slot is occupied moves to a free landblock, so
  nothing already in the new client is displaced. `--spare-base 5300` picks
  where relocations start.
- Records are written in the **target's** format, converting between
  generations when needed. It says which in the first line.
- Cells the old LandBlockInfo does not count are **dropped** — they are
  retired leftovers, and copying them produces landblocks that crash readers.
- Any mesh, texture or prop the target lacks is **carried over** into
  `restore_portal.dat`. If the target already has everything, no portal patch
  is produced and you skip that merge.
- It **refuses to emit** a landblock that fails validation.

Building a second patch to sit alongside a first? Tell it what is taken:

```
python3 build_restore.py ... --avoid 5000,5001,5002 --spare-base 5300 --out-dir myset2
```

---

## 5. Patch

```
python3 dat_merge.py --base new_cell.dat --patch myset\restore_cell.dat --out new_cell.restored.dat --allow-empty-lbi
```

If step 4 produced a portal patch:

```
python3 dat_merge.py --base new_portal.dat --patch myset\restore_portal.dat --out new_portal.restored.dat --allow-empty-lbi
```

Several patches into one output, in a single pass:

```
python3 dat_merge.py --base new_cell.dat --patch a\restore_cell.dat --patch b\restore_cell.dat --out new_cell.restored.dat --allow-empty-lbi
```

```
  replacing 4 empty LandBlockInfo records: 0114FFFE 015FFFFE 0160FFFE 016AFFFE
new_cell.restored.dat: 479154 records (477244 copied, 4 replaced, 1906 added), 168.6 MB
```

`--allow-empty-lbi` permits replacing a LandBlockInfo that declares **zero**
cells — that record is how a landblock registers an interior, and it currently
says there is none. Nothing else may be replaced. If the merge stops with a
collision it means two patches want the same id and one would destroy the
other; fix the patch rather than reaching for `--overwrite`.

Keep free space equal to the output. The result is *smaller* than the input
even with records added — a tighter index and no dead blocks, not lost data.

---

## 6. Verify

```
python3 dungeon_diff.py --validate new_cell.restored.dat
```

The broken count must be no worse than step 1. Then confirm the restored
dungeons match where they came from:

```
python3 dungeon_diff.py --old-cell old_cell.dat --old-portal old_portal.dat --new-cell new_cell.restored.dat --kind dungeon --out check.csv
```

Every landblock you restored should now be absent from `check.csv`, or sitting
at 0% — it matches the old client because it *is* the old client's version.

---

## 7. Maps (optional)

```
python3 -m landblock --cell myset\restore_cell.dat --portal old_portal.dat --out maps --all --min-cells 1
```

One PNG per restored dungeon, named by its new landblock id. Render from the
patch plus the **old** portal — that is the pair the geometry came from, and
it avoids handling a gigabyte-sized merged portal.

If the patch was converted between generations, add `--allow-mixed-era`. That
is refused by default because a mismatched pair is normally a mistake; here it
is not, since conversion does not change which meshes a cell names.

---

## 8. Install

Back up first, then rename into place. A client looks for exact filenames:

```
ren client_cell_1.dat client_cell_1.stock.dat
copy new_cell.restored.dat client_cell_1.dat
```

---

## If something goes wrong

| symptom | cause |
|---|---|
| `unrecognized arguments: \` | `cmd` needs `^` for continuation, or one line |
| `error: the following arguments are required` | old copy of the script; unzip the release over it |
| stale behaviour after updating | delete `__pycache__\` |
| `is tod but the base is pretod` | patch and target are different generations |
| `%d patch records already exist` | two patches collide; use `--avoid` when building |
| `cannot find the landblock package` | `landblock\` must sit beside the scripts |
| `SyntaxError` on a path like `dmreleasedats\CELL.DAT` | use forward slashes in paths you pass to Python |

Everything here is verified against the format and against real client data —
never against a running game. `--validate` checks the invariants known to
matter, which is not a guarantee of loadability.

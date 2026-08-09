# Regression tests

The retail (2005 → end of retail) decoders have no client dats checked in, so
these build valid Throne-of-Destiny-format data by hand and read it back:

* `test_tod_container.py` — synthesises a ToD dat: 0x140 header, 24-byte
  directory entries, multi-block payload chains and a two-level B-tree, then
  checks every payload round-trips byte-identically and that `open_dat()`
  routes 0x140 to `Dat` and 0x12C to `OldDat`.
* `test_tod_geometry.py` — hand-encodes a ToD environment (vertex array,
  polygons, cell and physics BSPs) and an EnvCell, reads them through
  `Geometry`, and checks the floor lands at the right world coordinates, the
  vertical face becomes a wall, and a deliberately mismatched dat pair is
  reported rather than silently drawn with holes.

Both run without arguments:

    python3 tests/test_tod_container.py
    python3 tests/test_tod_geometry.py

The original-era decoders are covered by the dats themselves — every
environment and every interior cell must consume to the exact byte or the
parser raises (see "Verifying against a new client" in the README).

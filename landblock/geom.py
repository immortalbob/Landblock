"""Dungeon geometry: EnvCells from cell.dat and 0x0D meshes from portal.dat."""
import math
from .dat import Reader

# ---------------------------------------------------------------- BSP skipping
# We do not need the BSP trees, but we must consume them byte-exactly to reach
# the next CellStruct in an environment file.

def _skip_bsp(r, kind):
    tag = r.b[r.p:r.p + 4][::-1].decode('latin-1')
    if tag == 'LEAF':
        r.skip(4); r.i32()
        if kind == 'physics':
            r.i32(); r.skip(16)
            r.skip(2 * r.u32())
        return
    if tag == 'PORT':
        r.skip(4); r.skip(16)
        _skip_bsp(r, kind); _skip_bsp(r, kind)
        if kind == 'drawing':
            r.skip(16)
            npoly = r.u32(); nport = r.u32()
            r.skip(2 * npoly); r.skip(4 * nport)
        return
    r.skip(4); r.skip(16)
    if tag in ('BPnn', 'BPIn', 'BpIN', 'BpnN'):
        _skip_bsp(r, kind)
    elif tag in ('BPIN', 'BPnN'):
        _skip_bsp(r, kind); _skip_bsp(r, kind)
    if kind == 'cell':
        return
    r.skip(16)
    if kind == 'physics':
        return
    r.skip(2 * r.u32())


def _read_polygon(r):
    npts = r.u8()
    stip = r.u8()
    sides = r.i32()
    r.i16(); r.i16()
    vids = [r.i16() for _ in range(npts)]
    if not (stip & 0x04):
        r.skip(npts)
    if sides == 2 and not (stip & 0x08):
        r.skip(npts)
    return vids


def _read_cellstruct(r):
    npoly = r.u32(); nphys = r.u32(); nport = r.u32()
    r.i32()                      # vertex type (always 1)
    nvert = r.u32()
    verts = {}
    for _ in range(nvert):
        key = r.u16()
        nuv = r.u16()
        ox, oy, oz = r.vec3()
        nx, ny, nz = r.vec3()
        r.skip(8 * nuv)
        verts[key] = (ox, oy, oz, nx, ny, nz)
    polys = {}
    for _ in range(npoly):
        k = r.u16()
        polys[k] = _read_polygon(r)
    r.skip(2 * nport)
    r.align()
    _skip_bsp(r, 'cell')
    for _ in range(nphys):
        r.u16(); _read_polygon(r)
    _skip_bsp(r, 'physics')
    if r.u32() != 0:
        _skip_bsp(r, 'drawing')
    r.align()
    return verts, polys


def read_environment(buf):
    r = Reader(buf)
    r.u32()
    n = r.u32()
    cells = {}
    for _ in range(n):
        key = r.u32()
        cells[key] = _read_cellstruct(r)
    if r.p != len(buf):
        raise ValueError('environment parse length mismatch (%d/%d)' % (r.p, len(buf)))
    return cells


# ---------------------------------------------------------------------- cells
FLAG_SEEN_OUTSIDE = 0x01
FLAG_HAS_STATIC = 0x02
FLAG_HAS_RESTRICT = 0x08


class EnvCellRec:
    __slots__ = ('cell_id', 'env_id', 'struct_idx', 'origin', 'rot',
                 'portals', 'statics', 'floors', 'slopes', 'voids', 'walls',
                 'zmin', 'zmax')


def qrot(q, v):
    w, qx, qy, qz = q
    x, y, z = v
    cx = qy * z - qz * y
    cy = qz * x - qx * z
    cz = qx * y - qy * x
    c2x = qy * cz - qz * cy
    c2y = qz * cx - qx * cz
    c2z = qx * cy - qy * cx
    return (x + 2 * w * cx + 2 * c2x,
            y + 2 * w * cy + 2 * c2y,
            z + 2 * w * cz + 2 * c2z)


class Geometry:
    """Loads and caches dungeon geometry for landblocks."""

    def __init__(self, cell_dat, portal_dat):
        self.cell = cell_dat
        self.portal = portal_dat
        self._env = {}
        self._index = None

    def env(self, eid):
        if eid not in self._env:
            try:
                self._env[eid] = read_environment(self.portal.get(eid))
            except Exception:
                self._env[eid] = {}
        return self._env[eid]

    def landblocks_with_interiors(self):
        if self._index is None:
            idx = {}
            for i in self.cell.files:
                lo = i & 0xFFFF
                if 0x0100 <= lo < 0xFFFE:
                    idx.setdefault(i >> 16, []).append(i)
            self._index = idx
        return self._index

    def load(self, lb):
        """Return list of EnvCellRec with world-space (landblock-space) floors."""
        out = []
        for cid in sorted(self.landblocks_with_interiors().get(lb, [])):
            r = Reader(self.cell.get(cid))
            r.u32()
            flags = r.u32()
            r.u32()
            nsurf = r.u8(); nport = r.u8(); nvis = r.u16()
            r.skip(2 * nsurf)
            env_id = 0x0D000000 | r.u16()
            sidx = r.u16()
            origin = r.vec3()
            rot = r.quat()
            portals = [(r.u16(), r.u16(), r.u16(), r.u16()) for _ in range(nport)]
            r.skip(2 * nvis)
            statics = []
            if flags & FLAG_HAS_STATIC:
                for _ in range(r.u32()):
                    did = r.u32()
                    statics.append((did, r.vec3(), r.quat()))
            rec = EnvCellRec()
            rec.cell_id = cid
            rec.env_id = env_id
            rec.struct_idx = sidx
            rec.origin = origin
            rec.rot = rot
            rec.portals = portals
            rec.statics = statics
            (rec.floors, rec.slopes, rec.voids, rec.walls,
             rec.zmin, rec.zmax) = self._floors(env_id, sidx, origin, rot,
                                               {p[1] for p in portals})
            out.append(rec)
        return out

    def _floors(self, env_id, sidx, origin, rot, portal_polys=()):
        cells = self.env(env_id)
        if sidx not in cells:
            return [], [], [], [], 0.0, 0.0
        verts, polys = cells[sidx]
        if not verts:
            return [], [], [], [], 0.0, 0.0
        floors = []
        slopes = []
        caps = []
        walls = []
        zs = []
        for pkey, vids in polys.items():
            local = [verts[v][:3] for v in vids]
            nz = newell(local)[2]
            if abs(nz) <= 0.35:
                if pkey in portal_polys:
                    # the quad filling a doorway between two cells, not a wall
                    continue
                # a wall: its plan projection is a segment between the two
                # furthest-apart vertices. These are the room outlines.
                pts2 = []
                for v in vids:
                    wx, wy, _wz = qrot(rot, verts[v][:3])
                    pts2.append((origin[0] + wx, origin[1] + wy))
                best = None
                for ii in range(len(pts2)):
                    for jj in range(ii + 1, len(pts2)):
                        d = (pts2[ii][0] - pts2[jj][0]) ** 2 + (pts2[ii][1] - pts2[jj][1]) ** 2
                        if best is None or d > best[0]:
                            best = (d, pts2[ii], pts2[jj])
                if best and best[0] > 0.6:
                    walls.append((best[1], best[2]))
            # floor = upward-facing surface. Ceilings point down, walls sideways.
            # This keeps tilted ramps and stair treads, which a min-z test drops.
            if nz <= 0.35:
                if nz < -0.35:
                    cap = []
                    for v in vids:
                        wx, wy, wz = qrot(rot, verts[v][:3])
                        cap.append((origin[0] + wx, origin[1] + wy, origin[2] + wz))
                    if _area2(cap) >= 0.5:
                        caps.append(cap)
                continue
            pts = []
            for v in vids:
                wx, wy, wz = qrot(rot, verts[v][:3])
                pts.append((origin[0] + wx, origin[1] + wy, origin[2] + wz))
            if _area2(pts) < 0.5:
                continue
            floors.append(pts)
            zs.extend(p[2] for p in pts)
            rise = max(p[2] for p in pts) - min(p[2] for p in pts)
            if rise > 1.0:
                grad, ang = slope_of(pts)
                steps = len({round(p[2], 1) for p in pts})
                slopes.append(dict(pts=pts, rise=rise, angle=ang, grad=grad,
                                   kind='stairs' if steps > 2 else 'ramp'))
        # a cell with only a downward-facing cap is open space seen from above --
        # the client renders nothing there, so it is not walkable map area
        if not zs:
            return [], [], caps, walls, origin[2], origin[2]
        return floors, slopes, [], walls, min(zs), max(zs)


def newell(pts):
    """Geometric normal of a polygon (Newell's method)."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1, z1 = pts[i]
        x2, y2, z2 = pts[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / l, ny / l, nz / l


def _area2(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def slope_of(pts):
    """Least-squares plane fit -> (gradient magnitude, uphill direction radians)."""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    mz = sum(p[2] for p in pts) / n
    sxx = syy = sxy = sxz = syz = 0.0
    for x, y, z in pts:
        dx, dy, dz = x - mx, y - my, z - mz
        sxx += dx * dx; syy += dy * dy; sxy += dx * dy
        sxz += dx * dz; syz += dy * dz
    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-6:
        return 0.0, 0.0
    a = (sxz * syy - syz * sxy) / det
    b = (syz * sxx - sxz * sxy) / det
    return math.hypot(a, b), math.atan2(b, a)


def shift_cell(c, dx, dy):
    """Copy a cell with its geometry translated in plan."""
    n = EnvCellRec()
    n.cell_id = c.cell_id
    n.env_id = c.env_id
    n.struct_idx = c.struct_idx
    n.origin = (c.origin[0] + dx, c.origin[1] + dy, c.origin[2])
    n.rot = c.rot
    n.portals = c.portals
    n.statics = c.statics
    n.floors = [[(p[0] + dx, p[1] + dy, p[2]) for p in poly] for poly in c.floors]
    n.slopes = [dict(sl, pts=[(p[0] + dx, p[1] + dy, p[2]) for p in sl['pts']])
                for sl in c.slopes]
    n.voids = [[(p[0] + dx, p[1] + dy, p[2]) for p in poly] for poly in c.voids]
    n.walls = [((a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy)) for a, b in c.walls]
    n.zmin, n.zmax = c.zmin, c.zmax
    return n

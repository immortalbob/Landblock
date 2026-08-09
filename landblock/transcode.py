"""Re-encode original-era (pre-Throne of Destiny) records into the retail layout.

The two generations describe the same structures with the same fields in the
same order. What differs is small print:

* an original EnvCell opens ``{flags, id}`` where retail opens ``{id, flags,
  id}``, and pads to a 4-byte boundary after the surface list, the portal
  list, the stab list and the static-object list;
* original polygons pad after their vertex and UV runs;
* original BSP nodes pad after the polygon-index list they carry;
* an original LandBlockInfo pads after its building list.

So converting is not a rewrite so much as a walk that copies every field
through and drops padding at exactly those points. Two padding points are
*kept* -- inside a cell struct after the portal-index list, and at the end of
a cell struct -- because retail pads there too; those are re-emitted against
the output position rather than copied, since removing earlier bytes moves
everything after them.

Correctness standard is the same as the readers': the walk must consume its
input to the exact byte, and the result must parse under the retail decoders
and yield identical geometry. ``verify_environment`` and ``verify_envcell``
check precisely that.
"""
import struct

from .dat import Reader
from .geom import read_environment, read_environment_old


class TranscodeError(ValueError):
    pass


class _Recoder:
    """Copies a buffer through field by field, dropping or re-laying padding."""

    def __init__(self, buf):
        self.r = Reader(buf)
        self.out = bytearray()
        self.mark = 0

    def flush(self):
        """Copy everything read since the last flush into the output."""
        self.out += self.r.b[self.mark:self.r.p]
        self.mark = self.r.p

    def align(self, keep):
        """Consume the input's 4-byte padding.

        keep=False drops it -- retail has no padding here. keep=True re-emits
        padding sized for the *output* position, which is not the input's,
        because earlier drops have shifted everything along.
        """
        self.flush()
        self.r.align()
        self.mark = self.r.p
        if keep:
            self.out += b'\x00' * ((-len(self.out)) % 4)

    def done(self):
        self.flush()
        if self.r.p != len(self.r.b):
            raise TranscodeError('consumed %d of %d bytes' % (self.r.p, len(self.r.b)))
        return bytes(self.out)


# ------------------------------------------------------------------ meshes

def _recode_polygon(rc):
    """CPolygon: id and body copy through; the trailing pad goes."""
    r = rc.r
    r.i16()                                  # poly id -- retail reads it too
    npts = r.u8()
    stip = r.u8()
    sides = r.i32()
    r.i16(); r.i16()                         # pos / neg surface
    r.skip(2 * npts)                         # vertex indices
    if not (stip & 0x04):
        r.skip(npts)
    if sides == 2 and not (stip & 0x08):
        r.skip(npts)
    rc.align(keep=False)


def _recode_bsp(rc, kind):
    r = rc.r
    tag = r.b[r.p:r.p + 4][::-1].decode('latin-1')
    if tag == 'LEAF':
        r.skip(4); r.i32()
        if kind == 'physics':
            r.i32(); r.skip(16)
            r.skip(2 * r.u32())
            rc.align(keep=False)
        return
    if tag == 'PORT':
        r.skip(4); r.skip(16)
        _recode_bsp(rc, kind); _recode_bsp(rc, kind)
        if kind == 'drawing':
            r.skip(16)
            npoly = r.u32(); nport = r.u32()
            r.skip(2 * npoly); r.skip(4 * nport)
            rc.align(keep=False)
        return
    r.skip(4); r.skip(16)
    if tag in ('BPnn', 'BPIn', 'BpIN', 'BpnN'):
        _recode_bsp(rc, kind)
    elif tag in ('BPIN', 'BPnN'):
        _recode_bsp(rc, kind); _recode_bsp(rc, kind)
    if kind == 'cell':
        return
    r.skip(16)
    if kind == 'physics':
        return
    r.skip(2 * r.u32())
    rc.align(keep=False)


def _recode_cellstruct(rc):
    r = rc.r
    r.u32()                                  # cell struct id
    npoly = r.u32(); nphys = r.u32(); nport = r.u32()
    vtype = r.u32()
    nvert = r.u32()
    if vtype == 1:
        for _ in range(nvert):
            r.u16()
            nuv = r.u16()
            r.skip(24)                       # origin + normal
            r.skip(8 * nuv)
    elif vtype in (2, 3):
        r.skip(32 * nvert)
    else:
        raise TranscodeError('vertex type %d' % vtype)
    for _ in range(npoly):
        _recode_polygon(rc)
    r.skip(2 * nport)
    rc.align(keep=True)                      # retail pads here too
    _recode_bsp(rc, 'cell')
    for _ in range(nphys):
        _recode_polygon(rc)
    _recode_bsp(rc, 'physics')
    if r.u32() != 0:
        _recode_bsp(rc, 'drawing')
    rc.align(keep=True)                      # and here


def environment_to_tod(buf):
    """Convert one 0x0D environment record to the retail encoding."""
    rc = _Recoder(buf)
    rc.r.u32()                               # environment id
    n = rc.r.u32()
    for _ in range(n):
        _recode_cellstruct(rc)
    return rc.done()


# ---------------------------------------------------------------- envcells

def envcell_to_tod(buf):
    """Convert one EnvCell record to the retail encoding.

    Retail repeats the cell id up front and drops four padding points; every
    field is otherwise identical, so this rebuilds rather than copies.
    """
    r = Reader(buf)
    flags = r.u32()
    cid = r.u32()
    nsurf = r.u8(); nport = r.u8(); nstab = r.u16()
    surfaces = [r.u16() for _ in range(nsurf)]
    r.align()
    env_id = r.u16(); sidx = r.u16()
    frame = r.b[r.p:r.p + 28]                # origin vec3 + rotation quat
    r.skip(28)
    portals = [r.b[r.p + i * 8:r.p + i * 8 + 8] for i in range(nport)]
    r.skip(8 * nport)
    r.align()
    stabs = [r.u16() for _ in range(nstab)]
    r.align()
    statics = b''
    nstatic = 0
    if flags & 0x02:
        nstatic = r.u32()
        # per object: setup id (4) + origin vec3 (12) + rotation quat (16)
        statics = r.b[r.p:r.p + 32 * nstatic]
        r.skip(32 * nstatic)
        r.align()
    restriction = r.u32() if flags & 0x08 else None
    if r.p != len(buf):
        raise TranscodeError('envcell %08X: consumed %d of %d bytes'
                             % (cid, r.p, len(buf)))

    out = bytearray()
    out += struct.pack('<3I', cid, flags, cid)        # retail leads with the id
    out += struct.pack('<BBH', nsurf, nport, nstab)
    for s in surfaces:
        out += struct.pack('<H', s)
    out += struct.pack('<2H', env_id, sidx)
    out += frame
    for p in portals:
        out += p
    for s in stabs:
        out += struct.pack('<H', s)
    if flags & 0x02:
        out += struct.pack('<I', nstatic) + statics
    if restriction is not None:
        out += struct.pack('<I', restriction)
    return bytes(out)


def relocate_envcell(buf, landblock, era='pretod'):
    """Move a cell record to another landblock.

    Only the embedded cell id carries the landblock; portal links and stab
    entries are 16-bit and landblock-relative, so this is a four-byte edit.
    """
    out = bytearray(buf)
    off = 4 if era == 'pretod' else 0
    cid = struct.unpack_from('<I', out, off)[0]
    new = (landblock << 16) | (cid & 0xFFFF)
    struct.pack_into('<I', out, off, new)
    if era != 'pretod':
        struct.pack_into('<I', out, 8, new)           # retail repeats it
    return bytes(out), new


# ---------------------------------------------------------- landblock info

def landblockinfo_to_tod(buf):
    """Convert a LandBlockInfo record. Only the post-building pad differs."""
    rc = _Recoder(buf)
    r = rc.r
    r.u32()                                  # id
    r.u32()                                  # num_cells
    nobj = r.u32()
    r.skip(40 * nobj)                        # object id + frame
    info = r.u32()
    count, bflags = info & 0xFFFF, info >> 16
    for _ in range(count):
        r.u32()                              # building id
        r.skip(28)                           # frame
        r.u32()                              # num leaves
        nport = r.u32()
        for _ in range(nport):
            r.skip(6)                        # flags, cell, other portal
            ns = r.u16()
            r.skip(2 * ns)
            # both eras pad a 2-byte tail here, by different spellings
            if ns & 1:
                r.skip(2)
    rc.align(keep=False)                     # retail has no pad after buildings
    if bflags & 1:
        raise TranscodeError('restriction table present; not handled')
    rc.align(keep=True)
    return rc.done()


# ----------------------------------------------------------- verification

def verify_environment(old_buf, tod_buf):
    """The retail decode of the output must match the original decode."""
    a = read_environment_old(old_buf)
    b = read_environment(tod_buf)
    if set(a) != set(b):
        raise TranscodeError('cell struct ids differ')
    for k in a:
        if a[k][0] != b[k][0] or a[k][1] != b[k][1]:
            raise TranscodeError('cell struct %d differs after conversion' % k)
    return len(a)


def verify_envcell(old_buf, tod_buf):
    """Both decodes must agree on every field the map generator reads."""
    def old(buf):
        r = Reader(buf)
        flags = r.u32(); cid = r.u32()
        ns = r.u8(); npo = r.u8(); nst = r.u16()
        surf = [r.u16() for _ in range(ns)]
        r.align()
        rest = _tail(r, npo, nst, flags)
        return (flags, cid, surf) + rest

    def tod(buf):
        r = Reader(buf)
        lead = r.u32(); flags = r.u32(); cid = r.u32()
        if lead != cid:
            raise TranscodeError('retail id fields disagree')
        ns = r.u8(); npo = r.u8(); nst = r.u16()
        surf = [r.u16() for _ in range(ns)]
        rest = _tail(r, npo, nst, flags, pad=False)
        return (flags, cid, surf) + rest

    a, b = old(old_buf), tod(tod_buf)
    if a != b:
        raise TranscodeError('envcell fields differ after conversion')
    return a[1]


def _tail(r, nport, nstab, flags, pad=True):
    env = r.u16(); sidx = r.u16()
    origin = r.vec3(); rot = r.quat()
    portals = [(r.u16(), r.u16(), r.u16(), r.u16()) for _ in range(nport)]
    if pad:
        r.align()
    stabs = [r.u16() for _ in range(nstab)]
    if pad:
        r.align()
    statics = []
    if flags & 0x02:
        for _ in range(r.u32()):
            statics.append((r.u32(), r.vec3(), r.quat()))
        if pad:
            r.align()
    restriction = r.u32() if flags & 0x08 else None
    if r.p != len(r.b):
        raise TranscodeError('trailing bytes: %d of %d' % (r.p, len(r.b)))
    return (env, sidx, origin, rot, tuple(portals), tuple(stabs),
            tuple(statics), restriction)


# ----------------------------------------------------- textures and surfaces
# Retail restructured the texture chain. Originally a Surface (0x08) pointed
# straight at an ImgTex (0x05): an 8-bit indexed image plus a palette id.
# Retail inserts a level -- Surface -> SurfaceTexture (0x05) -> RenderSurface
# (0x06) -- and RenderSurface holds plain D3D-format pixels with no palette.
# So carrying a texture across is a real image conversion, not a copy: the
# indexed pixels are resolved through their palette and written out as
# D3DFMT_R8G8B8, which is the best-travelled 24-bit path in retail data.

D3DFMT_R8G8B8 = 20


def imgtex_to_rgb(buf, palette_buf):
    """Decode an original 8-bit indexed ImgTex to (width, height, RGB bytes)."""
    tid, typ, w, h = struct.unpack_from('<IiII', buf, 0)
    if typ != 2:
        raise TranscodeError('texture %08X is type %d, not 8-bit indexed' % (tid, typ))
    pixels = buf[16:16 + w * h]
    if len(pixels) != w * h:
        raise TranscodeError('texture %08X is short' % tid)
    _pid, ncol = struct.unpack_from('<2I', palette_buf, 0)
    pal = struct.unpack_from('<%dI' % ncol, palette_buf, 8)
    out = bytearray(w * h * 3)
    for i, idx in enumerate(pixels):
        c = pal[idx]
        out[i * 3] = (c >> 16) & 0xFF
        out[i * 3 + 1] = (c >> 8) & 0xFF
        out[i * 3 + 2] = c & 0xFF
    return w, h, bytes(out)


def render_surface_record(tex_id, width, height, rgb, unknown=3):
    """Build a retail RenderSurface (0x06): header then raw pixels."""
    if len(rgb) != width * height * 3:
        raise TranscodeError('pixel run does not match %dx%d' % (width, height))
    return struct.pack('<6I', tex_id, unknown, width, height,
                       D3DFMT_R8G8B8, len(rgb)) + rgb


def surface_texture_record(st_id, texture_ids, unknown=0, kind=2):
    """Build a retail SurfaceTexture (0x05): the id list retail added."""
    b = struct.pack('<2I', st_id, unknown) + struct.pack('<B', kind)
    b += struct.pack('<I', len(texture_ids))
    for t in texture_ids:
        b += struct.pack('<I', t)
    return b


def surface_to_tod(buf, texture_id=None):
    """Convert a Surface (0x08). Retail drops the leading id and carries no
    palette, because the palette is resolved when the texture is converted."""
    r = Reader(buf)
    r.u32()                                   # original leading id
    typ = r.u32()
    if typ & 6:
        tex = r.u32(); pal = r.u32()
    else:
        tex = pal = None
        colour = r.u32()
    tr, lum, dif = r.f32(), r.f32(), r.f32()
    if r.p != len(buf):
        raise TranscodeError('surface: consumed %d of %d' % (r.p, len(buf)))
    out = struct.pack('<I', typ)
    if typ & 6:
        out += struct.pack('<2I', texture_id if texture_id is not None else tex, 0)
    else:
        out += struct.pack('<I', colour)
    return out + struct.pack('<3f', tr, lum, dif)


# ------------------------------------------------------------ GfxObj props
# Static scenery in a cell points at a GfxObj. The record survived into retail
# with the same fields, but the three counts inside it switched from a plain
# 32-bit integer to the variable-length form retail uses everywhere, and the
# trailing pad went the way of all the others.

def pack_compressed32(v):
    """Retail's variable-length unsigned integer (ReadCompressedUInt32)."""
    if v < 0x80:
        return struct.pack('<B', v)
    if v < 0x4000:
        return struct.pack('<2B', 0x80 | (v >> 8), v & 0xFF)
    hi = v >> 16
    if hi >= 0x4000:
        raise TranscodeError('value %d too large for compressed32' % v)
    return struct.pack('<2B', 0xC0 | ((hi >> 8) & 0x3F), hi & 0xFF) \
        + struct.pack('<H', v & 0xFFFF)


def _recode_vertex_array(rc):
    r = rc.r
    vtype = r.u32()
    nvert = r.u32()
    if vtype == 1:
        for _ in range(nvert):
            r.u16()
            nuv = r.u16()
            r.skip(24)
            r.skip(8 * nuv)
    elif vtype in (2, 3):
        r.skip(32 * nvert)
    else:
        raise TranscodeError('vertex type %d' % vtype)


def gfxobj_to_tod(buf):
    """Convert a GfxObj (0x01) record to the retail encoding."""
    rc = _Recoder(buf)
    r = rc.r
    r.u32()                                  # id
    fields = r.u32()
    if fields & 0x04:
        # the pre-ToD surface-triangle-fan block; the SDK never implemented it
        raise TranscodeError('GfxObj uses triangle fans (Fields & 4)')
    rc.flush()

    nsurf = r.u32()                          # plain int becomes compressed
    rc.mark = r.p
    rc.out += pack_compressed32(nsurf)
    r.skip(4 * nsurf)
    rc.flush()

    _recode_vertex_array(rc)

    if fields & 0x01:
        nphys = r.u32()
        rc.flush(); rc.mark = r.p
        rc.out += pack_compressed32(nphys)
        for _ in range(nphys):
            _recode_polygon(rc)
        _recode_bsp(rc, 'physics')

    rc.flush()
    r.skip(12)                               # sort centre
    rc.flush()

    if fields & 0x02:
        npoly = r.u32()
        rc.flush(); rc.mark = r.p
        rc.out += pack_compressed32(npoly)
        for _ in range(npoly):
            _recode_polygon(rc)
        _recode_bsp(rc, 'drawing')

    rc.align(keep=False)                     # retail has no trailing pad
    return rc.done()

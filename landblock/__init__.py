"""landblock -- dungeon maps for Asheron's Call, generated from game data.

    from landblock import open_dat, Geometry, World, render_map

    geom = Geometry(open_dat('client_cell_1.dat'), open_dat('client_portal.dat'))
    world = World('/path/to/ACE-World/Database')
    cells = geom.load(0x01F5)
    insts, links = world.instances(0x01F5)
    render_map(0x01F5, cells, insts, links, world, 'aerfalle.png')

open_dat() reads both dat generations: the 2005-through-retail container and
the original 1999-2005 one. For original-era dats there is no matching world
database -- use NullWorld() for geometry-only maps.

Or from the command line: python -m landblock --help
"""
from . import dat, geom, world, render, annotations   # submodules stay reachable
from .dat import Dat, OldDat, open_dat, Reader
from .geom import Geometry, read_environment, read_environment_old
from .world import World, NullWorld, coord_string, load_enums
from .render import render as render_map, compute_floors, overlap_fraction, classify
from .annotations import Annotations

__version__ = '1.2.2'
__all__ = ['dat', 'geom', 'world', 'render', 'annotations', 'Annotations',
           'Dat', 'OldDat', 'open_dat', 'Reader', 'Geometry',
           'read_environment', 'read_environment_old', 'World', 'NullWorld',
           'coord_string', 'load_enums', 'render_map', 'compute_floors',
           'overlap_fraction', 'classify']

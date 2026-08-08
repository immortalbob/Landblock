"""landblock -- dungeon maps for Asheron's Call, generated from game data.

    from landblock import Dat, Geometry, World, render_map

    geom = Geometry(Dat('client_cell_1.dat'), Dat('client_portal.dat'))
    world = World('/path/to/ACE-World/Database')
    cells = geom.load(0x01F5)
    insts, links = world.instances(0x01F5)
    render_map(0x01F5, cells, insts, links, world, 'aerfalle.png')

Or from the command line: python -m landblock --help
"""
from . import dat, geom, world, render          # submodules stay reachable
from .dat import Dat, Reader
from .geom import Geometry, read_environment
from .world import World, coord_string, load_enums
from .render import render as render_map, compute_floors, overlap_fraction, classify

__version__ = '1.0.0'
__all__ = ['dat', 'geom', 'world', 'render',
           'Dat', 'Reader', 'Geometry', 'read_environment', 'World',
           'coord_string', 'load_enums', 'render_map', 'compute_floors',
           'overlap_fraction', 'classify']

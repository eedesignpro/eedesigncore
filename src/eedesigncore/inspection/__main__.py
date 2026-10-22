"""Inspect a netbom or native KiCad project from the command line."""

from eedesigncore.inspection.native import open_inspection
from eedesigncore.inspection.portable import _cli

if __name__ == "__main__":
    _cli(open_inspection)

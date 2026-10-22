"""The single version authority for this package.

Hatchling reads the DISTRIBUTION version from this file
(``pyproject.toml`` ``[tool.hatch.version]``), and ``__init__`` re-exports the
same string as ``eedesigncore.__version__``. One file feeds both, so the
installed metadata and the imported module cannot disagree.

The authority lives INSIDE the package on purpose. It used to be a ``VERSION``
file at the repository root, which the sdist ships and the wheel does not, so an
import-time read of it worked in a checkout and failed in a wheel install. That
is the fault UF-012 reported: a version that does not travel with the code.

The release manager edits this file. Nothing else writes it.
"""

__version__ = "0.38.0"

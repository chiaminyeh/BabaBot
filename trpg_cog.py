"""Compatibility entry point.

The TRPG implementation now lives in the `trpg/` package (see trpg/cog.py).
This thin shim keeps `load_extension("trpg_cog")` working: discord.py imports
this module and calls its `setup`, which we re-export from trpg.cog.
"""

from trpg.cog import setup, TRPGCog  # noqa: F401

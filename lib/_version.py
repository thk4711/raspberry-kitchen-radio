"""Single source of truth for the Raspberry Kitchen Radio version.

The version string is defined here and re-exported from ``lib/__init__.py`` so
it can be imported as ``from lib import __version__`` (tests / tooling) or, at
runtime where ``lib/`` is on ``sys.path``, as ``from _version import
__version__``. It is surfaced on the boot splash subtitle so a flashed image
advertises which build it is running, and it backs the ``--version`` flag of
``radio.py``.

Keep this in lockstep with the top ``CHANGELOG.md`` entry and the git tag when
cutting a release (see ``CHANGELOG.md`` for the release checklist).
"""

__version__ = "0.1.0"

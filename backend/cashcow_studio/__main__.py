"""``python -m cashcow_studio`` opens the app (same as ``ccs open``).

Extra arguments are passed to the ``ccs`` command line, so
``python -m cashcow_studio doctor`` works too.
"""

from __future__ import annotations

import sys

from .cli import app

if __name__ == "__main__":
    app(sys.argv[1:] or ["open"])

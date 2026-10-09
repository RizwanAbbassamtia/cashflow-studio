"""``python -m cashflow_studio`` opens the app (same as ``cfs open``).

Extra arguments are passed to the ``cfs`` command line, so
``python -m cashflow_studio doctor`` works too.
"""

from __future__ import annotations

import sys

from .cli import app

if __name__ == "__main__":
    app(sys.argv[1:] or ["open"])

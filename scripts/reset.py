#!/usr/bin/env python
"""Dev helper: remove the local ~/.xorcise state dir (delegates to home.purge_home)."""

from __future__ import annotations

from xorcise.core.home import purge_home, xorcise_home

if __name__ == "__main__":
    home = xorcise_home()
    existed = home.exists()
    purge_home(home)
    print(f"removed {home}" if existed else "nothing to remove")

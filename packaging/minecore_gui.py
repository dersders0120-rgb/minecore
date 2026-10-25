"""PyInstaller entry point for the MineCore workbench.

``MineCore.exe --self-test REPORT`` runs the headless installation self-test
instead of the window and exits with 0 when every step passes.
"""

import sys

if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
    from minecore.selftest import run

    raise SystemExit(run(sys.argv[2] if len(sys.argv) > 2 else "minecore-selftest.txt"))

from minecore.gui.app import main

raise SystemExit(main())

"""PyInstaller entry point for the MineCore workbench.

``MineCore.exe --self-test REPORT`` runs the headless installation self-test
instead of the window and exits with 0 when every step passes.
"""

import os
import sys

# A windowed (no console) executable has no standard streams; library code
# that writes warnings to them would raise AttributeError.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115, PTH123

if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
    from minecore.selftest import run

    raise SystemExit(run(sys.argv[2] if len(sys.argv) > 2 else "minecore-selftest.txt"))

from minecore.gui.app import main  # noqa: E402 - after the stream fix-up

raise SystemExit(main())

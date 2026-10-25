# Session handoff

Updated: 2026-10-25 ~02:30 UTC (architect, thread 2 "Build block model and estimation")

## Owner direction (binding)
- Windows 11 only. Wants a usable program fast, accepts some bugs, tests the GUI
  himself. Reply in Turkish, short, plain. He is not a GitHub user.
- Claude may merge PRs itself when CI is green (owner, 2026-10-25).
- Overnight 2026-10-25: work without asking until 09:55 TR (06:55 UTC), or 11:55
  TR if he does not answer; skip blockers and report them.
- Target depth: Datamine (Studio RM + NPVS + OP) as one integrated program.
  Next after pit work: solids/wireframes, domains, design, scheduling.
- Open-source libraries welcome if permissive (MIT/BSD/Apache); no GPL.

## State
- `main`: PRs #1-#8 merged (foundation, contracts, drillholes, first GUI).
- PR #9 (branch `claude/project-thread-3hc3k9`): estimation engine + GUI,
  grade-tonnage, pit optimisation (Rust max closure), shells/pushbacks/schedule,
  DTM surfaces + rock/air, swath + cross-validation, Rust estimation kernels
  (OK ~143k blocks/s on 4 cores), CLI for the whole chain. 322 pytest pass.
- Workbench menus: Proje, Sondaj, Blok Modeli, Yüzey, Açık Ocak, Yardım.
- Windows exe: GitHub Actions "Windows app" workflow, artifact `MineCore-windows`.

## Where things live
- Rust: `mc-core::pitopt` (max closure), `mc-core::kriging` (NN/IDW/OK kernels).
- Python: `minecore.estimation`, `minecore.blockmodel` (+ `workflow`,
  `validation`), `minecore.pitopt`, `minecore.surfaces`, `minecore.cli_modelling`.
- GUI: `gui/app.py` (main window), `gui/resource.py`, `gui/pit.py`.
- Limits: `docs/KNOWN_LIMITATIONS.md` (keep it honest).

## Next actions
1. Merge PR #9 when CI is green; post the Windows artifact link to the owner.
2. Solids: closed triangulated wireframes (import OBJ/STL/DXF 3DFACE), point-in-
   solid block flagging, domain-restricted estimation.
3. Pit design basics: shell to surface/contours, ramps later.
4. Scheduling: bench lag, plant capacity, stockpile.
5. Rotated block models and sub-cells.

## Owner owes
- Try the Windows build with real data and report problems.

# Session handoff

Updated: 2026-10-25 ~02:50 UTC (architect, thread 2 "Build block model and estimation")

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
- `main`: PRs #1-#11 merged (foundation, drillholes, estimation, pit
  optimisation, solids/surfaces, constrained schedule, Excel reports).
- PR #12 (branch `claude/project-thread-3hc3k9`): pit design outputs (bench
  crest/toe loops, pit solid, mined-out surface, DXF R12 export), minimum
  mining width for pushbacks, mining recovery/dilution, NSR value mode,
  section view (N-S/E-W/plan), confidence categories (not JORC), filtered
  grade-tonnage, block model calculator. 358 pytest pass locally.
- Workbench menus: Proje, Sondaj, Blok Modeli, Yüzey, Açık Ocak, Yardım.
- Windows exe: GitHub Actions "Windows app" workflow, artifact `MineCore-windows`.

## Where things live
- Rust: `mc-core::pitopt` (max closure), `mc-core::kriging` (NN/IDW/OK kernels).
- Python: `minecore.estimation`, `minecore.blockmodel` (+ `workflow`,
  `validation`, `classify`, `calc`), `minecore.pitopt` (+ `scheduling`,
  `design`, `width`), `minecore.surfaces`, `minecore.solids`, `minecore.io`
  (`excel`, `dxf`), `minecore.cli_modelling`.
- GUI: `gui/app.py` (main window, section view), `gui/resource.py`, `gui/pit.py`.
- Limits: `docs/KNOWN_LIMITATIONS.md` (keep it honest).

## Next actions
1. Merge PR #12 when CI is green; post the Windows artifact link to the owner.
2. Rotated block models and sub-cells (contract change in mc-core: needs an ADR).
3. Pit design: ramps, berms, batter angles from shells.
4. Cut-off optimisation (Lane) and multiple process routes.
5. Underground: stope shapes (later milestone).

## Owner owes
- Try the Windows build with real data and report problems.

# Session handoff

Updated: 2026-10-25 ~05:00 UTC (architect, thread "Build block model and estimation", overnight run)

## Owner direction (binding)
- Windows 11 only. Wants a usable program fast, accepts some bugs, tests the GUI
  himself. Reply in Turkish, short, plain. He is not a GitHub user.
- Claude may merge PRs itself when CI is green (owner, 2026-10-25).
- Overnight 2026-10-25: work without asking until 09:55 TR (06:55 UTC), or 11:55
  TR if he does not answer; skip blockers and report them.
- Target depth: Datamine (Studio RM + NPVS + OP) as one integrated program.
- Open-source libraries welcome if permissive (MIT/BSD/Apache); no GPL.

## State
- `main`: PRs #1-#23 merged (PR #21-#22: process routes in pit optimisation
  and per-route plant capacity in the scheduler; PR #23: ore drives).
- Overnight additions (PR #15-#20): string import and section linking, user
  guide (F1), re-blocking, Lane cut-off optimisation, partial-block solid
  fractions and weighted reports, solid CLI, CLI swath, spiral haul ramp in
  bench design, implicit RBF domains, section/plan PDF/PNG/SVG plots,
  end-of-period surfaces, decline and level crosscuts, self-test,
  geotechnical sectors in bench design, process routes (mill + leach).
- Bugs fixed overnight: saving a surface/solid/string set under an existing
  name failed (second bench design run crashed); plan plots of tall models.
- Windows build: "Windows app" workflow, artifact `MineCore-windows`; from
  PR #20 on it runs `MineCore.exe --self-test` (whole chain on the sample).
  Its first run found three packaging bugs that earlier exe builds had:
  pyvista steps crashed (half-bundled mypy), the pit optimisation warning
  crashed (no stderr in a windowed exe), PDF/SVG plots failed (matplotlib
  backends not bundled). All fixed; the self-test passes in CI.
- Local gates: run the scratch `gate.sh` equivalent (ruff check, ruff format
  --check, mypy, cargo fmt) and check for the text GATES-OK; piping through
  `tail` hides the exit status.

## Where things live
- Rust: `mc-core::pitopt` (max closure), `mc-core::kriging`.
- Python: `minecore.estimation`, `minecore.blockmodel` (`workflow`,
  `validation`, `classify`, `calc`, `importer`, `reblock`), `minecore.pitopt`
  (`scheduling`, `design`, `width`, `benchdesign`, `cutoff`),
  `minecore.underground` (`stopes`, `development`), `minecore.geology.implicit`,
  `minecore.surfaces`, `minecore.solids`, `minecore.strings`, `minecore.plots`,
  `minecore.selftest`, `minecore.io` (`excel`, `dxf`), `minecore.cli_modelling`.
- GUI: `gui/app.py` (main window, section view, print), `gui/resource.py`,
  `gui/pit.py`, `gui/underground.py`, `gui/geology.py`, `gui/guide.py`.
- Limits: `docs/KNOWN_LIMITATIONS.md` (kept current).

## Next actions
1. Merge PR #20 when CI (incl. the packaged self-test) is green.
2. Rotated block models and sub-cells (contract change in mc-core: needs an ADR).
3. Pit design: switchback ramps, multiple pit bottoms, blended sector changes.
4. Routes and stockpiles in Lane; re-routing ore when a route is full.
5. Underground: stope shapes beyond grid boxes, footwall drives and drawpoints,
   development schedule.
6. Interoperability: OMF export (check licence of the `omf` package first).

## Owner owes
- Try the Windows build with real data and report problems (Yardım → Kurulumu
  sına checks an installation in one click).

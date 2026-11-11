# Session handoff

Updated: 2026-11-11 (architect, thread "Prepare MineCore for safe pause").
Resume file for a fresh session: `docs/RESUME_PROMPT.md`.

## Owner direction (binding)
- Windows 11 only. Wants a usable program fast, accepts some bugs, tests the GUI
  himself. Reply in Turkish, short, plain. He is not a GitHub user.
- Claude may merge PRs itself when CI is green (owner, 2026-10-25).
- Overnight 2026-10-25: work without asking until 09:55 TR (06:55 UTC), or 11:55
  TR if he does not answer; skip blockers and report them.
- Target depth: Datamine (Studio RM + NPVS + OP) as one integrated program.
- Open-source libraries welcome if permissive (MIT/BSD/Apache); no GPL.

## State
- 2026-11-11 pause check: `main` = `d7e62fc` (PR #43) holds all work; CI and
  the Windows app build are green on it. Leftover `claude/project-thread-*`
  branches were squash-merged (#26, #37, #43) and hold nothing new. Open PRs
  are Dependabot version bumps only (#50-#57). `docs/RESUME_PROMPT.md` added.
- 2026-11-03: relicensed under MIT and the repository made public
  (ADR-0010); PyPI release workflow (Trusted Publishing, tag `v0.1.0`);
  contributor guide, code of conduct, security policy; CI hardening
  (read-only token, RustSec advisories, Dependabot); #43 OMF export/import
  and haul road network distances feed the fleet; Windows artifacts kept
  7 days.
- 2026-10-25 after #38: #39 fleet haulage cost in the project cash flow and
  a monthly period preset; #40 greedy schedule keeps blending targets; #41
  underground development schedule (decline/level timing, stopes wait for
  ready levels); #42 haul road network shortest routes and travel times.
- Thread "Match Datamine Studio OP and NPVS" (2026-10-25): gap list vs Studio
  OP + NPVS from public knowledge (no inspection of the installed software;
  rule 7). PR #38 closes it: MILP blending targets; variable period lengths
  and per-period capacities (discounting, fixed cost, depreciation, IRR,
  payback in years); MCAF/PCAF cost factors; concentrate TC and penalties;
  bench reserves between surfaces and cut/fill volume (`minecore.reserves`);
  plan string editor (`gui/strings_editor.py`); pit design from toe strings
  (`benchdesign.design_from_toes`, `pit design-toes`); grade control dig
  lines (`minecore.gradecontrol`, CLI `gc`); dump sequencing by haul distance
  feeding the fleet; solid verify/repair/voxel booleans/cut
  (`minecore.solidtools`); connector ramps from side pit bottoms, road edge
  and safety bund strings. Owner asked (14:25 UTC) to delegate simple work
  to Haiku subagents and verify it.
- Same thread, later (2026-10-25): #34 pushbacks chosen by NPV
  (`pitopt/phases.py`); #35 one ramp per separate pit, pit bottoms counted,
  Lane never below the best constant cut-off (hypothesis counterexample
  fixed); #36 pit scenarios (save/compare/reload), underground stope
  economics + schedule + cash flow, truck/loader fleet by period, drill and
  blast design with hole layout, waste dump design, Excel fleet/scenario
  sheets; #37 mineable inventory by pushback and confidence category
  ("Envanter (kategori)" tab). From 11:16 UTC the owner asked for speed over
  tests: #36-#37 features have smoke tests only (listed in
  KNOWN_LIMITATIONS). The owner ran the Windows build of #36 and said every
  feature worked (11:53 UTC).
- Thread "Continue MineCore development" (2026-10-25): PR #27 rotated block
  models (ADR-0009); #28 switchback ramps and technical audit v1
  (`docs/audit/AUDIT_2026-10.md`, known-answer tests A-H, pushback ore-target
  fix); #29 payables, royalty and project cash flow (capital, tax with
  depreciation and loss carry-forward, closure, IRR, payback); #30
  co-products/by-products; #31 stockpile grade bins, capacity, rehandling;
  #32 sensitivity analysis (spider table/chart) and exact Lane balancing
  points; #33 NPV-maximising MILP schedule (HiGHS via scipy, never below the
  greedy schedule). Owner said not to post Windows build links for now.
- `main`: PRs #1-#26 merged (PR #21-#22: process routes in pit optimisation
  and per-route plant capacity in the scheduler; PR #23: ore drives; PR #24:
  self-test covers routes, Lane cut-off, sectors and the decline; PR #26:
  fixes from the owner's first hands-on Windows test, see below).
- Owner's first hands-on test (2026-10-25 morning) and PR #26 fixes: the
  new-project save dialog looked like it wanted an existing file (now folder,
  then name; open accepts the containing folder); about 5 s "not responding" on
  the first import (libraries now warmed up at start, wait cursor); no app
  icon (`tools/make_icon.py` draws it; exe, window and taskbar); tall dialogs
  (variogram, pit optimisation) did not fit the screen (`gui/layout.py`
  scroll panels and `fit_to_screen`); a second block model with the default
  name crashed (free default name, clear message); user messages showed a
  traceback (plain `RuntimeError` now shows as a warning); the validation
  report mixed English and showed 150 rows for about 10 causes (`gui/issues_tr.py`:
  Turkish wording, one row per interval that overlaps many others).
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
  `gui/pit.py`, `gui/underground.py`, `gui/geology.py`, `gui/guide.py`,
  `gui/layout.py` (scroll panels), `gui/issues_tr.py` (report wording), `gui/assets/` (icon).
- Limits: `docs/KNOWN_LIMITATIONS.md` (kept current).

## Next actions
1. Owner feedback on the Windows build with real data comes first.
2. Deeper tests for the smoke-tested features of #36-#37 (scenarios, stope
   economics, fleet, drill and blast, dumps, category inventory).
3. Sub-cells (needs its own ADR).
4. Routes and stockpiles in Lane; short-term (monthly shot) scheduling;
   blending in the greedy schedule.
5. Underground: stope shapes beyond grid boxes, footwall drives and
   drawpoints (development schedule done in #41).
6. Dump ramps (haulage cost and road network done in #39, #42).
7. Interoperability: further open formats (OMF done in #43); M12 installer
   and PyPI release.
8. Dependabot PRs #50-#57: merge when CI is green.

## Owner owes
- Try the Windows build with real data and report problems (Yardım → Kurulumu
  sına checks an installation in one click).

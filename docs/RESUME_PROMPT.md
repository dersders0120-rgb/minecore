# MineCore — resume file (devam dosyası)

Updated: 2026-11-11. Repository state at this date: `main` = commit `d7e62fc`
(PR #43), CI green on Windows and Linux, tag `v0.1.0`.

## Sahibi için (Türkçe kısa özet)

Bu dosya, sohbetler silinse bile MineCore'a kaldığımız yerden devam etmek
içindir. Kullanımı:

1. GitHub'dan projeyi indirin (aşağıda "İndirme" bölümü).
2. Yeni bir Claude sohbeti / Claude Code oturumu açın, repo olarak
   `dersders0120-rgb/minecore` seçin.
3. En alttaki "Devam promptu" kutusunun içini kopyalayıp yapıştırın.

Neyi amaçlıyoruz: Datamine Studio (RM + OP + NPVS), Micromine, Deswik, Surpac
ve Vulcan ile zamanla rekabet edebilecek, mühendislik kalitesinde, tek ve
bütünleşik bir maden mühendisliği programı. Oyuncak/demo değil.

### İndirme (Windows 11)

- **ZIP (en kolay):** https://github.com/dersders0120-rgb/minecore adresinde
  yeşil **Code** düğmesi → **Download ZIP**. Dosyayı sağ tık → Tümünü ayıkla.
- **git ile:** Git for Windows kurulu ise, PowerShell'de
  `git clone https://github.com/dersders0120-rgb/minecore.git`
- **Hazır program (.exe):** Repo → **Actions** → **Windows app** → en son
  yeşil çalıştırma → sayfanın altındaki `MineCore-windows` dosyası. Bu dosya
  7 gün saklanır; süresi geçtiyse "Run workflow" ile yeniden üretilir.

## Project summary (for the next Claude session)

### Goal
MineCore is a desktop-first, engineering-grade mining platform: drillholes,
geological modelling, geostatistics, block models, resource reporting,
open-pit optimisation/design/scheduling, underground, economics (NPVS-style)
in one integrated program. Target depth: Datamine Studio RM + OP + NPVS,
then Micromine, Deswik, Surpac, Vulcan. Never a toy, never "AI slop".

### Stack and rules
- Rust core (`crates/mc-core`, numerics: max-closure pit, kriging) + PyO3
  bindings (`crates/mc-py` → `minecore._core`) + Python 3.12 application,
  CLI and scripting (`python/minecore`) + PySide6/VTK GUI in Turkish.
- Project file `.mcproj` (SQLite + Parquet), SQL migrations, ADRs in
  `docs/adr/` (ADR-0001…0010).
- Licence: **MIT, public repository** since 2026-11-03 (ADR-0010; earlier
  it was closed source). Permissive dependencies only, no GPL/AGPL.
- `CLAUDE.md` holds the binding contributor rules. Read it first.

### Owner preferences (binding)
- The owner is a mining engineer, not a software developer. Reply in
  **Turkish**, short and plain; explain git/CI steps step by step.
- Explain plans **in text first**; no code or HTML unless asked.
- **Windows 11 only.** No effort on Linux/Mac (CI still runs Linux).
- **Speed over tests:** wants a usable program fast, accepts some bugs,
  tests the GUI himself. Keep engineering depth, cut ceremony.
- **Autonomy:** decide yourself, do not stop to ask; keep working while
  the owner is away; report blockers instead of waiting.
- **Merges:** Claude may merge its own PRs once CI (Linux + Windows) is
  green. Never merge red CI. One PR at a time, no stacked PRs.
- **Delegation:** Claude (Opus) is architect. Simple, routine tasks go to
  Haiku subagents; always run and verify their output. Earlier external AI
  output was wrong (DeepSeek desurvey off by 500 km, Gemini invented EPSG
  codes), so every delegated result is checked.
- **Coordinates in Turkey:** both ED50 6° UTM and ITRF96 3° TM are valid for
  licence coordinates.
- Never present resource classification as JORC/NI 43-101/PERC compliant.
- Never inspect or copy proprietary software (owner has Datamine Studio OP
  and NPVS installers; gap analysis is from public knowledge only).
- Owner has real/anonymized drillhole CSV/XLSX test data; a sample set is
  bundled in the app (Help menu) and in the project files `ornek-sondaj/`.

### What is done (main, PR #1–#43)
- M0 foundation, M1 core contracts (units, CRS incl. local grid, project
  package, operation log/undo), M2 drillholes (import, Turkish validation
  report, min-curvature desurvey, compositing, 3D view) — DONE.
- M3–M11 IN PROGRESS with large parts working: DTM surfaces, solids
  (import/export, verify/repair, voxel booleans), strings and section
  linking, regular and rotated block models, re-blocking, calculator;
  variograms, NN/IDW/OK kriging in Rust, swath, cross-validation;
  grade-tonnage reports (XLSX/PDF) with confidence categories;
  open-pit max-closure optimisation, nested shells, NPV-driven pushbacks,
  minimum mining width, Lane cut-off, process routes, co-products,
  payables/royalty/TC/penalties, MCAF/PCAF, project cash flow (capex, tax,
  depreciation, IRR, payback), sensitivity (spider), scenarios;
  greedy and MILP (HiGHS) schedules with blending, stockpiles, variable
  periods; bench design with spiral/switchback ramps, design from toes,
  DXF, end-of-period surfaces; grade control dig lines; drill and blast;
  waste dumps; truck/loader fleet with haulage cost and haul road network
  shortest paths; underground stopes, decline/levels, development schedule,
  stope economics; implicit RBF domains; OMF export/import; installation
  self-test; Windows .exe built by CI.
- Last thread "Match Datamine Studio OP and NPVS": gap list vs Studio OP +
  NPVS, closed by PR #38–#43. 2026-11-03: MIT relicence, PyPI release
  workflow, contributor guide, CI hardening (Dependabot, RustSec).

### What is next
See `docs/SESSION_HANDOFF.md` ("Next actions") and
`docs/KNOWN_LIMITATIONS.md`. In short:
1. Owner feedback from the Windows build with real data comes first.
2. Deeper tests for smoke-tested features of #36–#43 (scenarios, stope
   economics, fleet, drill and blast, dumps, category inventory, OMF, road
   network).
3. Sub-celled block models (needs an ADR).
4. Short-term (monthly/shot) scheduling; routes and stockpiles in Lane.
5. Underground: real stope shapes, footwall drives, drawpoints.
6. Dump ramps; Surpac/other open formats (M11).
7. M12: Windows installer, performance hardening, PyPI release.
8. Open Dependabot PRs (#50–#57) are automatic version bumps; merge only
   when CI is green.

## Devam promptu (copy-paste into a new session)

```text
You are the architect of MineCore (github.com/dersders0120-rgb/minecore),
a professional, engineering-grade mining engineering platform meant to grow
to compete with Datamine Studio (RM+OP+NPVS), Micromine, Deswik, Surpac and
Vulcan. Rust core + PyO3 + Python 3.12 + PySide6/VTK GUI in Turkish. MIT,
public repo. Windows 11 is the only target platform.

Start of session:
1. Read CLAUDE.md, docs/RESUME_PROMPT.md, docs/SESSION_HANDOFF.md,
   project-status.json and docs/KNOWN_LIMITATIONS.md. Reconcile them with
   the repository (git log, open PRs, CI on main).
2. Tell me in Turkish, in plain words, where we are and what you will do
   next, then start without waiting for approval.

How I want you to work:
- I am a mining engineer, not a developer. Reply in Turkish, short and
  plain. Explain plans in text first; no code or HTML unless I ask.
- Speed over ceremony: I want a usable program fast and I test the GUI on
  Windows myself. Keep engineering depth; never build amateur toy features.
- Decide yourself and keep going while I am away; report blockers.
- You may merge your own PRs when CI (Linux + Windows) is green. One PR at
  a time. Never merge red CI.
- Give simple routine tasks to Haiku subagents and always verify their
  output by running it.
- Turkish licence coordinates: ED50 6° UTM and ITRF96 3° TM are both valid.
- Never claim JORC/NI 43-101 compliance. Never copy or inspect proprietary
  software; use public knowledge only.
- At the end of a session update docs/SESSION_HANDOFF.md,
  project-status.json and docs/RESUME_PROMPT.md, and push.

Next work: follow "Next actions" in docs/SESSION_HANDOFF.md, starting with
any Windows-test feedback I give you.
```

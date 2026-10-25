# MineCore roadmap

Milestones are gates, not dates. A milestone closes only when its exit evidence
has been produced and recorded in `project-status.json`. Dates are estimated
after M1, from measured throughput.

| ID | Milestone | Scope | Exit evidence |
| --- | --- | --- | --- |
| M0 | Foundation | Repository, Rust + Python skeleton, CLI, CI (Windows + Linux), licence policy, governance files, verification script | All gates green in CI; `minecore info` reports the Rust core from Python |
| M1 | Core contracts | Units and quantities, CRS handling, project package with versioning and migration, operation log, undo/redo model, error hierarchy, job/cancellation model | Create, save, reopen and migrate tests pass; unit conversion property tests; CRS round-trip tests against PROJ |
| M2 | Drillhole data | Collar/survey/assay/lithology import with schema mapping, validation report, minimum-curvature desurvey, compositing (length, domain, bench) | Hand-calculated desurvey cases; overlap/gap detection; Turkish locale CSV tests; owner's real dataset imports cleanly |
| M3 | Geometry and 3D | Triangle mesh validation and repair, robust (exact-predicate) mesh booleans, point-in-solid, plane sections, volumes, spatial index, 3D viewer for traces and meshes | Cube/sphere volumes match analytic values; viewer smoke test |
| M4 | Block model | One engine for regular, sub-celled (flexible and octree) and rotated models, typed attributes, domain flagging from solids, chunked storage, regularisation to SMU | Volume conservation; query and storage round-trip tests; 10M-block benchmark |
| M5 | Estimation | NN, IDW, experimental variograms, model fitting, ordinary kriging, search ellipsoids, validation (swath plots, global bias) | Agreement with an independent reference within recorded tolerance; weights sum to 1; exact reproduction at sample locations |
| M6 | Resource reporting — **alpha** | Cut-off scenarios, grade-tonnage curves, configurable classification, reproducible XLSX/PDF reports | End-to-end sample project reproduces identical results from a clean run |
| M7 | Open-pit optimisation | Economic block values, SMU regularisation gate, ultimate pit by max-closure (pseudoflow), minimum mining width, nested shells | Matches brute force on small cases; benchmark on a 1M-block model |
| M8 | Open-pit design and scheduling | Benches, berms, ramps, slope design; phases; production scheduling under capacity constraints | Design validation tests; schedule feasibility and precedence tests |
| M9 | Underground | Development and level layouts, stope design, method-specific constraints, development scheduling | Geometric clash tests; method constraint tests |
| M10 | Advanced geology | Implicit modelling (RBF), faults and domain surfaces, section interpretation | Recovery of known surfaces from synthetic data |
| M11 | Interoperability and automation | OMF, DXF, Surpac formats, batch processing, public Python API | Round-trip tests per format |
| M12 | Commercial readiness | Windows installer, licensing, updates, performance hardening, Turkish/English UI | Clean-machine install test; published benchmark report |

Later estimation work (from research T-002): change of support (MIK, uniform conditioning)
and conditional-simulation ensembles carried into design and scheduling.

Extension domains (geotechnics, ventilation, processing, mine economics, grade
control, drill and blast) get interfaces after M8 and are scoped against real
engineering requirements, never as data viewers presented as simulators.

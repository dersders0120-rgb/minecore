# Known limitations

What MineCore cannot do yet, and what each gap depends on. Updated every session.

| Area | Limitation | Depends on |
| --- | --- | --- |
| Whole product | Only the units contract is implemented; no data import, modelling or estimation yet. | M1 onward |
| Units | No affine units (degrees Celsius), no currency dimension, no years (ambiguous). | ADR when economics or thermal data arrive |
| Licences (Python) | Python runtime dependencies are tracked by hand in `docs/DEPENDENCIES.md`; only Rust is checked automatically (`cargo-deny`). | Packaging work (M12) or more Python dependencies |
| i18n | CLI messages are English only. Turkish/English UI strings arrive with the workbench. | Workbench milestone |
| Packaging | No Windows installer or signed build yet. | M12 |

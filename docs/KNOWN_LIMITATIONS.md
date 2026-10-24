# Known limitations

What MineCore cannot do yet, and what each gap depends on. Updated every session.

| Area | Limitation | Depends on |
| --- | --- | --- |
| Whole product | No engineering capability is implemented yet; M0 delivers only the build, test and governance foundation. | M1 onward |
| Licences (Python) | Only Rust dependencies are licence-checked automatically (`cargo-deny`). Python runtime dependencies will get an automated check when the first one is added. | First runtime Python dependency |
| i18n | CLI messages are English only. Turkish/English UI strings arrive with the workbench. | Workbench milestone |
| Packaging | No Windows installer or signed build yet. | M12 |

# Dependency licence register

Every dependency that ships with MineCore is listed here with its licence and
the date it was approved. Rust dependencies are also enforced by `deny.toml`
in CI. Development-only tools are listed separately; they are not distributed.

## Shipped (runtime)

| Dependency | Side | Licence | Purpose | Approved |
| --- | --- | --- | --- | --- |
| pyo3 | Rust | MIT OR Apache-2.0 | Python bindings | 2026-10-24 |
| thiserror | Rust | MIT OR Apache-2.0 | Error types | 2026-10-24 |
| numpy | Python | BSD-3-Clause | Array computing | 2026-10-24 |

## Development only

| Tool | Licence | Purpose |
| --- | --- | --- |
| maturin | MIT OR Apache-2.0 | Build backend |
| proptest | MIT OR Apache-2.0 | Rust property tests |
| pytest, hypothesis | MIT, MPL-2.0 | Python tests |
| ruff | MIT | Lint and format |
| mypy | MIT | Type checking |
| cargo-deny | MIT OR Apache-2.0 | Licence policy |

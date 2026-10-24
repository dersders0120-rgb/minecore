# ADR-0004: Project package persistence

- Status: accepted 2026-10-24 (T-005)
- Deciders: architect

## Context

A MineCore project holds a small amount of structured metadata (identity,
settings, object catalogue, history) and large bulk data (drillhole tables,
block models, meshes). Mining companies often keep projects on network file
shares, several engineers may try to open the same project, and projects must
survive crashes and software upgrades.

## Decision

1. **Directory package** `Name.mcproj/` with `project.sqlite`, `project.lock`,
   `tables/`, `blocks/`, `geometry/` and `backups/`.
2. **Persistence is implemented in Python** (`minecore.project`) with the
   standard `sqlite3` module. Rust kernels stay pure functions on arrays and
   never do file I/O; this keeps them testable and reusable. The schema itself
   is language-neutral: numbered SQL files in `minecore/project/schema/`.
3. **Identity and version:** SQLite `application_id = 0x4D434F52` ("MCOR")
   identifies MineCore databases; `user_version` is the schema version.
4. **Migrations:** migration *N* moves a project from version *N-1* to *N*. Each
   runs in one transaction with its version bump and a `schema_migrations`
   history row; a failure rolls back completely. Before migrating, the
   database is copied to `backups/` with SQLite's online backup API. Applied
   migration files are never edited.
5. **Compatibility:** a project newer than the running MineCore is refused
   without being modified. Read-only opening never migrates.
6. **Single writer:** an operating-system lock on `project.lock` (flock on
   POSIX, `msvcrt.locking` on Windows) is held while a project is open for
   writing. A crashed process releases it automatically. Read-only opening
   needs no lock.
7. **Durability:** rollback journal (`journal_mode=DELETE`), not WAL, because
   WAL is unsafe on network file systems; `synchronous=FULL`; foreign keys on;
   STRICT tables.
8. **Object catalogue:** every project object has a UUID, a `lower_snake_case`
   kind, a name unique per kind, an optional parent, an optional storage path
   relative to the package (validated against escaping it) and JSON metadata.

## Alternatives considered

- *Single-file project (one SQLite file with blobs):* simple to copy, but
  multi-gigabyte block models in blobs are slow and bloat backups. Rejected.
- *Persistence in Rust (rusqlite, arrow-rs):* one implementation for every
  client, but slower to evolve and duplicates pyarrow. Revisit if a non-Python
  client (e.g. a server) needs to read projects.
- *WAL mode:* faster concurrent reads, but corrupts on network shares. Rejected.

## Consequences

- Bulk files under `tables/`, `blocks/` and `geometry/` need their own atomic
  write rule (write to a temporary name, then rename), defined with the first
  module that writes them (M2).
- Copying a project means copying the directory; a single-file export/archive
  format is future work.

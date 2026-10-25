# ADR-0007: Operation log, undo/redo, jobs and errors

- Status: accepted 2026-10-25 (T-006, T-007)
- Deciders: architect

## Decision
1. **Operations.** Every change to a tracked table (`settings`, `objects`) runs
   inside an operation, one SQLite transaction. Writes outside an operation are
   refused by triggers. Each operation is logged (`operations`) with its kind,
   description, parameters, user, host, MineCore version and time.
2. **Generic undo.** Triggers (migration 0002) store before/after row images
   in `operation_changes`. Undo applies the before images in reverse order,
   redo the after images in order; history is linear and a new operation
   discards undone ones. No per-command inverse code exists to get wrong.
3. **Bulk files are immutable.** A module that writes under `tables/`,
   `blocks/` or `geometry/` writes a new file (temporary name, then rename)
   and points the catalogue row at it; it never overwrites a referenced file.
   Restoring a row therefore restores the data. Unreferenced files are removed
   by a garbage-collection step (with the first bulk writer, M2).
4. **New tracked tables or columns** need their triggers (re)created in the
   same migration and `history.TRACKED` updated; a test compares them.
5. **Jobs.** Long-running entry points take a `Progress` (throttled reports,
   nested spans) that is also the cancellation point; `CancelledError` inside
   an operation rolls everything back. Rust kernels get a cancellation flag
   when the first long kernel lands.
6. **Errors.** All intended errors derive from `MineCoreError`
   (`minecore.errors`). CLI exit codes: 0 ok, 1 error, 2 usage, 130 cancelled.

## Consequences
Undo is per project and survives reopen. Very large operations store row
images for catalogue rows only; bulk data never enters the log.

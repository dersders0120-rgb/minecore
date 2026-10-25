# ADR-0006: Array boundary between Python and the Rust core

- Status: accepted 2026-10-25 (T-004)

## Decision
Bulk numeric data crosses the Python/Rust boundary as NumPy float64 arrays via
`rust-numpy` (BSD-2-Clause). Bindings accept anything NumPy can convert,
copy the input once, run the kernel with the GIL released (`py.detach`) and
return new arrays; inputs are never mutated. Kernels in `mc-core` take plain
slices and know nothing about Python. Absent optional arrays are `None`, never
NaN sentinels.

## Consequences
One copy per call (acceptable: kernels are O(n) or worse). Zero-copy views can
be added later for read-only kernels if profiling demands it.

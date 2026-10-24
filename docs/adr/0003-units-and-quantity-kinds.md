# ADR-0003: Units of measure and quantity kinds

- Status: accepted 2026-10-24 (T-003)
- Deciders: architect

## Context

Mining data mixes metric and imperial lengths (m, ft, US survey ft), grade
units (%, ppm, g/t, troy oz per short ton), densities (t/m^3, g/cm^3) and
angles (degrees, gon). Silent unit errors produce wrong tonnages and grades,
which are commercially and legally serious. Every module must use one units
contract.

## Decision

1. **One contract in `mc-core::units`**, exposed to Python as
   `minecore.units`. No other module defines units or conversion factors.
2. **Base dimensions:** length, mass, time and plane angle. Angle is a base
   dimension so degrees never mix with plain numbers. Grades and ratios are
   dimensionless.
3. **Exact scales:** each unit's factor to coherent SI is a rational number
   times a power of pi (`Scale`). Composition and comparison are exact;
   rounding to `f64` happens once. Equivalent units (`ppm`, `g/t`, `mg/kg`)
   convert with factor exactly 1.0 and arrays are then copied, not scaled.
4. **Strict parsing:** unit text is parsed by a small grammar (`*`, `.`, `·`,
   `/`, `^n`, trailing digits, superscripts). Symbols are case-sensitive.
   Ambiguous symbols (`ton`, `oz`, `ppt`, years) are rejected with advice;
   nothing is guessed. Assay-file shorthands `gpt`, `opt`, `pct`, `ppm`, `ppb`
   are accepted case-insensitively as whole expressions.
5. **Quantity kinds** (`length`, `area`, `volume`, `mass`, `density`,
   `grade`, `ratio`, `angle`, `duration`) give an attribute its engineering
   meaning and fix its dimension. Grades have no default unit: the unit must
   be declared.
6. **Storage:** an attribute keeps the unit it was declared with (e.g. Au in
   g/t), so imported values are stored bit-for-bit. Kernels request the unit
   they need. Geometry is always stored in the project length unit (metres).

## Alternatives considered

- *Floating-point factor tables:* simple, but `t/m3 -> g/cm3` would not be
  exactly 1 and composed factors drift. Rejected.
- *Converting everything to SI on import:* loses the user's declared units and
  original values. Rejected.
- *A third-party units crate:* existing crates are type-level (compile-time
  units), unsuited to units read from files at runtime.

## Consequences

- Affine units (degrees Celsius) and currency are not supported yet; they need
  an ADR extending the base dimensions when economics or thermal data arrive.
- Unit text in project files is the canonical symbol from `Unit.symbol`.

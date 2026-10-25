//! The table of unit symbols MineCore recognises.
//!
//! Symbols are case-sensitive (SI distinguishes `mg` from `Mg`). Symbols that
//! are ambiguous in mining practice are rejected with a suggestion instead of
//! being guessed: `ton` (metric, short or long?), `oz` (troy or avoirdupois?),
//! `ppt` (per thousand or per trillion?).

use super::dimension::Dimension;
use super::scale::Scale;

/// A named unit atom that can appear in a unit expression.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Atom {
    /// Canonical symbol used when formatting.
    pub symbol: &'static str,
    /// Other accepted spellings (exact match).
    pub aliases: &'static [&'static str],
    /// Dimension of the atom.
    pub dimension: Dimension,
    /// Exact factor to the coherent SI unit of that dimension.
    pub scale: Scale,
}

const fn atom(
    symbol: &'static str,
    aliases: &'static [&'static str],
    dimension: Dimension,
    scale: Scale,
) -> Atom {
    Atom {
        symbol,
        aliases,
        dimension,
        scale,
    }
}

const POUND: Scale = Scale::ratio(45_359_237, 100_000_000);

/// All recognised atoms. SI coherent units have scale 1.
pub(crate) const ATOMS: &[Atom] = &[
    // Length (SI: metre)
    atom("m", &[], Dimension::LENGTH, Scale::ONE),
    atom("mm", &[], Dimension::LENGTH, Scale::ratio(1, 1000)),
    atom("cm", &[], Dimension::LENGTH, Scale::ratio(1, 100)),
    atom("km", &[], Dimension::LENGTH, Scale::ratio(1000, 1)),
    atom("in", &[], Dimension::LENGTH, Scale::ratio(254, 10_000)),
    atom("ft", &[], Dimension::LENGTH, Scale::ratio(3048, 10_000)),
    atom(
        "usft",
        &["ftUS"],
        Dimension::LENGTH,
        Scale::ratio(1200, 3937),
    ),
    // Area
    atom("ha", &[], Dimension::AREA, Scale::ratio(10_000, 1)),
    // Volume
    atom("L", &["l"], Dimension::VOLUME, Scale::ratio(1, 1000)),
    // Mass (SI: kilogram)
    atom("kg", &[], Dimension::MASS, Scale::ONE),
    atom("g", &[], Dimension::MASS, Scale::ratio(1, 1000)),
    atom("mg", &[], Dimension::MASS, Scale::ratio(1, 1_000_000)),
    atom("t", &["tonne"], Dimension::MASS, Scale::ratio(1000, 1)),
    atom("kt", &[], Dimension::MASS, Scale::ratio(1_000_000, 1)),
    atom("Mt", &[], Dimension::MASS, Scale::ratio(1_000_000_000, 1)),
    atom("lb", &[], Dimension::MASS, POUND),
    // short ton = 2000 lb, long ton = 2240 lb
    atom(
        "st",
        &["short_ton"],
        Dimension::MASS,
        Scale::ratio(90_718_474, 100_000),
    ),
    atom(
        "lt",
        &["long_ton"],
        Dimension::MASS,
        Scale::ratio(10_160_469_088, 10_000_000),
    ),
    // troy ounce = 31.1034768 g
    atom(
        "ozt",
        &["oz_troy"],
        Dimension::MASS,
        Scale::ratio(311_034_768, 10_000_000_000),
    ),
    // Time (SI: second)
    atom("s", &[], Dimension::TIME, Scale::ONE),
    atom("min", &[], Dimension::TIME, Scale::ratio(60, 1)),
    atom("h", &[], Dimension::TIME, Scale::ratio(3600, 1)),
    atom("d", &[], Dimension::TIME, Scale::ratio(86_400, 1)),
    // Plane angle (SI: radian)
    atom("rad", &[], Dimension::ANGLE, Scale::ONE),
    atom("deg", &["°"], Dimension::ANGLE, Scale::ratio_pi(1, 180, 1)),
    atom(
        "gon",
        &["grad"],
        Dimension::ANGLE,
        Scale::ratio_pi(1, 200, 1),
    ),
    // Dimensionless
    atom("frac", &["fraction"], Dimension::NONE, Scale::ONE),
    atom(
        "%",
        &["pct", "percent"],
        Dimension::NONE,
        Scale::ratio(1, 100),
    ),
    atom("ppm", &[], Dimension::NONE, Scale::ratio(1, 1_000_000)),
    atom("ppb", &[], Dimension::NONE, Scale::ratio(1, 1_000_000_000)),
];

/// Whole-expression shorthands common in assay files, expanded before parsing.
/// Matching is case-insensitive.
pub(crate) const SHORTHANDS: &[(&str, &str)] = &[
    ("gpt", "g/t"),
    ("opt", "ozt/st"),
    ("ppm", "ppm"),
    ("ppb", "ppb"),
    ("pct", "%"),
];

/// Ambiguous symbols and what to use instead.
pub(crate) const AMBIGUOUS: &[(&str, &str)] = &[
    (
        "ton",
        "use 't' (metric tonne), 'st' (short ton) or 'lt' (long ton)",
    ),
    (
        "tons",
        "use 't' (metric tonne), 'st' (short ton) or 'lt' (long ton)",
    ),
    (
        "oz",
        "use 'ozt' (troy ounce); avoirdupois ounces are not supported",
    ),
    ("ppt", "use 'ppm' or 'ppb'; 'ppt' is ambiguous"),
    ("yr", "years are ambiguous; use 'd'"),
    ("a", "years are ambiguous; use 'd'"),
];

/// Looks up an atom by canonical symbol or alias.
pub(crate) fn find_atom(symbol: &str) -> Option<&'static Atom> {
    ATOMS
        .iter()
        .find(|a| a.symbol == symbol || a.aliases.contains(&symbol))
}

/// Returns the expansion of a whole-expression shorthand, if any.
pub(crate) fn find_shorthand(input: &str) -> Option<&'static str> {
    SHORTHANDS
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(input))
        .map(|(_, v)| *v)
}

/// Returns the advice for an ambiguous symbol, if it is one.
pub(crate) fn ambiguity_hint(symbol: &str) -> Option<&'static str> {
    AMBIGUOUS
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(symbol))
        .map(|(_, v)| *v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn symbols_and_aliases_are_unique() {
        let mut seen = HashSet::new();
        for a in ATOMS {
            assert!(seen.insert(a.symbol), "duplicate symbol {}", a.symbol);
            for alias in a.aliases {
                assert!(seen.insert(alias), "duplicate alias {alias}");
            }
        }
    }

    #[test]
    fn ambiguous_symbols_are_not_atoms() {
        for (sym, _) in AMBIGUOUS {
            assert!(find_atom(sym).is_none(), "{sym} must not resolve");
        }
    }

    #[test]
    fn imperial_definitions_are_exact() {
        // 1 short ton = 2000 lb, 1 long ton = 2240 lb exactly.
        let lb = find_atom("lb").unwrap().scale;
        let st = find_atom("st").unwrap().scale;
        let lt = find_atom("lt").unwrap().scale;
        assert_eq!(st.checked_div(lb).unwrap(), Scale::ratio(2000, 1));
        assert_eq!(lt.checked_div(lb).unwrap(), Scale::ratio(2240, 1));
    }
}

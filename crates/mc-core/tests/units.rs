//! Contract tests for `mc_core::units`.
//!
//! Reference values are legal definitions (international foot, US survey foot,
//! avoirdupois pound, troy ounce) or exact arithmetic identities, not values
//! computed by the code under test.

#![allow(clippy::float_cmp)] // exact equality is the property under test

use mc_core::units::{Dimension, QuantityKind, Unit, UnitError};
use proptest::prelude::*;

fn u(s: &str) -> Unit {
    Unit::parse(s).unwrap_or_else(|e| panic!("{s}: {e}"))
}

fn exact(from: &str, to: &str) -> (i128, i128, i8) {
    let s = u(from).exact_factor_to(&u(to)).unwrap();
    (s.numerator(), s.denominator(), s.pi_exponent())
}

#[test]
fn legal_definitions_are_exact() {
    assert_eq!(exact("ft", "m"), (381, 1250, 0)); // 0.3048 m
    assert_eq!(exact("usft", "m"), (1200, 3937, 0));
    assert_eq!(exact("lb", "kg"), (45_359_237, 100_000_000, 0));
    assert_eq!(exact("in", "cm"), (127, 50, 0)); // 2.54 cm
    // 1 troy ounce per short ton = 480 grains / 14 000 000 grains = 240/7 ppm.
    assert_eq!(exact("ozt/st", "ppm"), (240, 7, 0));
    assert_eq!(exact("opt", "g/t"), (240, 7, 0));
    // 1 troy ounce per metric tonne = 31.1034768 g/t.
    assert_eq!(exact("ozt/t", "g/t"), (19_439_673, 625_000, 0));
}

#[test]
fn mining_identities_hold_exactly() {
    for (a, b) in [
        ("g/t", "ppm"),
        ("t/m3", "g/cm3"),
        ("kg/L", "t/m3"),
        ("mg/kg", "ppm"),
    ] {
        assert!(u(a).is_equivalent(&u(b)), "{a} == {b}");
        assert_eq!(u(a).factor_to(&u(b)).unwrap(), 1.0, "{a} -> {b}");
    }
    assert_eq!(exact("%", "ppm"), (10_000, 1, 0));
    assert_eq!(exact("ha", "m2"), (10_000, 1, 0));
    assert_eq!(exact("gon", "deg"), (9, 10, 0));
    assert_eq!(exact("deg", "rad"), (1, 180, 1));
    assert_eq!(exact("Mt", "t"), (1_000_000, 1, 0));
    assert_eq!(exact("d", "h"), (24, 1, 0));
}

#[test]
fn float_conversions_match_definitions() {
    let cases = [
        (1.0, "ft", "m", 0.3048),
        (100.0, "m", "ft", 328.083_989_501_312_3),
        (1.0, "opt", "g/t", 34.285_714_285_714_285),
        (2.5, "%", "ppm", 25_000.0),
        (180.0, "deg", "rad", std::f64::consts::PI),
        (400.0, "gon", "deg", 360.0),
        (2.7, "t/m3", "kg/m3", 2700.0),
    ];
    for (v, from, to, expected) in cases {
        let got = u(from).convert(v, &u(to)).unwrap();
        let tol = 4.0 * f64::EPSILON * expected.abs();
        assert!(
            (got - expected).abs() <= tol,
            "{v} {from} -> {to}: {got} vs {expected}"
        );
    }
}

#[test]
fn spellings_parse_to_the_same_unit() {
    let reference = u("kg/m^3");
    for s in [
        "kg/m3",
        "kg/m³",
        "kg*m^-3",
        "kg.m^-3",
        "kg·m⁻³",
        " kg / m3 ",
    ] {
        let parsed = u(s);
        assert!(parsed.is_equivalent(&reference), "{s}");
        assert_eq!(parsed.dimension(), Dimension::DENSITY);
    }
    assert_eq!(u("t/m³").symbol(), "t/m3");
    assert_eq!(u("°").symbol(), "deg");
    assert_eq!(u("GPT").symbol(), "g/t");
    assert_eq!(u("PPM").symbol(), "ppm");
    assert_eq!(u("kg/m/s").dimension(), Dimension::new(-1, 1, -1, 0));
}

#[test]
fn ambiguous_and_unknown_symbols_are_rejected_with_advice() {
    for s in ["ton", "oz/t", "ppt", "tons"] {
        match Unit::parse(s) {
            Err(UnitError::UnknownUnit { hint: Some(_), .. }) => {}
            other => panic!("{s}: expected ambiguity error, got {other:?}"),
        }
    }
    assert!(matches!(
        Unit::parse("furlong"),
        Err(UnitError::UnknownUnit { hint: None, .. })
    ));
}

#[test]
fn malformed_expressions_are_rejected() {
    // '-' without '^' is not an exponent: "kg.m-3" must be rejected.
    for s in [
        "", "   ", "/m", "m/", "m^", "m^x", "m**2", "m0", "kg m", "(m)", "m^99", "kg.m-3",
    ] {
        assert!(Unit::parse(s).is_err(), "{s:?} should not parse");
    }
}

#[test]
fn dimension_mismatch_is_an_error_and_leaves_data_untouched() {
    let mut values = [1.0, 2.0];
    let err = u("m").convert_slice(&mut values, &u("t"));
    assert!(matches!(err, Err(UnitError::DimensionMismatch { .. })));
    assert_eq!(values, [1.0, 2.0]);
    // Angles are not dimensionless: degrees never mix with plain ratios.
    assert!(u("deg").factor_to(&u("frac")).is_err());
}

#[test]
fn convert_slice_scales_every_value() {
    let mut values = [1.0, 10.0, 0.5];
    u("%").convert_slice(&mut values, &u("ppm")).unwrap();
    assert_eq!(values, [10_000.0, 100_000.0, 5_000.0]);
}

#[test]
fn quantity_kinds_guard_attribute_units() {
    assert!(QuantityKind::Grade.check(&u("g/t")).is_ok());
    assert!(QuantityKind::Grade.check(&u("%")).is_ok());
    assert!(QuantityKind::Density.check(&u("g/cm3")).is_ok());
    assert!(QuantityKind::Density.check(&u("%")).is_err());
    assert!(QuantityKind::Angle.check(&u("gon")).is_ok());
    assert!(QuantityKind::Length.check(&u("usft")).is_ok());
    assert_eq!(QuantityKind::Grade.default_unit_symbol(), None);
}

fn unit_strategy(dimension: &'static [&'static str]) -> impl Strategy<Value = Unit> {
    prop::sample::select(dimension).prop_map(u)
}

const LENGTHS: &[&str] = &["m", "mm", "cm", "km", "in", "ft", "usft"];
const MASSES: &[&str] = &["kg", "g", "mg", "t", "kt", "Mt", "lb", "st", "lt", "ozt"];
const GRADES: &[&str] = &["%", "ppm", "ppb", "g/t", "opt", "ozt/t", "frac", "mg/kg"];
const DENSITIES: &[&str] = &["t/m3", "g/cm3", "kg/m3", "kg/L", "lb/ft3", "st/usft3"];

proptest! {
    #[test]
    fn round_trip_is_identity_within_two_ulp(
        v in -1.0e9_f64..1.0e9,
        a in unit_strategy(LENGTHS),
        b in unit_strategy(LENGTHS),
    ) {
        let back = b.convert(a.convert(v, &b).unwrap(), &a).unwrap();
        prop_assert!((back - v).abs() <= 2.0 * f64::EPSILON * v.abs().max(f64::MIN_POSITIVE));
    }

    #[test]
    fn exact_factors_compose(
        a in unit_strategy(MASSES),
        b in unit_strategy(MASSES),
        c in unit_strategy(MASSES),
    ) {
        let ab = a.exact_factor_to(&b).unwrap();
        let bc = b.exact_factor_to(&c).unwrap();
        prop_assert_eq!(ab.checked_mul(bc).unwrap(), a.exact_factor_to(&c).unwrap());
    }

    #[test]
    fn grade_and_density_factors_are_mutually_inverse(
        a in unit_strategy(GRADES),
        b in unit_strategy(GRADES),
        c in unit_strategy(DENSITIES),
        d in unit_strategy(DENSITIES),
    ) {
        let ab = a.exact_factor_to(&b).unwrap();
        prop_assert!(ab.checked_mul(b.exact_factor_to(&a).unwrap()).unwrap().is_one());
        let cd = c.exact_factor_to(&d).unwrap();
        prop_assert!(cd.checked_mul(d.exact_factor_to(&c).unwrap()).unwrap().is_one());
    }

    #[test]
    fn parser_never_panics(s in "\\PC{0,12}") {
        let _ = Unit::parse(&s);
    }
}

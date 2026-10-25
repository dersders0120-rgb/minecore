//! Quantity kinds: the engineering meaning of a numeric attribute.
//!
//! A unit says how a number is measured; a kind says what it is. Grade and
//! recovery are both dimensionless, but a recovery must never be summed into a
//! grade. Attribute schemas declare a kind, and the unit attached to the data
//! must have the kind's dimension.

use std::fmt;
use std::str::FromStr;

use super::UnitError;
use super::dimension::Dimension;
use super::unit::Unit;

/// Engineering meaning of a numeric attribute.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum QuantityKind {
    /// Lengths, depths, distances, coordinates.
    Length,
    /// Areas.
    Area,
    /// Volumes.
    Volume,
    /// Masses and tonnages.
    Mass,
    /// Dry bulk density.
    Density,
    /// Grade as a mass fraction (%, ppm, g/t).
    Grade,
    /// Other dimensionless ratios: recovery, dilution, stripping ratio.
    Ratio,
    /// Plane angles: azimuth, dip, rotation.
    Angle,
    /// Durations.
    Duration,
}

impl QuantityKind {
    /// All kinds, in declaration order.
    pub const ALL: [Self; 9] = [
        Self::Length,
        Self::Area,
        Self::Volume,
        Self::Mass,
        Self::Density,
        Self::Grade,
        Self::Ratio,
        Self::Angle,
        Self::Duration,
    ];

    /// Stable lower-case name used in project files and the Python API.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Length => "length",
            Self::Area => "area",
            Self::Volume => "volume",
            Self::Mass => "mass",
            Self::Density => "density",
            Self::Grade => "grade",
            Self::Ratio => "ratio",
            Self::Angle => "angle",
            Self::Duration => "duration",
        }
    }

    /// Dimension every unit of this kind must have.
    pub const fn dimension(self) -> Dimension {
        match self {
            Self::Length => Dimension::LENGTH,
            Self::Area => Dimension::AREA,
            Self::Volume => Dimension::VOLUME,
            Self::Mass => Dimension::MASS,
            Self::Density => Dimension::DENSITY,
            Self::Grade | Self::Ratio => Dimension::NONE,
            Self::Angle => Dimension::ANGLE,
            Self::Duration => Dimension::TIME,
        }
    }

    /// Default unit for new attributes of this kind. `None` for grades: a
    /// grade unit (% for base metals, g/t for gold) must always be declared.
    pub const fn default_unit_symbol(self) -> Option<&'static str> {
        match self {
            Self::Length => Some("m"),
            Self::Area => Some("m2"),
            Self::Volume => Some("m3"),
            Self::Mass => Some("t"),
            Self::Density => Some("t/m3"),
            Self::Grade => None,
            Self::Ratio => Some("frac"),
            Self::Angle => Some("deg"),
            Self::Duration => Some("h"),
        }
    }

    /// True when `unit` has this kind's dimension.
    pub fn accepts(self, unit: &Unit) -> bool {
        unit.dimension() == self.dimension()
    }

    /// Checks that `unit` is valid for this kind.
    ///
    /// # Errors
    /// Returns [`UnitError::KindMismatch`] when the dimensions differ.
    pub fn check(self, unit: &Unit) -> Result<(), UnitError> {
        if self.accepts(unit) {
            Ok(())
        } else {
            Err(UnitError::KindMismatch {
                kind: self.name(),
                unit: unit.symbol().to_string(),
                expected: self.dimension(),
                found: unit.dimension(),
            })
        }
    }
}

impl fmt::Display for QuantityKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

impl FromStr for QuantityKind {
    type Err = UnitError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::ALL
            .into_iter()
            .find(|k| k.name() == s)
            .ok_or_else(|| UnitError::UnknownKind(s.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_units_match_their_kind() {
        for kind in QuantityKind::ALL {
            if let Some(sym) = kind.default_unit_symbol() {
                let unit = Unit::parse(sym).unwrap();
                assert!(kind.accepts(&unit), "{kind}: {sym}");
            }
        }
    }

    #[test]
    fn names_round_trip() {
        for kind in QuantityKind::ALL {
            assert_eq!(kind.name().parse::<QuantityKind>().unwrap(), kind);
        }
        assert!("tonnage".parse::<QuantityKind>().is_err());
    }

    #[test]
    fn density_rejects_grade_units() {
        let err = QuantityKind::Density.check(&Unit::parse("g/t").unwrap());
        assert!(matches!(err, Err(UnitError::KindMismatch { .. })));
        assert!(
            QuantityKind::Grade
                .check(&Unit::parse("g/t").unwrap())
                .is_ok()
        );
    }
}

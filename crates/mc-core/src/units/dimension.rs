//! Physical dimensions as exponent vectors over MineCore's base dimensions.

use std::fmt;

use super::UnitError;

/// Exponents of the base dimensions length (L), mass (M), time (T) and plane
/// angle (A).
///
/// Angle is a base dimension here, not a dimensionless ratio, so that degrees
/// can never be silently mixed with plain numbers. Further base dimensions
/// (currency, temperature) are added by ADR when a module needs them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct Dimension {
    /// Length exponent.
    pub length: i8,
    /// Mass exponent.
    pub mass: i8,
    /// Time exponent.
    pub time: i8,
    /// Plane-angle exponent.
    pub angle: i8,
}

impl Dimension {
    /// Dimensionless (ratios, grades as mass fractions, recoveries).
    pub const NONE: Self = Self::new(0, 0, 0, 0);
    /// Length.
    pub const LENGTH: Self = Self::new(1, 0, 0, 0);
    /// Area.
    pub const AREA: Self = Self::new(2, 0, 0, 0);
    /// Volume.
    pub const VOLUME: Self = Self::new(3, 0, 0, 0);
    /// Mass.
    pub const MASS: Self = Self::new(0, 1, 0, 0);
    /// Mass density.
    pub const DENSITY: Self = Self::new(-3, 1, 0, 0);
    /// Time.
    pub const TIME: Self = Self::new(0, 0, 1, 0);
    /// Plane angle.
    pub const ANGLE: Self = Self::new(0, 0, 0, 1);

    /// Builds a dimension from its exponents.
    pub const fn new(length: i8, mass: i8, time: i8, angle: i8) -> Self {
        Self {
            length,
            mass,
            time,
            angle,
        }
    }

    /// Dimension of a product.
    ///
    /// # Errors
    /// Returns [`UnitError::Overflow`] if an exponent leaves the `i8` range.
    pub fn checked_mul(self, rhs: Self) -> Result<Self, UnitError> {
        let add = |a: i8, b: i8| a.checked_add(b).ok_or(UnitError::Overflow);
        Ok(Self::new(
            add(self.length, rhs.length)?,
            add(self.mass, rhs.mass)?,
            add(self.time, rhs.time)?,
            add(self.angle, rhs.angle)?,
        ))
    }

    /// Dimension of an integer power.
    ///
    /// # Errors
    /// Returns [`UnitError::Overflow`] if an exponent leaves the `i8` range.
    pub fn checked_powi(self, exp: i32) -> Result<Self, UnitError> {
        let mul = |a: i8| {
            i32::from(a)
                .checked_mul(exp)
                .and_then(|v| i8::try_from(v).ok())
                .ok_or(UnitError::Overflow)
        };
        Ok(Self::new(
            mul(self.length)?,
            mul(self.mass)?,
            mul(self.time)?,
            mul(self.angle)?,
        ))
    }

    /// True for the dimensionless dimension.
    pub fn is_dimensionless(self) -> bool {
        self == Self::NONE
    }
}

impl fmt::Display for Dimension {
    /// Formats as e.g. `L^-3 M`, or `1` when dimensionless.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let parts: Vec<String> = [
            ("L", self.length),
            ("M", self.mass),
            ("T", self.time),
            ("A", self.angle),
        ]
        .iter()
        .filter(|(_, e)| *e != 0)
        .map(|(s, e)| {
            if *e == 1 {
                (*s).to_string()
            } else {
                format!("{s}^{e}")
            }
        })
        .collect();
        if parts.is_empty() {
            f.write_str("1")
        } else {
            f.write_str(&parts.join(" "))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn density_is_mass_over_volume() {
        let d = Dimension::MASS
            .checked_mul(Dimension::VOLUME.checked_powi(-1).unwrap())
            .unwrap();
        assert_eq!(d, Dimension::DENSITY);
        assert_eq!(d.to_string(), "L^-3 M");
    }

    #[test]
    fn mass_fraction_is_dimensionless() {
        let d = Dimension::MASS
            .checked_mul(Dimension::MASS.checked_powi(-1).unwrap())
            .unwrap();
        assert!(d.is_dimensionless());
        assert_eq!(d.to_string(), "1");
    }

    #[test]
    fn exponent_overflow_is_reported() {
        let big = Dimension::new(100, 0, 0, 0);
        assert_eq!(big.checked_powi(2), Err(UnitError::Overflow));
    }
}

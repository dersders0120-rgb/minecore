//! Exact scale factors relative to coherent SI units.
//!
//! A scale is `num / den * pi^pi_exp` with integer `num`, `den` and `pi_exp`.
//! Every unit MineCore supports has an exactly representable factor in this
//! form (the foot is 3048/10000 m, the troy ounce 311034768/10^10 kg, the
//! degree pi/180 rad), so composing and comparing units never accumulates
//! floating-point error. Conversion to `f64` happens once, at the end.

use std::cmp::Ordering;
use std::f64::consts::PI;

use super::UnitError;

/// Exact positive scale factor `num / den * pi^pi_exp`, always in lowest terms.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[must_use]
pub struct Scale {
    num: i128,
    den: i128,
    pi_exp: i8,
}

const fn gcd(mut a: i128, mut b: i128) -> i128 {
    while b != 0 {
        let t = a % b;
        a = b;
        b = t;
    }
    a.abs()
}

impl Scale {
    /// The identity scale (factor 1).
    pub const ONE: Self = Self {
        num: 1,
        den: 1,
        pi_exp: 0,
    };

    /// Builds `num / den * pi^pi_exp`.
    ///
    /// # Errors
    /// Returns [`UnitError::InvalidScale`] when `num` or `den` is not positive.
    pub fn new(num: i128, den: i128, pi_exp: i8) -> Result<Self, UnitError> {
        if num <= 0 || den <= 0 {
            return Err(UnitError::InvalidScale);
        }
        let g = gcd(num, den);
        Ok(Self {
            num: num / g,
            den: den / g,
            pi_exp,
        })
    }

    /// Integer constructor for table definitions; panics on invalid input,
    /// which can only come from a programming error in the unit table.
    pub(crate) const fn ratio(num: i128, den: i128) -> Self {
        assert!(num > 0 && den > 0, "unit table scale must be positive");
        let g = gcd(num, den);
        Self {
            num: num / g,
            den: den / g,
            pi_exp: 0,
        }
    }

    /// Integer constructor with a power of pi, for angle units.
    pub(crate) const fn ratio_pi(num: i128, den: i128, pi_exp: i8) -> Self {
        let s = Self::ratio(num, den);
        Self {
            num: s.num,
            den: s.den,
            pi_exp,
        }
    }

    /// Numerator in lowest terms.
    pub const fn numerator(self) -> i128 {
        self.num
    }

    /// Denominator in lowest terms (always positive).
    pub const fn denominator(self) -> i128 {
        self.den
    }

    /// Exponent of pi.
    pub const fn pi_exponent(self) -> i8 {
        self.pi_exp
    }

    /// Exact product.
    ///
    /// # Errors
    /// Returns [`UnitError::Overflow`] if the exact result does not fit.
    pub fn checked_mul(self, rhs: Self) -> Result<Self, UnitError> {
        // Cross-reduce first to keep intermediates small.
        let g1 = gcd(self.num, rhs.den);
        let g2 = gcd(rhs.num, self.den);
        let num = (self.num / g1)
            .checked_mul(rhs.num / g2)
            .ok_or(UnitError::Overflow)?;
        let den = (self.den / g2)
            .checked_mul(rhs.den / g1)
            .ok_or(UnitError::Overflow)?;
        let pi_exp = self
            .pi_exp
            .checked_add(rhs.pi_exp)
            .ok_or(UnitError::Overflow)?;
        Self::new(num, den, pi_exp)
    }

    /// Exact reciprocal.
    pub const fn recip(self) -> Self {
        Self {
            num: self.den,
            den: self.num,
            pi_exp: -self.pi_exp,
        }
    }

    /// Exact quotient.
    ///
    /// # Errors
    /// Returns [`UnitError::Overflow`] if the exact result does not fit.
    pub fn checked_div(self, rhs: Self) -> Result<Self, UnitError> {
        self.checked_mul(rhs.recip())
    }

    /// Exact integer power.
    ///
    /// # Errors
    /// Returns [`UnitError::Overflow`] if the exact result does not fit.
    pub fn checked_powi(self, exp: i32) -> Result<Self, UnitError> {
        let base = match exp.cmp(&0) {
            Ordering::Less => self.recip(),
            Ordering::Equal => return Ok(Self::ONE),
            Ordering::Greater => self,
        };
        let mut out = Self::ONE;
        for _ in 0..exp.unsigned_abs() {
            out = out.checked_mul(base)?;
        }
        Ok(out)
    }

    /// Nearest `f64` to the exact factor.
    #[allow(clippy::cast_precision_loss)] // the single, intended rounding step
    pub fn to_f64(self) -> f64 {
        let ratio = self.num as f64 / self.den as f64;
        ratio * PI.powi(i32::from(self.pi_exp))
    }

    /// True when the factor is exactly 1.
    pub const fn is_one(self) -> bool {
        self.num == 1 && self.den == 1 && self.pi_exp == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_reduces_to_lowest_terms() {
        let s = Scale::new(3048, 10_000, 0).unwrap();
        assert_eq!((s.numerator(), s.denominator()), (381, 1250));
    }

    #[test]
    fn rejects_non_positive_parts() {
        assert_eq!(Scale::new(0, 1, 0), Err(UnitError::InvalidScale));
        assert_eq!(Scale::new(1, -2, 0), Err(UnitError::InvalidScale));
    }

    #[test]
    fn product_and_quotient_are_exact() {
        let ft = Scale::ratio(3048, 10_000);
        let ft3 = ft.checked_powi(3).unwrap();
        assert_eq!(
            ft3.checked_div(ft).unwrap(),
            ft.checked_mul(ft).unwrap(),
            "ft^3 / ft == ft^2 exactly"
        );
        assert!(ft.checked_div(ft).unwrap().is_one());
    }

    #[test]
    fn negative_power_is_reciprocal() {
        let km = Scale::ratio(1000, 1);
        let inv = km.checked_powi(-2).unwrap();
        assert_eq!((inv.numerator(), inv.denominator()), (1, 1_000_000));
    }

    #[test]
    fn pi_exponent_round_trips() {
        let deg = Scale::ratio_pi(1, 180, 1);
        let gon = Scale::ratio_pi(1, 200, 1);
        let r = gon.checked_div(deg).unwrap();
        assert_eq!(
            (r.numerator(), r.denominator(), r.pi_exponent()),
            (9, 10, 0)
        );
        assert!((deg.to_f64() - PI / 180.0).abs() < 1e-18);
    }

    #[test]
    fn overflow_is_reported_not_wrapped() {
        let big = Scale::ratio(10_i128.pow(20), 1);
        assert_eq!(big.checked_powi(3), Err(UnitError::Overflow));
    }
}

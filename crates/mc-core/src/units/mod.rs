//! Units of measure and quantity kinds — a shared MineCore contract.
//!
//! Every numeric attribute in a MineCore project carries a [`Unit`] and a
//! [`QuantityKind`]. Conversions are computed from exact rational scales
//! ([`Scale`]), so `ft -> m`, `ozt/st -> g/t` and `gon -> deg` are exact up to
//! the final rounding to `f64`, and equivalent units convert with a factor of
//! exactly 1.
//!
//! Changing this module is a contract change: it needs an ADR and tests that
//! prove existing project data still reads identically.

mod dimension;
mod kind;
mod scale;
mod table;
mod unit;

use thiserror::Error;

pub use dimension::Dimension;
pub use kind::QuantityKind;
pub use scale::Scale;
pub use unit::Unit;

/// Errors from parsing, composing or converting units.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum UnitError {
    /// The unit text was empty.
    #[error("unit is empty")]
    Empty,
    /// The symbol is not a known unit, or is ambiguous.
    #[error("unknown unit '{symbol}'{}", hint.map(|h| format!(": {h}")).unwrap_or_default())]
    UnknownUnit {
        /// The offending symbol.
        symbol: String,
        /// Advice for ambiguous symbols such as `ton` or `oz`.
        hint: Option<&'static str>,
    },
    /// The expression is malformed.
    #[error("invalid unit '{input}' at byte {position}: {message}")]
    Syntax {
        /// The full input.
        input: String,
        /// Byte offset of the problem.
        position: usize,
        /// What was expected.
        message: &'static str,
    },
    /// Conversion between units of different dimensions.
    #[error("cannot convert '{from}' ({from_dimension}) to '{to}' ({to_dimension})")]
    DimensionMismatch {
        /// Source unit symbol.
        from: String,
        /// Source dimension.
        from_dimension: Dimension,
        /// Target unit symbol.
        to: String,
        /// Target dimension.
        to_dimension: Dimension,
    },
    /// A unit does not fit the declared quantity kind.
    #[error("unit '{unit}' ({found}) is not valid for kind '{kind}' (needs {expected})")]
    KindMismatch {
        /// Kind name.
        kind: &'static str,
        /// Unit symbol.
        unit: String,
        /// Dimension the kind requires.
        expected: Dimension,
        /// Dimension of the unit.
        found: Dimension,
    },
    /// Unknown quantity kind name.
    #[error("unknown quantity kind '{0}'")]
    UnknownKind(String),
    /// An exponent or exact scale left the representable range.
    #[error("unit expression is too large to represent exactly")]
    Overflow,
    /// A scale with a non-positive numerator or denominator.
    #[error("unit scale must be positive")]
    InvalidScale,
}

//! Units: parsing, composition and conversion.

use std::fmt;

use super::UnitError;
use super::dimension::Dimension;
use super::scale::Scale;
use super::table::{ambiguity_hint, find_atom, find_shorthand};

/// A physical unit: a canonical symbol, a dimension and an exact scale to the
/// coherent SI unit of that dimension.
///
/// `PartialEq` is structural (same symbol, dimension and scale). Use
/// [`Unit::is_equivalent`] to ask whether two spellings denote the same unit,
/// e.g. `ppm` and `g/t`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Unit {
    symbol: String,
    dimension: Dimension,
    scale: Scale,
}

/// One `atom^exponent` factor of a parsed expression.
struct Term {
    symbol: &'static str,
    exponent: i32,
}

impl Unit {
    /// Parses a unit expression such as `m`, `t/m3`, `kg/m^3`, `g/t`, `%`,
    /// `deg` or `m³`.
    ///
    /// Grammar: terms joined by `*`, `.` or `·` (product) and `/` (quotient,
    /// left-associative, so `kg/m/s` is `kg·m⁻¹·s⁻¹`). A term is an atom with
    /// an optional integer exponent written `^n`, as trailing digits (`m3`) or
    /// as superscripts (`m³`). Whitespace is allowed only around operators.
    ///
    /// # Errors
    /// Returns a [`UnitError`] for empty input, unknown or ambiguous symbols,
    /// syntax errors and exponent overflow. Nothing is guessed.
    pub fn parse(input: &str) -> Result<Self, UnitError> {
        let trimmed = input.trim();
        if trimmed.is_empty() {
            return Err(UnitError::Empty);
        }
        let expr = find_shorthand(trimmed).unwrap_or(trimmed);
        let terms = parse_terms(expr)?;
        Self::from_terms(&terms)
    }

    fn from_terms(terms: &[Term]) -> Result<Self, UnitError> {
        let mut dimension = Dimension::NONE;
        let mut scale = Scale::ONE;
        for term in terms {
            let atom = find_atom(term.symbol).ok_or_else(|| UnitError::UnknownUnit {
                symbol: term.symbol.to_string(),
                hint: None,
            })?;
            dimension = dimension.checked_mul(atom.dimension.checked_powi(term.exponent)?)?;
            scale = scale.checked_mul(atom.scale.checked_powi(term.exponent)?)?;
        }
        Ok(Self {
            symbol: format_terms(terms),
            dimension,
            scale,
        })
    }

    /// Canonical symbol, e.g. `t/m3` for input `t/m³`.
    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    /// Physical dimension.
    pub const fn dimension(&self) -> Dimension {
        self.dimension
    }

    /// Exact scale to the coherent SI unit of the same dimension.
    pub const fn scale(&self) -> Scale {
        self.scale
    }

    /// Factor to the coherent SI unit as `f64` (e.g. 0.3048 for `ft`).
    pub fn si_factor(&self) -> f64 {
        self.scale.to_f64()
    }

    /// True when both units have the same dimension and exactly the same scale.
    pub fn is_equivalent(&self, other: &Self) -> bool {
        self.dimension == other.dimension && self.scale == other.scale
    }

    /// Exact conversion factor: `value_in_target = value_in_self * factor`.
    ///
    /// # Errors
    /// Returns [`UnitError::DimensionMismatch`] when the dimensions differ.
    pub fn exact_factor_to(&self, target: &Self) -> Result<Scale, UnitError> {
        if self.dimension != target.dimension {
            return Err(UnitError::DimensionMismatch {
                from: self.symbol.clone(),
                from_dimension: self.dimension,
                to: target.symbol.clone(),
                to_dimension: target.dimension,
            });
        }
        self.scale.checked_div(target.scale)
    }

    /// Conversion factor as `f64`; exactly `1.0` for equivalent units.
    ///
    /// # Errors
    /// Returns [`UnitError::DimensionMismatch`] when the dimensions differ.
    pub fn factor_to(&self, target: &Self) -> Result<f64, UnitError> {
        let exact = self.exact_factor_to(target)?;
        Ok(if exact.is_one() { 1.0 } else { exact.to_f64() })
    }

    /// Converts one value from `self` to `target`.
    ///
    /// # Errors
    /// Returns [`UnitError::DimensionMismatch`] when the dimensions differ.
    pub fn convert(&self, value: f64, target: &Self) -> Result<f64, UnitError> {
        Ok(value * self.factor_to(target)?)
    }

    /// Converts values in place from `self` to `target`. Leaves the slice
    /// untouched when the units are equivalent.
    ///
    /// # Errors
    /// Returns [`UnitError::DimensionMismatch`] when the dimensions differ;
    /// the slice is then unchanged.
    pub fn convert_slice(&self, values: &mut [f64], target: &Self) -> Result<(), UnitError> {
        let exact = self.exact_factor_to(target)?;
        if !exact.is_one() {
            let factor = exact.to_f64();
            for v in values.iter_mut() {
                *v *= factor;
            }
        }
        Ok(())
    }
}

impl fmt::Display for Unit {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.symbol)
    }
}

impl std::str::FromStr for Unit {
    type Err = UnitError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::parse(s)
    }
}

fn is_atom_char(c: char) -> bool {
    c.is_alphabetic() || c == '%' || c == '°' || c == '_'
}

fn superscript_digit(c: char) -> Option<i32> {
    match c {
        '⁰' => Some(0),
        '¹' => Some(1),
        '²' => Some(2),
        '³' => Some(3),
        '⁴' => Some(4),
        '⁵' => Some(5),
        '⁶' => Some(6),
        '⁷' => Some(7),
        '⁸' => Some(8),
        '⁹' => Some(9),
        _ => None,
    }
}

fn syntax(input: &str, position: usize, message: &'static str) -> UnitError {
    UnitError::Syntax {
        input: input.to_string(),
        position,
        message,
    }
}

/// Splits an expression into signed terms. Exponents after `/` are negated.
fn parse_terms(input: &str) -> Result<Vec<Term>, UnitError> {
    let chars: Vec<(usize, char)> = input.char_indices().collect();
    let mut terms = Vec::new();
    let mut i = 0;
    let mut sign = 1;
    loop {
        // Atom
        let start = i;
        while i < chars.len() && is_atom_char(chars[i].1) {
            i += 1;
        }
        let pos = chars.get(start).map_or(input.len(), |c| c.0);
        if i == start {
            return Err(syntax(input, pos, "expected a unit symbol"));
        }
        let end = chars.get(i).map_or(input.len(), |c| c.0);
        let text = &input[pos..end];
        let symbol = match find_atom(text) {
            Some(atom) => atom.symbol,
            None => {
                return Err(UnitError::UnknownUnit {
                    symbol: text.to_string(),
                    hint: ambiguity_hint(text),
                });
            }
        };
        // Exponent
        let (exponent, next) = parse_exponent(input, &chars, i)?;
        i = next;
        terms.push(Term {
            symbol,
            exponent: exponent * sign,
        });
        // Operator or end
        while i < chars.len() && chars[i].1.is_whitespace() {
            i += 1;
        }
        if i == chars.len() {
            return Ok(terms);
        }
        sign = match chars[i].1 {
            '*' | '.' | '·' => 1,
            '/' => -1,
            _ => return Err(syntax(input, chars[i].0, "expected '*', '.', '/' or end")),
        };
        i += 1;
        while i < chars.len() && chars[i].1.is_whitespace() {
            i += 1;
        }
    }
}

fn parse_exponent(
    input: &str,
    chars: &[(usize, char)],
    mut i: usize,
) -> Result<(i32, usize), UnitError> {
    let pos = |i: usize| chars.get(i).map_or(input.len(), |c| c.0);
    let digits = |i: &mut usize| -> Option<i32> {
        let mut value: Option<i32> = None;
        while let Some(d) = chars.get(*i).and_then(|c| c.1.to_digit(10)) {
            let d = i32::try_from(d).unwrap_or(0);
            value = Some(value.unwrap_or(0).saturating_mul(10).saturating_add(d));
            *i += 1;
        }
        value
    };
    match chars.get(i).map(|c| c.1) {
        Some('^') => {
            i += 1;
            let negative = match chars.get(i).map(|c| c.1) {
                Some('-') => {
                    i += 1;
                    true
                }
                Some('+') => {
                    i += 1;
                    false
                }
                _ => false,
            };
            let value = digits(&mut i)
                .ok_or_else(|| syntax(input, pos(i), "expected an integer exponent after '^'"))?;
            Ok((if negative { -value } else { value }, i))
        }
        Some(c) if c.is_ascii_digit() => {
            let value = digits(&mut i).unwrap_or(1);
            Ok((value, i))
        }
        Some(c) if c == '⁻' || superscript_digit(c).is_some() => {
            let negative = c == '⁻';
            if negative {
                i += 1;
            }
            let mut value: Option<i32> = None;
            while let Some(d) = chars.get(i).and_then(|c| superscript_digit(c.1)) {
                value = Some(value.unwrap_or(0).saturating_mul(10).saturating_add(d));
                i += 1;
            }
            let value =
                value.ok_or_else(|| syntax(input, pos(i), "expected a superscript digit"))?;
            Ok((if negative { -value } else { value }, i))
        }
        _ => Ok((1, i)),
    }
    .and_then(|(value, next)| {
        if value == 0 {
            Err(syntax(input, pos(next), "exponent must not be zero"))
        } else if value.abs() > 12 {
            Err(UnitError::Overflow)
        } else {
            Ok((value, next))
        }
    })
}

/// Canonical text: numerator terms joined by `*`, then `/` before each
/// denominator term; positive exponents as trailing digits (`m3`).
fn format_terms(terms: &[Term]) -> String {
    let fmt_term = |t: &Term, exp: i32| {
        if exp == 1 {
            t.symbol.to_string()
        } else {
            format!("{}{}", t.symbol, exp)
        }
    };
    let num: Vec<String> = terms
        .iter()
        .filter(|t| t.exponent > 0)
        .map(|t| fmt_term(t, t.exponent))
        .collect();
    let den: Vec<String> = terms
        .iter()
        .filter(|t| t.exponent < 0)
        .map(|t| fmt_term(t, -t.exponent))
        .collect();
    let mut out = if num.is_empty() {
        "1".to_string()
    } else {
        num.join("*")
    };
    for d in den {
        out.push('/');
        out.push_str(&d);
    }
    out
}

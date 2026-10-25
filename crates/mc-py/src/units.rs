//! Python bindings for units of measure and quantity kinds.

use mc_core::units as core_units;
use pyo3::prelude::*;

use crate::UnitError;

fn unit_err(e: &core_units::UnitError) -> PyErr {
    UnitError::new_err(e.to_string())
}

/// A unit of measure, parsed from text such as `"t/m3"`, `"g/t"` or `"deg"`.
#[pyclass(
    module = "minecore._core",
    name = "Unit",
    frozen,
    eq,
    hash,
    from_py_object
)]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct PyUnit {
    inner: core_units::Unit,
}

/// A unit given either as a `Unit` or as its text.
#[derive(FromPyObject)]
enum UnitArg {
    Unit(PyUnit),
    Text(String),
}

impl UnitArg {
    fn resolve(self) -> PyResult<core_units::Unit> {
        match self {
            Self::Unit(u) => Ok(u.inner),
            Self::Text(s) => core_units::Unit::parse(&s).map_err(|e| unit_err(&e)),
        }
    }
}

#[pymethods]
impl PyUnit {
    #[new]
    fn new(expression: &str) -> PyResult<Self> {
        core_units::Unit::parse(expression)
            .map(|inner| Self { inner })
            .map_err(|e| unit_err(&e))
    }

    /// Canonical symbol, e.g. `"t/m3"`.
    #[getter]
    fn symbol(&self) -> &str {
        self.inner.symbol()
    }

    /// Dimension as text, e.g. `"L^-3 M"`; `"1"` when dimensionless.
    #[getter]
    fn dimension(&self) -> String {
        self.inner.dimension().to_string()
    }

    /// Factor to the coherent SI unit as a float.
    #[getter]
    fn si_factor(&self) -> f64 {
        self.inner.si_factor()
    }

    /// Exact factor to the coherent SI unit as `(numerator, denominator, pi_exponent)`.
    #[getter]
    fn exact_si_factor(&self) -> (i128, i128, i8) {
        let s = self.inner.scale();
        (s.numerator(), s.denominator(), s.pi_exponent())
    }

    /// True when both denote the same unit (e.g. `ppm` and `g/t`).
    fn is_equivalent(&self, other: UnitArg) -> PyResult<bool> {
        Ok(self.inner.is_equivalent(&other.resolve()?))
    }

    /// Exact conversion factor to `target` as `(numerator, denominator, pi_exponent)`.
    fn exact_factor_to(&self, target: UnitArg) -> PyResult<(i128, i128, i8)> {
        let s = self
            .inner
            .exact_factor_to(&target.resolve()?)
            .map_err(|e| unit_err(&e))?;
        Ok((s.numerator(), s.denominator(), s.pi_exponent()))
    }

    /// Conversion factor to `target`; exactly 1.0 for equivalent units.
    fn factor_to(&self, target: UnitArg) -> PyResult<f64> {
        self.inner
            .factor_to(&target.resolve()?)
            .map_err(|e| unit_err(&e))
    }

    /// Converts one value to `target`.
    fn convert(&self, value: f64, target: UnitArg) -> PyResult<f64> {
        self.inner
            .convert(value, &target.resolve()?)
            .map_err(|e| unit_err(&e))
    }

    fn __str__(&self) -> &str {
        self.inner.symbol()
    }

    fn __repr__(&self) -> String {
        format!("Unit('{}')", self.inner.symbol())
    }
}

/// Engineering meaning of a numeric attribute.
#[pyclass(module = "minecore._core", name = "QuantityKind", frozen, eq, hash)]
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
#[allow(clippy::upper_case_acronyms)]
pub enum PyQuantityKind {
    LENGTH,
    AREA,
    VOLUME,
    MASS,
    DENSITY,
    GRADE,
    RATIO,
    ANGLE,
    DURATION,
}

impl PyQuantityKind {
    const fn core(self) -> core_units::QuantityKind {
        use core_units::QuantityKind as K;
        match self {
            Self::LENGTH => K::Length,
            Self::AREA => K::Area,
            Self::VOLUME => K::Volume,
            Self::MASS => K::Mass,
            Self::DENSITY => K::Density,
            Self::GRADE => K::Grade,
            Self::RATIO => K::Ratio,
            Self::ANGLE => K::Angle,
            Self::DURATION => K::Duration,
        }
    }

    const fn from_core(kind: core_units::QuantityKind) -> Self {
        use core_units::QuantityKind as K;
        match kind {
            K::Length => Self::LENGTH,
            K::Area => Self::AREA,
            K::Volume => Self::VOLUME,
            K::Mass => Self::MASS,
            K::Density => Self::DENSITY,
            K::Grade => Self::GRADE,
            K::Ratio => Self::RATIO,
            K::Angle => Self::ANGLE,
            K::Duration => Self::DURATION,
        }
    }
}

// PyO3 methods must take `&self`, even for a one-byte enum.
#[allow(clippy::trivially_copy_pass_by_ref)]
#[pymethods]
impl PyQuantityKind {
    /// Kind from its stable name, e.g. `"density"`.
    #[staticmethod]
    fn from_name(name: &str) -> PyResult<Self> {
        name.parse::<core_units::QuantityKind>()
            .map(Self::from_core)
            .map_err(|e| unit_err(&e))
    }

    /// Stable lower-case name used in project files.
    #[getter]
    fn name(&self) -> &'static str {
        self.core().name()
    }

    /// Dimension every unit of this kind must have, as text.
    #[getter]
    fn dimension(&self) -> String {
        self.core().dimension().to_string()
    }

    /// Default unit for new attributes, or `None` for grades (always declared).
    #[getter]
    fn default_unit(&self) -> PyResult<Option<PyUnit>> {
        self.core()
            .default_unit_symbol()
            .map(PyUnit::new)
            .transpose()
    }

    /// True when `unit` has this kind's dimension.
    fn accepts(&self, unit: UnitArg) -> PyResult<bool> {
        Ok(self.core().accepts(&unit.resolve()?))
    }

    /// Raises `UnitError` when `unit` is not valid for this kind.
    fn check(&self, unit: UnitArg) -> PyResult<()> {
        self.core()
            .check(&unit.resolve()?)
            .map_err(|e| unit_err(&e))
    }
}

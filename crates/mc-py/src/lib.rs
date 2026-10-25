//! Python bindings for the MineCore core engine.
//!
//! Exposed to Python as `minecore._core`. This crate only translates between
//! Python and `mc-core`; it holds no engineering logic of its own.

mod coords;
mod pitopt;
mod units;

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;

create_exception!(
    _core,
    MineCoreError,
    PyException,
    "Base class of all MineCore errors."
);
create_exception!(
    _core,
    UnitError,
    MineCoreError,
    "Invalid unit, unit syntax or unit conversion."
);
create_exception!(
    _core,
    CoordinateError,
    MineCoreError,
    "Invalid local grid, control points or coordinate arrays."
);

create_exception!(
    _core,
    OptimisationError,
    MineCoreError,
    "Invalid input to an optimisation solver."
);

/// Returns the version of the Rust core engine.
#[pyfunction]
fn core_version() -> &'static str {
    mc_core::VERSION
}

/// Returns the compilation profile of the Rust core engine ("debug" or "release").
#[pyfunction]
fn core_build_profile() -> &'static str {
    mc_core::build_info().profile.as_str()
}

/// Native extension module `minecore._core`.
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(core_version, m)?)?;
    m.add_function(wrap_pyfunction!(core_build_profile, m)?)?;
    m.add_class::<units::PyUnit>()?;
    m.add_class::<units::PyQuantityKind>()?;
    m.add_class::<coords::PyLocalGrid>()?;
    m.add_class::<coords::PyGridFit>()?;
    m.add_function(wrap_pyfunction!(coords::fit_local_grid, m)?)?;
    m.add_function(wrap_pyfunction!(pitopt::max_closure, m)?)?;
    m.add("OptimisationError", m.py().get_type::<OptimisationError>())?;
    m.add("MineCoreError", m.py().get_type::<MineCoreError>())?;
    m.add("UnitError", m.py().get_type::<UnitError>())?;
    m.add("CoordinateError", m.py().get_type::<CoordinateError>())?;
    Ok(())
}

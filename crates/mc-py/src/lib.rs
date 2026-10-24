//! Python bindings for the MineCore core engine.
//!
//! Exposed to Python as `minecore._core`. This crate only translates between
//! Python and `mc-core`; it holds no engineering logic of its own.

use pyo3::prelude::*;

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
    Ok(())
}

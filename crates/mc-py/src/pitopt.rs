//! Python bindings for the ultimate-pit (maximum closure) solver.

use mc_core::pitopt;
use numpy::{AllowTypeChange, IntoPyArray, PyArray1, PyArrayLike1, PyArrayLike2};
use pyo3::prelude::*;

use crate::OptimisationError;

/// Smallest maximum-weight closure (the ultimate pit).
///
/// `values` are integer block values (e.g. cents); `precedence` is an
/// `m x 2` array of node pairs `(i, j)` meaning "mining i requires j".
/// Returns a boolean array, `True` for blocks inside the pit. Runs without
/// the GIL.
#[pyfunction]
#[allow(clippy::needless_pass_by_value)]
pub fn max_closure<'py>(
    py: Python<'py>,
    values: PyArrayLike1<'py, i64, AllowTypeChange>,
    precedence: PyArrayLike2<'py, u32, AllowTypeChange>,
) -> PyResult<Bound<'py, PyArray1<bool>>> {
    let values = values.as_array().to_vec();
    let arcs = precedence.as_array();
    if arcs.ncols() != 2 && arcs.nrows() > 0 {
        return Err(OptimisationError::new_err(
            "precedence must be an m x 2 array",
        ));
    }
    let pairs: Vec<(u32, u32)> = arcs.rows().into_iter().map(|r| (r[0], r[1])).collect();
    let pit = py
        .detach(|| pitopt::max_closure(&values, &pairs))
        .map_err(|e| OptimisationError::new_err(e.to_string()))?;
    Ok(pit.into_pyarray(py))
}

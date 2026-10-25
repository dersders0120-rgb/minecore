//! Python bindings for the neighbourhood estimation kernels.

use mc_core::kriging::{self, Estimates, Method, Problem, Selection, Shape, Structure, Variogram};
use numpy::{AllowTypeChange, IntoPyArray, PyArray1, PyArrayLike1, PyArrayLike2};
use pyo3::prelude::*;

use crate::EstimationKernelError;

type Out<'py> = (
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<i64>>,
    Bound<'py, PyArray1<f64>>,
);
type F2<'py> = PyArrayLike2<'py, f64, AllowTypeChange>;

fn rows3(name: &str, a: &F2<'_>) -> PyResult<Vec<[f64; 3]>> {
    let a = a.as_array();
    if a.ncols() != 3 {
        return Err(EstimationKernelError::new_err(format!(
            "{name} must be n x 3"
        )));
    }
    Ok(a.rows().into_iter().map(|r| [r[0], r[1], r[2]]).collect())
}

/// Estimates targets from KD-tree candidates (see `mc_core::kriging`).
///
/// `method` is `"nearest"`, `"inverse_distance"` or `"ordinary_kriging"`.
/// `structures` holds `(shape, sill, transform)` with shape "spherical",
/// "exponential" or "gaussian" and a 3 x 3 transform (axes / ranges).
/// Returns `(value, variance, used, mean_distance)`.
#[pyfunction]
#[allow(clippy::needless_pass_by_value, clippy::too_many_arguments)]
#[pyo3(signature = (method, samples, values, targets, candidates, distances, *, min_samples, max_samples, max_per_hole = 0, holes = None, exclude_hole = None, power = 2.0, nugget = 0.0, structures = Vec::new(), offsets = None))]
pub fn estimate_neighbourhood<'py>(
    py: Python<'py>,
    method: &str,
    samples: F2<'py>,
    values: PyArrayLike1<'py, f64, AllowTypeChange>,
    targets: F2<'py>,
    candidates: PyArrayLike2<'py, u32, AllowTypeChange>,
    distances: F2<'py>,
    min_samples: usize,
    max_samples: usize,
    max_per_hole: usize,
    holes: Option<PyArrayLike1<'py, i64, AllowTypeChange>>,
    exclude_hole: Option<PyArrayLike1<'py, i64, AllowTypeChange>>,
    power: f64,
    nugget: f64,
    structures: Vec<(String, f64, [[f64; 3]; 3])>,
    offsets: Option<F2<'py>>,
) -> PyResult<Out<'py>> {
    let samples = rows3("samples", &samples)?;
    let targets = rows3("targets", &targets)?;
    let values = values.as_array().to_vec();
    let cand = candidates.as_array();
    let k = cand.ncols();
    if cand.nrows() != targets.len() || distances.as_array().dim() != cand.dim() {
        return Err(EstimationKernelError::new_err(
            "candidates and distances must be m x k with one row per target",
        ));
    }
    if values.len() != samples.len() {
        return Err(EstimationKernelError::new_err(
            "one value per sample is required",
        ));
    }
    let cand: Vec<u32> = cand.iter().copied().collect();
    let dist: Vec<f64> = distances.as_array().iter().copied().collect();
    let holes = holes.map(|h| h.as_array().to_vec());
    let exclude = exclude_hole.map(|h| h.as_array().to_vec());
    if holes.as_ref().is_some_and(|h| h.len() != samples.len())
        || exclude.as_ref().is_some_and(|h| h.len() != targets.len())
    {
        return Err(EstimationKernelError::new_err(
            "holes / exclude_hole have the wrong length",
        ));
    }
    if cand
        .iter()
        .any(|&c| c != u32::MAX && c as usize >= samples.len())
    {
        return Err(EstimationKernelError::new_err(
            "candidate index out of range",
        ));
    }
    let method = match method {
        "nearest" => Method::Nearest,
        "inverse_distance" => Method::InverseDistance { power },
        "ordinary_kriging" => {
            let mut parsed = Vec::new();
            for (shape, sill, transform) in structures {
                let shape = match shape.as_str() {
                    "spherical" => Shape::Spherical,
                    "exponential" => Shape::Exponential,
                    "gaussian" => Shape::Gaussian,
                    other => {
                        return Err(EstimationKernelError::new_err(format!(
                            "unknown structure shape {other:?}"
                        )));
                    }
                };
                parsed.push(Structure {
                    shape,
                    sill,
                    transform,
                });
            }
            let offsets = match offsets {
                Some(o) => rows3("offsets", &o)?,
                None => vec![[0.0; 3]],
            };
            Method::OrdinaryKriging {
                variogram: Variogram {
                    nugget,
                    structures: parsed,
                },
                offsets,
            }
        }
        other => {
            return Err(EstimationKernelError::new_err(format!(
                "unknown method {other:?}"
            )));
        }
    };
    let rules = Selection {
        min_samples,
        max_samples,
        max_per_hole,
    };
    let result: Estimates = py.detach(|| {
        let problem = Problem {
            samples: &samples,
            values: &values,
            holes: holes.as_deref(),
            targets: &targets,
            exclude_hole: exclude.as_deref(),
            candidates: &cand,
            distances: &dist,
            k,
        };
        kriging::estimate(&problem, &method, rules)
    });
    Ok((
        result.value.into_pyarray(py),
        result.variance.into_pyarray(py),
        result.used.into_pyarray(py),
        result.mean_distance.into_pyarray(py),
    ))
}

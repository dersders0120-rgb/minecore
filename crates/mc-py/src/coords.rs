//! Python bindings for local mine grids.
//!
//! Array arguments accept anything NumPy can convert to a 1-D (or `n x 2`)
//! float64 array. Inputs are copied once; the transformation runs without the
//! GIL and the results are returned as new arrays (ADR-0006).

use mc_core::coords::{
    ControlPoint, CoordError, FitOptions, GridFit, LocalGrid, LocalGridParams, ScaleModel,
    VerticalModel,
};
use numpy::{AllowTypeChange, IntoPyArray, PyArray1, PyArrayLike1, PyArrayLike2};
use pyo3::prelude::*;

use crate::CoordinateError;

fn coord_err(e: &CoordError) -> PyErr {
    CoordinateError::new_err(e.to_string())
}

type Array<'py> = PyArrayLike1<'py, f64, AllowTypeChange>;
type Arrays<'py> = (
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Option<Bound<'py, PyArray1<f64>>>,
);
type InPlace = fn(&LocalGrid, &mut [f64], &mut [f64], Option<&mut [f64]>) -> Result<(), CoordError>;

/// A local mine grid: 2D similarity transformation plus a vertical offset
/// relative to a base projected CRS. See `mc_core::coords::LocalGridParams`
/// for the exact formula and the rotation convention.
#[pyclass(
    module = "minecore._core",
    name = "LocalGrid",
    frozen,
    eq,
    from_py_object
)]
#[derive(Clone, PartialEq)]
pub struct PyLocalGrid {
    inner: LocalGrid,
}

impl PyLocalGrid {
    fn apply<'py>(
        &self,
        py: Python<'py>,
        x: &Array<'py>,
        y: &Array<'py>,
        z: Option<&Array<'py>>,
        op: InPlace,
    ) -> PyResult<Arrays<'py>> {
        let mut xs = x.as_array().to_vec();
        let mut ys = y.as_array().to_vec();
        let mut zs = z.map(|z| z.as_array().to_vec());
        let grid = self.inner;
        py.detach(|| op(&grid, &mut xs, &mut ys, zs.as_deref_mut()))
            .map_err(|e| coord_err(&e))?;
        Ok((
            xs.into_pyarray(py),
            ys.into_pyarray(py),
            zs.map(|z| z.into_pyarray(py)),
        ))
    }
}

// PyO3 extracts arguments by value.
#[allow(clippy::needless_pass_by_value)]
#[pymethods]
impl PyLocalGrid {
    #[new]
    #[pyo3(signature = (*, base_origin, local_origin, rotation_deg, scale = 1.0, z_offset = 0.0))]
    fn new(
        base_origin: (f64, f64),
        local_origin: (f64, f64),
        rotation_deg: f64,
        scale: f64,
        z_offset: f64,
    ) -> PyResult<Self> {
        LocalGrid::new(LocalGridParams {
            base_origin: [base_origin.0, base_origin.1],
            local_origin: [local_origin.0, local_origin.1],
            rotation_deg,
            scale,
            z_offset,
        })
        .map(|inner| Self { inner })
        .map_err(|e| coord_err(&e))
    }

    /// `(E0, N0)` in the base CRS.
    #[getter]
    fn base_origin(&self) -> (f64, f64) {
        let [e, n] = self.inner.params().base_origin;
        (e, n)
    }

    /// `(x0, y0)` in the local grid.
    #[getter]
    fn local_origin(&self) -> (f64, f64) {
        let [x, y] = self.inner.params().local_origin;
        (x, y)
    }

    /// Azimuth of local grid north, clockwise from base grid north, degrees.
    #[getter]
    fn rotation_deg(&self) -> f64 {
        self.inner.params().rotation_deg
    }

    /// Local length per base length.
    #[getter]
    fn scale(&self) -> f64 {
        self.inner.params().scale
    }

    /// Added to base elevations to give local elevations.
    #[getter]
    fn z_offset(&self) -> f64 {
        self.inner.params().z_offset
    }

    /// Base `(E, N[, Z])` arrays to local `(x, y, z)`; `z` is `None` if not given.
    #[pyo3(signature = (x, y, z = None))]
    fn to_local<'py>(
        &self,
        py: Python<'py>,
        x: Array<'py>,
        y: Array<'py>,
        z: Option<Array<'py>>,
    ) -> PyResult<Arrays<'py>> {
        self.apply(py, &x, &y, z.as_ref(), LocalGrid::to_local_in_place)
    }

    /// Local `(x, y[, z])` arrays to base `(E, N, Z)`; `Z` is `None` if not given.
    #[pyo3(signature = (x, y, z = None))]
    fn to_base<'py>(
        &self,
        py: Python<'py>,
        x: Array<'py>,
        y: Array<'py>,
        z: Option<Array<'py>>,
    ) -> PyResult<Arrays<'py>> {
        self.apply(py, &x, &y, z.as_ref(), LocalGrid::to_base_in_place)
    }

    fn __repr__(&self) -> String {
        let p = self.inner.params();
        format!(
            "LocalGrid(base_origin=({:?}, {:?}), local_origin=({:?}, {:?}), rotation_deg={:?}, \
             scale={:?}, z_offset={:?})",
            p.base_origin[0],
            p.base_origin[1],
            p.local_origin[0],
            p.local_origin[1],
            p.rotation_deg,
            p.scale,
            p.z_offset
        )
    }
}

/// Result of `fit_local_grid`: the fitted grid and its quality figures.
#[pyclass(module = "minecore._core", name = "GridFit", frozen)]
pub struct PyGridFit {
    inner: GridFit,
}

#[pymethods]
impl PyGridFit {
    /// The fitted grid; its origins are the control-point centroids.
    #[getter]
    fn grid(&self) -> PyLocalGrid {
        PyLocalGrid {
            inner: self.inner.grid,
        }
    }

    /// Per point `(dx, dy, dz)`: observed local minus fitted, in input order.
    #[getter]
    fn residuals(&self) -> Vec<(f64, f64, Option<f64>)> {
        self.inner
            .residuals
            .iter()
            .map(|r| (r.dx, r.dy, r.dz))
            .collect()
    }

    /// RMS of horizontal residual lengths.
    #[getter]
    fn rms_horizontal(&self) -> f64 {
        self.inner.rms_horizontal
    }

    /// Largest horizontal residual length.
    #[getter]
    fn max_horizontal(&self) -> f64 {
        self.inner.max_horizontal
    }

    /// Standard error of unit weight; `None` without redundancy.
    #[getter]
    fn sigma0_horizontal(&self) -> Option<f64> {
        self.inner.sigma0_horizontal
    }

    /// Number of points with elevations in both systems.
    #[getter]
    fn vertical_points(&self) -> usize {
        self.inner.vertical_points
    }

    /// RMS of vertical residuals, if any point has elevations.
    #[getter]
    fn rms_vertical(&self) -> Option<f64> {
        self.inner.rms_vertical
    }
}

fn xy_rows(name: &str, a: &PyArrayLike2<'_, f64, AllowTypeChange>) -> PyResult<Vec<[f64; 2]>> {
    let view = a.as_array();
    if view.ncols() != 2 {
        return Err(CoordinateError::new_err(format!(
            "{name} must have shape (n, 2), got {:?}",
            view.shape()
        )));
    }
    Ok(view.rows().into_iter().map(|r| [r[0], r[1]]).collect())
}

/// Fits a local grid to control points by least squares.
///
/// `base_xy` and `local_xy` are `n x 2` arrays of the same points. Give both
/// `base_z` and `local_z`, or neither. `scale=None` fits the scale (4
/// parameters); a number holds it (1.0 for a rigid grid). `z_offset=None` fits
/// the vertical offset from the elevations, or uses 0 when none are given.
#[pyfunction]
#[allow(clippy::needless_pass_by_value)]
#[pyo3(signature = (base_xy, local_xy, *, base_z = None, local_z = None, scale = None, z_offset = None))]
pub fn fit_local_grid(
    base_xy: PyArrayLike2<'_, f64, AllowTypeChange>,
    local_xy: PyArrayLike2<'_, f64, AllowTypeChange>,
    base_z: Option<Array<'_>>,
    local_z: Option<Array<'_>>,
    scale: Option<f64>,
    z_offset: Option<f64>,
) -> PyResult<PyGridFit> {
    let base = xy_rows("base_xy", &base_xy)?;
    let local = xy_rows("local_xy", &local_xy)?;
    if base.len() != local.len() {
        return Err(CoordinateError::new_err(format!(
            "base_xy has {} points but local_xy has {}",
            base.len(),
            local.len()
        )));
    }
    let elevations = match (base_z, local_z) {
        (None, None) => None,
        (Some(b), Some(l)) => {
            let (b, l) = (b.as_array().to_vec(), l.as_array().to_vec());
            if b.len() != base.len() || l.len() != base.len() {
                return Err(CoordinateError::new_err(
                    "base_z and local_z must have one value per control point",
                ));
            }
            Some((b, l))
        }
        _ => {
            return Err(CoordinateError::new_err(
                "give both base_z and local_z, or neither",
            ));
        }
    };
    let points: Vec<ControlPoint> = base
        .iter()
        .zip(&local)
        .enumerate()
        .map(|(i, (b, l))| ControlPoint {
            base: *b,
            local: *l,
            base_z: elevations.as_ref().map(|(bz, _)| bz[i]),
            local_z: elevations.as_ref().map(|(_, lz)| lz[i]),
        })
        .collect();
    let options = FitOptions {
        scale: scale.map_or(ScaleModel::Free, ScaleModel::Fixed),
        vertical: match (z_offset, &elevations) {
            (Some(v), _) => VerticalModel::Fixed(v),
            (None, Some(_)) => VerticalModel::FromPoints,
            (None, None) => VerticalModel::Fixed(0.0),
        },
    };
    mc_core::coords::fit_local_grid(&points, options)
        .map(|inner| PyGridFit { inner })
        .map_err(|e| coord_err(&e))
}

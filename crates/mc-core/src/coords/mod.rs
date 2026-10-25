//! Coordinate contracts: local mine grids — a shared MineCore contract.
//!
//! Map projections and datum transformations are delegated to PROJ in the
//! Python layer (ADR-0005). This module owns the part that PROJ does not model
//! and that every mine relies on: the **local mine grid**, a 2D similarity
//! transformation (origin, rotation, scale) plus a vertical offset relative to a
//! base projected CRS, and its least-squares fit from control points with
//! residuals.
//!
//! Changing this module is a contract change: it needs an ADR and tests that
//! prove stored local grids still transform identically.

mod fit;
mod local_grid;

use thiserror::Error;

pub use fit::{
    ControlPoint, FitOptions, GridFit, PointResidual, ScaleModel, VerticalModel, fit_local_grid,
};
pub use local_grid::{LocalGrid, LocalGridParams};

/// Errors from defining, fitting or applying a local grid.
#[derive(Debug, Clone, PartialEq, Error)]
pub enum CoordError {
    /// A transformation parameter is outside its valid range.
    #[error("invalid local grid parameter '{name}' = {value}: {reason}")]
    InvalidParameter {
        /// Parameter name.
        name: &'static str,
        /// The rejected value.
        value: f64,
        /// Why it was rejected.
        reason: &'static str,
    },
    /// Coordinate arrays of different lengths.
    #[error("coordinate arrays differ in length (x: {x}, y: {y}, z: {z:?})")]
    LengthMismatch {
        /// Length of the x array.
        x: usize,
        /// Length of the y array.
        y: usize,
        /// Length of the z array, if given.
        z: Option<usize>,
    },
    /// Fewer than two control points.
    #[error("a local grid fit needs at least 2 control points, got {0}")]
    NotEnoughControlPoints(usize),
    /// The control points cannot determine a rotation and scale.
    #[error("control points are degenerate: {0}")]
    DegenerateControlPoints(&'static str),
    /// A control point coordinate is NaN or infinite.
    #[error("control point {index} has a non-finite coordinate")]
    NonFiniteControlPoint {
        /// Zero-based index of the point.
        index: usize,
    },
    /// A control point has an elevation in only one of the two systems.
    #[error("control point {index} has an elevation in only one system")]
    PartialVerticalControl {
        /// Zero-based index of the point.
        index: usize,
    },
    /// The vertical offset was to be fitted, but no point has elevations.
    #[error("no control point has elevations in both systems; give the vertical offset explicitly")]
    NoVerticalControl,
}

//! Least-squares fit of a local grid from control points.
//!
//! The horizontal part is the closed-form 2D similarity (or rigid, with a fixed
//! scale) least-squares solution, computed on centroid-reduced coordinates so
//! that projected coordinates of millions of metres lose no precision. In
//! complex notation, with `p` and `q` the centred base and local points, the
//! fitted `s·e^(iθ) = Σ q·conj(p) / Σ |p|²`; a fixed scale keeps the same θ.

use super::CoordError;
use super::local_grid::{LocalGrid, LocalGridParams};

/// A point known in both the base CRS and the local grid.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ControlPoint {
    /// `(E, N)` in the base CRS.
    pub base: [f64; 2],
    /// `(x, y)` in the local grid.
    pub local: [f64; 2],
    /// Base elevation, if surveyed.
    pub base_z: Option<f64>,
    /// Local elevation, if known.
    pub local_z: Option<f64>,
}

/// How the scale factor is determined.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ScaleModel {
    /// Fitted with the rotation (4-parameter similarity).
    Free,
    /// Held at the given value (3-parameter fit; 1.0 for a rigid grid).
    Fixed(f64),
}

/// How the vertical offset is determined.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum VerticalModel {
    /// Mean of `local_z - base_z` over points with both elevations.
    FromPoints,
    /// Held at the given value; residuals are still reported.
    Fixed(f64),
}

/// Options for [`fit_local_grid`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FitOptions {
    /// Scale model.
    pub scale: ScaleModel,
    /// Vertical model.
    pub vertical: VerticalModel,
}

/// Misclosure at one control point: observed local minus fitted local.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PointResidual {
    /// Easting-axis residual, local units.
    pub dx: f64,
    /// Northing-axis residual, local units.
    pub dy: f64,
    /// Elevation residual, if the point has elevations.
    pub dz: Option<f64>,
}

impl PointResidual {
    /// Horizontal residual length.
    pub fn horizontal(&self) -> f64 {
        self.dx.hypot(self.dy)
    }
}

/// Result of a local grid fit, with its quality figures.
#[derive(Debug, Clone, PartialEq)]
pub struct GridFit {
    /// The fitted grid. Its origins are the control-point centroids.
    pub grid: LocalGrid,
    /// One residual per control point, in input order.
    pub residuals: Vec<PointResidual>,
    /// Root mean square of horizontal residual lengths.
    pub rms_horizontal: f64,
    /// Largest horizontal residual length.
    pub max_horizontal: f64,
    /// A-posteriori standard error of unit weight, `sqrt(Σr² / (2n - u))`;
    /// `None` when the fit has no redundancy.
    pub sigma0_horizontal: Option<f64>,
    /// Number of points with elevations in both systems.
    pub vertical_points: usize,
    /// RMS of vertical residuals, if any point has elevations.
    pub rms_vertical: Option<f64>,
}

/// Control points closer together than this (RMS distance from their centroid,
/// metres) cannot define a rotation.
const MIN_SPREAD: f64 = 1e-3;

/// Fits a local grid to control points.
///
/// # Errors
/// - [`CoordError::NotEnoughControlPoints`] for fewer than two points.
/// - [`CoordError::NonFiniteControlPoint`] / [`CoordError::PartialVerticalControl`]
///   for invalid points.
/// - [`CoordError::DegenerateControlPoints`] if the points coincide in either
///   system or define no rotation.
/// - [`CoordError::NoVerticalControl`] with [`VerticalModel::FromPoints`] and no
///   elevations.
/// - [`CoordError::InvalidParameter`] for a non-positive or non-finite fixed
///   scale or vertical offset.
#[allow(clippy::cast_precision_loss)] // point counts are far below 2^52
pub fn fit_local_grid(points: &[ControlPoint], options: FitOptions) -> Result<GridFit, CoordError> {
    let n = points.len();
    if n < 2 {
        return Err(CoordError::NotEnoughControlPoints(n));
    }
    for (index, p) in points.iter().enumerate() {
        let horizontal = [p.base[0], p.base[1], p.local[0], p.local[1]];
        let vertical = [p.base_z, p.local_z].into_iter().flatten();
        if !horizontal.into_iter().chain(vertical).all(f64::is_finite) {
            return Err(CoordError::NonFiniteControlPoint { index });
        }
        if p.base_z.is_some() != p.local_z.is_some() {
            return Err(CoordError::PartialVerticalControl { index });
        }
    }

    let nf = n as f64;
    let base_c = centroid(points.iter().map(|p| p.base));
    let local_c = centroid(points.iter().map(|p| p.local));
    let (mut spp, mut sqq, mut re, mut im) = (0.0, 0.0, 0.0, 0.0);
    for p in points {
        let (px, py) = (p.base[0] - base_c[0], p.base[1] - base_c[1]);
        let (qx, qy) = (p.local[0] - local_c[0], p.local[1] - local_c[1]);
        spp += px * px + py * py;
        sqq += qx * qx + qy * qy;
        re += qx * px + qy * py;
        im += qy * px - qx * py;
    }
    if (spp / nf).sqrt() < MIN_SPREAD {
        return Err(CoordError::DegenerateControlPoints(
            "base control points coincide",
        ));
    }
    if (sqq / nf).sqrt() < MIN_SPREAD {
        return Err(CoordError::DegenerateControlPoints(
            "local control points coincide",
        ));
    }
    let correlation = re.hypot(im);
    if correlation <= 1e-12 * (spp * sqq).sqrt() {
        return Err(CoordError::DegenerateControlPoints(
            "base and local points share no consistent rotation",
        ));
    }

    let scale = match options.scale {
        ScaleModel::Free => correlation / spp,
        ScaleModel::Fixed(s) => s,
    };
    let dz: Vec<f64> = points
        .iter()
        .filter_map(|p| Some(p.local_z? - p.base_z?))
        .collect();
    let z_offset = match options.vertical {
        VerticalModel::Fixed(v) => v,
        VerticalModel::FromPoints if dz.is_empty() => return Err(CoordError::NoVerticalControl),
        VerticalModel::FromPoints => {
            let first = dz[0];
            first + dz.iter().map(|d| d - first).sum::<f64>() / dz.len() as f64
        }
    };
    let grid = LocalGrid::new(LocalGridParams {
        base_origin: base_c,
        local_origin: local_c,
        rotation_deg: im.atan2(re).to_degrees(),
        scale,
        z_offset,
    })?;

    let residuals: Vec<PointResidual> = points
        .iter()
        .map(|p| {
            let [x, y] = grid.to_local_xy(p.base[0], p.base[1]);
            let dz = match (p.base_z, p.local_z) {
                (Some(bz), Some(lz)) => Some(lz - grid.to_local_z(bz)),
                _ => None,
            };
            PointResidual {
                dx: p.local[0] - x,
                dy: p.local[1] - y,
                dz,
            }
        })
        .collect();
    let sum_h: f64 = residuals.iter().map(|r| r.dx * r.dx + r.dy * r.dy).sum();
    let unknowns = match options.scale {
        ScaleModel::Free => 4,
        ScaleModel::Fixed(_) => 3,
    };
    let redundancy = 2 * n - unknowns;
    let vertical: Vec<f64> = residuals.iter().filter_map(|r| r.dz).collect();
    Ok(GridFit {
        grid,
        rms_horizontal: (sum_h / nf).sqrt(),
        max_horizontal: residuals
            .iter()
            .map(PointResidual::horizontal)
            .fold(0.0, f64::max),
        sigma0_horizontal: (redundancy > 0).then(|| (sum_h / redundancy as f64).sqrt()),
        vertical_points: vertical.len(),
        rms_vertical: (!vertical.is_empty())
            .then(|| (vertical.iter().map(|d| d * d).sum::<f64>() / vertical.len() as f64).sqrt()),
        residuals,
    })
}

/// Centroid computed relative to the first point, so large coordinates keep
/// full precision.
#[allow(clippy::cast_precision_loss)]
fn centroid(mut points: impl Iterator<Item = [f64; 2]>) -> [f64; 2] {
    let Some(first) = points.next() else {
        return [0.0, 0.0];
    };
    let (mut sx, mut sy, mut count) = (0.0, 0.0, 1usize);
    for [x, y] in points {
        sx += x - first[0];
        sy += y - first[1];
        count += 1;
    }
    [first[0] + sx / count as f64, first[1] + sy / count as f64]
}

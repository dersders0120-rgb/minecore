//! Local mine grid: 2D similarity transformation plus a vertical offset.

use super::CoordError;

/// Definition of a local mine grid relative to a base projected CRS.
///
/// Base coordinates `(E, N, Z)` map to local coordinates `(x, y, z)` by
///
/// ```text
/// dE = E - E0,  dN = N - N0
/// x  = x0 + s (dE cos θ - dN sin θ)
/// y  = y0 + s (dE sin θ + dN cos θ)
/// z  = Z + dz
/// ```
///
/// where θ (`rotation_deg`) is the azimuth of local grid north, measured
/// clockwise from base grid north, and s (`scale`) is local length per base
/// length. With θ = 10°, local north points 10° east of base grid north.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LocalGridParams {
    /// `(E0, N0)`: a point in the base CRS.
    pub base_origin: [f64; 2],
    /// `(x0, y0)`: the same point in local grid coordinates.
    pub local_origin: [f64; 2],
    /// Azimuth of local grid north, clockwise from base grid north, degrees.
    pub rotation_deg: f64,
    /// Local length per base length; 1 for a rigid grid.
    pub scale: f64,
    /// Added to base elevations to give local elevations.
    pub z_offset: f64,
}

/// A validated local mine grid, ready to transform coordinates both ways.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LocalGrid {
    params: LocalGridParams,
    /// `s cos θ`.
    a: f64,
    /// `s sin θ`.
    b: f64,
}

/// Largest accepted |rotation|; larger values are almost always unit mistakes.
const MAX_ROTATION_DEG: f64 = 360.0;

fn finite(name: &'static str, value: f64) -> Result<(), CoordError> {
    if value.is_finite() {
        Ok(())
    } else {
        Err(CoordError::InvalidParameter {
            name,
            value,
            reason: "must be finite",
        })
    }
}

impl LocalGrid {
    /// Validates the parameters.
    ///
    /// # Errors
    /// [`CoordError::InvalidParameter`] if a value is not finite, the scale is
    /// not positive, or |rotation| exceeds 360°.
    pub fn new(params: LocalGridParams) -> Result<Self, CoordError> {
        finite("base_origin.e", params.base_origin[0])?;
        finite("base_origin.n", params.base_origin[1])?;
        finite("local_origin.x", params.local_origin[0])?;
        finite("local_origin.y", params.local_origin[1])?;
        finite("rotation_deg", params.rotation_deg)?;
        finite("scale", params.scale)?;
        finite("z_offset", params.z_offset)?;
        if params.scale <= 0.0 {
            return Err(CoordError::InvalidParameter {
                name: "scale",
                value: params.scale,
                reason: "must be positive",
            });
        }
        if params.rotation_deg.abs() > MAX_ROTATION_DEG {
            return Err(CoordError::InvalidParameter {
                name: "rotation_deg",
                value: params.rotation_deg,
                reason: "must be within [-360, 360] degrees",
            });
        }
        let (sin, cos) = params.rotation_deg.to_radians().sin_cos();
        Ok(Self {
            params,
            a: params.scale * cos,
            b: params.scale * sin,
        })
    }

    /// The defining parameters, exactly as given.
    pub const fn params(&self) -> LocalGridParams {
        self.params
    }

    /// Base `(E, N)` to local `(x, y)`.
    pub fn to_local_xy(&self, e: f64, n: f64) -> [f64; 2] {
        let de = e - self.params.base_origin[0];
        let dn = n - self.params.base_origin[1];
        [
            self.params.local_origin[0] + (self.a * de - self.b * dn),
            self.params.local_origin[1] + (self.b * de + self.a * dn),
        ]
    }

    /// Local `(x, y)` to base `(E, N)`.
    pub fn to_base_xy(&self, x: f64, y: f64) -> [f64; 2] {
        let dx = x - self.params.local_origin[0];
        let dy = y - self.params.local_origin[1];
        let s2 = self.a * self.a + self.b * self.b;
        [
            self.params.base_origin[0] + (self.a * dx + self.b * dy) / s2,
            self.params.base_origin[1] + (self.a * dy - self.b * dx) / s2,
        ]
    }

    /// Base elevation to local elevation.
    pub fn to_local_z(&self, z: f64) -> f64 {
        z + self.params.z_offset
    }

    /// Local elevation to base elevation.
    pub fn to_base_z(&self, z: f64) -> f64 {
        z - self.params.z_offset
    }

    /// Transforms base coordinates to local coordinates in place.
    ///
    /// # Errors
    /// [`CoordError::LengthMismatch`] if the slices differ in length; the data
    /// is left untouched.
    pub fn to_local_in_place(
        &self,
        x: &mut [f64],
        y: &mut [f64],
        z: Option<&mut [f64]>,
    ) -> Result<(), CoordError> {
        check_lengths(x, y, z.as_deref())?;
        for (xi, yi) in x.iter_mut().zip(y.iter_mut()) {
            [*xi, *yi] = self.to_local_xy(*xi, *yi);
        }
        if let Some(z) = z {
            for zi in z.iter_mut() {
                *zi = self.to_local_z(*zi);
            }
        }
        Ok(())
    }

    /// Transforms local coordinates to base coordinates in place.
    ///
    /// # Errors
    /// [`CoordError::LengthMismatch`] if the slices differ in length; the data
    /// is left untouched.
    pub fn to_base_in_place(
        &self,
        x: &mut [f64],
        y: &mut [f64],
        z: Option<&mut [f64]>,
    ) -> Result<(), CoordError> {
        check_lengths(x, y, z.as_deref())?;
        for (xi, yi) in x.iter_mut().zip(y.iter_mut()) {
            [*xi, *yi] = self.to_base_xy(*xi, *yi);
        }
        if let Some(z) = z {
            for zi in z.iter_mut() {
                *zi = self.to_base_z(*zi);
            }
        }
        Ok(())
    }
}

fn check_lengths(x: &[f64], y: &[f64], z: Option<&[f64]>) -> Result<(), CoordError> {
    let zl = z.map(<[f64]>::len);
    if x.len() == y.len() && zl.is_none_or(|n| n == x.len()) {
        Ok(())
    } else {
        Err(CoordError::LengthMismatch {
            x: x.len(),
            y: y.len(),
            z: zl,
        })
    }
}

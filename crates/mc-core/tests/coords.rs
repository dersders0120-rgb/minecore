//! Local mine grid contract tests.

use mc_core::coords::{
    ControlPoint, CoordError, FitOptions, LocalGrid, LocalGridParams, ScaleModel, VerticalModel,
    fit_local_grid,
};
use proptest::prelude::*;

const E0: f64 = 512_345.678;
const N0: f64 = 4_423_456.789;

fn grid(rotation_deg: f64, scale: f64) -> LocalGrid {
    LocalGrid::new(LocalGridParams {
        base_origin: [E0, N0],
        local_origin: [10_000.0, 50_000.0],
        rotation_deg,
        scale,
        z_offset: 1_000.0,
    })
    .unwrap()
}

fn close(a: [f64; 2], b: [f64; 2], tol: f64) -> bool {
    (a[0] - b[0]).hypot(a[1] - b[1]) <= tol
}

const FREE: FitOptions = FitOptions {
    scale: ScaleModel::Free,
    vertical: VerticalModel::Fixed(0.0),
};

/// Control points generated from a known grid, with optional per-point noise.
fn control(truth: &LocalGrid, base: &[[f64; 2]], noise: &[[f64; 2]]) -> Vec<ControlPoint> {
    base.iter()
        .zip(noise.iter().chain(std::iter::repeat(&[0.0, 0.0])))
        .map(|(b, e)| {
            let [x, y] = truth.to_local_xy(b[0], b[1]);
            ControlPoint {
                base: *b,
                local: [x + e[0], y + e[1]],
                base_z: None,
                local_z: None,
            }
        })
        .collect()
}

const SITE: [[f64; 2]; 6] = [
    [E0, N0],
    [E0 + 812.0, N0 + 95.0],
    [E0 + 1_430.5, N0 - 1_210.0],
    [E0 - 640.25, N0 - 905.5],
    [E0 + 205.0, N0 + 1_533.0],
    [E0 - 1_120.0, N0 + 702.0],
];

// --- definition and convention -------------------------------------------------

#[test]
fn origin_maps_to_origin() {
    let g = grid(23.5, 0.9996);
    assert!(close(g.to_local_xy(E0, N0), [10_000.0, 50_000.0], 1e-9));
    assert!(close(g.to_base_xy(10_000.0, 50_000.0), [E0, N0], 1e-9));
}

#[test]
fn rotation_is_azimuth_of_local_north_clockwise() {
    // Local north points 90 degrees east: a point due east in the base CRS lies
    // on the local +y axis.
    let g = grid(90.0, 1.0);
    assert!(close(
        g.to_local_xy(E0 + 10.0, N0),
        [10_000.0, 50_010.0],
        1e-9
    ));
    // With 10 degrees, a point 100 m from the origin at base azimuth 10 degrees
    // lies on the local north axis, 100 m * scale away.
    let g = grid(10.0, 1.5);
    let az = 10f64.to_radians();
    let p = g.to_local_xy(E0 + 100.0 * az.sin(), N0 + 100.0 * az.cos());
    assert!(close(p, [10_000.0, 50_150.0], 1e-9), "{p:?}");
}

#[test]
fn elevations_shift_by_offset() {
    let g = grid(0.0, 1.0);
    assert!((g.to_local_z(812.5) - 1_812.5).abs() < 1e-12);
    assert!((g.to_base_z(1_812.5) - 812.5).abs() < 1e-12);
}

#[test]
fn invalid_parameters_are_rejected() {
    let ok = grid(0.0, 1.0).params();
    for (params, name) in [
        (LocalGridParams { scale: 0.0, ..ok }, "scale"),
        (LocalGridParams { scale: -1.0, ..ok }, "scale"),
        (
            LocalGridParams {
                scale: f64::NAN,
                ..ok
            },
            "scale",
        ),
        (
            LocalGridParams {
                rotation_deg: 400.0,
                ..ok
            },
            "rotation_deg",
        ),
        (
            LocalGridParams {
                base_origin: [f64::INFINITY, N0],
                ..ok
            },
            "base_origin.e",
        ),
        (
            LocalGridParams {
                z_offset: f64::NAN,
                ..ok
            },
            "z_offset",
        ),
    ] {
        match LocalGrid::new(params) {
            Err(CoordError::InvalidParameter { name: got, .. }) => assert_eq!(got, name),
            other => panic!("{name}: {other:?}"),
        }
    }
}

#[test]
fn in_place_transforms_round_trip_and_check_lengths() {
    let g = grid(-37.25, 1.000_25);
    let (mut x, mut y, mut z) = (
        vec![E0, E0 + 500.0],
        vec![N0, N0 - 250.0],
        vec![100.0, 200.0],
    );
    g.to_local_in_place(&mut x, &mut y, Some(&mut z)).unwrap();
    g.to_base_in_place(&mut x, &mut y, Some(&mut z)).unwrap();
    assert!(close([x[1], y[1]], [E0 + 500.0, N0 - 250.0], 1e-8));
    assert!((z[1] - 200.0).abs() < 1e-12);

    let mut short = vec![1.0];
    let err = g.to_local_in_place(&mut x, &mut short, None).unwrap_err();
    assert_eq!(
        err,
        CoordError::LengthMismatch {
            x: 2,
            y: 1,
            z: None
        }
    );
    assert_eq!(short, vec![1.0], "data must be untouched on error");
}

proptest! {
    #[test]
    fn round_trip_is_sub_micrometre(
        rotation in -360.0f64..360.0,
        scale in 0.5f64..2.0,
        de in -20_000.0f64..20_000.0,
        dn in -20_000.0f64..20_000.0,
    ) {
        let g = grid(rotation, scale);
        let local = g.to_local_xy(E0 + de, N0 + dn);
        let back = g.to_base_xy(local[0], local[1]);
        prop_assert!(close(back, [E0 + de, N0 + dn], 1e-7), "{back:?}");
    }

    #[test]
    fn distances_scale_and_angles_are_preserved(
        rotation in -180.0f64..180.0,
        scale in 0.5f64..2.0,
        a in (-5_000.0f64..5_000.0, -5_000.0f64..5_000.0),
        b in (-5_000.0f64..5_000.0, -5_000.0f64..5_000.0),
    ) {
        let g = grid(rotation, scale);
        let pa = g.to_local_xy(E0 + a.0, N0 + a.1);
        let pb = g.to_local_xy(E0 + b.0, N0 + b.1);
        let base = (a.0 - b.0).hypot(a.1 - b.1);
        let local = (pa[0] - pb[0]).hypot(pa[1] - pb[1]);
        prop_assert!((local - scale * base).abs() <= 1e-8 * (1.0 + base));
    }
}

// --- fitting -------------------------------------------------------------------

#[test]
fn two_points_fit_exactly() {
    let truth = grid(12.345, 1.000_4);
    let fit = fit_local_grid(&control(&truth, &SITE[..2], &[]), FREE).unwrap();
    assert!(fit.max_horizontal < 1e-8, "{}", fit.max_horizontal);
    assert_eq!(
        fit.sigma0_horizontal, None,
        "no redundancy with 2 points and 4 unknowns"
    );
    let p = fit.grid.params();
    assert!((p.rotation_deg - 12.345).abs() < 1e-9);
    assert!((p.scale - 1.000_4).abs() < 1e-12);
}

#[test]
fn noise_free_fit_recovers_the_transformation() {
    let truth = grid(-71.5, 0.999_6);
    let fit = fit_local_grid(&control(&truth, &SITE, &[]), FREE).unwrap();
    assert!(fit.max_horizontal < 1e-8);
    for probe in [[E0 + 3_000.0, N0 - 4_000.0], [E0 - 9_000.0, N0 + 7_500.0]] {
        let expect = truth.to_local_xy(probe[0], probe[1]);
        let got = fit.grid.to_local_xy(probe[0], probe[1]);
        assert!(close(got, expect, 1e-7), "{got:?} vs {expect:?}");
    }
}

#[test]
fn noisy_fit_is_a_least_squares_minimum() {
    let truth = grid(33.0, 1.0);
    let noise = [
        [0.004, -0.002],
        [-0.003, 0.005],
        [0.001, 0.001],
        [-0.004, -0.003],
        [0.002, 0.0],
        [0.0, -0.002],
    ];
    let pts = control(&truth, &SITE, &noise);
    let fit = fit_local_grid(&pts, FREE).unwrap();
    let sigma0 = fit.sigma0_horizontal.unwrap();
    assert!(fit.rms_horizontal > 0.0 && fit.rms_horizontal < 0.006);
    // sigma0 = sqrt(SSR / (2n - 4)) and rms = sqrt(SSR / n), n = 6.
    assert!((sigma0 - fit.rms_horizontal * (6.0f64 / 8.0).sqrt()).abs() < 1e-15);

    // Independent optimality check: perturbing any parameter increases the
    // sum of squared residuals.
    let ssr = |g: &LocalGrid| -> f64 {
        pts.iter()
            .map(|p| {
                let [x, y] = g.to_local_xy(p.base[0], p.base[1]);
                (p.local[0] - x).powi(2) + (p.local[1] - y).powi(2)
            })
            .sum()
    };
    let best = ssr(&fit.grid);
    let p = fit.grid.params();
    for perturbed in [
        LocalGridParams {
            rotation_deg: p.rotation_deg + 1e-6,
            ..p
        },
        LocalGridParams {
            rotation_deg: p.rotation_deg - 1e-6,
            ..p
        },
        LocalGridParams {
            scale: p.scale * (1.0 + 1e-7),
            ..p
        },
        LocalGridParams {
            scale: p.scale * (1.0 - 1e-7),
            ..p
        },
        LocalGridParams {
            local_origin: [p.local_origin[0] + 1e-4, p.local_origin[1]],
            ..p
        },
        LocalGridParams {
            local_origin: [p.local_origin[0], p.local_origin[1] - 1e-4],
            ..p
        },
    ] {
        assert!(ssr(&LocalGrid::new(perturbed).unwrap()) > best);
    }
}

#[test]
fn fixed_scale_fit_keeps_scale_and_shows_misfit() {
    let truth = grid(5.0, 1.000_2);
    let rigid = FitOptions {
        scale: ScaleModel::Fixed(1.0),
        vertical: VerticalModel::Fixed(0.0),
    };
    let fit = fit_local_grid(&control(&truth, &SITE, &[]), rigid).unwrap();
    let p = fit.grid.params();
    assert!((p.scale - 1.0).abs() < f64::EPSILON);
    assert!(
        (p.rotation_deg - 5.0).abs() < 1e-9,
        "rotation is unaffected by the scale model"
    );
    // 200 ppm over ~1 km of spread leaves decimetre-level residuals.
    assert!(
        fit.max_horizontal > 0.05 && fit.max_horizontal < 0.5,
        "{}",
        fit.max_horizontal
    );
}

#[test]
fn vertical_offset_from_points_and_fixed() {
    let truth = grid(0.0, 1.0);
    let mut pts = control(&truth, &SITE[..3], &[]);
    for (p, (bz, lz)) in
        pts.iter_mut()
            .zip([(100.0, 1_100.02), (250.0, 1_249.99), (80.0, 1_079.99)])
    {
        p.base_z = Some(bz);
        p.local_z = Some(lz);
    }
    let fit = fit_local_grid(
        &pts,
        FitOptions {
            scale: ScaleModel::Free,
            vertical: VerticalModel::FromPoints,
        },
    )
    .unwrap();
    assert!((fit.grid.params().z_offset - 1_000.0).abs() < 1e-9);
    assert_eq!(fit.vertical_points, 3);
    let dz: Vec<f64> = fit.residuals.iter().map(|r| r.dz.unwrap()).collect();
    assert!((dz[0] - 0.02).abs() < 1e-9 && (dz[1] + 0.01).abs() < 1e-9);
    assert!(fit.rms_vertical.unwrap() > 0.0);

    let fixed = fit_local_grid(
        &pts,
        FitOptions {
            scale: ScaleModel::Free,
            vertical: VerticalModel::Fixed(999.0),
        },
    )
    .unwrap();
    assert!((fixed.residuals[0].dz.unwrap() - 1.02).abs() < 1e-9);
}

#[test]
fn invalid_control_is_rejected() {
    let truth = grid(0.0, 1.0);
    let pts = control(&truth, &SITE, &[]);
    let from_points = FitOptions {
        scale: ScaleModel::Free,
        vertical: VerticalModel::FromPoints,
    };

    assert_eq!(
        fit_local_grid(&pts[..1], FREE).unwrap_err(),
        CoordError::NotEnoughControlPoints(1)
    );
    assert_eq!(
        fit_local_grid(&pts, from_points).unwrap_err(),
        CoordError::NoVerticalControl
    );

    let mut partial = pts.clone();
    partial[2].base_z = Some(10.0);
    assert_eq!(
        fit_local_grid(&partial, FREE).unwrap_err(),
        CoordError::PartialVerticalControl { index: 2 }
    );

    let mut nan = pts.clone();
    nan[4].local[1] = f64::NAN;
    assert_eq!(
        fit_local_grid(&nan, FREE).unwrap_err(),
        CoordError::NonFiniteControlPoint { index: 4 }
    );

    let mut same = pts[..2].to_vec();
    same[1].base = same[0].base;
    assert!(matches!(
        fit_local_grid(&same, FREE),
        Err(CoordError::DegenerateControlPoints(_))
    ));
    let mut same_local = pts[..2].to_vec();
    same_local[1].local = same_local[0].local;
    assert!(matches!(
        fit_local_grid(&same_local, FREE),
        Err(CoordError::DegenerateControlPoints(_))
    ));

    let bad_scale = FitOptions {
        scale: ScaleModel::Fixed(0.0),
        vertical: VerticalModel::Fixed(0.0),
    };
    assert!(matches!(
        fit_local_grid(&pts, bad_scale),
        Err(CoordError::InvalidParameter { name: "scale", .. })
    ));
}

proptest! {
    #[test]
    fn fit_recovers_any_similarity(
        rotation in -180.0f64..180.0,
        scale in 0.9f64..1.1,
        offsets in prop::collection::vec((-3_000.0f64..3_000.0, -3_000.0f64..3_000.0), 3..12),
    ) {
        let truth = grid(rotation, scale);
        let base: Vec<[f64; 2]> = offsets.iter().map(|(de, dn)| [E0 + de, N0 + dn]).collect();
        let spread = base.iter().map(|b| (b[0] - base[0][0]).hypot(b[1] - base[0][1])).fold(0.0, f64::max);
        prop_assume!(spread > 10.0);
        let fit = fit_local_grid(&control(&truth, &base, &[]), FREE).unwrap();
        prop_assert!(fit.max_horizontal < 1e-6, "{}", fit.max_horizontal);
        let probe = [E0 + 1_234.5, N0 - 2_345.6];
        let expect = truth.to_local_xy(probe[0], probe[1]);
        prop_assert!(close(fit.grid.to_local_xy(probe[0], probe[1]), expect, 1e-5));
    }
}

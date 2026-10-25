//! MineCore core engine.
//!
//! This crate owns the shared engineering contracts of MineCore: units,
//! coordinate reference handling, the geometry kernel, the project data model
//! and the numerical kernels. It has no GUI and no Python dependency, so every
//! capability can be exercised headless from tests and the command line.
//!
//! Dependency rule: domain modules and the desktop workbench depend on this
//! crate; this crate never depends on them.

pub mod units;

/// Version of the core engine, taken from the workspace manifest.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Compilation profile of the running core engine.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuildProfile {
    /// Built with debug assertions (development build).
    Debug,
    /// Built without debug assertions (optimised build).
    Release,
}

impl BuildProfile {
    /// Lower-case name used in diagnostics output.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Debug => "debug",
            Self::Release => "release",
        }
    }
}

/// Identification of the running core engine, reported by `minecore info`
/// and recorded in project operation logs for reproducibility.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BuildInfo {
    /// Semantic version of the core engine.
    pub version: &'static str,
    /// Compilation profile.
    pub profile: BuildProfile,
}

/// Returns the identification of the running core engine.
pub const fn build_info() -> BuildInfo {
    BuildInfo {
        version: VERSION,
        profile: if cfg!(debug_assertions) {
            BuildProfile::Debug
        } else {
            BuildProfile::Release
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_matches_manifest() {
        assert_eq!(build_info().version, env!("CARGO_PKG_VERSION"));
        assert!(!VERSION.is_empty());
    }

    #[test]
    fn profile_matches_compilation_flags() {
        let expected = if cfg!(debug_assertions) {
            "debug"
        } else {
            "release"
        };
        assert_eq!(build_info().profile.as_str(), expected);
    }
}

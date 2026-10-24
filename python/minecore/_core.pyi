"""Type stubs for the native extension built from crates/mc-py."""

def core_version() -> str:
    """Return the version of the Rust core engine."""

def core_build_profile() -> str:
    """Return the compilation profile of the Rust core engine ("debug" or "release")."""

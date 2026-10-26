"""Unit tests for ``scripts.applets_manifest``."""

from __future__ import annotations

from pathlib import Path

from scripts.applets_manifest import (
    APPLET_MANIFEST,
    BUSYBOX_SOURCES,
    ENABLED_CONFIGS,
    EXTRA_ENABLED_CONFIGS,
    NUMERIC_CONFIG_DEFAULTS,
    STRING_CONFIG_DEFAULTS,
    AppletSpec,
    get_sorted_applets,
)


def test_applet_manifest_non_empty_and_unique() -> None:
    """Verify that the applet manifest contains unique, well-formed entries."""
    assert len(APPLET_MANIFEST) >= 135
    names = [spec.name for spec in APPLET_MANIFEST]
    assert len(names) == len(set(names))
    for spec in APPLET_MANIFEST:
        assert isinstance(spec, AppletSpec)
        assert spec.name
        assert spec.main_fn
        assert spec.dir_enum.startswith("BB_DIR_")
        assert spec.suid_enum.startswith("BB_SUID_")
        assert spec.macro in {
            "APPLET",
            "APPLET_NOFORK",
            "APPLET_NOEXEC",
            "APPLET_ODDNAME",
        }
        assert spec.config_symbol in ENABLED_CONFIGS


def test_get_sorted_applets_is_strictly_sorted() -> None:
    """Verify that ``get_sorted_applets`` returns applets in ASCII order."""
    sorted_applets = get_sorted_applets()
    names = [spec.name for spec in sorted_applets]
    assert names == sorted(names)
    assert list(APPLET_MANIFEST) == sorted_applets


def test_enabled_configs_and_defaults() -> None:
    """Verify enabled config symbols, numeric defaults, and string defaults."""
    assert EXTRA_ENABLED_CONFIGS.issubset(ENABLED_CONFIGS)
    assert "ZIP" not in ENABLED_CONFIGS
    assert NUMERIC_CONFIG_DEFAULTS["GZIP_FAST"] == 0
    assert NUMERIC_CONFIG_DEFAULTS["FEATURE_KILL_DELAY"] == 0
    assert NUMERIC_CONFIG_DEFAULTS["FEATURE_VI_MAX_LEN"] == 4096
    assert STRING_CONFIG_DEFAULTS["UNAME_OSNAME"] == "GNU/Linux"


def test_busybox_sources_unique_and_sorted(tmp_path: Path) -> None:
    """Verify that ``BUSYBOX_SOURCES`` has zero duplicates and valid paths.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    assert len(BUSYBOX_SOURCES) == len(set(BUSYBOX_SOURCES))
    assert list(BUSYBOX_SOURCES) == sorted(BUSYBOX_SOURCES)
    for rel_src in BUSYBOX_SOURCES:
        assert rel_src.endswith(".c")
        assert not Path(rel_src).is_absolute()
    assert not (tmp_path / "nonexistent_busybox").is_dir()

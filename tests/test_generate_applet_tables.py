"""Unit tests for ``scripts.generate_applet_tables``."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from scripts.applets_manifest import AppletSpec
from scripts.generate_applet_tables import (
    compute_applet_flag_bytes,
    generate_headers,
    main,
    sanitize_applet_identifier,
)


def test_sanitize_applet_identifier() -> None:
    """Verify C identifier sanitization for standard and symbol applet names."""
    assert sanitize_applet_identifier("ash") == "ash"
    assert sanitize_applet_identifier("sha256sum") == "sha256sum"
    assert sanitize_applet_identifier("[") == ""
    assert sanitize_applet_identifier("[[") == ""


def test_compute_applet_flag_bytes() -> None:
    """Verify 2-bit flag packing for NOFORK, NOEXEC, and standard applets."""
    specs = [
        AppletSpec("a", "a", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET", "A"),
        AppletSpec("b", "b", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK", "B"),
        AppletSpec("c", "c", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC", "C"),
        AppletSpec("d", "d", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME", "D"),
        AppletSpec("e", "e", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK", "E"),
    ]
    packed = compute_applet_flag_bytes(specs)
    assert packed == ["0x24", "0x01"]

    assert compute_applet_flag_bytes(specs[:1]) == ["0x00"]
    assert compute_applet_flag_bytes(specs[:2]) == ["0x04"]
    assert compute_applet_flag_bytes(specs[:3]) == ["0x24"]


def test_generate_headers_and_cli_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify ``generate_headers`` and ``main`` CLI success and error paths.

    Args:
        tmp_path: Pytest temporary directory fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    assert main([]) == 1
    captured_out = capsys.readouterr().out
    assert "Usage:" in captured_out

    out_dir = tmp_path / "gen_inc"
    rc = main([str(out_dir)])
    assert rc == 0
    captured_out = capsys.readouterr().out
    assert "Generated applets headers" in captured_out

    for fname in (
        "NUM_APPLETS.h",
        "applets.h",
        "applet_tables.h",
        "usage_compressed.h",
        "common_bufsiz.h",
        "bbconfigopts.h",
    ):
        assert (out_dir / fname).is_file()

    custom_specs = [
        AppletSpec(
            "[", "test", "BB_DIR_USR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME", "TEST1", "EXPR ]"
        ),
        AppletSpec("cat", "cat", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET", "CAT", "FILE"),
    ]
    count = generate_headers(out_dir, custom_specs)
    assert count == 2
    applets_h = (out_dir / "applets.h").read_text(encoding="utf-8")
    assert "IF_TEST1(APPLET_ODDNAME([, test, BB_DIR_USR_BIN, BB_SUID_DROP))" in applets_h
    assert "IF_CAT(APPLET(cat, BB_DIR_BIN, BB_SUID_DROP))" in applets_h

    old_argv = sys.argv
    sys.argv = ["generate_applet_tables.py"]
    try:
        script_path = (
            Path(__file__).resolve().parent.parent / "scripts" / "generate_applet_tables.py"
        )
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(script_path), run_name="__main__")
        assert exc_info.value.code == 1
    finally:
        sys.argv = old_argv
    capsys.readouterr()

"""Unit tests for ``scripts.generate_autoconf``."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from scripts.generate_autoconf import (
    generate_autoconf,
    main,
    render_autoconf_header,
    scan_config_symbols,
)


def test_scan_config_symbols_and_errors(tmp_path: Path) -> None:
    """Verify symbol scanning across directories and error handling on bad paths.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    missing_dir = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        scan_config_symbols(missing_dir)

    nl = chr(10)
    src_root = tmp_path / "busybox_mock"
    src_root.mkdir()
    (src_root / ".git").mkdir()
    (src_root / ".git" / "ignored.c").write_text(
        f"#if ENABLE_SHOULD_NOT_SCAN{nl}", encoding="utf-8"
    )
    (src_root / "notes.txt").write_text(f"CONFIG_TEXT_IGNORED{nl}", encoding="utf-8")
    (src_root / "Config.in").write_text(f"CONFIG_FROM_CONFIG_IN{nl}", encoding="utf-8")

    core_dir = src_root / "coreutils"
    core_dir.mkdir()
    (core_dir / "cat.c").write_text(
        nl.join(
            [
                "//config:config CAT",
                '//config:\tbool "cat"',
                "#if ENABLE_CAT && !defined(CONFIG_FEATURE_CATN)",
                "IF_FEATURE_CATV(int v;)",
                "IF_NOT_LOCALE_SUPPORT(int c;)",
                "#endif",
            ]
        )
        + nl,
        encoding="utf-8",
    )

    syms = scan_config_symbols(src_root)
    assert "SHOULD_NOT_SCAN" not in syms
    assert "TEXT_IGNORED" not in syms
    assert {
        "FROM_CONFIG_IN",
        "CAT",
        "FEATURE_CATN",
        "FEATURE_CATV",
        "LOCALE_SUPPORT",
    }.issubset(syms)


def test_render_autoconf_header_branches() -> None:
    """Verify boolean, numeric (zero and non-zero), string, and skipped symbols."""
    discovered = {
        "CAT",
        "DISABLED_APP",
        "NUM_ZERO",
        "NUM_NONZERO",
        "STR_OPT",
        "NOMMU",
        "BUSYBOX_EXEC_PATH",
        "0",
    }
    enabled = frozenset({"CAT"})
    numeric = {"NUM_ZERO": 0, "NUM_NONZERO": 128}
    strings = {"STR_OPT": "custom_val"}

    header = render_autoconf_header(
        discovered_symbols=discovered,
        enabled_configs=enabled,
        numeric_defaults=numeric,
        string_defaults=strings,
    )

    assert "#define CONFIG_CAT 1" in header
    assert "#define ENABLE_CAT 1" in header
    assert "#define IF_CAT(...) __VA_ARGS__" in header
    assert "#define IF_NOT_CAT(...)" in header

    assert "#define CONFIG_DISABLED_APP 0" in header
    assert "#define ENABLE_DISABLED_APP 0" in header
    assert "#define IF_DISABLED_APP(...)" in header
    assert "#define IF_NOT_DISABLED_APP(...) __VA_ARGS__" in header

    assert "#define CONFIG_NUM_ZERO 0" in header
    assert "#define ENABLE_NUM_ZERO 0" in header
    assert "#define IF_NUM_ZERO(...)" in header
    assert "#define IF_NOT_NUM_ZERO(...) __VA_ARGS__" in header

    assert "#define CONFIG_NUM_NONZERO 128" in header
    assert "#define ENABLE_NUM_NONZERO 1" in header
    assert "#define IF_NUM_NONZERO(...) __VA_ARGS__" in header
    assert "#define IF_NOT_NUM_NONZERO(...)" in header

    assert '#define CONFIG_STR_OPT "custom_val"' in header
    assert "#define ENABLE_STR_OPT 1" in header
    assert "#define IF_STR_OPT(...) __VA_ARGS__" in header
    assert "#define IF_NOT_STR_OPT(...)" in header


def test_generate_autoconf_and_cli_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify ``generate_autoconf`` file writing and CLI ``main`` entry point.

    Args:
        tmp_path: Pytest temporary directory fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    assert main([]) == 1
    assert "Usage:" in capsys.readouterr().out

    missing_src = tmp_path / "missing_busybox"
    out_file = tmp_path / "out" / "autoconf.h"
    assert main([str(missing_src), str(out_file)]) == 1
    assert "Error:" in capsys.readouterr().err

    nl = chr(10)
    mock_bb = tmp_path / "mock_bb"
    mock_bb.mkdir()
    (mock_bb / "echo.c").write_text(
        f"//config:config ECHO{nl}#if ENABLE_ECHO{nl}#endif{nl}", encoding="utf-8"
    )
    assert main([str(mock_bb), str(out_file)]) == 0
    assert "Generated" in capsys.readouterr().out
    assert out_file.is_file()

    count = generate_autoconf(mock_bb, out_file)
    assert count >= 1

    old_argv = sys.argv
    sys.argv = ["generate_autoconf.py"]
    try:
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "generate_autoconf.py"
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(script_path), run_name="__main__")
        assert exc_info.value.code == 1
    finally:
        sys.argv = old_argv
    capsys.readouterr()

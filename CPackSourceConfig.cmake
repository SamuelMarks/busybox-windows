# =============================================================================
# CPackSourceConfig.cmake - Source Archive Configuration for BusyBox Windows
#
# Configures CPack source packaging (ZIP and TGZ) and excludes build trees,
# VCS metadata, and local IDE artifacts.
#
# Parameters / Variables Set:
#   CPACK_SOURCE_GENERATOR         - Source archive formats ("ZIP;TGZ").
#   CPACK_SOURCE_PACKAGE_FILE_NAME - Base filename for source archives.
#   CPACK_SOURCE_IGNORE_FILES      - Regex patterns excluded from source archive.
# =============================================================================

set(CPACK_SOURCE_GENERATOR "ZIP;TGZ")
if(DEFINED PROJECT_VERSION AND NOT "${PROJECT_VERSION}" STREQUAL "")
    set(CPACK_SOURCE_PACKAGE_FILE_NAME "BusyBox-${PROJECT_VERSION}-src")
else()
    set(CPACK_SOURCE_PACKAGE_FILE_NAME "BusyBox-1.39.0-src")
endif()
set(CPACK_SOURCE_IGNORE_FILES
    "/\\.git/"
    "/auto-win-msvc/"
    "/busybox/"
    "/\\.vagrant/"
    "/\\.watcom/"
    "/build.*/"
    "/_CPack_Packages/"
    "/\\.vscode/"
    "/\\.idea/"
    "/__pycache__/"
    "/\\.(pytest|mypy|ruff)_cache/"
    "/\\.coverage$"
    "/\\.DS_Store$"
)

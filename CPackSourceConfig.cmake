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
set(CPACK_SOURCE_PACKAGE_FILE_NAME "BusyBox-1.39.0-src")
set(CPACK_SOURCE_IGNORE_FILES
    "/\\.git/"
    "/build.*/"
    "/\\.vscode/"
    "/\\.idea/"
    "/__pycache__/"
    "/\\.DS_Store"
)

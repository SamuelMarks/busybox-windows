# CPack Source configuration for BusyBox Windows
set(CPACK_SOURCE_GENERATOR "ZIP;TGZ")
set(CPACK_SOURCE_PACKAGE_FILE_NAME "BusyBox-1.39.0-src")
set(CPACK_SOURCE_IGNORE_FILES
    "/\.git/"
    "/build.*/"
    "/\.vscode/"
    "/\.idea/"
    "/__pycache__/"
    "/\.DS_Store"
)

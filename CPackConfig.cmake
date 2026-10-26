# =============================================================================
# CPackConfig.cmake - Binary Package Configuration for BusyBox Windows
#
# Configures CPack to produce standalone portable ZIP archives and NSIS
# Windows installers containing the compiled busybox executable and licenses.
#
# Parameters / Variables Set:
#   CPACK_PACKAGE_NAME                - Package identifier ("BusyBox").
#   CPACK_PACKAGE_VENDOR              - Vendor string ("BusyBox").
#   CPACK_PACKAGE_VERSION             - Full semantic version ("1.39.0").
#   CPACK_PACKAGE_DESCRIPTION_SUMMARY - Short package summary.
#   CPACK_GENERATOR                   - Binary generators ("ZIP;NSIS").
#   CPACK_NSIS_MODIFY_PATH            - Prompt user to add BusyBox to PATH.
# =============================================================================

set(CPACK_PACKAGE_NAME "BusyBox")
set(CPACK_PACKAGE_VENDOR "BusyBox")
set(CPACK_PACKAGE_VERSION "1.39.0")
set(CPACK_PACKAGE_VERSION_MAJOR "1")
set(CPACK_PACKAGE_VERSION_MINOR "39")
set(CPACK_PACKAGE_VERSION_PATCH "0")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "The Swiss Army Knife of Embedded Linux for native Windows.")
set(CPACK_PACKAGE_HOMEPAGE_URL "https://busybox.net/")
set(CPACK_PACKAGE_CONTACT "Samuel Marks")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "BusyBox")
set(CPACK_GENERATOR "ZIP;NSIS")

# NSIS Installer Settings
set(CPACK_NSIS_DISPLAY_NAME "BusyBox for Windows")
set(CPACK_NSIS_PACKAGE_NAME "BusyBox for Windows")
set(CPACK_NSIS_MODIFY_PATH ON)

# =============================================================================
# CPackConfig.cmake - Binary Package Configuration for BusyBox Windows
#
# Configures CPack to produce standalone portable ZIP archives, NSIS (.exe),
# and WiX (.msi) Windows installers containing the compiled busybox executable
# and licenses.
#
# Parameters / Variables Set:
#   CPACK_PACKAGE_NAME                - Package identifier ("BusyBox").
#   CPACK_PACKAGE_VENDOR              - Vendor string ("BusyBox").
#   CPACK_PACKAGE_VERSION             - Full semantic version ("1.39.0").
#   CPACK_PACKAGE_DESCRIPTION_SUMMARY - Short package summary.
#   CPACK_RESOURCE_FILE_LICENSE       - License file for NSIS/WiX installers.
#   CPACK_GENERATOR                   - Binary generators ("ZIP;NSIS;WIX").
#   CPACK_NSIS_MODIFY_PATH            - Prompt user to add BusyBox to PATH.
#   CPACK_WIX_UPGRADE_GUID            - Stable upgrade GUID for WiX MSI builds.
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
set(CPACK_GENERATOR "ZIP;NSIS;WIX")

# License Configuration (NSIS & WiX require a .txt or .rtf extension)
set(_cpack_license_src "")
if(DEFINED BUSYBOX_SOURCE_DIR AND EXISTS "${BUSYBOX_SOURCE_DIR}/LICENSE")
    set(_cpack_license_src "${BUSYBOX_SOURCE_DIR}/LICENSE")
elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/busybox/LICENSE")
    set(_cpack_license_src "${CMAKE_CURRENT_SOURCE_DIR}/busybox/LICENSE")
elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../busybox/LICENSE")
    set(_cpack_license_src "${CMAKE_CURRENT_SOURCE_DIR}/../busybox/LICENSE")
elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-MIT")
    set(_cpack_license_src "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-MIT")
endif()

if(_cpack_license_src)
    if(DEFINED CMAKE_CURRENT_BINARY_DIR AND NOT "${CMAKE_CURRENT_BINARY_DIR}" STREQUAL "${CMAKE_CURRENT_SOURCE_DIR}")
        configure_file("${_cpack_license_src}" "${CMAKE_CURRENT_BINARY_DIR}/LICENSE.txt" COPYONLY)
        set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_BINARY_DIR}/LICENSE.txt")
    else()
        set(CPACK_RESOURCE_FILE_LICENSE "${_cpack_license_src}")
    endif()
endif()

# NSIS Installer Settings
set(CPACK_NSIS_DISPLAY_NAME "BusyBox for Windows")
set(CPACK_NSIS_PACKAGE_NAME "BusyBox for Windows")
set(CPACK_NSIS_MODIFY_PATH ON)

# WiX Installer Settings
set(CPACK_WIX_UPGRADE_GUID "8F4E2B10-6C9A-4D3E-9A51-7B2D4E8F1039")

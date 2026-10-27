# =============================================================================
# CMakeLists.txt - BusyBox Windows & Cross-Platform Build Configuration
#
# Orchestrates declarative patching of upstream BusyBox sources, build-time
# header generation (autoconf.h, applet_tables.h, applets.h, usage_compressed.h,
# common_bufsiz.h, bbconfigopts.h), auto-win-msvc POSIX/Linux polyfill linking,
# and comprehensive CTest functional test suites for all enabled applets.
#
# Parameters / Cache Variables:
#   BUSYBOX_SOURCE_DIR - Path to upstream BusyBox source tree (auto-detected or
#                        fetched via FetchContent if absent).
#   FETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC - Path to local auto-win-msvc tree.
#   BUILD_TESTING      - Enables CTest functional applet verification (default ON).
# =============================================================================

cmake_minimum_required(VERSION 3.24)

project(busybox-windows VERSION 1.39.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(BUSYBOX_VERSION "1.39.0")

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# -----------------------------------------------------------------------------
# Upstream BusyBox Source Location Discovery
# -----------------------------------------------------------------------------
if(NOT DEFINED BUSYBOX_SOURCE_DIR)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/busybox/include/busybox.h")
        set(BUSYBOX_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/busybox")
    elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../busybox/include/busybox.h")
        set(BUSYBOX_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../busybox")
    else()
        set(BUSYBOX_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/busybox")
    endif()
endif()
get_filename_component(BUSYBOX_SOURCE_DIR "${BUSYBOX_SOURCE_DIR}" ABSOLUTE)

if(NOT EXISTS "${BUSYBOX_SOURCE_DIR}/include/busybox.h")
    include(FetchContent)
    FetchContent_Declare(
        busybox_upstream
        GIT_REPOSITORY https://git.busybox.net/busybox
        GIT_TAG e6302b2711d851bfb17eaf9316514c9d0a806cc7
    )
    FetchContent_MakeAvailable(busybox_upstream)
    set(BUSYBOX_SOURCE_DIR "${busybox_upstream_SOURCE_DIR}")
endif()

# -----------------------------------------------------------------------------
# Declarative Patch Application against Upstream BusyBox
# -----------------------------------------------------------------------------
set(BUSYBOX_PATCH_STAMP "${CMAKE_CURRENT_BINARY_DIR}/busybox_patches.stamp")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -m scripts.apply_patches
            "${BUSYBOX_SOURCE_DIR}"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches"
            "--stamp" "${BUSYBOX_PATCH_STAMP}"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE _patch_res
)
if(NOT _patch_res EQUAL 0)
    message(FATAL_ERROR "Failed to apply BusyBox compatibility patches via scripts/apply_patches.py")
endif()

# -----------------------------------------------------------------------------
# auto-win-msvc Compatibility Layer Discovery
# -----------------------------------------------------------------------------
include(CTest)
include(FetchContent)
set(_saved_build_shared_libs "${BUILD_SHARED_LIBS}")
set(_saved_build_testing "${BUILD_TESTING}")
set(BUILD_SHARED_LIBS OFF)
set(BUILD_TESTING OFF)
set(AUTO_WIN_MSVC_BUILD_TESTING OFF)

if(NOT TARGET auto-win-msvc)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc/CMakeLists.txt")
        if(NOT DEFINED FETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC)
            set(FETCHCONTENT_SOURCE_DIR_AUTO_WIN_MSVC "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc" CACHE PATH "Local auto-win-msvc")
        endif()
        if(NOT DEFINED FETCHCONTENT_SOURCE_DIR_AUTO-WIN-MSVC)
            set(FETCHCONTENT_SOURCE_DIR_AUTO-WIN-MSVC "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc" CACHE PATH "Local auto-win-msvc")
        endif()
    endif()

    FetchContent_Declare(
        auto_win_msvc
        GIT_REPOSITORY https://github.com/SamuelMarks/auto-win-msvc.git
        GIT_TAG master
    )
    FetchContent_MakeAvailable(auto_win_msvc)
endif()

set(BUILD_SHARED_LIBS "${_saved_build_shared_libs}")
set(BUILD_TESTING "${_saved_build_testing}")

# -----------------------------------------------------------------------------
# Generated Headers in CMAKE_CURRENT_BINARY_DIR/include
# -----------------------------------------------------------------------------
set(BUSYBOX_GEN_INC "${CMAKE_CURRENT_BINARY_DIR}/include")
file(MAKE_DIRECTORY "${BUSYBOX_GEN_INC}")

set(BUSYBOX_GENERATED_HEADERS
    "${BUSYBOX_GEN_INC}/autoconf.h"
    "${BUSYBOX_GEN_INC}/NUM_APPLETS.h"
    "${BUSYBOX_GEN_INC}/applets.h"
    "${BUSYBOX_GEN_INC}/applet_tables.h"
    "${BUSYBOX_GEN_INC}/usage_compressed.h"
    "${BUSYBOX_GEN_INC}/common_bufsiz.h"
    "${BUSYBOX_GEN_INC}/bbconfigopts.h"
)

# Run generators at configure time so headers exist immediately for dependency scanning
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -m scripts.generate_autoconf
            "${BUSYBOX_SOURCE_DIR}"
            "${BUSYBOX_GEN_INC}/autoconf.h"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE _autoconf_res
)
if(NOT _autoconf_res EQUAL 0 OR NOT EXISTS "${BUSYBOX_GEN_INC}/autoconf.h")
    message(FATAL_ERROR "Failed to generate autoconf.h via generate_autoconf.py")
endif()

execute_process(
    COMMAND "${Python3_EXECUTABLE}" -m scripts.generate_applet_tables
            "${BUSYBOX_GEN_INC}"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE _applets_res
)
if(NOT _applets_res EQUAL 0 OR NOT EXISTS "${BUSYBOX_GEN_INC}/applet_tables.h")
    message(FATAL_ERROR "Failed to generate applet tables via generate_applet_tables.py")
endif()

# Register build-time custom commands so edits to scripts/*.py trigger incremental regeneration
add_custom_command(
    OUTPUT "${BUSYBOX_GEN_INC}/autoconf.h"
    COMMAND "${Python3_EXECUTABLE}" -m scripts.generate_autoconf
            "${BUSYBOX_SOURCE_DIR}"
            "${BUSYBOX_GEN_INC}/autoconf.h"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_autoconf.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/applets_manifest.py"
    COMMENT "Generating BusyBox autoconf.h"
    VERBATIM
)

add_custom_command(
    OUTPUT "${BUSYBOX_GEN_INC}/NUM_APPLETS.h"
           "${BUSYBOX_GEN_INC}/applets.h"
           "${BUSYBOX_GEN_INC}/applet_tables.h"
           "${BUSYBOX_GEN_INC}/usage_compressed.h"
           "${BUSYBOX_GEN_INC}/common_bufsiz.h"
           "${BUSYBOX_GEN_INC}/bbconfigopts.h"
    COMMAND "${Python3_EXECUTABLE}" -m scripts.generate_applet_tables
            "${BUSYBOX_GEN_INC}"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_applet_tables.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/applets_manifest.py"
    COMMENT "Generating BusyBox applet dispatch tables and usage headers"
    VERBATIM
)

add_custom_target(busybox_generated_headers DEPENDS ${BUSYBOX_GENERATED_HEADERS})

# -----------------------------------------------------------------------------
# Deduplicated Source File List from Upstream BusyBox
# -----------------------------------------------------------------------------
set(BUSYBOX_CORE_SOURCES
    # Archival Applets & Libarchive
    "${BUSYBOX_SOURCE_DIR}/archival/ar.c"
    "${BUSYBOX_SOURCE_DIR}/archival/bbunzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/bzip2.c"
    "${BUSYBOX_SOURCE_DIR}/archival/chksum_and_xwrite_tar_header.c"
    "${BUSYBOX_SOURCE_DIR}/archival/cpio.c"
    "${BUSYBOX_SOURCE_DIR}/archival/gzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/common.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_align.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_extract_all.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_extract_to_command.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_extract_to_stdout.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_skip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_bunzip2.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_gunzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_unxz.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/filter_accept_all.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/filter_accept_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/filter_accept_reject_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/find_list_entry.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/get_header_ar.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/get_header_cpio.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/get_header_tar.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/header_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/header_skip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/header_verbose_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/init_handle.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/lzo1x_1.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/lzo1x_d.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/open_transformer.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/seek_by_jump.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/seek_by_read.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/unpack_ar_archive.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/unsafe_prefix.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/unsafe_symlink_target.c"
    "${BUSYBOX_SOURCE_DIR}/archival/lzop.c"
    "${BUSYBOX_SOURCE_DIR}/archival/tar.c"
    "${BUSYBOX_SOURCE_DIR}/archival/unzip.c"

    # Console Tools
    "${BUSYBOX_SOURCE_DIR}/console-tools/clear.c"
    "${BUSYBOX_SOURCE_DIR}/console-tools/reset.c"

    # Coreutils Applets
    "${BUSYBOX_SOURCE_DIR}/coreutils/basename.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cat.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/chmod.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cksum.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/comm.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cp.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cut.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/date.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/df.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/dirname.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/dos2unix.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/du.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/echo.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/env.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/expand.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/factor.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/false.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/fold.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/head.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/id.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/libcoreutils/cp_mv_stat.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/ln.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/ls.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/md5_sha1_sum.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/mkdir.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/mktemp.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/mv.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/nl.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/od.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/paste.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/printenv.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/printf.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/pwd.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/readlink.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/realpath.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/rm.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/rmdir.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/seq.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/shuf.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/sleep.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/sort.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/split.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/stat.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/sum.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/sync.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/tail.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/tee.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/test.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/test_ptr_hack.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/touch.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/tr.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/true.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/truncate.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/tsort.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/uname.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/uniq.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/unlink.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/usleep.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/uudecode.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/uuencode.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/wc.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/whoami.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/yes.c"

    # Debianutils Applets
    "${BUSYBOX_SOURCE_DIR}/debianutils/which.c"

    # Editors
    "${BUSYBOX_SOURCE_DIR}/editors/awk.c"
    "${BUSYBOX_SOURCE_DIR}/editors/cmp.c"
    "${BUSYBOX_SOURCE_DIR}/editors/diff.c"
    "${BUSYBOX_SOURCE_DIR}/editors/ed.c"
    "${BUSYBOX_SOURCE_DIR}/editors/patch.c"
    "${BUSYBOX_SOURCE_DIR}/editors/sed.c"
    "${BUSYBOX_SOURCE_DIR}/editors/vi.c"

    # Findutils
    "${BUSYBOX_SOURCE_DIR}/findutils/find.c"
    "${BUSYBOX_SOURCE_DIR}/findutils/grep.c"
    "${BUSYBOX_SOURCE_DIR}/findutils/xargs.c"

    # Core Library (libbb & libpwdgrp)
    "${BUSYBOX_SOURCE_DIR}/libbb/appletlib.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/ask_confirmation.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/auto_string.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_bswap_64.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_cat.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_get_servport_by_name.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_getgroups.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_pwd.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_strtod.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_strtonum.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/c_escape.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/chomp.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/common_bufsiz.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/compare_string_array.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/concat_path_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/concat_subpath_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/const_hack.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/copy_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/copyfd.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/crc32.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/default_error_retval.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/dump.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/duration.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/endofname.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/executable.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/fclose_nonstdin.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/fflush_stdout_and_exit.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/fgets_str.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/find_mount_point.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/find_pid_by_name.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/full_write.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/get_last_path_component.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/get_line_from_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/get_shell_name.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/getopt32.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/getpty.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/hash_md5_sha.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/human_readable.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/inet_cksum.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/inode_hash.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/ioloop.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/isdirectory.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/isqrt.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/last_char_is.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/lineedit.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/llist.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/login.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/make_directory.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/messages.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/mode_string.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/parse_config.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/parse_mode.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/percent_decode.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/perror_msg.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/perror_nomsg_and_die.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/platform.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/poll_with_signals.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/print_numbered_lines.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/printable.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/printable_string.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/process_escape_sequence.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/procps.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/progress.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/ptr_to_globals.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/read.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/read_key.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/read_printf.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/recursive_action.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/remove_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_poll.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_strncpy.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_write.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/signals.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/single_argv.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/skip_whitespace.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/str_tolower.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/sysconf.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/time.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/trim.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/u_signal_names.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/udp_io.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/uuencode.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/verror_msg.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/vfork_daemon_rexec.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/warn_ignoring_args.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/wfopen.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/wfopen_input.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xatonum.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xconnect.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xfunc_die.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xfuncs.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xfuncs_printf.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xgetcwd.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xreadlink.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xrealloc_vector.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xregcomp.c"

    # Miscutils
    "${BUSYBOX_SOURCE_DIR}/miscutils/bc.c"
    "${BUSYBOX_SOURCE_DIR}/miscutils/hexedit.c"
    "${BUSYBOX_SOURCE_DIR}/miscutils/strings.c"
    "${BUSYBOX_SOURCE_DIR}/miscutils/time.c"

    # Networking Applets
    "${BUSYBOX_SOURCE_DIR}/networking/dnsd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/ftpgetput.c"
    "${BUSYBOX_SOURCE_DIR}/networking/httpd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/nc.c"
    "${BUSYBOX_SOURCE_DIR}/networking/nslookup.c"
    "${BUSYBOX_SOURCE_DIR}/networking/parse_pasv_epsv.c"
    "${BUSYBOX_SOURCE_DIR}/networking/ping.c"
    "${BUSYBOX_SOURCE_DIR}/networking/telnet.c"
    "${BUSYBOX_SOURCE_DIR}/networking/telnetd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/wget.c"

    # Procps Applets
    "${BUSYBOX_SOURCE_DIR}/procps/free.c"
    "${BUSYBOX_SOURCE_DIR}/procps/kill.c"
    "${BUSYBOX_SOURCE_DIR}/procps/pgrep.c"
    "${BUSYBOX_SOURCE_DIR}/procps/pidof.c"
    "${BUSYBOX_SOURCE_DIR}/procps/ps.c"
    "${BUSYBOX_SOURCE_DIR}/procps/uptime.c"
    "${BUSYBOX_SOURCE_DIR}/procps/watch.c"

    # Shells
    "${BUSYBOX_SOURCE_DIR}/shell/ash.c"
    "${BUSYBOX_SOURCE_DIR}/shell/ash_ptr_hack.c"
    "${BUSYBOX_SOURCE_DIR}/shell/hush.c"
    "${BUSYBOX_SOURCE_DIR}/shell/match.c"
    "${BUSYBOX_SOURCE_DIR}/shell/math.c"
    "${BUSYBOX_SOURCE_DIR}/shell/random.c"
    "${BUSYBOX_SOURCE_DIR}/shell/shell_common.c"

    # Util-Linux Applets
    "${BUSYBOX_SOURCE_DIR}/util-linux/cal.c"
    "${BUSYBOX_SOURCE_DIR}/util-linux/hexdump.c"
    "${BUSYBOX_SOURCE_DIR}/util-linux/hexdump_xxd.c"
)

# -----------------------------------------------------------------------------
# Optional MSVC Source Transformation (VLAs and C89/MSVC compatibility)
# -----------------------------------------------------------------------------
if(EXISTS "${auto_win_msvc_SOURCE_DIR}/cmake/AutoWinMsvcTransform.cmake")
    include("${auto_win_msvc_SOURCE_DIR}/cmake/AutoWinMsvcTransform.cmake")
elseif(EXISTS "${auto-win-msvc_SOURCE_DIR}/cmake/AutoWinMsvcTransform.cmake")
    include("${auto-win-msvc_SOURCE_DIR}/cmake/AutoWinMsvcTransform.cmake")
elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc/cmake/AutoWinMsvcTransform.cmake")
    include("${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc/cmake/AutoWinMsvcTransform.cmake")
endif()

if(COMMAND auto_win_msvc_transform_sources AND MSVC)
    auto_win_msvc_transform_sources(
        OUTPUT_DIR "${CMAKE_CURRENT_BINARY_DIR}/src_gen"
        OUTPUT_VAR BUSYBOX_CORE_SOURCES
        SOURCES ${BUSYBOX_CORE_SOURCES}
    )
endif()

# -----------------------------------------------------------------------------
# Target: busybox executable
# -----------------------------------------------------------------------------
list(LENGTH BUSYBOX_CORE_SOURCES _busybox_src_count)
message(STATUS "BUSYBOX_CORE_SOURCES count: ${_busybox_src_count}")

add_executable(busybox ${BUSYBOX_CORE_SOURCES} ${BUSYBOX_GENERATED_HEADERS})
add_dependencies(busybox busybox_generated_headers)

target_include_directories(busybox PRIVATE
    "${BUSYBOX_GEN_INC}"
    "${CMAKE_CURRENT_BINARY_DIR}/src_gen"
    "${BUSYBOX_SOURCE_DIR}/include"
    "${BUSYBOX_SOURCE_DIR}/libbb"
    "${BUSYBOX_SOURCE_DIR}/coreutils"
    "${BUSYBOX_SOURCE_DIR}/coreutils/libcoreutils"
    "${BUSYBOX_SOURCE_DIR}/archival"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive"
    "${BUSYBOX_SOURCE_DIR}/editors"
    "${BUSYBOX_SOURCE_DIR}/findutils"
    "${BUSYBOX_SOURCE_DIR}/procps"
    "${BUSYBOX_SOURCE_DIR}/miscutils"
    "${BUSYBOX_SOURCE_DIR}/console-tools"
    "${BUSYBOX_SOURCE_DIR}/networking"
)

if(MSVC)
    target_compile_options(busybox PRIVATE
        "/FIauto-win-msvc-configure.h"
        "/FIautoconf.h"
    )
else()
    target_compile_options(busybox PRIVATE
        "SHELL:-include auto-win-msvc-configure.h"
        "SHELL:-include ${BUSYBOX_GEN_INC}/autoconf.h"
    )
endif()

target_compile_definitions(busybox PRIVATE
    _GNU_SOURCE=1
    BB_VER="${BUSYBOX_VERSION}"
    AUTOCONF_TIMESTAMP=""
)

if(TARGET auto-win-msvc)
    target_link_libraries(busybox PRIVATE auto-win-msvc)
endif()

if(UNIX AND NOT LINUX)
    find_library(RESOLV_LIBRARY resolv)
    if(RESOLV_LIBRARY)
        target_link_libraries(busybox PRIVATE "${RESOLV_LIBRARY}")
    endif()
endif()

# -----------------------------------------------------------------------------
# Installation Rules
# -----------------------------------------------------------------------------
install(TARGETS busybox RUNTIME DESTINATION bin)
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/README.md")
    install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/README.md" DESTINATION .)
endif()
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-APACHE")
    install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-APACHE" DESTINATION .)
endif()
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-MIT")
    install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-MIT" DESTINATION .)
endif()

# -----------------------------------------------------------------------------
# Packaging via CPack
# -----------------------------------------------------------------------------
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/CPackConfig.cmake")
    include("${CMAKE_CURRENT_SOURCE_DIR}/CPackConfig.cmake")
endif()
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/CPackSourceConfig.cmake")
    include("${CMAKE_CURRENT_SOURCE_DIR}/CPackSourceConfig.cmake")
endif()
include(CPack)

# -----------------------------------------------------------------------------
# Testing via CTest
# -----------------------------------------------------------------------------
include(CTest)
if(BUILD_TESTING)
    enable_testing()

    # Core CLI & Shell verification tests with strict output assertions
    add_test(NAME test_busybox_help COMMAND busybox --help)
    set_tests_properties(test_busybox_help PROPERTIES PASS_REGULAR_EXPRESSION "BusyBox")

    add_test(NAME test_busybox_ash COMMAND busybox ash -c "echo ash_works")
    set_tests_properties(test_busybox_ash PROPERTIES PASS_REGULAR_EXPRESSION "ash_works")

    add_test(NAME test_busybox_hush COMMAND busybox hush -c "echo hush_works")
    set_tests_properties(test_busybox_hush PROPERTIES PASS_REGULAR_EXPRESSION "hush_works")

    add_test(NAME test_busybox_sh COMMAND busybox sh -c "echo sh_works")
    set_tests_properties(test_busybox_sh PROPERTIES PASS_REGULAR_EXPRESSION "sh_works")

    add_test(NAME test_busybox_echo COMMAND busybox echo "BusyBox native build successful")
    set_tests_properties(test_busybox_echo PROPERTIES PASS_REGULAR_EXPRESSION "BusyBox native build successful")

    add_test(NAME test_busybox_true COMMAND busybox true)

    add_test(NAME test_busybox_false COMMAND busybox false)
    set_tests_properties(test_busybox_false PROPERTIES WILL_FAIL TRUE)

    add_test(NAME test_busybox_basename COMMAND busybox basename "foo/bar/baz.txt" ".txt")
    set_tests_properties(test_busybox_basename PROPERTIES PASS_REGULAR_EXPRESSION "^baz")

    add_test(NAME test_busybox_dirname COMMAND busybox dirname "foo/bar/baz.txt")
    set_tests_properties(test_busybox_dirname PROPERTIES PASS_REGULAR_EXPRESSION "^foo/bar")

    add_test(NAME test_busybox_printf COMMAND busybox printf "%s %d
" "value:" 42)
    set_tests_properties(test_busybox_printf PROPERTIES PASS_REGULAR_EXPRESSION "value: 42")

    add_test(NAME test_busybox_factor COMMAND busybox factor 42)
    set_tests_properties(test_busybox_factor PROPERTIES PASS_REGULAR_EXPRESSION "42: 2 3 7")

    add_test(NAME test_busybox_awk COMMAND busybox awk "BEGIN { print 6 * 7; exit }")
    set_tests_properties(test_busybox_awk PROPERTIES PASS_REGULAR_EXPRESSION "^42")

    add_test(NAME test_busybox_bc COMMAND busybox bc "${CMAKE_CURRENT_SOURCE_DIR}/tests/bc_test.txt")
    set_tests_properties(test_busybox_bc PROPERTIES PASS_REGULAR_EXPRESSION "42")

    add_test(NAME test_busybox_dc COMMAND busybox dc -e "6 7 * p")
    set_tests_properties(test_busybox_dc PROPERTIES PASS_REGULAR_EXPRESSION "^42")

    add_test(NAME test_busybox_pidof COMMAND busybox pidof busybox)
    set_tests_properties(test_busybox_pidof PROPERTIES PASS_REGULAR_EXPRESSION "[0-9]+")

    add_test(NAME test_busybox_df COMMAND busybox df .)
    set_tests_properties(test_busybox_df PROPERTIES PASS_REGULAR_EXPRESSION "Filesystem")

    add_test(NAME test_busybox_free COMMAND busybox free)
    set_tests_properties(test_busybox_free PROPERTIES PASS_REGULAR_EXPRESSION "Mem:")

    if(NOT MSVC)
        # Isolated, self-contained functional applet suites (zero --help tests, zero source-tree mutations)
        add_test(
            NAME test_suite_coreutils
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_applet_test.py"
                    "$<TARGET_FILE:busybox>" --suite coreutils
        )
        add_test(
            NAME test_suite_editors_and_find
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_applet_test.py"
                    "$<TARGET_FILE:busybox>" --suite editors
        )
        add_test(
            NAME test_suite_archival
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_applet_test.py"
                    "$<TARGET_FILE:busybox>" --suite archival
        )
        add_test(
            NAME test_suite_procps_and_shell
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_applet_test.py"
                    "$<TARGET_FILE:busybox>" --suite procps
        )
        add_test(
            NAME test_suite_networking
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_applet_test.py"
                    "$<TARGET_FILE:busybox>" --suite networking
        )
    endif()
endif()

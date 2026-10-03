set(BUILD_SHARED_LIBS OFF CACHE BOOL "Build static libraries" FORCE)
cmake_minimum_required(VERSION 3.24)

project(busybox-windows VERSION 1.39.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(BUSYBOX_VERSION "1.39.0")

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

# Fallback to FetchContent if local busybox repository not found
if(NOT EXISTS "${BUSYBOX_SOURCE_DIR}/include/busybox.h")
    include(FetchContent)
    FetchContent_Declare(
        busybox_upstream
        GIT_REPOSITORY https://github.com/mirror/busybox.git
        GIT_TAG master
    )
    FetchContent_MakeAvailable(busybox_upstream)
    set(BUSYBOX_SOURCE_DIR "${busybox_upstream_SOURCE_DIR}")
endif()

# -----------------------------------------------------------------------------
# auto-win-msvc Compatibility Layer Discovery
# -----------------------------------------------------------------------------
include(FetchContent)
set(AUTO_WIN_MSVC_BUILD_TESTING OFF CACHE BOOL "Disable auto-win-msvc tests" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "Disable dependency tests" FORCE)
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

# -----------------------------------------------------------------------------
# Generated Headers in CMAKE_CURRENT_BINARY_DIR via configure_file
# -----------------------------------------------------------------------------
set(BUSYBOX_GEN_INC "${CMAKE_CURRENT_BINARY_DIR}/include")
file(MAKE_DIRECTORY "${BUSYBOX_GEN_INC}")

# 1. autoconf.h - Core configuration defining applets and features
execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env python3 "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_autoconf.py" "${BUSYBOX_SOURCE_DIR}" "${BUSYBOX_GEN_INC}/autoconf.h.in"
    RESULT_VARIABLE autoconf_res
)
if(NOT autoconf_res EQUAL 0 OR NOT EXISTS "${BUSYBOX_GEN_INC}/autoconf.h.in")
    message(FATAL_ERROR "Failed to generate autoconf.h.in via generate_autoconf.py")
endif()
configure_file("${BUSYBOX_GEN_INC}/autoconf.h.in" "${BUSYBOX_GEN_INC}/autoconf.h" COPYONLY)


# 2. common_bufsiz.h - Standard I/O buffer definition
if(NOT EXISTS "${BUSYBOX_GEN_INC}/common_bufsiz.h")
    file(WRITE "${BUSYBOX_GEN_INC}/common_bufsiz.h.in" [[
#ifndef COMMON_BUFSIZ_H
#define COMMON_BUFSIZ_H 1

enum { COMMON_BUFSIZE = 1024 };
extern char bb_common_bufsiz1[];
#define setup_common_bufsiz() ((void)0)

#endif /* COMMON_BUFSIZ_H */
]])
    configure_file("${BUSYBOX_GEN_INC}/common_bufsiz.h.in" "${BUSYBOX_GEN_INC}/common_bufsiz.h" COPYONLY)
endif()

# 3. bbconfigopts.h - Configuration string
if(NOT EXISTS "${BUSYBOX_GEN_INC}/bbconfigopts.h")
    file(WRITE "${BUSYBOX_GEN_INC}/bbconfigopts.h.in" [[
#ifndef BBCONFIGOPTS_H
#define BBCONFIGOPTS_H 1

#define bbconfig_config ""
#define bbconfig_config_bz2 ""

#endif /* BBCONFIGOPTS_H */
]])
    configure_file("${BUSYBOX_GEN_INC}/bbconfigopts.h.in" "${BUSYBOX_GEN_INC}/bbconfigopts.h" COPYONLY)
endif()

# 4. busybox_msvc_compat.h - Forced include for MSVC shimming
if(NOT EXISTS "${BUSYBOX_GEN_INC}/busybox_msvc_compat.h")
    file(WRITE "${BUSYBOX_GEN_INC}/busybox_msvc_compat.h.in" [[
#ifndef BUSYBOX_MSVC_COMPAT_H
#define BUSYBOX_MSVC_COMPAT_H 1

#ifndef HAVE_MNTENT_H
#define HAVE_MNTENT_H 1
#endif
#ifndef HAVE_SYS_STATFS_H
#define HAVE_SYS_STATFS_H 1
#endif

#include "autoconf.h"
#ifdef COMPILING_ASH
#undef CONFIG_NOMMU
#define CONFIG_NOMMU 0
#undef ENABLE_NOMMU
#define ENABLE_NOMMU 0
#undef IF_NOMMU
#define IF_NOMMU(...)
#undef IF_NOT_NOMMU
#define IF_NOT_NOMMU(...) __VA_ARGS__
#endif

#include <stdio.h>
FILE *posix_fopen(const char *pathname, const char *mode);
#ifndef fopen
#define fopen posix_fopen
#endif

#if defined(_MSC_VER)

#define __clang_major__ 10
#define inline __inline
#define __inline__ __inline
#define __restrict
#define __restrict__
#define __extension__
#define __attribute__(x)
#define __builtin_unreachable() __assume(0)

#define FAST_FUNC
#define FIX_ALIASING
#define EXTERNALLY_VISIBLE
#define MAIN_EXTERNALLY_VISIBLE
#define PUSH_AND_SET_FUNCTION_VISIBILITY_TO_HIDDEN
#define POP_SAVED_FUNCTION_VISIBILITY
#define barrier() ((void)0)

#define major(dev) 0
#define minor(dev) 0
#define makedev(maj, min) 0

#define itoa bb_itoa

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#endif

#ifdef s_addr
#undef s_addr
#endif

#ifdef get_le32
#undef get_le32
#endif
#define get_le32(p) bb_get_unaligned_le32(p)

#ifdef get_be32
#undef get_be32
#endif
#define get_be32(p) bb_get_unaligned_be32(p)

#if !ENABLE_FEATURE_CLEAN_UP

#endif
#define s_addr bb_s_addr_param

static __inline char *bb_win_strrchr_slash(const char *s) {
    const char *p1 = strrchr(s, '/');
    const char *p2 = strrchr(s, '\\');
    return (char *)((p1 > p2) ? p1 : p2);
}
#define strrchr(s, c) (((c) == '/') ? bb_win_strrchr_slash(s) : (strrchr)(s, c))

#define getc_unlocked getc
#define getchar_unlocked getchar
#define putc_unlocked putc
#define putchar_unlocked putchar
#define fputs_unlocked fputs
#define fgets_unlocked fgets
#define ferror_unlocked ferror
#define fileno_unlocked _fileno
#define alloca _alloca
#define fseeko _fseeki64

static __inline void ASSIGN_CONST_PTR(const void *pptr, void *v) {
    *(void**)pptr = v;
}
static __inline void XZALLOC_CONST_PTR(const void *pptr, size_t size) {
    extern void *xzalloc(size_t size);
    *(void**)pptr = xzalloc(size);
}

static __inline unsigned int bb_get_unaligned_le32(const void *p) {
    const unsigned char *b = (const unsigned char *)p;
    return (unsigned int)b[0] | ((unsigned int)b[1] << 8) | ((unsigned int)b[2] << 16) | ((unsigned int)b[3] << 24);
}
static __inline unsigned int bb_get_unaligned_be32(const void *p) {
    const unsigned char *b = (const unsigned char *)p;
    return ((unsigned int)b[0] << 24) | ((unsigned int)b[1] << 16) | ((unsigned int)b[2] << 8) | (unsigned int)b[3];
}
#define get_unaligned_le32(p) bb_get_unaligned_le32(p)
#define get_unaligned_be32(p) bb_get_unaligned_be32(p)
#define get_le32(p) bb_get_unaligned_le32(p)
#define get_be32(p) bb_get_unaligned_be32(p)

#endif /* _MSC_VER */

#include <stddef.h>
#include <string.h>

#if defined(_MSC_VER)
void *mempcpy(void *dest, const void *src, size_t n);
char *strchrnul(const char *s, int c);
char *stpcpy(char *dest, const char *src);
char *stpncpy(char *dest, const char *src, size_t n);
char *strcasestr(const char *haystack, const char *needle);
static __inline void *memrchr(const void *s, int c, size_t n) { const unsigned char *p = (const unsigned char *)s + n; while (n--) { if (*--p == (unsigned char)c) return (void *)p; } return NULL; }
#elif defined(__APPLE__)
#define res_init() (0)
#define _res (*((struct __res_state *)0))
#define res_ninit(statp) (0)
#define __res_9_init res_init
#define res_9_init res_init
int __res_9_init(void) { return res_init(); }

#define res_9_state _res
#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define ___res_state() (&_res)
#define ___res_9_state() (&_res)

#define ___res_9_state() (&_res)
struct __res_state *__res_9_state(void) { return &_res; }
#define ___res_9_state _res


#define res_9_state _res
#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define ___res_state() (&_res)
#define ___res_9_state() (&_res)

#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define __res_9_init res_init
#define res_9_init res_init
int __res_9_init(void) { return res_init(); }

#define res_9_state _res
#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define ___res_state() (&_res)
#define ___res_9_state() (&_res)

#define ___res_9_state() (&_res)
struct __res_state *__res_9_state(void) { return &_res; }
#define ___res_9_state _res



#define res_init() (0)
#define _res (*((struct __res_state *)0))
#define res_ninit(statp) (0)
#define __res_9_init res_init
#define res_9_init res_init
int __res_9_init(void) { return res_init(); }

#define res_9_state _res
#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define ___res_state() (&_res)
#define ___res_9_state() (&_res)

#define ___res_9_state() (&_res)
struct __res_state *__res_9_state(void) { return &_res; }
#define ___res_9_state _res


#define res_9_state _res
#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define ___res_state() (&_res)
#define ___res_9_state() (&_res)

#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define __res_9_init res_init
#define res_9_init res_init
int __res_9_init(void) { return res_init(); }

#define res_9_state _res
#define __res_state _res
struct __res_state *__res_9_state(void) { return &_res; }

#define ___res_state() (&_res)
#define ___res_9_state() (&_res)

#define ___res_9_state() (&_res)
struct __res_state *__res_9_state(void) { return &_res; }
#define ___res_9_state _res





#include <stdint.h>
struct iphdr {
#if BYTE_ORDER == LITTLE_ENDIAN
    unsigned int ihl:4;
    unsigned int version:4;
#elif BYTE_ORDER == BIG_ENDIAN
    unsigned int version:4;
    unsigned int ihl:4;
#else
# error "Please fix <machine/endian.h>"
#endif
    uint8_t tos;
    uint16_t tot_len;
    uint16_t id;
    uint16_t frag_off;
    uint8_t ttl;
    uint8_t protocol;
    uint16_t check;
    uint32_t saddr;
    uint32_t daddr;
};

#ifndef memrchr
static __inline void *memrchr(const void *s, int c, size_t n) { const unsigned char *p = (const unsigned char *)s + n; while (n--) { if (*--p == (unsigned char)c) return (void *)p; } return NULL; }
#endif
#define sigisemptyset(set) (*(set) == 0)

#ifndef sigisemptyset
#define sigisemptyset(set) (*(set) == 0)
#define sigisemptyset sigisemptyset
#endif

#ifndef mempcpy
static __inline void *bb_apple_mempcpy(void *dest, const void *src, size_t n) {
    return (char *)memcpy(dest, src, n) + n;
}
#define mempcpy bb_apple_mempcpy
#endif
#ifndef __bswap16
#define __bswap16 __builtin_bswap16
#define __bswap32 __builtin_bswap32
#define __bswap64 __builtin_bswap64
#endif
#endif

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#ifndef NAME_MAX
#define NAME_MAX 255
#endif

#ifndef SSIZE_MAX
#define SSIZE_MAX ((ssize_t)((size_t)-1 >> 1))
#endif

#ifndef SHUT_RD
#define SHUT_RD 0
#endif
#ifndef SHUT_WR
#define SHUT_WR 1
#endif
#ifndef SHUT_RDWR
#define SHUT_RDWR 2
#endif

#if defined(__APPLE__)
#include <stdint.h>
#ifndef _BSM_AUDIT_H
#define _BSM_AUDIT_H 1
#endif
#ifndef st_atim
#define st_atim st_atimespec
#define st_mtim st_mtimespec
#define st_ctim st_ctimespec
#endif
#include <sys/param.h>
#include <sys/mount.h>
#ifndef f_namelen
#define f_namelen f_flags
#endif
#endif

#if defined(_WIN32)
#ifndef st_blocks
#define st_blocks st_size
#endif
#endif

#if !defined(__linux__)
#ifndef _SYSINFO_DEFINED
#define _SYSINFO_DEFINED
struct sysinfo {
    long uptime;
    unsigned long loads[3];
    unsigned long totalram;
    unsigned long freeram;
    unsigned long sharedram;
    unsigned long bufferram;
    unsigned long totalswap;
    unsigned long freeswap;
    unsigned short procs;
    unsigned long totalhigh;
    unsigned long freehigh;
    unsigned int mem_unit;
    char _f[20 - 2 * sizeof(long) - sizeof(int)];
};
#endif
#endif

#if defined(_WIN32)
typedef struct _WIN_MEMORYSTATUSEX {
    unsigned long dwLength;
    unsigned long dwMemoryLoad;
    unsigned long long ullTotalPhys;
    unsigned long long ullAvailPhys;
    unsigned long long ullTotalPageFile;
    unsigned long long ullAvailPageFile;
    unsigned long long ullTotalVirtual;
    unsigned long long ullAvailVirtual;
    unsigned long long ullAvailExtendedVirtual;
} WIN_MEMORYSTATUSEX;

#ifdef __cplusplus
extern "C" {
#endif
__declspec(dllimport) unsigned long __stdcall GetTickCount(void);
__declspec(dllimport) int __stdcall GlobalMemoryStatusEx(WIN_MEMORYSTATUSEX *lpBuffer);
#ifdef __cplusplus
}
#endif

void bb_simple_perror_msg_and_die(const char *s);
int vfork(void);
static __inline int bb_libbb_xvfork(void) {
    int bb__xvfork_pid = vfork();
    if (bb__xvfork_pid < 0)
        bb_simple_perror_msg_and_die("vfork");
    return bb__xvfork_pid;
}

static __inline int sysinfo(struct sysinfo *info) {
    WIN_MEMORYSTATUSEX mem;
    if (!info) return -1;
    memset(info, 0, sizeof(*info));
    info->uptime = (long)(GetTickCount() / 1000);
    info->mem_unit = 1;
    mem.dwLength = (unsigned long)sizeof(mem);
    if (GlobalMemoryStatusEx(&mem)) {
        info->totalram = (unsigned long)(mem.ullTotalPhys > 0xFFFFFFFFUL ? 0xFFFFFFFFUL : mem.ullTotalPhys);
        info->freeram = (unsigned long)(mem.ullAvailPhys > 0xFFFFFFFFUL ? 0xFFFFFFFFUL : mem.ullAvailPhys);
        info->totalswap = (unsigned long)(mem.ullTotalPageFile > 0xFFFFFFFFUL ? 0xFFFFFFFFUL : mem.ullTotalPageFile);
        info->freeswap = (unsigned long)(mem.ullAvailPageFile > 0xFFFFFFFFUL ? 0xFFFFFFFFUL : mem.ullAvailPageFile);
    }
    return 0;
}
#elif !defined(__linux__)
#include <time.h>
static __inline int sysinfo(struct sysinfo *info) {
    struct timespec ts;
    if (!info) return -1;
    memset(info, 0, sizeof(*info));
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        info->uptime = (long)ts.tv_sec;
    }
    info->mem_unit = 1;
    return 0;
}
#endif

#endif /* BUSYBOX_MSVC_COMPAT_H */
]])
    configure_file("${BUSYBOX_GEN_INC}/busybox_msvc_compat.h.in" "${BUSYBOX_GEN_INC}/busybox_msvc_compat.h" COPYONLY)
endif()

# 5. applets.h, NUM_APPLETS.h, and applet_tables.h - Generated dynamically
find_package(Python3 COMPONENTS Interpreter REQUIRED)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_applet_tables.py" "${BUSYBOX_GEN_INC}"
    RESULT_VARIABLE res_gen_applets
)
if(NOT res_gen_applets EQUAL 0)
    message(FATAL_ERROR "Failed to generate applet tables header files.")
endif()

# 7. usage.h and usage_compressed.h
if(NOT EXISTS "${BUSYBOX_GEN_INC}/usage_compressed.h")
    file(WRITE "${BUSYBOX_GEN_INC}/usage_compressed.h.in" [[
#ifndef USAGE_COMPRESSED_H
#define USAGE_COMPRESSED_H 1

#define UNPACKED_USAGE "BusyBox v1.39.0 (auto-win-msvc)\n"
#define UNPACKED_USAGE_BMAP ""
static const char packed_usage[] ALIGN1 =
"BusyBox v1.39.0 (auto-win-msvc)\n\n"
"Usage: busybox [function [arguments]...]\n"
"   or: busybox --list\n"
"   or: function [arguments]...\n\n"
"\tCurrently defined functions:\n"
"\tbase64, basename, cat, cksum, comm, cp, cut, dirname,\n"
"\techo, false, fold, head, md5sum, mkdir, mv, paste,\n"
"\tprintenv, printf, pwd, rm, rmdir, seq, sha1sum, sha256sum,\n"
"\tsha512sum, sleep, sort, sum, sync, tail, tee, test, touch,\n"
"\ttr, true, uname, uniq, usleep, wc, which, whoami, yes\n";

#endif /* USAGE_COMPRESSED_H */
]])
    configure_file("${BUSYBOX_GEN_INC}/usage_compressed.h.in" "${BUSYBOX_GEN_INC}/usage_compressed.h" COPYONLY)
endif()


# 8. libbb.h - Wrapper header to eliminate statement expressions in C90/MSVC
file(WRITE "${BUSYBOX_GEN_INC}/libbb.h.in" [[
#ifndef BUSYBOX_LIBBB_WRAPPER_H
#define BUSYBOX_LIBBB_WRAPPER_H 1

#include "@BUSYBOX_SOURCE_DIR@/include/libbb.h"

#if defined(__APPLE__)
#include <arpa/inet.h>
#endif
struct mntent;
extern struct mntent *find_mount_point(const char *name, int subdir_too) FAST_FUNC;

#if defined(_MSC_VER)
#ifdef isblank
#undef isblank
#endif
static __inline int bb_msvc_isblank(int c) { return (c == ' ' || c == '\t'); }
#define isblank(c) bb_msvc_isblank(c)

#ifdef iscntrl
#undef iscntrl
#endif
static __inline int bb_msvc_iscntrl(int c) { return ((unsigned char)c < ' ' || (unsigned char)c == 0x7f); }
#define iscntrl(c) bb_msvc_iscntrl(c)

#ifdef isspace
#undef isspace
#endif
static __inline int bb_msvc_isspace(int c) { return (c == ' ' || ((unsigned char)c >= 9 && (unsigned char)c <= 13)); }
#define isspace(c) bb_msvc_isspace(c)

#ifdef s_addr
#undef s_addr
#endif

#ifdef get_le32
#undef get_le32
#endif
#define get_le32(p) bb_get_unaligned_le32(p)

#ifdef get_be32
#undef get_be32
#endif
#define get_be32(p) bb_get_unaligned_be32(p)

#if !ENABLE_FEATURE_CLEAN_UP

#endif

#endif /* _MSC_VER */

#endif /* BUSYBOX_LIBBB_WRAPPER_H */
]])
configure_file("${BUSYBOX_GEN_INC}/libbb.h.in" "${BUSYBOX_GEN_INC}/libbb.h" @ONLY)

# -----------------------------------------------------------------------------
# Source File Lists from Upstream BusyBox
# -----------------------------------------------------------------------------
set(BUSYBOX_CORE_SOURCES
    # Core Library (libbb)
    "${BUSYBOX_SOURCE_DIR}/libbb/appletlib.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/ask_confirmation.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/auto_string.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_cat.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_do_delay.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_pwd.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_qsort.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_strtod.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_strtonum.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/chomp.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/common_bufsiz.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/compare_string_array.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/concat_path_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/concat_subpath_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/copy_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/copyfd.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/crc32.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/default_error_retval.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/device_open.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/dump.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/duration.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/endofname.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/executable.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/fclose_nonstdin.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/fflush_stdout_and_exit.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/full_write.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/get_line_from_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/get_volsize.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/getopt32.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/human_readable.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/isdirectory.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/kernel_version.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/last_char_is.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/llist.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/make_directory.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/messages.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/mode_string.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/parse_mode.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/perror_msg.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/perror_nomsg.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/perror_nomsg_and_die.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/printable.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/printable_string.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/u_signal_names.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/ptr_to_globals.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/read.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/read_key.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/recursive_action.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/remove_file.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_poll.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_strncpy.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_write.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/setup_environment.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/signals.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/skip_whitespace.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/speed_table.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/str_tolower.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/strrstr.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/time.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/trim.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/verror_msg.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/wfopen.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/wfopen_input.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/write.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/herror_msg.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/safe_gethostname.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/sysconf.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/parse_config.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/getpty.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/inet_cksum.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xgethostbyname.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/udp_io.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/ioloop.c"
    "${BUSYBOX_SOURCE_DIR}/libpwdgrp/uidgid_get.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/login.c"

    "${BUSYBOX_SOURCE_DIR}/libbb/xatonum.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xfunc_die.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xfuncs.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xfuncs_printf.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xgetcwd.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xreadlink.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xrealloc_vector.c"

    # Coreutils Applets
    "${BUSYBOX_SOURCE_DIR}/coreutils/cat.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/chmod.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cksum.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/comm.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cp.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/cut.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/date.c"
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
    "${BUSYBOX_SOURCE_DIR}/coreutils/ln.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/ls.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/md5_sha1_sum.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/mkdir.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/mktemp.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/mv.c"
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
    "${BUSYBOX_SOURCE_DIR}/coreutils/stat.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/df.c"
    "${BUSYBOX_SOURCE_DIR}/procps/free.c"
    "${BUSYBOX_SOURCE_DIR}/editors/ed.c"
    "${BUSYBOX_SOURCE_DIR}/miscutils/strings.c"
    "${BUSYBOX_SOURCE_DIR}/procps/uptime.c"
    "${BUSYBOX_SOURCE_DIR}/util-linux/cal.c"
    "${BUSYBOX_SOURCE_DIR}/util-linux/hexdump.c"
    "${BUSYBOX_SOURCE_DIR}/util-linux/hexdump_xxd.c"
    "${BUSYBOX_SOURCE_DIR}/console-tools/clear.c"
    "${BUSYBOX_SOURCE_DIR}/console-tools/reset.c"
    "${BUSYBOX_SOURCE_DIR}/editors/awk.c"
    "${BUSYBOX_SOURCE_DIR}/editors/cmp.c"
    "${BUSYBOX_SOURCE_DIR}/editors/diff.c"
    "${BUSYBOX_SOURCE_DIR}/editors/patch.c"
    "${BUSYBOX_SOURCE_DIR}/editors/ed.c"
    "${BUSYBOX_SOURCE_DIR}/editors/vi.c"
    "${BUSYBOX_SOURCE_DIR}/editors/sed.c"
    "${BUSYBOX_SOURCE_DIR}/findutils/find.c"
    "${BUSYBOX_SOURCE_DIR}/findutils/grep.c"
    "${BUSYBOX_SOURCE_DIR}/findutils/xargs.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xregcomp.c"
        "${BUSYBOX_SOURCE_DIR}/miscutils/bc.c"
    "${BUSYBOX_SOURCE_DIR}/shell/ash.c"
    "${BUSYBOX_SOURCE_DIR}/shell/ash_ptr_hack.c"
    "${BUSYBOX_SOURCE_DIR}/shell/hush.c"
    "${BUSYBOX_SOURCE_DIR}/shell/shell_common.c"
    "${BUSYBOX_SOURCE_DIR}/shell/math.c"
    "${BUSYBOX_SOURCE_DIR}/shell/match.c"

    "${BUSYBOX_SOURCE_DIR}/shell/random.c"
    # Archival Applets & Libarchive
    "${BUSYBOX_SOURCE_DIR}/archival/bbunzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/gzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/bzip2.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/open_transformer.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_gunzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_bunzip2.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_unxz.c"
    "${BUSYBOX_SOURCE_DIR}/archival/lzop.c"
    "${BUSYBOX_SOURCE_DIR}/archival/unzip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/cpio.c"
    "${BUSYBOX_SOURCE_DIR}/archival/ar.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/get_header_ar.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/header_skip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_align.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/filter_accept_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/unsafe_symlink_target.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/common.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/lzo1x_1o.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/lzo1x_1.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/lzo1x_d.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/init_handle.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/unsafe_prefix.c"

    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/get_header_cpio.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/unpack_ar_archive.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/decompress_unlzma.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/filter_accept_all.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/find_list_entry.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/header_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/header_verbose_list.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/seek_by_jump.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/seek_by_read.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_skip.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_extract_all.c"
    "${BUSYBOX_SOURCE_DIR}/archival/libarchive/data_extract_to_stdout.c"
    # Process & Network Applets
    "${BUSYBOX_SOURCE_DIR}/procps/kill.c"
    "${BUSYBOX_SOURCE_DIR}/procps/pidof.c"
    "${BUSYBOX_SOURCE_DIR}/miscutils/time.c"
    "${BUSYBOX_SOURCE_DIR}/networking/httpd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/ping.c"
    "${BUSYBOX_SOURCE_DIR}/networking/ftpgetput.c"
    "${BUSYBOX_SOURCE_DIR}/networking/parse_pasv_epsv.c"
    "${BUSYBOX_SOURCE_DIR}/networking/telnet.c"
    "${BUSYBOX_SOURCE_DIR}/networking/telnetd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/dnsd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/nslookup.c"
    "${BUSYBOX_SOURCE_DIR}/procps/ps.c"
    "${BUSYBOX_SOURCE_DIR}/procps/pgrep.c"
    "${BUSYBOX_SOURCE_DIR}/procps/free.c"
    "${BUSYBOX_SOURCE_DIR}/miscutils/time.c"
    "${BUSYBOX_SOURCE_DIR}/networking/httpd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/ping.c"
    "${BUSYBOX_SOURCE_DIR}/networking/ftpgetput.c"
    "${BUSYBOX_SOURCE_DIR}/networking/parse_pasv_epsv.c"
    "${BUSYBOX_SOURCE_DIR}/networking/telnet.c"
    "${BUSYBOX_SOURCE_DIR}/networking/telnetd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/dnsd.c"
    "${BUSYBOX_SOURCE_DIR}/networking/nslookup.c"
    "${BUSYBOX_SOURCE_DIR}/procps/watch.c"
    "${BUSYBOX_SOURCE_DIR}/networking/nc.c"
    "${BUSYBOX_SOURCE_DIR}/networking/wget.c"

    # Debianutils Applets
    "${BUSYBOX_SOURCE_DIR}/debianutils/which.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/basename.c"
    "${BUSYBOX_SOURCE_DIR}/coreutils/libcoreutils/cp_mv_stat.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/get_last_path_component.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/inode_hash.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/isqrt.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/percent_decode.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/progress.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/read_printf.c"
        "${BUSYBOX_SOURCE_DIR}/libbb/vfork_daemon_rexec.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/find_pid_by_name.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/procps.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_get_servport_by_name.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/xconnect.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/process_escape_sequence.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/warn_ignoring_args.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/single_argv.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/hash_md5_sha.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/uuencode.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/c_escape.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/poll_with_signals.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_getgroups.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/bb_bswap_64.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/fgets_str.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/lineedit.c"
    "${BUSYBOX_SOURCE_DIR}/libbb/find_mount_point.c"


)


# -----------------------------------------------------------------------------
# Optional MSVC Source Transformation (for VLAs and non-standard C90 constructs)
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
        SOURCES ${BUSYBOX_CORE_SOURCES} "${BUSYBOX_SOURCE_DIR}/coreutils/od_bloaty.c"
    )
    list(REMOVE_ITEM BUSYBOX_CORE_SOURCES "${CMAKE_CURRENT_BINARY_DIR}/src_gen/od_bloaty.c")
endif()

# -----------------------------------------------------------------------------
# Target: busybox executable
# -----------------------------------------------------------------------------
message(STATUS "BUSYBOX_CORE_SOURCES count: ${BUSYBOX_CORE_SOURCES}")
add_executable(busybox ${BUSYBOX_CORE_SOURCES})

set_source_files_properties("${BUSYBOX_SOURCE_DIR}/shell/ash.c" PROPERTIES COMPILE_DEFINITIONS "COMPILING_ASH=1;ENABLE_NOMMU=0;CONFIG_NOMMU=0;BB_MMU=1")
set_source_files_properties("${BUSYBOX_SOURCE_DIR}/shell/ash_ptr_hack.c" PROPERTIES COMPILE_DEFINITIONS "COMPILING_ASH=1;ENABLE_NOMMU=0;CONFIG_NOMMU=0;BB_MMU=1")

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
    "${BUSYBOX_SOURCE_DIR}/shell"
)

if(MSVC)
    target_compile_options(busybox PRIVATE
        /FIbusybox_msvc_compat.h
    )
else()
    target_compile_options(busybox PRIVATE
        -include "${BUSYBOX_GEN_INC}/busybox_msvc_compat.h"
    )
endif()

target_compile_definitions(busybox PRIVATE
    _GNU_SOURCE=1
    BB_VER="${BUSYBOX_VERSION}"
    AUTOCONF_TIMESTAMP=""
)

if(TARGET auto-win-msvc)
    target_link_libraries(busybox PRIVATE auto-win-msvc resolv)


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
enable_testing()
file(COPY "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt" DESTINATION "${CMAKE_CURRENT_BINARY_DIR}")
file(RENAME "${CMAKE_CURRENT_BINARY_DIR}/tsort_input.txt" "${CMAKE_CURRENT_BINARY_DIR}/tests_gz.txt")
add_test(NAME test_busybox_help COMMAND busybox --help)
add_test(NAME test_busybox_ash COMMAND busybox ash -c "echo ash_works")
add_test(NAME test_busybox_hush COMMAND busybox hush -c "echo hush_works")
add_test(NAME test_busybox_sh COMMAND busybox sh -c "echo sh_works")

add_test(NAME test_busybox_echo COMMAND busybox echo "BusyBox native build successful")
add_test(NAME test_busybox_true COMMAND busybox true)
add_test(NAME test_busybox_basename COMMAND busybox basename "foo/bar/baz.txt" ".txt")
add_test(NAME test_busybox_dirname COMMAND busybox dirname "foo/bar/baz.txt")
add_test(NAME test_busybox_pwd COMMAND busybox pwd)
add_test(NAME test_busybox_test COMMAND busybox test 1 -eq 1)
add_test(NAME test_busybox_false COMMAND busybox false)
set_tests_properties(test_busybox_false PROPERTIES WILL_FAIL TRUE)
add_test(NAME test_busybox_printf COMMAND busybox printf "%s %d\n" "value:" 42)
add_test(NAME test_busybox_wc COMMAND busybox wc "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
add_test(NAME test_busybox_expand COMMAND busybox expand "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
add_test(NAME test_busybox_unexpand COMMAND busybox unexpand "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
add_test(NAME test_busybox_tsort COMMAND busybox tsort "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_realpath COMMAND busybox realpath "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
add_test(NAME test_busybox_mktemp COMMAND busybox mktemp -u)
add_test(NAME test_busybox_shuf COMMAND busybox shuf -i 1-10 -n 3)
add_test(NAME test_busybox_uuencode COMMAND busybox uuencode "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt" "cmake.txt")
add_test(NAME test_busybox_readlink COMMAND busybox readlink -f "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
add_test(NAME test_busybox_split COMMAND busybox split -l 100 "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt" "${CMAKE_CURRENT_BINARY_DIR}/split_test_")
add_test(NAME test_busybox_truncate COMMAND busybox truncate -s 16 "${CMAKE_CURRENT_BINARY_DIR}/trunc_test.txt")
add_test(NAME test_busybox_unlink COMMAND busybox unlink "${CMAKE_CURRENT_BINARY_DIR}/trunc_test.txt")
add_test(NAME test_busybox_base32 COMMAND busybox base32 "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_cal COMMAND busybox cal 10 2026)
add_test(NAME test_busybox_chmod COMMAND busybox chmod 644 "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_date COMMAND busybox date "+%Y-%m-%d")
add_test(NAME test_busybox_env COMMAND busybox env)
add_test(NAME test_busybox_factor COMMAND busybox factor 42)
add_test(NAME test_busybox_hexdump COMMAND busybox hexdump -C "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_id COMMAND busybox id)
add_test(NAME test_busybox_od COMMAND busybox od -c "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_sha3sum COMMAND busybox sha3sum "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_strings COMMAND busybox strings "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_uptime COMMAND busybox uptime)
add_test(NAME test_busybox_xxd COMMAND busybox xxd "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_awk COMMAND busybox awk "BEGIN { print 6 * 7; exit }")
add_test(NAME test_busybox_bc COMMAND busybox bc "${CMAKE_CURRENT_SOURCE_DIR}/tests/bc_test.txt")
add_test(NAME test_busybox_dc COMMAND busybox dc -e "6 7 * p")
add_test(NAME test_busybox_clear COMMAND busybox clear)
add_test(NAME test_busybox_cmp COMMAND busybox cmp "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt" "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_diff COMMAND busybox diff -u "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt" "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_patch COMMAND busybox patch --help)
add_test(NAME test_busybox_reset COMMAND busybox reset)
add_test(NAME test_busybox_sed COMMAND busybox sed "s/a/z/g" "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_grep COMMAND busybox grep "b" "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_egrep COMMAND busybox egrep "[bc]" "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_fgrep COMMAND busybox fgrep "b c" "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_find COMMAND busybox find "${CMAKE_CURRENT_SOURCE_DIR}/tests" -name "*.txt")
add_test(NAME test_busybox_xargs COMMAND busybox xargs --help)
add_test(NAME test_busybox_kill COMMAND busybox kill -l)
add_test(NAME test_busybox_nc COMMAND busybox nc)
set_tests_properties(test_busybox_nc PROPERTIES WILL_FAIL TRUE)
add_test(NAME test_busybox_gzip COMMAND busybox gzip "${CMAKE_CURRENT_BINARY_DIR}/tests_gz.txt")
add_test(NAME test_busybox_gunzip COMMAND busybox gunzip "${CMAKE_CURRENT_BINARY_DIR}/tests_gz.txt.gz")
add_test(NAME test_busybox_ls COMMAND busybox ls "${CMAKE_CURRENT_SOURCE_DIR}/tests")
add_test(NAME test_busybox_du COMMAND busybox du "${CMAKE_CURRENT_SOURCE_DIR}/tests")
add_test(NAME test_busybox_pidof COMMAND busybox pidof busybox)
set_tests_properties(test_busybox_pidof PROPERTIES WILL_FAIL TRUE)
add_test(NAME test_busybox_wget COMMAND busybox wget --help)
add_test(NAME test_busybox_watch COMMAND busybox watch --help)
add_test(NAME test_busybox_crc32 COMMAND busybox crc32 "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_stat COMMAND busybox stat "${CMAKE_CURRENT_SOURCE_DIR}/tests/tsort_input.txt")
add_test(NAME test_busybox_time COMMAND busybox time echo 1)
add_test(NAME test_busybox_ed COMMAND busybox ed --help)
add_test(NAME test_busybox_bzip2 COMMAND busybox bzip2 --help)
add_test(NAME test_busybox_bunzip2 COMMAND busybox bunzip2 --help)
add_test(NAME test_busybox_unxz COMMAND busybox unxz --help)
add_test(NAME test_busybox_xzcat COMMAND busybox xzcat --help)
add_test(NAME test_busybox_bzcat COMMAND busybox bzcat --help)
add_test(NAME test_busybox_df COMMAND busybox df --help)
add_test(NAME test_busybox_free COMMAND busybox free)
add_test(NAME test_busybox_lzop COMMAND busybox lzop --help)
add_test(NAME test_busybox_unzip COMMAND busybox unzip --help)
add_test(NAME test_busybox_zip COMMAND busybox zip --help)
add_test(NAME test_busybox_cpio COMMAND busybox cpio --help)
add_test(NAME test_busybox_ar COMMAND busybox ar --help)
add_test(NAME test_busybox_ps COMMAND busybox ps --help)
add_test(NAME test_busybox_pgrep COMMAND busybox pgrep --help)
add_test(NAME test_busybox_pkill COMMAND busybox pkill --help)
add_test(NAME test_busybox_httpd COMMAND busybox httpd --help)
add_test(NAME test_busybox_ping COMMAND busybox ping --help)
add_test(NAME test_busybox_ftpget COMMAND busybox ftpget --help)
add_test(NAME test_busybox_ftpput COMMAND busybox ftpput --help)
add_test(NAME test_busybox_telnet COMMAND busybox telnet --help)
add_test(NAME test_busybox_dnsd COMMAND busybox dnsd --help)
add_test(NAME test_busybox_nslookup COMMAND busybox nslookup --help)
# Porting upstream testsuite
add_custom_target(busybox_upstream_testsuite_config ALL
    COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_BINARY_DIR}/include/autoconf.h" "${CMAKE_CURRENT_BINARY_DIR}/.config"
)
add_test(NAME test_upstream_testsuite
         COMMAND busybox ash "${BUSYBOX_SOURCE_DIR}/testsuite/runtest"
         WORKING_DIRECTORY "${BUSYBOX_SOURCE_DIR}/testsuite")
set_tests_properties(test_upstream_testsuite PROPERTIES
                     ENVIRONMENT "bindir=${CMAKE_CURRENT_BINARY_DIR};PATH=${CMAKE_CURRENT_BINARY_DIR};LC_ALL=C")

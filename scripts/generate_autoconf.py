#!/usr/bin/env python3
import sys
import os
import re

def main():
    if len(sys.argv) < 3:
        print("Usage: generate_autoconf.py <busybox_dir> <output_header>")
        sys.exit(1)

    busybox_dir = sys.argv[1]
    output_path = sys.argv[2]

    configs = set()
    for root, dirs, files in os.walk(busybox_dir):
        if ".git" in root:
            continue
        for f in files:
            if f.endswith((".c", ".h", ".in", ".src")):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                        matches = re.findall(r"\bCONFIG_([A-Za-z0-9_]+)\b", content)
                        configs.update(matches)
                except Exception:
                    pass

    for root, dirs, files in os.walk(busybox_dir):
        if ".git" in root:
            continue
        for f in files:
            if f.endswith((".c", ".h")):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                        for m in re.findall(r"\bENABLE_([A-Za-z0-9_]+)\b", content):
                            configs.add(m)
                        for m in re.findall(r"\bIF_([A-Za-z0-9_]+)\(", content):
                            if m.startswith("NOT_"):
                                m = m[4:]
                            configs.add(m)
                except Exception:
                    pass

    enabled = {
        "HAVE_DOT_CONFIG", "HAVE_SYS_STATFS_H", "HAVE_MNTENT_H", "DESKTOP", "INCLUDE_SUSv2",
        "LONG_OPTS", "SHOW_USAGE", "FEATURE_VERBOSE_USAGE",
        "BUSYBOX", "NOMMU", "FEATURE_PREFER_APPLETS",
        "ECHO", "TRUE", "FALSE", "TEST", "TEST1", "TEST2",
        "PRINTF", "PRINTENV", "PWD", "CAT", "HEAD", "TAIL", "WC", "YES",
        "SLEEP", "USLEEP", "BASENAME", "DIRNAME", "WHOAMI", "UNAME",
        "WHICH", "CUT", "TR", "SEQ", "TEE", "SORT", "UNIQ", "COMM",
        "PASTE", "FOLD", "BASE64", "MD5SUM", "SHA1SUM", "SHA256SUM", "SHA384SUM", "BASE32", "FEATURE_FANCY_HEAD",
        "SHA512SUM", "CKSUM", "SUM", "TOUCH", "MKDIR", "RMDIR",
        "CP", "MV", "RM", "SYNC",
        "EXPAND", "UNEXPAND", "TSORT", "SPLIT", "SHUF",
        "UUENCODE", "UUDECODE", "READLINK", "REALPATH",
        "TRUNCATE", "MKTEMP", "UNLINK", "FEATURE_READLINK_FOLLOW", "FEATURE_SPLIT_FANCY",
        "BASE32", "CAL", "CHMOD", "DATE", "DOS2UNIX", "ENV", "FACTOR",
        "HEXDUMP", "ID", "LN", "OD", "SHA3SUM", "STRINGS",
        "UNIX2DOS", "UPTIME", "XXD", "FEATURE_STAT_FORMAT", "FEATURE_DATE_ISOFMT",
        "AWK", "BC", "DC", "CLEAR", "RESET", "CMP", "DIFF", "PATCH", "ED",
        "VI", "FEATURE_VI_MAX_LEN", "FEATURE_VI_8BIT", "FEATURE_VI_COLON", "FEATURE_VI_YANKMARK", "FEATURE_VI_SEARCH", "FEATURE_VI_USE_SIGNALS", "FEATURE_VI_DOT_CMD", "FEATURE_VI_READONLY", "FEATURE_VI_SETOPTS", "FEATURE_VI_SET", "FEATURE_VI_WIN_RESIZE", "FEATURE_VI_ASK_TERMINAL", "FEATURE_VI_UNDO", "FEATURE_VI_UNDO_QUEUE", "FEATURE_VI_UNDO_QUEUE_MAX",
        "SED", "GREP", "EGREP", "FGREP", "FIND", "XARGS", "FEATURE_GREP_CONTEXT",
        "FEATURE_AWK_LIBM", "FEATURE_AWK_GNU_EXTENSIONS", "FEATURE_XARGS_SUPPORT_CONFIRMATION",
        "FEATURE_XARGS_SUPPORT_QUOTES", "FEATURE_XARGS_SUPPORT_TERMOPT", "FEATURE_XARGS_SUPPORT_ZERO_TERM",
        "GZIP", "FEATURE_GZIP_DECOMPRESS", "GUNZIP", "ZCAT",
        "BZIP2", "BUNZIP2", "BZCAT", "UNXZ", "XZCAT",
        "LZOP", "LZOPCAT", "UNLZOP", "FEATURE_LZOP_COMPR_HIGH",
        "UNZIP", "FEATURE_UNZIP_BZIP2", "FEATURE_UNZIP_LZMA", "FEATURE_UNZIP_XZ",
        "ZIP", "CPIO", "FEATURE_CPIO_O", "FEATURE_CPIO_P", "AR", "FEATURE_AR_CREATE", "FEATURE_AR_LONG_FILENAMES",
        "KILL", "KILLALL", "TIME", "WATCH", "NC", "NC_SERVER",
        "HTTPD", "PING", "PING6", "FTPGET", "FTPPUT", "TELNET", "TELNETD", "DNSD", "NSLOOKUP",
        "LS", "FEATURE_LS_FILETYPES", "FEATURE_LS_FOLLOWLINKS",
        "FEATURE_LS_RECURSIVE", "FEATURE_LS_WIDTH", "FEATURE_LS_SORTFILES",
        "FEATURE_LS_TIMESTAMPS", "DU", "FEATURE_DU_DEFAULT_BLOCKSIZE_1K",
        "PIDOF", "FEATURE_PIDOF_SINGLE", "FEATURE_PIDOF_OMIT",
        "TIME", "PS", "FEATURE_PS_TIME", "FEATURE_PS_ADDITIONAL_COLUMNS", "FEATURE_PS_UNUSUAL_SYSTEMS", "FEATURE_PS_LONG", "PGREP", "PKILL", "FREE",
        "WGET", "FEATURE_WGET_STATUSBAR", "FEATURE_WGET_AUTHENTICATION",
        "FEATURE_WGET_TIMEOUT",
        "CRC32", "STAT", "FEATURE_STAT_FILESYSTEM", "DF", "FREE",
        "ASH", "HUSH", "SH", "ASH_BASH_COMPAT", "ASH_ALIAS", "ASH_OPTIMIZE_FOR_SIZE", "ASH_INTERNAL_GLOB", "ASH_EXPAND_PRMT", "HUSH_BASH_COMPAT", "HUSH_BRACE_EXPANSION", "HUSH_INTERACTIVE", "HUSH_SAVEHISTORY", "HUSH_JOB", "HUSH_TICK", "HUSH_IF", "HUSH_LOOPS", "HUSH_CASE", "HUSH_FUNCTIONS", "HUSH_LOCAL", "HUSH_RANDOM_SUPPORT", "HUSH_MODE_X", "HUSH_ECHO", "HUSH_PRINTF", "HUSH_TEST", "HUSH_HELP", "HUSH_EXPORT", "HUSH_EXPORT_N", "HUSH_READONLY", "HUSH_KILL", "HUSH_WAIT", "HUSH_TRAP", "HUSH_TYPE", "HUSH_TIMES", "HUSH_READ", "HUSH_SET", "HUSH_UNSET", "HUSH_ULIMIT", "HUSH_UMASK", "HUSH_GETOPTS", "HUSH_MEMLEAK", "FEATURE_SH_IS_ASH", "FEATURE_BASH_IS_ASH", "FEATURE_SH_STANDALONE", "FEATURE_SH_NOFORK", "FEATURE_SH_HISTFILESIZE",

    }

    numeric_defaults = {
        "FEATURE_COPYBUF_KB": "4",
        "PASSWORD_MINLEN": "6",
        "MD5_SMALL": "1",
        "SHA1_SMALL": "3",
        "SHA3_SMALL": "1",
        "FEATURE_EDITING_MAX_LEN": "1024",
        "FEATURE_EDITING_HISTORY": "255",
        "SUBST_WCHAR": "65533",
        "LAST_SUPPORTED_WCHAR": "0",
        "BZIP2_SMALL": "8",
        "GZIP_FAST": "0",
        "FEATURE_VI_MAX_LEN": "4096",
        "FEATURE_VI_UNDO_QUEUE_MAX": "256",
        "FEATURE_KILL_DELAY": "0",
        "LAST_ID": "60000",
        "FIRST_SYSTEM_ID": "100",
        "LAST_SYSTEM_ID": "999",
        "FEATURE_BEEP_FREQ": "0",
        "FEATURE_BEEP_LENGTH_MS": "0",
        "FEATURE_LESS_MAXLINES": "9999999",
        "FEATURE_HTTPD_PORT_DEFAULT": "80",
        "FEATURE_TELNETD_PORT_DEFAULT": "23",
        "UDHCP_DEBUG": "0",
        "UDHCPC_SLACK_FOR_BUGGY_SERVERS": "0",
        "FEATURE_SYSLOGD_READ_BUFFER_SIZE": "256",
        "FEATURE_IPC_SYSLOG_BUFFER_SIZE": "16",
    }

    lines = [
        "/* Generated by generate_autoconf.py for busybox-windows */",
        "#ifndef AUTOCONF_H",
        "#define AUTOCONF_H 1",
        "",
        '#define CONFIG_BUSYBOX_EXEC_PATH "busybox.exe"',
        "#ifndef HAVE_MNTENT_H",
        "#define HAVE_MNTENT_H 1",
        "#endif",
        "#ifndef HAVE_SYS_STATFS_H",
        "#define HAVE_SYS_STATFS_H 1",
        "#endif",
        "#if defined(__APPLE__)",
        "#ifndef st_atim",
        "#define st_atim st_atimespec",
        "#define st_mtim st_mtimespec",
        "#define st_ctim st_ctimespec",
        "#endif",
        "#ifndef f_namelen",
        "#define f_namelen f_flags",
        "#endif",
        "#endif",
        ""
    ]

    for sym in sorted(configs):
        if sym in ("BUSYBOX_EXEC_PATH", "LITTLE_ENDIAN", "BIG_ENDIAN", "VARIABLE_ARCH_PAGESIZE"):
            continue
        if sym in numeric_defaults:
            val = numeric_defaults[sym]
            lines.append(f"#define CONFIG_{sym} {val}")
            lines.append(f"#define ENABLE_{sym} 1")
            lines.append(f"#define IF_{sym}(...) __VA_ARGS__")
            lines.append(f"#define IF_NOT_{sym}(...)")
        elif sym in enabled:
            lines.append(f"#define CONFIG_{sym} 1")
            lines.append(f"#define ENABLE_{sym} 1")
            lines.append(f"#define IF_{sym}(...) __VA_ARGS__")
            lines.append(f"#define IF_NOT_{sym}(...)")
        else:
            lines.append(f"#define CONFIG_{sym} 0")
            lines.append(f"#define ENABLE_{sym} 0")
            lines.append(f"#define IF_{sym}(...)")
            lines.append(f"#define IF_NOT_{sym}(...) __VA_ARGS__")
        lines.append("")

    lines.append("#endif /* AUTOCONF_H */\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out:
        out.write("\n".join(lines))
    print(f"Generated {output_path} with {len(configs)} configuration symbols.")

if __name__ == "__main__":
    main()

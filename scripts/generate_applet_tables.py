#!/usr/bin/env python3
import sys
import os

APPLETS = [
    ("ash", "ash", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("hush", "hush", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("sh", "ash", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),

    ("awk", "awk", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("base32", "baseNUM", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("base64", "baseNUM", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("basename", "basename", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("bc", "bc", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("bunzip2", "bunzip2", "BB_DIR_USR_BIN", "BB_SUID_DROP", "APPLET"),
    ("bzcat", "bunzip2", "BB_DIR_USR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("bzip2", "bzip2", "BB_DIR_USR_BIN", "BB_SUID_DROP", "APPLET"),
    ("cal", "cal", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("cat", "cat", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("chmod", "chmod", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("cksum", "cksum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("clear", "clear", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("cmp", "cmp", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("comm", "comm", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("cp", "cp", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("crc32", "cksum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("cut", "cut", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("date", "date", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("dc", "dc", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("df", "df", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("diff", "diff", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("dirname", "dirname", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("dos2unix", "dos2unix", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("du", "du", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("echo", "echo", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("ed", "ed", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("egrep", "grep", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("env", "env", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("expand", "expand", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("factor", "factor", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("false", "false", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("fgrep", "grep", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("find", "find", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("fold", "fold", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("free", "free", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("grep", "grep", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("gunzip", "gunzip", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("gzip", "gzip", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("head", "head", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("hexdump", "hexdump", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("id", "id", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("kill", "kill", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("killall", "kill", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("ln", "ln", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("ls", "ls", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("md5sum", "md5_sha1_sum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("mkdir", "mkdir", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("mktemp", "mktemp", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("mv", "mv", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("nc", "nc", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("od", "od", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("paste", "paste", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("patch", "patch", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("pidof", "pidof", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("printenv", "printenv", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("printf", "printf", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("pwd", "pwd", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("readlink", "readlink", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("realpath", "realpath", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("reset", "reset", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("rm", "rm", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("rmdir", "rmdir", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("sed", "sed", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("seq", "seq", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("sha1sum", "md5_sha1_sum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("sha256sum", "md5_sha1_sum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("sha3sum", "md5_sha1_sum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("sha512sum", "md5_sha1_sum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("shuf", "shuf", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("sleep", "sleep", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("sort", "sort", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("split", "split", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("stat", "stat", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("strings", "strings", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("sum", "sum", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("sync", "sync", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("tail", "tail", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("tee", "tee", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("test", "test", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("time", "time", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("touch", "touch", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("tr", "tr", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("true", "true", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("truncate", "truncate", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("tsort", "tsort", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("uname", "uname", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("unexpand", "expand", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("uniq", "uniq", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("unix2dos", "dos2unix", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("unlink", "unlink", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("unxz", "unxz", "BB_DIR_USR_BIN", "BB_SUID_DROP", "APPLET"),
    ("uptime", "uptime", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("usleep", "usleep", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("uudecode", "uudecode", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("uuencode", "uuencode", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("watch", "watch", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("wc", "wc", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("wget", "wget", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("which", "which", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET"),
    ("whoami", "whoami", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("xargs", "xargs", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("xxd", "xxd", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOEXEC"),
    ("xzcat", "unxz", "BB_DIR_USR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
    ("yes", "yes", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_NOFORK"),
    ("zcat", "gunzip", "BB_DIR_BIN", "BB_SUID_DROP", "APPLET_ODDNAME"),
]

def main():
    if len(sys.argv) < 2:
        print("Usage: generate_applet_tables.py <output_inc_dir>")
        sys.exit(1)

    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)

    applets = sorted(APPLETS, key=lambda a: a[0])
    num_applets = len(applets)

    # 1. NUM_APPLETS.h
    with open(os.path.join(out_dir, "NUM_APPLETS.h"), "w", encoding="utf-8") as f:
        f.write(f"#define NUM_APPLETS {num_applets}\n")

    # 2. applets.h
    with open(os.path.join(out_dir, "applets.h"), "w", encoding="utf-8") as f:
        f.write("""/* Generated by generate_applet_tables.py */
#ifndef BUSYBOX_APPLETS_H
#define BUSYBOX_APPLETS_H 1

#if defined(PROTOTYPES)
# define APPLET(name,l,s) int name##_main(int argc, char **argv) MAIN_EXTERNALLY_VISIBLE;
# define APPLET_ODDNAME(name,main,l,s) int main##_main(int argc, char **argv) MAIN_EXTERNALLY_VISIBLE;
# define APPLET_NOEXEC(name,main,l,s) int main##_main(int argc, char **argv) MAIN_EXTERNALLY_VISIBLE;
# define APPLET_NOFORK(name,main,l,s) int main##_main(int argc, char **argv) MAIN_EXTERNALLY_VISIBLE;
#elif defined(NAME_MAIN)
# define APPLET(name,l,s) name name##_main
# define APPLET_ODDNAME(name,main,l,s) name main##_main
# define APPLET_NOEXEC(name,main,l,s) name main##_main
# define APPLET_NOFORK(name,main,l,s) name main##_main
#elif defined(MAKE_USAGE)
# define APPLET(name,l,s) name##_trivial_usage "\\0" name##_full_usage "\\0"
# define APPLET_ODDNAME(name,main,l,s) name##_trivial_usage "\\0" name##_full_usage "\\0"
# define APPLET_NOEXEC(name,main,l,s) name##_trivial_usage "\\0" name##_full_usage "\\0"
# define APPLET_NOFORK(name,main,l,s) name##_trivial_usage "\\0" name##_full_usage "\\0"
#elif defined(MAKE_LINKS)
# define APPLET(name,l,s) LINK l name
# define APPLET_ODDNAME(name,main,l,s) LINK l name
# define APPLET_NOEXEC(name,main,l,s) LINK l name
# define APPLET_NOFORK(name,main,l,s) LINK l name
#else
  const struct bb_applet applets[] = {
# define APPLET(name,l,s) { #name, #name, l, s, 0, 0 },
# define APPLET_ODDNAME(name,main,l,s) { #name, #main, l, s, 0, 0 },
# define APPLET_NOEXEC(name,main,l,s) { #name, #main, l, s, 1, 0 },
# define APPLET_NOFORK(name,main,l,s) { #name, #main, l, s, 0, 1 },
#endif

""")
        for name, main_fn, loc, suid, macro in applets:
            cfg = name.upper()
            f.write(f"#if ENABLE_{cfg}\n")
            if macro in ("APPLET_ODDNAME", "APPLET_NOEXEC", "APPLET_NOFORK"):
                f.write(f"IF_{cfg}({macro}({name}, {main_fn}, {loc}, {suid}))\n")
            else:
                f.write(f"IF_{cfg}({macro}({name}, {loc}, {suid}))\n")
            f.write(f"#endif\n")
        f.write("""
#if !defined(PROTOTYPES) && !defined(NAME_MAIN) && !defined(MAKE_USAGE) && !defined(MAKE_LINKS)
};
#endif

#endif /* BUSYBOX_APPLETS_H */
""")

    # 3. applet_tables.h
    with open(os.path.join(out_dir, "applet_tables.h"), "w", encoding="utf-8") as f:
        f.write("""/* Generated by generate_applet_tables.py */
#ifndef APPLET_TABLES_H
#define APPLET_TABLES_H 1

#include "NUM_APPLETS.h"

const char applet_names[] ALIGN1 = ""
""")
        for name, _, _, _, _ in applets:
            f.write(f'"{name}\\0"\n')
        f.write(";\n\n")

        for i, (name, _, _, _, _) in enumerate(applets):
            f.write(f"#define APPLET_NO_{name} {i}\n")
        f.write("\n")

        f.write("#ifndef SKIP_applet_main\n")
        f.write("int (*const applet_main[])(int argc, char **argv) = {\n")
        for _, main_fn, _, _, _ in applets:
            f.write(f"{main_fn}_main,\n")
        f.write("};\n")
        f.write("#endif\n\n")

        # applet_flags calculation
        f.write("const uint8_t applet_flags[] ALIGN1 = {\n")
        flags = []
        for _, _, _, _, macro in applets:
            noexec = 1 if macro == "APPLET_NOEXEC" else 0
            nofork = 1 if macro == "APPLET_NOFORK" else 0
            flags.append((nofork, noexec))

        i = 0
        byte_vals = []
        while i < num_applets:
            v = flags[i][0] + (flags[i][1] << 1)
            if i + 1 < num_applets:
                v |= (flags[i+1][0] + (flags[i+1][1] << 1)) << 2
            if i + 2 < num_applets:
                v |= (flags[i+2][0] + (flags[i+2][1] << 1)) << 4
            if i + 3 < num_applets:
                v |= (flags[i+3][0] + (flags[i+3][1] << 1)) << 6
            byte_vals.append(f"0x{v:02x}")
            i += 4
        f.write(", ".join(byte_vals) + "\n};\n\n")
        f.write("#endif /* APPLET_TABLES_H */\n")

    print(f"Generated applets headers for {num_applets} applets in {out_dir}")

if __name__ == "__main__":
    main()

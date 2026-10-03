#!/usr/bin/env python3
"""
Automated Source Transformation Engine for MSVC (auto-win-msvc).
Transforms unsupported C99 constructs (such as Variable Length Arrays)
into strict C89 / MSVC-compatible constructs at build time.
"""

import argparse
import os
import re
import sys


def transform_source_content(content: str) -> str:
    """
    Apply AST-like heuristic regex rewrites to eliminate VLAs and C99 idioms.
    """
    # 1. Transform memcached.c extras_len VLA pattern
    # Original:
    #   size_t extras_len = sizeof(":unix:") + sizeof("65535");
    #   char addr[MAXPATHLEN + extras_len];
    #   char svr_addr[MAXPATHLEN + extras_len];
    memcached_vla_pattern = re.compile(
        r'(\s*)size_t\s+extras_len\s*=\s*sizeof\(":unix:"\)\s*\+\s*sizeof\("65535"\);\s*\n'
        r'\s*char\s+addr\[MAXPATHLEN\s*\+\s*extras_len\];\s*\n'
        r'\s*char\s+svr_addr\[MAXPATHLEN\s*\+\s*extras_len\];',
        re.MULTILINE,
    )
    if memcached_vla_pattern.search(content):
        replacement = (
            "\\1#ifndef MC_EXTRAS_LEN\n"
            '\\1#define MC_EXTRAS_LEN (sizeof(":unix:") + sizeof("65535"))\n'
            "\\1#endif\n"
            "\\1size_t extras_len = MC_EXTRAS_LEN;\n"
            "\\1char addr[MAXPATHLEN + MC_EXTRAS_LEN];\n"
            "\\1char svr_addr[MAXPATHLEN + MC_EXTRAS_LEN];"
        )
        content = memcached_vla_pattern.sub(replacement, content)

    # 2. Transform proto_bin.c mech[nkey+1] VLA pattern
    # Original:
    #   char mech[nkey+1];
    proto_bin_vla = re.compile(r"char\s+mech\[nkey\s*\+\s*1\];")
    content = proto_bin_vla.sub(
        r"char mech[256]; /* bounded by protocol max key length */", content
    )

    # 3. Generic VLA pattern: char name[expr] where expr has non-constant identifier
    has_alloca = False

    def vla_alloca_repl(match):
        nonlocal has_alloca
        indent = match.group(1)
        type_str = match.group(2)
        var_name = match.group(3)
        expr = match.group(4).strip()
        # Avoid matching constant expressions (numbers, sizeof, macros, uppercase expressions, compile-time assertions)
        if re.match(r"^[0-9A-Z_+\-*/ ()\t?:=<>!&|]+$", expr) or "sizeof" in expr or "ARRAY_SIZE" in expr:
            return match.group(0)
        has_alloca = True
        return (
            f"{indent}{type_str} *{var_name} = "
            f"({type_str} *)_alloca(({expr}) * sizeof({type_str}));"
        )

    # Transform local variable arrays: e.g. "    char temp[len + 1];" or "    char *tmp_argv[argc + 1];"
    generic_vla_pattern = re.compile(
        r"^(\s+)((?:unsigned\s+)?(?:char|int|uint8_t|uint16_t|uint32_t|int32_t)(?:\s*\*+)?)\s*([a-zA-Z0-9_]+)\[([^\]\n]+)\];",
        re.MULTILINE,
    )
    content = generic_vla_pattern.sub(vla_alloca_repl, content)

    # Transform xvfork() invocation into inline function call
    content = re.sub(r'\bxvfork\(\)', 'bb_libbb_xvfork()', content)

    if has_alloca and "#include <malloc.h>" not in content:
        content = "/* clang-format off */\n#include <malloc.h>\n/* clang-format on */\n" + content

    # 4. Transform GNU binary ternary (elvis operator) x ?: y into (x ? x : y)
    elvis_pattern = re.compile(
        r"((?:\([^)]+\)|[a-zA-Z0-9_]+(?:\([^)]*\)|(?:\s*(?:->|\.)\s*[a-zA-Z0-9_]+))*))\s*\?\s*:\s*([^,\);]+)"
    )
    content = elvis_pattern.sub(r"(\1 ? \1 : \2)", content)

    # 5. Transform GNU self-initializing dummy warning suppressors (var = var) into (var = 0)
    # This prevents MSVC /RTC1 runtime failure for reading uninitialized stack memory.
    self_init_pattern = re.compile(
        r"\b((?:(?:const|unsigned|signed|struct|enum|long|short)\s+)*[a-zA-Z0-9_]+)(?:\s+(\*+)?|\s*(\*+))\s*([a-zA-Z0-9_]+)\s*=\s*\4\s*;"
    )

    def self_init_repl(m):
        prefix = m.group(1)
        stars = (m.group(2) or "") + (m.group(3) or "")
        var = m.group(4)
        if stars:
            return f"{prefix} {stars}{var} = 0;"
        return f"{prefix} {var} = 0;"

    content = self_init_pattern.sub(self_init_repl, content)

    # 6. Add return 0 after fflush_stdout_and_exit calls to satisfy MSVC C4716 / RTC
    fflush_pattern = re.compile(r'(fflush_stdout_and_exit(?:_SUCCESS)?\s*\([^;]*\);)')
    content = fflush_pattern.sub(r'\1 return 0;', content)

    # 7. Disambiguate Windows SDK typedef collisions in od_bloaty.c (CHAR, SHORT, INT, LONG)
    if "enum size_spec {" in content:
        content = re.sub(r'\b(CHAR|SHORT|INT|LONG)\b', r'OD_\1', content)

    # 8. Transform xargs ISSPACE statement expression
    content = content.replace(
        "#define ISSPACE(a) ({ unsigned char xargs__isspace = (a) - 9; xargs__isspace == (\x27 \x27 - 9) || xargs__isspace <= (13 - 9); })",
        "#define ISSPACE(a) (isspace((unsigned char)(a)))",
    )

    # 9. Support MSVC RAND_MAX (0x7fff) in awk.c
    content = content.replace(
        "# error Not implemented for this value of RAND_MAX",
        "uint32_t u = ((uint32_t)rand() << 16) | (uint32_t)rand();\n\t\t\t\tR_d = (double)u / 4294967296.0;",
    )

    # 10. Fix static declaration mismatch in sed.c
    content = content.replace(
        "void sed_free_and_close_stuff(void);",
        "static void sed_free_and_close_stuff(void) {}",
    )

    # 11. Fix uninitialized variables and missing return in awk.c
    content = content.replace("} L = L; /* for compiler */", "} L; memset(&L, 0, sizeof(L));")
    content = content.replace("} R = R;", "} R; memset(&R, 0, sizeof(R));")
    content = content.replace("double L_d = L_d;", "double L_d = 0;")
    content = content.replace("/*return 0;*/", "return 0;")

    # 12. Pack header union in decompress_gunzip.c for MSVC
    content = re.sub(
        r"union\s*\{\s*(?:unsigned\s+char|uint8_t)\s+raw\[8\];",
        "#if defined(_MSC_VER)\n#pragma pack(push, 1)\n#endif\n\tunion {\n\t\tunsigned char raw[8];",
        content,
    )
    content = content.replace(
        "} PACKED formatted;\n\t} header;",
        "} PACKED formatted;\n\t} header;\n#if defined(_MSC_VER)\n#pragma pack(pop)\n#endif",
    )

    # 13. Stub out reset_ino_dev_hashtable call in callers when clean up is disabled
    content = content.replace("reset_ino_dev_hashtable();", "((void)0);")

    # 14. Stat MSVC member compatibility
    if "printf_s(char *pformat" in content:
        content = re.sub(r'\bprintf_s\(', 'bb_printf_s(', content)
    content = re.sub(r'human_time\(&([a-zA-Z0-9_]+(?:->|\.)st_)atim\)', r'human_time((struct timespec*)&(\1atime))', content)
    content = re.sub(r'human_time\(&([a-zA-Z0-9_]+(?:->|\.)st_)mtim\)', r'human_time((struct timespec*)&(\1mtime))', content)
    content = re.sub(r'human_time\(&([a-zA-Z0-9_]+(?:->|\.)st_)ctim\)', r'human_time((struct timespec*)&(\1ctime))', content)
    content = re.sub(r'([a-zA-Z0-9_]+(?:->|\.))st_blksize\b', r'4096', content)
    content = re.sub(r'([a-zA-Z0-9_]+(?:->|\.))st_blocks\b', r'(\1st_size / 512)', content)

    # 15. Fix uninitialized variable in df.c
    content = content.replace("char *chp, *opt_t;", "char *chp = NULL, *opt_t = NULL;")

    # 17. Prevent bc from reading stdin if quit was encountered in input file
    content = content.replace("if (IS_BC || (option_mask32 & BC_FLAG_I))", "if (!G_exiting && (IS_BC || (option_mask32 & BC_FLAG_I)))")

    # 18. Map xvfork() in time.c to spawn()
    content = re.sub(
        r"pid\s*=\s*xvfork\(\)\s*;\s*if\s*\(\s*pid\s*==\s*0\s*\)\s*\{\s*/\*\s*Child\s*\*/\s*BB_EXECVP_or_die\(\s*\(char\*\*\)cmd\s*\)\s*;\s*\}",
        "pid = spawn((char**)cmd);",
        content
    )

    # 16. Fix missing return in yes.c and uudecode.c
    content = re.sub(r'(while\s*\(\s*full_write\s*\([^;]*\s*==\s*len\s*\)\s*continue\s*;)', r'\1 return 0;', content)
    content = content.replace("die_if_ferror(src_stream, fn);", "die_if_ferror(src_stream, fn); return 0;")
    content = re.sub(r'(bb_perror_nomsg_and_die\([^;]*\);)', r'\1 return 0;', content)
    content = re.sub(r'(bb_simple_error_msg_and_die\([^;]*\);)', r'\1 return 0;', content)

    return content


def transform_file(input_path: str, output_path: str) -> None:
    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        original = f.read()

    transformed = transform_source_content(original)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(transformed)


def main():
    parser = argparse.ArgumentParser(
        description="Transform C source files for MSVC compatibility."
    )
    parser.add_argument("inputs", nargs="+", help="Input file(s) or input/output pair")
    parser.add_argument("--output", "-o", help="Output file path (for single input)")
    parser.add_argument(
        "--out-dir", "-d", help="Output directory for generated sources"
    )

    args = parser.parse_args()

    if args.output:
        if len(args.inputs) != 1:
            print("Error: --output can only be used with a single input file.")
            sys.exit(1)
        transform_file(args.inputs[0], args.output)
    elif args.out_dir:
        for inp in args.inputs:
            rel_name = os.path.basename(inp)
            out_file = os.path.join(args.out_dir, rel_name)
            transform_file(inp, out_file)
    elif len(args.inputs) == 2 and not os.path.isdir(args.inputs[1]):
        transform_file(args.inputs[0], args.inputs[1])
    else:
        print("Usage: transform_sources.py <input> <output> OR --out-dir <dir> <files...>")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
layout_audit.py - which structs change layout in the 64-bit build?

The 32-bit PC builds (and the ROM data the port converts in place) rely on the
N64's 4-byte pointers. A 64-bit build widens every pointer field, which
silently moves later fields and grows the struct. This script asks clang for
the real record layouts of the same sources under both targets and reports
every record whose size or field offsets differ:

  i386-unknown-linux-gnu  - the proven 32-bit PC layout (reference)
  arm64-apple-macos       - the GE_64BIT build

Usage (from the repository root, after configuring build_mac):
  python3 scripts/port/layout_audit.py [build_dir] [--json out.json] [--all]

It reads build_dir/compile_commands.json if present, otherwise asks ninja for
the compile database. --all also lists records that only grew at the end
(fields at the same offsets, larger sizeof); by default those are listed too,
but separately from records whose interior offsets moved.

The report is the work list for the struct rework (docs/port/macos-arm64.md):
a record that is mapped over ROM data, embedded in such a record, or sized by a
literal must keep its 32-bit layout (pointer fields become PTR32); any other
record may grow.
"""
import collections
import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DROP_FLAGS = {'-c', '-MD', '-w', '-g'}
DROP_WITH_ARG = {'-o', '-MF', '-MT', '-arch'}

QUIET = ['-fsyntax-only', '-Wno-everything', '-Wno-int-conversion',
         '-Wno-incompatible-function-pointer-types', '-Wno-incompatible-pointer-types',
         '-ferror-limit=0', '-Xclang', '-fdump-record-layouts-complete']

LAYOUT_HDR = '*** Dumping AST Record Layout'
FIELD_RE = re.compile(r'^\s*(\d+)(?::(\d+)-(\d+))? \|( +)(.*)$')
SIZE_RE = re.compile(r'\[sizeof=(\d+),.*?align=(\d+)')


def load_compdb(build_dir):
    path = os.path.join(build_dir, 'compile_commands.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    out = subprocess.run(['ninja', '-C', build_dir, '-t', 'compdb'],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def base_args(cmd):
    args = shlex.split(cmd)
    out = []
    skip = False
    for a in args[1:]:
        if skip:
            skip = False
            continue
        if a in DROP_WITH_ARG:
            skip = True
            continue
        if a in DROP_FLAGS:
            continue
        out.append(a)
    return out


def variant(args, target):
    """args without the source-selecting bits, retargeted"""
    a = [x for x in args]
    if target == 'i386':
        a = [x for x in a if x not in ('-DGE_64BIT=1',) and not x.startswith('-isystem')]
        a = [x for x in a if x not in ('/opt/homebrew/include', '/opt/homebrew/include/SDL2')]
        return ['clang', '--target=i386-unknown-linux-gnu', '-msse2', '-mfpmath=sse'] + a
    return ['clang', '-arch', 'arm64'] + a


def parse_layouts(text):
    """{record name: (size, align, [(offset, bitinfo, type+name), ...])} - top-level fields only"""
    recs = {}
    for block in text.split(LAYOUT_HDR)[1:]:
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        m = FIELD_RE.match(lines[0])
        if not m:
            continue
        name = m.group(5).strip()
        top_indent = None
        fields = []
        size = align = None
        for l in lines[1:]:
            sm = SIZE_RE.search(l)
            if sm and l.strip().startswith('|'):
                size, align = int(sm.group(1)), int(sm.group(2))
                break
            fm = FIELD_RE.match(l)
            if not fm:
                continue
            indent = len(fm.group(4))
            if top_indent is None:
                top_indent = indent
            if indent != top_indent:
                continue
            bits = fm.group(2) and '%s-%s' % (fm.group(2), fm.group(3)) or ''
            fields.append((int(fm.group(1)), bits, fm.group(5).strip()))
        if size is not None:
            recs[name] = (size, align, fields)
    return recs


def dump(entry, target):
    args = variant(base_args(entry['command']), target)
    src = entry['file']
    args = [a for a in args if a != src] + QUIET + [src]
    r = subprocess.run(args, cwd=entry['directory'], capture_output=True, text=True)
    return parse_layouts(r.stdout)


def short(name):
    # anonymous records carry their absolute path; keep it repo-relative
    return name.replace(ROOT + '/', '')


def main():
    argv = sys.argv[1:]
    build_dir = os.path.join(ROOT, 'build_mac')
    json_out = None
    if argv and not argv[0].startswith('--'):
        build_dir = argv.pop(0)
    if '--json' in argv:
        json_out = argv[argv.index('--json') + 1]
    db = [e for e in load_compdb(build_dir) if e['file'].endswith('.c')]

    layouts = {'i386': {}, 'arm64': {}}
    users = collections.defaultdict(set)

    def work(e):
        return e['file'], dump(e, 'i386'), dump(e, 'arm64')

    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        for fn, l32, l64 in ex.map(work, db):
            for k, v in l32.items():
                layouts['i386'].setdefault(k, v)
                users[k].add(os.path.relpath(fn, ROOT))
            for k, v in l64.items():
                layouts['arm64'].setdefault(k, v)

    moved, grown = [], []
    for name, (s32, a32, f32) in sorted(layouts['i386'].items()):
        if name not in layouts['arm64']:
            continue
        if '/Applications/' in name or '/usr/' in name or 'homebrew' in name:
            continue
        s64, a64, f64 = layouts['arm64'][name]
        offs32 = [(o, b) for o, b, _ in f32]
        offs64 = [(o, b) for o, b, _ in f64]
        if offs32 != offs64:
            diffs = []
            for (o1, b1, d1), (o2, b2, d2) in zip(f32, f64):
                if (o1, b1) != (o2, b2):
                    diffs.append((o1, o2, d1))
            ptrs = [d for _, _, d in f64 if '*' in d]
            moved.append((short(name), s32, s64, diffs, ptrs, len(users[name])))
        elif s32 != s64:
            ptrs = [d for _, _, d in f64 if '*' in d]
            grown.append((short(name), s32, s64, ptrs, len(users[name])))

    print('records laid out: %d' % len(layouts['i386']))
    print('\n== interior offsets move in the 64-bit build: %d ==' % len(moved))
    for name, s32, s64, diffs, ptrs, n in sorted(moved, key=lambda r: -r[5]):
        print('%s  sizeof 0x%x -> 0x%x  (%d TUs)' % (name, s32, s64, n))
        o1, o2, d = diffs[0]
        print('    first moved field: %s  0x%x -> 0x%x' % (d, o1, o2))
        if ptrs:
            print('    pointer fields: %s' % '; '.join(ptrs[:6]) + (' ...' if len(ptrs) > 6 else ''))
    print('\n== only sizeof grows (tail pointer / alignment): %d ==' % len(grown))
    for name, s32, s64, ptrs, n in sorted(grown, key=lambda r: -r[4]):
        print('%s  sizeof 0x%x -> 0x%x  (%d TUs)  %s' % (name, s32, s64, n, '; '.join(ptrs[:3])))

    if json_out:
        with open(json_out, 'w') as f:
            json.dump({'moved': moved, 'grown': grown}, f, indent=1)


if __name__ == '__main__':
    main()

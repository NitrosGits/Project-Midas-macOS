#!/usr/bin/env python3
"""
ptr32_check.py - the checks to run after converting a struct family to PTR32
(docs/port/macos-arm64.md). The GE_64BIT build alone misses three things:

  aliases   Build every TU with -Wincompatible-pointer-types and list the
            warnings that name a PTR32 union. A local `T **p = rec->arr` alias
            compiles silently (incompatible pointer types are not an error in
            GE_64BIT builds) and then reads 8-byte entries.
  variants  Syntax-check every game source as VERSION_JP, VERSION_EU and DEBUG,
            and list PTR32 misuse in branches the US build never compiles.
  i386      Syntax-check every game source as i386-linux (the proven 32-bit
            layout) with GCC-like leniency. With --orig DIR (an unpacked copy of
            the tree from before the change), also compile both at -O2 and
            compare the object code, so the 32-bit builds provably stay the same.
            No i386 libc is needed; scripts/port/i386inc has minimal stubs.
  views     Every incompatible pointer conversion between two different struct
            types, as one struct used as a view of another (a ModelNode read
            through a struct with native pointers, an embedded record smaller
            than what is written through it). The pairs whose GE_64BIT layouts
            disagree on where the native pointers are, within their common
            prefix, are listed with their sites, as are T * / T ** mixups.
            VIEWS_REVIEWED holds the pairs checked and found harmless.

Usage (from the repository root):
  python3 scripts/port/ptr32_check.py build_mac aliases|variants|i386|views [--orig DIR]

Make the --orig copy before the change, for example:
  mkdir /tmp/orig && git ls-files -z src include port assets/embedded | \\
      tar -c --null -T - | tar -x -C /tmp/orig
(or a tar of the working tree when the port work is uncommitted).
"""
import argparse
import concurrent.futures
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUBS = os.path.join(ROOT, 'scripts', 'port', 'i386inc')
OBJDUMP = shutil.which('llvm-objdump') or '/opt/homebrew/opt/llvm/bin/llvm-objdump'

PTR32_RE = re.compile(r"union \(unnamed|unnamed union|t_\[0\]|PTR32|ModelRwWord|ModelNodeWord|"
                      r"member reference (base )?type '?union|requires expression of scalar type|"
                      r"invalid operands to binary expression \('union")
VARIANTS = {
    'JP': ['-UVERSION_US', '-ULANG_US', '-DVERSION_JP', '-DLANG_JP'],
    'EU': ['-UVERSION_US', '-ULANG_US', '-UREFRESH_NTSC', '-DVERSION_EU', '-DLANG_EU', '-DREFRESH_PAL'],
    'DEBUG': ['-DDEBUG'],
}
LENIENT = ['-Wno-everything', '-Wno-int-conversion', '-Wno-incompatible-pointer-types',
           '-Wno-incompatible-function-pointer-types', '-Wno-implicit-function-declaration',
           '-Wno-implicit-int', '-Wno-return-type', '-Wno-int-to-pointer-cast', '-ferror-limit=0']


def compdb(build_dir):
    out = subprocess.run(['ninja', '-C', build_dir, '-t', 'compdb'],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def strip(cmd, drop_werror=True):
    """build args without output/dependency flags; returns (args, source)"""
    out, skip = [], False
    for a in shlex.split(cmd):
        if skip:
            skip = False
            continue
        if a in ('-o', '-MF', '-MT'):
            skip = True
            continue
        if a in ('-c', '-MD') or (drop_werror and a.startswith('-Werror')):
            continue
        out.append(a)
    return out[:-1], out[-1]


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def check_aliases(db, jobs):
    def one(e):
        a, src = strip(e['command'])
        r = run(a + ['-fsyntax-only', '-Wincompatible-pointer-types', '-Wno-error', src], e['directory'])
        lines = r.stderr.splitlines()
        return ['%s\n      %s' % (l.replace(ROOT + '/', ''), lines[i + 1].strip() if i + 1 < len(lines) else '')
                for i, l in enumerate(lines)
                if 'warning:' in l and ('union (unnamed' in l or 'unnamed union' in l or 'Word' in l)]
    found = set()
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for r in ex.map(one, [e for e in db if e['file'].endswith(('.c', '.cpp'))]):
            found.update(r)
    for l in sorted(found):
        print(l)
    print('%d PTR32 pointer-type conversion(s)' % len(found))
    return not found


def check_variants(db, jobs):
    def one(job):
        e, v = job
        a, src = strip(e['command'])
        r = run(a + ['-fsyntax-only'] + VARIANTS[v] + [src], e['directory'])
        return v, [l.split(' error: ')[0] for l in r.stderr.splitlines() if ' error: ' in l and PTR32_RE.search(l)]
    files = [e for e in db if e['file'].startswith(ROOT + '/src/') and e['file'].endswith('.c')]
    res = {}
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for v, locs in ex.map(one, [(e, v) for e in files for v in VARIANTS]):
            for loc in locs:
                res.setdefault(loc, set()).add(v)
    for loc in sorted(res):
        print(','.join(sorted(res[loc])), loc.replace(ROOT + '/', ''))
    print('%d unconverted site(s) in other variants' % len(res))
    return not res


# struct pairs reviewed and harmless: the pointer only passes through, or the
# layouts agree where they are actually used (docs/port/macos-arm64.md)
VIEWS_REVIEWED = {
    ('struct ALSoundState_s *', 'struct sfxRecord *'): 'the sfxRecord is the handle slot (state, a native pointer, at 0)',
    ('struct ItemModelFileRecord *', 'struct ModelFileHeader *'): 'prop_header is always cast back to ModelFileHeader *',
    ('struct ChrRecord *', 'struct PropRecord *'): 'g_LastOnScreenProp list arithmetic; pointers pass through',
    ('struct Model *', 'struct Model **'): 'struct player embeds a Model at &player->model (real Model on GE_64BIT)',
    ('struct ALSoundState_s *', 'struct ALSoundState_s **'): 'sound handle slots (snd.c sndSlotGet/Set)',
    ('struct ALSoundState **', 'struct ALSoundState_s *'): 'sound handle slots (snd.c sndSlotGet/Set)',
    ('struct PropRecord *', 'struct PropRecord **'): 'g_LastOnScreenProp points into g_OnScreenPropList',
    ('struct damage_display_parent[23]', 'struct damage_display_val *'): 'pointer-free records',
    ('struct StanPrefixRecord *', 'struct StanPrefixRecord **'): 'stanLoadFile: the PC branch stores into stan_prefix directly',
}
TYPE_RE = re.compile(r"'([^']*)'(?: \(aka '([^']*)'\))?")
LAYOUT_HEAD = re.compile(r'^\s*0 \| (struct|union) (\S+)')
LAYOUT_FIELD = re.compile(r'^\s*(\d+)(?::\d+-\d+)? \|   (\S.*)$')  # depth-1 fields only
LAYOUT_SIZE = re.compile(r'\[sizeof=(\d+)')


def check_views(db, jobs):
    def one(e):
        a, src = strip(e['command'])
        r = run(a + ['-fsyntax-only', '-Wincompatible-pointer-types', '-Wno-error',
                     '-Xclang', '-fdump-record-layouts-complete', src], e['directory'])
        pairs = []
        lines = r.stderr.splitlines()
        for i, l in enumerate(lines):
            if 'warning: incompatible pointer types' not in l:
                continue
            tys = [re.sub(r'\b(const|volatile)\b\s*', '', b or a).strip()
                   for a, b in TYPE_RE.findall(l.split('warning:', 1)[1])][:2]
            if len(tys) == 2 and tys[0] != tys[1] and all(re.search(r'\b(struct|union)\b', t) and '(*)' not in t for t in tys):
                site = '%s\n      %s' % (l.split(' warning:')[0].replace(ROOT + '/', ''), lines[i + 1].strip() if i + 1 < len(lines) else '')
                pairs.append((tuple(sorted(tys)), site))
        recs, cur = {}, None
        for l in r.stdout.splitlines():
            m = LAYOUT_HEAD.match(l)
            if m:
                cur = (m.group(1) + ' ' + m.group(2), [])
                continue
            if cur is None:
                continue
            m = LAYOUT_SIZE.search(l)
            if m:
                recs.setdefault(cur[0], (cur[1], int(m.group(1))))
                cur = None
                continue
            m = LAYOUT_FIELD.match(l)
            if m:
                decl = m.group(2)
                if '(*' in decl or (re.search(r'\*\s*\w+$', decl) and '[' not in decl.split()[-1]):
                    cur[1].append(int(m.group(1)))  # a native (or function) pointer
        return pairs, recs
    sites, recs = {}, {}
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for pairs, r in ex.map(one, [e for e in db if e['file'].endswith('.c')]):
            for k, s in pairs:
                sites.setdefault(k, set()).add(s)
            for k, v in r.items():
                recs.setdefault(k, v)
    found = 0
    for (ta, tb), s in sorted(sites.items()):
        if (ta, tb) in VIEWS_REVIEWED:
            continue
        if ta.count('*') != tb.count('*'):
            why = 'pointer level differs'
        else:
            A, B = recs.get(ta.rstrip('* ')), recs.get(tb.rstrip('* '))
            if A is None or B is None:
                continue  # an incomplete (opaque) type: nothing is read through it
            lim = min(A[1], B[1])
            pa = sorted(o for o in A[0] if o < lim)
            pb = sorted(o for o in B[0] if o < lim)
            if pa == pb:
                continue
            why = 'native pointers at %s vs %s' % (pa, pb)
        found += 1
        print('%s <-> %s: %s' % (ta, tb, why))
        for x in sorted(s)[:6]:
            print('    ' + x)
    print('%d struct view(s) to review' % found)
    return found == 0


def i386_args(e, root, src_root):
    a, src = strip(e['command'])
    a = [x for x in a[1:] if x not in ('-g', '-DGE_64BIT=1')]
    out, skip = [], False
    for x in a:  # drop -arch arm64 and the Homebrew -isystem pairs
        if skip:
            skip = False
            continue
        if x in ('-arch', '-isystem'):
            skip = True
            continue
        out.append(x)
    if root != src_root:
        build = os.path.join(src_root, 'build')
        out = [x if x.startswith('-I' + build) else x.replace(src_root, root) for x in out]
        src = src.replace(src_root, root)
    base = ['clang', '--target=i386-unknown-linux-gnu', '-msse2', '-mfpmath=sse'] + out + LENIENT
    base += ['-idirafter', STUBS]
    # unrelated to layout: image.c declares its own fwrite/fclose, and lv.c /
    # spectrum.c use #pragma weak aliases clang rejects (GCC accepts) - the
    # object compare takes their Apple branch instead
    if src.endswith('image.c'):
        base.append('-DSTUB_NO_FILEIO')
    return base, src


def objsig(path):
    r = subprocess.run([OBJDUMP, '-dr', '-s', '--no-show-raw-insn', path], capture_output=True, text=True)
    return r.stdout.splitlines()[2:]


def check_i386(db, jobs, orig, tmp):
    def one(e):
        rel = os.path.relpath(e['file'], ROOT)
        base, src = i386_args(e, ROOT, ROOT)
        r = run(base + ['-fsyntax-only', src], e['directory'])
        out = {'errors': [l.replace(ROOT + '/', '') for l in r.stderr.splitlines() if ' error: ' in l]}
        if orig:
            objs = []
            for tree, tag in ((ROOT, 'new'), (orig, 'old')):
                b, s = i386_args(e, tree, ROOT)
                if rel.endswith(('lv.c', 'spectrum.c')):
                    b.append('-D__APPLE__')
                o = os.path.join(tmp, tag, rel + '.o')
                os.makedirs(os.path.dirname(o), exist_ok=True)
                pm = ['-ffile-prefix-map=%s=%s' % (tree, ROOT)] if tree != ROOT else []
                rr = run(b + pm + ['-O2', '-ffunction-sections', '-fdata-sections', '-c', s, '-o', o], e['directory'])
                objs.append(o if rr.returncode == 0 else None)
            if None in objs:
                out['cmp'] = 'compile failed'
            else:
                a, b2 = objsig(objs[0]), objsig(objs[1])
                out['cmp'] = 'same' if a == b2 else 'DIFF'
                if a != b2:
                    out['diff'] = list(difflib.unified_diff(b2, a, lineterm='', n=1))[:30]
        return rel, out
    files = [e for e in db if e['file'].startswith(ROOT + '/src/') and e['file'].endswith('.c')]
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        res = dict(ex.map(one, files))
    nerr = 0
    for rel in sorted(res):
        for l in res[rel]['errors']:
            print('ERR', l)
            nerr += 1
    if orig:
        groups = {}
        for rel in sorted(res):
            groups.setdefault(res[rel]['cmp'], []).append(rel)
        for k, v in sorted(groups.items()):
            print('%-16s %d' % (k, len(v)))
            if k != 'same':
                for rel in v:
                    print('   ', rel)
                    for l in res[rel].get('diff', [])[:12]:
                        print('       ', l)
        print('(compiletime.c always differs: __DATE__/__TIME__)')
    print('i386 syntax errors: %d in %d game sources' % (nerr, len(files)))
    return nerr == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('build_dir')
    ap.add_argument('check', choices=('aliases', 'variants', 'i386', 'views'))
    ap.add_argument('--orig', help='pre-change tree for the i386 object compare')
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()
    db = compdb(args.build_dir)
    if args.check == 'aliases':
        ok = check_aliases(db, args.j)
    elif args.check == 'variants':
        ok = check_variants(db, args.j)
    elif args.check == 'views':
        ok = check_views(db, args.j)
    else:
        tmp = os.path.abspath(os.path.join(args.build_dir, 'ptr32_check_obj'))
        shutil.rmtree(tmp, ignore_errors=True)
        ok = check_i386(db, args.j, os.path.realpath(args.orig) if args.orig else None, tmp)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
retype_locals.py - give s32/u32 variables that hold pointers their pointer type.

The decompiler often typed a pointer-holding variable as s32. GE_64BIT builds
reject every assignment, argument and return that moves a pointer through such
a variable, so the build log is a list of evidence: each diagnostic names the
variable's position and the pointer type the code wants there. This script
reads a build log, collects that evidence per variable, and rewrites the
variable's declaration (in the enclosing function, or at file scope with its
extern declarations) to the pointer type.

Evidence, strongest first:
  x = <T value>            assigning to 's32' from 'T'
  s32 x = <T value>        initializing 's32' with an expression of type 'T'
  f(.., x, ..)             passing 's32' to parameter of type 'T'
  p = x;  return x;        assigning to 'T' from 's32' / returning 's32' ... 'T'

A variable with conflicting strongest evidence is reported, not touched.
Rebuild and rerun until nothing changes. Retyping can change what integer
arithmetic on the variable means (pointer arithmetic scales), so check the
result with `ptr32_check.py build_mac i386 --orig ...`: any function whose
32-bit code changed needs a look.

Usage (from the repository root):
  ninja -C build_mac -k 0 > /tmp/build.txt 2>&1
  python3 scripts/port/retype_locals.py /tmp/build.txt [--only file.c] [--dry-run]
"""
import argparse
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INT = r"(?:s32|u32|int|unsigned int)"
Q = r"'([^']*)'(?: \(aka '[^']*'\))?"
ERR_RE = re.compile(r"^(?P<file>/[^:\n]+):(?P<line>\d+):(?P<col>\d+): error: (?P<msg>.*)$")
FORMS = [
    # (regex, rank, side): side tells where the variable is relative to the caret
    (re.compile(r"incompatible pointer to integer conversion assigning to '%s'.* from %s" % (INT, Q)), 0, 'lhs'),
    (re.compile(r"incompatible pointer to integer conversion initializing '%s'.* with an expression of type %s" % (INT, Q)), 0, 'init'),
    (re.compile(r"incompatible integer to pointer conversion passing '%s'.* to parameter of type %s" % (INT, Q)), 1, 'arg'),
    (re.compile(r"incompatible integer to pointer conversion assigning to %s from '%s'" % (Q, INT)), 2, 'rhs'),
    (re.compile(r"incompatible integer to pointer conversion returning '%s'.* from a function with result type %s" % (INT, Q)), 2, 'ret'),
]
IDENT = re.compile(r"[A-Za-z_]\w*")


def ptr_type(t):
    t = t.strip()
    m = re.fullmatch(r"(.*?)\s*\[\d*\]", t)
    if m:  # an array decays to a pointer
        t = m.group(1) + ' *'
    if '*' not in t or '(' in t:
        return None  # not an object pointer (function pointers stay manual)
    t = re.sub(r'\s*\*', ' *', t)
    return t


def var_at(text, col, side):
    """the plain identifier the diagnostic is about, or None"""
    i = col - 1
    if side in ('lhs', 'init'):
        # caret at '=' (lhs) or at the initializer (init): back up to '=' then the name
        j = i
        if side == 'init':
            j = text.rfind('=', 0, i + 1)
            if j < 0:
                return None
        k = j - 1
        while k >= 0 and text[k] == ' ':
            k -= 1
        m = re.search(r"[A-Za-z_]\w*$", text[:k + 1])
        if not m:
            return None
        if m.start() > 0 and text[m.start() - 1] in '.>]':
            return None  # a member or element, not a variable
        return m.group(0)
    if side == 'arg':
        m = IDENT.match(text, i)
        if not m:
            return None
        rest = text[m.end():].lstrip()
        if not rest or rest[0] not in ',)':
            return None
        if i > 0 and text[i - 1] in '.>':
            return None
        return m.group(0)
    if side in ('rhs', 'ret'):
        if side == 'ret':
            m = re.search(r"\breturn\s+([A-Za-z_]\w*)\s*;", text)
            return m.group(1) if m else None
        j = text.find('=', i)
        if j < 0:
            return None
        m = re.match(r"\s*([A-Za-z_]\w*)\s*;", text[j + 1:])
        return m.group(1) if m else None
    return None


FUNC_START = re.compile(r"^[A-Za-z_][\w\s\*]*\b[A-Za-z_]\w*\s*\([^;]*$")


def func_bounds(lines, n):
    """(first body line, line of the error) of the function holding line n (0-based)"""
    for k in range(n, -1, -1):
        if lines[k].startswith('{') or (FUNC_START.match(lines[k]) and lines[k].rstrip().endswith('{')):
            return k
        if lines[k].startswith('}') and k != n:
            return None
    return None


def decl_regex(var):
    return re.compile(r"^(?P<ind>\s*)(?P<q>(?:static\s+|volatile\s+)*)(?P<t>%s)\s+(?P<list>[^;()]*\b%s\b[^;()]*);(?P<tail>.*)$" % (INT, re.escape(var)))


def split_decl(m, var, newtype):
    """rewrite one declaration line so var gets newtype; None if too odd"""
    items = [x.strip() for x in m.group('list').split(',')]
    mine = [x for x in items if re.match(r"%s\b" % re.escape(var), x)]
    if len(mine) != 1:
        return None
    item = mine[0]
    if re.match(r"%s\s*\[" % re.escape(var), item):
        return None  # an array of ints: not a pointer variable
    others = [x for x in items if x is not item]
    ind, q = m.group('ind'), m.group('q')
    star = newtype.rstrip().endswith('*')
    new = '%s%s%s%s%s;' % (ind, q, newtype.rstrip('*').rstrip() + ' ' if star else newtype + ' ',
                           '*' if star else '', item)
    new = re.sub(r'\*\*', '**', new)
    out = []
    if others:
        out.append('%s%s%s %s;' % (ind, q, m.group('t'), ', '.join(others)))
    out.append(new + m.group('tail'))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--only', action='append', default=[])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--globals', action='store_true', help='also retype file-scope variables (check their externs)')
    args = ap.parse_args()

    log = open(args.log, errors='replace').read().splitlines()
    evidence = collections.defaultdict(lambda: collections.defaultdict(set))  # (file, funcline, var) -> rank -> types
    seen = set()
    for idx, l in enumerate(log):
        m = ERR_RE.match(l)
        if not m:
            continue
        f, ln, col, msg = m.group('file'), int(m.group('line')), int(m.group('col')), m.group('msg')
        if args.only and not any(f.endswith(o) for o in args.only):
            continue
        if not f.startswith(ROOT + '/') or (f, ln, col, msg) in seen:
            continue
        seen.add((f, ln, col, msg))
        for rx, rank, side in FORMS:
            fm = rx.search(msg)
            if not fm:
                continue
            t = ptr_type(fm.group(1))
            if t is None:
                break
            try:
                lines = open(f).read().split('\n')
            except OSError:
                break
            # the snippet clang prints under the error is the real source line;
            # after #line the reported number may not be (gun.c, gunfire.c, lv.c)
            sm = re.match(r"^\s*\d+ \| (.*)$", log[idx + 1]) if idx + 1 < len(log) else None
            snippet = sm.group(1) if sm else None
            if ln - 1 >= len(lines) or (snippet is not None and lines[ln - 1] != snippet):
                hits = [k for k, x in enumerate(lines) if x == snippet] if snippet else []
                if len(hits) != 1:
                    break
                ln = hits[0] + 1
            if col - 1 >= len(lines[ln - 1]):
                break
            var = var_at(lines[ln - 1], col, side)
            if var is None:
                break
            fb = func_bounds(lines, ln - 1)
            evidence[(f, fb, var)][rank].add(t)
            break

    changed_files = {}
    report = []
    for (f, fb, var), ranks in sorted(evidence.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2])):
        best = ranks[min(ranks)]
        if len(best) != 1:
            report.append('%s: %s: conflicting types %s' % (os.path.relpath(f, ROOT), var, sorted(best)))
            continue
        t = next(iter(best))
        lines = changed_files.get(f) or open(f).read().split('\n')
        rx = decl_regex(var)
        hit = None
        if fb is not None:
            end = len(lines)
            for k in range(fb, len(lines)):
                if lines[k].startswith('}'):
                    end = k
                    break
            for k in range(fb, end):
                dm = rx.match(lines[k])
                if dm:
                    hit = k
                    break
        where = 'local'
        if hit is None and args.globals:
            for k, line in enumerate(lines):
                dm = rx.match(line)
                if dm and not line[:1].isspace():
                    hit = k
                    where = 'global'
                    break
        if hit is None:
            report.append('%s: %s -> %s: declaration not found' % (os.path.relpath(f, ROOT), var, t))
            continue
        new = split_decl(rx.match(lines[hit]), var, t)
        if new is None:
            report.append('%s:%d: %s -> %s: declaration too odd to rewrite' % (os.path.relpath(f, ROOT), hit + 1, var, t))
            continue
        lines[hit:hit + 1] = new
        changed_files[f] = lines
        print('%s:%d: %s %s -> %s' % (os.path.relpath(f, ROOT), hit + 1, where, var, t))
        if where == 'global':
            report.append('%s: global %s retyped; check its extern declarations' % (os.path.relpath(f, ROOT), var))
    if not args.dry_run:
        for f, lines in changed_files.items():
            with open(f, 'w') as fp:
                fp.write('\n'.join(lines))
    for r in report:
        print('manual:', r)


if __name__ == '__main__':
    main()

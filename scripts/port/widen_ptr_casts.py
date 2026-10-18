#!/usr/bin/env python3
"""
widen_ptr_casts.py - rewrite flagged pointer->int casts to pointer-width types.

Reads a GE_64BIT build log and, for every
    file:line:col: error: cast to smaller integer type 's32'/'u32'/... from <ptr>
rewrites the cast at exactly that position: (s32)/(int) -> (intptr_t),
(u32)/(unsigned int) -> (uintptr_t). In the 32-bit builds those are the same
types, so nothing changes there; in GE_64BIT the value keeps its full width.

A widened value that is later narrowed back to 32 bits is still caught: the
GE_64BIT build has -Werror=shorten-64-to-32. Fix those by hand (a type change,
or portPtr32 for a real 32-bit word; docs/port/macos-arm64.md).

Usage: python3 scripts/port/widen_ptr_casts.py build.log [--dry-run]
"""
import collections
import re
import sys

ERR = re.compile(r"^(/[^:]+):(\d+):(\d+): error: cast to smaller integer type '([^']+)'"
                 r"(?: \(aka '[^']+'\))? from .*\[-Werror,-W(?:void-)?pointer-to-int-cast\]")
CASTS = [
    (re.compile(r'\(\s*s32\s*\)'), '(intptr_t)'),
    (re.compile(r'\(\s*int\s*\)'), '(intptr_t)'),
    (re.compile(r'\(\s*u32\s*\)'), '(uintptr_t)'),
    (re.compile(r'\(\s*unsigned int\s*\)'), '(uintptr_t)'),
    (re.compile(r'\(\s*unsigned\s*\)'), '(uintptr_t)'),
]


def find_line(lines, reported, snippet):
    """physical index of the flagged line: #line directives make clang's line
    numbers differ from the file's, so match the snippet clang printed"""
    if 0 < reported <= len(lines) and lines[reported - 1].rstrip() == snippet:
        return reported - 1
    hits = [i for i, l in enumerate(lines) if l.rstrip() == snippet]
    if not hits:
        return None
    return min(hits, key=lambda i: abs(i + 1 - reported))


def main():
    log, dry = sys.argv[1], '--dry-run' in sys.argv
    sites = collections.defaultdict(set)
    loglines = open(log, errors='replace').read().split('\n')
    for n, line in enumerate(loglines):
        m = ERR.match(line)
        if m and n + 1 < len(loglines):
            sm = re.match(r'^ *\d+ \| (.*)$', loglines[n + 1])
            if sm:
                sites[m.group(1)].add((int(m.group(2)), int(m.group(3)), sm.group(1).rstrip()))
    total = skipped = 0
    for path, locs in sorted(sites.items()):
        lines = open(path).read().split('\n')
        resolved = set()
        for ln, col, snippet in locs:
            i = find_line(lines, ln, snippet)
            if i is None:
                skipped += 1
                print('skip %s:%d (line not found)' % (path, ln))
                continue
            resolved.add((i, col))
        # rightmost first so earlier columns on the same line stay valid
        for i, col in sorted(resolved, key=lambda t: (t[0], -t[1])):
            text = lines[i]
            c = col - 1
            for rx, repl in CASTS:
                m = rx.match(text, c)
                if m:
                    lines[i] = text[:c] + repl + text[m.end():]
                    total += 1
                    break
            else:
                skipped += 1
                print('skip %s:%d:%d  %s' % (path, i + 1, col, text.strip()[:100]))
        if not dry:
            open(path, 'w').write('\n'.join(lines))
    print('rewrote %d casts in %d files, %d left for hand fixes' % (total, len(sites), skipped))


if __name__ == '__main__':
    main()

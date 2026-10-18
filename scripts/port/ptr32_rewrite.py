#!/usr/bin/env python3
"""
ptr32_rewrite.py - wrap every use of a soon-to-be PTR32 field in PGET/PSET.

Converting a layout-bound pointer field to PTR32(T) (docs/port/macos-arm64.md)
breaks every access to it. This script finds those accesses in the CURRENT
source, while the fields are still plain pointers, by asking clang-query for
the AST of every translation unit in the compile database, and rewrites them:

  x->f            ->  PGET(x->f)          (any read)
  x->f = v        ->  PSET(x->f, v)       (plain assignment)
  x->arr[i]       ->  PGET(x->arr[i])     (--array: a pointer to PTR32 words)
  x->arr[i] = v   ->  PSET(x->arr[i], v)

Run it BEFORE changing the field types, then change them and build. Sites it
can't rewrite safely are listed for manual work and left alone: compound
assignments, ++/--, &x->f, and anything assigned inside a macro body. Code
the compile database never sees (#ifdef TARGET_N64, other VERSION_*) is
untouched; in the 32-bit builds PGET/PSET are identity, so it needs nothing.

Usage (from the repository root):
  python3 scripts/port/ptr32_rewrite.py build_mac \\
      ModelNode.Data,Parent,Next,Prev,Child --array ModelFileHeader.Switches \\
      [--skip src/game/model.c:5955-6215] [--dry-run]

Locations come from clang's dump, which honours #line (gun.c, gunfire.c,
lv.c, env.c); they are mapped back to real lines and every site is checked
against the source text before it is touched.

Not covered, so check by hand after the build is clean: a local alias of a
converted array (`ModelNode **sw = hdr->Switches;`) compiles, because
incompatible pointer types aren't an error in GE_64BIT builds, and then reads
8-byte entries. Build once with -Wincompatible-pointer-types and look for
warnings naming the PTR32 union types, and grep for `(T **)` casts.
"""
import argparse
import collections
import concurrent.futures
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLANG_QUERY_CANDIDATES = ['/opt/homebrew/opt/llvm/bin/clang-query', 'clang-query']


def find_clang_query():
    for c in CLANG_QUERY_CANDIDATES:
        p = shutil.which(c) if not os.path.isabs(c) else (c if os.path.exists(c) else None)
        if p:
            return p
    sys.exit('clang-query not found (brew install llvm)')


def load_compdb(build_dir):
    path = os.path.join(build_dir, 'compile_commands.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    out = subprocess.run(['ninja', '-C', build_dir, '-t', 'compdb'],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def lenient(cmd):
    """the build command without the pointer-width -Werror flags: the AST must
    come from the code as it is, errors and all"""
    args = shlex.split(cmd)
    out = [a for a in args if not a.startswith('-Werror')]
    return shlex.join(out)


# ---------------------------------------------------------------- matchers

def field_matcher(specs):
    alts = []
    for rec, fields in specs:
        names = ', '.join('"%s"' % f for f in fields)
        alts.append('allOf(hasAnyName(%s), hasAncestor(recordDecl(hasName("%s"))))' % (names, rec))
    return 'fieldDecl(anyOf(%s))' % ', '.join(alts) if len(alts) > 1 else 'fieldDecl(%s)' % alts[0]


def build_query(fields, arrays):
    lines = ['set output dump', 'set bind-root false']
    if fields:
        fm = field_matcher(fields)
        mem = 'memberExpr(member(%s))' % fm
        lines.append('match %s.bind("read")' % mem)
        lines.append('match binaryOperator(isAssignmentOperator(), hasLHS(ignoringParens(%s))).bind("assign")' % mem)
        lines.append('match unaryOperator(hasOperatorName("&"), hasUnaryOperand(ignoringParens(%s))).bind("addr")' % mem)
        lines.append('match unaryOperator(anyOf(hasOperatorName("++"), hasOperatorName("--")), hasUnaryOperand(ignoringParens(%s))).bind("incdec")' % mem)
    if arrays:
        am = 'memberExpr(member(%s))' % field_matcher(arrays)
        sub = 'arraySubscriptExpr(hasBase(ignoringParenImpCasts(%s)))' % am
        lines.append('match %s.bind("aread")' % sub)
        lines.append('match binaryOperator(isAssignmentOperator(), hasLHS(ignoringParens(%s))).bind("aassign")' % sub)
        lines.append('match unaryOperator(hasOperatorName("&"), hasUnaryOperand(ignoringParens(%s))).bind("aaddr")' % sub)
        lines.append('match unaryOperator(anyOf(hasOperatorName("++"), hasOperatorName("--")), hasUnaryOperand(ignoringParens(%s))).bind("aincdec")' % sub)
        lines.append('match unaryOperator(hasOperatorName("*"), hasUnaryOperand(ignoringParenImpCasts(%s))).bind("aderef")' % am)
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- dump parsing

BIND_RE = re.compile(r'^Binding for "(\w+)":$')
LOC_TOKEN_RE = re.compile(r'(?:(?P<file>[^<>,]+?):(?P<fl>\d+):(?P<fc>\d+)|line:(?P<l>\d+):(?P<lc>\d+)|col:(?P<c>\d+))')


def parse_range(text, last=(None, None)):
    """'<file:1:2, col:5>' -> [(file, line, col), (file, line, col)] or None
    when any part is a macro spelling / scratch / invalid location. The dump
    drops the file and line when they repeat, so a child's range continues
    from `last`, the parent's end location."""
    if '<Spelling=' in text or 'scratch space' in text or 'invalid sloc' in text or 'built-in' in text:
        return None
    inner = text[1:-1]
    parts = [p.strip() for p in inner.split(', ')]
    out = []
    lf, ll = last
    for p in parts:
        m = LOC_TOKEN_RE.fullmatch(p)
        if not m:
            return None
        if m.group('file'):
            lf, ll, c = m.group('file'), int(m.group('fl')), int(m.group('fc'))
        elif m.group('l'):
            ll, c = int(m.group('l')), int(m.group('lc'))
        else:
            c = int(m.group('c'))
        out.append((lf, ll, c))
    if len(out) == 1:
        out.append(out[0])
    return out


NODE_RANGE_RE = re.compile(r'^\w+ 0x[0-9a-f]+ (<.*?>) ')


def run_query(cq, dbdir, qfile, src):
    r = subprocess.run([cq, '-p', dbdir, '-f', qfile, src], capture_output=True, text=True)
    sites = []
    lines = r.stdout.splitlines()
    i = 0
    while i < len(lines):
        m = BIND_RE.match(lines[i])
        if m and i + 1 < len(lines):
            kind = m.group(1)
            head = lines[i + 1]
            nm = NODE_RANGE_RE.match(head)
            rng = parse_range(nm.group(1)) if nm else None
            member = None
            mm = re.search(r'(?:->|\.)(\w+) 0x[0-9a-f]+$', head)
            if mm:
                member = mm.group(1)
            op = None
            om = re.search(r"'([^']*=)'(?: ComputeLHSTy| ComputeResultTy|$)", head)
            if om:
                op = om.group(1)
            # the assignment's LHS range (first child) tells us where the LHS ends
            child = None
            if kind in ('assign', 'aassign') and i + 2 < len(lines):
                cm = re.match(r'^[|`]-\w+ 0x[0-9a-f]+ (<.*?>) ', lines[i + 2])
                if cm and rng is not None:
                    child = parse_range(cm.group(1), rng[1][:2])
            sites.append({'kind': kind, 'range': rng, 'member': member, 'op': op,
                          'lhs': child, 'head': head})
            i += 2
            continue
        i += 1
    return sites, r.stderr


# ---------------------------------------------------------------- source mapping

class Source:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        self.starts = [0]
        for i, b in enumerate(self.data):
            if b == 0x0A:
                self.starts.append(i + 1)
        # #line segments: (first real line, presumed line there, presumed file or None)
        self.segs = [(1, 1, None)]
        for n, s in enumerate(self.starts):
            line = self.data[s:self.data.find(b'\n', s) if self.data.find(b'\n', s) >= 0 else len(self.data)]
            m = re.match(rb'\s*#\s*line\s+(\d+)(?:\s+"([^"]*)")?', line)
            if m:
                self.segs.append((n + 2, int(m.group(1)), m.group(2).decode() if m.group(2) else None))

    def offsets(self, pfile, pline, col):
        """candidate byte offsets for a presumed (file, line, col)"""
        cands = []
        for k, (rstart, pstart, sfile) in enumerate(self.segs):
            if sfile is None:
                if os.path.realpath(pfile) != self.path:
                    continue
            elif os.path.basename(sfile) != os.path.basename(pfile):
                continue
            rend = self.segs[k + 1][0] - 2 if k + 1 < len(self.segs) else len(self.starts)
            real = rstart + (pline - pstart)
            if rstart <= real <= rend and real - 1 < len(self.starts):
                cands.append(self.starts[real - 1] + col - 1)
        return cands

    def text(self, a, b):
        return self.data[a:b].decode('latin-1')


TOKEN_RE = re.compile(rb'[A-Za-z_][A-Za-z0-9_]*|\d[A-Za-z0-9_.]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|.', re.S)


def token_end(src, off):
    m = TOKEN_RE.match(src.data, off)
    return m.end() if m else off + 1


def rhs_end(src, off):
    """end of the expression starting at off: the first ; , ) ] } at depth 0"""
    depth = 0
    last = off
    for m in TOKEN_RE.finditer(src.data, off):
        t = m.group(0)
        if t.isspace():
            continue
        if t in (b'(', b'[', b'{'):
            depth += 1
        elif t in (b')', b']', b'}'):
            if depth == 0:
                return last
            depth -= 1
        elif depth == 0 and t in (b';', b','):
            return last
        elif t.startswith((b'//', b'/*')):
            continue
        last = m.end()
    return last


def matches(src, loc, member):
    """every candidate offset of loc where member is spelled"""
    pfile, pline, col = loc
    return [c for c in src.offsets(pfile, pline, col)
            if src.data[c:c + len(member)] == member.encode() and token_end(src, c) == c + len(member)]


def resolve(src, loc, member=None, before=None, after=None):
    """byte offset of loc; with member, the one where that name is spelled.
    A presumed line can map to two real lines (#line), so `before`/`after`
    pick the candidate closest to an already-resolved end of the same node."""
    pfile, pline, col = loc
    cands = src.offsets(pfile, pline, col)
    if member is not None:
        cands = [c for c in cands if src.data[c:c + len(member)] == member.encode()
                 and token_end(src, c) == c + len(member)]
    if before is not None and len(cands) > 1:
        cands = [max([c for c in cands if c <= before], default=None)]
    if after is not None and len(cands) > 1:
        cands = [min([c for c in cands if c >= after], default=None)]
    return cands[0] if len(cands) == 1 else None


# ---------------------------------------------------------------- main

def parse_spec(s):
    rec, _, fields = s.partition('.')
    return rec, [f for f in fields.split(',') if f]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('build_dir')
    ap.add_argument('fields', nargs='*', help='Record.field1,field2 (PTR32 fields)')
    ap.add_argument('--array', action='append', default=[], help='Record.field: pointer to PTR32 words')
    ap.add_argument('--skip', action='append', default=[], help='file:first-last lines to leave alone')
    ap.add_argument('--only', action='append', default=[], help='restrict to these source files')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()

    fields = [parse_spec(s) for s in args.fields]
    arrays = [parse_spec(s) for s in args.array]
    member_names = {f for _, fs in fields for f in fs}

    skips = []
    for s in args.skip:
        f, _, r = s.rpartition(':')
        a, _, b = r.partition('-')
        skips.append((os.path.realpath(os.path.join(ROOT, f)), int(a), int(b or a)))

    cq = find_clang_query()
    db = load_compdb(args.build_dir)
    tmp = tempfile.mkdtemp(prefix='ptr32_')
    entries = []
    for e in db:
        f = os.path.realpath(os.path.join(e['directory'], e['file']))
        if not f.startswith(ROOT + os.sep) or not f.endswith(('.c', '.cpp')):
            continue
        if args.only and not any(f.endswith(o) for o in args.only):
            continue
        cmd = e.get('command') or shlex.join(e['arguments'])
        entries.append({'directory': e['directory'], 'file': f, 'command': lenient(cmd)})
    with open(os.path.join(tmp, 'compile_commands.json'), 'w') as fp:
        json.dump(entries, fp)
    qfile = os.path.join(tmp, 'q.txt')
    with open(qfile, 'w') as fp:
        fp.write(build_query(fields, arrays))

    all_sites = []
    with concurrent.futures.ThreadPoolExecutor(args.j) as ex:
        futs = {ex.submit(run_query, cq, tmp, qfile, e['file']): e['file'] for e in entries}
        for fu in concurrent.futures.as_completed(futs):
            sites, err = fu.result()
            if 'error:' in err:
                print('warning: %s: clang-query reported errors' % futs[fu], file=sys.stderr)
            all_sites.extend(sites)
    shutil.rmtree(tmp, ignore_errors=True)

    sources = {}

    def src_for(path):
        p = os.path.realpath(path if os.path.isabs(path) else os.path.join(ROOT, path))
        if not p.startswith(ROOT + os.sep) or os.path.relpath(p, ROOT).startswith('build'):
            return None
        if p not in sources:
            sources[p] = Source(p)
        return sources[p]

    def skipped(src, off):
        line = next(i for i in range(len(src.starts), 0, -1) if src.starts[i - 1] <= off)
        return any(src.path == f and a <= line <= b for f, a, b in skips)

    manual = []
    # per file: read sites {(begin, end)} and plain assignments
    # {(lhs_begin, lhs_end, end of the '=', rhs_end)}; unary holds operator
    # ranges whose operand must stay unwrapped
    reads = collections.defaultdict(set)
    assigns = collections.defaultdict(set)
    lhs_sites = collections.defaultdict(set)
    unary = []

    def where(src, off):
        line = next(i for i in range(len(src.starts), 0, -1) if src.starts[i - 1] <= off)
        return '%s:%d' % (os.path.relpath(src.path, ROOT), line)

    for s in all_sites:
        rng = s['range']
        kind = s['kind']
        if "'union (unnamed" in s['head'] or 'PTR32' in s['head']:
            continue  # the field is PTR32 already: a converted site, or one inside PGET/PSET
        if 'non_odr_use_unevaluated' in s['head']:
            continue  # the __typeof__ copy inside an existing PGET; the evaluated copy is matched too
        if rng is None:
            manual.append(('macro location', s['head']))
            continue
        src = src_for(rng[0][0])
        if src is None:
            continue
        if kind in ('read', 'aread'):
            if kind == 'read':
                # after #line one presumed line can be several real ones, each
                # its own site (the dump lists them all): take every match
                ends = matches(src, rng[1], s['member'])
                pairs = [(resolve(src, rng[0], before=e), e) for e in ends]
                if not pairs or any(b is None for b, _ in pairs) or s['member'] not in member_names:
                    manual.append(('unresolved read', s['head']))
                    continue
                for b, e in pairs[:-1]:
                    if not skipped(src, b):
                        reads[src.path].add((b, e + len(s['member'])))
                b, e = pairs[-1]
                e += len(s['member'])
            else:
                e = resolve(src, rng[1])
                b = resolve(src, rng[0], before=e) if e is not None else None
                if b is None or e is None or src.data[e:e + 1] != b']':
                    manual.append(('unresolved subscript', s['head']))
                    continue
                e += 1
            if b >= e:
                manual.append(('inverted range (macro?)', s['head']))
                continue
            if skipped(src, b):
                continue
            reads[src.path].add((b, e))
        elif kind in ('assign', 'aassign'):
            lhs = s['lhs']
            if s['op'] != '=':
                b = resolve(src, rng[0])
                manual.append(('compound assignment %s' % s['op'], where(src, b) if b is not None else s['head']))
                if lhs:
                    lb, le = resolve(src, lhs[0]), resolve(src, lhs[1])
                    if lb is not None and le is not None:
                        unary.append((src.path, lb, token_end(src, le)))
                continue
            if lhs is None:
                manual.append(('assignment without LHS range', s['head']))
                continue
            if kind == 'assign':
                le = None
                # the LHS ends at its member token; find it by name
                for name in member_names:
                    le = resolve(src, lhs[1], name)
                    if le is not None:
                        le += len(name)
                        break
            else:
                le = resolve(src, lhs[1])
                if le is not None and src.data[le:le + 1] == b']':
                    le += 1
                else:
                    le = None
            lb = resolve(src, lhs[0], before=le) if le is not None else resolve(src, lhs[0])
            re_ = resolve(src, rng[1], after=le) if le is not None else None
            rend = token_end(src, re_) if re_ is not None else None
            if re_ is None and lb is not None and le is not None:
                # the RHS ends inside a macro (NULL, a cast macro): find its
                # end by scanning the tokens after the '='
                eqpos = src.data.find(b'=', le)
                if eqpos >= 0 and not src.text(le, eqpos).strip():
                    rend = rhs_end(src, eqpos + 1)
                    re_ = rend if rend > eqpos + 1 else None
            if lb is None or le is None or re_ is None or re_ < le:
                manual.append(('assignment in a macro', where(src, lb) if lb is not None else s['head']))
                if lb is not None and le is not None:
                    unary.append((src.path, lb, le))
                continue
            if skipped(src, lb):
                continue
            between = src.text(le, re_)
            eq = between.find('=')
            if eq < 0 or between[:eq].strip():
                manual.append(('odd assignment', where(src, lb)))
                unary.append((src.path, lb, le))
                continue
            assigns[src.path].add((lb, le, le + eq + 1, rend))
            lhs_sites[src.path].add((lb, le))
        else:
            b = resolve(src, rng[0])
            e = resolve(src, rng[1])
            if kind != 'aaddr':
                label = {'addr': '&field', 'incdec': '++/-- on field',
                         'aincdec': '++/-- on array element', 'aderef': '*array'}[kind]
                manual.append((label, where(src, b) if b is not None else s['head']))
            if b is not None and e is not None:
                # the operand is the widest site inside the operator's range:
                # &x->arr[i] keeps its meaning (a pointer to the 32-bit word),
                # and the others are left for manual work
                unary.append((src.path, b, token_end(src, e)))

    for path, b, e in unary:
        inside = [r for r in reads[path] if b <= r[0] and r[1] <= e]
        if inside:
            widest = max(inside, key=lambda r: r[1] - r[0])
            reads[path].discard(widest)

    changed = 0
    for path in sorted(set(reads) | set(assigns)):
        src = sources[path]
        # (offset, closers first, wider ranges open first, text)
        ins = []
        reps = []  # (start, end, replacement)
        lhs = lhs_sites[path]
        for (b, e) in reads[path]:
            if (b, e) in lhs:
                continue
            ins.append((b, 1, -(e - b), 'PGET('))
            ins.append((e, 0, 0, ')'))
        for (lb, le, eqend, rend) in assigns[path]:
            ins.append((lb, 1, -(rend - lb), 'PSET('))
            reps.append((le, eqend, ','))
            ins.append((rend, 0, 0, ')'))
        # inserts never fall strictly inside a replaced span; build left to right
        events = sorted(ins, key=lambda t: (t[0], t[1], t[2]))
        reps.sort()
        out = bytearray()
        pos = 0
        ri = 0
        for off, _, _, text in events:
            while ri < len(reps) and reps[ri][0] < off:
                a, b2, t = reps[ri]
                out += src.data[pos:a] + t.encode()
                pos = b2
                ri += 1
            out += src.data[pos:off] + text.encode()
            pos = off
        while ri < len(reps):
            a, b2, t = reps[ri]
            out += src.data[pos:a] + t.encode()
            pos = b2
            ri += 1
        out += src.data[pos:]
        nread = len([1 for r in reads[path] if r not in lhs])
        print('%-40s %4d reads %4d writes' % (os.path.relpath(path, ROOT), nread, len(assigns[path])))
        if not args.dry_run and bytes(out) != src.data:
            with open(path, 'wb') as fp:
                fp.write(out)
            changed += 1

    if manual:
        print('\nmanual sites (left alone):')
        for why, at in sorted(set(manual)):
            print('  %-40s %s' % (why, at))
    print('\n%d file(s) %s' % (changed, 'would change' if args.dry_run else 'rewritten'))


if __name__ == '__main__':
    main()

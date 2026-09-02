#!/usr/bin/env python3
"""
tools/harness_hygiene.py — static audit of the test harnesses.

WHY THIS EXISTS: `postgres-dual-ci.yml` failed on 30 consecutive runner
executions while passing 599/0 on the operator's Mac, and the cause was two
lines at the top of `legacy/bug_check.py`:

    _orig_popen = subprocess.Popen
    subprocess.Popen = lambda *a, **kw: None      # for the whole run

A process-wide `Popen` that returns `None` is not a stub. `ctypes.util.
find_library` on Linux does `with subprocess.Popen(['/sbin/ldconfig','-p']) as p:`
— so `import pyzbar` raised `TypeError: 'NoneType' object does not support the
context manager protocol`, a type that no `except` clause in the harness
anticipated. On macOS the same function probes dyld and never shells out, so
the bug was invisible on every machine anyone tested on.

Two properties made it survive for two months:

  * the blast radius was the whole process, so the symptom appeared in an
    unrelated check;
  * the check that broke guarded its optional dependency with
    `except ImportError:` — one exception type — so a differently-shaped
    failure of the SAME missing library became a hard error on one platform
    and a SILENT PASS on the other. The assertion ran on neither.

This script looks for that class of defect, not that instance of it. It is
static (`ast`), needs no services, and runs in about a second.

    python tools/harness_hygiene.py                 # audit the default targets
    python tools/harness_hygiene.py path/to/file.py # audit specific files
    python tools/harness_hygiene.py --list          # what it checks, and why

Exit codes:  0 clean · 1 findings · 2 the auditor itself broke.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The harnesses this audits. Everything here runs as a GATE, which is exactly
# why a defect in one is expensive: it does not break a feature, it breaks the
# thing that would have told you a feature was broken.
DEFAULT_TARGETS = [
    "legacy/bug_check.py",
    "backend/api/service_tests.py",
    "tests/ai_eval/runner.py",
    "tools/parity_check.py",
]

# Standard-library attributes whose replacement affects EVERY library in the
# process, including ones the harness has never heard of.
_STDLIB_LANDMINES = {
    ("subprocess", "Popen"), ("subprocess", "run"), ("subprocess", "call"),
    ("subprocess", "check_output"), ("subprocess", "check_call"),
    ("os", "system"), ("os", "popen"), ("os", "fork"), ("os", "execv"),
    ("socket", "socket"), ("socket", "create_connection"),
    ("time", "sleep"), ("time", "time"),
    ("shutil", "which"), ("shutil", "rmtree"),
    ("platform", "system"), ("platform", "machine"),
    ("webbrowser", "open"), ("webbrowser", "open_new"),
    ("ssl", "create_default_context"),
    ("urllib.request", "urlopen"),
}

# A replacement value that cannot survive being used as its original would be.
# `None` is the whole story of this script; a bare lambda returning nothing is
# the same mistake wearing a different hat.
_UNUSABLE_STUBS = ("None-returning stub",)

# Names that look like an outbound sender or a queue drainer. Reassigning one
# without restoring it leaves the next check talking to a mock it did not ask
# for — and, worse, leaves a REAL sender mocked out if the restore is what got
# skipped.
_SENDER_PREFIXES = ("_send", "send_", "_dispatch", "dispatch", "_deliver",
                    "_post", "_notify", "fire_", "_flush", "process_queue")

# Exception types that an optional native dependency can plausibly raise while
# being unavailable. A guard naming exactly ONE of these is guessing which way
# the library will fail, and the libzbar case proves it guesses wrong: the same
# absent library raised ImportError on macOS and TypeError on Linux.
_DEP_FAILURE_TYPES = {"ImportError", "ModuleNotFoundError", "OSError",
                      "TypeError", "AttributeError", "ValueError",
                      "RuntimeError"}

# ⚠️ A CATCH-ALL IS THE FIX, NOT THE DEFECT, so H3 must not fire on one. The
# rule's first draft had `Exception` inside the set above and therefore flagged
# every guard it had just persuaded somebody to write — a gate that rejects its
# own remedy teaches people to disable it.
_CATCH_ALL_TYPES = {"Exception", "BaseException"}


@dataclass
class Finding:
    rule: str
    path: str
    line: int
    message: str
    why: str

    def render(self) -> str:
        return (f"{self.path}:{self.line}: [{self.rule}] {self.message}\n"
                f"    why: {self.why}")


RULES = {
    "H1": "a stdlib primitive is replaced process-wide with an unusable stub",
    "H2": "a stdlib primitive is replaced at module scope and never restored",
    "H3": "an optional-dependency guard catches a single exception type",
    "H4": "a check can no-op silently — a skip that reports itself as a pass",
    "H5": "a sender/queue mock is installed without a try/finally that restores it",
}


def _dotted(node: ast.AST) -> str:
    """`subprocess.Popen` → 'subprocess.Popen'; '' for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _returns_nothing(value: ast.AST) -> bool:
    """True for `lambda *a, **kw: None`, `lambda: None` and a bare `None`."""
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    return (isinstance(value, ast.Lambda)
            and isinstance(value.body, ast.Constant)
            and value.body.value is None)


def _enclosing_functions(tree: ast.Module) -> dict[int, ast.FunctionDef]:
    """line number → the function that contains it (innermost wins)."""
    owner: dict[int, ast.FunctionDef] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if hasattr(n, "lineno"):
                    owner[n.lineno] = fn
    return owner


def audit_file(path: Path) -> list[Finding]:
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:                          # outside the repo (self-test)
        rel = str(path)
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=rel)
    owner = _enclosing_functions(tree)
    findings: list[Finding] = []

    # ── H1 / H2 / H5 — assignments that replace something ────────────────────
    module_level_patches: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for dotted in _assign_targets(node):
            if "." not in dotted:
                continue
            mod, _, attr = dotted.rpartition(".")
            fn = owner.get(node.lineno)

            if (mod, attr) in _STDLIB_LANDMINES:
                if _returns_nothing(node.value):
                    findings.append(Finding(
                        "H1", rel, node.lineno,
                        f"`{dotted}` is replaced with something that returns "
                        f"None",
                        "every library in this process now shares that stub. "
                        "`ctypes.util.find_library` does `with "
                        "subprocess.Popen(...) as p:` — a None there raises "
                        "TypeError, not ImportError, and the failure surfaces "
                        "somewhere unrelated. Substitute an object that keeps "
                        "the contract (context manager, .communicate, "
                        ".returncode), or narrow the patch to the caller you "
                        "actually mean."))
                if fn is None:
                    module_level_patches[dotted] = node.lineno

            if fn is not None and attr.startswith(_SENDER_PREFIXES):
                if not _restored_in_finally(fn, dotted):
                    findings.append(Finding(
                        "H5", rel, node.lineno,
                        f"`{dotted}` is mocked inside `{fn.name}` with no "
                        f"try/finally restoring it",
                        "an assertion that fires early leaves the mock "
                        "installed for every later check — which silently "
                        "disarms a real sender, and the checks after it pass "
                        "for the wrong reason."))

    # H2 — module-level stdlib patches that nothing restores anywhere in the file
    restored = {d
                for n in ast.walk(tree) if isinstance(n, ast.Assign)
                if _looks_like_restore(n)
                for d in _assign_targets(n)}
    for dotted, line in module_level_patches.items():
        if dotted not in restored:
            findings.append(Finding(
                "H2", rel, line,
                f"`{dotted}` is patched at module scope and never restored",
                "the patch outlives the run. Anything importing this module — "
                "a REPL, another harness, a profiler — inherits it."))

    # ── H3 / H4 — the guards inside checks ───────────────────────────────────
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not (fn.name.startswith("check") or fn.name.startswith("test")):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            if not any(isinstance(s, (ast.Import, ast.ImportFrom))
                       for s in node.body):
                continue
            for handler in node.handlers:
                caught = _handler_names(handler)
                if caught and not (caught & _CATCH_ALL_TYPES) and \
                        len(caught & _DEP_FAILURE_TYPES) == 1 and \
                        caught <= _DEP_FAILURE_TYPES:
                    findings.append(Finding(
                        "H3", rel, handler.lineno,
                        f"`{fn.name}` guards an optional import with "
                        f"`except {'/'.join(sorted(caught))}` alone",
                        "a missing native library does not raise a consistent "
                        "type. libzbar raised ImportError on macOS and "
                        "TypeError on Linux for the SAME absent library — the "
                        "one-type guard turned that into a silent pass on one "
                        "platform and a hard failure on the other."))
                if _body_is_bare_return(handler.body):
                    findings.append(Finding(
                        "H4", rel, handler.lineno,
                        f"`{fn.name}` returns silently when its dependency is "
                        f"missing",
                        "the run stays green and the report counts it as a "
                        "PASS, so coverage nobody has is indistinguishable "
                        "from coverage that works. Raise a SkipCheck (or the "
                        "harness's equivalent) so the skip is counted and "
                        "printed."))
    return findings


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:                    # bare `except:` — a catch-all
        return {"BaseException"}
    if isinstance(handler.type, ast.Tuple):
        return {_dotted(e) or getattr(e, "id", "") for e in handler.type.elts}
    return {_dotted(handler.type) or getattr(handler.type, "id", "")}


def _body_is_bare_return(body: list[ast.stmt]) -> bool:
    """`return` / `return None` / `pass`, possibly after a docstring."""
    real = [s for s in body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(real) != 1:
        return False
    s = real[0]
    if isinstance(s, ast.Pass):
        return True
    return isinstance(s, ast.Return) and (
        s.value is None
        or (isinstance(s.value, ast.Constant) and s.value.value is None))


def _looks_like_restore(node: ast.Assign) -> bool:
    """`subprocess.Popen = _orig_popen` — a restore, not a patch."""
    v = node.value
    name = v.id if isinstance(v, ast.Name) else _dotted(v)
    low = (name or "").lower()
    return any(k in low for k in ("orig", "original", "real", "saved", "prev"))


def _assign_targets(node: ast.Assign) -> list[str]:
    """Every dotted name this assignment writes, tuple/list targets included.

    ⚠️ THE TUPLE FORM IS THE COMMON ONE IN RESTORES, and missing it made this
    auditor's first run cry wolf on `check_meta_provider_routing`, which
    restores correctly with `W.WHATSAPP_PROVIDER, W._send_via_meta = op, om`.
    A hygiene gate that reports a correct restore as a defect is the same
    category of problem it exists to find.
    """
    out: list[str] = []
    for target in node.targets:
        elts = (target.elts if isinstance(target, (ast.Tuple, ast.List))
                else [target])
        out.extend(d for d in (_dotted(e) for e in elts) if d)
    return out


def _restored_in_finally(fn: ast.AST, dotted: str) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            for stmt in node.finalbody:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Assign) and \
                            dotted in _assign_targets(sub):
                        return True
        # `with patch.object(...)` / `with patch(...)` restores on exit.
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if "patch" in ast.dump(item.context_expr):
                    return True
    return False


# ── negative controls ───────────────────────────────────────────────────────
# ⚠️ AN AUDITOR THAT CANNOT FAIL IS AN AUDITOR NOBODY SHOULD TRUST. The suite it
# guards learned this the hard way twice: `check_qr_decode_roundtrip` reported a
# PASS for an assertion that had never executed, and suite E's cookie-replay
# checks passed vacuously for months. So every rule here carries a snippet that
# MUST trip it and a near-identical one that must NOT — the second half is the
# one that matters, because a rule that fires on everything gets switched off.
_SELF_TEST: list[tuple[str, str, bool, str]] = [
    ("H1", """
import subprocess
subprocess.Popen = lambda *a, **kw: None
""", True, "the exact line that cost 30 CI runs"),
    ("H1", """
import subprocess
_orig = subprocess.Popen
subprocess.Popen = _guarded_popen
subprocess.Popen = _orig
""", False, "a real stand-in, restored — the remedy must not trip the rule"),
    ("H2", """
import platform
platform.system = lambda: "Linux"
""", True, "a module-scope patch nothing puts back outlives the run"),
    ("H2", """
import platform
_orig_platform_system = platform.system
platform.system = lambda: "Linux"
platform.system = _orig_platform_system
""", False, "saved and restored"),
    ("H3", """
def check_thing():
    try:
        import pyzbar
    except ImportError:
        raise SkipCheck("no")
""", True, "one named type is a guess about how a native lib fails"),
    ("H3", """
def check_thing():
    try:
        import pyzbar
    except Exception:
        raise SkipCheck("no")
""", False, "a catch-all is the FIX; flagging it would reject the remedy"),
    ("H4", """
def check_thing():
    try:
        import reportlab
    except Exception:
        return
""", True, "a bare return is a skip wearing a pass's clothes"),
    ("H4", """
def check_thing():
    try:
        import reportlab
    except Exception as e:
        raise SkipCheck(str(e))
""", False, "raising a skip is the remedy"),
    ("H5", """
def check_sender():
    om = W._send_via_meta
    W._send_via_meta = lambda p, t: None
    assert True
""", True, "a sender mocked with nothing to put it back"),
    ("H5", """
def check_sender():
    op, om = W.PROVIDER, W._send_via_meta
    try:
        W._send_via_meta = lambda p, t: None
        assert True
    finally:
        W.PROVIDER, W._send_via_meta = op, om
""", False, "restored via a TUPLE target — the false positive this rule shipped with"),
]


def self_test() -> int:
    """Prove each rule still fires on its bug and stays quiet on its fix."""
    import tempfile
    failures = 0
    with tempfile.TemporaryDirectory(prefix="hygiene_selftest_") as td:
        for i, (rule, src, should_fire, why) in enumerate(_SELF_TEST):
            p = Path(td) / f"case_{i}.py"
            p.write_text(src, encoding="utf-8")
            fired = any(f.rule == rule for f in audit_file(p))
            ok = fired is should_fire
            verb = "fires" if should_fire else "stays quiet"
            print(f"  {'✅' if ok else '❌'} {rule} {verb}: {why}")
            failures += 0 if ok else 1
    print(f"▶ self-test · {len(_SELF_TEST)} controls · {failures} failed")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("paths", nargs="*", help="files to audit (default: the gates)")
    ap.add_argument("--list", action="store_true", help="print the rules and exit")
    ap.add_argument("--self-test", action="store_true",
                    help="prove each rule fires on its bug and not on its fix")
    args = ap.parse_args(argv)

    if args.list:
        for rid, desc in RULES.items():
            print(f"  {rid}  {desc}")
        return 0

    if args.self_test:
        return self_test()

    targets = [Path(p) for p in args.paths] or \
              [REPO_ROOT / p for p in DEFAULT_TARGETS]

    findings: list[Finding] = []
    audited = 0
    for path in targets:
        if not path.exists():
            print(f"  – skipped (not present): {path}")
            continue
        audited += 1
        try:
            findings.extend(audit_file(path))
        except SyntaxError as e:
            print(f"❌ {path}: does not parse — {e}")
            return 2

    print(f"▶ harness hygiene · {audited} file(s) audited · "
          f"{len(RULES)} rules")
    if not findings:
        print("✅ clean")
        return 0

    for f in sorted(findings, key=lambda f: (f.path, f.line)):
        print(f"❌ {f.render()}")
    print(f"\n❌ {len(findings)} finding(s). "
          f"See `python tools/harness_hygiene.py --list` for what each rule means.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — the auditor must not be the outage
        print(f"❌ harness_hygiene crashed: {type(e).__name__}: {e}")
        sys.exit(2)

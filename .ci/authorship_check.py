#!/usr/bin/env python3
"""Authorship check (Lane 5 F6): new commits and metadata name only the rule's author.

The owner's rule (21 Nov): every author and maintainer is Adam Szablya <aszablya@metrolla.com>,
and nothing names an AI assistant as an author (no co-author trailers, no "generated with"
lines, no assistant session links).

What it checks, for the commits in BASE..HEAD only (history already on the base branch is never
read, so old commits and upstream history do not fail it):

  1. Each commit's author and committer are exactly the rule's identity.
     Allowed exceptions:
       - a merge commit GitHub made (committer GitHub <noreply@github.com>, two or more parents:
         the merge button or "Update branch"); its author is the GitHub account that clicked;
       - the committer GitHub <noreply@github.com> on any commit (GitHub's own web-flow
         committer), the author is still checked;
       - upstream commits, when .ci/authorship_upstream.txt exists (driver forks): a commit whose
         author and committer both match a pattern there is upstream history brought in by a
         sync. Only the assistant markers (rule 2b) are checked on those.
  2. Each commit message has
       a. no "Co-authored-by:" trailer (any name) and no line starting "Generated with";
       b. no assistant marker: "noreply@anthropic.com", "Claude-Session:", "claude.ai/code/session".
  3. Lines the range adds to metadata files (package.xml, setup.py, setup.cfg, pyproject.toml,
     package.json, Cargo.toml, debian/control, debian/changelog, AUTHORS/CONTRIBUTORS/MAINTAINERS)
     name only the rule's identity in author/maintainer fields (the email must be the rule's
     address and a person's name must be the rule's name).
  4. Lines the range adds to any tracked file carry no assistant byline marker (rule 2b's markers
     and "Generated with [Claude").
  5. No tracked file at HEAD, and no new commit message, contains a personal address of the owner
     (the work identity above is the only one to use). The address is held here only as a sha256
     of its normalised form (lower case; for the webmail domain, dots and any +tag in the local
     part removed), so this file never names it; every e-mail-like token is normalised, hashed and
     compared, and a finding prints the file and line only, never the token.

Usage:
  python3 .ci/authorship_check.py --base <rev> --head <rev>   # a pull request: base.sha..head.sha
  python3 .ci/authorship_check.py --branch-only                # a push: commits on no other branch
  python3 .ci/authorship_check.py --self-test
  add --json for machine output.
Exit codes: 0 clean, 1 a finding (or an error).
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RULE_NAME = "Adam Szablya"
RULE_EMAIL = "aszablya@metrolla.com"
GITHUB_COMMITTER_EMAIL = "noreply@github.com"
UPSTREAM_FILE = ".ci/authorship_upstream.txt"
SELF_PATHS = {".ci/authorship_check.py", ".ci/authorship_upstream.txt"}
# sha256 of the owner's personal address, normalised by normalise_email(). Never the address.
PERSONAL_SHA256 = {"6039027f5058f63b912bf9e253d5a61484237724ebda1c4e6edbc4294bd3936f"}

# Written split so this file never contains the markers it looks for.
_AT = "@"
ASSISTANT_MARKERS = [
    re.compile(r"noreply" + _AT + r"anthropic\.com", re.I),
    re.compile(r"^\s*claude" + r"-session\s*:", re.I | re.M),
    re.compile(r"claude\.ai/code/" + r"session", re.I),
    re.compile(r"generated with \[?" + r"claude", re.I),
]
TRAILER_COAUTHOR = re.compile(r"^\s*co-authored" + r"-by\s*:", re.I | re.M)
LINE_GENERATED = re.compile(r"^\W{0,4}\s*generated" + r" with\b", re.I | re.M)

META_NAMES = {
    "package.xml", "setup.py", "setup.cfg", "pyproject.toml", "package.json", "cargo.toml",
}
META_PATTERNS = [
    re.compile(r"(^|/)debian/(control|changelog)$"),
    re.compile(r"(^|/)(authors|contributors|maintainers)([._-][^/]*)?$", re.I),
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+" + _AT + r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9._%+-]+" + _AT + r"[A-Za-z0-9-]+")


def normalise_email(token: str) -> str:
    local, _, domain = token.strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def is_personal(token: str, hashes: set[str]) -> bool:
    import hashlib
    return hashlib.sha256(normalise_email(token).encode()).hexdigest() in hashes


def git(*args: str, cwd: str | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def is_meta(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    if base in META_NAMES:
        return True
    return any(p.search(path) for p in META_PATTERNS)


def load_upstream(cwd: str | None) -> list[re.Pattern]:
    p = Path(cwd or ".") / UPSTREAM_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(re.compile(line, re.I))
    return out


def matches_any(value: str, pats: list[re.Pattern]) -> bool:
    return any(p.search(value) for p in pats)


def rule_identity(name: str, email: str) -> bool:
    return name.strip() == RULE_NAME and email.strip().lower() == RULE_EMAIL


# ---------------------------------------------------------------- commits

def commits_in_range(base: str, head: str, cwd: str | None) -> list[str]:
    out = git("rev-list", "--reverse", f"{base}..{head}", cwd=cwd)
    return [c for c in out.split() if c]


def commits_on_branch_only(head: str, cwd: str | None) -> list[str]:
    """Commits reachable from HEAD and from no other remote branch or tag (a push)."""
    current = os.environ.get("GITHUB_REF_NAME", "")
    refs = git("for-each-ref", "--format=%(refname)", "refs/remotes", "refs/tags", cwd=cwd).split()
    others = [r for r in refs if not r.endswith("/HEAD") and r != f"refs/remotes/origin/{current}"]
    if not others:
        return []
    out = git("rev-list", "--reverse", head, "--not", *others, cwd=cwd)
    return [c for c in out.split() if c]


def check_commit(sha: str, upstream: list[re.Pattern], cwd: str | None,
                 personal: set[str] = PERSONAL_SHA256) -> list[dict]:
    fmt = "%an%x00%ae%x00%cn%x00%ce%x00%P%x00%B"
    an, ae, cn, ce, parents, body = git("show", "-s", f"--format={fmt}", sha, cwd=cwd).split("\x00", 5)
    nparents = len(parents.split())
    short = sha[:10]
    found: list[dict] = []

    def add(rule: str, detail: str) -> None:
        found.append({"commit": short, "rule": rule, "detail": detail})

    for pat in ASSISTANT_MARKERS:
        m = pat.search(body)
        if m:
            add("assistant-marker", f"message contains {m.group(0).strip()!r}")
    if any(is_personal(t, personal) for t in EMAIL_RE.findall(body)):
        add("personal-address", "message contains the owner's personal address (hashed match)")
    github_made = ce.strip().lower() == GITHUB_COMMITTER_EMAIL
    upstream_commit = bool(upstream) and matches_any(f"{an} <{ae}>", upstream) and (
        github_made or matches_any(f"{cn} <{ce}>", upstream))
    if upstream_commit:
        return found
    for m in TRAILER_COAUTHOR.finditer(body):
        line = body[m.start():].splitlines()[0].strip()
        add("co-author-trailer", line)
    for m in LINE_GENERATED.finditer(body):
        line = body[m.start():].splitlines()[0].strip()
        add("generated-with-line", line)
    if github_made and nparents >= 2:
        return found  # GitHub's merge button / "Update branch": author is the clicking account
    if not rule_identity(an, ae):
        add("author", f"{an} <{ae}>")
    if not github_made and not rule_identity(cn, ce):
        add("committer", f"{cn} <{ce}>")
    return found


# ---------------------------------------------------------------- added lines

def added_lines(base: str, head: str, cwd: str | None) -> list[tuple[str, int, str]]:
    out = git("diff", "--no-color", "--no-ext-diff", "-U0", "--no-renames", f"{base}...{head}", cwd=cwd)
    path = None
    lineno = 0
    res = []
    for line in out.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            path = None if p == "/dev/null" else p[2:] if p.startswith("b/") else p
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and path is not None:
            res.append((path, lineno, line[1:]))
            lineno += 1
    return res


AUTHOR_FIELD_PATTERNS = [
    # package.xml
    re.compile(r"<(maintainer|author)\b([^>]*)>([^<]*)</\1>", re.I),
    # debian/control, debian/changelog sign-off
    re.compile(r"^(Maintainer|Uploaders)\s*:\s*(.*)$", re.I),
    re.compile(r"^-- (.+?<[^>]*>)"),
    # setup.py / setup.cfg
    re.compile(r"\b(author|maintainer)(_email)?\s*[=:]\s*['\"]?([^'\",]*)", re.I),
    # pyproject / package.json / Cargo.toml
    re.compile(r"\b(authors|maintainers|contributors|author)\b[\"']?\s*[=:]\s*(.*)$", re.I),
    re.compile(r"\{\s*name\s*=\s*\"([^\"]*)\"(?:\s*,\s*email\s*=\s*\"([^\"]*)\")?", re.I),
    re.compile(r"\"(name|email)\"\s*:\s*\"([^\"]*)\""),
]


def check_meta_line(path: str, text: str, upstream: list[re.Pattern]) -> str | None:
    s = text.strip()
    if not s or s.startswith(("<!--", "#", "//")):
        return None
    base = path.rsplit("/", 1)[-1].lower()
    line_emails = EMAIL_RE.findall(s)
    if upstream and line_emails and all(matches_any(f"<{e}>", upstream) for e in line_emails):
        return None  # an upstream maintainer's own line (a fork sync)
    authorsfile = bool(META_PATTERNS[1].search(path))
    relevant = authorsfile or any(p.search(s) for p in AUTHOR_FIELD_PATTERNS[:6])
    if base == "package.json" and re.search(r"\"(name|email)\"\s*:", s):
        # only inside an author/contributors/maintainers object; flag the email form only
        relevant = bool(EMAIL_RE.search(s))
    if not relevant:
        return None
    if base == "setup.cfg" and not re.match(r"\s*(author|maintainer)(_email)?\s*=", s, re.I):
        return None
    if base == "pyproject.toml" and not re.search(r"\b(authors|maintainers)\b|\{\s*name\s*=", s, re.I):
        return None
    if base in ("setup.py",) and not re.search(r"\b(author|maintainer)(_email)?\s*=", s, re.I):
        return None
    emails = [e.lower() for e in EMAIL_RE.findall(s)]
    bad_emails = [e for e in emails if e != RULE_EMAIL]
    if bad_emails:
        return f"author/maintainer address {bad_emails[0]}"
    # names: package.xml element text, setup.py author=/maintainer=, pyproject name =, control
    m = re.search(r"<(maintainer|author)\b[^>]*>([^<]*)</", s, re.I)
    if m and m.group(2).strip() != RULE_NAME:
        return f"{m.group(1)} name {m.group(2).strip()!r}"
    m = re.search(r"\b(author|maintainer)\s*[=:]\s*['\"]([^'\"]*)['\"]", s, re.I)
    if m and base in ("setup.py", "setup.cfg") and m.group(2).strip() != RULE_NAME:
        return f"{m.group(1)} name {m.group(2).strip()!r}"
    m = re.search(r"\bname\s*=\s*\"([^\"]*)\"", s)
    if m and base == "pyproject.toml" and m.group(1).strip() != RULE_NAME:
        return f"author name {m.group(1).strip()!r}"
    m = re.match(r"(Maintainer|Uploaders)\s*:\s*([^<]*)<", s, re.I)
    if m and m.group(2).strip() != RULE_NAME:
        return f"{m.group(1)} name {m.group(2).strip()!r}"
    m = re.match(r"-- ([^<]*)<", s)
    if m and base == "changelog" and m.group(1).strip() != RULE_NAME:
        return f"changelog sign-off {m.group(1).strip()!r}"
    if authorsfile and not emails and s and RULE_NAME not in s:
        return f"authors file entry {s[:60]!r}"
    if base in ("setup.py", "setup.cfg", "pyproject.toml", "package.json", "cargo.toml") and not emails:
        m = re.search(r"\b(author|maintainer)[\"']?\s*[=:]\s*['\"]([^'\"]+)['\"]", s, re.I)
        if m and m.group(2).strip() != RULE_NAME:
            return f"{m.group(1)} {m.group(2).strip()!r}"
    return None


def check_added(base: str, head: str, upstream: list[re.Pattern], cwd: str | None) -> list[dict]:
    found = []
    for path, lineno, text in added_lines(base, head, cwd):
        if path in SELF_PATHS or path.startswith(".ci/tests/authorship"):
            continue
        for pat in ASSISTANT_MARKERS:
            m = pat.search(text)
            if m:
                found.append({"file": path, "line": lineno, "rule": "assistant-marker",
                              "detail": m.group(0).strip()})
                break
        if is_meta(path):
            why = check_meta_line(path, text, upstream)
            if why:
                found.append({"file": path, "line": lineno, "rule": "metadata-author", "detail": why})
    return found


def check_tree(head: str, cwd: str | None, personal: set[str] = PERSONAL_SHA256) -> list[dict]:
    """Every tracked file at HEAD: no e-mail-like token that hashes to a personal address."""
    r = subprocess.run(["git", "grep", "-I", "-n", "-o", "-E", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", head, "--"],
                       cwd=cwd, capture_output=True, text=True)
    found = []
    for line in r.stdout.splitlines():
        parts = line.split(":", 3)  # rev:path:line:token
        if len(parts) != 4 or parts[1] in SELF_PATHS:
            continue
        if is_personal(parts[3], personal):
            found.append({"file": parts[1], "line": int(parts[2]), "rule": "personal-address",
                          "detail": "the owner's personal address (hashed match); use the work identity"})
    return found


# ---------------------------------------------------------------- main

def run(base: str | None, head: str, branch_only: bool, cwd: str | None = None,
        personal: set[str] = PERSONAL_SHA256) -> dict:
    upstream = load_upstream(cwd)
    if branch_only:
        commits = commits_on_branch_only(head, cwd)
        base = f"{commits[0]}^" if commits else head
        if commits and not git("rev-parse", "-q", "--verify", f"{commits[0]}^", cwd=cwd, check=False).strip():
            base = git("hash-object", "-t", "tree", "/dev/null", cwd=cwd).strip()
    else:
        commits = commits_in_range(base, head, cwd)
    findings = []
    for c in commits:
        findings += check_commit(c, upstream, cwd, personal)
    if commits:
        findings += check_added(base, head, upstream, cwd)
    findings += check_tree(head, cwd, personal)
    return {"base": base, "head": head, "commits": len(commits), "findings": findings}


def report(res: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res, indent=2))
    else:
        print(f"authorship check: {res['commits']} new commit(s) in {res['base']}..{res['head']}")
        for f in res["findings"]:
            where = f.get("commit") or f"{f['file']}:{f['line']}"
            print(f"  FAIL {where}: {f['rule']}: {f['detail']}")
        if res["findings"]:
            print(f"{len(res['findings'])} finding(s). Rule: author and committer "
                  f"{RULE_NAME} <{RULE_EMAIL}>, no co-author or generated-with lines.")
        else:
            print("ok")
    return 1 if res["findings"] else 0


# ---------------------------------------------------------------- self-test

def self_test() -> int:
    ok_id = {"GIT_AUTHOR_NAME": RULE_NAME, "GIT_AUTHOR_EMAIL": RULE_EMAIL,
             "GIT_COMMITTER_NAME": RULE_NAME, "GIT_COMMITTER_EMAIL": RULE_EMAIL}
    other = {"GIT_AUTHOR_NAME": "Someone Else", "GIT_AUTHOR_EMAIL": "someone" + _AT + "example.com",
             "GIT_COMMITTER_NAME": "Someone Else", "GIT_COMMITTER_EMAIL": "someone" + _AT + "example.com"}
    gh_merge = {"GIT_AUTHOR_NAME": "Owner Web", "GIT_AUTHOR_EMAIL": "owner" + _AT + "example.org",
                "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": GITHUB_COMMITTER_EMAIL}
    failures = []
    import hashlib
    fake = "Personal.Test+x" + _AT + "gmail.com"  # normalises to personaltest at the webmail domain
    test_personal = {hashlib.sha256(normalise_email(fake).encode()).hexdigest()}

    with tempfile.TemporaryDirectory() as d:
        def g(*a, env=None):
            e = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
                     **(env or ok_id))
            r = subprocess.run(["git", *a], cwd=d, env=e, capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(r.stderr)
            return r.stdout.strip()

        def commit(msg, files=None, env=None):
            for name, content in (files or {"f.txt": msg}).items():
                p = Path(d, name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                g("add", name, env=env)
            g("commit", "-q", "-m", msg, env=env)
            return g("rev-parse", "HEAD")

        def expect(label, base, head, rules):
            res = run(base, head, False, cwd=d, personal=test_personal)
            got = sorted({f["rule"] for f in res["findings"]})
            if got != sorted(rules):
                failures.append(f"{label}: expected {sorted(rules)}, got {got} {res['findings']}")

        g("init", "-q", "-b", "main")
        # upstream history on main with other authors and trailers: never read
        commit("old upstream work\n\nCo-authored" + "-by: X <x" + _AT + "y.z>", env=other)
        main = commit("old owner work", env=ok_id)

        g("checkout", "-q", "-b", "good")
        commit("good change", {"a.txt": "a"})
        commit("metadata ok", {"pkg/package.xml":
               f'<package><maintainer email="{RULE_EMAIL}">{RULE_NAME}</maintainer></package>\n'})
        expect("clean branch", main, "HEAD", [])

        # GitHub merge commit ("Update branch") with the owner's web identity
        g("checkout", "-q", "main")
        commit("main moves on", {"m.txt": "m"}, env=ok_id)
        g("checkout", "-q", "good")
        g("merge", "-q", "--no-ff", "-m", "Merge branch 'main' into good", "main", env=gh_merge)
        expect("github merge commit", main, "HEAD", [])

        g("checkout", "-q", "-b", "bad-trailer", "good")
        commit("bad\n\nCo-Authored" + "-By: Claude <noreply" + _AT + "anthropic.com>", {"b.txt": "b"})
        expect("co-author trailer", "good", "HEAD", ["assistant-marker", "co-author-trailer"])

        g("checkout", "-q", "-b", "bad-generated", "good")
        commit("bad\n\n\U0001F916 Generated" + " with [Claude Code](https://example.invalid)", {"c.txt": "c"})
        expect("generated-with line", "good", "HEAD", ["assistant-marker", "generated-with-line"])

        g("checkout", "-q", "-b", "bad-author", "good")
        commit("someone else", {"d.txt": "d"}, env=other)
        expect("other author", "good", "HEAD", ["author", "committer"])

        g("checkout", "-q", "-b", "bad-web-edit", "good")
        commit("web edit, not a merge", {"e.txt": "e"}, env=gh_merge)
        expect("github committer, non-merge, other author", "good", "HEAD", ["author"])

        g("checkout", "-q", "-b", "bad-meta", "good")
        commit("metadata", {"x/package.xml":
               '<package><maintainer email="you' + _AT + 'example.com">You</maintainer></package>\n',
               "x/setup.py": "setup(\n    maintainer='Someone',\n    maintainer_email='s" + _AT + "x.io',\n)\n",
               "debian/control": "Source: x\nMaintainer: Team <team" + _AT + "x.io>\n"})
        expect("metadata authors", "good", "HEAD", ["metadata-author"])
        res = run("good", "HEAD", False, cwd=d)
        if len([f for f in res["findings"] if f["rule"] == "metadata-author"]) != 4:
            failures.append(f"metadata authors: expected 4 findings, got {res['findings']}")

        g("checkout", "-q", "-b", "bad-personal", "good")
        commit("doc", {"docs/owner.md": "Owner: P (personaltest" + _AT + "GMail.com)\n"})
        expect("personal address in a tracked file", "good", "HEAD", ["personal-address"])
        g("checkout", "-q", "-b", "bad-personal-old", "good~1")
        commit("doc", {"docs/owner.md": "Owner: P (p.e.r.sonaltest+y" + _AT + "gmail.com)\n"})
        g("checkout", "-q", "good")
        g("merge", "-q", "--no-ff", "-m", "merge", "bad-personal-old")
        expect("personal address already on a branch is still found at HEAD", "good~1", "HEAD", ["personal-address"])
        g("reset", "-q", "--hard", "HEAD~1")
        if normalise_email("A.B+c" + _AT + "GoogleMail.com") != "ab" + _AT + "gmail.com":
            failures.append("normalise_email")

        g("checkout", "-q", "-b", "bad-byline", "good")
        commit("doc", {"docs/x.md": "text\n\U0001F916 Generated" + " with [Claude Code](https://x)\n"})
        expect("byline in a file", "good", "HEAD", ["assistant-marker"])

        # upstream sync in a fork: allowed by .ci/authorship_upstream.txt
        g("checkout", "-q", "-b", "fork-sync", "good")
        commit("allow upstream", {UPSTREAM_FILE: r"@example\.com>$" + "\n"})
        base_sync = g("rev-parse", "HEAD")
        commit("upstream fix\n\nCo-authored" + "-by: Y <y" + _AT + "example.com>", {"u.txt": "u"}, env=other)
        expect("upstream commit allowed", base_sync, "HEAD", [])
        commit("upstream with marker\n\nCo-authored" + "-by: C <noreply" + _AT + "anthropic.com>",
               {"u2.txt": "u"}, env=other)
        expect("upstream commit, assistant marker still fails", base_sync, "HEAD", ["assistant-marker"])

    for f in failures:
        print("SELF-TEST FAIL:", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="base revision (a pull request's base sha)")
    ap.add_argument("--head", default="HEAD", help="head revision (default HEAD)")
    ap.add_argument("--branch-only", action="store_true",
                    help="check the commits on HEAD that no other remote branch or tag has (a push)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.base and not a.branch_only:
        ap.error("give --base (pull request) or --branch-only (push)")
    try:
        res = run(a.base, a.head, a.branch_only)
    except RuntimeError as e:
        print(f"authorship check: error: {e}", file=sys.stderr)
        return 1
    return report(res, a.json)


if __name__ == "__main__":
    sys.exit(main())

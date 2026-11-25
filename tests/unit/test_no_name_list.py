"""No customer-name list is tracked in this repository, and images do not ship .ci/.

Customer-name checks run on the maintainers' machines before a push; no list is kept in this
repository. This test fails when a name list file is tracked again: .ci/name-denylist.txt,
.ci/name-allowlist.txt (at any depth) or any file whose name contains "denylist".
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIST_FILES = (".ci/name-denylist.txt", ".ci/name-allowlist.txt")


def name_list_files(paths):
    """The paths in PATHS that are a customer-name list."""
    hits = []
    for p in paths:
        base = p.rsplit("/", 1)[-1].lower()
        if any(p == f or p.endswith("/" + f) for f in LIST_FILES) or "denylist" in base:
            hits.append(p)
    return hits


def tracked_files():
    """Tracked paths of the checkout at ROOT, or None when ROOT is not the top of a git checkout
    (a package build tree, an unpacked source archive)."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                             capture_output=True, text=True)
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT:
            return None
        ls = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in ls.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


class NoNameListTracked(unittest.TestCase):
    def test_matcher(self):
        paths = [".ci/name-denylist.txt", ".ci/name-allowlist.txt", "src/x/.ci/name-allowlist.txt",
                 "docs/site-DenyList.yaml", "src/node.py", ".ci/authorship_check.py",
                 "scripts/public_param_allowlist.txt"]
        self.assertEqual(name_list_files(paths), paths[:4])

    def test_no_name_list_is_tracked(self):
        files = tracked_files()
        if files is None:
            self.skipTest("not a git checkout")
        self.assertEqual(name_list_files(files), [],
                         "customer-name lists are not kept in the repository")

    def test_images_do_not_ship_ci(self):
        """The image build context (the repository root) leaves out .ci/."""
        if tracked_files() is None:
            self.skipTest("not a git checkout")
        ignore = ROOT / ".dockerignore"
        self.assertTrue(ignore.is_file(), ".dockerignore is missing")
        lines = {l.strip() for l in ignore.read_text().splitlines()}
        self.assertTrue(lines & {".ci", ".ci/", "/.ci", "/.ci/", ".ci/**", "/.ci/**"},
                        ".dockerignore must exclude .ci/")


if __name__ == "__main__":
    unittest.main()

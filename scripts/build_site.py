"""
Stage the GitHub Pages artifact into a directory (default: _site).

The published site is docs/ (the static demo), minus the repo's markdown
notes that live in the same folder, plus the pages the project already
served before the demo existed: dashboard/ at /dashboard/ and the earlier
homepage as /home-classic.html. Without those two, switching Pages to the
Actions source would silently take the existing report and dashboard
links offline.

Used by .github/workflows/pages.yml and by tests/test_public_site.py, so
the tests check exactly what is deployed.

    python scripts/build_site.py [out_dir]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "docs", out, ignore=shutil.ignore_patterns("*.md"))
    shutil.copytree(ROOT / "dashboard", out / "dashboard")
    shutil.copy2(ROOT / "index.html", out / "home-classic.html")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site"
    print(build(target))

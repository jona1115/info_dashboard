"""Copy site/ to the deploy folder, stamping a version on every code URL.

GitHub Pages lets browsers cache files for 10 minutes, each on its own clock, so right after a deploy a
browser can mix a new index.html with an old app.js (e.g. a new button that does nothing). Adding
?v=<version> to the stylesheet, scripts and every relative JS import makes each deploy a separate set
of URLs: a browser gets either all-old or all-new code, never a mix.

usage: python -m collector.build_site OUT_DIR --version <git sha>
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

IMPORT_RE = re.compile(r"""((?:\bfrom|\bimport)\s*\(?\s*["'])(\.{1,2}/[^"'?#]+\.js)(["'])""")
HTML_RE = re.compile(r"""((?:src|href)=["'])((?:js|vendor)/[^"'?#]+\.js|[^"'?#:/]+\.css)(["'])""")


class BuildError(Exception):
    pass


def stamp_js(text: str, version: str) -> str:
    return IMPORT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}?v={version}{m.group(3)}", text)


def stamp_html(text: str, version: str) -> str:
    return HTML_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}?v={version}{m.group(3)}", text)


def build(out: Path, version: str, src: Path = SITE) -> dict:
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,40}", version or ""):
        raise BuildError(f"bad version {version!r}")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, out, dirs_exist_ok=True)
    counts = {"html": 0, "js": 0}
    for html in out.glob("*.html"):
        t = html.read_text(encoding="utf-8")
        new = stamp_html(t, version)
        counts["html"] += new.count(f"?v={version}")
        html.write_text(new, encoding="utf-8", newline="\n")
    for js in (out / "js").rglob("*.js"):
        t = js.read_text(encoding="utf-8")
        new = stamp_js(t, version)
        counts["js"] += new.count(f"?v={version}")
        # Every relative import must be stamped, or that module could still come from an old cache.
        left = [m.group(1) for m in re.finditer(r"""(?:\bfrom|\bimport)\s*\(?\s*["'](\.{1,2}/[^"']+)["']""", new)
                if f"?v={version}" not in m.group(1)]
        if left:
            raise BuildError(f"{js.relative_to(out)}: unstamped import(s) {left}")
        js.write_text(new, encoding="utf-8", newline="\n")
    if not counts["html"] or not counts["js"]:
        raise BuildError(f"nothing stamped ({counts}): did the file layout change?")
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--version", required=True)
    a = ap.parse_args(argv)
    counts = build(a.out, a.version)
    print(f"site built in {a.out} (version {a.version}; {counts['html']} page refs, {counts['js']} imports stamped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

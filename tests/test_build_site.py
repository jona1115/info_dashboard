import pytest

from collector import build_site


def test_stamps_page_refs_and_every_relative_import(tmp_path):
    counts = build_site.build(tmp_path / "out", "a1b2c3d")
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css?v=a1b2c3d"' in html and 'src="js/app.js?v=a1b2c3d"' in html
    assert 'src="vendor/chart.umd.min.js?v=a1b2c3d"' in html
    assert "data:image/svg+xml" in html                      # the inline icon is left alone
    app = (tmp_path / "out" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'from "./layout.js?v=a1b2c3d"' in app
    assert 'getJSON("manifest.json")' in app                  # data URLs are untouched
    assert counts["js"] >= 20
    assert not (tmp_path / "out" / "js" / "app.js").read_bytes().count(b"\r\n")


def test_stamp_js_forms():
    v = "x1"
    assert build_site.stamp_js('import { a } from "./a.js";', v) == 'import { a } from "./a.js?v=x1";'
    assert build_site.stamp_js("import {a} from '../b/c.js'", v) == "import {a} from '../b/c.js?v=x1'"
    assert build_site.stamp_js('const m = await import("./lazy.js");', v) == 'const m = await import("./lazy.js?v=x1");'
    assert build_site.stamp_js('import x from "https://cdn/x.js"', v) == 'import x from "https://cdn/x.js"'
    assert build_site.stamp_js('fetch("./data.json")', v) == 'fetch("./data.json")'


def test_rejects_bad_version_and_unstamped_imports(tmp_path):
    with pytest.raises(build_site.BuildError, match="bad version"):
        build_site.build(tmp_path / "o1", "a b")
    src = tmp_path / "src"
    (src / "js").mkdir(parents=True)
    (src / "index.html").write_text('<script type="module" src="js/app.js"></script>', encoding="utf-8")
    (src / "js" / "app.js").write_text('import { x } from "./x.mjs";\n', encoding="utf-8")
    with pytest.raises(build_site.BuildError, match="unstamped import"):
        build_site.build(tmp_path / "o2", "v1", src=src)

import csv
import io
import os
import struct
import sys
import zlib

import pytest

from app.agents.fake import FakeAgentAdapter
from app.artifacts import collector, comparator, models
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence
from app.pipelines.engine import PipelineEngine
from tests.conftest import create_project_with_repo

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _png(width, height, fill=b"\x00"):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + fill * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture
def env(app, client, tmp_path):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        root = str(tmp_path / "artifacts")
        app.config["ARTIFACT_DIR"] = root
        engine = PipelineEngine(db, provider, root, lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        yield type("E", (), {"app": app, "client": client, "db": db, "engine": engine,
                             "project_id": project_id, "repo": repo, "root": root})


def _run(env, *elements, name="p"):
    persistence.create_pipeline(env.db, {"name": name, "elements": list(elements)}, env.project_id)
    eid = env.engine.create(name, env.project_id, 1)
    env.engine.run(eid)
    return eid


def test_classification():
    assert collector.classify("shot.PNG") == "screenshot"
    assert collector.classify("trace.zip") == "trace" and collector.classify("data.zip") == "file"
    assert collector.classify("a.diff") == "diff" and collector.classify("run.log") == "log"
    assert collector.classify("report.html") == "report" and collector.classify("clip.webm") == "video"
    assert collector.classify("mystery.bin") == "file"
    assert collector.image_size(_png(7, 5)) == (7, 5) and collector.image_size(b"nope") is None


def test_step_log_is_indexed_and_linked(env):
    eid = _run(env, {"name": "say", "type": "COMMAND", "config": {"command": f'{PY} -c "print(42)"'}})
    items, total = models.search(env.db, env.project_id)
    assert total == 1
    a = items[0]
    step = executions.list_steps(env.db, eid)[0]
    assert (a.kind, a.execution_id, a.step_execution_id, a.step_name) == ("log", eid, step.id, "say")
    with open(collector.file_path(env.root, a)) as fh:
        assert "42" in fh.read()
    assert a.metadata["lines"] == 1


def test_collect_files_from_step_classifies_and_redacts(env):
    shot = _png(4, 3)
    with open(os.path.join(env.repo, "shot.png"), "wb") as fh:
        fh.write(shot)
    with open(os.path.join(env.repo, "notes.log"), "w") as fh:
        fh.write("token=API_KEY=supersecretvalue123\n")
    os.makedirs(os.path.join(env.repo, "out"))
    with open(os.path.join(env.repo, "out", "r.html"), "w") as fh:
        fh.write("<b>x</b>")
    _run(
        env,
        {"name": "make", "type": "COMMAND",
         "config": {"command": f'{PY} -c "pass"', "collect": ["*.png", "*.log", "out/*.html", "../*"]}},
    )
    items, _ = models.search(env.db, env.project_id)
    by_name = {a.name: a for a in items}
    assert by_name["shot.png"].kind == "screenshot" and by_name["shot.png"].metadata["width"] == 4
    assert by_name["r.html"].kind == "report"
    log = by_name["notes.log"]
    assert log.redacted
    with open(collector.file_path(env.root, log)) as fh:
        assert "supersecretvalue123" not in fh.read()
    assert not any(n.startswith("..") for n in by_name)


def test_collect_ignores_symlinks_out_of_repo_and_traversal(env, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, os.path.join(env.repo, "link.txt"))
    eid = _run(env, {"name": "c", "type": "COMMAND", "config": {"command": f'{PY} -c "pass"', "collect": ["link.txt", "/etc/passwd"]}})
    assert not [a for a in models.search(env.db, env.project_id)[0] if a.name in ("link.txt", "passwd")]
    assert executions.get_execution(env.db, eid).status == "COMPLETED"


def test_artifact_capture_step_uses_library(env):
    with open(os.path.join(env.repo, "cov.xml"), "w") as fh:
        fh.write("<c/>")
    eid = _run(env, {"name": "cap", "type": "ARTIFACT_CAPTURE", "config": {"paths": ["cov.xml"]}})
    assert any(a.name == "cov.xml" for a in models.search(env.db, env.project_id)[0])
    eid2 = _run(env, {"name": "cap", "type": "ARTIFACT_CAPTURE", "config": {"paths": ["missing.xml"]}}, name="p2")
    assert executions.get_execution(env.db, eid2).status == "FAILED"


def _seed(env):
    ids = {}
    ids["a"] = collector.store_bytes(env.db, env.root, env.project_id, "one.log", b"a\nb\nc\n", "x", step_name="build", tags=["nightly"])
    ids["b"] = collector.store_bytes(env.db, env.root, env.project_id, "two.log", b"a\nB\nc\nd\n", "x", step_name="test")
    ids["s1"] = collector.store_bytes(env.db, env.root, env.project_id, "s1.png", _png(4, 4), "x", step_name="shot")
    ids["s2"] = collector.store_bytes(env.db, env.root, env.project_id, "s2.png", _png(4, 4, b"\xff"), "x", step_name="shot")
    ids["s3"] = collector.store_bytes(env.db, env.root, env.project_id, "s3.png", _png(4, 4), "x", step_name="shot")
    return ids


def test_search_and_filters(env):
    ids = _seed(env)
    db, pid = env.db, env.project_id
    assert models.search(db, pid, q="two")[1] == 1
    assert models.search(db, pid, kinds=["screenshot"])[1] == 3
    assert models.search(db, pid, step_name="build")[1] == 1
    assert models.search(db, pid, tag="nightly")[0][0].id == ids["a"]
    assert models.search(db, pid, q="nightly")[1] == 1  # tags are searchable
    assert models.search(db, pid, q="100%")[1] == 0  # LIKE wildcards are escaped
    assert models.search(db, pid, since="2999-01-01")[1] == 0
    assert models.search(db, pid, until="2999-01-01")[1] == 5
    assert models.step_names(db, pid) == ["build", "shot", "test"]
    assert models.search(db, pid + 99)[1] == 0


def test_comparisons(env):
    ids = _seed(env)
    a, b = (models.get_artifact(env.db, ids[k]) for k in ("a", "b"))
    kind, result = comparator.compare(env.root, a, b)
    assert kind == "text_diff" and result["added"] == 2 and result["removed"] == 1 and not result["identical"]
    s1, s2, s3 = (models.get_artifact(env.db, ids[k]) for k in ("s1", "s2", "s3"))
    _, differ = comparator.compare(env.root, s1, s2)
    _, same = comparator.compare(env.root, s1, s3)
    assert not differ["identical"] and differ["same_dimensions"] and same["identical"]
    with pytest.raises(comparator.ComparisonError):
        comparator.compare(env.root, a, s1)
    with pytest.raises(comparator.ComparisonError):
        comparator.compare(env.root, a, a)


def test_library_pages(env):
    ids = _seed(env)
    base = f"/projects/{env.project_id}/artifacts"
    page = env.client.get(base).get_data(as_text=True)
    assert "Artifact library" in page and "one.log" in page and "s1.png" in page
    assert "one.log" not in env.client.get(f"{base}?kind=screenshot").get_data(as_text=True)
    assert "two.log" in env.client.get(f"{base}?q=two").get_data(as_text=True)
    results = env.client.get(f"{base}/search?q=nightly").get_json()
    assert results["total"] == 1 and results["results"][0]["name"] == "one.log"

    detail = env.client.get(f"{base}/{ids['a']}").get_data(as_text=True)
    assert "one.log" in detail and "#nightly" in detail
    assert env.client.get(f"{base}/{ids['s1']}").status_code == 200
    preview = env.client.get(f"{base}/{ids['s1']}/preview")
    assert preview.status_code == 200 and preview.mimetype == "image/png"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    text = env.client.get(f"{base}/{ids['a']}/preview")
    assert text.mimetype == "text/plain" and b"a\nb" in text.data
    dl = env.client.get(f"{base}/{ids['a']}/download")
    assert dl.status_code == 200 and "attachment" in dl.headers["Content-Disposition"]


def test_svg_and_html_are_never_previewed_inline(env):
    svg = collector.store_bytes(env.db, env.root, env.project_id, "x.svg", b"<svg onload=alert(1)/>", "x", kind="file")
    html = collector.store_bytes(env.db, env.root, env.project_id, "x.html", b"<script>alert(1)</script>", "x")
    base = f"/projects/{env.project_id}/artifacts"
    assert env.client.get(f"{base}/{svg}/preview").status_code == 415
    page = env.client.get(f"{base}/{html}/preview")
    assert page.mimetype == "text/plain" and "default-src 'none'" in page.headers["Content-Security-Policy"]


def test_tags_and_compare_over_http(env):
    ids = _seed(env)
    base = f"/projects/{env.project_id}/artifacts"
    assert env.client.post(f"{base}/{ids['a']}/tags", data={"tags": "Smoke, ui"}, headers=AJAX).status_code == 200
    assert models.get_artifact(env.db, ids["a"]).tags == ["nightly", "smoke", "ui"]
    env.client.post(f"{base}/{ids['a']}/tags", data={"remove": "ui"}, headers=AJAX)
    assert "ui" not in models.get_artifact(env.db, ids["a"]).tags
    assert env.client.post(f"{base}/{ids['a']}/tags", data={}, headers=AJAX).status_code == 400

    resp = env.client.post(f"{base}/compare", data={"a": ids["a"], "b": ids["b"]}, headers=AJAX)
    assert resp.status_code == 200
    page = env.client.get(resp.get_json()["redirect"]).get_data(as_text=True)
    assert "1 line(s) removed" in page or "removed" in page
    mismatch = env.client.post(f"{base}/compare", data={"a": ids["a"], "b": ids["s1"]}, headers=AJAX)
    assert mismatch.status_code == 422
    assert env.client.post(f"{base}/compare", data={"a": ids["a"]}, headers=AJAX).status_code == 400
    imgs = env.client.post(f"{base}/compare", data={"a": ids["s1"], "b": ids["s2"]}, headers=AJAX)
    assert "differ" in env.client.get(imgs.get_json()["redirect"]).get_data(as_text=True)


def test_csv_export_neutralises_formulas(env):
    collector.store_bytes(env.db, env.root, env.project_id, "cmd.log", b"x", "x", step_name="@evil")
    rows = list(csv.reader(io.StringIO(env.client.get(f"/projects/{env.project_id}/artifacts/export.csv").get_data(as_text=True))))
    assert rows[0][:3] == ["id", "kind", "name"]
    assert rows[1][2] == "cmd.log" and rows[1][4] == "'@evil"


def test_artifacts_scoped_to_project(env):
    ids = _seed(env)
    other = int(env.client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    base = f"/projects/{other}/artifacts"
    for suffix in (f"/{ids['a']}", f"/{ids['a']}/preview", f"/{ids['a']}/download"):
        assert env.client.get(base + suffix).status_code == 404
    assert env.client.post(f"{base}/compare", data={"a": ids["a"], "b": ids["b"]}, headers=AJAX).status_code == 404
    assert env.client.get(base).get_data(as_text=True).count("one.log") == 0


def test_path_traversal_in_stored_name_is_neutralised(env):
    aid = collector.store_bytes(env.db, env.root, env.project_id, "../../evil.txt", b"x", "dir")
    a = models.get_artifact(env.db, aid)
    assert ".." not in a.path and collector.file_path(env.root, a).startswith(os.path.realpath(env.root))

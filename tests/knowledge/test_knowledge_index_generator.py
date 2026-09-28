import json
import os

import pytest

from app.db import get_db
from app.knowledge import index_generator, models


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_generate_writes_one_file_per_group(db, tmp_path):
    models.create_entry(
        db, "note", "Py testing", "c", slug="py-testing", confidence="reviewed",
        language="python", library="pytest", version="8.x", topic="testing",
    )
    models.create_entry(
        db, "note", "Py fixtures", "c", slug="py-fixtures", confidence="reviewed",
        language="python", library="pytest", version="8.x", topic="fixtures",
    )
    models.create_entry(
        db, "note", "JS routing", "c", slug="js-routing", confidence="reviewed",
        language="js", library="express", version="4.x", topic="routing",
    )
    # Unreviewed and slugless entries are excluded from the index.
    models.create_entry(db, "note", "Draft", "c", slug="draft-entry", confidence="unverified")
    models.create_entry(db, "note", "No slug", "c", confidence="reviewed")

    written = index_generator.generate(db, str(tmp_path))
    assert len(written) == 2

    py_file = [p for p in written if "python" in p][0]
    payload = json.loads(open(py_file, encoding="utf-8").read())
    assert payload["entry_count"] == 2
    assert payload["language"] == "python"
    assert {e["slug"] for e in payload["entries"]} == {"py-testing", "py-fixtures"}
    assert payload["last_updated"] is not None


def test_generate_is_idempotent_and_overwrites(db, tmp_path):
    models.create_entry(db, "note", "A", "c", slug="a", confidence="reviewed", language="python")
    first = index_generator.generate(db, str(tmp_path))
    second = index_generator.generate(db, str(tmp_path))
    assert first == second
    assert os.path.exists(first[0])

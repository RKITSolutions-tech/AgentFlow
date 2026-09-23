import pytest

from app import create_app
from app.config import Config


@pytest.fixture
def app(tmp_path):
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()

    config = Config(
        DATABASE_PATH=str(tmp_path / "test.sqlite3"),
        SECRET_KEY="test-secret",
        ALLOWED_PROJECT_ROOTS=(str(allowed_root),),
        TESTING=True,
    )
    application = create_app(config)
    application.config["allowed_root"] = str(allowed_root)
    return application


@pytest.fixture
def client(app):
    return app.test_client()

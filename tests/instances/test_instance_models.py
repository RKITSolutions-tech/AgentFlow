from app.db import get_db
from app.instances import models as instance_models


def test_add_and_list_instance(app):
    with app.app_context():
        db = get_db()
        instance_id = instance_models.add_instance(db, "windows-server", "http://windows-server:5000/", "secret-token")

        instances = instance_models.list_instances(db)
        assert len(instances) == 1
        instance = instances[0]
        assert instance.id == instance_id
        assert instance.name == "windows-server"
        # Trailing slash is stripped so client.py can safely append paths.
        assert instance.base_url == "http://windows-server:5000"
        assert instance.token == "secret-token"
        assert instance.last_status == "UNKNOWN"


def test_get_missing_instance_returns_none(app):
    with app.app_context():
        db = get_db()
        assert instance_models.get_instance(db, 999) is None


def test_delete_instance(app):
    with app.app_context():
        db = get_db()
        instance_id = instance_models.add_instance(db, "a", "http://a:5000", "tok")
        instance_models.delete_instance(db, instance_id)
        assert instance_models.get_instance(db, instance_id) is None


def test_set_status_updates_last_seen(app):
    with app.app_context():
        db = get_db()
        instance_id = instance_models.add_instance(db, "a", "http://a:5000", "tok")
        instance_models.set_status(db, instance_id, "ONLINE")
        instance = instance_models.get_instance(db, instance_id)
        assert instance.last_status == "ONLINE"
        assert instance.last_seen_at is not None

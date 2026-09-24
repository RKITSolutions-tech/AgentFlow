import json
import pytest

from app.agents.models import add_agent_event, create_agent_session
from app.db import get_db
from app.documents.models import (
    create_document,
    create_document_ref,
    get_document,
    get_document_refs,
    get_referencing_documents,
    list_documents,
    supersede_document,
    update_document_status,
)
from app.projects.models import add_repository, create_project


def test_create_document(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        doc_id = create_document(
            db,
            project_id,
            repo_id,
            "DESIGN",
            "/docs/design.md",
            "Design Document",
            chain_id="chain-1",
            current_git_sha="abc123",
        )

        assert doc_id is not None
        doc = get_document(db, doc_id)
        assert doc is not None
        assert doc.doc_type == "DESIGN"
        assert doc.path == "/docs/design.md"
        assert doc.title == "Design Document"
        assert doc.status == "DRAFT"
        assert doc.chain_id == "chain-1"
        assert doc.current_git_sha == "abc123"


def test_document_status_transitions(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )
        doc_id = create_document(
            db, project_id, repo_id, "IMPLEMENTATION", "/docs/impl.md", "Implementation"
        )

        # Test DRAFT -> IN_REVIEW
        update_document_status(db, doc_id, "IN_REVIEW")
        doc = get_document(db, doc_id)
        assert doc.status == "IN_REVIEW"

        # Test IN_REVIEW -> APPROVED
        update_document_status(db, doc_id, "APPROVED")
        doc = get_document(db, doc_id)
        assert doc.status == "APPROVED"


def test_supersede_document(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        doc1_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/v1.md", "Design v1"
        )
        doc2_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/v2.md", "Design v2"
        )

        # Supersede doc1 with doc2
        supersede_document(db, doc1_id, doc2_id)

        doc1 = get_document(db, doc1_id)
        assert doc1.status == "SUPERSEDED"
        assert doc1.supersedes_id == doc2_id


def test_document_refs_creation(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        doc1_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design.md", "Design"
        )
        doc2_id = create_document(
            db, project_id, repo_id, "IMPLEMENTATION", "/docs/impl.md", "Implementation"
        )

        create_document_ref(db, doc2_id, doc1_id, "DERIVES_FROM")

        refs = get_document_refs(db, doc2_id)
        assert len(refs) == 1
        assert refs[0].document_id == doc2_id
        assert refs[0].referenced_document_id == doc1_id
        assert refs[0].relationship == "DERIVES_FROM"


def test_document_refs_reverse_lookup(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        doc1_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design.md", "Design"
        )
        doc2_id = create_document(
            db, project_id, repo_id, "IMPLEMENTATION", "/docs/impl.md", "Implementation"
        )
        doc3_id = create_document(
            db, project_id, repo_id, "TESTING", "/docs/test.md", "Testing"
        )

        create_document_ref(db, doc2_id, doc1_id, "DERIVES_FROM")
        create_document_ref(db, doc3_id, doc1_id, "REFERENCES")

        referring_docs = get_referencing_documents(db, doc1_id)
        assert len(referring_docs) == 2
        doc_ids = {ref.document_id for ref in referring_docs}
        assert doc2_id in doc_ids
        assert doc3_id in doc_ids


def test_list_documents_filtering(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        doc1_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design.md", "Design"
        )
        doc2_id = create_document(
            db, project_id, repo_id, "IMPLEMENTATION", "/docs/impl.md", "Implementation"
        )
        doc3_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design2.md", "Design 2"
        )

        update_document_status(db, doc1_id, "APPROVED")
        update_document_status(db, doc2_id, "DRAFT")

        # Filter by project_id
        docs = list_documents(db, project_id=project_id)
        assert len(docs) == 3

        # Filter by status
        approved_docs = list_documents(db, project_id=project_id, status="APPROVED")
        assert len(approved_docs) == 1
        assert approved_docs[0].id == doc1_id

        draft_docs = list_documents(db, project_id=project_id, status="DRAFT")
        assert len(draft_docs) == 2
        ids = {doc.id for doc in draft_docs}
        assert doc2_id in ids
        assert doc3_id in ids


def test_agent_session_with_document(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )
        doc_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design.md", "Design"
        )

        session_id = create_agent_session(db, project_id, "claude")
        db.execute(
            "UPDATE agent_sessions SET document_id = ?, reviewed_git_sha = ? WHERE id = ?",
            (doc_id, "def456", session_id),
        )
        db.commit()

        session_row = db.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        assert session_row["document_id"] == doc_id
        assert session_row["reviewed_git_sha"] == "def456"


def test_review_events_on_agent_session(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        session_id = create_agent_session(db, project_id, "claude")

        # Add review_comment event
        comment_data = json.dumps(
            {"line_start": 10, "line_end": 15, "body": "Fix this", "severity": "HIGH"}
        )
        event1_id = add_agent_event(db, session_id, "review_comment", comment_data)

        # Add review_verdict event
        verdict_data = json.dumps({"verdict": "APPROVED"})
        event2_id = add_agent_event(db, session_id, "review_verdict", verdict_data)

        # Add doc_read event
        doc_read_data = json.dumps({"document_id": 42, "git_sha": "xyz789"})
        event3_id = add_agent_event(db, session_id, "doc_read", doc_read_data)

        events = db.execute(
            "SELECT * FROM agent_events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        assert len(events) == 3
        assert events[0]["event_type"] == "review_comment"
        assert json.loads(events[0]["data"])["line_start"] == 10
        assert events[1]["event_type"] == "review_verdict"
        assert json.loads(events[1]["data"])["verdict"] == "APPROVED"
        assert events[2]["event_type"] == "doc_read"
        assert json.loads(events[2]["data"])["document_id"] == 42


def test_foreign_key_cascade_on_project_delete(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )
        doc_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design.md", "Design"
        )

        # Delete project should cascade to documents
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()

        doc = get_document(db, doc_id)
        assert doc is None


def test_foreign_key_cascade_on_document_delete(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )
        doc1_id = create_document(
            db, project_id, repo_id, "DESIGN", "/docs/design.md", "Design"
        )
        doc2_id = create_document(
            db, project_id, repo_id, "IMPLEMENTATION", "/docs/impl.md", "Implementation"
        )

        # Create reference
        create_document_ref(db, doc2_id, doc1_id, "DERIVES_FROM")

        # Delete doc1 should cascade to document_refs
        db.execute("DELETE FROM documents WHERE id = ?", (doc1_id,))
        db.commit()

        refs = get_document_refs(db, doc2_id)
        assert len(refs) == 0


def test_document_nullable_fields(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        # Create document without optional fields
        doc_id = create_document(
            db, project_id, repo_id, "PLAN", "/docs/plan.md", "Plan"
        )

        doc = get_document(db, doc_id)
        assert doc.chain_id is None
        assert doc.current_git_sha is None
        assert doc.supersedes_id is None


def test_document_type_validation(app):
    with app.app_context():
        db = get_db()

        project_id = create_project(db, "Test Project")
        repo_id = add_repository(
            db, project_id, "Main Repo", "/home/test", ("/home",), is_primary=True
        )

        # Valid doc_type values should work
        valid_types = ["DESIGN", "IMPLEMENTATION", "TESTING", "PLAN", "STANDARD"]
        for doc_type in valid_types:
            doc_id = create_document(
                db, project_id, repo_id, doc_type, f"/docs/{doc_type}.md", f"{doc_type} Doc"
            )
            doc = get_document(db, doc_id)
            assert doc.doc_type == doc_type

        # Invalid doc_type should fail
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO documents (project_id, repo_id, doc_type, path, title) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, repo_id, "INVALID", "/docs/invalid.md", "Invalid"),
            )
            db.commit()

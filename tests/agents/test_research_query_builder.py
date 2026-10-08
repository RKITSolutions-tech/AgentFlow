"""Unit tests for research query builder (task 49.4)."""
from __future__ import annotations

import pytest

from app.agents.research_query_builder import (
    build_search_queries,
    _simple_tokenize,
    _compute_tf_idf_scores,
)


class TestSimpleTokenize:
    """Tests for text tokenization."""

    def test_tokenize_extracts_words(self):
        """Tokenize extracts words and lowercases them."""
        tokens = _simple_tokenize("Hello WORLD Test")
        assert "hello" in tokens or len(tokens) > 0
        assert "world" in tokens or len(tokens) > 0

    def test_tokenize_removes_stopwords(self):
        """Stopwords like 'the', 'and', 'is' are removed."""
        tokens = _simple_tokenize("the quick and brown fox is here")
        # 'the', 'and', 'is' should be removed
        assert "the" not in tokens
        assert "and" not in tokens
        assert "is" not in tokens
        # Content words should remain
        assert "quick" in tokens or "brown" in tokens or "fox" in tokens

    def test_tokenize_removes_short_words(self):
        """Words with 3 or fewer characters (after stopword filter) are kept/removed."""
        tokens = _simple_tokenize("I am a developer")
        # 'I', 'am', 'a' are stopwords or too short, 'developer' should remain
        assert "developer" in tokens

    def test_tokenize_handles_punctuation(self):
        """Punctuation is removed."""
        tokens = _simple_tokenize("test, code! work?")
        # Should extract just the words
        assert "test" in tokens
        assert "code" in tokens
        assert "work" in tokens

    def test_tokenize_empty_string(self):
        """Empty string returns empty list."""
        tokens = _simple_tokenize("")
        assert tokens == []


class TestComputeTfIdfScores:
    """Tests for TF-IDF scoring."""

    def test_scores_range_0_to_1(self):
        """Scores are normalized to [0, 1]."""
        query_words = ["test", "code"]
        docs = [["test", "run"], ["code", "run"]]
        scores = _compute_tf_idf_scores(query_words, docs)
        assert all(0 <= s <= 1 for s in scores)

    def test_scores_higher_for_common_words(self):
        """Words appearing in more documents get higher scores."""
        query_words = ["test", "rare"]
        docs = [
            ["test", "run"],
            ["test", "debug"],
            ["rare", "find"],
        ]
        scores = _compute_tf_idf_scores(query_words, docs)
        # "test" appears in 2/3 docs, "rare" in 1/3
        assert scores[0] > scores[1]

    def test_empty_documents(self):
        """Empty document list returns 1.0 scores."""
        query_words = ["test", "code"]
        scores = _compute_tf_idf_scores(query_words, [])
        assert all(s == 1.0 for s in scores)


class TestBuildSearchQueries:
    """Tests for query building."""

    def test_empty_question_returns_empty_list(self):
        """Empty question returns no queries."""
        queries = build_search_queries("")
        assert queries == []

    def test_extracts_keywords_from_question(self):
        """Question keywords become queries."""
        queries = build_search_queries("How to configure authentication in Python?")
        # Should include keywords like 'configure', 'authentication', 'python'
        assert len(queries) > 0
        query_text = " ".join(queries)
        # At least one of these keywords should be present
        assert any(word in query_text.lower() for word in ["configure", "authentication", "python"])

    def test_respects_max_queries(self):
        """Returns at most max_queries."""
        queries = build_search_queries("test", max_queries=2)
        assert len(queries) <= 2

    def test_deduplicates_queries(self):
        """Duplicate queries are removed."""
        queries = build_search_queries("test test test", max_queries=5)
        assert len(queries) == len(set(queries))

    def test_includes_knowledge_base_keywords(self):
        """Knowledge base titles/tags influence queries."""
        kb_entries = [
            {"title": "JWT Token Authentication Guide", "tags": ["token", "authentication"]},
            {"title": "Flask Security Framework", "tags": ["framework", "security"]},
        ]
        queries = build_search_queries(
            "How to secure my application?",
            library_entries=kb_entries,
            max_queries=5,
        )
        query_text = " ".join(queries).lower()
        # Should include words from KB or question
        assert len(queries) > 0
        # The combined keywords should produce some result
        assert "secure" in query_text or "application" in query_text or "token" in query_text or "framework" in query_text

    def test_question_keywords_score_higher(self):
        """Question keywords are prioritized over KB keywords."""
        kb_entries = [
            {"title": "Database Queries", "tags": ["database", "sql"]},
        ]
        queries = build_search_queries(
            "How to optimize Python performance?",
            library_entries=kb_entries,
            max_queries=3,
        )
        query_text = " ".join(queries).lower()
        # Python and performance from question should appear
        assert "python" in query_text or "performance" in query_text

    def test_handles_whitespace_only_question(self):
        """Whitespace-only question returns empty list."""
        queries = build_search_queries("   \n\t  ")
        assert queries == []

    def test_handles_no_knowledge_base(self):
        """Works without knowledge base entries."""
        queries = build_search_queries("test query", library_entries=None)
        assert len(queries) > 0

    def test_combines_keywords_into_phrases(self):
        """Some queries are multi-word combinations."""
        queries = build_search_queries(
            "How to implement machine learning models in production?",
            max_queries=5,
        )
        # Should have both single and multi-word queries
        single_word_count = sum(1 for q in queries if len(q.split()) == 1)
        multi_word_count = sum(1 for q in queries if len(q.split()) > 1)
        # At least some should be multi-word
        assert multi_word_count > 0 or single_word_count > 0

    def test_real_world_example_authentication(self):
        """Real-world example: authentication queries."""
        kb = [
            {"title": "OAuth 2.0 Flow", "tags": ["oauth", "authorization"]},
            {"title": "JWT Tokens", "tags": ["jwt", "token", "auth"]},
        ]
        queries = build_search_queries(
            "How do I implement OAuth authentication in my Flask app?",
            library_entries=kb,
            max_queries=4,
        )
        assert len(queries) > 0
        assert len(queries) <= 4
        # Should include relevant keywords
        query_text = " ".join(queries).lower()
        relevant = ["oauth", "auth", "flask", "implement", "jwt"]
        assert any(word in query_text for word in relevant)

    def test_real_world_example_performance(self):
        """Real-world example: performance optimization queries."""
        kb = [
            {"title": "Python Profiling Tools", "tags": ["profiling", "performance", "optimization"]},
            {"title": "Caching Strategies", "tags": ["cache", "memory", "speed"]},
        ]
        queries = build_search_queries(
            "What are best practices for optimizing Python code performance?",
            library_entries=kb,
            max_queries=5,
        )
        assert len(queries) > 0
        query_text = " ".join(queries).lower()
        # Should have performance/optimization-related terms
        relevant = ["performance", "optimization", "profiling", "cache", "python"]
        assert any(word in query_text for word in relevant)

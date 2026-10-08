"""Query builder for research sessions (task 49.4).

Extracts keywords from questions and knowledge base entries, combines them
into search queries, and ranks by relevance for web research.
"""
from __future__ import annotations

import re
from typing import Any
from collections import Counter


def _simple_tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words, removing stopwords and punctuation.

    Simple implementation using regex and a stopword list.
    """
    # Common English stopwords to skip
    stopwords = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "up", "about", "into", "through", "is",
        "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "should", "could", "can", "may",
        "might", "must", "i", "you", "he", "she", "it", "we", "they",
    }

    # Extract words (alphanumeric + underscores)
    words = re.findall(r"\b[a-z0-9_]+\b", text.lower())
    # Filter out stopwords and short words
    return [w for w in words if w not in stopwords and len(w) > 2]


def _compute_tf_idf_scores(
    query_words: list[str], doc_words: list[list[str]]
) -> list[float]:
    """Compute TF-IDF-like scores for query words against documents.

    Simple heuristic: higher score if query word appears in more documents.
    Returns scores in [0, 1] per query word.
    """
    if not query_words or not doc_words:
        return [1.0] * len(query_words)

    scores = []
    for word in query_words:
        # Count how many documents contain this word
        doc_count = sum(1 for doc in doc_words if word in doc)
        # Score: 1.0 if in all docs, 0 if in none, linear interpolation
        score = doc_count / len(doc_words) if doc_words else 1.0
        scores.append(score)

    return scores


def build_search_queries(
    question: str,
    library_entries: list[dict[str, Any]] | None = None,
    max_queries: int = 5,
) -> list[str]:
    """Build search queries from a research question and knowledge base.

    Combines keywords from the question with titles/tags from knowledge base
    entries, deduplicates, and ranks by relevance.

    Args:
        question: Research question to build queries from
        library_entries: Knowledge base entries with 'title' and 'tags' fields
        max_queries: Maximum number of queries to return

    Returns:
        List of search queries, ranked by relevance (at most max_queries)
    """
    library_entries = library_entries or []

    if not question.strip():
        return []

    # Extract keywords from the question
    question_words = _simple_tokenize(question)
    if not question_words:
        # Fallback: just use first words of question as-is
        words = question.split()[:3]
        question_words = [w for w in words if len(w) > 2]

    # Extract keywords from knowledge base entry titles
    kb_title_words: list[str] = []
    kb_doc_words: list[list[str]] = []
    for entry in library_entries:
        title = entry.get("title", "")
        tags = entry.get("tags", []) or []

        if title:
            title_words = _simple_tokenize(title)
            kb_title_words.extend(title_words)
            kb_doc_words.append(title_words)

        if tags:
            for tag in tags:
                tag_words = _simple_tokenize(str(tag))
                kb_title_words.extend(tag_words)
                kb_doc_words.append(tag_words)

    # Combine and deduplicate keywords
    all_keywords = list(set(question_words + kb_title_words))
    if not all_keywords:
        return []

    # Score keywords by relevance (question words score higher)
    keyword_scores = {}
    for word in all_keywords:
        # Higher if in question (2x weight) and in KB (1x weight)
        score = 0.0
        if word in question_words:
            score += 2.0
        if word in kb_title_words:
            score += 1.0
        keyword_scores[word] = score

    # Sort by score, highest first
    sorted_keywords = sorted(all_keywords, key=lambda w: keyword_scores[w], reverse=True)

    # Build queries by combining top keywords
    queries = []
    seen = set()

    # Strategy 1: Single keywords from top keywords
    for keyword in sorted_keywords[:min(3, max_queries)]:
        if keyword not in seen:
            queries.append(keyword)
            seen.add(keyword)

    # Strategy 2: Two-word combinations from top keywords
    for i in range(len(sorted_keywords) - 1):
        if len(queries) >= max_queries:
            break
        for j in range(i + 1, min(i + 3, len(sorted_keywords))):
            if len(queries) >= max_queries:
                break
            query = f"{sorted_keywords[i]} {sorted_keywords[j]}"
            if query not in seen:
                queries.append(query)
                seen.add(query)

    return queries[:max_queries]

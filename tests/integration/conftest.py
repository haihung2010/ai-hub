"""Local conftest for RAG-scrape integration tests.

We intentionally do NOT import the FastAPI app here, because the project's
top-level ``tests/conftest.py`` pulls in ``app.main`` → ``tracing_service``
(unrelated to the RAG scrape). Our test only needs the database, the
embedding service, and the retrieval service — all of which import cleanly
without the app.

The pytest auto-discovery still picks up the project root conftest's
``isolated_db`` fixture, so we mark every test with ``no_isolated_db``
to opt out of the truncate-before-test behavior (we need the live
populated knowledge base).
"""
# Intentionally minimal.

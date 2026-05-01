from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_knowledge_routes_require_api_key(client: TestClient) -> None:
    client.headers.clear()

    response = client.get("/v1/knowledge/cards", params={"project_id": "chatbot"})

    assert response.status_code == 401


@pytest.mark.integration
def test_create_list_and_search_knowledge_card(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge/cards",
        json={
            "tenant_id": "default",
            "project_id": "chatbot",
            "knowledge_domain": "customer_faq",
            "title": "Refund FAQ",
            "summary": "Refund rules",
            "content": "Customers can request refund within thirty days with order code.",
            "tags": ["refund", "orders"],
        },
    )

    assert create_response.status_code == 200
    card = create_response.json()["card"]
    assert card["project_id"] == "chatbot"

    list_response = client.get(
        "/v1/knowledge/cards",
        params={"tenant_id": "default", "project_id": "chatbot"},
    )
    assert list_response.status_code == 200
    assert list_response.json()["cards"][0]["title"] == "Refund FAQ"

    search_response = client.post(
        "/v1/knowledge/search",
        json={"tenant_id": "default", "project_id": "chatbot", "query": "refund order code"},
    )
    assert search_response.status_code == 200
    results = search_response.json()["results"]
    assert results[0]["title"] == "Refund FAQ"


@pytest.mark.integration
def test_knowledge_search_does_not_cross_project(client: TestClient) -> None:
    client.post(
        "/v1/knowledge/cards",
        json={
            "project_id": "chatbot",
            "knowledge_domain": "customer_faq",
            "title": "Chatbot Refund FAQ",
            "content": "Refund requires chatbot project approval.",
        },
    )

    response = client.post(
        "/v1/knowledge/search",
        json={"project_id": "other", "query": "refund chatbot"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []

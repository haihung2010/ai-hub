"""Unit tests for tenant-isolation guards in route layer.

Audit 2026-06-14 (security-reviewer agent):
- CRITICAL: ``app/routes/orders.py`` accepts client-supplied ``tenant_id``
  and ``user_id`` as query params. Tenant-bound API keys can read/write
  any tenant's orders by passing ``?tenant_id=other_tenant``. Fix: enforce
  ``request.state.api_key_tenant_id == tenant_id`` (mirror ``chat.py:45-57``).
- HIGH: ``app/routes/mcp_tools.py::search_knowledge`` hardcodes
  ``tenant_id="default"`` in the retrieval call, so any tenant-bound key
  ends up searching the "default" namespace (potential cross-tenant
  exfiltration of cards belonging to the default tenant).

The fix pattern matches ``chat.py``:
- If ``api_key_tenant_id`` is set on ``request.state`` (tenant-bound key),
  reject requests whose query/body ``tenant_id`` does not match.
- If ``api_key_tenant_id`` is ``None`` (master key), pass through by
  design — master keys have legitimate cross-tenant access for ops.

Each test below targets a single guard and is independent of the DB.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# All tests in this file are pure unit tests — they mock the OrdersService
# and the FastAPI Request, so the database is not touched. The
# ``isolated_db`` autouse fixture in conftest would refuse to run
# without AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1; the ``no_isolated_db``
# marker tells pytest to skip that fixture.
pytestmark = pytest.mark.no_isolated_db

from app.services.orders_service import Order, OrdersService


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────

def _mock_request(api_key_tenant_id: str | None) -> MagicMock:
    """Build a minimal mock Request with ``api_key_tenant_id`` on state.

    This is what the SecurityMiddleware (``app/middleware/security.py:300-335``)
    sets on every authenticated request, after looking up the API key row.
    """
    req = MagicMock()
    req.state.api_key_tenant_id = api_key_tenant_id
    return req


def _mock_svc() -> MagicMock:
    """Mock OrdersService that records calls but returns nothing by default.

    We never want cross-tenant requests to reach the service layer.
    """
    return MagicMock(spec=OrdersService)


def _order(**overrides) -> Order:
    """Build a minimal Order dataclass for return values."""
    base = dict(
        id="ord-1", tenant_id="acme", user_id="u1", order_code="ORD-1",
        product_name="Áo thun", size=None, color=None, price=None,
        purchase_date="2026-06-14", status="active",
    )
    base.update(overrides)
    return Order(**base)


# ──────────────────────────────────────────────────────────────────────
# orders.py — create_order
# ──────────────────────────────────────────────────────────────────────

class TestCreateOrderTenantGuard:
    def test_rejects_when_api_key_tenant_mismatches(self):
        from app.routes.orders import create_order
        req = _mock_request(api_key_tenant_id="acme")
        svc = _mock_svc()
        with pytest.raises(HTTPException) as exc:
            create_order(
                request=req,
                tenant_id="globex",  # mismatch with api key
                user_id="u1",
                order_code="ORD-1",
                product_name="Áo",
                svc=svc,
            )
        assert exc.value.status_code == 403
        assert "tenant" in exc.value.detail.lower()
        svc.create_order.assert_not_called()

    def test_allows_when_api_key_tenant_matches(self):
        from app.routes.orders import create_order
        req = _mock_request(api_key_tenant_id="acme")
        svc = _mock_svc()
        svc.create_order.return_value = _order(order_code="ORD-1")
        result = create_order(
            request=req,
            tenant_id="acme",
            user_id="u1",
            order_code="ORD-1",
            product_name="Áo",
            svc=svc,
        )
        assert result["order_code"] == "ORD-1"
        svc.create_order.assert_called_once()

    def test_allows_master_key_with_any_tenant(self):
        from app.routes.orders import create_order
        req = _mock_request(api_key_tenant_id=None)  # master key, by design cross-tenant
        svc = _mock_svc()
        svc.create_order.return_value = _order(order_code="ORD-2", tenant_id="any")
        result = create_order(
            request=req,
            tenant_id="any",
            user_id="u2",
            order_code="ORD-2",
            product_name="Quần",
            svc=svc,
        )
        assert result["order_code"] == "ORD-2"
        svc.create_order.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# orders.py — get_order
# ──────────────────────────────────────────────────────────────────────

class TestGetOrderTenantGuard:
    def test_rejects_when_api_key_tenant_mismatches(self):
        from app.routes.orders import get_order
        req = _mock_request(api_key_tenant_id="acme")
        svc = _mock_svc()
        with pytest.raises(HTTPException) as exc:
            get_order(
                request=req,
                order_code="ORD-X",
                tenant_id="globex",
                svc=svc,
            )
        assert exc.value.status_code == 403
        svc.get_by_code.assert_not_called()

    def test_allows_when_api_key_tenant_matches_and_order_exists(self):
        from app.routes.orders import get_order
        req = _mock_request(api_key_tenant_id="acme")
        svc = _mock_svc()
        svc.get_by_code.return_value = _order(order_code="ORD-3")
        result = get_order(
            request=req,
            order_code="ORD-3",
            tenant_id="acme",
            svc=svc,
        )
        assert result["order_code"] == "ORD-3"
        svc.get_by_code.assert_called_once_with("acme", "ORD-3")

    def test_returns_404_when_order_not_found(self):
        from app.routes.orders import get_order
        req = _mock_request(api_key_tenant_id="acme")
        svc = _mock_svc()
        svc.get_by_code.return_value = None
        with pytest.raises(HTTPException) as exc:
            get_order(
                request=req,
                order_code="MISSING",
                tenant_id="acme",
                svc=svc,
            )
        assert exc.value.status_code == 404


# ──────────────────────────────────────────────────────────────────────
# orders.py — request_return
# ──────────────────────────────────────────────────────────────────────

class TestRequestReturnTenantGuard:
    def test_rejects_when_api_key_tenant_mismatches(self):
        from app.routes.orders import request_return
        req = _mock_request(api_key_tenant_id="acme")
        svc = _mock_svc()
        with pytest.raises(HTTPException) as exc:
            request_return(
                request=req,
                order_code="ORD-Y",
                tenant_id="globex",
                reason="defective",
                svc=svc,
            )
        assert exc.value.status_code == 403
        svc.get_by_code.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# mcp_tools.py — knowledge-search
# ──────────────────────────────────────────────────────────────────────

class TestKnowledgeSearchTenantGuard:
    @pytest.mark.asyncio
    async def test_uses_api_key_tenant_when_bound(self):
        """Tenant-bound key → retrieval.search must receive the bound tenant_id,
        NOT the hardcoded "default".
        """
        from app.routes.mcp_tools import search_knowledge

        req = _mock_request(api_key_tenant_id="acme")

        with patch(
            "app.services.knowledge_retrieval_service.KnowledgeRetrievalService"
        ) as mock_ret_class:
            mock_ret = MagicMock()
            mock_ret.search.return_value = []
            mock_ret_class.return_value = mock_ret

            result = await search_knowledge(
                request=req,
                query="refund",
                project_id="chatbot",
                limit=4,
            )

        # The whole point: must be "acme", not "default".
        assert mock_ret.search.call_args.kwargs["tenant_id"] == "acme"
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_uses_default_tenant_for_master_key(self):
        """Master key (api_key_tenant_id is None) → retrieval.search falls back to "default"."""
        from app.routes.mcp_tools import search_knowledge

        req = _mock_request(api_key_tenant_id=None)

        with patch(
            "app.services.knowledge_retrieval_service.KnowledgeRetrievalService"
        ) as mock_ret_class:
            mock_ret = MagicMock()
            mock_ret.search.return_value = []
            mock_ret_class.return_value = mock_ret

            result = await search_knowledge(
                request=req,
                query="refund",
                project_id="chatbot",
                limit=4,
            )

        assert mock_ret.search.call_args.kwargs["tenant_id"] == "default"
        assert result.total == 0

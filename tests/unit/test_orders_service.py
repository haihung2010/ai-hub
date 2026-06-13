"""Unit tests for OrdersService."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.orders_service import Order, OrdersService, ReturnRequest


def _make_pool(rows_by_query: dict):
    """Mock pool: rows_by_query maps query → list of rows."""
    cur = AsyncMock()
    cur.fetchone = AsyncMock(side_effect=lambda: None)  # default
    cur.fetchall = AsyncMock(return_value=[])
    cur.execute = AsyncMock()

    async def fake_execute(query, params=None):
        # Match by query prefix
        for key, rows in rows_by_query.items():
            if key in query:
                cur.fetchone = AsyncMock(return_value=rows[0] if rows else None)
                cur.fetchall = AsyncMock(return_value=rows)
                return
        cur.fetchone = AsyncMock(return_value=None)
        cur.fetchall = AsyncMock(return_value=[])

    cur.execute = fake_execute

    conn = MagicMock()
    conn.cursor = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    return pool


@pytest.mark.no_isolated_db
@pytest.mark.asyncio
async def test_get_by_code_returns_order():
    pool = _make_pool({
        "SELECT id, tenant_id": [{
            "id": "ord-1", "tenant_id": "t1", "user_id": "u1",
            "order_code": "ORD-001", "product_name": "Áo thun",
            "size": "M", "color": "trắng", "price": 250000,
            "purchase_date": "2026-06-13 10:00:00", "status": "active",
        }],
    })
    svc = OrdersService(pool)
    order = await svc.get_by_code("t1", "ORD-001")
    assert order is not None
    assert order.order_code == "ORD-001"
    assert order.product_name == "Áo thun"


@pytest.mark.no_isolated_db
@pytest.mark.asyncio
async def test_get_by_code_returns_none_if_not_found():
    pool = _make_pool({})  # no rows
    svc = OrdersService(pool)
    order = await svc.get_by_code("t1", "MISSING")
    assert order is None


@pytest.mark.no_isolated_db
@pytest.mark.asyncio
async def test_create_order_returns_order_with_code():
    pool = _make_pool({})
    svc = OrdersService(pool)
    order = await svc.create_order(
        tenant_id="t1", user_id="u1", order_code="ORD-002",
        product_name="Quần jean", size="L", color="xanh", price=450000,
    )
    assert order.order_code == "ORD-002"
    assert order.product_name == "Quần jean"
    assert order.status == "active"


@pytest.mark.no_isolated_db
@pytest.mark.asyncio
async def test_list_user_orders_filters_by_user():
    pool = _make_pool({
        "SELECT id, tenant_id": [
            {"id": "o1", "tenant_id": "t1", "user_id": "u1", "order_code": "A",
             "product_name": "P1", "size": "M", "color": None, "price": 100,
             "purchase_date": "2026-06-13", "status": "active"},
            {"id": "o2", "tenant_id": "t1", "user_id": "u1", "order_code": "B",
             "product_name": "P2", "size": "L", "color": None, "price": 200,
             "purchase_date": "2026-06-12", "status": "active"},
        ],
    })
    svc = OrdersService(pool)
    orders = await svc.list_user_orders("t1", "u1")
    assert len(orders) == 2
    assert all(o.user_id == "u1" for o in orders)

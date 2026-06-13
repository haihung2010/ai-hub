"""Orders + return request service for e-commerce chatbot."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Order:
    id: str
    tenant_id: str
    user_id: str
    order_code: str
    product_name: str
    size: str | None
    color: str | None
    price: int | None
    purchase_date: str
    status: str


@dataclass
class ReturnRequest:
    id: str
    tenant_id: str
    order_id: str
    reason: str
    product_serial: str | None
    status: str
    requested_at: str
    resolved_at: str | None
    resolution_note: str | None


class OrdersService:
    """CRUD for orders + return requests. Multi-tenant isolated."""

    def __init__(self, db_pool):
        self.db = db_pool

    async def create_order(
        self,
        tenant_id: str,
        user_id: str,
        order_code: str,
        product_name: str,
        size: str | None = None,
        color: str | None = None,
        price: int | None = None,
    ) -> Order:
        """Create a new order. Returns the Order dataclass."""
        order_id = str(uuid.uuid4())
        async with self.db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO orders (id, tenant_id, user_id, order_code, product_name, size, color, price) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (order_id, tenant_id, user_id, order_code, product_name, size, color, price),
                )
        return Order(
            id=order_id, tenant_id=tenant_id, user_id=user_id, order_code=order_code,
            product_name=product_name, size=size, color=color, price=price,
            purchase_date=datetime.now(timezone.utc).isoformat(),
            status="active",
        )

    async def get_by_code(self, tenant_id: str, order_code: str) -> Order | None:
        """Look up order by order_code, scoped to tenant. Returns None if not found.

        Critical: must filter by tenant_id to prevent cross-tenant leaks.
        """
        async with self.db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, tenant_id, user_id, order_code, product_name, size, color, price, "
                    "purchase_date, status FROM orders WHERE tenant_id = %s AND order_code = %s",
                    (tenant_id, order_code),
                )
                row = await cur.fetchone()
        if not row:
            return None
        return Order(
            id=row["id"], tenant_id=row["tenant_id"], user_id=row["user_id"],
            order_code=row["order_code"], product_name=row["product_name"],
            size=row["size"], color=row["color"], price=row["price"],
            purchase_date=str(row["purchase_date"]), status=row["status"],
        )

    async def list_user_orders(self, tenant_id: str, user_id: str, limit: int = 20) -> list[Order]:
        """List orders for a user (most recent first)."""
        async with self.db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, tenant_id, user_id, order_code, product_name, size, color, price, "
                    "purchase_date, status FROM orders WHERE tenant_id = %s AND user_id = %s "
                    "ORDER BY purchase_date DESC LIMIT %s",
                    (tenant_id, user_id, limit),
                )
                rows = await cur.fetchall()
        return [
            Order(
                id=r["id"], tenant_id=r["tenant_id"], user_id=r["user_id"],
                order_code=r["order_code"], product_name=r["product_name"],
                size=r["size"], color=r["color"], price=r["price"],
                purchase_date=str(r["purchase_date"]), status=r["status"],
            )
            for r in rows
        ]

    async def request_return(
        self, tenant_id: str, order_id: str, reason: str, product_serial: str | None = None
    ) -> ReturnRequest:
        """Create a return request for an order. Returns the ReturnRequest."""
        ret_id = str(uuid.uuid4())
        async with self.db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO return_requests (id, tenant_id, order_id, reason, product_serial) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (ret_id, tenant_id, order_id, reason, product_serial),
                )
        return ReturnRequest(
            id=ret_id, tenant_id=tenant_id, order_id=order_id, reason=reason,
            product_serial=product_serial, status="pending",
            requested_at=datetime.now(timezone.utc).isoformat(),
            resolved_at=None, resolution_note=None,
        )

"""Orders + return request REST endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.services.orders_service import OrdersService, ReturnRequest, Order
from app.core.database import _get_pool
from app.utils.tenant_guard import resolve_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["orders"])


def _get_orders_service() -> OrdersService:
    pool = _get_pool()
    return OrdersService(pool)


@router.post("/orders")
def create_order(
    request: Request,
    tenant_id: str,
    user_id: str,
    order_code: str,
    product_name: str,
    size: Optional[str] = None,
    color: Optional[str] = None,
    price: Optional[int] = None,
    svc: OrdersService = Depends(_get_orders_service),
) -> dict:
    """Create a new order."""
    resolve_tenant(request, tenant_id)
    order = svc.create_order(
        tenant_id=tenant_id, user_id=user_id, order_code=order_code,
        product_name=product_name, size=size, color=color, price=price,
    )
    return {"order_code": order.order_code, "id": order.id, "status": order.status}


@router.get("/orders/{order_code}")
def get_order(
    request: Request,
    order_code: str,
    tenant_id: str,
    svc: OrdersService = Depends(_get_orders_service),
) -> dict:
    """Look up order by order_code. Returns 404 if not found.

    Cross-tenant requests (tenant-bound key + mismatched tenant_id) are
    rejected with 403 before the service layer is touched.
    """
    resolve_tenant(request, tenant_id)
    order = svc.get_by_code(tenant_id, order_code)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "id": order.id, "tenant_id": order.tenant_id, "user_id": order.user_id,
        "order_code": order.order_code, "product_name": order.product_name,
        "size": order.size, "color": order.color, "price": order.price,
        "purchase_date": order.purchase_date, "status": order.status,
    }


@router.post("/orders/{order_code}/return")
def request_return(
    request: Request,
    order_code: str,
    tenant_id: str,
    reason: str,
    product_serial: Optional[str] = None,
    svc: OrdersService = Depends(_get_orders_service),
) -> dict:
    """Process a return request. Verifies order exists in this tenant first."""
    resolve_tenant(request, tenant_id)
    order = svc.get_by_code(tenant_id, order_code)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    ret = svc.request_return(
        tenant_id=tenant_id, order_id=order.id, reason=reason,
        product_serial=product_serial,
    )
    return {
        "return_id": ret.id, "order_id": ret.order_id, "status": ret.status,
        "requested_at": ret.requested_at,
    }

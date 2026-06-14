"""Orders + return request REST endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.services.orders_service import OrdersService, ReturnRequest, Order
from app.core.database import _get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["orders"])


def _get_orders_service() -> OrdersService:
    pool = _get_pool()
    return OrdersService(pool)


def _check_tenant_match(request: Request, tenant_id: str) -> None:
    """Reject cross-tenant requests from tenant-bound API keys.

    The SecurityMiddleware sets ``request.state.api_key_tenant_id`` from
    the API key row; if the bound tenant doesn't match the tenant_id
    query param, the caller is trying to access another tenant's data.
    Master keys (the legacy ``X-API-KEY`` matching ``settings.api_key``)
    leave ``api_key_tenant_id = None`` and pass through by design — ops
    tooling legitimately needs cross-tenant access.

    Audit 2026-06-14: previously these routes accepted any ``tenant_id``
    from the query string, letting tenant-bound keys read or write any
    tenant's orders. Mirrors the force-override pattern in
    ``app/routes/chat.py:45-57``.
    """
    api_key_tenant = getattr(request.state, "api_key_tenant_id", None)
    if api_key_tenant is not None and api_key_tenant != tenant_id:
        logger.info(
            "orders_tenant_mismatch api_key_tenant=%s request_tenant=%s path=%s",
            api_key_tenant, tenant_id, request.url.path,
        )
        raise HTTPException(
            status_code=403,
            detail="tenant_id does not match API key",
        )


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
    _check_tenant_match(request, tenant_id)
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
    _check_tenant_match(request, tenant_id)
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
    _check_tenant_match(request, tenant_id)
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

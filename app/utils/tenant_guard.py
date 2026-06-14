"""Tenant isolation enforcement at the route layer.

Tenant scoping lives in two places:
  * `request.state.api_key_tenant_id` — set by the security middleware
    to the tenant bound to the API key (or OAuth bearer token). For
    the master key, this is ``None`` (master key has legitimate
    cross-tenant access by design).
  * `request.state.api_key_is_admin` — set by the middleware to True
    for admin keys/tokens. Admin keys can read across tenants.

Every route that returns data scoped to a single tenant (sessions,
history, knowledge cards, predictions, skills) MUST go through one of
the helpers in this module. Otherwise a client can:
  1. own an API key bound to tenant ``foo``
  2. send ``?tenant_id=bar`` (or pass a path-param entity from bar)
  3. read bar's data — cross-tenant leak.

The helpers raise ``HTTPException(403)`` if the client's claimed tenant
does not match the principal's bound tenant. Admins and master-key
callers (no auth tenant) are exempt.
"""

from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException, Request, status


def _auth_tenant(request: Request) -> str | None:
    """Return the tenant_id the security middleware bound to the
    principal, or ``None`` for master-key / no-key callers."""
    return getattr(request.state, "api_key_tenant_id", None)


def _is_admin(request: Request) -> bool:
    return bool(getattr(request.state, "api_key_is_admin", False))


def resolve_tenant(
    request: Request,
    claimed: str | None,
    *,
    default: str = "default",
) -> str:
    """Return the tenant the request should run as.

    * If the principal is admin OR has no auth tenant (master key):
      honour the client's ``claimed`` value (defaulting to ``default``).
    * Otherwise: ``claimed`` MUST equal the auth tenant, else 403.
    """
    auth_tenant = _auth_tenant(request)
    if _is_admin(request) or auth_tenant is None:
        return claimed or default
    if not claimed:
        return auth_tenant
    if claimed != auth_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"tenant_id mismatch: key is bound to {auth_tenant!r}, "
                f"request claimed {claimed!r}"
            ),
        )
    return claimed


def assert_entity_tenant(
    request: Request,
    entity_tenant: str | None,
    *,
    entity_label: str,
    entity_id: str,
) -> str:
    """Verify the entity the route just fetched belongs to the
    principal's tenant. Returns the auth tenant for downstream use.

    Use this for routes where the path param is a global id (user_id,
    card_id, skill_id, …) and the actual row's tenant must be checked
    after the fetch. A mismatch returns 404 (NOT 403) so we don't
    leak the existence of the entity to a foreign tenant.
    """
    auth_tenant = _auth_tenant(request)
    if _is_admin(request) or auth_tenant is None:
        return auth_tenant or entity_tenant or "default"
    if entity_tenant != auth_tenant:
        # 404 on purpose: "you can't see this" not "this exists but
        # you can't read it". Same posture as the strict-isolation
        # tests in tests/integration/test_user_sessions_endpoint.py.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_label} not found: {entity_id}",
        )
    return auth_tenant


def filter_tenant_param(
    request: Request,
    claimed: str | None,
) -> str:
    """Same as ``resolve_tenant`` but kept for symmetry with the
    inline guard the chat route already uses
    (``payload.tenant_id = api_key_tenant``)."""
    return resolve_tenant(request, claimed)


def bulk_tenant_check(
    request: Request,
    items: Iterable,
    *,
    tenant_attr: str = "tenant_id",
    entity_label: str = "item",
) -> list:
    """Filter a list of objects/rows to only those in the principal's
    tenant. Admin / no-auth-tenant callers see everything; everyone
    else sees only their own. Returns a list (not a generator) so the
    caller's existing code paths still work.
    """
    auth_tenant = _auth_tenant(request)
    if _is_admin(request) or auth_tenant is None:
        return list(items)
    out = []
    for item in items:
        item_tenant = getattr(item, tenant_attr, None) or (item.get(tenant_attr) if isinstance(item, dict) else None)
        if item_tenant == auth_tenant:
            out.append(item)
    return out

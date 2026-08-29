"""`/orders` CRUD — the managed workload. Each handler does 1-2 DB queries."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_cache, get_session, get_store
from app.faults import maybe_slow_db
from app.models import Order
from app.remediation import ResponseCache
from app.schemas import OrderCreate, OrderRead, OrderUpdate
from app.state_store import StateStore

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    session: AsyncSession = Depends(get_session),
    store: StateStore = Depends(get_store),
    cache: ResponseCache = Depends(get_cache),
):
    await maybe_slow_db(store)
    order = Order(**payload.model_dump())
    session.add(order)
    await session.commit()
    await session.refresh(order)
    cache.clear()  # list results are now stale
    return order


@router.get("", response_model=list[OrderRead])
async def list_orders(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    store: StateStore = Depends(get_store),
    cache: ResponseCache = Depends(get_cache),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    key = f"orders:{limit}:{offset}"

    if store.read().remediation.cache_enabled:
        cached = cache.get(key)
        if cached is not None:
            return cached

    await maybe_slow_db(store)
    # 1: count (cheap aggregate), 2: page — gives the trace two DB spans.
    await session.scalar(select(func.count()).select_from(Order))
    rows = (
        await session.scalars(
            select(Order).order_by(Order.id.desc()).limit(limit).offset(offset)
        )
    ).all()
    result = [OrderRead.model_validate(row) for row in rows]

    if store.read().remediation.cache_enabled:
        cache.set(key, result)
    return result


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    store: StateStore = Depends(get_store),
):
    await maybe_slow_db(store)
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@router.patch("/{order_id}", response_model=OrderRead)
async def update_order(
    order_id: int,
    payload: OrderUpdate,
    session: AsyncSession = Depends(get_session),
    store: StateStore = Depends(get_store),
    cache: ResponseCache = Depends(get_cache),
):
    await maybe_slow_db(store)
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    changes = payload.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(order, field, value)
    await session.commit()
    await session.refresh(order)
    cache.clear()
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    store: StateStore = Depends(get_store),
    cache: ResponseCache = Depends(get_cache),
):
    await maybe_slow_db(store)
    result = await session.execute(delete(Order).where(Order.id == order_id))
    await session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="order not found")
    cache.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

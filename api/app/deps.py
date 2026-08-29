"""Shared FastAPI dependencies. Resources live on ``app.state``."""
from __future__ import annotations

import secrets
from typing import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.remediation import ResponseCache
from app.state_store import StateStore


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> StateStore:
    return request.app.state.store


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_cache(request: Request) -> ResponseCache:
    return request.app.state.cache


async def get_session(
    database: Database = Depends(get_database),
) -> AsyncIterator[AsyncSession]:
    async with database.sessionmaker() as session:
        yield session


def require_admin(
    x_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing admin token"
        )

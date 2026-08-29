"""Admin control plane: fault injection + remediation. `X-Admin-Token` required."""
from __future__ import annotations

import socket
from typing import Annotated, Union

from fastapi import APIRouter, Body, Depends
from pydantic import Field

from app.deps import get_cache, get_database, get_store, require_admin
from app.db import Database
from app.remediation import ResponseCache, apply_remediation
from app.schemas import (
    ClearFault,
    ErrorFault,
    LatencyFault,
    RemediationRequest,
    StateResponse,
)
from app.state_store import FaultState, StateStore

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

FaultRequest = Annotated[
    Union[LatencyFault, ErrorFault, ClearFault], Field(discriminator="type")
]


@router.post("/fault")
async def set_fault(
    payload: FaultRequest = Body(...),
    store: StateStore = Depends(get_store),
):
    if isinstance(payload, LatencyFault):
        fault = store.update_fault(latency_ms=payload.ms, latency_target=payload.target)
    elif isinstance(payload, ErrorFault):
        fault = store.update_fault(error_rate=payload.magnitude)
    else:  # ClearFault
        fault = store.replace_fault(FaultState())
    return {"fault": fault}


@router.post("/remediation")
async def set_remediation(
    payload: RemediationRequest,
    store: StateStore = Depends(get_store),
    database: Database = Depends(get_database),
    cache: ResponseCache = Depends(get_cache),
):
    remediation = await apply_remediation(
        payload.action, payload.enabled, store=store, database=database, cache=cache
    )
    return {"remediation": remediation}


@router.get("/state", response_model=StateResponse)
async def get_state(store: StateStore = Depends(get_store)):
    state = store.read()
    return StateResponse(
        fault=state.fault, remediation=state.remediation, replica=socket.gethostname()
    )

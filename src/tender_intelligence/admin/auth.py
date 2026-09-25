"""Fail-closed Admin API actor boundary (docs/09 §9.3; O11 remains open)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from tender_intelligence.config.settings import get_env_settings


@dataclass(frozen=True)
class Actor:
    identity: str
    role: str


ActorResolver = callable


def resolve_actor(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_test_actor: Annotated[str | None, Header()] = None,
    x_test_role: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve actor through an installable auth provider; dev headers are opt-in only."""

    resolver = getattr(request.app.state, "actor_resolver", None)
    if resolver is not None:
        actor = resolver(request, authorization)
        if (
            not isinstance(actor, Actor)
            or not isinstance(actor.identity, str)
            or not actor.identity.strip()
            or actor.role not in {"admin", "viewer"}
        ):
            raise HTTPException(status_code=401, detail={"code": "authentication_required"})
        return actor
    if (
        get_env_settings().admin_enable_test_auth
        and get_env_settings().environment.lower() not in {"production", "prod"}
        and x_test_actor
        and x_test_role in {"admin", "viewer"}
    ):
        return Actor(identity=x_test_actor[:255], role=x_test_role)
    raise HTTPException(status_code=401, detail={"code": "authentication_required"})


CurrentActor = Annotated[Actor, Depends(resolve_actor)]


def require_admin(actor: CurrentActor) -> Actor:
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "authorization_denied"})
    return actor


AdminActor = Annotated[Actor, Depends(require_admin)]

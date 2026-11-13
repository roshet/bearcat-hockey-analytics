"""FastAPI: a thin, read-only layer over precomputed tables.

Nothing is computed per request (master plan, decision 2): reports are built
offline by ``bca scouting build`` and served verbatim. There is no auth: the
app runs locally until M7, and stays unlisted after that until the ACHA has
been asked for permission.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from bca.db import schema as db
from bca.ingest.endpoints import CURRENT_MENS_SEASON_ID

#: The Vite dev server. Production serving is decided in M7.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


class TeamSummary(BaseModel):
    team_id: int
    team: str
    division: str


def _session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.sessions
    with factory() as s:
        yield s


# Module level, not inside create_app: FastAPI resolves these by name.
SessionDep = Annotated[Session, Depends(_session)]
Season = Annotated[int, Query(description="season_id; default the current season")]


def create_app(sessions: sessionmaker[Session] | None = None) -> FastAPI:
    if sessions is None:
        from bca.db.session import make_engine, make_session_factory

        sessions = make_session_factory(make_engine())

    app = FastAPI(title="Bearcat Hockey Analytics", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    app.state.sessions = sessions
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET"],
                       allow_headers=["*"])

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/teams")
    def teams(s: SessionDep, season: Season = CURRENT_MENS_SEASON_ID) -> list[TeamSummary]:
        rows = s.scalars(
            select(db.StoredScoutingReport)
            .where(db.StoredScoutingReport.season_id == season)
            .order_by(db.StoredScoutingReport.team)
        )
        return [TeamSummary(team_id=r.team_id, team=r.team, division=r.division) for r in rows]

    @app.get("/api/special-teams/{team_id}")
    def special_teams(team_id: int, s: SessionDep,
                      season: Season = CURRENT_MENS_SEASON_ID) -> dict[str, Any]:
        row = s.get(db.StoredTeamDetailReport, (team_id, season))
        if row is None:
            raise HTTPException(
                404, f"no special-teams page for team {team_id} in season {season}"
            )
        payload: dict[str, Any] = json.loads(row.payload)
        return payload

    @app.get("/api/rankings")
    def rankings(s: SessionDep, season: Season = CURRENT_MENS_SEASON_ID) -> dict[str, Any]:
        row = s.get(db.StoredRankingsReport, season)
        if row is None:
            raise HTTPException(404, f"no rankings page for season {season}")
        payload: dict[str, Any] = json.loads(row.payload)
        return payload

    @app.get("/api/scouting/{team_id}")
    def scouting(team_id: int, s: SessionDep,
                 season: Season = CURRENT_MENS_SEASON_ID) -> dict[str, Any]:
        row = s.get(db.StoredScoutingReport, (team_id, season))
        if row is None:
            raise HTTPException(404, f"no scouting report for team {team_id} in season {season}")
        payload: dict[str, Any] = json.loads(row.payload)
        return payload

    return app

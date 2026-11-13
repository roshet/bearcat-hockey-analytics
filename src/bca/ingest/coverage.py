"""Per-season ingestion coverage: the M1 exit artifact.

Answers, for each men's regular season: how many MD1 teams, how many of their
games are complete, how many of those have an archived box score, how many
loaded cleanly -- and, when the numbers disagree, which games and why.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from bca.db import schema as db
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive

#: How many unexplained game ids to list per season before truncating.
_SAMPLE = 10


@dataclass
class SeasonCoverage:
    season: str
    season_id: int
    md1_teams: int
    games_scheduled: int
    games_final: int
    summaries_archived: int
    summaries_normalized: int
    issues: dict[str, int] = field(default_factory=dict)
    #: Final games with no archived box score (sample).
    missing_summaries: list[int] = field(default_factory=list)
    #: Archived but not loaded (sample) -- see ingest_issues for why.
    unloaded_summaries: list[int] = field(default_factory=list)

    @property
    def fetched(self) -> bool:
        """Whether any schedule for this season has been loaded at all."""
        return self.games_scheduled > 0

    @property
    def complete(self) -> bool:
        return self.fetched and (
            self.games_final == self.summaries_archived == self.summaries_normalized
        )


@dataclass
class CoverageReport:
    seasons: list[SeasonCoverage]
    #: Issues not tied to a game (season, team and roster resources).
    other_issues: dict[str, int]
    #: Never-recorded fields seen non-zero anywhere. Expected: 0.
    canary_findings: int

    @property
    def complete(self) -> bool:
        return all(s.complete for s in self.seasons) and self.canary_findings == 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["complete"] = self.complete
        for season, raw in zip(self.seasons, data["seasons"], strict=True):
            raw["complete"] = season.complete
        return data


def build_report(session: Session, archive: RawArchive) -> CoverageReport:
    seasons = [
        _season(session, archive, name, season_id)
        for name, season_id in endpoints.MENS_REGULAR_SEASONS.items()
    ]
    other = Counter(
        dict(
            session.execute(
                select(db.IngestIssue.stage, func.count())
                .where(db.IngestIssue.game_id.is_(None))
                .group_by(db.IngestIssue.stage)
            ).all()
        )
    )
    canaries = session.scalar(
        select(func.count()).select_from(db.IngestIssue).where(db.IngestIssue.stage == "canary")
    )
    return CoverageReport(seasons=seasons, other_issues=dict(other), canary_findings=canaries or 0)


def _season(session: Session, archive: RawArchive, name: str, season_id: int) -> SeasonCoverage:
    md1 = select(db.TeamSeason.team_id).where(
        db.TeamSeason.season_id == season_id, db.TeamSeason.is_md1
    )
    games = session.scalars(
        select(db.Game).where(
            db.Game.season_id == season_id,
            or_(db.Game.home_team_id.in_(md1), db.Game.visiting_team_id.in_(md1)),
        )
    ).all()
    final = [g for g in games if g.is_final]
    archived = {
        g.game_id
        for g in final
        if archive.path_for(endpoints.game_summary(g.game_id).cache_key).exists()
    }
    normalized = {g.game_id for g in final if g.summary_normalized}

    issues = dict(
        session.execute(
            select(db.IngestIssue.stage, func.count())
            .join(db.Game, db.Game.game_id == db.IngestIssue.game_id)
            .where(db.Game.season_id == season_id)
            .group_by(db.IngestIssue.stage)
        ).all()
    )

    final_ids = sorted(g.game_id for g in final)
    return SeasonCoverage(
        season=name,
        season_id=season_id,
        md1_teams=session.scalar(select(func.count()).select_from(md1.subquery())) or 0,
        games_scheduled=len(games),
        games_final=len(final),
        summaries_archived=len(archived),
        summaries_normalized=len(normalized),
        issues=issues,
        missing_summaries=[gid for gid in final_ids if gid not in archived][:_SAMPLE],
        unloaded_summaries=[
            gid for gid in final_ids if gid in archived and gid not in normalized
        ][:_SAMPLE],
    )

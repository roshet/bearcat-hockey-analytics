"""``bca audit run``: rebuild the reconciliation findings and completeness flags.

Offline and idempotent: it reads the normalized tables and replaces its own
output tables wholesale, in one transaction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from bca.audit.completeness import assess
from bca.audit.facts import gather
from bca.audit.reconcile import reconcile
from bca.db import schema as db


@dataclass
class AuditRun:
    findings: Counter[str]
    unexplained: Counter[str]
    quality_rows: int


def run_audit(sessions: sessionmaker[Session]) -> AuditRun:
    with sessions.begin() as session:
        facts = gather(session)
        result = reconcile(facts)
        quality = assess(facts)

        for table in (db.ReconciliationFinding, db.AuditCheckTotal, db.GameQuality):
            session.execute(delete(table))
        session.add_all(
            db.AuditCheckTotal(
                check_name=name, unit=t.unit, checked=t.checked, not_checkable=t.not_checkable
            )
            for name, t in result.totals.items()
        )
        session.add_all(
            db.ReconciliationFinding(
                check_name=f.check,
                season_id=f.season_id,
                game_id=f.game_id,
                team_id=f.team_id,
                player_id=f.player_id,
                expected=f.expected,
                actual=f.actual,
                explanation=f.explanation,
                detail=f.detail,
            )
            for f in result.findings
        )
        session.add_all(row.to_model() for row in quality)

    return AuditRun(
        findings=Counter(f.check for f in result.findings),
        unexplained=Counter(f.check for f in result.findings if f.explanation == "unexplained"),
        quality_rows=len(quality),
    )

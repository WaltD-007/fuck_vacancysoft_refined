from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import ClassificationResult, EnrichedJob


def cleanup_orphaned_classification_results(session: Session) -> int:
    valid_enriched_ids = set(session.execute(select(EnrichedJob.id)).scalars())
    classification_rows = list(session.execute(select(ClassificationResult)).scalars())

    removed = 0
    for row in classification_rows:
        if row.enriched_job_id not in valid_enriched_ids:
            session.delete(row)
            removed += 1

    session.commit()
    return removed

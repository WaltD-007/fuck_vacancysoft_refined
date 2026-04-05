from __future__ import annotations


def compute_export_score(*values: float) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)

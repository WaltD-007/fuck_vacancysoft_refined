from __future__ import annotations

from typing import Any

from vacancysoft.exporters.views import (
    accepted_only_query,
    accepted_plus_review_query,
    client_segment_query,
    load_exporter_config,
)


def resolve_profile_query(profile_name: str, config: dict[str, Any] | None = None):
    config = config or load_exporter_config()
    profiles = config.get("profiles", {})
    profile = profiles.get(profile_name)
    if profile is None:
        raise KeyError(f"Unknown export profile: {profile_name}")

    view = profile.get("view")
    if view == "accepted_only":
        return accepted_only_query()
    if view == "accepted_plus_review":
        return accepted_plus_review_query()
    raise KeyError(f"Unsupported profile view: {view}")


def resolve_segment_query(segment_name: str, config: dict[str, Any] | None = None):
    config = config or load_exporter_config()
    return client_segment_query(segment_name, config)

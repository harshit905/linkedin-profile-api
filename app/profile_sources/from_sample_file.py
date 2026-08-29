"""Source: a committed sample, for demo mode.

Lets the schema be exercised with no credentials — useful when reviewing the
API, and so a deployment still demonstrates its contract when no session is
configured. Reuses the parser test fixture, so the sample a reader sees is
the same data the tests assert against.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..response_models import Profile
from .source import Source
from .parse_api_json import parse_profile

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "profile_view.json"


class SampleFileSource(Source):
    name = "sample_file"
    requires_session = False

    async def fetch(self, public_id: str) -> Profile:
        bundle = {
            "profile_view": json.loads(FIXTURE_PATH.read_text()),
            "skills": {"elements": [{"name": "Mathematics", "endorsementCount": 99}]},
            "network_info": {"followersCount": 12000, "connectionsCount": 500},
            "partial_sections": [],
        }
        profile = parse_profile(bundle, public_id)
        return profile.model_copy(
            update={
                "meta": profile.meta.model_copy(
                    update={
                        "source": self.name,
                        "degraded_reason": (
                            "DEMO_MODE is enabled; this is a static sample, "
                            "not live LinkedIn data."
                        ),
                    }
                )
            }
        )

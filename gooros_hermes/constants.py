from __future__ import annotations

PRODUCT = "gooros-hermes-mission-control"
VERSION = "0.1.8"
SCHEMA_VERSION = 1

AGENTS = ("orchestrator", "scout", "scribe", "reach", "dev")
SPECIALISTS = ("scout", "scribe", "reach", "dev")

AGENT_META = {
    "orchestrator": {
        "code": "A-00",
        "initials": "OR",
        "name": "Orchestrator",
        "role": "Coordinator",
        "channel": "telegram",
    },
    "scout": {
        "code": "A-01",
        "initials": "SC",
        "name": "Scout",
        "role": "Research",
        "channel": "#scout",
    },
    "scribe": {
        "code": "A-02",
        "initials": "SB",
        "name": "Scribe",
        "role": "Writing",
        "channel": "#scribe",
    },
    "reach": {
        "code": "A-03",
        "initials": "RE",
        "name": "Reach",
        "role": "Marketing",
        "channel": "#reach",
    },
    "dev": {
        "code": "A-04",
        "initials": "DV",
        "name": "Dev",
        "role": "Engineering",
        "channel": "#dev",
    },
}

GOOROS_BEGIN = "# BEGIN GOOROS HERMES MANAGED"
GOOROS_END = "# END GOOROS HERMES MANAGED"

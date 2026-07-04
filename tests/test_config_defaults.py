"""The full-power configuration is the shipped default.

Every evolved capability ships on; a stored config still wins over defaults.
"""

import json

from hermes.config import Config, config_path

FULL_POWER_FLAGS = [
    "directives_enabled",
    "directive_header_rule",
    "compaction_enabled",
    "skills_enabled",
    "skills_nudge",
    "delegate_enabled",
    "prefix_cache_order",
    "checkpointing",
    "verify_before_done",
    "verify_code_runs",
]


def test_full_power_flags_on_by_default(home):
    cfg = Config.load()
    for flag in FULL_POWER_FLAGS:
        assert cfg.get(flag) is True, f"{flag} should default on"


def test_stored_config_still_wins_over_defaults(home):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"delegate_enabled": False}))
    cfg = Config.load()
    assert cfg.get("delegate_enabled") is False  # operator opt-out respected
    assert cfg.get("skills_enabled") is True  # unset keys take the new default

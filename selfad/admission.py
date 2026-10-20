"""Immutable contest rules and the execution context accepted with a push."""

import json
from types import SimpleNamespace

from sqlalchemy import select, update

from selfad.models import ScoringSettings
from selfad.scoring import get_scoring_settings

RULE_FIELDS = (
    "attack_reward_mode",
    "attack_max_points",
    "attack_points_per_flag",
    "defense_reward_mode",
    "defense_max_points",
    "defense_points_lost_per_flag",
    "penalty_mode",
    "attack_penalty_value",
    "defense_penalty_value",
    "attack_free_failures",
    "defense_free_failures",
    "penalize_check_errors",
    "stdout_noise_mode",
    "stdout_noise_penalty_percent",
)
RUNTIME_FIELDS = (
    "repository_path",
    "jury_repository_path",
    "default_branch",
    "runtime_source_commit",
    "runtime_jury_commit",
    "validated_jury_commit",
    "container_port",
    "healthcheck_path",
)


def rules_dict(settings):
    return {field: getattr(settings, field) for field in RULE_FIELDS}


def snapshot_admission(session, service, player):
    scoring = get_scoring_settings(session)
    serialized = json.dumps(rules_dict(scoring), sort_keys=True)
    session.execute(
        update(ScoringSettings)
        .where(
            ScoringSettings.id == scoring.id,
            ScoringSettings.locked_rules_json.is_(None),
        )
        .values(locked_rules_json=serialized)
        .execution_options(synchronize_session=False)
    )
    locked = session.scalar(
        select(ScoringSettings.locked_rules_json).where(ScoringSettings.id == scoring.id)
    )
    return json.dumps(
        {
            "schema_version": 1,
            "rules": json.loads(locked),
            "runtime": {field: getattr(service, field) for field in RUNTIME_FIELDS},
            "attack_dockerfile_sha": player.attack_dockerfile_sha,
            "defense_dockerfile_sha": player.defense_dockerfile_sha,
            "attack_requirements": scoring.attack_requirements,
            "allow_user_attack_requirements": scoring.allow_user_attack_requirements,
            "penalize_stdout_noise": scoring.penalize_stdout_noise,
        },
        sort_keys=True,
    )


def restore_admission(serialized):
    value = json.loads(serialized)
    rules = SimpleNamespace(
        **value["rules"],
        penalize_stdout_noise=value.get("penalize_stdout_noise", False),
        attack_requirements=value["attack_requirements"],
        allow_user_attack_requirements=value["allow_user_attack_requirements"],
    )
    return value, rules

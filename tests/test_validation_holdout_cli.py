from __future__ import annotations

import argparse
import json

import pytest

from apps.api.app.validation.repository import ValidationSampleRepository
from apps.api.app.validation.holdout_benchmark import write_json_atomic
from scripts.manage_validation_holdout import run


def test_cli_init_freeze_and_offline_evaluate_empty_holdout(tmp_path) -> None:
    database = tmp_path / "validation.sqlite3"
    ValidationSampleRepository(database)
    preregistration = tmp_path / "preregistration.json"
    temporal = tmp_path / "temporal.json"
    output = tmp_path / "validation_holdout_benchmark_v1.json"
    temporal.write_text('{"samples":[]}', encoding="utf-8")

    initialized = run(argparse.Namespace(
        command="init", preregistration=preregistration,
        methodology_note=["Independent future holdout."],
    ))
    assert initialized["status"] == "draft"

    collecting = run(argparse.Namespace(
        command="collecting", preregistration=preregistration,
    ))
    assert collecting["status"] == "collecting"

    frozen = run(argparse.Namespace(
        command="freeze", preregistration=preregistration,
        validation_db=database,
    ))
    assert frozen == {
        "status": "frozen",
        "holdout_sample_count": 0,
        "preregistration": str(preregistration.resolve()),
    }

    evaluated = run(argparse.Namespace(
        command="evaluate", preregistration=preregistration,
        validation_db=database, temporal_benchmark_json=temporal,
        soak_runs=None, output=output,
    ))
    assert evaluated["status"] == "evaluated"
    assert evaluated["gate"] == "INSUFFICIENT_HOLDOUT_DATA"
    assert json.loads(output.read_text(encoding="utf-8"))["dataset"]["binary_samples"] == 0
    assert json.loads(preregistration.read_text(encoding="utf-8"))["status"] == "evaluated"


def test_cli_never_overwrites_preregistration_or_evaluation_artifact_silently(tmp_path) -> None:
    preregistration = tmp_path / "preregistration.json"
    arguments = argparse.Namespace(
        command="init", preregistration=preregistration, methodology_note=[]
    )
    run(arguments)
    with pytest.raises(FileExistsError):
        run(arguments)
    artifact = tmp_path / "artifact.json"
    write_json_atomic(artifact, {"one": 1})
    with pytest.raises(FileExistsError):
        write_json_atomic(artifact, {"two": 2})

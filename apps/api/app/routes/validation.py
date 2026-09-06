"""Persistent experimental ground-truth endpoints."""

from __future__ import annotations

import csv
import io
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from ..dependencies import (
    AnalysisRegistry,
    get_analysis_registry,
    get_validation_repository,
    get_validation_temporal_benchmark_runner,
)
from ..exceptions import ApiError
from ..validation.models import (
    MaintenanceTruth,
    ValidationSampleCreate,
    ValidationSampleDetail,
    ValidationSampleList,
    ValidationSampleRow,
    ValidationBenchmark,
    ValidationTemporalBenchmark,
    ValidationSource,
    ValidationSummary,
    VegetationClass,
)
from ..validation.benchmark import build_validation_benchmark
from ..validation.repository import DuplicateAnalysisError, ValidationSampleRepository
from ..validation.service import CSV_COLUMNS, build_summary, create_record
from ..validation.temporal_benchmark import ValidationTemporalBenchmarkRunner


router = APIRouter(prefix="/api", tags=["validation"])
RepositoryDependency = Annotated[
    ValidationSampleRepository, Depends(get_validation_repository)
]
RegistryDependency = Annotated[AnalysisRegistry, Depends(get_analysis_registry)]
TemporalBenchmarkDependency = Annotated[
    ValidationTemporalBenchmarkRunner,
    Depends(get_validation_temporal_benchmark_runner),
]


@router.post(
    "/validation-samples",
    response_model=ValidationSampleDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_validation_sample(
    payload: ValidationSampleCreate,
    repository: RepositoryDependency,
    registry: RegistryDependency,
) -> dict:
    analysis_id = str(payload.analysis_id)
    result = registry.get(analysis_id)
    if result is None:
        raise ApiError(
            "ANALYSIS_NOT_AVAILABLE",
            "A analise precisa estar disponivel para ser registrada; execute-a novamente.",
            status_code=404,
        )
    try:
        return repository.insert(create_record(result, payload))
    except DuplicateAnalysisError as exc:
        raise ApiError(
            "VALIDATION_SAMPLE_EXISTS",
            "Esta analise ja possui uma amostra de validacao registrada.",
            status_code=409,
        ) from exc


@router.get("/validation-samples", response_model=ValidationSampleList)
def list_validation_samples(
    repository: RepositoryDependency,
    vegetation_class: VegetationClass | None = None,
    maintenance_truth: MaintenanceTruth | None = None,
    validation_source: ValidationSource | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    rows, total = repository.list(
        vegetation_class=vegetation_class.value if vegetation_class else None,
        maintenance_truth=maintenance_truth.value if maintenance_truth else None,
        validation_source=validation_source.value if validation_source else None,
        limit=limit,
        offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/validation-samples/{sample_id}", response_model=ValidationSampleDetail
)
def get_validation_sample(
    sample_id: UUID, repository: RepositoryDependency
) -> dict:
    record = repository.get(str(sample_id))
    if record is None:
        raise ApiError(
            "VALIDATION_SAMPLE_NOT_FOUND",
            "A amostra de validacao nao foi encontrada.",
            status_code=404,
        )
    return record


@router.get("/validation-summary", response_model=ValidationSummary)
def validation_summary(repository: RepositoryDependency) -> dict:
    return build_summary(repository.all_rows())


@router.get("/validation-benchmark", response_model=ValidationBenchmark)
def validation_benchmark(repository: RepositoryDependency) -> dict:
    return build_validation_benchmark(repository.all_details())


@router.get(
    "/validation-temporal-benchmark",
    response_model=ValidationTemporalBenchmark,
)
def validation_temporal_benchmark(
    repository: RepositoryDependency,
    runner: TemporalBenchmarkDependency,
) -> dict:
    return runner.run(repository.all_details())


@router.get("/validation-export")
def export_validation_samples(repository: RepositoryDependency) -> Response:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(repository.all_rows())
    return Response(
        content=stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="validation_samples.csv"'
        },
    )

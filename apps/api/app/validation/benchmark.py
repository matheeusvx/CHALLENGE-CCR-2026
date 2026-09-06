"""Exploratory statistics over persisted validation samples.

This module is deliberately descriptive: it does not emit recommendations,
thresholds, fusion weights, or automatic corrections.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from itertools import combinations
from typing import Any, Iterable

from .models import MaintenanceTruth, ValidationSource, VegetationClass


MIN_PAIRWISE_GROUP_SIZE = 2
MIN_CORRELATION_PAIRS = 3

FEATURES: dict[str, str] = {
    "current_ndvi_mean": "s2_ndvi_mean",
    "current_ndvi_median": "s2_ndvi_median",
    "current_percentile": "s2_current_percentile",
    "s2_observation_count": "s2_observation_count",
    "s2_quality": "s2_quality",
    "vv_sigma0_db": "s1_vv_sigma0_db",
    "vh_sigma0_db": "s1_vh_sigma0_db",
    "vh_minus_vv_db": "s1_vh_minus_vv_db",
    "vh_vv_sigma0_ratio": "s1_vh_vv_sigma0_ratio",
    "s1_canonical_observation_count": "s1_canonical_observation_count",
    "s1_quality": "s1_quality",
    "s1_coverage": "s1_coverage",
}

PAIRWISE_FEATURES = (
    "vv_sigma0_db",
    "vh_sigma0_db",
    "vh_minus_vv_db",
    "current_ndvi_mean",
    "current_percentile",
)

CORRELATIONS = {
    "ndvi_vs_vv_sigma0_db": ("current_ndvi_mean", "vv_sigma0_db"),
    "ndvi_vs_vh_sigma0_db": ("current_ndvi_mean", "vh_sigma0_db"),
    "percentile_vs_vv_sigma0_db": ("current_percentile", "vv_sigma0_db"),
    "percentile_vs_vh_sigma0_db": ("current_percentile", "vh_sigma0_db"),
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _values(rows: Iterable[dict[str, Any]], column: str) -> list[float]:
    values = (_finite(row.get(column)) for row in rows)
    return sorted(value for value in values if value is not None)


def _quantile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def descriptive_statistics(values: Iterable[float]) -> dict[str, Any] | None:
    finite_values = sorted(
        numeric
        for value in values
        if (numeric := _finite(value)) is not None
    )
    if not finite_values:
        return None
    return {
        "count": len(finite_values),
        "mean": statistics.fmean(finite_values),
        "median": statistics.median(finite_values),
        "min": finite_values[0],
        "max": finite_values[-1],
        "q25": _quantile(finite_values, 0.25),
        "q75": _quantile(finite_values, 0.75),
        "standard_deviation": (
            statistics.stdev(finite_values) if len(finite_values) >= 2 else None
        ),
    }


def cliffs_delta(values_a: Iterable[float], values_b: Iterable[float]) -> float | None:
    a = list(values_a)
    b = list(values_b)
    if not a or not b:
        return None
    greater = sum(left > right for left in a for right in b)
    lower = sum(left < right for left in a for right in b)
    return (greater - lower) / (len(a) * len(b))


def effect_magnitude(delta: float | None) -> str | None:
    if delta is None:
        return None
    absolute = abs(delta)
    if absolute < 0.147:
        return "negligible"
    if absolute < 0.33:
        return "small"
    if absolute < 0.474:
        return "medium"
    return "large"


def iqr_overlap(stats_a: dict[str, Any], stats_b: dict[str, Any]) -> str:
    lower_a, upper_a = stats_a["q25"], stats_a["q75"]
    lower_b, upper_b = stats_b["q25"], stats_b["q75"]
    if upper_a < lower_b or upper_b < lower_a:
        return "separated"
    if (
        lower_b <= stats_a["median"] <= upper_b
        or lower_a <= stats_b["median"] <= upper_a
    ):
        return "strong_overlap"
    return "partial_overlap"


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed):
        end = position + 1
        while end < len(indexed) and indexed[end][1] == indexed[position][1]:
            end += 1
        average_rank = ((position + 1) + end) / 2.0
        for original_index, _ in indexed[position:end]:
            ranks[original_index] = average_rank
        position = end
    return ranks


def spearman_rho(values_x: Iterable[float], values_y: Iterable[float]) -> float | None:
    x = list(values_x)
    y = list(values_y)
    if len(x) != len(y) or len(x) < MIN_CORRELATION_PAIRS:
        return None
    ranks_x = _average_ranks(x)
    ranks_y = _average_ranks(y)
    mean_x = statistics.fmean(ranks_x)
    mean_y = statistics.fmean(ranks_y)
    covariance = sum(
        (left - mean_x) * (right - mean_y)
        for left, right in zip(ranks_x, ranks_y)
    )
    variance_x = sum((rank - mean_x) ** 2 for rank in ranks_x)
    variance_y = sum((rank - mean_y) ** 2 for rank in ranks_y)
    denominator = math.sqrt(variance_x * variance_y)
    return covariance / denominator if denominator else None


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        snapshot = _mapping(value.get("snapshot"))
        sentinel2 = _mapping(snapshot.get("sentinel2"))
        sentinel1 = _mapping(snapshot.get("sentinel1"))
        value["s2_observation_count"] = sentinel2.get("observation_count")
        value["s2_quality"] = sentinel2.get("analysis_quality_score")
        value["s1_radiometric_calibration_status"] = sentinel1.get(
            "radiometric_calibration_status"
        )
        value["s1_calibrated_observation_count"] = sentinel1.get(
            "calibrated_observation_count"
        )
        normalized.append(value)
    return normalized


def _group_statistics(
    rows: list[dict[str, Any]], groups: Iterable[str], group_column: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for group in groups:
        selected = [row for row in rows if row.get(group_column) == group]
        result[group] = {
            "sample_count": len(selected),
            "features": {
                feature: descriptive_statistics(_values(selected, column))
                for feature, column in FEATURES.items()
            },
        }
    return result


def _dataset_overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts_by_class = Counter(row.get("vegetation_class") for row in rows)
    counts_by_truth = Counter(row.get("maintenance_truth") for row in rows)
    counts_by_source = Counter(row.get("validation_source") for row in rows)
    counts_by_orbit = Counter(
        str(row["s1_canonical_relative_orbit"])
        if row.get("s1_canonical_relative_orbit") is not None
        else "unknown"
        for row in rows
    )
    with_s1 = sum(row.get("s1_status") == "available" for row in rows)
    with_calibrated_s1 = sum(
        any(
            _finite(row.get(column)) is not None
            for column in ("s1_vv_sigma0_db", "s1_vh_sigma0_db")
        )
        for row in rows
    )
    with_s2 = sum(
        row.get("s2_decision") is not None
        or any(
            _finite(row.get(column)) is not None
            for column in ("s2_ndvi_mean", "s2_ndvi_median", "s2_current_percentile")
        )
        for row in rows
    )
    return {
        "total_samples": len(rows),
        "counts_by_vegetation_class": {
            item.value: counts_by_class[item.value] for item in VegetationClass
        },
        "counts_by_maintenance_truth": {
            item.value: counts_by_truth[item.value] for item in MaintenanceTruth
        },
        "counts_by_validation_source": {
            item.value: counts_by_source[item.value] for item in ValidationSource
        },
        "counts_by_s1_canonical_relative_orbit": dict(
            sorted(counts_by_orbit.items(), key=lambda item: item[0])
        ),
        "samples_with_s1": with_s1,
        "samples_without_s1": len(rows) - with_s1,
        "samples_with_calibrated_s1": with_calibrated_s1,
        "samples_with_s2": with_s2,
    }


def _sentinel2_performance(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    comparable_truth = {"cut", "no_cut"}
    comparable_predictions = {"cortar", "nao_cortar"}
    eligible = [
        row
        for row in rows
        if row.get("maintenance_truth") in comparable_truth
        and row.get("s2_decision") in comparable_predictions
    ]
    binary_truth = [
        row for row in rows if row.get("maintenance_truth") in comparable_truth
    ]
    confusion = {
        "true_cut_pred_cut": 0,
        "true_cut_pred_no_cut": 0,
        "true_no_cut_pred_cut": 0,
        "true_no_cut_pred_no_cut": 0,
    }
    disagreements: list[dict[str, Any]] = []
    correct = 0
    for row in eligible:
        truth = row["maintenance_truth"]
        prediction = row["s2_decision"]
        expected = "cortar" if truth == "cut" else "nao_cortar"
        if prediction == expected:
            correct += 1
        key = f"true_{truth}_pred_{'cut' if prediction == 'cortar' else 'no_cut'}"
        confusion[key] += 1
        if prediction != expected:
            disagreements.append(
                {
                    "sample_id": row.get("sample_id"),
                    "analysis_id": row.get("analysis_id"),
                    "vegetation_class": row.get("vegetation_class"),
                    "maintenance_truth": truth,
                    "s2_decision": prediction,
                    "current_ndvi_mean": row.get("s2_ndvi_mean"),
                    "current_ndvi_median": row.get("s2_ndvi_median"),
                    "current_percentile": row.get("s2_current_percentile"),
                    "s1_canonical_relative_orbit": row.get(
                        "s1_canonical_relative_orbit"
                    ),
                    "vv_sigma0_db": row.get("s1_vv_sigma0_db"),
                    "vh_sigma0_db": row.get("s1_vh_sigma0_db"),
                    "vh_minus_vv_db": row.get("s1_vh_minus_vv_db"),
                    "vh_vv_sigma0_ratio": row.get("s1_vh_vv_sigma0_ratio"),
                }
            )
    incorrect = len(eligible) - correct
    performance = {
        "eligible_binary_samples": len(eligible),
        "correct": correct,
        "incorrect": incorrect,
        "accuracy": correct / len(eligible) if eligible else None,
        **confusion,
        "inconclusive_predictions": sum(
            row.get("s2_decision") == "inconclusivo" for row in binary_truth
        ),
        "other_non_comparable_predictions": sum(
            row.get("s2_decision") not in comparable_predictions | {"inconclusivo"}
            for row in binary_truth
        ),
    }
    return performance, disagreements


def _pairwise_separation(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    classes = [item.value for item in VegetationClass]
    for feature in PAIRWISE_FEATURES:
        column = FEATURES[feature]
        comparisons_for_feature: list[dict[str, Any]] = []
        for class_a, class_b in combinations(classes, 2):
            values_a = _values(
                [row for row in rows if row.get("vegetation_class") == class_a],
                column,
            )
            values_b = _values(
                [row for row in rows if row.get("vegetation_class") == class_b],
                column,
            )
            if min(len(values_a), len(values_b)) < MIN_PAIRWISE_GROUP_SIZE:
                continue
            stats_a = descriptive_statistics(values_a)
            stats_b = descriptive_statistics(values_b)
            assert stats_a is not None and stats_b is not None
            delta = cliffs_delta(values_a, values_b)
            comparisons_for_feature.append(
                {
                    "class_a": class_a,
                    "class_b": class_b,
                    "n_a": len(values_a),
                    "n_b": len(values_b),
                    "median_a": stats_a["median"],
                    "median_b": stats_b["median"],
                    "median_difference": stats_a["median"] - stats_b["median"],
                    "iqr_a": {
                        "q25": stats_a["q25"],
                        "q75": stats_a["q75"],
                        "width": stats_a["q75"] - stats_a["q25"],
                    },
                    "iqr_b": {
                        "q25": stats_b["q25"],
                        "q75": stats_b["q75"],
                        "width": stats_b["q75"] - stats_b["q25"],
                    },
                    "iqr_overlap": iqr_overlap(stats_a, stats_b),
                    "cliffs_delta": delta,
                    "effect_magnitude": effect_magnitude(delta),
                }
            )
        result[feature] = comparisons_for_feature
    return result


def _feature_summary(
    rows: list[dict[str, Any]], pairwise: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    features: dict[str, Any] = {}
    for feature, column in FEATURES.items():
        effects = [
            abs(item["cliffs_delta"])
            for item in pairwise.get(feature, [])
            if item.get("cliffs_delta") is not None
        ]
        represented_classes = sum(
            bool(
                _values(
                    [row for row in rows if row.get("vegetation_class") == item.value],
                    column,
                )
            )
            for item in VegetationClass
        )
        features[feature] = {
            "observation_count": len(_values(rows, column)),
            "represented_class_count": represented_classes,
            "largest_absolute_pairwise_effect": max(effects) if effects else None,
            "median_absolute_pairwise_effect": (
                statistics.median(effects) if effects else None
            ),
            "medium_or_large_comparison_count": sum(
                item["effect_magnitude"] in {"medium", "large"}
                for item in pairwise.get(feature, [])
            ),
        }
    rankable = [
        (feature, details["largest_absolute_pairwise_effect"])
        for feature, details in features.items()
        if details["largest_absolute_pairwise_effect"] is not None
    ]
    rankable.sort(key=lambda item: (-item[1], item[0]))
    return {
        "features": features,
        "descriptive_separation_rank": [
            {"rank": index, "feature": feature, "largest_absolute_effect": effect}
            for index, (feature, effect) in enumerate(rankable, start=1)
        ],
        "warning": (
            "Exploratory ranking based only on separation observed in the current "
            "dataset; it is not a scientific or operational feature selection."
        ),
    }


def _correlations(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, (feature_x, feature_y) in CORRELATIONS.items():
        column_x = FEATURES[feature_x]
        column_y = FEATURES[feature_y]
        pairs = [
            (left, right)
            for row in rows
            if (left := _finite(row.get(column_x))) is not None
            and (right := _finite(row.get(column_y))) is not None
        ]
        result[name] = {
            "n": len(pairs),
            "rho": spearman_rho(
                [pair[0] for pair in pairs], [pair[1] for pair in pairs]
            ),
        }
    return result


def _median_or_none(rows: list[dict[str, Any]], column: str) -> float | None:
    values = _values(rows, column)
    return statistics.median(values) if values else None


def _orbit_bias_check(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    known_orbits = sorted(
        {
            int(row["s1_canonical_relative_orbit"])
            for row in rows
            if row.get("s1_canonical_relative_orbit") is not None
        }
    )
    by_orbit: dict[str, Any] = {}
    for orbit in known_orbits:
        selected = [
            row for row in rows if row.get("s1_canonical_relative_orbit") == orbit
        ]
        by_orbit[str(orbit)] = {
            "count": len(selected),
            "vv_sigma0_db_median": _median_or_none(selected, "s1_vv_sigma0_db"),
            "vh_sigma0_db_median": _median_or_none(selected, "s1_vh_sigma0_db"),
            "vh_minus_vv_db_median": _median_or_none(
                selected, "s1_vh_minus_vv_db"
            ),
        }

    comparisons_by_class: dict[str, list[dict[str, Any]]] = {}
    low_sample_comparison = False
    metric_columns = {
        "vv_sigma0_db": "s1_vv_sigma0_db",
        "vh_sigma0_db": "s1_vh_sigma0_db",
        "vh_minus_vv_db": "s1_vh_minus_vv_db",
    }
    for vegetation_class in VegetationClass:
        selected = [
            row
            for row in rows
            if row.get("vegetation_class") == vegetation_class.value
            and row.get("s1_canonical_relative_orbit") is not None
        ]
        represented_orbits = sorted(
            {int(row["s1_canonical_relative_orbit"]) for row in selected}
        )
        comparisons_for_class: list[dict[str, Any]] = []
        for orbit_a, orbit_b in combinations(represented_orbits, 2):
            rows_a = [
                row for row in selected if row["s1_canonical_relative_orbit"] == orbit_a
            ]
            rows_b = [
                row for row in selected if row["s1_canonical_relative_orbit"] == orbit_b
            ]
            low_sample_comparison |= min(len(rows_a), len(rows_b)) < 3
            metric_differences: dict[str, Any] = {}
            for metric, column in metric_columns.items():
                median_a = _median_or_none(rows_a, column)
                median_b = _median_or_none(rows_b, column)
                metric_differences[metric] = {
                    "median_a": median_a,
                    "median_b": median_b,
                    "median_difference": (
                        median_a - median_b
                        if median_a is not None and median_b is not None
                        else None
                    ),
                }
            comparisons_for_class.append(
                {
                    "orbit_a": orbit_a,
                    "orbit_b": orbit_b,
                    "n_a": len(rows_a),
                    "n_b": len(rows_b),
                    "metrics": metric_differences,
                }
            )
        comparisons_by_class[vegetation_class.value] = comparisons_for_class
    return (
        {
            "by_canonical_relative_orbit": by_orbit,
            "within_vegetation_class_comparisons": comparisons_by_class,
            "automatic_correction_applied": False,
        },
        low_sample_comparison,
    )


def _warnings(
    rows: list[dict[str, Any]],
    correlations: dict[str, dict[str, Any]],
    *,
    low_sample_orbit_comparison: bool,
) -> list[str]:
    warnings: list[str] = []
    for vegetation_class in VegetationClass:
        count = sum(
            row.get("vegetation_class") == vegetation_class.value for row in rows
        )
        if count < 3:
            warnings.append(f"class_sample_size_very_low:{vegetation_class.value}")
        elif count < 5:
            warnings.append(f"class_sample_size_low:{vegetation_class.value}")
    for feature, column in FEATURES.items():
        if len(_values(rows, column)) < len(rows):
            warnings.append(f"feature_missing_data:{feature}")
    if low_sample_orbit_comparison:
        warnings.append("orbit_comparison_sample_size_low")
    for name, result in correlations.items():
        if result["n"] < MIN_CORRELATION_PAIRS:
            warnings.append(f"correlation_sample_size_low:{name}")
    return warnings


def build_validation_benchmark(raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _normalize_rows(raw_rows)
    performance, disagreements = _sentinel2_performance(rows)
    pairwise = _pairwise_separation(rows)
    correlations = _correlations(rows)
    orbit_bias, low_sample_orbit = _orbit_bias_check(rows)
    return {
        "dataset": _dataset_overview(rows),
        "class_statistics": _group_statistics(
            rows, (item.value for item in VegetationClass), "vegetation_class"
        ),
        "maintenance_statistics": _group_statistics(
            rows, (item.value for item in MaintenanceTruth), "maintenance_truth"
        ),
        "sentinel2_performance": performance,
        "disagreements": disagreements,
        "pairwise_separation": pairwise,
        "feature_summary": _feature_summary(rows, pairwise),
        "s1_s2_correlations": correlations,
        "orbit_bias_check": orbit_bias,
        "warnings": _warnings(
            rows,
            correlations,
            low_sample_orbit_comparison=low_sample_orbit,
        ),
        "methodology": {
            "purpose": "Exploratory descriptive benchmark only; no operational rule is produced.",
            "null_handling": "Null, non-numeric, NaN, and infinite values are excluded per feature and never replaced by zero.",
            "quantiles": "Q25 and Q75 use linear interpolation at position (n - 1) * q.",
            "standard_deviation": "Sample standard deviation (n - 1); null when n < 2.",
            "cliffs_delta": "(number of a>b pairs - number of a<b pairs) / (n_a * n_b).",
            "effect_magnitude_thresholds": {
                "negligible": "abs(delta) < 0.147",
                "small": "0.147 <= abs(delta) < 0.33",
                "medium": "0.33 <= abs(delta) < 0.474",
                "large": "abs(delta) >= 0.474",
            },
            "pairwise_minimum": "At least two finite observations in each class.",
            "iqr_overlap": {
                "separated": "The IQR intervals do not intersect.",
                "strong_overlap": "The intervals intersect and at least one group median lies inside the other group IQR.",
                "partial_overlap": "The intervals intersect but neither median lies inside the other IQR.",
            },
            "spearman": "Pearson correlation of average ranks with ties; rho is null for n < 3 or constant ranks.",
            "sentinel2_binary_evaluation": "Only cut/no_cut truth and cortar/nao_cortar predictions enter accuracy; inconclusivo is counted separately.",
            "s1_availability": "S1 is present when source status is available; calibrated S1 requires a finite canonical VV or VH sigma0 dB value.",
            "sample_size": {
                "very_low": "n < 3",
                "low": "3 <= n < 5",
                "exploratory": "n >= 5; this is not definitive scientific validation.",
            },
            "orbit_bias": "Canonical-orbit medians are reported globally and within vegetation class; no correction is applied.",
            "p_values": "No p-value hypothesis test is used to validate a feature.",
        },
    }

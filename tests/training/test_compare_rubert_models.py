from __future__ import annotations

import numpy as np
import pytest

from scripts.training.compare_rubert_models import (
    _filter_holdout,
    _metrics,
    _normalized_digest,
    _percentile,
)


def test_filter_holdout_removes_normalized_text_overlap() -> None:
    rows = [{"text": " Same   TEXT "}, {"text": "unique"}]

    filtered, excluded = _filter_holdout(rows, {_normalized_digest("same text")})

    assert filtered == [{"text": "unique"}]
    assert excluded == 1


def test_metrics_include_per_label_false_positive_and_false_negative_counts() -> None:
    probabilities = np.asarray([[0.9, 0.2], [0.8, 0.9], [0.1, 0.2]])
    labels = np.asarray([[1, 0], [0, 1], [0, 1]])

    result = _metrics(
        probabilities,
        labels,
        np.asarray([0.5, 0.5]),
        ["SAFE", "HATE"],
    )

    assert result["per_label"]["SAFE"]["false_positives"] == 1
    assert result["per_label"]["SAFE"]["false_negatives"] == 0
    assert result["per_label"]["HATE"]["false_positives"] == 0
    assert result["per_label"]["HATE"]["false_negatives"] == 1
    assert result["exact_match"] == pytest.approx(1 / 3)


def test_percentile_interpolates_between_nearest_values() -> None:
    assert _percentile([10.0, 20.0, 30.0], 0.95) == pytest.approx(29.0)

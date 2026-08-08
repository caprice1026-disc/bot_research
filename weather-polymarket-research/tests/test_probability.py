import pytest

from weather_research.probability import (
    Bucket,
    ensemble_bucket_probability,
    gaussian_bucket_probability,
    normalize_distribution,
)


def test_inclusive_bucket_counts_both_endpoints() -> None:
    bucket = Bucket(lower=81.0, upper=82.0, lower_inclusive=True, upper_inclusive=True)

    assert ensemble_bucket_probability([81.0, 82.0, 82.0, 83.0], bucket) == pytest.approx(0.75)


def test_exclusive_upper_bucket_does_not_count_upper_endpoint() -> None:
    bucket = Bucket(lower=81.0, upper=83.0, lower_inclusive=True, upper_inclusive=False)

    assert ensemble_bucket_probability([81.0, 82.0, 83.0], bucket) == pytest.approx(2 / 3)


def test_gaussian_bucket_probability_uses_normal_interval() -> None:
    bucket = Bucket(lower=81.0, upper=83.0, lower_inclusive=True, upper_inclusive=True)

    assert gaussian_bucket_probability(82.0, 1.0, bucket) == pytest.approx(0.682689, rel=1e-5)


def test_normalize_distribution_sums_probabilities_to_one() -> None:
    assert normalize_distribution({"low": 0.2, "high": 0.3}) == {
        "low": pytest.approx(0.4),
        "high": pytest.approx(0.6),
    }


def test_probability_inputs_must_be_valid() -> None:
    with pytest.raises(ValueError, match="standard_deviation"):
        gaussian_bucket_probability(82.0, 0.0, Bucket(lower=81.0, upper=83.0))

    with pytest.raises(ValueError, match="positive total"):
        normalize_distribution({"low": 0.0, "high": 0.0})

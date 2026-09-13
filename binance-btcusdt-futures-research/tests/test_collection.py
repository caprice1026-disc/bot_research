from datetime import date
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from btc_regime_eda.collection import months_covering_range


def test_months_covering_range_includes_partial_final_month():
    assert list(months_covering_range(date(2025, 8, 17), date(2026, 8, 17)))[-1] == date(2026, 8, 1)


def test_months_covering_range_excludes_month_start_end_boundary():
    months = list(months_covering_range(date(2025, 8, 17), date(2026, 8, 1)))
    assert months[-1] == date(2026, 7, 1)
    assert date(2026, 8, 1) not in months

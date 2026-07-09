from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class CheckResult:
    check_type: str
    severity: str
    dimensions: dict[str, Any]
    expected: Decimal
    actual: Decimal
    title: str
    evidence: dict[str, Any] = field(default_factory=dict)


def find_missing_dates(previous: pd.DataFrame, current: pd.DataFrame) -> list[CheckResult]:
    previous_dates = set(pd.to_datetime(previous["data_date"]).dt.date)
    current_dates = set(pd.to_datetime(current["data_date"]).dt.date)
    return [
        CheckResult(
            check_type="missing_date",
            severity="CRITICAL",
            dimensions={"data_date": missing.isoformat()},
            expected=Decimal(1),
            actual=Decimal(0),
            title=f"Полностью пропала дата {missing.isoformat()}",
        )
        for missing in sorted(previous_dates - current_dates)
    ]


def find_metric_drops(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    keys: list[str],
    metric: str,
    relative_threshold: float = 0.20,
    absolute_threshold: float = 10,
) -> list[CheckResult]:
    old = previous.groupby(keys, dropna=False)[metric].sum().rename("expected")
    new = current.groupby(keys, dropna=False)[metric].sum().rename("actual")
    comparison = old.to_frame().join(new, how="left").fillna({"actual": 0})
    results: list[CheckResult] = []
    for dimension_values, row in comparison.iterrows():
        expected = Decimal(str(row["expected"]))
        actual = Decimal(str(row["actual"]))
        drop = expected - actual
        if expected <= 0:
            continue
        relative_drop = drop / expected
        if drop >= Decimal(str(absolute_threshold)) and relative_drop >= Decimal(
            str(relative_threshold)
        ):
            values = (
                dimension_values
                if isinstance(dimension_values, tuple)
                else (dimension_values,)
            )
            dimensions = {
                key: value.isoformat() if isinstance(value, date) else str(value)
                for key, value in zip(keys, values, strict=True)
            }
            results.append(
                CheckResult(
                    check_type=f"{metric}_drop",
                    severity="HIGH",
                    dimensions=dimensions,
                    expected=expected,
                    actual=actual,
                    title=f"{metric}: снижение на {relative_drop:.1%}",
                    evidence={"absolute_drop": str(drop), "relative_drop": str(relative_drop)},
                )
            )
    return results

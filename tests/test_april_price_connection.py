from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.april_price_connection import distribution, require_join, signed_transfer_envelope


def test_price_join_preserves_original_merge_flag_and_rejects_missing_or_growth():
    left = pd.DataFrame({"key": [1, 2], "_merge": ["source_a", "source_b"]})
    right = pd.DataFrame({"key": [1, 2], "price": [Decimal("0.01"), Decimal("10.00")]})
    actual = require_join(left, right, ["key"], validate="one_to_one")
    assert actual._merge.tolist() == left._merge.tolist()
    assert actual.price.tolist() == right.price.tolist()
    with pytest.raises(ValueError):
        require_join(left, right.iloc[:1], ["key"], validate="one_to_one")
    with pytest.raises(pd.errors.MergeError):
        require_join(left, pd.concat([right, right]), ["key"], validate="one_to_one")


def test_empty_price_group_is_not_zero_and_unknown_is_not_silently_dropped():
    assert distribution(pd.Series([], dtype=float)) == dict(
        count=0, minimum=None, q25=None, median=None, q75=None, maximum=None
    )
    a = distribution(pd.Series([Decimal("0.01"), Decimal("1.00"), Decimal("100.00")]))
    assert a["count"] == 3 and a["median"] == 1 and a["minimum"] == 0.01 and a["maximum"] == 100
    with pytest.raises(ValueError):
        distribution(pd.Series([None, 10]))


def test_total_envelope_is_not_additional_margin_and_preserves_reverse_sign():
    a = signed_transfer_envelope(
        Decimal(-900), Decimal(-663), Decimal(0), Decimal(50), Decimal(-287)
    )
    assert a["model_bound"] == -237 and a["available_identity_matches"]
    b = signed_transfer_envelope(
        Decimal(1000), Decimal(100), Decimal(50), Decimal(400), Decimal(450)
    )
    assert b["model_bound"] == 850 and b["available_identity_matches"]
    assert b["model_bound"] != 450


def test_price_connection_keeps_all_scenarios_but_only_12960_unique_prices():
    root = Path(__file__).resolve().parents[1]
    out = root / "data/interim/occto/april_price_connection/20261027_price_v3"
    if not (out / "manifest.json").exists():
        pytest.skip("ローカル比較版が必要")
    p = pd.read_parquet(out / "april_price_observations.parquet")
    a = pd.read_parquet(out / "all_scenarios_with_spot_prices.parquet")
    assert len(p) == 12960 and not p.duplicated(["target_area", "delivery_date", "period"]).any()
    assert (
        len(a) == 440640
        and a.groupby(["target_area", "delivery_date", "period"]).size().eq(34).all()
    )
    assert a.groupby(["target_area", "delivery_date", "period"]).area_price.nunique().eq(1).all()
    assert set(a[a.target_area.isin(["東京", "中部"])].entity_code) == {"jera"}
    s = pd.read_parquet(out / "hokkaido_supply_set_reconciliation.parquet")
    assert s.scatter_capacity_mw.sum() == 462 and s.trial_upper_at_example_mw.sum() == 250
    assert s.exclusion_due_to_fuel_scope.sum() == 2 and not s.automatic_reinstatement.any()

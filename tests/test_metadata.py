"""メタデータの不整合が検出されることを検証する。"""

from copy import deepcopy

import pytest
from jsonschema import ValidationError

from scripts.validate_metadata import load_metadata, validate_metadata


@pytest.fixture
def metadata():
    return deepcopy(load_metadata())


def test_catalog_and_dictionary_are_consistent(metadata):
    report = validate_metadata(*metadata)
    assert report["ソース数"] == 10
    assert report["未確定事項"]
    assert report["実データ検証"] == "未実施"


@pytest.mark.parametrize(
    ("field", "value"),
    [("timezone", "UTC"), ("periods_per_day", 24), ("period_minutes", 60),
     ("start", "2025-01-01"), ("end", "2026-04-01")],
)
def test_research_time_contract_cannot_change_silently(metadata, field, value):
    catalog, variables, schema = metadata
    catalog["baseline"][field] = value
    with pytest.raises(ValidationError):
        validate_metadata(catalog, variables, schema)


def test_duplicate_source_is_rejected(metadata):
    catalog, variables, schema = metadata
    catalog["sources"].append(deepcopy(catalog["sources"][0]))
    with pytest.raises(ValueError, match="ソースID"):
        validate_metadata(catalog, variables, schema)


def test_duplicate_variable_is_rejected(metadata):
    catalog, variables, schema = metadata
    variables.append(deepcopy(variables[0]))
    with pytest.raises(ValueError, match="変数キー重複"):
        validate_metadata(catalog, variables, schema)


@pytest.mark.parametrize("unit", ["not_applicable", "", "KW"])
def test_missing_or_invalid_quantity_unit_is_rejected(metadata, unit):
    catalog, variables, schema = metadata
    next(row for row in variables if row["kind"] == "quantity")["unit"] = unit
    with pytest.raises(ValueError):
        validate_metadata(catalog, variables, schema)


def test_unknown_source_reference_is_rejected(metadata):
    catalog, variables, schema = metadata
    variables[0]["source_id"] = "unknown"
    with pytest.raises(ValueError, match="不明なソース"):
        validate_metadata(catalog, variables, schema)


@pytest.mark.parametrize("field", ["variables", "primary_key_candidate"])
def test_unknown_column_reference_is_rejected(metadata, field):
    catalog, variables, schema = metadata
    catalog["sources"][0][field].append("unknown")
    with pytest.raises(ValueError, match="辞書にない"):
        validate_metadata(catalog, variables, schema)


def test_validation_only_source_remains_separate(metadata):
    catalog, _, _ = metadata
    sources = {source["source_id"]: source for source in catalog["sources"]}
    assert sources["high_price_validation"]["usage"] == "validation_only"


def test_unverified_units_are_explicit_todos(metadata):
    _, variables, _ = metadata
    assert all(row["unit"] == "TODO" for row in variables
               if row["source_id"] == "occto_demand")

"""実データを読み込まず、カタログを検証し未確定事項を出力する。"""

import csv
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SOURCE_IDS = {
    "jepx_spot", "jepx_price_sensitivity", "occto_generation", "hjks_unit",
    "hjks_outage", "occto_demand", "occto_reserve", "occto_interconnector",
    "regulatory_entities", "high_price_validation",
}
UNITS = {"TODO", "not_applicable", "kW", "MW", "kWh", "MWh", "JPY/kWh", "%", "ratio"}


def validate_metadata(catalog, variables, schema):
    """構造・参照を検証し、TODOと取得未確認状態を日本語で返す。"""
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(catalog)
    ids = [source["source_id"] for source in catalog["sources"]]
    if len(ids) != len(set(ids)) or set(ids) != SOURCE_IDS:
        raise ValueError("ソースIDが重複、欠落、または収集仕様A〜Jと不一致")
    required = {
        "source_id", "variable_name", "definition", "kind", "unit",
        "source_unit", "source_column", "status", "notes",
    }
    keys = set()
    todos = []
    for row in variables:
        if set(row) != required or any(not isinstance(v, str) or not v for v in row.values()):
            raise ValueError("変数辞書の列または必須値が不正")
        key = (row["source_id"], row["variable_name"])
        if key in keys:
            raise ValueError(f"変数キー重複: {key}")
        keys.add(key)
        if row["source_id"] not in SOURCE_IDS | {"common", "derived"}:
            raise ValueError(f"不明なソース参照: {key}")
        if row["kind"] not in {"quantity", "string", "date", "datetime", "integer", "boolean"}:
            raise ValueError(f"不正な変数型: {key}")
        if row["unit"] not in UNITS or row["source_unit"] not in UNITS:
            raise ValueError(f"不正な単位: {key}")
        if row["kind"] == "quantity" and row["unit"] == "not_applicable":
            raise ValueError(f"数量に単位が必要: {key}")
        if row["status"] not in {"TODO", "TODO_definition", "specified", "web_unit_confirmed"}:
            raise ValueError(f"不正な確認状態: {key}")
        if any(value.startswith("TODO") for value in row.values()):
            todos.append(f"{key[0]}.{key[1]}: 列名={row['source_column']}, "
                         f"元単位={row['source_unit']}, 状態={row['status']}")
    for source in catalog["sources"]:
        for name in source["variables"]:
            if (source["source_id"], name) not in keys:
                raise ValueError(f"辞書にない変数参照: {source['source_id']}.{name}")
        for name in source["primary_key_candidate"]:
            if (source["source_id"], name) not in keys and ("common", name) not in keys:
                raise ValueError(f"辞書にない主キー候補: {source['source_id']}.{name}")
        todos.extend(f"{source['source_id']}: {todo}" for todo in source["todos"])
    return {"ソース数": len(ids), "変数数": len(variables), "未確定事項": todos,
            "実データ検証": "未実施"}


def load_metadata(root=ROOT):
    metadata = root / "metadata"
    catalog = yaml.safe_load((metadata / "data_sources.yml").read_text(encoding="utf-8"))
    schema = json.loads((metadata / "catalog.schema.json").read_text(encoding="utf-8"))
    with (metadata / "variable_dictionary.csv").open(encoding="utf-8", newline="") as handle:
        variables = list(csv.DictReader(handle))
    return catalog, variables, schema


if __name__ == "__main__":
    print(json.dumps(validate_metadata(*load_metadata()), ensure_ascii=False, indent=2))

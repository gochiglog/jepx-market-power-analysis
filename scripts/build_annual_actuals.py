"""保有一次CSV108地域月を年間D/R台帳へ。原列/空欄/区間契約を保持。"""

import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.actual_net_demand import clock_keys, numeric_raw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/interim/occto/annual_diagnostic/20261027_inputs_v2"
REPORT = ROOT / "reports/27_fy2025_annual/qa"
MAPPING = {
    "hokkaido": "北海道",
    "tohoku": "東北",
    "tokyo": "東京",
    "hokuriku": "北陸",
    "kansai": "関西",
    "chugoku": "中国",
    "shikoku": "四国",
    "kyushu": "九州",
}
FIELDS = {
    "demand_actual_sample_mw": "エリア需要",
    "pv_actual_sample_mw": "太陽光発電実績",
    "wind_actual_sample_mw": "風力発電実績",
    "nuclear_regional_sample_mw": "原子力",
    "pv_curtailment_sample_mw": "太陽光出力制御量",
    "wind_curtailment_sample_mw": "風力出力制御量",
}


def decode_csv(data):
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return list(csv.reader(io.StringIO(text, newline=""))), encoding
    raise ValueError("文字コードを厳密復号できない")


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run():
    if OUTPUT.exists():
        raise ValueError("既存入力版を上書きしない")
    OUTPUT.mkdir(parents=True)
    (OUTPUT / "original_rows").mkdir()
    records = [
        json.loads(x)
        for x in (ROOT / "metadata/research/annual_actual_acquisition_20261027.jsonl")
        .read_text()
        .splitlines()
    ]
    got = {(r["area_key"], r["month"]): r for r in records if r["status"] == "downloaded"}
    assert len(got) == 88
    frames = []
    audits = []

    def parse(area, month, path, data, member=None):
        rows, encoding = decode_csv(data)
        assert "MW" in rows[0][0], (area, month, rows[0])
        header = rows[1]
        assert len(header) == len(set(header))
        assert all(len(r) == len(header) for r in rows[2:])
        raw = pd.DataFrame(rows[2:], columns=header)
        tag = area + "_" + month
        orig = pa.Table.from_pandas(raw, preserve_index=False)
        op = OUTPUT / "original_rows" / (tag + ".parquet")
        pq.write_table(orig, op, compression="zstd")
        assert pq.read_table(op).equals(orig)
        padding = raw.eq("").all(axis=1)
        active = raw.loc[~padding].reset_index(names="original_index")
        keys = clock_keys(active.DATE, active.TIME, role="end" if area == "九州" else "start")
        frame = pd.concat([active[header], keys], axis=1)
        assert pd.to_datetime(frame.delivery_date).dt.strftime("%Y%m").eq(month).all(), (
            area,
            month,
        )
        frame["area"] = area
        frame["source_file"] = str(path.relative_to(ROOT))
        frame["source_sha256"] = sha(path)
        frame["source_zip_member"] = member
        frame["source_row"] = active.original_index + 3
        frame["clock_role_contract"] = "end" if area == "九州" else "start"
        for target, source in FIELDS.items():
            if source not in frame:
                raise ValueError((area, month, "列変更", source))
            frame[target] = frame[source].map(numeric_raw)

        def total(*xs):
            return None if any(x is None for x in xs) else sum(xs)

        frame["renewable_actual_sample_mw"] = [
            total(a, b)
            for a, b in zip(frame.pv_actual_sample_mw, frame.wind_actual_sample_mw, strict=True)
        ]
        frame["fixed_actual_mw"] = [
            total(a, b, c)
            for a, b, c in zip(
                frame.pv_actual_sample_mw,
                frame.wind_actual_sample_mw,
                frame.nuclear_regional_sample_mw,
                strict=True,
            )
        ]
        frame["net_demand_B_mw"] = [
            None if d is None or r is None else d - r
            for d, r in zip(frame.demand_actual_sample_mw, frame.fixed_actual_mw, strict=True)
        ]
        for target, source in [
            ("pumped_net_injection_mw", "揚水"),
            ("battery_net_injection_mw", "蓄電池"),
        ]:
            frame[target] = frame[source].map(numeric_raw) if source in frame else None
        frame["net_import_from_actual_csv_mw"] = [
            None if v is None else v * (-1 if area == "九州" else 1)
            for v in frame["連系線"].map(numeric_raw)
        ]
        frame["accounting_scenario"] = "published_demand_only_no_observed_charging_obligation"
        assert not frame.duplicated(["area", "delivery_date", "period"]).any()
        assert (
            frame.groupby("delivery_date").period.apply(lambda s: set(s) == set(range(1, 49))).all()
        )
        assert frame[header].equals(active[header])
        audits.append(
            dict(
                area=area,
                month=month,
                rows=len(frame),
                original_rows=len(raw),
                padding_rows=int(padding.sum()),
                encoding=encoding,
                unit_header=rows[0],
                columns=header,
                newline_contract=(
                    "CR_only" if b"\r" in data and b"\n" not in data else "LF_or_CRLF"
                ),
                header_sha256=hashlib.sha256(
                    json.dumps(header, ensure_ascii=False).encode()
                ).hexdigest(),
                source_file=str(path.relative_to(ROOT)),
                source_member=member,
                source_sha256=sha(path),
                missing_D=int(frame.demand_actual_sample_mw.isna().sum()),
                missing_R=int(frame.fixed_actual_mw.isna().sum()),
                clock_role=frame.clock_role_contract.iloc[0],
                original_all_cells_equal=True,
            )
        )
        frames.append(frame)

    refs = ROOT / "data/interim/research_reference/20261026_actual_supply_v1"
    for key, area in MAPPING.items():
        april = (
            refs / (key + "_202504.csv")
            if key != "tokyo"
            else ROOT
            / "data/interim/research_reference/20261026_R_feasibility_v1/tokyo_actual_202504.csv"
        )
        parse(area, "202504", april, april.read_bytes())
        for (a, m), r in sorted(got.items()):
            if a == key:
                path = ROOT / r["path"]
                assert sha(path) == r["sha256"]
                parse(area, m, path, path.read_bytes())
    path = ROOT / "data/interim/research_reference/20261026_april_balance_v1/eria_jukyu_2025.zip"
    with zipfile.ZipFile(path) as z:
        assert len(z.namelist()) == 12
        for member in sorted(z.namelist()):
            parse("中部", member[11:17], path, z.read(member), member)
    data = pd.concat(frames, ignore_index=True)
    assert len(data) == 157680 and not data.duplicated(["area", "delivery_date", "period"]).any()
    assert data.groupby("area").size().eq(17520).all()
    old = pq.read_table(
        ROOT
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    ).to_pandas()
    k = ["area", "delivery_date", "period"]
    comparison = old.merge(
        data, on=k, how="left", validate="one_to_one", indicator=True, suffixes=("_old", "_annual")
    )
    assert comparison._merge.eq("both").all()
    for col in list(FIELDS) + ["fixed_actual_mw", "net_demand_B_mw"]:
        assert comparison[col + "_old"].equals(comparison[col + "_annual"]), col
    table = pa.Table.from_pandas(data, preserve_index=False)
    path = OUTPUT / "annual_primary_input_ledger.parquet"
    pq.write_table(table, path, compression="zstd")
    assert pq.read_table(path).equals(table)
    pd.DataFrame(audits).to_json(
        REPORT / "annual_input_file_contracts.json", orient="records", force_ascii=False, indent=2
    )
    pd.DataFrame(audits).drop(columns=["columns", "unit_header"]).to_csv(
        REPORT / "annual_input_month_qa.csv", index=False
    )
    result = dict(
        rows=len(data),
        times=17520,
        area_time_keys=157680,
        duplicate_keys=0,
        day_periods=48,
        april_join_rate=1.0,
        april_input_values_unchanged=True,
        missing_D=int(data.demand_actual_sample_mw.isna().sum()),
        missing_R=int(data.fixed_actual_mw.isna().sum()),
        source_area_months=len(audits),
        new_downloads=88,
        april_or_chubu_redownloaded=False,
        outputs=[dict(path=str(path.relative_to(ROOT)), sha256=sha(path))],
        new_reference_manifest="metadata/research/annual_actual_acquisition_20261027.jsonl",
    )
    (REPORT / "annual_input_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(result)


if __name__ == "__main__":
    run()

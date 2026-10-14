"""保存済みinterimをraw・時刻契約・QAと再照合する（出力の書き換えなし）。"""

import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.reorganize_raw import relocated_path  # noqa: E402
from src.raw_acquisition import detect_encoding  # noqa: E402
from src.time_normalization import (  # noqa: E402
    CONTRACTS,
    TimeAudit,
    demand_key,
    fiscal_dates,
    output_filename,
    parse_date,
    period_from_label,
    sha256,
    time_key,
    wide_time_key,
)


def verify(report_path: Path, root: Path) -> dict:
    report = json.loads(report_path.read_text())
    records = {
        r["local_path"]: r
        for r in map(
            json.loads, (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
        )
    }
    audits = {v: TimeAudit(fiscal_dates(report["fiscal_year"])) for v in CONTRACTS}
    rows = 0
    for item in report["files"]:
        original = Path(item["input_path"])
        raw = root / relocated_path(root, str(original.relative_to(root)))
        output = Path(item["output_path"])
        record = records[str(raw.relative_to(root))]
        assert sha256(raw) == item["input_sha256"] == record["sha256"]
        assert sha256(output) == item["output_sha256"]
        assert output.name == output_filename(record)
        c = CONTRACTS[item["variant"]]
        text, _ = detect_encoding(raw.read_bytes())
        source = csv.reader(io.StringIO(text, newline=""))
        with output.open(encoding="utf-8-sig", newline="") as stream:
            dest = csv.reader(stream)
            for _ in range(c.preamble_rows):
                assert next(source) == next(dest)
            headers = [next(source) for _ in range(c.header_rows)]
            for header in headers:
                assert next(dest)[: len(header)] == header
            names = headers[-1]
            date_index = names.index(c.date_column)
            time_index = names.index(c.time_column) if c.time_column else None
            count = 0
            for number, row in enumerate(source, c.preamble_rows + c.header_rows + 1):
                result = next(dest)
                assert result[: len(row)] == row
                day = parse_date(row[date_index])
                group = tuple(row[names.index(n)] for n in c.group_columns)
                if c.reference == "wide_end":
                    key, kind, slots = wide_time_key(day), "wide_30min", ("wide_48",)
                elif c.reference == "hourly":
                    label = row[time_index]
                    key, kind = demand_key(day, label)
                    slots = (int(label[:2]),) if kind == "hourly" else (label,)
                else:
                    p = (
                        int(row[time_index])
                        if c.reference == "period"
                        else period_from_label(row[time_index], reference=c.reference)
                    )
                    key, kind, slots = time_key(day, p), "30min", (p,)
                assert result[len(row) :] == [str(x) for x in key] + [
                    kind,
                    record["sha256"],
                    str(number),
                ]
                audits[item["variant"]].observe(day, group, slots, kind)
                count += 1
            assert next(dest, None) is None and count == item["rows"]
            rows += count
    for variant, audit in audits.items():
        assert audit.result() == report["sources"][variant]
        assert audit.result()["passed"]
    assert rows == report["rows"]
    assert audits["spot"].rows == 17520
    return {
        "files": len(report["files"]),
        "rows": rows,
        "qa_matches": True,
        "raw_unchanged": True,
        "jepx_periods": audits["spot"].rows,
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2]).resolve()), ensure_ascii=False))

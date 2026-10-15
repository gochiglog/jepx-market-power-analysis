"""公開系統情報サービスの翌々日・策定の空容量。原形式で取得・検査する。"""

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from src.raw_acquisition import FY_END, FY_START, date_range, detect_encoding, fetch
from src.source_downloads import session

URL = "https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/CF01S010C"
SOURCE_ID = "occto_system_interconnector_capacity"
DIRECTORY = "occto/system_interconnector_capacity/dayahead2/planned"


def week_ranges():
    start = FY_START
    while start <= FY_END:
        end = min(start + timedelta(days=6), FY_END)
        yield start.isoformat(), end.isoformat()
        start = end + timedelta(days=1)


def validate_capacity(data, start, end):
    text, _ = detect_encoding(data)
    reader = csv.DictReader(io.StringIO(text))
    required = {"対象断面", "策定／更新後", "策定日", "連系線", "年月日", "時刻", "方向", "空容量"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("空容量CSVの必須列がない")
    groups = defaultdict(Counter)
    values = Counter()
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("空容量CSVの列数不一致")
        if row["対象断面"] != "翌々日" or row["策定／更新後"] != "策定":
            raise ValueError("対象断面・策定版が要求と異なる")
        if row["方向"] not in {"順方向", "逆方向"}:
            raise ValueError("空容量の方向が不明")
        day = row["年月日"].replace("/", "-")
        if row["策定日"] != (date.fromisoformat(day) - timedelta(days=2)).strftime("%Y%m%d"):
            raise ValueError("翌々日版の策定日が対象日の2日前ではない")
        groups[(day, row["連系線"], row["方向"])][row["時刻"]] += 1
        values[row["空容量"]] += 1
    days = {key[0] for key in groups}
    expected = set(date_range(date.fromisoformat(start), date.fromisoformat(end)))
    if days != expected:
        raise ValueError(f"空容量の対象日不一致: 未収録日={sorted(expected - days)}")
    times = {f"{i // 2:02}:{i % 2 * 30:02}" for i in range(1, 49)}
    for key, counts in groups.items():
        if set(counts) != times or any(n != 1 for n in counts.values()):
            raise ValueError(f"空容量の48コマ/キー一意性不一致: {key}")
        if (key[0], key[1], "逆方向" if key[2] == "順方向" else "順方向") not in groups:
            raise ValueError(f"空容量の両方向が揃わない: {key}")
    return {
        "capacity_audit": {
            "date_count": len(days),
            "group_count": len(groups),
            "row_count": sum(sum(c.values()) for c in groups.values()),
            "all_groups_have_48_unique_periods": True,
            "both_directions_present": True,
            "interconnectors": sorted({k[1] for k in groups}),
            "non_numeric_capacity_value_counts": {
                v: n
                for v, n in values.items()
                if not v.replace("-", "", 1).replace(".", "", 1).isdigit()
            },
        }
    }


def download_capacity(start, end):
    opener = session()
    base = URL.rsplit("/", 1)[0] + "/"
    fetch(base + "LOGIN_login", opener=opener)
    fetch(URL + "?fwExtention.pathInfo=CF01S010C", {}, opener=opener)
    fields = {
        "fwExtention.actionType": "reference",
        "fwExtention.pathInfo": "CF01S010C",
        "fwExtention.prgbrh": "0",
        "fwExtention.formId": "CF01S010P",
        "transitionContextKey": "DEFAULT",
    }

    def ajax(action):
        fields["fwExtention.actionSubType"] = action
        data, _, _ = fetch(URL, fields, opener=opener, extra_headers={"sdReqType": "AJAX"})
        result = json.loads(data)["root"]
        if result.get("errMessage") or result.get("interceptorErr") or result.get("errFields"):
            raise ValueError("公開画面がCSV取得の検証エラーを返した")
        for key, value in result.get("bizRoot", {}).get("header", {}).items():
            if isinstance(value, dict) and "value" in value:
                fields[key] = value["value"] or ""

    ajax("initDisplay")
    fields.update(
        {
            "tabSntk": "0",
            "rklDataKnd": "05",
            "dvlSlashLblUpdaf": "1",
            "rklNngpFrom": start.replace("-", "/"),
            "rklNngpTo": end.replace("-", "/"),
            "allTgtRklSectDwld": "Y",
        }
    )
    fields.update({f"rkl{i}": f"{i:02}" for i in range(1, 16)})
    ajax("print")
    ajax("ok")
    fields["fwExtention.actionSubType"] = "download"
    data, headers, final = fetch(URL, fields, opener=opener)
    # 記録は実際の選択条件だけ。セッション・token・動的hidden値は出力しない。
    parameters = {
        k: v
        for k, v in fields.items()
        if k
        in {
            "tabSntk",
            "rklDataKnd",
            "dvlSlashLblUpdaf",
            "rklNngpFrom",
            "rklNngpTo",
            "allTgtRklSectDwld",
        }
        or (k.startswith("rkl") and k[3:].isdigit())
    }
    return data, headers, final, parameters

"""承認済み未保有88地域月だけを取得。原バイト・HTTP・SHAを保持し再開する。"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCES = {
    "hokkaido": "https://www.hepco.co.jp/network/con_service/public_document/supply_demand_results/csv/eria_jukyu_{month}_01.csv",
    "tohoku": "https://setsuden.nw.tohoku-epco.co.jp/common/demand/eria_jukyu_{month}_02.csv",
    "tokyo": "https://www.tepco.co.jp/forecast/html/images/eria_jukyu_{month}_03.csv",
    "hokuriku": "https://www.rikuden.co.jp/nw/denki-yoho/csv/eria_jukyu_{month}_05.csv",
    "kansai": "https://www.kansai-td.co.jp/interchange/denkiyoho/area-performance/eria_jukyu_{month}_06.csv",
    "chugoku": "https://www.energia.co.jp/nw/jukyuu/sys/eria_jukyu_{month}_07.csv",
    "shikoku": "https://www.yonden.co.jp/nw/supply_demand/csv/eria_jukyu_{month}_08.csv",
    "kyushu": "https://www.kyuden.co.jp/td_area_jukyu/csv/eria_jukyu_{month}_09.csv",
}
MONTHS = [f"2025{x:02}" for x in range(5, 13)] + ["202601", "202602", "202603"]
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/interim/research_reference/20261027_annual_actuals_v1"
MANIFEST = ROOT / "metadata/research/annual_actual_acquisition_20261027.jsonl"


def acquire(area, month, url):
    now = datetime.now(timezone.utc).isoformat()
    record = dict(area_key=area, month=month, url=url, retrieved_at=now)
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        if b"<html" in r.content[:1000].lower():
            raise ValueError("CSVの代わりにHTML")
        path = DEST / area / url.split("/")[-1]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError("未登録の既存ファイルを上書きしない")
        path.write_bytes(r.content)
        record.update(
            status="downloaded",
            path=str(path.relative_to(ROOT)),
            sha256=hashlib.sha256(r.content).hexdigest(),
            bytes=len(r.content),
            http_status=r.status_code,
            final_url=r.url,
            http_metadata={
                k: r.headers.get(k)
                for k in [
                    "Content-Type",
                    "Content-Length",
                    "Last-Modified",
                    "ETag",
                    "Content-Disposition",
                ]
            },
        )
    except Exception as e:
        record.update(status="failed", error=str(e))
    return record


def main():
    old = [json.loads(x) for x in MANIFEST.read_text().splitlines()] if MANIFEST.exists() else []
    completed = {}
    for r in old:
        if r["status"] == "downloaded":
            data = (ROOT / r["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == r["sha256"]
            completed[r["area_key"], r["month"]] = r
    jobs = [
        (a, m, u.format(month=m))
        for a, u in SOURCES.items()
        for m in MONTHS
        if (a, m) not in completed
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = [pool.submit(acquire, *j) for j in jobs]
        for future in as_completed(tasks):
            record = future.result()
            with MANIFEST.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                record["area_key"],
                record["month"],
                record["status"],
                record.get("error", ""),
                flush=True,
            )


if __name__ == "__main__":
    main()

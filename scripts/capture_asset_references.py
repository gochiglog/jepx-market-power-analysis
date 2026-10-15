"""上位10コードに限る公式参考資料の追加取得。既存rawは上書きしない。"""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.raw_acquisition import detect_encoding  # noqa: E402
from src.source_downloads import curl_request  # noqa: E402

TARGETS = {
    "GB583": ("富津", "futtsu"),
    "GB573": ("川崎", "kawasaki"),
    "GB593": ("千葉", "chiba"),
    "GB4Q3": ("姉崎", "anegasaki"),
    "GB553": ("常陸那珂", "hitachinaka"),
    "GAZT3": ("五井", "goi"),
    "48193": ("西名古屋", "nishinagoya"),
    "GC5F3": ("五井", "goi"),
    "GV943": ("常陸那珂共同", "hitachinaka-kyodo"),
    "GAWA3": ("横須賀", "yokosuka"),
}


def capture(root, repo):
    stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%dT%H%M%S")
    out = root / "data/raw/reference/issue15" / stamp
    out.mkdir(parents=True, exist_ok=False)
    records = []

    def save(label, url, data, headers, fields=None):
        mime = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        ext = (
            ".pdf" if data.startswith(b"%PDF") else ".csv" if label.startswith("hjks_") else ".html"
        )
        path = out / (label + ext)
        with path.open("xb") as f:
            f.write(data)
        disposition = next(
            (v for k, v in headers.items() if k.lower() == "content-disposition"), ""
        )
        filename = re.search(r'filename="?([^";]+)', disposition)
        original_name = filename[1] if filename else url.rsplit("/", 1)[-1]
        record = dict(
            reference_id=label,
            source_url=url,
            local_path=str(path.relative_to(root)),
            downloaded_at=datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(),
            file_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            search_conditions=fields or {},
            http_metadata={
                k: v
                for k, v in headers.items()
                if k.lower() in ("content-type", "content-disposition", "last-modified", "etag")
            },
            mime_type=mime,
            saved_filename=path.name,
            original_filename=original_name,
            official_filename=disposition,
            purpose="設備対応根拠のみ。年度データの再取得ではない",
        )
        records.append(record)
        return record

    url = "https://hjks.jepx.or.jp/hjks/unit"
    with tempfile.TemporaryDirectory() as temporary:
        html, _, _ = curl_request(url, temporary)
        token = re.search(r'name="_csrf" value="([^"]+)"', html.decode()).group(1)
        action = re.search(r'<form method="post" action="([^"]+)"', html.decode()).group(1)
        submit_url = "https://hjks.jepx.or.jp/hjks/" + action
        for field, values in [
            ("plantcd", list(TARGETS)),
            ("name", sorted({v[0] for v in TARGETS.values()})),
        ]:
            for value in values:
                fields = dict(
                    csv="csv",
                    area="",
                    company="",
                    plantcd="",
                    name="",
                    format="",
                    unitname="",
                    _enddtFlg="on",
                )
                fields[field] = value
                search_html, _, _ = curl_request(
                    submit_url, temporary, {**fields, "csv": "", "_csrf": token}
                )
                token = re.search(r'name="_csrf" value="([^"]+)"', search_html.decode()).group(1)
                data, headers, _ = curl_request(submit_url, temporary, {**fields, "_csrf": token})
                decoded, encoding = detect_encoding(data)
                rows = list(csv.reader(io.StringIO(decoded)))
                if not rows or "発電所コード" not in rows[0] or len(rows) > 151:
                    raise ValueError("条件検索CSVではない応答。取得を停止")
                column = rows[0].index("発電所コード" if field == "plantcd" else "発電所名")
                if not all(value in row[column] for row in rows[1:]):
                    raise ValueError("検索条件が結果に反映されていない")
                record = save(f"hjks_{field}_{value}", url, data, headers, fields)
                record["data_rows"] = len(rows) - 1
                record["encoding"] = encoding
                record["request_sequence"] = [
                    "GET unit",
                    "POST unit csv=空欄（検索）",
                    "POST unit csv=csv（取得）",
                ]
                print(record["reference_id"], record["data_rows"], flush=True)
    for slug in sorted({v[1] for v in TARGETS.values()}):
        url = f"https://www.jera.co.jp/corporate/business/thermal-power/list/{slug}/"
        with tempfile.TemporaryDirectory() as temporary:
            data, headers, _ = curl_request(url, temporary)
            save("jera_" + slug, url, data, headers)
        # 発電所固有パンフレットのみ。Webが提供しているURLをそのまま使う。
        links = re.findall(r"href=[\"\x27]([^\"\x27]+\.pdf)[\"\x27]", data.decode())
        links = [u for u in links if f"/list/{slug}/" in u]
        for number, link in enumerate(dict.fromkeys(links)):
            if link.startswith("/"):
                link = "https://www.jera.co.jp" + link
            with tempfile.TemporaryDirectory() as temporary:
                pdf, headers, _ = curl_request(link, temporary)
                save(f"jera_{slug}_pamphlet_{number}", link, pdf, headers)
        print("jera", slug, flush=True)
    path = repo / f"metadata/research/asset_reference_manifest_{stamp}.json"
    with path.open("x") as stream:
        stream.write(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print("saved", len(records))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    capture(args.data_root, Path(__file__).resolve().parents[1])

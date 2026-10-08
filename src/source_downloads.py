"""確認した公式フォームと同じ操作でサンプル/元ファイルを取得する。"""

import http.cookiejar
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from src.raw_acquisition import MAX_BYTES, fetch

JEPX_PAGE = "https://www.jepx.jp/electricpower/market-data/spot/"
GEN_BASE = "https://hatsuden-kokai.occto.or.jp/hks-web-public/"
RESERVE_BASE = "https://web-kohyo.occto.or.jp/kks-web-public/"
RECIPES = {
    "demand": {"source_id": "occto_demand", "adapter": "system", "date_column": "年月日"},
    "spot": {
        "source_id": "jepx_spot",
        "adapter": "jepx",
        "directory": "spot_summary",
        "date_column": "受渡日",
        "period_column": "時刻コード",
    },
    "sensitivity": {
        "source_id": "jepx_price_sensitivity",
        "adapter": "jepx",
        "directory": "virtualprice",
        "date_column": "年月日",
        "period_column": "時刻コード",
    },
    "sensitivity_diff": {
        "source_id": "jepx_price_sensitivity",
        "adapter": "jepx",
        "directory": "virtualprice_diff",
        "date_column": "年月日",
        "period_column": "時刻コード",
    },
    "generation": {
        "source_id": "occto_generation",
        "adapter": "generation",
        "date_column": "対象日",
    },
    "unit": {"source_id": "hjks_unit", "adapter": "hjks", "page": "unit"},
    "outage": {"source_id": "hjks_outage", "adapter": "hjks", "page": "outages"},
    "reserve_nextday2": {
        "source_id": "occto_reserve",
        "adapter": "reserve",
        "type": "05",
        "date_column": "対象年月日",
    },
    "reserve_latest": {
        "source_id": "occto_reserve",
        "adapter": "reserve",
        "type": "02",
        "date_column": "対象年月日",
    },
    "reserve_interconnector_nextday2": {
        "source_id": "occto_interconnector",
        "adapter": "reserve",
        "type": "06",
        "date_column": "対象年月日",
    },
    "reserve_interconnector_latest": {
        "source_id": "occto_interconnector",
        "adapter": "reserve",
        "type": "04",
        "date_column": "対象年月日",
    },
    "regulatory": {
        "source_id": "regulatory_entities",
        "adapter": "file",
        "url": "https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/005_09_00.pdf",
        "filename": "005_09_00.pdf",
    },
    "high_price": {
        "source_id": "high_price_validation",
        "adapter": "file",
        "url": "https://www.egc.meti.go.jp/info/business/spike/pdf/20251031_DataSheet.pdf",
        "filename": "20251031_DataSheet.pdf",
    },
}


def download_system_demand(start, end):
    """公開セッションでCSV保存の確認→OK→ダウンロードを通常どおり行う。"""
    opener = session()
    base = "https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/"
    fetch(base + "LOGIN_login", opener=opener)
    fetch(base + "CF01S010C?fwExtention.pathInfo=CF01S010C", {}, opener=opener)
    url = base + "CF01S010C"
    fields = {
        "fwExtention.actionType": "reference",
        "fwExtention.actionSubType": "initDisplay",
        "fwExtention.pathInfo": "CF01S010C",
        "fwExtention.prgbrh": "0",
        "fwExtention.formId": "CF01S010P",
        "transitionContextKey": "DEFAULT",
    }

    def ajax():
        response, _, _ = fetch(url, fields, opener=opener, extra_headers={"sdReqType": "AJAX"})
        result = json.loads(response)["root"]
        if result.get("errMessage") or result.get("interceptorErr"):
            raise ValueError(f"公式画面の検証エラー: {result.get('errMessage')}")
        header = result.get("bizRoot", {}).get("header", {})
        for key in (
            "ajaxToken",
            "requestToken",
            "requestTokenBk",
            "transitionContextKey",
            "rklNmHdn",
            "downloadKey",
        ):
            if key in header:
                fields[key] = header[key].get("value") or ""

    ajax()
    fields.update(
        {
            "tabSntk": "1",
            "areaDataKnd": "12",
            "areaNngpFrom": start.replace("-", "/"),
            "areaNngpTo": end.replace("-", "/"),
            "allAreaSectDwld": "11",
        }
    )
    fields.update(
        {
            key: f"{i:02}"
            for i, key in enumerate(
                ["hkd", "thk", "tko", "chb", "hkr", "kns", "cgk", "skk", "kys", "oki", "areaSum"], 1
            )
        }
    )
    for action in ("print", "ok"):
        fields["fwExtention.actionSubType"] = action
        ajax()
    fields["fwExtention.actionSubType"] = "download"
    data, headers, final = fetch(url, fields, opener=opener)
    return data, headers, final, url, fields, f"demand_{start}_{end}.csv"


def session():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def curl_request(url, directory, fields=None):
    """HJKSではOS標準curlを使用する。TLS証明書検証は無効化しない。"""
    directory = Path(directory)
    headers_path, body_path = directory / "headers", directory / "body"
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--fail",
        "--location",
        "--max-time",
        "45",
        "--max-filesize",
        str(MAX_BYTES),
        "--cookie",
        str(directory / "cookies"),
        "--cookie-jar",
        str(directory / "cookies"),
        "--dump-header",
        str(headers_path),
        "--output",
        str(body_path),
    ]
    for name, value in (fields or {}).items():
        command.extend(["--data-urlencode", f"{name}={value}"])
    command.append(url)
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=50)
    except subprocess.TimeoutExpired:
        raise ValueError("curl取得時間上限超過（フォームの秘密値は記録しない）") from None
    if result.returncode:
        raise ValueError(f"curl取得失敗: exit={result.returncode}（フォームの秘密値は記録しない）")
    blocks = headers_path.read_text().strip().split("\n\n")
    headers = dict(line.split(":", 1) for line in blocks[-1].splitlines()[1:] if ":" in line)
    return body_path.read_bytes(), {k.strip(): v.strip() for k, v in headers.items()}, url


def download_recipe(recipe, start, end):
    adapter = recipe["adapter"]
    if adapter == "system":
        return download_system_demand(start, end)
    if adapter == "jepx":
        directory = recipe["directory"]
        filename = f"{directory}_2025.csv"
        fields = {"dir": directory, "file": filename}
        url = "https://www.jepx.jp/_download.php"
        data, headers, final = fetch(url, fields, JEPX_PAGE)
        return data, headers, final, url, fields, filename
    if adapter == "reserve":
        fields = {
            "jhSybt": recipe["type"],
            "tgtYmdFrom": start.replace("-", "/"),
            "tgtYmdTo": end.replace("-", "/"),
        }
        url = RESERVE_BASE + "download/downloadCsv?" + urllib.parse.urlencode(fields)
        data, headers, final = fetch(url, referer=RESERVE_BASE + "download")
        return data, headers, final, url, None, f"{recipe['type']}_{start}_{end}.csv"
    if adapter == "generation":
        opener = session()
        fetch(GEN_BASE + "disclaimer-agree", opener=opener)
        fetch(GEN_BASE + "disclaimer-agree/next", {"agreed": "0"}, opener=opener)
        fetch(GEN_BASE + "info/hks", opener=opener)
        fields = {
            "htdnsCd": "",
            "htdnsNm": "",
            "unitNm": "",
            "areaCheckbox": ["99"] + [f"{i:02}" for i in range(1, 11)],
            "hatudenHosikiCheckbox": ["99"] + [f"{i:02}" for i in range(1, 10)],
            "tgtDateDateFrom": start.replace("-", "/"),
            "tgtDateDateTo": end.replace("-", "/"),
        }
        response, _, _ = fetch(GEN_BASE + "info/hks/search", fields, opener=opener)
        result = json.loads(response)
        if result.get("errorMessages") or result.get("infoMessages"):
            raise ValueError(
                f"公式検索応答: {result.get('errorMessages') or result['infoMessages']}"
            )
        url = GEN_BASE + "info/hks/downloadCsv?" + urllib.parse.urlencode(fields, doseq=True)
        data, headers, final = fetch(url, opener=opener)
        return data, headers, final, url, None, f"generation_{start}_{end}.csv"
    if adapter == "hjks":
        page = recipe["page"]
        url = f"https://hjks.jepx.or.jp/hjks/{page}"
        with tempfile.TemporaryDirectory() as temporary:
            html, _, _ = curl_request(url, temporary)
            match = re.search(r'name="_csrf" value="([^"]+)"', html.decode("utf-8"))
            if not match:
                raise ValueError("通常フォームのCSRF値を確認できない")
            fields = {
                "_csrf": match[1],
                "csv": "csv",
                "area": "",
                "company": "",
                "plantcd": "",
                "name": "",
                "format": "",
                "unitname": "",
            }
            if page == "unit":
                # 稼働終了ユニットも含める。現在取得版であることは別途報告する。
                fields["_enddtFlg"] = "on"
            else:
                fields.update(
                    {"maintemode": "", "assortment": "", "startdtfrom": "", "startdtto": ""}
                )
            data, headers, final = curl_request(url, temporary, fields)
            return data, headers, final, url, fields, f"{page}_snapshot.csv"
    if adapter == "file":
        data, headers, final = fetch(recipe["url"])
        return data, headers, final, recipe["url"], None, recipe["filename"]
    raise ValueError(f"未対応adapter: {adapter}")

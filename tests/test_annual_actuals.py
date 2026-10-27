"""年度途中の改行契約変更と、原空欄・時刻の非補完。"""

import pytest

from scripts.build_annual_actuals import decode_csv
from src.actual_net_demand import clock_keys, numeric_raw


def test_cr_only_and_crlf_decode_preserve_same_original_cells():
    records = [["DATE", "TIME", "値"], ["2026/03/31", "23:30", ""], ["2026/03/31", "23:00", "-1"]]
    for newline in ["\r", "\r\n", "\n"]:
        raw = newline.join(",".join(r) for r in records).encode("cp932")
        decoded, _ = decode_csv(raw)
        assert decoded == records
        assert numeric_raw(decoded[1][2]) is None
        assert numeric_raw(decoded[2][2]) == -1


def test_fiscal_end_and_end_clock_keep_original_delivery_date():
    result = clock_keys(["2026/03/31"], ["24:00"], role="end")
    assert str(result.delivery_date.iloc[0]) == "2026-03-31"
    assert result.period.iloc[0] == 48
    assert str(result.timestamp_end.iloc[0]) == "2026-04-01 00:00:00+09:00"
    with pytest.raises(ValueError):
        clock_keys(["2026/03/31"], ["24:00"], role="start")

"""元行を保持し、確認済み符号の表示値と単一地域間の記述価格差を追加する。"""

from decimal import Decimal

import pandas as pd

from src.market_panel import key_qa

SIGN_URL = "https://www.occto.or.jp/houkokusho/2025/files/denryokujukyuu_2024_250910.pdf"


def area_price_reference(frame, market, prefix=""):
    key_qa(market, ["area", "delivery_date", "period"])
    result = pd.DataFrame(index=frame.index)
    qa = {}
    verified = frame[prefix + "direction_status"].eq("official_orientation_verified")
    for role in ("sending", "receiving"):
        area_col = f"{prefix}{role}_area"
        left = frame[["delivery_date", "period", area_col]].rename(columns={area_col: "area"})
        left.loc[~verified, "area"] = None
        left["_ordinal"] = range(len(left))
        prices = market[["area", "delivery_date", "period", "area_price"]]
        joined = left.merge(
            prices,
            on=["area", "delivery_date", "period"],
            how="left",
            validate="many_to_one",
            indicator=True,
            sort=False,
        )
        if len(joined) != len(frame) or joined._ordinal.tolist() != list(range(len(frame))):
            raise ValueError("連系線の価格参照による行増殖/順序変化")
        eligible = left.area.notna()
        matched = joined._merge.eq("both")
        result[f"{prefix}{role}_area_price"] = joined.area_price.to_numpy()
        result[f"{prefix}{role}_price_key_matched"] = matched.to_numpy()
        n = int(eligible.sum())
        qa[role] = dict(
            eligible=n,
            matched=int((eligible & matched).sum()),
            unmatched=int((eligible & ~matched).sum()),
            match_rate=float((eligible & matched).sum() / n) if n else None,
            ineligible_multi_region_or_held=int((~eligible).sum()),
        )
    send = result[f"{prefix}sending_area_price"]
    receive = result[f"{prefix}receiving_area_price"]
    status_col = prefix + "direction_status"
    eligible = frame[f"{prefix}sending_area"].notna() & frame[f"{prefix}receiving_area"].notna()
    eligible = eligible & verified
    complete = eligible & send.notna() & receive.notna()
    difference = [r - s if ok else None for s, r, ok in zip(send, receive, complete, strict=True)]
    result[f"{prefix}receiving_minus_sending_price"] = difference
    result[f"{prefix}price_comparison_status"] = [
        "matched_single_area_prices"
        if ok
        else ("held_multi_area_or_direction" if not single else "missing_market_price")
        for single, ok in zip(eligible, complete, strict=True)
    ]
    qa.update(
        price_difference_available=int(complete.sum()),
        direction_status_counts=frame[status_col].value_counts(dropna=False).to_dict(),
    )
    return result, qa


def annotate_comparison(frame, market, *, system):
    if frame.source_record_id.duplicated().any():
        raise ValueError("連系線元行IDが重複")
    added = pd.DataFrame(index=frame.index)
    qa = {"rows": len(frame), "row_growth": 0, "silent_drop": 0, "directions": {}}
    if system:
        known = frame.direction_raw.isin(["順方向", "逆方向"]) & frame.direction_status.eq(
            "official_orientation_verified"
        )
        added["capacity_display_sign_multiplier"] = [
            Decimal(1) if d == "順方向" else Decimal(-1) if d == "逆方向" else None
            for d in frame.direction_raw
        ]
        added.loc[~known, "capacity_display_sign_multiplier"] = None
        added["available_capacity_direction_display_mw"] = [
            value * factor if valid and pd.notna(value) else None
            for value, factor, valid in zip(
                frame.available_capacity_mw,
                added.capacity_display_sign_multiplier,
                known,
                strict=True,
            )
        ]
        added["capacity_display_formula"] = [
            "available_capacity_mw * 1"
            if d == "順方向"
            else "available_capacity_mw * -1"
            if d == "逆方向"
            else "held_unknown_direction"
            for d in frame.direction_raw
        ]
        added.loc[~known, "capacity_display_formula"] = "held_unknown_direction_or_sign"
        added["capacity_display_source_url"] = SIGN_URL
        added["capacity_display_source_page"] = "物理36〜41 図2-17〜27：順正・逆負軸"
        added["capacity_display_rule_scope"] = "system_capacity_dayahead2_planned_only"
        added["capacity_display_rule_status"] = [
            "signed_axis_to_named_direction" if v else "held_unverified_sign" for v in known
        ]
        prices, stats = area_price_reference(frame, market)
        added = pd.concat([added, prices], axis=1)
        qa["directions"]["source_row_direction"] = stats
        qa["sign_multiplier_counts"] = frame.direction_raw.value_counts().to_dict()
    else:
        for side in ("forward", "reverse"):
            # 別欄の公表数量は無変換の同値参照。系統CSVの符号を流用しない。
            added[f"{side}_available_capacity_comparison_mw"] = frame[
                f"{side}_available_capacity_mw"
            ]
            added[f"{side}_capacity_display_rule_status"] = "as_published_separate_direction_column"
            prices, stats = area_price_reference(frame, market, side + "__")
            added = pd.concat([added, prices], axis=1)
            qa["directions"][side] = stats
    if set(added) & set(frame):
        raise ValueError("比較列が元列を上書き")
    return added, qa

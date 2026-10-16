"""原行の範囲を保って公式根拠のある方向だけ追加。地域能力は合算しない。"""

import csv
import json
from collections import Counter
from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.interconnector_market import VARIANTS
from src.market_panel import key_qa
from src.time_normalization import sha256

BASE = "data/processed/market/20261016_market_diagnostic_v1/interconnectors"


def direction_fields(mapping, prefix=""):
    send = json.loads(mapping["sending_area_set_json"])
    receive = json.loads(mapping["receiving_area_set_json"])
    return {
        prefix + "sending_area_set_json": mapping["sending_area_set_json"],
        prefix + "receiving_area_set_json": mapping["receiving_area_set_json"],
        prefix + "sending_area": send[0] if len(send) == 1 else None,
        prefix + "receiving_area": receive[0] if len(receive) == 1 else None,
        prefix + "direction_status": mapping["direction_status"],
    }


def annotate_frame(frame, rows, variant):
    mapping = {
        (r["link_name_raw"], r["direction_raw"]): r for r in rows if r["source_variant"] == variant
    }
    expected = sum(r["source_variant"] == variant for r in rows)
    if len(mapping) != expected:
        raise ValueError("方向対応表の重複")
    out = frame.copy()
    system = variant == VARIANTS[0]
    additions = []
    for row in frame[["link_name_raw", "direction_raw", "delivery_date", "period"]].to_dict(
        "records"
    ):
        name = row["link_name_raw"]
        if (name, "順方向") not in mapping or (name, "逆方向") not in mapping:
            raise ValueError("未登録名称。推測対応しない")
        m = mapping[(name, row["direction_raw"])] if system else mapping[(name, "順方向")]
        extra = dict(
            equipment_scope_class=m["equipment_scope_class"],
            equipment_scope_description=m["scope_description"],
            constraint_overlap_group=m["overlap_group"],
            direction_reference_url=m["source_url"],
            direction_reference_page=m["source_page"],
            direction_reference_checked_on=m["checked_on"],
            formal_full_FY_validity_verified=False,
            equipment_history_fully_verified=False,
            raw_quantities_sign_changed=False,
            qa_new_fence_before_notice_target_label=(
                name in ("中部フェンス", "関西フェンス")
                and (
                    row["delivery_date"] < date(2026, 3, 13)
                    or (row["delivery_date"] == date(2026, 3, 13) and row["period"] < 2)
                )
            ),
            scope_phase="transition_publication"
            if row["delivery_date"] >= date(2026, 3, 13) and m["overlap_group"] == "central_loop"
            else "observed_named_scope",
        )
        if system:
            extra.update(direction_fields(m))
        else:
            extra.update(direction_fields(mapping[(name, "順方向")], "forward__"))
            extra.update(direction_fields(mapping[(name, "逆方向")], "reverse__"))
        additions.append(extra)
    new = pd.DataFrame(additions, index=out.index)
    if set(new) & set(out):
        raise ValueError("方向列の上書き")
    return pd.concat([out, new], axis=1)


def annotate_existing_links(root, repo, output):
    with (repo / "metadata/research/interconnector_direction_scope_20261016.csv").open(
        newline=""
    ) as f:
        mapping = list(csv.DictReader(f))
    qa = {}
    for variant in VARIANTS:
        p = root / BASE / f"{variant}_FY2025.parquet"
        original = pq.read_table(p)
        source = original.to_pandas()
        annotated = annotate_frame(source, mapping, variant)
        key_qa(
            annotated,
            ["delivery_date", "period", "link_name_raw", "direction_raw"],
            expected=len(source),
        )
        # Arrowの元列はそのまま追加し、元型・全セル・schema metadataを維持。
        added = pa.Table.from_pandas(annotated.drop(columns=source.columns), preserve_index=False)
        result = original
        for field, col in zip(added.schema, added.columns, strict=True):
            result = result.append_column(field, col)
        target = output / f"{variant}_with_directions_FY2025.parquet"
        pq.write_table(result, target, compression="zstd")
        loaded = pq.read_table(target)
        if not loaded.equals(result, check_metadata=True) or not loaded.select(
            original.column_names
        ).equals(original):
            raise ValueError("方向付与の元セル/書戻し不一致")
        statuses = Counter(
            annotated["direction_status" if variant == VARIANTS[0] else "forward__direction_status"]
        )
        q = dict(
            rows=len(source),
            duplicate_keys=0,
            original_cells_types_equal=True,
            direction_status_counts=dict(statuses),
            direction_orientation_unmatched_rows=sum(
                count for status, count in statuses.items() if status.startswith("held")
            ),
            new_fence_before_notice_target_label_rows=int(
                annotated.qa_new_fence_before_notice_target_label.sum()
            ),
            physical_scope_full_FY_confirmed=False,
            sign_changed=False,
            sha256=sha256(target),
            nulls={c: loaded[c].null_count for c in loaded.column_names},
        )
        if variant == VARIANTS[0]:
            x = source
            q["signed_capacity_identity_matches"] = int(
                (
                    x.available_capacity_mw
                    == (
                        x.operating_capacity_mw
                        - x.margin_mw
                        - x.planned_flow_mw
                        - x.wide_area_adjustment_mw
                    )
                ).sum()
            )
            q["capacity_sign_counts"] = {
                d: dict(
                    negative=int(g.available_capacity_mw.lt(0).sum()),
                    zero=int(g.available_capacity_mw.eq(0).sum()),
                    positive=int(g.available_capacity_mw.gt(0).sum()),
                )
                for d, g in x.groupby("direction_raw")
            }
        qa[variant] = q
    return qa

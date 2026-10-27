"""年間結果を原列のまま価格へ接続し、本文4図・月別表を作る。LPは実行しない。"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from matplotlib import font_manager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.trial_adjustable_removal import ENTITIES, TARGETS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/interim/occto/annual_diagnostic/20261027_results_v1"
OUT = ROOT / "data/processed/annual_diagnostic/20261027_publication_v1"
REPORT = ROOT / "reports/27_fy2025_annual"
MARKET = (
    ROOT
    / "data/processed/occto/demand_supply_direction/20261016_native_v1"
    / "market_base_FY2025_with_reserve.parquet"
)
BASE = "reference_no_deduction"
NET = "saved_plan_constraints"


def classify(g):
    baseline = g.all_firms_status.eq("feasible")
    removed = g.removed_simultaneous_status
    if (
        len(g) != 17
        or not g.all_firms_status.isin(["feasible", "infeasible"]).all()
        or not removed.isin(["feasible", "infeasible"]).all()
    ):
        return "held"
    if not baseline.all():
        return "baseline_scope_differs"
    if removed.eq("infeasible").all():
        return "all17_removed_infeasible"
    if removed.eq("feasible").all():
        return "all17_removed_feasible"
    return "condition_dependent"


def savefig(fig, stem):
    fig.savefig(REPORT / "figures" / (stem + ".pdf"), bbox_inches="tight")
    fig.savefig(REPORT / "figures" / (stem + ".png"), dpi=160, bbox_inches="tight")
    plt.close(fig)


def main():
    assert len(list((SOURCE / "checkpoints").glob("*.json"))) == 12, "全12月の計算完了後に実行"
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "priced_multipliers").mkdir()
    (REPORT / "figures").mkdir(exist_ok=True)
    qa = REPORT / "qa"
    cols = [
        "area",
        "delivery_date",
        "period",
        "area_price",
        "system_price_market",
        "system_price_sensitivity_base",
        "baseline_price_match_flag",
    ]
    price = pq.read_table(MARKET, columns=cols).to_pandas()
    price.delivery_date = pd.to_datetime(price.delivery_date).dt.date
    assert len(price) == 157680 and not price.duplicated(cols[:3]).any()
    pindex = pd.MultiIndex.from_frame(price[cols[:3]])
    monthly_states, summaries, statuses, base_mult, base_sim, joins, links = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for month in [f"2025{m:02}" for m in range(4, 13)] + ["202601", "202602", "202603"]:
        simparts = []
        for path in sorted((SOURCE / "simultaneous").glob(month + "__*.parquet")):
            f = pd.read_parquet(path)
            f["month"] = month
            simparts.append(f)
            for key, g in f.groupby(
                [
                    "supply_trial_scenario",
                    "network_scenario",
                    "entity_code",
                    "all_firms_status",
                    "removed_simultaneous_status",
                ],
                dropna=False,
            ):
                summaries.append(
                    dict(
                        zip(
                            [
                                "supply_trial_scenario",
                                "network_scenario",
                                "entity_code",
                                "all_firms_status",
                                "removed_simultaneous_status",
                            ],
                            key,
                        ),
                        month=month,
                        frames=len(g),
                    )
                )
            if f.supply_trial_scenario.iloc[0] == BASE:
                base_sim.append(f)
        sim = pd.concat(simparts, ignore_index=True)
        for key, g in sim.groupby(
            ["network_scenario", "entity_code", "delivery_date", "period"], sort=False
        ):
            monthly_states.append(
                dict(
                    zip(["network_scenario", "entity_code", "delivery_date", "period"], key),
                    month=month,
                    consistency=classify(g),
                    baseline_feasible_conditions=int(g.all_firms_status.eq("feasible").sum()),
                    removed_feasible_conditions=int(
                        g.removed_simultaneous_status.eq("feasible").sum()
                    ),
                )
            )
        for path in sorted((SOURCE / "multipliers").glob(month + "__*.parquet")):
            original = pq.read_table(path)
            f = original.to_pandas()
            f.delivery_date = pd.to_datetime(f.delivery_date).dt.date
            keys = pd.MultiIndex.from_frame(f[["target_area", "delivery_date", "period"]])
            idx = pindex.get_indexer(keys)
            assert (idx >= 0).all()
            assert not f.duplicated(
                [
                    "supply_trial_scenario",
                    "network_scenario",
                    "target_area",
                    "delivery_date",
                    "period",
                ]
            ).any()
            result = original
            for c in cols[3:]:
                result = result.append_column(
                    c, pa.array(price[c].iloc[idx].reset_index(drop=True))
                )
            pq.write_table(result, OUT / "priced_multipliers" / path.name, compression="zstd")
            assert result.select(original.column_names).equals(original)
            joins.append(
                dict(
                    file=path.name,
                    rows=len(f),
                    matched=len(idx),
                    unmatched=0,
                    multiplication=0,
                    original_columns_equal=True,
                )
            )
            f["month"] = month
            for key, g in f.groupby(
                ["supply_trial_scenario", "network_scenario", "target_area", "status"], dropna=False
            ):
                statuses.append(
                    dict(
                        zip(
                            ["supply_trial_scenario", "network_scenario", "target_area", "status"],
                            key,
                        ),
                        month=month,
                        frames=len(g),
                    )
                )
            valid = f.status.eq("valid")
            contradiction = (
                valid
                & ((f.lambda_removed >= 1 + 1e-8) != f.removed_simultaneous_status.eq("feasible"))
                & (abs(f.lambda_removed - 1) > 1e-8)
            )
            links.append(
                dict(
                    file=path.name,
                    valid=int(valid.sum()),
                    max_vs_independent_balance_exceptions=int(contradiction.sum()),
                )
            )
            if f.supply_trial_scenario.iloc[0] == BASE:
                for c in cols[3:]:
                    f[c] = price[c].iloc[idx].to_numpy()
                base_mult.append(f)
        print("価格・集計", month, flush=True)
    state = pd.DataFrame(monthly_states)
    state.to_parquet(OUT / "condition_consistency_all_times.parquet", index=False)
    state.groupby(["network_scenario", "entity_code", "month", "consistency"]).size().rename(
        "frames"
    ).reset_index().to_csv(qa / "condition_consistency_monthly.csv", index=False)
    state.groupby(["network_scenario", "entity_code", "consistency"]).size().rename(
        "frames"
    ).reset_index().to_csv(qa / "condition_consistency_annual.csv", index=False)
    pd.DataFrame(summaries).to_csv(qa / "simultaneous_monthly.csv", index=False)
    pd.DataFrame(statuses).to_csv(qa / "multiplier_status_monthly.csv", index=False)
    pd.DataFrame(joins).to_csv(qa / "price_join.csv", index=False)
    pd.DataFrame(links).to_csv(qa / "maximum_vs_original_balance.csv", index=False)
    b = pd.concat(base_mult, ignore_index=True)
    bs = pd.concat(base_sim, ignore_index=True)
    b.to_parquet(OUT / "explanatory_no_stop_priced.parquet", index=False)
    bs.to_parquet(OUT / "explanatory_no_stop_simultaneous.parquet", index=False)
    stats = []
    for key, g in b.groupby(["network_scenario", "target_area", "month"]):
        v = g[g.status.eq("valid")]
        stats.append(
            dict(
                zip(["network_scenario", "target_area", "month"], key),
                frames=len(g),
                valid=len(v),
                below_one=int((v.lambda_removed < 1 - 1e-8).sum()),
                lambda_min=v.lambda_removed.min(),
                lambda_median=v.lambda_removed.median(),
                lambda_max=v.lambda_removed.max(),
                price_median_all=g.area_price.median(),
                price_median_valid=v.area_price.median(),
            )
        )
    pd.DataFrame(stats).to_csv(qa / "explanatory_monthly.csv", index=False)
    annual = []
    for key, g in b.groupby(["network_scenario", "target_area"]):
        v = g[g.status.eq("valid")]
        annual.append(
            dict(
                zip(["network_scenario", "target_area"], key),
                frames=len(g),
                valid=len(v),
                below_one=int((v.lambda_removed < 1 - 1e-8).sum()),
                lambda_median=v.lambda_removed.median(),
                lambda_min=v.lambda_removed.min(),
                lambda_max=v.lambda_removed.max(),
                all_firms_infeasible=int(g.status.eq("all_firms_infeasible").sum()),
                nonpositive=int(g.status.eq("nonpositive_target_net").sum()),
                other_infeasible=int(g.status.eq("removed_other_regions_infeasible").sum()),
                held=int(
                    (
                        ~g.status.isin(
                            [
                                "valid",
                                "nonpositive_target_net",
                                "all_firms_infeasible",
                                "removed_other_regions_infeasible",
                            ]
                        )
                    ).sum()
                ),
            )
        )
    pd.DataFrame(annual).to_csv(qa / "explanatory_annual.csv", index=False)
    fonts = [
        f
        for f in font_manager.fontManager.ttflist
        if f.name in ["Hiragino Sans", "Noto Sans CJK JP", "IPAexGothic", "Arial Unicode MS"]
    ]
    assert fonts, "日本語フォントが必要"
    plt.rcParams.update({"font.family": fonts[0].name, "axes.unicode_minus": False, "font.size": 9})
    # 図1：コードと同じ節点・辺。箱内は固定値/変数を区別。
    fig, ax = plt.subplots(figsize=(12, 5))
    xy = {
        "北海道": (0, 1),
        "東北": (1, 1),
        "東京": (2, 1),
        "中部": (3, 1),
        "北陸": (3, 2),
        "関西": (4, 1),
        "中国": (5, 1),
        "四国": (5, 0),
        "九州": (6, 1),
    }
    from src.net_demand_network import EDGES

    for _, a, c in EDGES:
        ax.annotate("", xy=xy[c], xytext=xy[a], arrowprops={"arrowstyle": "<->", "color": "gray"})
    for a, (x, y) in xy.items():
        ax.text(
            x,
            y,
            a,
            ha="center",
            va="center",
            bbox={"boxstyle": "round", "facecolor": "white", "edgecolor": "black"},
        )
    ax.text(0, 2.6, "固定入力：実績需要 D − 固定実績 R（PV・風力・原子力）＝純需要 N", fontsize=11)
    ax.text(
        0,
        -0.8,
        "供給：参照上限＋17停止条件 → 名参照の対象企業分だけ全地域から除去\n"
        "変数：地域調整出力 g、純移送 f。保存留保枠の包絡・フェンスを同時適用\n"
        "出口① 元Nの9地域同時成立　出口② 他地域N固定・対象地域の最大倍率",
        fontsize=10,
    )
    ax.set(xlim=(-0.5, 6.5), ylim=(-1.3, 3))
    ax.axis("off")
    savefig(fig, "01_model")
    labels = list(ENTITIES)
    cats = [
        "all17_removed_feasible",
        "all17_removed_infeasible",
        "condition_dependent",
        "baseline_scope_differs",
        "held",
    ]
    names = [
        "17条件で成立一致",
        "17条件で不成立一致",
        "条件依存",
        "全法人不成立条件を含む",
        "入力保留等",
    ]
    sc = state[state.network_scenario.eq(NET)]
    fig, ax = plt.subplots(figsize=(12, 5))
    bottom = np.zeros(8)
    for cat, name in zip(cats, names):
        counts = (
            sc[sc.consistency.eq(cat)]
            .groupby("entity_code")
            .size()
            .reindex(labels, fill_value=0)
            .to_numpy()
        )
        ax.bar(np.arange(8), counts / 17520 * 100, bottom=bottom, label=name)
        bottom += counts / 17520 * 100
    ax.set_xticks(
        range(8),
        [
            "北海道電力",
            "東北電力",
            "JERA",
            "北陸電力",
            "関西電力",
            "中国電力",
            "四国電力",
            "九州電力",
        ],
    )
    ax.set_ylabel("FY2025全17,520コマに占める割合〔%〕")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3)
    ax.set_ylim(0, 100)
    savefig(fig, "02_condition_consistency")
    comp = []
    for code in labels:
        s = bs[bs.entity_code.eq(code)]
        a = s[s.network_scenario.eq(NET)].set_index(["delivery_date", "period"])
        z = s[s.network_scenario.ne(NET)].set_index(["delivery_date", "period"])
        common = a.all_firms_status.eq("feasible") & z.all_firms_status.eq("feasible")
        comp.append(
            dict(
                entity_code=code,
                denominator=int(common.sum()),
                excluded=17520 - int(common.sum()),
                constrained_infeasible=int(
                    a.loc[common, "removed_simultaneous_status"].eq("infeasible").sum()
                ),
                unconstrained_infeasible=int(
                    z.loc[common, "removed_simultaneous_status"].eq("infeasible").sum()
                ),
            )
        )
    c = pd.DataFrame(comp)
    c.to_csv(qa / "transmission_common_times.csv", index=False)
    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(8)
    ax.bar(x - 0.18, c.constrained_infeasible / c.denominator * 100, 0.36, label="保存送電包絡あり")
    ax.bar(x + 0.18, c.unconstrained_infeasible / c.denominator * 100, 0.36, label="包絡のみ解除")
    ax.set_xticks(
        x,
        [
            "北海道電力",
            "東北電力",
            "JERA",
            "北陸電力",
            "関西電力",
            "中国電力",
            "四国電力",
            "九州電力",
        ],
    )
    ax.set_ylabel("共通全法人成立時刻の除去後不成立割合〔%〕")
    ax.legend()
    ax.set_title("停止控除なし／比較分母 " + str(c.denominator.iloc[0]) + " コマ（全時刻表を別掲）")
    savefig(fig, "03_transmission_comparison")
    windows = []
    for near in [False, True]:
        fig, axes = plt.subplots(3, 3, figsize=(13, 11))
        for ax, (area, _) in zip(axes.flat, TARGETS):
            g = b[b.target_area.eq(area) & b.network_scenario.eq(NET)]
            v = g[g.status.eq("valid")]
            ax.scatter(v.lambda_removed, v.area_price, s=2, alpha=0.18, rasterized=True)
            ax.axvline(1, color="red", lw=0.8)
            ax.set_title(f"{area}：有効{len(v):,}／17,520")
            ax.set_xlabel("企業除去後の地域別純需要倍率")
            ax.set_ylabel("spotエリア価格〔円/kWh〕")
            if near:
                ax.set_xlim(0, 2)
            if not near:
                windows.append(
                    dict(
                        area=area,
                        total=len(g),
                        valid=len(v),
                        not_drawable=len(g) - len(v),
                        outside_0_2=int(((v.lambda_removed < 0) | (v.lambda_removed > 2)).sum()),
                    )
                )
        fig.suptitle(
            "名参照代理による調整供給除去／停止控除なし・保存送電包絡あり\n"
            + (
                "表示範囲0〜2（点の除外・修正なし、全範囲は別図）"
                if near
                else "全範囲。需要・時間帯をそろえた因果比較ではない"
            )
        )
        fig.tight_layout()
        savefig(fig, "04_multiplier_price_" + ("near_one" if near else "full"))
    pd.DataFrame(windows).to_csv(qa / "scatter_display_windows.csv", index=False)
    # 季節差は同じ条件の月別9地域法人対。分母も図示する。
    m = pd.DataFrame(stats)
    m = m[m.network_scenario.eq(NET)]
    fig, axes = plt.subplots(3, 3, figsize=(13, 9))
    months = sorted(m.month.unique())
    for ax, (area, _) in zip(axes.flat, TARGETS):
        g = m[m.target_area.eq(area)].set_index("month").loc[months]
        ax.plot(range(12), g.below_one / g.frames * 100, "o-", label="1未満／全時刻")
        ax.plot(range(12), g.valid / g.frames * 100, "s--", label="倍率有効／全時刻")
        ax.set_ylim(0, 105)
        ax.set_xticks(range(12), [s[-2:] for s in months])
        ax.set_title(area)
        ax.set_ylabel("月内コマ割合〔%〕")
    axes.flat[0].legend(fontsize=8)
    fig.tight_layout()
    savefig(fig, "A01_monthly_status")
    audit = dict(
        price_keys=len(price),
        multiplier_frames=sum(r["rows"] for r in joins),
        price_join_rate=1.0,
        unmatched=0,
        duplicate_market_key=0,
        row_multiplication=0,
        original_result_columns_equal=True,
        consistency_frames=len(state),
        condition_count=17,
        independent_price_observations=157680,
        system_price_mismatch_times=int(
            price.loc[price.area.eq("北海道"), "baseline_price_match_flag"].eq(False).sum()
        ),
        maximum_balance_exceptions=sum(r["max_vs_independent_balance_exceptions"] for r in links),
        figures="本文4点（図4は全範囲/拡大の2表示）＋月別付録1点",
        source=str(SOURCE.relative_to(ROOT)),
    )
    (qa / "publication_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(audit, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

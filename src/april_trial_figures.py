"""4月の固定済み試行を説明する5図。数値を表示のために削除・修正しない。"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.april_trial_comparison import CATEGORIES, LABELS, ecdf_points
from src.net_demand_network import AREAS

SHORT = {
    "北海道": "北海道電力",
    "東北": "東北電力",
    "東京": "JERA",
    "中部": "JERA",
    "北陸": "北陸電力",
    "関西": "関西電力",
    "中国": "中国電力",
    "四国": "四国電力",
    "九州": "九州電力",
}


def render(output, report):
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "april_trial_mpl"))
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt
    from matplotlib.ticker import PercentFormatter

    fonts = [
        f
        for f in font_manager.fontManager.ttflist
        if f.name in ("Hiragino Sans", "IPAexGothic", "Noto Sans CJK JP", "Arial Unicode MS")
    ]
    if not fonts:
        raise ValueError("日本語フォントが必要")
    plt.rcParams.update(
        {"font.family": fonts[0].name, "axes.unicode_minus": False, "font.size": 10}
    )
    figures = report / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    records = []

    def save(fig, name):
        for extension in ["png", "pdf"]:
            path = figures / (name + "." + extension)
            if path.exists():
                raise ValueError("既存図の上書き禁止")
            fig.savefig(path, dpi=170, bbox_inches="tight")
            records.append(
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        plt.close(fig)

    shares = pd.read_csv(report / "qa/all_time_state_shares.csv")
    counts = shares.pivot(index="target_area", columns="category", values="count").reindex(AREAS)
    assert counts.sum(axis=1).eq(1440).all()
    fig, ax = plt.subplots(figsize=(14, 7.7))
    bottom = np.zeros(9)
    colors = ["#0072B2", "#56B4E9", "#F0E442", "#D55E00", "#CC79A7", "#999999", "#333333"]
    for category, color in zip(CATEGORIES, colors, strict=True):
        values = counts[category].to_numpy() / 1440
        ax.bar(np.arange(9), values, bottom=bottom, label=LABELS[category], color=color, width=0.72)
        bottom += values
    for i, area in enumerate(AREAS):
        row = shares[shares.target_area.eq(area)].iloc[0]
        ax.text(
            i,
            1.015,
            f"有効{row.valid_time_count:,}\n有効中1未満\n{row.below_one_share_among_valid:.1%}",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    ax.set(
        xticks=np.arange(9),
        xticklabels=[a + "\n" + SHORT[a] for a in AREAS],
        ylim=(0, 1.18),
        ylabel="4月全1,440コマに占める割合",
        title="名参照代理による調整供給除去：全時刻の状態\n説明用条件：停止控除なし・保存計画送電制約あり",
    )
    ax.set_yticks(np.linspace(0, 1, 6))
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3, fontsize=9)
    fig.text(
        0.5,
        0.01,
        "各棒は全1,440コマ。有効中の1未満割合は別分母。地理順で、指標順位ではない。",
        ha="center",
        fontsize=10,
    )
    fig.subplots_adjust(bottom=0.24, top=0.84)
    save(fig, "01_all_time_states")
    pair = pd.read_parquet(output / "reference_network_pairs_all_times.parquet")
    ecdf_rows = []
    window = []
    for area in AREAS:
        g = pair[pair.target_area.eq(area)]
        common = g.status_network.eq("valid") & g.status_relaxed.eq("valid")
        f = g[common]
        window.append(
            {
                "target_area": area,
                "common_valid": len(f),
                "network_above_zoom_2": int(f.lambda_removed_network.gt(2).sum()),
                "relaxed_above_zoom_2": int(f.lambda_removed_relaxed.gt(2).sum()),
                "network_max": f.lambda_removed_network.max(),
                "relaxed_max": f.lambda_removed_relaxed.max(),
            }
        )
        for kind, col in [
            ("送電制約あり", "lambda_removed_network"),
            ("送電包絡なし", "lambda_removed_relaxed"),
        ]:
            x, y = ecdf_points(f[col])
            for xv, yv in zip(x, y, strict=True):
                ecdf_rows.append(
                    {
                        "target_area": area,
                        "condition": kind,
                        "multiplier": xv,
                        "cumulative_share": yv,
                        "denominator_common": len(f),
                    }
                )
    points = pd.DataFrame(ecdf_rows)
    points.to_csv(report / "qa/ecdf_coordinates.csv", index=False)
    pd.DataFrame(window).to_csv(report / "qa/ecdf_display_windows.csv", index=False)
    for zoom in [False, True]:
        fig, axes = plt.subplots(3, 3, figsize=(14, 12), sharey=True)
        for ax, area in zip(axes.flat, AREAS, strict=True):
            for kind, color in [("送電制約あり", "#0072B2"), ("送電包絡なし", "#D55E00")]:
                g = points[points.target_area.eq(area) & points.condition.eq(kind)]
                ax.step(
                    g.multiplier, g.cumulative_share, where="post", label=kind, color=color, lw=1.7
                )
            ax.axvline(1, color="black", ls="--", lw=1)
            n = int(next(x["common_valid"] for x in window if x["target_area"] == area))
            ax.set(title=f"{area}／{SHORT[area]}（共通有効 {n:,}）", ylim=(0, 1.02))
            ax.grid(alpha=0.2)
            ax.yaxis.set_major_formatter(PercentFormatter(1))
            if zoom:
                ax.set_xlim(0, 2)
            else:
                ax.set_xlim(left=0)
        fig.supxlabel("除去後の地域別純需要倍率〔倍〕")
        fig.supylabel("その値以下のコマの累積割合（対象ごとの共通有効時刻が分母）")
        scope = (
            "表示範囲0〜2倍。範囲外の値も分母と累積割合へ含む"
            if zoom
            else "全範囲表示。極端値の削除・切上げなし"
        )
        fig.suptitle("送電制約による地域別純需要倍率の違い／停止控除なし\n" + scope, fontsize=14)
        axes.flat[0].legend(fontsize=9, loc="lower right")
        fig.tight_layout(rect=(0.025, 0.03, 1, 0.94))
        save(fig, "03_network_ecdf_near_one" if zoom else "02_network_ecdf_full_range")
    summary = pd.read_csv(report / "qa/stop_pair_summary.csv")
    name = "58852_early__full_stop_zero"
    selected = (
        summary[
            summary.original_scenario.eq(name)
            & summary.network_scenario.eq("saved_plan_constraints")
        ]
        .set_index("target_area")
        .loc[list(AREAS)]
    )
    fig, axes = plt.subplots(1, 3, figsize=(17, 6))
    x = np.arange(9)
    axes[0].bar(x - 0.18, selected.valid_before, width=0.36, label="元の停止条件", color="#999999")
    axes[0].bar(
        x + 0.18, selected.valid_after, width=0.36, label="記録保留に追加控除なし", color="#0072B2"
    )
    axes[0].set(title="倍率算定可能なコマ", ylabel="コマ数（全1,440コマ中）", ylim=(0, 1500))
    axes[0].legend(fontsize=8)
    axes[1].bar(x, selected.below_to_above, color="#009E73")
    axes[1].set(title="双方有効で1未満→1以上", ylabel="コマ数（双方有効時刻で比較）")
    recovered = (
        selected.became_valid_from_all_firms_infeasible
        + selected.became_valid_from_other_regions_infeasible
        + selected.became_valid_from_other_held
    )
    axes[2].bar(x, recovered, color="#E69F00")
    axes[2].set(title="保留／他地域不成立等→有効", ylabel="コマ数（全1,440コマを比較）")
    for ax in axes:
        ax.set_xticks(x, AREAS, rotation=45)
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle(
        "停止情報の不明部分の扱いによる違い\n説明用の一組：58,852行版・早端・全停止0／保存計画送電制約あり（最終baselineではない）",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.89))
    save(fig, "04_stop_record_restoration_example")
    sim = pd.read_csv(report / "qa/simultaneous_summary.csv")
    ref = sim[
        sim.supply_trial_scenario.eq("reference_no_deduction")
        & sim.network_scenario.eq("saved_plan_constraints")
    ]
    codes = ["hokkaido", "tohoku", "jera", "hokuriku", "kansai", "chugoku", "shikoku", "kyushu"]
    labels = [
        "北海道電力",
        "東北電力",
        "JERA",
        "北陸電力",
        "関西電力",
        "中国電力",
        "四国電力",
        "九州電力",
    ]
    kinds = [
        "all_feasible_removed_feasible",
        "all_feasible_removed_infeasible",
        "all_firms_already_infeasible",
        "held_or_numerical_failure",
    ]
    title = [
        "全法人成立→除去後も成立",
        "全法人成立→除去後不成立",
        "全法人時から不成立",
        "その他保留／失敗",
    ]
    table = (
        ref.pivot(index="entity_code", columns="transition", values="time_keys")
        .reindex(index=codes, columns=kinds)
        .fillna(0)
    )
    assert table.sum(axis=1).eq(1440).all()
    fig, ax = plt.subplots(figsize=(13, 6.5))
    bottom = np.zeros(8)
    for kind, label, color in zip(
        kinds, title, ["#56B4E9", "#D55E00", "#999999", "#333333"], strict=True
    ):
        values = table[kind].to_numpy() / 1440
        ax.bar(np.arange(8), values, bottom=bottom, label=label, color=color)
        bottom += values
    ax.set(
        xticks=np.arange(8),
        xticklabels=labels,
        ylim=(0, 1),
        ylabel="4月全1,440コマに占める割合",
        title="名参照代理の調整供給除去後に、9地域の元の純需要を同時に満たせるか\n停止控除なし・保存計画送電制約あり／倍率は使わない／JERAは1法人として1回集計",
    )
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2)
    fig.subplots_adjust(bottom=0.25, top=0.84)
    save(fig, "05_nine_area_simultaneous")
    manifest = {
        "font": fonts[0].name,
        "figures": records,
        "figures_count": 5,
        "main_denominator": 1440,
        "ecdf_denominator": "対象ごとの送電両条件共通有効時刻",
        "ecdf_full_values_retained": True,
        "ecdf_zoom_xlim": [0, 2],
        "representative_stop_pair": name,
        "representative_is_final_baseline": False,
    }
    (report / "qa/figure_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return manifest

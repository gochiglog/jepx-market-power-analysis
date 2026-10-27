"""4月の条件付き倍率と価格の記述図。回帰・検定・価格閾値の選別なし。"""

import hashlib
import json
import os
import tempfile

import pandas as pd

from src.april_price_connection import LABELS, STATES
from src.april_trial_comparison import ecdf_points
from src.net_demand_network import AREAS

COLORS = ["#D55E00", "#0072B2", "#CC79A7", "#888888", "#333333"]


def render(output, report):
    os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir() + "/april_trial_mpl")
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt
    from matplotlib.ticker import PercentFormatter

    fonts = [
        f
        for f in font_manager.fontManager.ttflist
        if f.name in ["Hiragino Sans", "IPAexGothic", "Noto Sans CJK JP"]
    ]
    if not fonts:
        raise ValueError("日本語フォントが必要")
    plt.rcParams.update({"font.family": fonts[0].name, "axes.unicode_minus": False, "font.size": 9})
    folder = report / "figures"
    folder.mkdir(parents=True, exist_ok=True)
    files = []

    def save(fig, name):
        for ext in ["png", "pdf"]:
            p = folder / (name + "." + ext)
            if p.exists():
                raise ValueError("既存図への上書き禁止")
            fig.savefig(p, dpi=170, bbox_inches="tight")
            files.append(dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
        plt.close(fig)

    a = pd.read_parquet(output / "all_scenarios_with_spot_prices.parquet")
    a = a[
        a.supply_trial_scenario.eq("reference_no_deduction")
        & a.network_scenario.eq("saved_plan_constraints")
    ]
    assert len(a) == 12960
    for zoom in [False, True]:
        fig, axes = plt.subplots(3, 3, figsize=(14, 11))
        for area, ax in zip(AREAS, axes.flat, strict=True):
            g = a[a.target_area.eq(area)]
            v = g[g.status.eq("valid")]
            ax.scatter(
                v.lambda_removed.astype(float),
                v.area_price.astype(float),
                s=7,
                alpha=0.32,
                color="#0072B2",
                linewidths=0,
            )
            ax.axvline(1, color="black", ls="--", lw=1)
            ax.set(title=f"{area}（描画{len(v):,}／未算定{len(g) - len(v):,}）")
            ax.grid(alpha=0.15)
            if zoom:
                ax.set_xlim(0, 2)
            else:
                ax.set_xlim(left=0)
        scope = (
            "表示範囲0〜2倍。範囲外の点は保存版・全範囲図に保持"
            if zoom
            else "全範囲表示。極端値の削除・切上げなし"
        )
        fig.suptitle(
            "図A：地域別純需要倍率とspotエリア価格／停止控除なし・保存送電制約あり\n" + scope,
            fontsize=13,
        )
        fig.supxlabel("名参照調整供給除去後の地域別純需要倍率〔倍〕")
        fig.supylabel("当該地域spotエリア価格〔円/kWh〕")
        fig.tight_layout(rect=(0.02, 0.025, 1, 0.94))
        save(fig, "02_multiplier_price_near_one" if zoom else "01_multiplier_price_full")
    b = pd.read_parquet(output / "consistency_with_prices_by_network.parquet")
    b = b[b.network_scenario.eq("saved_plan_constraints")]
    fig, axes = plt.subplots(3, 3, figsize=(14, 12))
    for area, ax in zip(AREAS, axes.flat, strict=True):
        g = b[b.target_area.eq(area)]
        assert len(g) == 1440
        for state, label, color in zip(STATES, LABELS, COLORS, strict=True):
            z = g[g.consistency_state.eq(state)]
            if state == "other_held" and z.empty:
                continue
            if z.empty:
                ax.plot([], [], color=color, label=label + "：該当なし")
            else:
                xx, yy = ecdf_points(z.area_price.astype(float))
                ax.step(xx, yy, where="post", label=f"{label}：{len(z):,}", color=color)
        ax.set(title=area, ylim=(0, 1.02))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(alpha=0.15)
        ax.legend(fontsize=7, loc="lower right")
    fig.suptitle(
        "図B：今回の17条件内の判定一致区分と価格\n保存送電制約あり／全時刻・純需要非正も含む（図Aと母集団が異なる）\n凡例は件数、空区分は該当なし",
        fontsize=13,
    )
    fig.supxlabel("当該地域spotエリア価格〔円/kWh〕")
    fig.supylabel("各区分内でその価格以下のコマの累積割合")
    fig.tight_layout(rect=(0.02, 0.025, 1, 0.94))
    save(fig, "03_consistency_price_distribution")
    fig, axes = plt.subplots(3, 3, figsize=(14, 11))
    for area, ax in zip(AREAS, axes.flat, strict=True):
        g = b[b.target_area.eq(area)]
        for state, label, color in zip(STATES, LABELS, COLORS, strict=True):
            z = g[g.consistency_state.eq(state)]
            if z.empty:
                continue
            counts = z.hour_start.value_counts().reindex(range(24), fill_value=0)
            ax.step(range(24), counts / len(z), where="mid", color=color, label=label)
        ax.set(title=area, xticks=[0, 6, 12, 18, 23], xlim=(-0.5, 23.5))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(alpha=0.15)
    axes.flat[0].legend(fontsize=7)
    fig.suptitle(
        "補助図：判定一致区分ごとの時間帯の分布\n区分内のコマ数が分母。需要や時間帯を揃えた比較ではない",
        fontsize=13,
    )
    fig.supxlabel("受渡区間の開始時刻〔時・JST〕")
    fig.supylabel("各区分内で、その時間帯が占める割合")
    fig.tight_layout(rect=(0.02, 0.025, 1, 0.94))
    save(fig, "04_hour_composition")
    (report / "qa/figure_manifest.json").write_text(
        json.dumps(
            dict(
                figures=files,
                font=fonts[0].name,
                figures_count=4,
                scatter_condition="reference_no_deduction/saved_plan_constraints",
                scatter_zoom_xlim=[0, 2],
                causal_comparison=False,
                all_nine_areas_kept=True,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )

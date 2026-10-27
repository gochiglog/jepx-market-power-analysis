"""論文の中心主図・初見向け方法図・同一時刻の価格候補。新しいLPなし。"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.net_demand_network import EDGES
from src.paper_central_result import CATEGORIES, LABELS, artificial_fence_example

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/28_paper_central_result"


def configure():
    fonts = [
        f.name
        for f in font_manager.fontManager.ttflist
        if f.name in ["Hiragino Sans", "Noto Sans CJK JP", "IPAexGothic", "Arial Unicode MS"]
    ]
    if not fonts:
        raise ValueError("日本語フォントが必要")
    plt.rcParams.update(
        {
            "font.family": [fonts[0], "DejaVu Sans"],
            "font.size": 11,
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
            "svg.fonttype": "path",
        }
    )


def save(fig, name):
    for ext in ["pdf", "svg", "png"]:
        fig.savefig(REPORT / "figures" / (name + "." + ext), bbox_inches="tight", dpi=170)
    plt.close(fig)


def triangle(ax, post):
    xy = {"中部": (0.12, 0.62), "北陸": (0.5, 1.00), "関西": (0.88, 0.62)}
    for a, b, label, position in [
        ("中部", "関西", "M", (0.5, 0.56)),
        ("中部", "北陸", "S", (0.25, 0.84)),
        ("北陸", "関西", "E", (0.75, 0.84)),
    ]:
        ax.annotate(
            "",
            xy=xy[b],
            xytext=xy[a],
            arrowprops=dict(
                arrowstyle="->",
                lw=1.7,
                shrinkA=22,
                shrinkB=22,
                color="#49697e",
                linestyle="--" if post else "-",
            ),
            zorder=1,
        )
        ax.text(
            *position,
            label,
            fontsize=13,
            ha="center",
            bbox=dict(facecolor="white", edgecolor="none", pad=1),
            zorder=3,
        )
    for label, pos in xy.items():
        ax.text(
            *pos,
            label,
            ha="center",
            va="center",
            fontsize=12,
            bbox=dict(boxstyle="round", facecolor="white", edgecolor="#334455", pad=0.4),
            zorder=3,
        )
    ax.text(
        0.5,
        1.40,
        "C：新フェンスが揃う保存時刻" if post else "B：切替前の保存時刻",
        ha="center",
        fontsize=13,
        weight="bold",
    )
    ax.text(
        0.5,
        1.27,
        "2026/3/14–3/31：864コマ" if post else "2025/4/1–2026/3/12：16,608コマ",
        ha="center",
        fontsize=10,
    )
    ax.text(0.5, 1.14, "M＝三重、S＝南福光、E＝越前（矢印が正方向）", ha="center", fontsize=9)
    if post:
        ax.text(
            0.02,
            0.37,
            "F_C = M＋S　 F_H = S−E　 F_K = M＋E\n各フェンスの上下限を同時に適用",
            fontsize=11,
            va="top",
        )
        ax.text(
            0.02,
            0.08,
            "中央の旧3区間の個別上限は重ねない。\n破線は地域輸送の補助変数（物理線を再現しない）。",
            fontsize=10,
            va="top",
        )
    else:
        ax.text(0.02, 0.23, "個別：L_M ≤ M ≤ H_M、L_S ≤ S ≤ H_S\n　　　L_E ≤ E ≤ H_E", fontsize=11)
        ax.text(
            0.02,
            0.08,
            "同じS/Eに、さらに北陸フェンス：\nL_H ≤ S−E ≤ H_H を同時に適用",
            fontsize=11,
            color="#9c5200",
            va="top",
        )
    ax.set(xlim=(-0.04, 1.04), ylim=(-0.18, 1.50))
    ax.axis("off")


def method():
    fig = plt.figure(figsize=(12, 10))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.12, 1.65, 1.05], hspace=0.24, wspace=0.16)
    ax = fig.add_subplot(gs[0, :])
    xy = {
        "北海道": (0, 0.7),
        "東北": (1.1, 0.7),
        "東京": (2.2, 0.7),
        "中部": (3.3, 0.7),
        "北陸": (3.9, 1.4),
        "関西": (4.5, 0.7),
        "中国": (5.6, 0.7),
        "四国": (5.25, 0.02),
        "九州": (6.7, 0.7),
    }
    for i, (_, a, b) in enumerate(EDGES):
        ax.annotate(
            "", xy=xy[b], xytext=xy[a], arrowprops=dict(arrowstyle="<->", lw=1.2, color="#758595")
        )
        if i not in [3, 4, 5]:
            x = (xy[a][0] + xy[b][0]) / 2
            y = (xy[a][1] + xy[b][1]) / 2
            ax.text(
                x,
                y + 0.1,
                str(i + 1),
                fontsize=9,
                ha="center",
                bbox=dict(facecolor="white", edgecolor="none", pad=0.5),
            )
    for area, pos in xy.items():
        ax.text(
            *pos,
            area,
            ha="center",
            va="center",
            fontsize=11,
            bbox=dict(boxstyle="round", facecolor="white", edgecolor="#334455", pad=0.3),
        )
    ax.text(
        -0.25,
        2.95,
        "A：固定Rを残し、名参照の調整供給だけを全地域から除く",
        fontsize=14,
        weight="bold",
        va="top",
    )
    ax.text(
        -0.25,
        2.25,
        "固定：N = 実績需要D − 実績PV・風力・原子力R　／　参照上限U・保存送電枠\n"
        "変数：調整出力gと地域間純移送f。元Nの9地域同時成立を検査する。",
        fontsize=11,
        va="top",
    )
    ax.text(
        -0.25,
        -0.45,
        "矢印：地域間の接続（両方向）。実潮流・距離・実線容量を示さない。中央3地域はB/Cで拡大。",
        fontsize=10,
    )
    ax.set(xlim=(-0.4, 7.2), ylim=(-0.8, 3.02))
    ax.axis("off")
    triangle(fig.add_subplot(gs[1, 0]), False)
    triangle(fig.add_subplot(gs[1, 1]), True)
    ax = fig.add_subplot(gs[2, :])
    ax.axis("off")
    ax.text(
        0,
        1.05,
        "D：人工値で「個別とフェンスの同時適用」を読む（全てMW、実データの容量ではない）",
        fontsize=12,
        weight="bold",
    )
    examples = [artificial_fence_example(80, -30), artificial_fence_example(80, 30)]
    rows = [
        [
            r["S"],
            r["E"],
            r["F_H"],
            "各区間は範囲内",
            "フェンス外 → 不可" if not r["fence_ok"] else "フェンス内 → 可",
        ]
        for r in examples
    ]
    t = ax.table(
        cellText=rows,
        colLabels=["S", "E", "S−E", "各区間：−100〜100", "フェンス：−60〜60"],
        cellLoc="center",
        bbox=[0, 0.32, 1, 0.52],
        colWidths=[0.1, 0.1, 0.14, 0.31, 0.35],
    )
    t.auto_set_font_size(False)
    t.set_fontsize(11)
    ax.text(
        0,
        0.91,
        "上限を足すのではない：同じ移送量S/Eが、両方の条件を満たす必要がある。",
        fontsize=11,
    )
    ax.text(
        0,
        0.12,
        "2026/3/13の48コマは送電入力保留。保存表への適用期間と、物理的切替完了時刻を区別する。",
        fontsize=10,
        va="top",
    )
    ax.text(
        0,
        -0.16,
        "新フェンスでは F_C−F_K=F_H。独立した追加能力として合算しない。\n"
        "全期間で保存C−M−Wの包絡（既存の仕様B）を用いる。",
        fontsize=10,
        va="top",
    )
    save(fig, "01_method_readable")
    pd.DataFrame(examples).to_csv(REPORT / "qa/artificial_fence_examples.csv", index=False)
    edge = pd.DataFrame(
        [
            dict(number=i + 1, name=name, sending_area=a, receiving_area=b, capacity_depicted=False)
            for i, (name, a, b) in enumerate(EDGES)
        ]
    )
    edge.to_csv(REPORT / "qa/method_edge_legend.csv", index=False)


def central_figure():
    """棒グラフ・凡例・注記にそれぞれ独立した領域を確保する。"""
    f = pd.read_csv(REPORT / "qa/central_three_categories.csv")
    fig, ax = plt.subplots(figsize=(11.7, 8.5))
    fig.subplots_adjust(left=0.09, right=0.98, top=0.88, bottom=0.30)
    bottom = np.zeros(8)
    colors = ["#3c80a6", "#edab48", "#986daa"]
    for cat, label, color in zip(CATEGORIES, LABELS, colors):
        values = f[cat].to_numpy() / 16848 * 100
        ax.bar(range(8), values, bottom=bottom, label=label, color=color)
        for i, (v, n) in enumerate(zip(values, f[cat])):
            if v >= 8:
                ax.text(
                    i,
                    bottom[i] + v / 2,
                    f"{n:,}\n({v:.1f}%)",
                    ha="center",
                    va="center",
                    fontsize=10,
                )
        bottom += values
    ax.set_xticks(range(8), f.entity_ja)
    ax.set_ylim(0, 100)
    ax.set_ylabel("共通16,848コマに占める割合〔%〕")
    ax.set_title(
        "固定供給を残す名参照調整供給除去：送電枠の有無による需要充足\n停止控除なし／8法人・FY2025の同じ時刻",
        fontsize=14,
    )
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.54, 0.235),
        ncol=1,
        fontsize=11,
        frameon=False,
    )
    fig.text(
        0.09,
        0.045,
        "年間17,520のうち全法人不成立624・送電入力保留48を比較対象外として別掲。\n包絡解除は実際の増強効果ではなく、限定設備・名参照代理・保存留保枠に条件付けた対照。",
        fontsize=10,
    )
    save(fig, "02_central_three_categories")


def main():
    configure()
    method()
    central_figure()
    colors = ["#3c80a6", "#edab48", "#986daa"]
    # 候補は点+四分位範囲だけ。箱ひげ/回帰/有意差検定を作らない。
    stats = pd.read_csv(REPORT / "qa/price_demand_fixed_output_by_category.csv")
    missing = pd.read_csv(REPORT / "qa/multiplier_missing_in_same_view.csv").set_index("area")
    areas = ["北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州"]
    fig, axes = plt.subplots(3, 3, figsize=(12, 10))
    for ax, area in zip(axes.flat, areas):
        g = stats[stats.area.eq(area) & stats.variable.eq("area_price")].set_index(
            "central_category"
        )
        for i, (cat, color) in enumerate(zip(CATEGORIES, colors)):
            r = g.loc[cat]
            if r.frames == 0:
                ax.text(
                    i,
                    0.08,
                    "該当なし",
                    transform=ax.get_xaxis_transform(),
                    ha="center",
                    fontsize=9,
                    color="gray",
                )
                continue
            ax.vlines(i, r.q25, r.q75, color=color, lw=5, alpha=0.75)
            ax.scatter(i, r["median"], color=color, s=45, zorder=3)
            ax.text(
                i,
                0.95,
                f"n={int(r.frames):,}",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=8,
            )
        ax.set_xticks(range(3), ["保存枠で成立", "解除で成立", "解除でも\n不成立"], fontsize=9)
        ax.set_ylabel("spotエリア価格〔円/kWh〕")
        ax.set_title(area)
        ax.set_xlim(-0.5, 2.5)
        ax.margins(y=0.3)
        if area in ["東京", "中部"]:
            r = missing.loc[area]
            ax.set_title(
                f"{area}\n倍率未算定：{int(r.annual_uncomputed):,}/17,520（年）\n"
                f"{int(r.common_uncomputed):,}/16,848（共通母集団）",
                fontsize=9,
            )
    fig.suptitle(
        "価格の記述候補：主図と同じ時刻・同じ3区分\n点＝中央値、線＝25–75%点。構成差（需要/R/時間帯/季節）は別表。因果効果ではない。",
        fontsize=13,
    )
    fig.tight_layout(h_pad=3.5, rect=[0, 0, 1, 0.94])
    save(fig, "A01_price_same_categories_candidate")
    (REPORT / "qa/figure_audit.json").write_text(
        json.dumps(
            dict(
                new_lp_calls=0,
                formats=["pdf", "svg", "png"],
                method_example="artificial_only_not_line_capacity",
                common_denominator=16848,
                price_composition_dimensions=["D", "R", "N", "season", "time_band", "month"],
                price_observations_repeated_by_scenario=False,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()

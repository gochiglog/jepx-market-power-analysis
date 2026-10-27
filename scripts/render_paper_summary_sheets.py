"""一ページの先行研究対照と問い→結果→図→限定。既存CSVのみ描画。"""

import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.plot_paper_central_result import configure

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/28_paper_central_result"


def cell(text, width):
    return "\n".join(textwrap.wrap(str(text), width=width, break_long_words=True))


def main():
    configure()
    f = pd.read_csv(REPORT / "qa/technical_reference_contrast.csv")
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    fig.subplots_adjust(left=0.025, right=0.975, top=0.98, bottom=0.025)
    ax.text(
        0, 0.98, "先行研究対照：TCRSI・TCMPIと今回の年間診断", fontsize=15, weight="bold", va="top"
    )
    ax.text(
        0,
        0.932,
        "Lee, Baldick & Hur, Technical Reference (2011-05-10), §4–6。ページは印刷/物理が一致。",
        fontsize=10,
        va="top",
    )
    ax.text(
        0,
        0.886,
        r"式(14): $\max_{q,t}t;\ 1^Tq=t1^Td;\ H_gq-H_d(td)\leq b;$"
        r"$\ 0\leq q_j\leq\bar q_j\ (j\notin G_i),\ q_j=0\ (j\in G_i)$",
        fontsize=11,
        va="top",
    )
    rows = []
    for r in f.itertuples():
        rows.append(
            [
                cell(r.comparison_axis, 11),
                cell(r.original_technical_reference, 25),
                cell(r.current_diagnostic + "。" + r.interpretation, 35),
                cell(r.page_equation, 22),
            ]
        )
    t = ax.table(
        cellText=rows,
        colLabels=["対照軸", "原Technical Reference", "今回と一致/相違", "原文の根拠"],
        colWidths=[0.11, 0.28, 0.38, 0.23],
        cellLoc="left",
        bbox=[0, 0.18, 1, 0.65],
    )
    t.auto_set_font_size(False)
    t.set_fontsize(8)
    for (i, j), c in t.get_celld().items():
        c.PAD = 0.04
        if i == 0:
            c.set_facecolor("#dfebf0")
    ax.text(
        0,
        0.145,
        "4母線（図6/表3、pp19–20）：g3はRSI=1／TCRSI=0.58。負荷を独立に動かすと地理的配分が歪む。\n今回の主図は元N固定。局所倍率の散布図は、原図7のTCRSI–TCMPI対照ではない。",
        fontsize=9,
        va="top",
    )
    ax.text(
        0,
        0.065,
        "今回：事後的な限定構造評価。入札時情報・利益増加・実際の行使を認定しない。新規性/国内初は主張しない。\n"
        "原文：https://users.ece.utexas.edu/~baldick/papers/firm_index_technical.pdf"
        "　（保存原本SHAは確認記録へ）",
        fontsize=9,
        va="top",
    )
    for ext in ["pdf", "svg", "png"]:
        fig.savefig(REPORT / ("literature_contrast_one_page." + ext), dpi=160)
    plt.close(fig)
    f = pd.read_csv(REPORT / "qa/question_result_figure_limit.csv")
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.axis("off")
    fig.subplots_adjust(left=0.025, right=0.975, top=0.975, bottom=0.025)
    ax.text(
        0, 0.98, "論文の中心結果：問い → 結果 → 図 → 限定", fontsize=15, weight="bold", va="top"
    )
    ax.text(
        0,
        0.91,
        "固定供給を残す限定診断では、調整供給の代替可否は保存送電枠の有無で大きく変わり、\nその程度は法人で異なる。主図は同じ元需要・同じ16,848時刻の3区分。",
        fontsize=12,
        va="top",
    )
    rows = []
    for r in f.itertuples():
        rows.append(
            [
                cell(r.role + "：" + r.question, 24),
                cell(r.claimable_result, 31),
                cell(r.figure, 22),
                cell(r.limitation, 32),
            ]
        )
    t = ax.table(
        cellText=rows,
        colLabels=["研究の問い/位置づけ", "主張できる結果", "対応図", "限定"],
        colWidths=[0.23, 0.29, 0.2, 0.28],
        cellLoc="left",
        bbox=[0, 0.27, 1, 0.49],
    )
    t.auto_set_font_size(False)
    t.set_fontsize(9)
    for (i, j), c in t.get_celld().items():
        c.PAD = 0.05
        if i == 0:
            c.set_facecolor("#dfebf0")
    ax.text(
        0,
        0.2,
        "共通分母：双方の全法人時が成立する16,848。年間17,520から全法人不成立624＋送電入力保留48を別掲。\nJERAは一法人。東京/中部の価格と地域倍率は別地域として参照し、企業数へ重複計上しない。",
        fontsize=11,
        va="top",
    )
    ax.text(
        0,
        0.10,
        "推奨配置：本文は方法図＋中心3区分。17条件図・月別・価格候補は付録へ。\n停止控除なしは説明用。最終baseline・契約帰属・論文の最終結論は今回採用しない。",
        fontsize=11,
        va="top",
    )
    for ext in ["pdf", "svg", "png"]:
        fig.savefig(REPORT / ("one_page_result." + ext), dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()

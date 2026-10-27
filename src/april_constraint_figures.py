"""工程23の説明図4枚。全1,440コマの分母・元区分を保持。"""

import hashlib
import json
import os
import tempfile

import numpy as np
import pandas as pd

CODES = ["hokkaido", "tohoku", "jera", "hokuriku", "kansai", "chugoku", "shikoku", "kyushu"]
NAMES = [
    "北海道電力",
    "東北電力",
    "JERA",
    "北陸電力",
    "関西電力",
    "中国電力",
    "四国電力",
    "九州電力",
]
STATES = [
    "all17_base_feasible_removed_infeasible",
    "all17_base_feasible_removed_feasible",
    "removed_varies_among_baseline_feasible",
    "baseline_scope_differs",
    "other_held",
]
LABELS = [
    "17条件とも全法人成立・除去後不成立",
    "17条件とも全法人成立・除去後成立",
    "全法人成立条件でも除去後判定が変化",
    "全法人不成立の条件を含む",
    "その他保留／失敗",
]
COLORS = ["#D55E00", "#56B4E9", "#CC79A7", "#999999", "#333333"]


def render(output, report):
    os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir() + "/april_trial_mpl")
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.ticker import PercentFormatter

    fonts = [
        f
        for f in font_manager.fontManager.ttflist
        if f.name in ["Hiragino Sans", "IPAexGothic", "Noto Sans CJK JP"]
    ]
    if not fonts:
        raise ValueError("日本語フォントが必要")
    plt.rcParams.update(
        {"font.family": fonts[0].name, "axes.unicode_minus": False, "font.size": 10}
    )
    qa = report / "qa"
    folder = report / "figures"
    folder.mkdir(parents=True, exist_ok=True)
    files = []

    def save(fig, name):
        for ext in ["png", "pdf"]:
            p = folder / (name + "." + ext)
            if p.exists():
                raise ValueError("既存図への上書き禁止")
            fig.savefig(p, dpi=170, bbox_inches="tight")
            files.append({"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        plt.close(fig)

    data = pd.read_csv(qa / "all17_simultaneous_summary.csv")
    data = data[data.network_scenario.eq("saved_plan_constraints")]
    cases = ["reference_no_deduction"] + sorted(
        x for x in data.supply_trial_scenario.unique() if x != "reference_no_deduction"
    )

    def short(x):
        if x == "reference_no_deduction":
            return "停止控除なし"
        result = (
            x.replace("58852", "58,852版")
            .replace("7828", "7,828版")
            .replace("_early", "早端")
            .replace("_late", "遅端")
            .replace("__full_stop_zero", " 全停止0")
            .replace("__mean_reduction", " 平均量")
            .replace("__record_held_reference", "＋保留戻し")
        )
        return result

    failures = (
        data[data.transition.eq("all_feasible_removed_infeasible")]
        .pivot(index="supply_trial_scenario", columns="entity_code", values="time_keys")
        .reindex(index=cases, columns=CODES)
        .fillna(0)
    )
    baseline = (
        data[data.transition.eq("all_firms_already_infeasible")]
        .pivot(index="supply_trial_scenario", columns="entity_code", values="time_keys")
        .reindex(index=cases, columns=CODES)
        .fillna(0)
    )
    assert baseline.nunique(axis=1).eq(1).all()
    feasible = (
        data[data.transition.eq("all_feasible_removed_feasible")]
        .pivot(index="supply_trial_scenario", columns="entity_code", values="time_keys")
        .reindex(index=cases, columns=CODES)
        .fillna(0)
    )
    assert (feasible + failures + baseline).eq(1440).all().all()
    fig, ax = plt.subplots(figsize=(15, 12))
    im = ax.imshow(failures.to_numpy() / 1440, vmin=0, vmax=1, cmap="Oranges", aspect="auto")
    for i in range(17):
        for j in range(8):
            ax.text(
                j,
                i,
                f"成立{int(feasible.iloc[i, j]):,}\n不成立{int(failures.iloc[i, j]):,}",
                ha="center",
                va="center",
                fontsize=8,
                color="white" if failures.iloc[i, j] > 900 else "black",
            )
    ax.set(
        xticks=range(8),
        xticklabels=NAMES,
        yticks=range(17),
        yticklabels=[short(x) + f"〔全法人不成立{int(baseline.loc[x].iloc[0])}〕" for x in cases],
        title=(
            "名参照調整供給の除去：17供給条件／保存送電制約あり\n"
            "セル上段＝除去後成立、下段＝全法人成立→除去後不成立。行名〔〕は全法人時から不成立\n"
            "色＝除去で不成立になる割合（分母1,440）。数字はコマ数"
        ),
    )
    fig.colorbar(im, ax=ax, format=PercentFormatter(1), label="全1,440コマに占める割合")
    fig.tight_layout()
    save(fig, "01_all17_removal_failure")
    a = pd.read_csv(qa / "condition_consistency_counts.csv")
    a = a[a.network_scenario.eq("saved_plan_constraints")]
    table = (
        a.pivot(index="entity_code", columns="consistency_state", values="time_keys")
        .reindex(index=CODES, columns=STATES)
        .fillna(0)
    )
    assert table.sum(axis=1).eq(1440).all()
    fig, ax = plt.subplots(figsize=(14, 7))
    bottom = np.zeros(8)
    for s, label, color in zip(STATES, LABELS, COLORS, strict=True):
        v = table[s].to_numpy() / 1440
        ax.bar(range(8), v, bottom=bottom, label=label, color=color)
        bottom += v
    ax.set(
        xticks=range(8),
        xticklabels=NAMES,
        ylim=(0, 1),
        ylabel="全1,440コマに占める割合",
        title="今回の17条件内で判定が一致する範囲\n保存送電制約あり。全法人時の不成立を除去効果へ混ぜない",
    )
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, fontsize=9)
    fig.subplots_adjust(bottom=0.26)
    save(fig, "02_condition_consistency")
    release = pd.read_csv(qa / "release_simultaneous_summary.csv")
    reg = pd.read_csv(qa / "release_registry.csv")
    codes = ["hokkaido", "hokuriku", "jera", "kyushu"]
    cases = [x for x in reg.release_case if x != "saved_all"]
    t = release.pivot(
        index="release_case",
        columns="entity_code",
        values="recovered_where_baseline_already_feasible",
    ).reindex(index=cases, columns=codes)
    labels = []
    for case in cases:
        x = json.loads(reg[reg.release_case.eq(case)].released_constraints_json.iloc[0])
        labels.append(
            "全11組の包絡解除"
            if case == "release_all"
            else "北陸関連：南福光＋越前＋フェンス"
            if case == "release_hokuriku_overlap_group"
            else x[0]
        )
    fig, ax = plt.subplots(figsize=(12, 9))
    im = ax.imshow(t.to_numpy(), cmap="Blues", aspect="auto", vmin=0)
    for i in range(len(cases)):
        for j in range(4):
            ax.text(
                j,
                i,
                f"{int(t.iloc[i, j]):,}",
                ha="center",
                va="center",
                color="white" if t.iloc[i, j] > 900 else "black",
            )
    ax.set(
        xticks=range(4),
        xticklabels=["北海道電力", "北陸電力", "JERA", "九州電力"],
        yticks=range(len(cases)),
        yticklabels=labels,
        title="包絡を解除して企業除去後に9地域同時成立へ変わるコマ\n停止控除なし／元から全法人成立の1,430コマ内。上下包絡を同時解除",
    )
    fig.colorbar(im, ax=ax, label="不成立→成立のコマ数")
    fig.tight_layout()
    save(fig, "03_constraint_release")
    alltime = pd.read_parquet(output / "condition_consistency_all_times.parquet")
    alltime = alltime[alltime.network_scenario.eq("saved_plan_constraints")]
    matrix = []
    for code in codes:
        g = alltime[alltime.entity_code.eq(code)].sort_values(["delivery_date", "period"])
        assert len(g) == 1440
        matrix.append([STATES.index(x) for x in g.consistency_state])
    fig, ax = plt.subplots(figsize=(15, 4))
    ax.imshow(
        matrix,
        aspect="auto",
        cmap=ListedColormap(COLORS),
        vmin=-0.5,
        vmax=4.5,
        interpolation="nearest",
    )
    ax.set(
        yticks=range(4),
        yticklabels=["北海道電力", "北陸電力", "JERA", "九州電力"],
        xticks=np.arange(0, 1440, 48) + 23.5,
        xticklabels=[str(x) for x in range(1, 31)],
        xlabel="2025年4月の日（各日48コマを時刻順に表示）",
        title="どの時刻で17条件内の判定が変わるか／保存送電制約あり",
    )
    from matplotlib.patches import Patch

    ax.legend(
        handles=[Patch(color=c, label=label) for c, label in zip(COLORS, LABELS, strict=True)],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
        ncol=2,
        fontsize=9,
    )
    fig.subplots_adjust(bottom=0.35)
    save(fig, "04_focus_time_states")
    (qa / "figure_manifest.json").write_text(
        json.dumps(
            {
                "figures": files,
                "font": fonts[0].name,
                "main_denominator": 1440,
                "constraint_release_comparable_denominator": 1430,
                "figures_count": 4,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )

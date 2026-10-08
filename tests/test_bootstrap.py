"""Issue #1の実行環境と研究データのGit除外を検証する。"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


@pytest.mark.parametrize(
    "module",
    ["pandas", "numpy", "pyarrow", "openpyxl", "scipy", "statsmodels", "matplotlib", "jupyter"],
)
def test_required_dependencies_import(module):
    importlib.import_module(module)


@pytest.mark.parametrize("layer", ["raw", "interim", "processed"])
def test_research_data_is_ignored(layer):
    # 実ファイルは作らず、未作成のパスにも適用されるGit除外規則を確認する。
    path = f"data/{layer}/source/2025/example.csv"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == path

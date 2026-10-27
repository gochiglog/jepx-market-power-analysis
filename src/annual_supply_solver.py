"""同じ地域輸送LPをHiGHSモデル再利用で解く。q=対象純需要MW、lambda=q/N。"""

import highspy
import numpy as np
from scipy.sparse import csr_matrix

from src.net_demand_network import AREAS, EDGES, FENCE_COEFFICIENTS

A = np.zeros((9, 10))
for k, (_, sender, receiver) in enumerate(EDGES):
    A[AREAS.index(sender), k] = -1
    A[AREAS.index(receiver), k] = 1
CONSTRAINT_NAMES = [e[0] for e in EDGES] + ["北陸フェンス", "中部フェンス", "関西フェンス"]
C = np.vstack(
    [
        np.eye(10),
        FENCE_COEFFICIENTS["北陸フェンス"],
        FENCE_COEFFICIENTS["中部フェンス"],
        FENCE_COEFFICIENTS["関西フェンス"],
    ]
)


class TransportModel:
    """9地域のgを明示。過不足変数なし。解は毎回独立に残差確認する。"""

    def __init__(self, target=None):
        self.target = target
        nv = 19 + int(target is not None)
        matrix = np.zeros((22, nv))
        matrix[:9, :9] = np.eye(9)
        matrix[:9, 9:19] = A
        matrix[9:, 9:19] = C
        if target is not None:
            matrix[target, -1] = -1
        self.matrix = matrix
        lp = highspy.HighsLp()
        lp.num_col_ = nv
        lp.num_row_ = 22
        lp.col_cost_ = np.r_[np.zeros(nv - 1), -1] if target is not None else np.zeros(nv)
        lp.col_lower_ = np.r_[
            np.zeros(9), np.full(10, -highspy.kHighsInf), [0] if target is not None else []
        ]
        lp.col_upper_ = np.full(nv, highspy.kHighsInf)
        lp.row_lower_ = np.full(22, -highspy.kHighsInf)
        lp.row_upper_ = np.full(22, highspy.kHighsInf)
        sparse = csr_matrix(matrix)
        lp.a_matrix_.format_ = highspy.MatrixFormat.kRowwise
        lp.a_matrix_.start_ = sparse.indptr
        lp.a_matrix_.index_ = sparse.indices
        lp.a_matrix_.value_ = sparse.data
        self.h = highspy.Highs()
        for key, value in [
            ("output_flag", False),
            ("threads", 1),
            ("solver", "simplex"),
            ("presolve", "off"),
            ("primal_feasibility_tolerance", 1e-8),
        ]:
            self.h.setOptionValue(key, value)
        assert self.h.passModel(lp) == highspy.HighsStatus.kOk
        self.calls = 0

    def solve(self, net, upper, lo, hi):
        n, u = np.asarray(net, float), np.asarray(upper, float)
        if not np.isfinite(np.r_[n, u]).all():
            return {"status": "input_held", "value": None}
        if (u < 0).any():
            return {"status": "negative_upper_held", "value": None}
        b = n.copy()
        if self.target is not None:
            b[self.target] = 0
        lower = np.r_[b, lo]
        higher = np.r_[b, hi]
        self.h.changeRowsBounds(22, np.arange(22, dtype=np.int32), lower, higher)
        self.h.changeColsBounds(9, np.arange(9, dtype=np.int32), np.zeros(9), u)
        self.h.run()
        self.calls += 1
        status = self.h.getModelStatus()
        if status == highspy.HighsModelStatus.kInfeasible:
            return {"status": "infeasible", "value": None}
        if status != highspy.HighsModelStatus.kOptimal:
            return {"status": "numerical_failure", "value": None, "solver_status": str(status)}
        x = np.asarray(self.h.getSolution().col_value)
        achieved = self.matrix @ x
        err = max(
            float(np.maximum(lower - achieved, 0).max()),
            float(np.maximum(achieved - higher, 0).max()),
            float(np.maximum(-x[:9], 0).max()),
            float(np.maximum(x[:9] - u, 0).max()),
            max(0, -x[-1]) if self.target is not None else 0,
        )
        if err > 1e-6:
            raise ValueError(("年間LP残差", err))
        return {
            "status": "feasible",
            "value": float(x[-1]) if self.target is not None else None,
            "residual_mw": err,
            "generation_mw": x[:9].tolist(),
            "flows_mw": x[9:19].tolist(),
        }

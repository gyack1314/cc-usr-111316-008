"""多边投入价格指数：双边 Fisher、GEKS、缺边加权图估计与滚动接续。

统计口径见 ``docs/data-contract.md``。本模块不读取文件；调用方先用
:func:`analysis_data.panel.load_panel` 取得规范化面板。

方法选择是显式的：

* 请求单元集的每一条无序对都通过覆盖率门槛时，使用 ``geks-fisher``；
* 只要有任意一对缺少可比边，就使用 ``weighted-graph-least-squares``，
  绝不把缺边图冒充完整 GEKS；
* 图不连通时只在各连通分量内给出相对指数，不跨分量连接水平。

所有双边量先以 :class:`fractions.Fraction` 精确计算，再取几何平均。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Sequence

from .exact import AnalysisError, ContractError, fraction_text, solve

GEKS_METHOD = "geks-fisher"
GRAPH_METHOD = "weighted-graph-least-squares"
METHOD_LABELS = {
    GEKS_METHOD: "完整 GEKS-Fisher（请求单元集两两均有可比边）",
    GRAPH_METHOD: "缺边加权图最小二乘（非完整 GEKS；对数双边差拟合）",
}


@dataclass(frozen=True)
class Bilateral:
    """从 ``unit_a`` 到 ``unit_b`` 的 Fisher 双边比较。

    ``fisher`` 解释为 ``b`` 的价格水平相对 ``a``；反向比较满足
    ``fisher(b, a) = 1 / fisher(a, b)``。
    """

    unit_a: str
    unit_b: str
    common_products: tuple[str, ...]
    laspeyres: Fraction
    paasche: Fraction
    fisher: float
    log_fisher: float
    common_expenditure_a: Fraction
    common_expenditure_b: Fraction
    total_expenditure_a: Fraction
    total_expenditure_b: Fraction
    coverage_a: Fraction
    coverage_b: Fraction
    weight: float
    accepted: bool
    rejection: str | None

    def as_dict(self) -> dict[str, Any]:
        usable = self.accepted or not math.isnan(self.fisher)
        return {
            "unit_a": self.unit_a,
            "unit_b": self.unit_b,
            "common_products": list(self.common_products),
            "laspeyres": fraction_text(self.laspeyres) if usable else None,
            "paasche": fraction_text(self.paasche) if usable else None,
            "fisher": self.fisher if usable else None,
            "log_fisher": self.log_fisher if usable else None,
            "common_expenditure_a": fraction_text(self.common_expenditure_a),
            "common_expenditure_b": fraction_text(self.common_expenditure_b),
            "total_accepted_expenditure_a": fraction_text(self.total_expenditure_a),
            "total_accepted_expenditure_b": fraction_text(self.total_expenditure_b),
            "coverage_a": fraction_text(self.coverage_a),
            "coverage_b": fraction_text(self.coverage_b),
            "coverage_min": float(min(self.coverage_a, self.coverage_b)),
            "weight": self.weight,
            "accepted": self.accepted,
            "rejection": self.rejection,
        }


def compare_units(
    panel: Any, unit_a: str, unit_b: str, threshold: Fraction | None = None
) -> Bilateral:
    """计算两个单元的共同篮子 Laspeyres、Paasche 和 Fisher。

    只使用两端都有正价格、正数量的产品；覆盖率的分母是该单元全部有效
    支出（不含拒绝账）。任一侧覆盖率低于门槛时 ``accepted`` 为假。
    """
    if unit_a == unit_b:
        raise ContractError("双边比较需要两个不同单元")
    cells_a = panel.products(unit_a)
    cells_b = panel.products(unit_b)
    common = tuple(sorted(set(cells_a) & set(cells_b)))
    limit = panel.coverage_threshold if threshold is None else threshold
    total_a = panel.accepted_expenditure(unit_a)
    total_b = panel.accepted_expenditure(unit_b)
    if not common:
        zero = Fraction(0)
        return Bilateral(
            unit_a=unit_a,
            unit_b=unit_b,
            common_products=(),
            laspeyres=Fraction(1),
            paasche=Fraction(1),
            fisher=float("nan"),
            log_fisher=float("nan"),
            common_expenditure_a=zero,
            common_expenditure_b=zero,
            total_expenditure_a=total_a,
            total_expenditure_b=total_b,
            coverage_a=zero,
            coverage_b=zero,
            weight=0.0,
            accepted=False,
            rejection="no-common-products",
        )
    common_expenditure_a = sum(
        (cells_a[product].expenditure for product in common), Fraction(0)
    )
    common_expenditure_b = sum(
        (cells_b[product].expenditure for product in common), Fraction(0)
    )
    coverage_a = common_expenditure_a / total_a if total_a else Fraction(0)
    coverage_b = common_expenditure_b / total_b if total_b else Fraction(0)
    sum_p_b_q_a = sum(
        (cells_b[product].unit_value * cells_a[product].quantity for product in common),
        Fraction(0),
    )
    sum_p_a_q_a = common_expenditure_a
    sum_p_b_q_b = common_expenditure_b
    sum_p_a_q_b = sum(
        (cells_a[product].unit_value * cells_b[product].quantity for product in common),
        Fraction(0),
    )
    if sum_p_a_q_a <= 0 or sum_p_a_q_b <= 0:
        return Bilateral(
            unit_a=unit_a,
            unit_b=unit_b,
            common_products=common,
            laspeyres=Fraction(1),
            paasche=Fraction(1),
            fisher=float("nan"),
            log_fisher=float("nan"),
            common_expenditure_a=common_expenditure_a,
            common_expenditure_b=common_expenditure_b,
            total_expenditure_a=total_a,
            total_expenditure_b=total_b,
            coverage_a=coverage_a,
            coverage_b=coverage_b,
            weight=0.0,
            accepted=False,
            rejection="zero-basket",
        )
    laspeyres = sum_p_b_q_a / sum_p_a_q_a
    paasche = sum_p_b_q_b / sum_p_a_q_b
    fisher = math.sqrt(float(laspeyres) * float(paasche))
    log_fisher = 0.5 * (math.log(float(laspeyres)) + math.log(float(paasche)))
    weight = math.sqrt(float(coverage_a) * float(coverage_b))
    rejection = None
    if coverage_a < limit or coverage_b < limit:
        rejection = "coverage-below-threshold"
    return Bilateral(
        unit_a=unit_a,
        unit_b=unit_b,
        common_products=common,
        laspeyres=laspeyres,
        paasche=paasche,
        fisher=fisher,
        log_fisher=log_fisher,
        common_expenditure_a=common_expenditure_a,
        common_expenditure_b=common_expenditure_b,
        total_expenditure_a=total_a,
        total_expenditure_b=total_b,
        coverage_a=coverage_a,
        coverage_b=coverage_b,
        weight=weight,
        accepted=rejection is None,
        rejection=rejection,
    )


def bilateral_edges(panel: Any, units: Sequence[str]) -> list[Bilateral]:
    """按单元顺序输出所有无序对的候选边，包括被拒绝的对。"""
    result: list[Bilateral] = []
    for i, unit_a in enumerate(units):
        for unit_b in units[i + 1 :]:
            result.append(compare_units(panel, unit_a, unit_b))
    return result


def _accepted_edges(
    edges: Sequence[Bilateral], index_of: dict[str, int]
) -> dict[tuple[int, int], Bilateral]:
    result: dict[tuple[int, int], Bilateral] = {}
    for edge in edges:
        if edge.accepted:
            i, j = index_of[edge.unit_a], index_of[edge.unit_b]
            result[(min(i, j), max(i, j))] = edge
    return result


def _components(
    nodes: Sequence[str],
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
) -> list[list[str]]:
    """按单元顺序做确定性广度优先，得到含孤立点的连通分量。"""
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for (i, j) in accepted:
        a, b = nodes[i], nodes[j]
        adjacency[a].append(b)
        adjacency[b].append(a)
    for node in adjacency:
        adjacency[node].sort(key=index_of.__getitem__)
    seen: set[str] = set()
    components: list[list[str]] = []
    for start in nodes:
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        component: list[str] = []
        while queue:
            node = queue.pop(0)
            component.append(node)
            for neighbour in adjacency[node]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        components.append(component)
    return components


def _graph_solution(
    nodes: Sequence[str],
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
    anchor: str,
) -> dict[str, float]:
    """在单个连通分量上解加权图最小二乘，锚点固定为 0。

    目标函数为 ``sum w_ab * (z_b - z_a - log F_ab)^2``，固定锚点即为
    最小范数解。``nodes`` 是分量内单元，``accepted`` 的键是请求单元集的
    全局下标，因此必须经单元名换成本地位置。
    """
    node_index = {node: position for position, node in enumerate(nodes)}
    local_edges = []
    for (i, j), edge in sorted(accepted.items()):
        a, b = edge.unit_a, edge.unit_b
        if a in node_index and b in node_index:
            local_edges.append((node_index[a], node_index[b], edge))
    solution = {node: 0.0 for node in nodes}
    if len(nodes) == 1:
        return solution
    anchor_position = node_index[anchor]
    unknown = [position for position in range(len(nodes)) if position != anchor_position]
    position_to_unknown = {position: offset for offset, position in enumerate(unknown)}
    size = len(unknown)
    matrix = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for i, j, edge in local_edges:
        weight = edge.weight
        difference = edge.log_fisher
        for row in (i, j):
            if row != anchor_position:
                matrix[position_to_unknown[row]][position_to_unknown[row]] += weight
        if i != anchor_position and j != anchor_position:
            matrix[position_to_unknown[i]][position_to_unknown[j]] -= weight
            matrix[position_to_unknown[j]][position_to_unknown[i]] -= weight
        if i != anchor_position:
            rhs[position_to_unknown[i]] -= weight * difference
        if j != anchor_position:
            rhs[position_to_unknown[j]] += weight * difference
    values = solve(matrix, rhs)
    for position, value in zip(unknown, values):
        solution[nodes[position]] = value
    solution[anchor] = 0.0
    return solution


def _tree(
    nodes: Sequence[str], accepted: dict[tuple[int, int], Bilateral], index_of: dict[str, int]
) -> tuple[dict[str, str], list[tuple[int, int]]]:
    """按规范边顺序建立生成森林，返回父指针与非树边（环路）。"""
    adjacency: dict[str, list[tuple[str, int, int]]] = {node: [] for node in nodes}
    for (i, j) in sorted(accepted):
        a, b = nodes[i], nodes[j]
        adjacency[a].append((b, i, j))
        adjacency[b].append((a, i, j))
    parent: dict[str, str] = {}
    tree_edges: set[tuple[int, int]] = set()
    seen: set[str] = set()
    for start in nodes:
        if start in seen:
            continue
        seen.add(start)
        parent[start] = start
        queue = [start]
        while queue:
            node = queue.pop(0)
            for neighbour, i, j in sorted(
                adjacency[node], key=lambda item: index_of[item[0]]
            ):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                parent[neighbour] = node
                tree_edges.add((i, j))
                queue.append(neighbour)
    non_tree = [pair for pair in sorted(accepted) if pair not in tree_edges]
    return parent, non_tree


def _log_between(
    unit_a: str,
    unit_b: str,
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
) -> float:
    """返回 ``log(level_b / level_a)``，方向由参数决定。"""
    if unit_a == unit_b:
        return 0.0
    i, j = index_of[unit_a], index_of[unit_b]
    edge = accepted[(min(i, j), max(i, j))]
    return edge.log_fisher if i < j else -edge.log_fisher


def _path_log(
    start: str,
    end: str,
    parent: dict[str, str],
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
) -> float:
    """沿生成树返回 ``log(level_end / level_start)``。"""
    ancestors: dict[str, float] = {start: 0.0}
    current = start
    accumulated = 0.0
    while parent.get(current, current) != current:
        next_node = parent[current]
        accumulated += _log_between(current, next_node, accepted, index_of)
        current = next_node
        ancestors[current] = accumulated
    target = end
    accumulated = 0.0
    while target not in ancestors and parent.get(target, target) != target:
        next_node = parent[target]
        accumulated += _log_between(target, next_node, accepted, index_of)
        target = next_node
    if target not in ancestors:
        raise AnalysisError("生成树路径断裂")
    return ancestors[target] - accumulated


def _loop_inconsistencies(
    nodes: Sequence[str],
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
    parent: dict[str, str],
    non_tree: Sequence[tuple[int, int]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for (i, j) in non_tree:
        a, b = nodes[i], nodes[j]
        observed = _log_between(a, b, accepted, index_of)
        along_tree = _path_log(a, b, parent, accepted, index_of)
        records.append(
            {
                "edge": f"{a}->{b}",
                "tree_path_log": along_tree,
                "inconsistency": observed - along_tree,
            }
        )
    return records


def _geks_values(
    nodes: Sequence[str],
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
    base: str,
) -> dict[str, float]:
    """完整图上的 GEKS-Fisher 对数水平，基准取 0。"""
    n = len(nodes)

    def log_ratio(unit_a: str, unit_b: str) -> float:
        return _log_between(unit_a, unit_b, accepted, index_of)

    values = {
        unit: sum(log_ratio(k, unit) - log_ratio(k, base) for k in nodes) / n
        for unit in nodes
    }
    values[base] = 0.0
    return values


def _residual_records(
    accepted: dict[tuple[int, int], Bilateral],
    index_of: dict[str, int],
    nodes: Sequence[str],
    log_values: dict[str, float | None],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for (i, j), edge in sorted(accepted.items()):
        a, b = nodes[i], nodes[j]
        level_a, level_b = log_values.get(a), log_values.get(b)
        if level_a is None or level_b is None:
            continue
        fitted = level_b - level_a
        records.append(
            {
                "unit_a": a,
                "unit_b": b,
                "observed_log_fisher": edge.log_fisher,
                "fitted_log_difference": fitted,
                "residual": fitted - edge.log_fisher,
                "weight": edge.weight,
            }
        )
    return records


def _edge_matrices(
    units: Sequence[str],
    edges: Sequence[Bilateral],
) -> dict[str, Any]:
    lookup = {(edge.unit_a, edge.unit_b): edge for edge in edges}
    log_rows: list[list[float | None]] = []
    accepted_rows: list[list[bool]] = []
    coverage_rows: list[list[float | None]] = []
    for unit_a in units:
        log_row: list[float | None] = []
        ok_row: list[bool] = []
        coverage_row: list[float | None] = []
        for unit_b in units:
            if unit_a == unit_b:
                log_row.append(0.0)
                ok_row.append(True)
                coverage_row.append(1.0)
                continue
            key = (unit_a, unit_b) if (unit_a, unit_b) in lookup else (unit_b, unit_a)
            edge = lookup[key]
            coverage_row.append(float(min(edge.coverage_a, edge.coverage_b)))
            if not edge.accepted:
                log_row.append(None)
                ok_row.append(False)
            else:
                signed = edge.log_fisher if unit_a == edge.unit_a else -edge.log_fisher
                log_row.append(signed)
                ok_row.append(True)
        log_rows.append(log_row)
        accepted_rows.append(ok_row)
        coverage_rows.append(coverage_row)
    return {
        "units_order": list(units),
        "log_fisher": log_rows,
        "accepted": accepted_rows,
        "coverage_min": coverage_rows,
        "cell_edges": {
            unit_a: {
                unit_b: (
                    None
                    if unit_a == unit_b
                    else _canonical_edge_name(unit_a, unit_b, units)
                )
                for unit_b in units
            }
            for unit_a in units
        },
    }


def _canonical_edge_name(unit_a: str, unit_b: str, units: Sequence[str]) -> str:
    """单元格对应边的规范方向名（按请求单元顺序）。"""
    i, j = units.index(unit_a), units.index(unit_b)
    return f"{units[min(i, j)]}->{units[max(i, j)]}"


def multilateral(
    panel: Any,
    units: Sequence[str] | None = None,
    base_unit: str | None = None,
) -> dict[str, Any]:
    """对给定单元集输出完整 GEKS 或缺边加权图估计。

    ``units`` 缺省为清单全部单元，``base_unit`` 缺省为清单基准。返回值中的
    ``method``、``complete_graph`` 和 ``comparison_scope`` 是业务读取连续
    序列时必须携带的可比范围说明。
    """
    requested = list(panel.units if units is None else units)
    if not requested:
        raise ContractError("请求单元集为空")
    if len(set(requested)) != len(requested):
        raise ContractError("请求单元集含重复单元")
    unknown = [unit for unit in requested if unit not in panel.units]
    if unknown:
        raise ContractError(f"请求单元不在清单中: {unknown}")
    base = panel.base_unit if base_unit is None else base_unit
    if base not in requested:
        raise ContractError("base_unit 必须在请求单元集中")

    index_of = {unit: position for position, unit in enumerate(requested)}
    edges = bilateral_edges(panel, requested)
    accepted = _accepted_edges(edges, index_of)
    n = len(requested)
    all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    complete = bool(all_pairs) and all(pair in accepted for pair in all_pairs)
    components = _components(requested, accepted, index_of)
    rank = n - len(components)
    cycle_count = len(accepted) - rank
    warnings: list[str] = []

    parent, non_tree = _tree(requested, accepted, index_of)
    loops = _loop_inconsistencies(requested, accepted, index_of, parent, non_tree)
    max_loop = max((abs(record["inconsistency"]) for record in loops), default=0.0)

    values: dict[str, float | None] = {unit: None for unit in requested}
    logs_all: dict[str, float | None] = {unit: None for unit in requested}
    if n == 1:
        method = GRAPH_METHOD
        logs_all[requested[0]] = 0.0
        values[requested[0]] = 1.0
        comparison_scope = "single-unit-anchor"
        component_records = [
            {
                "units": requested,
                "anchor": requested[0],
                "contains_base": requested[0] == base,
                "index_relative_to_anchor": {requested[0]: 1.0},
                "comparable_with_base": requested[0] == base,
            }
        ]
        warnings.append("只有一个单元，没有双边比较。")
    elif complete:
        method = GEKS_METHOD
        computed = _geks_values(requested, accepted, index_of, base)
        for unit in requested:
            logs_all[unit] = computed[unit]
            values[unit] = math.exp(computed[unit])
        comparison_scope = f"all-units-relative-to-{base}"
        component_records = [
            {
                "units": requested,
                "anchor": base,
                "contains_base": True,
                "index_relative_to_anchor": dict(values),
                "comparable_with_base": True,
            }
        ]
    else:
        method = GRAPH_METHOD
        comparison_scope = (
            "within-component-only"
            if len(components) > 1
            else f"all-units-relative-to-{base}"
        )
        component_records = []
        for component in components:
            if len(component) == 1:
                lone = component[0]
                is_base = lone == base
                logs_all[lone] = 0.0
                values[lone] = 1.0 if is_base else None
                component_records.append(
                    {
                        "units": component,
                        "anchor": lone,
                        "contains_base": is_base,
                        "index_relative_to_anchor": {lone: 1.0 if is_base else None},
                        "comparable_with_base": is_base,
                    }
                )
                continue
            anchor = base if base in component else component[0]
            computed = _graph_solution(component, accepted, index_of, anchor)
            recalls = {}
            for unit in component:
                recalls[unit] = computed[unit] + (
                    math.log(values[anchor]) if values[anchor] is not None else 0.0
                )
                values[unit] = math.exp(recalls[unit])
            # 记录分量内相对锚点的对数水平，用于诊断残差与环路。
            for unit in component:
                logs_all[unit] = recalls[unit]
            component_records.append(
                {
                    "units": component,
                    "anchor": anchor,
                    "contains_base": base in component,
                    "index_relative_to_anchor": {
                        unit: math.exp(computed[unit]) for unit in component
                    },
                    "comparable_with_base": base in component,
                }
            )
        if len(components) > 1:
            warnings.append(
                "图不连通：各分量只在连通分量内给相对指数，不能跨分量比较，也未跨分量填值。"
            )
        warnings.append(
            "请求单元集不是完整可比图：使用缺边加权图最小二乘，不是完整 GEKS-Fisher。"
        )
        if any(value is None for value in values.values()):
            warnings.append("孤立单元没有可比边，其指数未估计。")

    residuals = _residual_records(accepted, index_of, requested, logs_all)
    objective = sum(record["weight"] * record["residual"] ** 2 for record in residuals)

    component_index = {
        unit: position for position, component in enumerate(components) for unit in component
    }
    unit_details: dict[str, Any] = {}
    for unit in requested:
        record = next(
            (item for item in component_records if unit in item["units"]), None
        )
        unit_details[unit] = {
            "index": values[unit],
            "component": component_index[unit],
            "component_anchor": record["anchor"] if record else None,
            "comparable_with_base": bool(
                record and record["comparable_with_base"] and values[unit] is not None
            ),
        }

    return {
        "method": method,
        "method_label": METHOD_LABELS[method],
        "complete_graph": complete,
        "requested_units": requested,
        "base_unit": base,
        "comparison_scope": comparison_scope,
        "index": {unit: values[unit] for unit in requested},
        "unit_details": unit_details,
        "components": component_records,
        "rank": rank,
        "edge_count": len(accepted),
        "candidate_edge_count": len(all_pairs),
        "cycle_count": cycle_count,
        "edges": [edge.as_dict() for edge in edges],
        "edge_matrix": _edge_matrices(requested, edges),
        "residuals": residuals,
        "objective": objective,
        "loop_inconsistency": {
            "max_abs": max_loop,
            "cycles": loops,
        },
        "warnings": warnings,
    }

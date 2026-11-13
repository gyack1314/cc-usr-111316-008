"""滚窗接续与冻结历史：只发布新月，修订另开完整版本。

口径（与 ``docs/data-contract.md`` 一致）：

* 固定窗口长度 ``w``，以 ``new_unit`` 结尾；
* 窗口内先以窗口首单元为 1，重算局部多边指数；若局部图缺边，局部指数
  本身使用明确标注的缺边图估计；
* 接续常数 ``c`` 为窗口内全部已发布重叠单元上
  ``mean(log(旧发布值) - log(新局部指数))`` 的等权平均；
* 冻结模式只发布 ``new_unit``，历史发布值一字不改；
* 修订模式额外输出完整版本，并列出受影响月份；不得把换基期当真实价格
  变化。

窗口缺失、共同边断裂或没有可接续重叠期时直接阻断，不用最近报价充数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .exact import AnalysisError, ContractError, mean
from .index import METHOD_LABELS, multilateral


@dataclass(frozen=True)
class SeriesPoint:
    unit: str
    value: float | None
    status: str
    method: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "value": self.value,
            "status": self.status,
            "method": self.method,
        }


def _ensure_window(panel: Any, window: list[str]) -> None:
    """窗口必须有有效报价，局部共同边图连通，且新月不孤立。"""
    for unit in window:
        if panel.accepted_expenditure(unit) <= 0:
            raise AnalysisError(f"窗口单元缺少有效报价，阻断接续: {unit}")
    from .index import _accepted_edges, _components, bilateral_edges

    index_of = {unit: position for position, unit in enumerate(window)}
    edges = bilateral_edges(panel, window)
    accepted = _accepted_edges(edges, index_of)
    if len(window) > 1:
        adjacent_to_new = [
            edge
            for edge in edges
            if edge.accepted and window[-1] in (edge.unit_a, edge.unit_b)
        ]
        if not adjacent_to_new:
            raise AnalysisError(
                f"窗口末端单元没有可比共同边，阻断接续: {window[-1]}"
            )
    components = _components(window, accepted, index_of)
    if len(components) != 1:
        raise AnalysisError(
            "窗口共同边图不连通，阻断接续: "
            + " | ".join(",".join(part) for part in components)
        )


def _local_multilateral(panel: Any, window: list[str]) -> dict[str, Any]:
    return multilateral(panel, units=window, base_unit=window[0])


def _local_values(result: dict[str, Any], window: list[str]) -> dict[str, float]:
    values = {unit: result["index"][unit] for unit in window}
    missing = [unit for unit, value in values.items() if value is None]
    if missing:
        raise AnalysisError(f"局部多边指数无法为窗口全部单元估值: {missing}")
    return values


def _link_constant(
    published: dict[str, float], local: dict[str, float], overlap: list[str]
) -> tuple[float, list[dict[str, Any]]]:
    if not overlap:
        raise AnalysisError("窗口内没有可接续的已发布重叠单元，阻断接续")
    records = []
    differences = []
    for unit in overlap:
        if unit not in local:
            continue
        difference = math.log(published[unit]) - math.log(local[unit])
        differences.append(difference)
        records.append(
            {
                "unit": unit,
                "published": published[unit],
                "local": local[unit],
                "log_difference": difference,
            }
        )
    if not differences:
        raise AnalysisError("已发布重叠单元不在局部指数内，阻断接续")
    return mean(differences), records


def rolling(panel: Any) -> dict[str, Any]:
    """执行滚窗接续；冻结模式只发布新月，修订模式另给完整版本。"""
    window = panel.window_units()
    if panel.new_unit != window[-1]:
        raise ContractError("窗口必须以 new_unit 结尾")
    _ensure_window(panel, window)

    local_result = _local_multilateral(panel, window)
    local = _local_values(local_result, window)
    overlap = [unit for unit in window if unit in panel.published]

    published_frozen = {unit: float(value) for unit, value in panel.published.items()}
    link, link_records = _link_constant(published_frozen, local, overlap)
    new_value = math.exp(link + math.log(local[panel.new_unit]))

    frozen_points: list[dict[str, Any]] = []
    for unit in panel.units:
        if unit in panel.published:
            frozen_points.append(
                SeriesPoint(
                    unit=unit,
                    value=float(panel.published[unit]),
                    status="published-frozen",
                    method=None,
                ).as_dict()
            )
        elif unit == panel.new_unit:
            frozen_points.append(
                SeriesPoint(
                    unit=unit,
                    value=new_value,
                    status="new-publication",
                    method=local_result["method"],
                ).as_dict()
            )
        else:
            frozen_points.append(
                SeriesPoint(
                    unit=unit,
                    value=None,
                    status="not-published",
                    method=None,
                ).as_dict()
            )

    revision: dict[str, Any] | None = None
    if panel.mode == "revise":
        revision = _complete_revision(panel, published_frozen)

    return {
        "window": {
            "units": window,
            "length": panel.window,
            "new_unit": panel.new_unit,
            "local_method": local_result["method"],
            "local_method_label": METHOD_LABELS[local_result["method"]],
            "local_complete_graph": local_result["complete_graph"],
            "local_index": local,
            "local_unit_details": local_result["unit_details"],
            "local_components": local_result["components"],
        },
        "overlap_units": overlap,
        "link_constant": {
            "value": link,
            "formula": "mean(log(published[o]) - log(local[o])) over overlap",
            "overlap_units": overlap,
            "averaging": "equal-weight",
            "terms": link_records,
        },
        "frozen": {
            "mode": panel.mode,
            "series": frozen_points,
            "changed_history": False,
            "new_unit_only": True,
        },
        "revision": revision,
    }


def _complete_revision(panel: Any, published: dict[str, float]) -> dict[str, Any]:
    """按逐窗推进重算完整版本，并标出受影响月份。

    首个窗口直接锚定窗口首单元为 1。此后每个窗口在已有覆盖的单元上取
    接续常数，并把窗口内所有单元（含重叠月）替换为新局部估计 —— 这是
    “完整版本”；它只作为另开的修订版本，旧发布值仍保留在冻结序列中。
    """
    units = panel.units
    width = panel.window
    values: dict[str, float] = {}
    window_records: list[dict[str, Any]] = []
    first_window = units[:width]
    _ensure_window(panel, first_window)
    first_local_result = _local_multilateral(panel, first_window)
    first_local = _local_values(first_local_result, first_window)
    for unit in first_window:
        values[unit] = first_local[unit]
    window_records.append(
        {
            "end_unit": first_window[-1],
            "window": first_window,
            "overlap_units": [],
            "link_constant": 0.0,
            "method": first_local_result["method"],
            "complete_graph": first_local_result["complete_graph"],
        }
    )

    for end in range(width, len(units)):
        window = units[end - width + 1 : end + 1]
        _ensure_window(panel, window)
        local_result = _local_multilateral(panel, window)
        local = _local_values(local_result, window)
        overlap = [unit for unit in window if unit in values]
        if not overlap:
            raise AnalysisError(
                f"窗口没有可接续重叠单元，阻断修订接续: {window}"
            )
        link, link_records = _link_constant(values, local, overlap)
        for unit in window:
            values[unit] = math.exp(link + math.log(local[unit]))
        window_records.append(
            {
                "end_unit": window[-1],
                "window": window,
                "overlap_units": overlap,
                "link_constant": link,
                "link_terms": link_records,
                "method": local_result["method"],
                "complete_graph": local_result["complete_graph"],
            }
        )

    series: list[dict[str, Any]] = []
    affected: list[dict[str, Any]] = []
    for unit in units:
        value = values.get(unit)
        status = "revised" if unit in published else "new-revision"
        series.append({"unit": unit, "value": value, "status": status})
        if unit in published:
            difference = value - published[unit]
            if not math.isclose(value, published[unit], rel_tol=1e-12, abs_tol=1e-12):
                affected.append(
                    {
                        "unit": unit,
                        "frozen": published[unit],
                        "revised": value,
                        "difference": difference,
                        "log_difference": math.log(value) - math.log(published[unit]),
                    }
                )
    return {
        "version": "revision-complete",
        "base_note": "完整版本以首个窗口首单元为滚动锚点；不等于把基准换到最新单元。",
        "series": series,
        "windows": window_records,
        "overlap_units": [
            unit for unit in units if unit in values and unit != panel.new_unit
        ],
        "affected_months": affected,
        "frozen_history_unchanged": True,
    }

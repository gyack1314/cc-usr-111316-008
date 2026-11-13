"""把面板与指数结果整理成审计读取器需要的稳定结构。"""
from __future__ import annotations

from typing import Any

from .exact import fraction_text
from .index import multilateral
from .rolling import rolling


def _cell(cell: Any) -> dict[str, Any]:
    return {
        "unit": cell.unit,
        "product": cell.product,
        "trade_ids": list(cell.trade_ids),
        "trade_count": len(cell.trade_ids),
        "quantity": fraction_text(cell.quantity),
        "expenditure": fraction_text(cell.expenditure),
        "unit_value": fraction_text(cell.unit_value),
    }


def transactions_view(panel: Any) -> dict[str, Any]:
    """交易聚合账与拒绝账。"""
    accepted_cells = [
        _cell(cell)
        for unit in panel.units
        for cell in panel.products(unit).values()
    ]
    rejected = [
        {
            "id": item.id,
            "unit": item.unit,
            "product": item.product,
            "price": item.price,
            "quantity": item.quantity,
            "reason": item.reason,
            "detail": item.detail,
        }
        for item in panel.rejected
    ]
    return {
        "units": panel.units,
        "accepted_cells": accepted_cells,
        "rejected": rejected,
        "rejected_count": len(rejected),
        "accepted_cell_count": len(accepted_cells),
        "aggregation": "sum(price*quantity)/sum(quantity) per unit×product",
    }


def frozen_view(panel: Any) -> dict[str, Any]:
    """冻结映射、基准与门槛版本，便于审计复现。"""
    return {
        "manifest_sha256": panel.manifest_sha256,
        "kind": panel.manifest.get("kind"),
        "units": panel.units,
        "base_unit": panel.base_unit,
        "coverage_threshold": fraction_text(panel.coverage_threshold),
        "edge_weight": panel.manifest.get("edge_weight"),
        "window": panel.window,
        "new_unit": panel.new_unit,
        "mode": panel.mode,
        "product_map": dict(panel.product_map),
        "mapping_records": [
            {"source": record.source, "target": record.target, "rationale": record.rationale}
            for record in panel.mapping_records
        ],
        "published": {unit: fraction_text(value) for unit, value in panel.published.items()},
    }


def analysis_view(panel: Any, *, include_rolling: bool = True) -> dict[str, Any]:
    """完整分析输出：交易聚合、共同篮子、覆盖率、边矩阵、指数与接续。"""
    index_result = multilateral(panel)
    result: dict[str, Any] = {
        "transactions": transactions_view(panel),
        "coverage_threshold": fraction_text(panel.coverage_threshold),
        "index": index_result,
    }
    if include_rolling:
        result["rolling"] = rolling(panel)
    return result

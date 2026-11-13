"""核对输入形状并输出清单摘要。"""
import json

from .io import asset, integer, unique
from .panel import load_panel


def summarize(v, root):
    """兼容冻结资料读取器的摘要：交易数、单元数与映射数。

    完整分析请使用 :func:`analysis_data.panel.load_panel` 及
    :func:`analysis_data.report.analysis_view`。
    """
    rows = [json.loads(x) for x in asset(root, v['transactions']).splitlines()]
    unique(rows, 'id')
    integer(v['rolling']['window'], 2)
    return {
        'transactions': len(rows),
        'units': len(v['units']),
        'product_mappings': len(v['product_map']),
    }


def summary(path):
    """读取清单并给出带冻结版本身份的分析摘要。"""
    panel = load_panel(path)
    return {
        "manifest_sha256": panel.manifest_sha256,
        "units": panel.units,
        "base_unit": panel.base_unit,
        "coverage_threshold": str(panel.coverage_threshold),
        "window": panel.window,
        "new_unit": panel.new_unit,
        "mode": panel.mode,
        "product_map": dict(panel.product_map),
        "accepted_products": {
            unit: sorted(panel.products(unit)) for unit in panel.units
        },
        "rejected_trades": len(panel.rejected),
    }

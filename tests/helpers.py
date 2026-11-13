"""从零构造合成面板的测试辅助。"""
from __future__ import annotations

from fractions import Fraction
from typing import Iterable

from analysis_data.panel import Panel, Transaction, aggregate_transactions


def make_transaction(trade_id, unit, product, price, quantity) -> Transaction:
    return Transaction(
        id=trade_id,
        unit=unit,
        product=product,
        price=Fraction(price),
        quantity=Fraction(quantity),
        price_text=str(price),
        quantity_text=str(quantity),
    )


def make_panel(
    transactions: Iterable[tuple],
    units: Iterable[str],
    *,
    base_unit: str | None = None,
    product_map: dict[str, str] | None = None,
    threshold: str = "1/2",
    window: int = 2,
    published: dict[str, str] | None = None,
    new_unit: str | None = None,
    mode: str = "freeze-history",
    manifest: dict | None = None,
) -> Panel:
    unit_list = list(units)
    trades = [make_transaction(*row) for row in transactions]
    mapping = product_map or {}
    block, rejected = aggregate_transactions(trades, unit_list, mapping)
    published_map = {
        unit: Fraction(value) for unit, value in (published or {}).items()
    }
    return Panel(
        units=unit_list,
        base_unit=base_unit or unit_list[0],
        coverage_threshold=Fraction(threshold),
        product_map=mapping,
        mapping_records=[],
        block=block,
        rejected=rejected,
        window=window,
        published=published_map,
        new_unit=new_unit or unit_list[-1],
        mode=mode,
        manifest=manifest or {},
    )

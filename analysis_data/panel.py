"""读取、校验并规范化价格指数清单与原始交易。

本模块只做身份与形状检查，以及把交易整理为 ``unit × product`` 的支出和
数量。价格指数本身在 :mod:`analysis_data.index` 中计算。

关键口径：

* 价格与数量必须为有限有理数；正价格、正数量才进入指数。
* 退货（负价或负量）、零价与零量不进入指数，只进入拒绝账。
* 同一 ``unit × product`` 的多笔交易先按 ``总支出 / 总数量`` 聚合，拆分
  成交不应改变结果。
* 产品替代只使用显式、冻结的人工映射；没有映射不做任何强配。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from .exact import ContractError, parse_fraction
from .io import asset, load

SUPPORTED_KINDS = ("price-index",)
EDGE_WEIGHT_MODES = ("geometric-mean-coverage",)
ROLLING_MODES = ("freeze-history", "revise")


@dataclass(frozen=True)
class Transaction:
    id: str
    unit: str
    product: str
    price: Fraction
    quantity: Fraction
    price_text: str
    quantity_text: str

    @property
    def expenditure(self) -> Fraction:
        return self.price * self.quantity


@dataclass(frozen=True)
class ProductMapping:
    """冻结的产品替代映射：同一原产品至多一个目标。"""

    source: str
    target: str
    rationale: str = ""


@dataclass(frozen=True)
class Rejection:
    id: str
    unit: str
    product: str
    price: str
    quantity: str
    reason: str
    detail: str


@dataclass(frozen=True)
class AggregatedCell:
    unit: str
    product: str
    quantity: Fraction
    expenditure: Fraction
    trade_ids: tuple[str, ...]

    @property
    def unit_value(self) -> Fraction:
        return self.expenditure / self.quantity


@dataclass
class Panel:
    units: list[str]
    base_unit: str
    coverage_threshold: Fraction
    product_map: dict[str, str]
    mapping_records: list[ProductMapping]
    block: dict[str, dict[str, AggregatedCell]]
    rejected: list[Rejection]
    window: int
    published: dict[str, Fraction]
    new_unit: str
    mode: str
    manifest: dict[str, Any]
    manifest_sha256: str = ""

    def products(self, unit: str) -> dict[str, AggregatedCell]:
        return self.block.get(unit, {})

    def accepted_expenditure(self, unit: str) -> Fraction:
        return sum((cell.expenditure for cell in self.block.get(unit, {}).values()), Fraction(0))

    def window_units(self) -> list[str]:
        """固定长度滚窗，以 ``new_unit`` 结尾。"""
        return self.units[-self.window:]


def _identity(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{field_name} 必须为非空字符串")
    return value


def _read_transactions(root: Path, ref: dict[str, Any]) -> list[Transaction]:
    try:
        raw = asset(root, ref)
    except KeyError as exc:
        raise ContractError(f"transactions 引用缺少字段: {exc}") from exc
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    expected_rows = ref.get("rows")
    lines = [line for line in raw.splitlines() if line.strip()]
    if expected_rows is not None:
        if type(expected_rows) is not int:
            raise ContractError("transactions.rows 必须为整数")
        if len(lines) != expected_rows:
            raise ContractError("交易行数与资产声明的 rows 不符")
    transactions: list[Transaction] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line.decode("utf8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(f"交易第 {number} 行不是合法 JSON") from exc
        if not isinstance(row, dict):
            raise ContractError(f"交易第 {number} 行必须为对象")
        trade_id = _identity(row.get("id"), "交易 id")
        if trade_id in seen:
            raise ContractError(f"交易 id 重复: {trade_id}")
        seen.add(trade_id)
        unit = _identity(row.get("unit"), "交易 unit")
        product = _identity(row.get("product"), "交易 product")
        price = parse_fraction(row.get("price"), f"{trade_id}.price")
        quantity = parse_fraction(row.get("quantity"), f"{trade_id}.quantity")
        transactions.append(
            Transaction(
                id=trade_id,
                unit=unit,
                product=product,
                price=price,
                quantity=quantity,
                price_text=str(row.get("price")),
                quantity_text=str(row.get("quantity")),
            )
        )
    return transactions


def _read_mapping(value: Any) -> tuple[dict[str, str], list[ProductMapping]]:
    """规范化产品映射，拒绝多目标、非终态映射与类型错误。

    契约允许 ``{source: target}`` 简表；也允许目标带说明的审计形式
    ``{source: {"target": ..., "rationale": ...}}``。若同一原产品在数组中
    出现两次且目标不同，视为多目标并拒绝。
    """
    if value is None:
        return {}, []
    mapping: dict[str, str] = {}
    rationale_by_source: dict[str, str] = {}

    def add(source: Any, target_value: Any) -> None:
        source_text = _identity(source, "product_map.source")
        if isinstance(target_value, dict):
            unknown = set(target_value) - {"target", "rationale"}
            if unknown:
                raise ContractError(f"product_map[{source_text}] 含未知字段: {sorted(unknown)}")
            target = _identity(target_value.get("target"), f"product_map[{source_text}].target")
            rationale = target_value.get("rationale", "")
            if not isinstance(rationale, str):
                raise ContractError(f"product_map[{source_text}].rationale 必须为字符串")
        else:
            target = _identity(target_value, f"product_map[{source_text}]")
            rationale = ""
        if source_text in mapping:
            if mapping[source_text] != target:
                raise ContractError(f"product_map 把同一产品映到多个目标: {source_text}")
            raise ContractError(f"product_map 重复来源: {source_text}")
        if source_text == target:
            raise ContractError(f"product_map 把产品映到自身: {source_text}")
        mapping[source_text] = target
        rationale_by_source[source_text] = rationale

    if isinstance(value, dict):
        for source, target_value in value.items():
            add(source, target_value)
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict) or "source" not in item or "target" not in item:
                raise ContractError("product_map 数组项必须含 source 与 target")
            unknown = set(item) - {"source", "target", "rationale"}
            if unknown:
                raise ContractError(f"product_map 数组项含未知字段: {sorted(unknown)}")
            target_value = {"target": item["target"]}
            if "rationale" in item:
                target_value["rationale"] = item["rationale"]
            add(item["source"], target_value)
    else:
        raise ContractError("product_map 必须为对象或数组")

    for source, target in mapping.items():
        if target in mapping:
            raise ContractError(
                f"product_map 不是冻结终态：{source}->{target}，而 {target} 仍有映射"
            )
    records = [
        ProductMapping(source=source, target=target, rationale=rationale_by_source[source])
        for source, target in mapping.items()
    ]
    return mapping, records


def _canonical_units(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractError("units 必须为非空数组")
    units: list[str] = []
    for item in value:
        unit = _identity(item, "units 项")
        if unit in units:
            raise ContractError(f"units 重复: {unit}")
        units.append(unit)
    return units


def _parse_threshold(value: Any) -> Fraction:
    threshold = parse_fraction(value, "coverage_threshold")
    if threshold <= 0 or threshold > 1:
        raise ContractError("coverage_threshold 必须在 (0, 1] 内")
    return threshold


def load_panel(path: str | Path) -> Panel:
    """读取清单及被引用的交易文件，规范化为面板。

    清单哈希与 :func:`analysis_data.io.load` 的返回值一致——都是原始清单
    文件的 SHA-256，便于与既有读取器核对同一份版本。
    """
    manifest_path = Path(path)
    try:
        manifest, manifest_sha256 = load(manifest_path)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    return build_panel(
        manifest, manifest_path.parent, manifest_sha256=manifest_sha256
    )


def build_panel(manifest: dict[str, Any], root: str | Path, *, manifest_sha256: str = "") -> Panel:
    """从已解析的清单构造面板；``root`` 为资产引用所在目录。"""
    if not isinstance(manifest, dict):
        raise ContractError("清单顶层必须为对象")
    if manifest.get("kind") not in SUPPORTED_KINDS:
        raise ContractError(f"不支持的清单类型: {manifest.get('kind')!r}")
    units = _canonical_units(manifest.get("units"))

    rolling = manifest.get("rolling")
    if not isinstance(rolling, dict):
        raise ContractError("rolling 必须为对象")
    window = rolling.get("window")
    if type(window) is not int or window < 2:
        raise ContractError("rolling.window 必须为不小于 2 的整数")
    if window > len(units):
        raise ContractError("rolling.window 不能超过单元数量")
    new_unit = _identity(rolling.get("new_unit"), "rolling.new_unit")
    if new_unit not in units:
        raise ContractError("rolling.new_unit 不在 units 中")
    if units.index(new_unit) != len(units) - 1:
        raise ContractError("rolling.new_unit 必须是 units 的最后一个单元")
    mode = rolling.get("mode")
    if mode not in ROLLING_MODES:
        raise ContractError(f"不支持的 rolling.mode: {mode!r}")

    published_value = rolling.get("published")
    if not isinstance(published_value, dict):
        raise ContractError("rolling.published 必须为对象")
    published: dict[str, Fraction] = {}
    for unit, value in published_value.items():
        if unit not in units:
            raise ContractError(f"rolling.published 含未知单元: {unit}")
        if unit == new_unit:
            raise ContractError("rolling.published 不能包含 new_unit")
        level = parse_fraction(value, f"rolling.published[{unit}]")
        if level <= 0:
            raise ContractError(f"rolling.published[{unit}] 必须为正")
        published[unit] = level

    base_unit = _identity(manifest.get("base_unit"), "base_unit")
    if base_unit not in units:
        raise ContractError("base_unit 不在 units 中")
    edge_weight = manifest.get("edge_weight")
    if edge_weight not in EDGE_WEIGHT_MODES:
        raise ContractError(f"不支持的 edge_weight: {edge_weight!r}")
    threshold = _parse_threshold(manifest.get("coverage_threshold"))
    mapping, records = _read_mapping(manifest.get("product_map"))
    transaction_ref = manifest.get("transactions")
    if not isinstance(transaction_ref, dict):
        raise ContractError("transactions 引用缺失")
    transactions = _read_transactions(Path(root), transaction_ref)

    known = set(units)
    unknown_units = {trade.unit for trade in transactions if trade.unit not in known}
    if unknown_units:
        # 清单之外的地区时期会让覆盖率和窗口含义不清，必须阻断。
        raise ContractError(f"交易引用清单外单元: {sorted(unknown_units)}")

    block, rejected = aggregate_transactions(transactions, units, mapping)
    return Panel(
        units=units,
        base_unit=base_unit,
        coverage_threshold=threshold,
        product_map=mapping,
        mapping_records=records,
        block=block,
        rejected=rejected,
        window=window,
        published=published,
        new_unit=new_unit,
        mode=mode,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
    )


def _reject_reason(trade: Transaction) -> str | None:
    if trade.price < 0:
        return "negative-price"
    if trade.quantity < 0:
        return "negative-quantity"
    if trade.price == 0:
        return "zero-price"
    if trade.quantity == 0:
        return "zero-quantity"
    return None


def aggregate_transactions(
    transactions: Iterable[Transaction],
    units: list[str],
    mapping: dict[str, str],
) -> tuple[dict[str, dict[str, AggregatedCell]], list[Rejection]]:
    """同一 ``unit×product`` 的多笔有效交易按总支出/总数量聚合。

    有效交易是严格正价格和严格正数量；退货（负价或负量）与零价、零量
    不进指数，只进拒绝账，并保留原始价格、数量和拒绝理由。
    """
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    rejected: list[Rejection] = []
    for trade in transactions:
        reason = _reject_reason(trade)
        if reason is not None:
            rejected.append(
                Rejection(
                    id=trade.id,
                    unit=trade.unit,
                    product=trade.product,
                    price=trade.price_text,
                    quantity=trade.quantity_text,
                    reason=reason,
                    detail="退货、零价或零量不进入价格指数",
                )
            )
            continue
        target = mapping.get(trade.product, trade.product)
        key = (trade.unit, target)
        state = cells.setdefault(
            key, {"quantity": Fraction(0), "expenditure": Fraction(0), "ids": []}
        )
        state["quantity"] += trade.quantity
        state["expenditure"] += trade.expenditure
        state["ids"].append(trade.id)
    block: dict[str, dict[str, AggregatedCell]] = {unit: {} for unit in units}
    for (unit, product), state in cells.items():
        block[unit][product] = AggregatedCell(
            unit=unit,
            product=product,
            quantity=state["quantity"],
            expenditure=state["expenditure"],
            trade_ids=tuple(sorted(state["ids"])),
        )
    # 按产品名排序，保证输出稳定。
    for unit in block:
        block[unit] = {product: block[unit][product] for product in sorted(block[unit])}
    return block, rejected

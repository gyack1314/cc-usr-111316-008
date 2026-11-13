"""精确数量工具：有理数解析、规范序列化与小型线性方程组求解。

价格与数量在内部始终用 :class:`fractions.Fraction` 表示，避免把字符串
有理数过早转成二进制浮点。只有指数、几何平均和权重开方才进入浮点。
"""
from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from typing import Any, Iterable, Sequence


class ContractError(ValueError):
    """输入不满足冻结资料契约。"""


class AnalysisError(ValueError):
    """输入形状正确，但当前分析不可识别或不可计算。"""


def parse_fraction(value: Any, field: str) -> Fraction:
    """把 JSON 中的有理数解析为 Fraction，保留字符串精度。"""
    if isinstance(value, bool):
        raise ContractError(f"{field} 不能是布尔值")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ContractError(f"{field} 为空")
        try:
            return Fraction(text)
        except (ValueError, ZeroDivisionError) as exc:
            raise ContractError(f"{field} 不是有理数: {value!r}") from exc
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{field} 不是有限数")
        return Fraction(str(value))
    raise ContractError(f"{field} 不是有理数: {value!r}")


def parse_positive(value: Any, field: str) -> Fraction:
    """解析严格正的有理数。"""
    result = parse_fraction(value, field)
    if result <= 0:
        raise ContractError(f"{field} 必须为正: {value!r}")
    return result


def fraction_text(value: Fraction) -> str:
    """以稳定、可回读的字符串保存有理数。"""
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf8")).hexdigest()


def solve(matrix: Sequence[Sequence[float]], rhs: Sequence[float], *, tolerance: float = 1e-12) -> list[float]:
    """带部分主元的高斯消元，用于小型正定图拉普拉斯方程。"""
    n = len(rhs)
    if len(matrix) != n or any(len(row) != n for row in matrix):
        raise AnalysisError("线性方程组维数不一致")
    augmented = [list(map(float, matrix[i])) + [float(rhs[i])] for i in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= tolerance:
            raise AnalysisError("图拉普拉斯方程奇异；无法解除位移自由度")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        for row in range(column + 1, n):
            factor = augmented[row][column] / pivot_value
            if factor == 0:
                continue
            augmented[row][column] = 0.0
            for col in range(column + 1, n + 1):
                augmented[row][col] -= factor * augmented[column][col]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = augmented[row][n]
        for col in range(row + 1, n):
            total -= augmented[row][col] * solution[col]
        solution[row] = total / augmented[row][row]
    return solution


def finite_float(value: float, field: str) -> float:
    if not math.isfinite(value):
        raise AnalysisError(f"{field} 不是有限数")
    return value


def mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        raise AnalysisError("平均值没有样本")
    return math.fsum(items) / len(items)

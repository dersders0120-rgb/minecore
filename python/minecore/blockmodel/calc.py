"""Block model calculator: new attributes from expressions over existing ones.

Expressions use Python syntax but are parsed with ``ast`` and evaluated by a
small whitelist interpreter over NumPy arrays; nothing is passed to ``eval``.

* operands: attribute names, numbers, ``x``/``y``/``z`` (block centroids);
* arithmetic ``+ - * / // % **``, comparisons (also chained), ``and``,
  ``or``, ``not`` (element-wise), conditional ``a if cond else b``;
* functions: ``abs sqrt exp log log10 min max clip where isnan round floor
  ceil``; ``min``/``max`` of several arguments are element-wise.

Missing values (NaN) propagate through arithmetic; comparisons with NaN are
false. Example: ``where(lito == 2, 2.65, 2.5)`` or
``cu + au * 0.62 if kaya == 1 else 0``.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

from minecore.blockmodel.model import BlockModel, BlockModelError

Floats = npt.NDArray[np.float64]

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _min(*args: Any) -> Any:
    out = args[0]
    for a in args[1:]:
        out = np.minimum(out, a)
    return out


def _max(*args: Any) -> Any:
    out = args[0]
    for a in args[1:]:
        out = np.maximum(out, a)
    return out


_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "min": _min,
    "max": _max,
    "clip": np.clip,
    "where": np.where,
    "isnan": np.isnan,
    "round": np.round,
    "floor": np.floor,
    "ceil": np.ceil,
}


class _Evaluator:
    def __init__(self, names: Mapping[str, Any]) -> None:
        self.names = names

    def run(self, node: ast.AST) -> Any:  # noqa: PLR0911, PLR0912 - one branch per node type
        if isinstance(node, ast.Expression):
            return self.run(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.names:
                raise BlockModelError(f"unknown attribute {node.id!r}")
            return self.names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](self.run(node.left), self.run(node.right))
        if isinstance(node, ast.UnaryOp):
            value = self.run(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.Not):
                return np.logical_not(value)
        if isinstance(node, ast.BoolOp):
            parts = [self.run(v) for v in node.values]
            combine = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            out = parts[0]
            for p in parts[1:]:
                out = combine(out, p)
            return out
        if isinstance(node, ast.Compare) and all(type(o) in _COMPARE for o in node.ops):
            left = self.run(node.left)
            result: Any = True
            for op, right_node in zip(node.ops, node.comparators, strict=True):
                right = self.run(right_node)
                result = np.logical_and(result, _COMPARE[type(op)](left, right))
                left = right
            return result
        if isinstance(node, ast.IfExp):
            return np.where(self.run(node.test), self.run(node.body), self.run(node.orelse))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS
            and not node.keywords
        ):
            return _FUNCS[node.func.id](*(self.run(a) for a in node.args))
        raise BlockModelError(f"not allowed in an expression: {ast.dump(node)[:60]}")


def evaluate(expression: str, names: Mapping[str, Any], size: int) -> Floats:
    """Evaluate ``expression``; the result is broadcast to ``size`` floats."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise BlockModelError(f"invalid expression: {exc.msg}") from None
    with np.errstate(all="ignore"):
        value = _Evaluator(names).run(tree)
        out = np.broadcast_to(np.asarray(value, np.float64), (size,)).copy()
    out[~np.isfinite(out)] = np.nan
    return out


def calculate(model: BlockModel, target: str, expression: str, unit: str | None = None) -> Floats:
    """Store ``expression`` as attribute ``target`` (undoable); returns the values."""
    if not target.isidentifier():
        raise BlockModelError("attribute name must be a letter/underscore identifier")
    if target in ("x", "y", "z"):
        raise BlockModelError("x, y and z are reserved for block centroids")
    try:
        used = {
            n.id
            for n in ast.walk(ast.parse(expression.strip(), mode="eval"))
            if isinstance(n, ast.Name)
        }
    except SyntaxError as exc:
        raise BlockModelError(f"invalid expression: {exc.msg}") from None
    table = model.table()
    names: dict[str, Any] = {
        n: np.asarray(table.column(n).to_numpy(zero_copy_only=False), np.float64)
        for n in used
        if n in model.attributes
    }
    if used & {"x", "y", "z"}:
        c = model.grid.centroids()
        names.update(x=c[:, 0], y=c[:, 1], z=c[:, 2])
    values = evaluate(expression, names, model.grid.blocks)
    model.set_attributes(
        {target: values},
        {target: {"unit": unit, "expression": expression}},
        f"Calculate {target} = {expression}",
    )
    return values

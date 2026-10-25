from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dialect:
    name: str
    encoding: str
    delimiter: str
    decimal: str
    lineterminator: str


def get_dialect(name: str) -> Dialect:
    if name == "std":
        return Dialect("std", "utf-8", ",", ".", "\n")
    if name == "tr":
        return Dialect("tr", "utf-8-sig", ";", ",", "\r\n")
    return Dialect("tr1254", "cp1254", ";", ",", "\r\n")


def fmt_float(value: float, digits: int, decimal: str) -> str:
    text = f"{value:.{digits}f}"
    if decimal != ".":
        text = text.replace(".", decimal)
    return text


def fmt_special(text: str, decimal: str) -> str:
    if decimal != ".":
        return text.replace(".", decimal)
    return text

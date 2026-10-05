from collections.abc import Callable

from salyq.statements.kaspi import (
    ParsedStatement,
    ParsedTransaction,
    StatementParseError,
    parse_kaspi_statement,
    parse_rows,
)

# Парсер под каждый банк (ТЗ 4.2). Halyk и Freedom — следующими.
PARSERS: dict[str, Callable[[bytes, str], ParsedStatement]] = {"kaspi": parse_kaspi_statement}

__all__ = [
    "PARSERS", "ParsedStatement", "ParsedTransaction", "StatementParseError", "parse_kaspi_statement", "parse_rows",
]

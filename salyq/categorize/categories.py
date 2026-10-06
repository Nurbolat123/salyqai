"""Категории операций (ТЗ 4.3) и их налоговый смысл."""

CATEGORIES: dict[str, str] = {
    "income_sales": "Доход от реализации",
    "personal_transfer": "Личный перевод",
    "refund": "Возврат",
    "own_transfer": "Перевод между своими счетами",
    "loan": "Заём / возврат займа",  # не доход; добавлено к списку ТЗ — подтвердить у бухгалтера
    "social_payments": "Соцплатежи",
    "taxes": "Налоги",
    "other_expense": "Прочий расход",
}

# Облагаемый доход по упрощённой декларации — только поступления этой категории
TAXABLE_INCOME = frozenset({"income_sales"})

INCOMING = frozenset({"income_sales", "personal_transfer", "refund", "own_transfer", "loan"})
OUTGOING = frozenset({"personal_transfer", "refund", "own_transfer", "loan", "social_payments", "taxes", "other_expense"})

REVIEW_THRESHOLD = 0.85  # ниже — очередь «Проверить» (ТЗ 4.3)


def allowed(category: str, direction: str) -> bool:
    return category in (INCOMING if direction == "in" else OUTGOING)

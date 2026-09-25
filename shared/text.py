"""Общие проверки текста: язык, «чужие» письменности, пометки в скобках."""

import re

CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)
# Модель иногда вставляет слово на другом языке ("каждый细节"): иероглифы, кана, хангыль, арабица…
FOREIGN_SCRIPT = re.compile(r"[֐-ۿऀ-෿฀-๿぀-ヿ㐀-鿿가-힯]")
# Пометки, которые не относятся к самому слову: транскрипция [ˈtaʊəl] и пояснения (амер.).
EXTRAS = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def clean(text):
    """Одна строка без лишних пробелов."""
    return " ".join(str(text or "").split())


def has_cyrillic(text):
    return bool(CYRILLIC.search(text or ""))


def has_foreign_script(text):
    return bool(FOREIGN_SCRIPT.search(text or ""))


def is_russian(text):
    """Русский ли текст по сути: пометки в скобках на это не влияют ("stove (амер.)" — не русский)."""
    return has_cyrillic(EXTRAS.sub(" ", text or ""))


def strip_extras(text):
    """Без транскрипции и пояснений: "towel [ˈtaʊəl] (для рук)" -> "towel"."""
    return clean(EXTRAS.sub(" ", text or ""))

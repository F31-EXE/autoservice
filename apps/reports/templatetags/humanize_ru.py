from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def money(value) -> str:
    """12345.6 → «12 346» (thin non-breaking spaces between thousands)."""
    if value is None:
        return "—"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}".replace(",", " ")
    return f"{Decimal(value):,.0f}".replace(",", " ")


@register.filter
def percent(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".", ",")


@register.filter
def pp(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f} п.п.".replace(".", ",", 1)

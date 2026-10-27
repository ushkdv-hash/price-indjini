# -*- coding: utf-8 -*-
"""Офсет: табличные цены, рабочие дни, реестр обновлений."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
REGISTRY_PATH = os.path.join(DATA_DIR, "offset_registry.json")

DEFAULT_PRICE_FACTOR = 1.6


def load_registry():
    path = REGISTRY_PATH
    if not os.path.exists(path):
        return {"reference_date": None, "catalog": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_registry(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_date(value):
    """YYYY-MM-DD или DD.MM.YYYY."""
    if not value:
        raise ValueError("Пустая дата.")
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return datetime.strptime(text, "%Y-%m-%d").date()
    if re.match(r"^\d{1,2}\.\d{1,2}(?:\.\d{4})?$", text):
        parts = text.split(".")
        if len(parts) == 2:
            parts.append(str(date.today().year))
        return datetime.strptime(".".join(parts), "%d.%m.%Y").date()
    raise ValueError(f"Неверный формат даты: {value}")


def parse_delivery_end(label, year=None):
    """Из «Доставка на 23.07 - 27.07» или «Готовность на 22.07» — дата готовности."""
    text = str(label).strip()
    m = re.search(r"(\d{1,2})\.(\d{1,2})\s*[-–—]\s*(\d{1,2})\.(\d{1,2})", text)
    if m:
        y = year or date.today().year
        return date(y, int(m.group(4)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\.(\d{1,2})", text)
    if m:
        y = year or date.today().year
        return date(y, int(m.group(2)), int(m.group(1)))
    raise ValueError(f"Не удалось разобрать срок доставки: {label}")


def working_days_between(start, end):
    """Рабочие дни (пн–пт) от start до end включительно."""
    if end < start:
        return 0
    days = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            days += 1
        d += timedelta(days=1)
    return days


def delivery_tier_ui(tier, price_as_of, display_mode="working_days"):
    """Подпись срока для UI и результата."""
    ref = parse_date(price_as_of)
    end = parse_date(tier.get("date_end") or tier.get("date_end_iso"))
    wd = working_days_between(ref, end)
    label = tier.get("label") or ""
    if display_mode == "readiness" and label.lower().startswith("готовность"):
        display = f"{label} ({wd} раб. дн.)"
    else:
        display = f"Срок готовности {wd} раб. дн."
    return {
        "id": tier["id"],
        "label": label,
        "date_end": end.isoformat(),
        "working_days": wd,
        "display": display,
    }


def enrich_delivery_tiers(prod):
    price_as_of = prod.get("price_as_of")
    if not price_as_of:
        return []
    display_mode = prod.get("delivery_tier_display", "working_days")
    return [
        delivery_tier_ui(t, price_as_of, display_mode)
        for t in prod.get("delivery_tiers") or []
    ]


def lookup_offset_cost(prod, size_id, material_id, print_mode, qty, delivery_tier_id, lamination_id=None):
    prices = prod.get("prices") or {}
    size_prices = prices.get(size_id)
    if size_prices is None and material_id in prices:
        size_prices = prices
    if not size_prices:
        raise ValueError("Размер не найден в прайсе.")
    mat = size_prices.get(material_id)
    if not mat:
        raise ValueError("Материал не найден в прайсе.")
    if prod.get("lamination_modes"):
        lam_key = lamination_id if lamination_id is not None else "none"
        lam_block = mat.get(lam_key)
        if not lam_block:
            raise ValueError("Ламинация не найдена в прайсе.")
        by_print = lam_block.get(print_mode)
    else:
        by_print = mat.get(print_mode)
    if not by_print:
        raise ValueError("Цветность не найдена в прайсе.")
    qty_key = str(int(qty))
    row = by_print.get(qty_key)
    if not row:
        raise ValueError(f"Тираж {qty} не найден в прайсе.")
    cost = row.get(delivery_tier_id)
    if cost is None:
        raise ValueError("Срок изготовления не найден в прайсе.")
    return float(cost)


def offset_registry_status(products):
    """Статус позиций офсета для админки."""
    reg = load_registry()
    ref = reg.get("reference_date")
    ref_date = parse_date(ref) if ref else None
    catalog = reg.get("catalog") or []
    rows = []
    for item in catalog:
        pid = item.get("id")
        prod = products.get(pid) if pid else None
        updated = (prod or {}).get("price_table_updated")
        updated_date = parse_date(updated) if updated else None
        disabled = item.get("status") == "disabled"
        if not prod:
            status = "missing"
            note = "Калькулятор не создан"
        elif disabled:
            status = "disabled"
            note = "Калькулятор отключён (можно включить)"
        elif not updated:
            status = "no_date"
            note = "Нет даты обновления прайса"
        elif ref_date and updated_date and updated_date < ref_date:
            status = "stale"
            note = f"Прайс старее эталона ({ref})"
        else:
            status = "ok"
            note = "Актуально"
        rows.append({
            "id": pid,
            "title": item.get("title") or (prod or {}).get("title", pid),
            "tab": item.get("tab", ""),
            "planned": item.get("status", "active") != "planned",
            "disabled": disabled,
            "status": status,
            "note": note,
            "price_table_updated": updated,
            "reference_date": ref,
        })
    # продукты в JSON, но не в реестре
    known = {item.get("id") for item in catalog}
    for pid, prod in products.items():
        if prod.get("offset_group") == "ofset" and pid not in known:
            rows.append({
                "id": pid,
                "title": prod.get("title", pid),
                "tab": prod.get("offset_tab", ""),
                "planned": True,
                "disabled": False,
                "status": "unlisted",
                "note": "Нет в реестре обновлений",
                "price_table_updated": prod.get("price_table_updated"),
                "reference_date": ref,
            })
    return rows


def registry_entry(product_id):
    """Запись реестра для продукта (или None)."""
    for item in load_registry().get("catalog") or []:
        if item.get("id") == product_id:
            return item
    return None


def disabled_product_ids():
    """Id продуктов, отключённых в реестре офсета."""
    return {
        item.get("id")
        for item in load_registry().get("catalog") or []
        if item.get("status") == "disabled"
    }


def set_registry_status(product_id, status, title=None, tab=None):
    """Включить/отключить позицию реестра (status: active | disabled).

    Если записи ещё нет — она создаётся (чтобы любую офсет-позицию
    или бумажные пакеты можно было управлять из админки).
    """
    reg = load_registry()
    catalog = reg.setdefault("catalog", [])
    for item in catalog:
        if item.get("id") == product_id:
            item["status"] = status
            if title:
                item.setdefault("title", title)
            if tab:
                item.setdefault("tab", tab)
            break
    else:
        entry = {"id": product_id, "status": status}
        if title:
            entry["title"] = title
        if tab:
            entry["tab"] = tab
        catalog.append(entry)
    save_registry(reg)

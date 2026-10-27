# -*- coding: utf-8 -*-
"""Широкоформатная печать: площадь, периметр, цены за м²."""
from __future__ import annotations

import math

DEFAULT_PRICE_FACTOR = 1.6


def capitalize_material_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return s
    return s[0].upper() + s[1:]


AREA_TIER_THRESHOLD_SQM = 10.0
LAMINATION_TIER_THRESHOLD_SQM = 5.0
LINEAR_CUT_TIER_THRESHOLD_M = 50.0
EYELET_TIER_THRESHOLD_PCS = 100
EYELET_PERIMETER_SPACING_MM = 300
EYELET_CORNERS_COUNT = 4


def item_area_sqm(width_mm: float, height_mm: float) -> float:
    return width_mm * height_mm / 1_000_000.0


def item_perimeter_m(width_mm: float, height_mm: float) -> float:
    return 2.0 * (width_mm + height_mm) / 1000.0


def eyelets_per_item(
    mode: str,
    width_mm: float,
    height_mm: float,
    spacing_mm: int = EYELET_PERIMETER_SPACING_MM,
) -> int:
    """Количество люверсов на одно изделие."""
    if mode == "corners":
        return EYELET_CORNERS_COUNT
    if mode == "perimeter":
        perimeter_m = item_perimeter_m(width_mm, height_mm)
        spacing_m = spacing_mm / 1000.0
        if spacing_m <= 0:
            return 0
        return max(1, math.ceil(perimeter_m / spacing_m))
    return 0


def eyelets_total_count(
    mode: str,
    width_mm: float,
    height_mm: float,
    qty: int,
    spacing_mm: int = EYELET_PERIMETER_SPACING_MM,
) -> int:
    return eyelets_per_item(mode, width_mm, height_mm, spacing_mm) * max(int(qty), 0)


def base_resolution(dpi: int) -> int:
    if dpi in (540, 630):
        return 540
    return 720


def resolution_surcharge(prod: dict, dpi: int) -> float:
    return float((prod.get("resolution_surcharges") or {}).get(str(dpi), 0) or 0)


def pick_two_tier_price(under: float, over: float | None, value: float, threshold: float) -> float:
    if over is None:
        return under
    return over if value >= threshold else under


def material_unit_price(material: dict, total_measure: float) -> float:
    threshold = float(material.get("tier_threshold") or AREA_TIER_THRESHOLD_SQM)
    under = float(material["price_under"])
    over = material.get("price_from")
    if over is not None:
        over = float(over)
    return pick_two_tier_price(under, over, total_measure, threshold)


def option_linear_price(opt: dict, total_linear_m: float) -> float:
    under = float(opt["price_under"])
    over = opt.get("price_from")
    if over is None:
        return under
    threshold = float(opt.get("tier_threshold") or LINEAR_CUT_TIER_THRESHOLD_M)
    return pick_two_tier_price(under, float(over), total_linear_m, threshold)


def option_piece_price(opt: dict, count: int) -> float:
    under = float(opt["price_under"])
    over = opt.get("price_from")
    if over is None:
        return under
    threshold = int(opt.get("tier_threshold") or EYELET_TIER_THRESHOLD_PCS)
    return pick_two_tier_price(under, float(over), count, threshold)


def lamination_unit_price(opt: dict, total_area_sqm: float) -> float:
    under = float(opt["price_under"])
    over = float(opt["price_from"])
    threshold = float(opt.get("tier_threshold") or LAMINATION_TIER_THRESHOLD_SQM)
    return pick_two_tier_price(under, over, total_area_sqm, threshold)


def min_order_for(material: dict, has_lamination: bool) -> float:
    if has_lamination:
        return float(material.get("min_order_lamination") or 800)
    group = material.get("min_order_group") or "banner_film"
    rules = {
        "banner_film": 400,
        "premium": 600,
    }
    return float(material.get("min_order") or rules.get(group, 400))


def group_wide_format_materials_for_ui(materials):
    """Один пункт списка на название; варианты по base_resolution (540 / 720)."""
    groups = {}
    order = []
    for m in materials or []:
        name = capitalize_material_name(m.get("name", ""))
        key = name.casefold()
        if key not in groups:
            groups[key] = {
                "name": name,
                "category": m.get("category"),
                "lamination_key": m.get("lamination_key"),
                "variant_ids": {},
            }
            order.append(key)
        g = groups[key]
        base = int(m.get("base_resolution") or 720)
        g["variant_ids"][base] = m["id"]
        if m.get("category"):
            g["category"] = m.get("category")
        if m.get("lamination_key"):
            g["lamination_key"] = m.get("lamination_key")
    out = []
    for key in order:
        g = groups[key]
        bases = list(g["variant_ids"].keys())
        primary_base = 720 if 720 in g["variant_ids"] else bases[0]
        out.append({
            "id": g["variant_ids"][primary_base],
            "name": g["name"],
            "category": g.get("category"),
            "lamination_key": g.get("lamination_key"),
            "base_resolution": primary_base,
            "variant_ids": dict(g["variant_ids"]),
        })
    return out


def find_material(prod: dict, material_id: str) -> dict | None:
    return next((m for m in prod.get("materials") or [] if m["id"] == material_id), None)


def find_option(prod: dict, opt_id: str) -> dict | None:
    return next((o for o in prod.get("wide_options") or [] if o["id"] == opt_id), None)

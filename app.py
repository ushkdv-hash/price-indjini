# -*- coding: utf-8 -*-
"""
Калькулятор продукции типографии (веб-приложение на Flask).

Локальный запуск (как десктоп):  python app.py  -> откроется http://127.0.0.1:5000
Готово к публикации в интернете (любой WSGI-хостинг).

Возможности:
  - Регистрация / вход сотрудников, роли (employee / admin)
  - Калькулятор Визиток: себестоимость (материалы + работы) x наценка по тиражу + доп. опции - скидка
  - Админка: правка курсов валют, стоимости часа, материалов, работ, наценок и опций
  - История расчётов, копирование готового текста

Данные хранятся в редактируемых JSON-файлах в папке data/.
"""
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import shutil
import threading
import webbrowser
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, flash, jsonify, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

from offset_lib import (
    DEFAULT_PRICE_FACTOR,
    disabled_product_ids,
    enrich_delivery_tiers,
    load_registry,
    lookup_offset_cost,
    offset_registry_status,
    registry_entry,
    set_registry_status,
)
from wide_format_lib import (
    EYELET_PERIMETER_SPACING_MM,
    capitalize_material_name,
    eyelets_per_item,
    eyelets_total_count,
    find_material as wide_find_material,
    find_option as wide_find_option,
    group_wide_format_materials_for_ui,
    item_area_sqm,
    item_perimeter_m,
    lamination_unit_price,
    material_unit_price,
    min_order_for,
    option_linear_price,
    option_piece_price,
    resolution_surcharge,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Каталог прайса из репозитория (seed). На Amvera runtime — /data (постоянное хранилище).
BUNDLED_DATA_DIR = os.path.join(APP_DIR, "data")
# users / history / activity — не перезаписываем при деплое из bundled.
PRESERVE_RUNTIME_FILES = frozenset({
    "users.json",
    "history.json",
    "user_activity.json",
    "news.json",
    "news_reads.json",
})


PERSISTENT_DATA_DIR = "/data"


def _dir_is_writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with io.open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except OSError:
        return False


def _resolve_data_dir():
    """Куда писать runtime-данные.

    Amvera: папка data в git ≠ постоянное /data. Без /data пользователи и статистика
    пропадают при каждой сборке.
    """
    env = (os.environ.get("DATA_DIR") or "").strip()
    if env:
        return env
    if os.path.isdir(PERSISTENT_DATA_DIR) and _dir_is_writable(PERSISTENT_DATA_DIR):
        return PERSISTENT_DATA_DIR
    if "AMVERA" in os.environ:
        return PERSISTENT_DATA_DIR
    return BUNDLED_DATA_DIR


DATA_DIR = _resolve_data_dir()


def _preserve_file_has_data(name, path):
    if not os.path.isfile(path):
        return False
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    if name == "user_activity.json":
        users = data.get("users") if isinstance(data, dict) else None
        login_log = data.get("login_log") if isinstance(data, dict) else None
        return bool(users) or bool(login_log)
    if name == "users.json":
        return bool(data)
    if name == "history.json":
        return bool(data)
    if name in ("news.json", "news_reads.json"):
        return bool(data)
    return os.path.getsize(path) > 2


def _migrate_preserve_file(name):
    """Переносит users/history/activity в постоянное хранилище, не затирая живые данные."""
    src = os.path.join(BUNDLED_DATA_DIR, name)
    dst = os.path.join(DATA_DIR, name)
    if not os.path.isfile(src):
        return
    if not os.path.isfile(dst):
        shutil.copy2(src, dst)
        return
    if _preserve_file_has_data(name, src) and not _preserve_file_has_data(name, dst):
        shutil.copy2(src, dst)


def ensure_runtime_data():
    """Синхронизирует справочники в DATA_DIR; users/history/activity не затирает."""
    global DATA_DIR
    DATA_DIR = _resolve_data_dir()
    os.makedirs(DATA_DIR, exist_ok=True)
    using_persistent = os.path.abspath(DATA_DIR) != os.path.abspath(BUNDLED_DATA_DIR)
    print(
        f"[auto-price] DATA_DIR={DATA_DIR} bundled={BUNDLED_DATA_DIR} persistent={using_persistent}",
        flush=True,
    )
    if not using_persistent:
        return
    if not os.path.isdir(BUNDLED_DATA_DIR):
        return
    for name in PRESERVE_RUNTIME_FILES:
        _migrate_preserve_file(name)
    for name in os.listdir(BUNDLED_DATA_DIR):
        src = os.path.join(BUNDLED_DATA_DIR, name)
        if not os.path.isfile(src) or name.startswith("."):
            continue
        if name in PRESERVE_RUNTIME_FILES:
            continue
        shutil.copy2(src, os.path.join(DATA_DIR, name))


ensure_runtime_data()

USER_STATUS_APPROVED = "approved"
USER_STATUS_PENDING = "pending"
USER_STATUS_REJECTED = "rejected"

CALENDAR_HUB = (
    ("kvartalnye_kalendari", "Квартальные"),
    ("perekidnye_kalendari", "Перекидные"),
    ("domiki_kalendari", "Домики"),
    ("karmannye_kalendari", "Карманные"),
)
CALENDAR_HUB_IDS = {pid for pid, _ in CALENDAR_HUB}

OFFSET_HUB = (
    ("vizitki_ofset", "Визитки"),
    ("listovki_ofset", "Листовки и плакаты"),
    ("listovki_omega", "Листовки"),
    ("buklety_liflety_ofset", "Буклеты и лифлеты"),
    ("buklety_omega", "Буклеты"),
    ("papki_0204_ofset", "Папки"),
)
OFFSET_HUB_IDS = {pid for pid, _ in OFFSET_HUB}

PAPKI_HUB = (
    ("papki_0204_ofset", "№0204 (510×385)"),
    ("papki_145_ofset", "№145 (385×510)"),
    ("papki_835_ofset", "№835 (540×385)"),
)
PAPKI_HUB_IDS = {pid for pid, _ in PAPKI_HUB}

PVD_PRODUCT_ID = "pakety_pvd"
WIDE_FORMAT_PRODUCT_ID = "shirokoformatnaya_pechat"
# Бумажные пакеты: активный калькулятор на прайсе Омега-Принт.
# Прежний (bumazhnye_pakety, прайс Сборка) отключён — включается в админке.
PAPER_BAGS_PRODUCT_ID = "bumazhnye_pakety_omega"
PAPER_BAGS_PRODUCT_IDS = frozenset({"bumazhnye_pakety", "bumazhnye_pakety_omega"})
BOARD_COVER_PRODUCT_ID = "oblozhki_perepletnyi_karton"

PRICE_SOURCE_NOTES = {
    # Подписи по умолчанию для продуктов без своей пометки.
    # Новые калькуляторы (listovki_omega, buklety_omega, bumazhnye_pakety_omega)
    # имеют собственную пометку «Омега-Принт» в products.json.
    "offset_table": "Цены даны по прайсу типографии Сборка",
    "paper_bags": "Цены даны по прайсу типографии Сборка",
    "pvd_bags": "Цены даны по прайсу типографии Мастер Цвета",
    "wide_format": "Цены даны по прайсу типографии Печатный цех",
}


def price_source_note_for(prod):
    if not prod:
        return None
    # у продуктов может быть своя подпись (например, прежние офсет-позиции — прайс «Сборка»)
    return prod.get("price_source_note") or PRICE_SOURCE_NOTES.get(prod.get("calc_type"))


def get_calendar_hub():
    """Вкладки раздела «Календари» — только существующие продукты."""
    products = get_products()
    return [(pid, label) for pid, label in CALENDAR_HUB if pid in products]


def get_offset_hub():
    """Вкладки раздела «Офсет» — существующие и включённые продукты."""
    disabled = disabled_product_ids()
    products = get_products()
    return [
        (pid, label)
        for pid, label in OFFSET_HUB
        if pid in products and pid not in disabled
    ]


def get_papki_hub():
    """Вкладки раздела «Папки» — три вида папок."""
    disabled = disabled_product_ids()
    products = get_products()
    return [
        (pid, label)
        for pid, label in PAPKI_HUB
        if pid in products and pid not in disabled
    ]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "auto-price-calc-secret-change-me-dev-only")
app.config["TEMPLATES_AUTO_RELOAD"] = os.environ.get("PRODUCTION") != "1"
if os.environ.get("PRODUCTION") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.template_filter("items_on_sheet")
def items_on_sheet(n):
    """Склонение: «24 изделия на листе», «20 изделий на листе»."""
    if n is None:
        return ""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        word = "изделие"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "изделия"
    else:
        word = "изделий"
    return f"{n} {word} на листе"


@app.template_filter("news_datetime")
def news_datetime(iso):
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    return f"{dt.day} {months[dt.month - 1]} {dt.year}, {dt:%H:%M}"

# ----------------------------------------------------------------------------
# Работа с JSON-хранилищем
# ----------------------------------------------------------------------------

def _path(name):
    return os.path.join(DATA_DIR, name)


def load_json(name, default):
    p = _path(name)
    if not os.path.exists(p):
        return default
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with io.open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_settings():
    return load_json("settings.json", {
        "rate_usd": 150, "rate_eur": 160, "hour_cost": 440,
        "print_4_0": 12.32, "print_1_0": 4.0,
        "xerox_print_4_0": 42.9, "xerox_print_1_0": 13.2,
    })


def get_works():
    return load_json("works.json", {})


def get_sheet_layout_info():
    """Раскладка изделий на листе и доп. работы (вкладка «Изделий на листе»)."""
    return load_json("sheet_layout.json", {
        "title": "Дополнительная информация",
        "subtitle": "",
        "presets": [],
        "extra_works": [],
    })


def get_design_info():
    """Прайс на дизайн — услуги, сроки, тариф «Стандарт»."""
    return load_json("design.json", {
        "title": "Дизайн",
        "subtitle": "",
        "source": "",
        "tariff": "",
        "rules": [],
        "categories": [],
    })


def get_materials():
    return load_json("materials.json", [])


def materials_by_id():
    return {m["id"]: m for m in get_materials() if m.get("id")}


def materials_by_name():
    return {m["name"]: m for m in get_materials()}


def resolve_product_materials(prod):
    """Материалы продукта: каталог + привязки material_refs (или legacy materials)."""
    by_id = materials_by_id()
    by_name = materials_by_name()
    refs = prod.get("material_refs")
    if refs:
        result = []
        for ref in refs:
            src = by_id.get(ref["id"])
            if not src:
                continue
            item = {
                "id": ref["id"],
                "name": ref.get("label") or src["name"],
                "price_a3": src["price_a3"],
                "markup_profile": ref.get("markup_profile", "default"),
            }
            if src.get("custom_price_a3"):
                item["custom_price_a3"] = True
            if ref.get("excluded_options"):
                item["excluded_options"] = list(ref["excluded_options"])
            if ref.get("print_modes"):
                item["print_modes"] = list(ref["print_modes"])
            if ref.get("calc_mode"):
                item["calc_mode"] = ref["calc_mode"]
            if ref.get("through_cut"):
                item["through_cut"] = dict(ref["through_cut"])
            if ref.get("magnetic_vinyl"):
                item["magnetic_vinyl"] = dict(ref["magnetic_vinyl"])
            if ref.get("qty_min"):
                item["qty_min"] = int(ref["qty_min"])
            if ref.get("brochure_ref_seb") is not None:
                item["brochure_ref_seb"] = float(ref["brochure_ref_seb"])
            if ref.get("brochure_default_print"):
                item["brochure_default_print"] = ref["brochure_default_print"]
            if ref.get("brochure_seb_by_print"):
                item["brochure_seb_by_print"] = dict(ref["brochure_seb_by_print"])
            result.append(item)
        return result

    # legacy: встроенный список materials с group
    legacy_profile = {"Картон": "standard", "Дизайнерская": "designer"}
    result = []
    for m in prod.get("materials") or []:
        profile = legacy_profile.get(m.get("group"), "default")
        item = {
            "id": m.get("id") or by_name.get(m["name"], {}).get("id", ""),
            "name": m["name"],
            "price_a3": m["price_a3"],
            "markup_profile": m.get("markup_profile", profile),
        }
        if m.get("custom_price_a3"):
            item["custom_price_a3"] = True
        result.append(item)
    return result


def _lamination_family(opt):
    """0 = глянец, 1 = мат, 2 = софт-тач."""
    name = opt.get("name", "").lower()
    film = opt.get("film", "").lower()
    if "софт" in name or "soft_touch" in film:
        return 2
    if "матов" in name or film.startswith("mat_"):
        return 1
    if "глянц" in name or "gloss" in film:
        return 0
    return 3


def _lamination_microns(opt):
    name = opt.get("name", "")
    m = re.search(r"(\d+)\s*мк", name, re.I)
    if m:
        return int(m.group(1))
    film = opt.get("film", "")
    m = re.search(r"_(\d+)$", film)
    return int(m.group(1)) if m else 999


def _lamination_sort_key(opt):
    return (_lamination_family(opt), _lamination_microns(opt), opt.get("name", ""))


def sort_product_options(options):
    """Ламинации: глянец → мат → софт-тач, внутри группы по возрастанию мкм. Остальные — как были."""
    options = list(options or [])
    kinds = get_option_kinds()
    is_lam = lambda o: option_kind_formula(o.get("kind")) == "lamination"
    lam = sorted((o for o in options if is_lam(o)), key=_lamination_sort_key)
    rest = [o for o in options if not is_lam(o)]
    return lam + rest


DISCOUNT_CUSTOM_ONLY_CALC_TYPES = frozenset({
    "offset_table", "pvd_bags", "wide_format", "envelopes",
})


def discount_ui_config(prod):
    """Настройки блока скидки для шаблона и проверки на сервере."""
    calc_type = prod.get("calc_type") or ""
    custom_only = calc_type in DISCOUNT_CUSTOM_ONLY_CALC_TYPES
    presets = list(prod.get("discounts") or [])
    if custom_only:
        max_pct = 10.0
        presets_ui = []
    else:
        percents = [float(d.get("percent") or 0) for d in presets]
        max_pct = max(percents) if percents else 20.0
        presets_ui = presets
    return {
        "max_percent": max_pct,
        "custom_only": custom_only,
        "presets": presets_ui,
    }


def parse_product_discount(prod, raw):
    """Проверяет скидку: не выше максимума для продукта."""
    max_pct = discount_ui_config(prod)["max_percent"]
    try:
        discount_pct = float(raw or 0)
    except (TypeError, ValueError):
        raise ValueError("Некорректное значение скидки.")
    if discount_pct < 0:
        raise ValueError("Скидка не может быть отрицательной.")
    if discount_pct > max_pct:
        raise ValueError(f"Максимальная скидка для этого продукта — {max_pct:g}%.")
    return discount_pct


def enrich_ui_product(prod):
    ui = product_for_ui(prod)
    ui["discount_ui"] = discount_ui_config(prod)
    return ui


def product_for_ui(prod):
    """Копия продукта с разрешёнными материалами для шаблона и JS."""
    if prod.get("calc_type") == "offset_table":
        materials_out = []
        for m in prod.get("materials") or []:
            mm = dict(m)
            tiers = m.get("delivery_tiers") or prod.get("delivery_tiers") or []
            mm["delivery_tiers"] = enrich_delivery_tiers({**prod, "delivery_tiers": tiers})
            materials_out.append(mm)
        size_material_config_ui = {}
        for sid, mats in (prod.get("size_material_config") or {}).items():
            size_material_config_ui[sid] = {}
            for mid, cfg in mats.items():
                tiers = cfg.get("delivery_tiers") or []
                size_material_config_ui[sid][mid] = {
                    "qty_options": cfg.get("qty_options") or [],
                    "delivery_tiers": enrich_delivery_tiers({**prod, "delivery_tiers": tiers}),
                }
        p = {
            k: v for k, v in prod.items()
            if k not in (
                "material_refs", "materials", "prices", "variants", "size_material_config",
            )
        }
        p["calc_type"] = "offset_table"
        p["materials"] = materials_out
        p["size_material_config"] = size_material_config_ui
        p["print_modes"] = prod.get("print_modes") or []
        p["print_mode_labels"] = prod.get("print_mode_labels") or {}
        p["qty_options"] = prod.get("qty_options") or []
        p["delivery_tiers"] = (
            materials_out[0]["delivery_tiers"]
            if materials_out
            else enrich_delivery_tiers(prod)
        )
        p["price_factor"] = prod.get("price_factor", DEFAULT_PRICE_FACTOR)
        p["illustration"] = prod.get("illustration")
        p["options"] = sort_product_options(prod.get("options") or [])
        p["sizes"] = [
            {"name": s["name"], "id": s.get("id", s["name"])}
            for s in prod.get("sizes") or [{"name": "стандарт", "id": "default"}]
        ]
        p["discounts"] = prod.get("discounts") or []
        p["size_label"] = prod.get("size_label", "Размер")
        p["delivery_tier_display"] = prod.get("delivery_tier_display", "working_days")
        p["lamination_modes"] = prod.get("lamination_modes") or []
        p["hide_size_select"] = bool(prod.get("hide_size_select"))
        return p

    if prod.get("calc_type") == "pvd_bags":
        p = {k: v for k, v in prod.items() if k not in ("material_refs", "prices")}
        p["calc_type"] = "pvd_bags"
        p["materials"] = list(prod.get("materials") or [])
        p["sizes"] = [
            {
                "name": s["name"],
                "id": s.get("id", s["name"]),
                "qty_options": s.get("qty_options") or [],
            }
            for s in prod.get("sizes") or []
        ]
        p["print_modes"] = prod.get("print_modes") or []
        p["print_mode_labels"] = prod.get("print_mode_labels") or {}
        p["options"] = []
        p["discounts"] = prod.get("discounts") or []
        p["notes"] = prod.get("notes") or []
        p["price_factor"] = prod.get("price_factor", DEFAULT_PRICE_FACTOR)
        return p

    if prod.get("calc_type") == "paper_bags":
        p = {k: v for k, v in prod.items() if k not in ("material_refs", "prices")}
        p["calc_type"] = "paper_bags"
        p["sizes"] = [
            {
                "name": s["name"],
                "id": s.get("id", s["name"]),
                "category": s.get("category"),
                "qty_options": s.get("qty_options") or [],
                "width_mm": s.get("width_mm"),
                "height_mm": s.get("height_mm"),
                "depth_mm": s.get("depth_mm"),
            }
            for s in prod.get("sizes") or []
        ]
        p["delivery"] = prod.get("delivery") or {}
        p["mandatory_options"] = prod.get("mandatory_options") or []
        p["options"] = []
        p["discounts"] = prod.get("discounts") or []
        p["price_factor"] = prod.get("price_factor", DEFAULT_PRICE_FACTOR)
        return p

    if prod.get("calc_type") == "wide_format":
        p = {k: v for k, v in prod.items() if k not in ("material_refs",)}
        p["calc_type"] = "wide_format"
        raw_materials = [
            {**m, "name": capitalize_material_name(m.get("name", ""))}
            for m in (prod.get("materials") or [])
        ]
        p["materials"] = group_wide_format_materials_for_ui(raw_materials)
        p["wide_options"] = prod.get("wide_options") or []
        p["resolutions"] = prod.get("resolutions") or [540, 630, 720, 1440]
        p["resolution_surcharges"] = prod.get("resolution_surcharges") or {}
        p["options"] = []
        p["discounts"] = prod.get("discounts") or []
        p["price_factor"] = prod.get("price_factor", DEFAULT_PRICE_FACTOR)
        return p

    p = {k: v for k, v in prod.items() if k not in ("material_refs", "materials")}
    p["materials"] = resolve_product_materials(prod)
    p["options"] = sort_product_options(prod.get("options") or [])
    if p.get("lanyard_option"):
        p["lanyard_option"] = enrich_lanyard_option(p["lanyard_option"], get_works())
    if prod.get("calc_type") == "plotter_grid":
        p["calc_type"] = "plotter_grid"
        p["sheet"] = prod.get("sheet")
        p["plotter_laminations"] = list(_plotter_lamination_map(prod).keys())
        mats = resolve_product_materials(prod)
        p["materials"] = [
            {
                "name": m["name"],
                "calc_mode": m.get("calc_mode", "self_adhesive"),
                "print_modes": m.get("print_modes") or [],
            }
            for m in mats
        ]
        return p
    if prod.get("calc_type") == "notebook":
        p["calc_type"] = "notebook"
        p["block_modes"] = prod.get("block_modes") or ["0+0", "1+0", "4+0"]
        p["cover_material"] = prod.get("cover_material")
        p["block_spec"] = prod.get("block_spec")
        p["default_page_count"] = prod.get("default_page_count", 40)
        p["min_page_count"] = prod.get("min_page_count", 20)
        p["max_page_count"] = prod.get("max_page_count", 90)
        p["materials"] = []
        p["sizes"] = [
            {
                "name": s["name"],
                "format": s.get("format"),
                "cover_print": s.get("cover_print"),
                "qty_options": s.get("qty_options") or [],
            }
            for s in prod.get("sizes", [])
        ]
        return p
    if prod.get("calc_type") == "board_cover":
        p["calc_type"] = "board_cover"
        p["size_label"] = prod.get("size_label", "\u0424ормат")
        p["materials"] = []
        p["board_cover_count_options"] = prod.get("board_cover_count_options", [1, 2])
        p["lamination_label"] = prod.get("lamination_label")
        p["laminations"] = prod.get("laminations") or []
        p["board_mode_label"] = prod.get("board_mode_label")
        p["board_mode_options"] = prod.get("board_mode_options") or []
        p["custom_size"] = prod.get("custom_size") or {}
        p["qty_optional"] = bool(prod.get("qty_optional"))
        p["qty_optional_label"] = prod.get("qty_optional_label")
        p["sizes"] = [
            {
                "name": s["name"],
                "format": s.get("format"),
                "qty_options": s.get("qty_options") or [],
            }
            for s in prod.get("sizes", [])
        ]
        return p
    if prod.get("calc_type") == "branded_cups":
        p["calc_type"] = "branded_cups"
        p["materials"] = [
            {
                "name": o["label"],
                "markup_profile": o["profile"],
                **({"qty_min": o["qty_min"]} if o.get("qty_min") else {}),
                **({"note": o["note"]} if o.get("note") else {}),
            }
            for o in prod.get("application_options") or []
        ]
        p["paper_cup_qty_options"] = prod.get("paper_cup_qty_options") or []
        p["sizes"] = [
            {
                "name": s["name"],
                "qty_options": s.get("qty_options") or [],
            }
            for s in prod.get("sizes", [])
        ]
        p["options"] = sort_product_options(prod.get("options") or [])
        p["options_label"] = prod.get("options_label")
        return p
    if prod.get("calc_type") == "sublimation":
        p["calc_type"] = "sublimation"
        p["custom_qty"] = bool(prod.get("custom_qty", True))
        p["qty_min"] = int(prod.get("qty_min") or 1)
        p["qty_max"] = int(prod.get("qty_max") or 1000)
        p["materials"] = [
            {
                "name": name,
                "markup_profile": variant.get("markup_profile", "white_mugs"),
            }
            for name, variant in (prod.get("variants") or {}).items()
        ]
        p["sizes"] = [
            {
                "name": s["name"],
                "qty_options": s.get("qty_options") or [],
                "markup_profiles": s.get("markup_profiles") or {},
            }
            for s in prod.get("sizes", [])
        ]
        return p
    if prod.get("calc_type") == "brochure_menu":
        p["calc_type"] = "brochure_menu"
        p["binding_types"] = prod.get("binding_types") or []
        p["orientations"] = prod.get("orientations") or []
        p["binding_positions"] = prod.get("binding_positions") or []
        p["lamination_by_binding"] = prod.get("lamination_by_binding") or {}
        p["size_availability_rules"] = prod.get("size_availability_rules") or []
        p["print_modes"] = prod.get("print_modes") or ["1+0", "1+1", "4+0", "4+4"]
        p["default_page_count"] = prod.get("default_page_count", 8)
        p["min_page_count"] = prod.get("min_page_count", 4)
        p["max_page_count"] = prod.get("max_page_count", 40)
        p["brochure_binding_factor"] = prod.get("brochure_binding_factor", 1.6)
        p["materials"] = [
            {
                "name": m["name"],
                "markup_profile": m.get("markup_profile", "default"),
                "print_modes": m.get("print_modes") or p["print_modes"],
                "excluded_options": m.get("excluded_options") or [],
            }
            for m in resolve_product_materials(prod)
        ]
        p["sizes"] = [
            {
                "name": s["name"],
                "size_key": _brochure_size_key(s),
                "binding": s.get("binding"),
                "orientation": s.get("orientation"),
                "ignore_orientation": bool(s.get("ignore_orientation")),
                "format_order": s.get("format_order"),
                "qty_options": s.get("qty_options") or [],
            }
            for s in sorted(
                prod.get("sizes", []),
                key=lambda x: (x.get("format_order", 99), x.get("name", "")),
            )
        ]
        return p
    if prod.get("calc_type") == "envelopes":
        p["calc_type"] = "envelopes"
        p["print_modes"] = prod.get("print_modes") or ["4+0", "без печати"]
        p["materials"] = [
            {"name": m["name"], "markup_profile": m.get("markup_profile", "default")}
            for m in resolve_product_materials(prod)
        ]
        p["sizes"] = [
            {
                "name": s["name"],
                "qty_options": s.get("qty_options") or [],
                "print_modes": s.get("print_modes") or p["print_modes"],
            }
            for s in prod.get("sizes", [])
        ]
        return p
    if prod.get("calc_type") == "quarterly_calendars":
        p["calc_type"] = "quarterly_calendars"
        p["print_modes"] = prod.get("print_modes") or []
        p["print_mode_label"] = prod.get("print_mode_label") or "Тип блока"
        p["hide_print_mode"] = bool(prod.get("hide_print_mode"))
        p["finish_select_kinds"] = prod.get("finish_select_kinds") or []
        p["finish_select_label"] = prod.get("finish_select_label")
        p["finish_none_label"] = prod.get("finish_none_label")
        p["calendar_group"] = prod.get("calendar_group")
        p["calendar_tab"] = prod.get("calendar_tab")
        p["options"] = sort_product_options(prod.get("options") or [])
        p["materials"] = []
        p["sizes"] = [
            {
                "name": s["name"],
                "qty_options": s.get("qty_options") or [],
                "lamination_surfaces_options": s.get("lamination_surfaces_options")
                or prod.get("lamination_surfaces_options")
                or [],
                "lamination_surface_divisors": s.get("lamination_surface_divisors") or [],
            }
            for s in prod.get("sizes", [])
        ]
        return p
    if prod.get("calc_type") == "wall_calendars":
        p["calc_type"] = "wall_calendars"
        p["print_modes"] = prod.get("print_modes") or []
        p["print_mode_label"] = prod.get("print_mode_label") or "Параметр"
        p["hide_print_mode"] = bool(prod.get("hide_print_mode"))
        p["finish_select_kinds"] = prod.get("finish_select_kinds") or []
        p["finish_select_label"] = prod.get("finish_select_label")
        p["finish_none_label"] = prod.get("finish_none_label")
        p["calendar_group"] = prod.get("calendar_group")
        p["calendar_tab"] = prod.get("calendar_tab")
        p["options"] = sort_product_options(prod.get("options") or [])
        p["materials"] = []
        p["sizes"] = [
            {
                "name": s["name"],
                "qty_options": s.get("qty_options") or [],
            }
            for s in prod.get("sizes", [])
        ]
        return p
    if prod.get("calc_type"):
        p["calc_type"] = prod["calc_type"]
    if prod.get("qty_tiers"):
        p["qty_tiers"] = prod["qty_tiers"]
    return p


def get_admin_materials():
    return get_materials()


def save_materials(materials):
    """Сохраняет общий каталог материалов."""
    save_json("materials.json", materials)


def _slugify(name):
    base = re.sub(r"[^\w\s-]", "", str(name).strip(), flags=re.UNICODE)
    return re.sub(r"\s+", "_", base.strip())


def _unique_key(prefix, name, existing):
    base = f"{prefix}{_slugify(name)}"[:48]
    key = base
    n = 2
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


def add_catalog_material(name, price_a3, custom_price_a3=False):
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название материала.")
    price_a3 = float(price_a3)
    if price_a3 < 0:
        raise ValueError("Цена не может быть отрицательной.")
    materials = get_materials()
    if any(m["name"].lower() == name.lower() for m in materials):
        raise ValueError("Материал с таким названием уже есть в каталоге.")
    mid = _unique_key("mat_", name, {m.get("id") for m in materials if m.get("id")})
    entry = {"id": mid, "name": name, "price_a3": round(price_a3, 2)}
    if custom_price_a3:
        entry["custom_price_a3"] = True
    materials.append(entry)
    save_materials(materials)
    return entry


def add_work_type(name, norm_per_min):
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название работы.")
    norm = float(norm_per_min)
    if norm <= 0:
        raise ValueError("Норматив должен быть больше нуля.")
    works = get_works()
    if any(w["name"].lower() == name.lower() for w in works.values()):
        raise ValueError("Работа с таким названием уже есть.")
    key = _unique_key("", name, set(works.keys()))
    hour_cost = float(get_settings().get("hour_cost", 440))
    works[key] = {
        "name": name,
        "norm_per_min": norm,
        "cost": round((hour_cost / 60.0) / norm, 6),
    }
    save_json("works.json", works)
    return key, works[key]


def add_option_film(name, price_a3):
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название плёнки.")
    price_a3 = float(price_a3)
    if price_a3 < 0:
        raise ValueError("Цена не может быть отрицательной.")
    films = get_shared_films()
    if any(f["name"].lower() == name.lower() for f in films.values()):
        raise ValueError("Плёнка с таким названием уже есть.")
    key = _unique_key("", name, set(films.keys()))
    films[key] = {"name": name, "a3": round(price_a3, 2)}
    save_films_and_sync(films)
    return key, films[key]


def add_product_option(name, kind, product_ids, params):
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название опции.")
    kinds = get_option_kinds()
    if kind not in kinds:
        raise ValueError("Неизвестный тип опции.")
    formula = kinds[kind]["formula"]
    if not product_ids:
        raise ValueError("Выберите хотя бы один продукт.")
    products = get_products()
    opt = {"name": name, "kind": kind, "price_factor": float(params.get("price_factor") or 1.6)}
    if formula == "lamination":
        if not params.get("film") or not params.get("work"):
            raise ValueError("Для ламинации укажите плёнку и вид работы.")
        opt["film"] = params["film"]
        opt["work"] = params["work"]
    elif formula == "corner":
        if not params.get("work"):
            raise ValueError("Укажите вид работы.")
        opt["work"] = params["work"]
        opt["count"] = int(params.get("count") or 4)
    elif formula == "crease":
        if not params.get("work"):
            raise ValueError("Укажите вид работы.")
        opt["work"] = params["work"]
        opt["count"] = int(params.get("count") or 0)
        opt["count_min"] = int(params.get("count_min") or 0)
        opt["count_max"] = int(params.get("count_max") or 10)
    elif formula == "work_unit":
        if not params.get("work"):
            raise ValueError("Укажите вид работы.")
        opt["work"] = params["work"]
        opt["count"] = int(params.get("count") or 1)
    elif formula == "foil":
        if not params.get("film"):
            raise ValueError("Для фольгирования укажите плёнку софт-тач.")
        opt["film"] = params["film"]
        opt["foil"] = params.get("foil") or "foil"
        opt["print_sides"] = int(params.get("print_sides") or 1)
        opt["surcharge"] = float(params.get("surcharge") or 1.3)
    elif formula == "fixed":
        if params.get("cost_add") in (None, ""):
            raise ValueError("Укажите прибавку к себестоимости, ₽.")
        opt["cost_add"] = float(params["cost_add"])
    elif formula == "fixed_per_item":
        if params.get("cost_add") in (None, ""):
            raise ValueError("Укажите цену за единицу, ₽.")
        opt["cost_add"] = float(params["cost_add"])
        opt["count"] = int(params.get("count") or 0)
        opt["count_min"] = int(params.get("count_min") or 0)
        opt["count_max"] = int(params.get("count_max") or 20)
    added = []
    for pid in product_ids:
        prod = products.get(pid)
        if not prod:
            continue
        opts = prod.setdefault("options", [])
        if any(o["name"].lower() == name.lower() for o in opts):
            continue
        opts.append(dict(opt))
        added.append(prod.get("title", pid))
    if not added:
        raise ValueError("Опция уже есть во всех выбранных продуктах.")
    save_json("products.json", products)
    return added


def get_products():
    return load_json("products.json", {})


def get_users():
    return load_json("users.json", {})


def save_users(u):
    save_json("users.json", u)


def user_status(user):
    """Статус аккаунта: approved / pending / rejected (legacy — approved)."""
    return user.get("status") or USER_STATUS_APPROVED


def is_user_active(user):
    return user_status(user) == USER_STATUS_APPROVED


def registration_enabled():
    return os.environ.get("REGISTRATION_OPEN", "1").strip() not in ("0", "false", "no")


# Восстановление пароля (без e-mail): администратор выпускает пользователю
# одноразовый код, по которому тот сам задаёт новый пароль.
RESET_TOKEN_TTL_SECONDS = 24 * 60 * 60  # 24 часа


def _token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_password_reset_token(uname):
    """Выпускает одноразовый код восстановления для пользователя. Возвращает код."""
    users = get_users()
    u = users.get(uname)
    if not u:
        raise ValueError("Пользователь не найден.")
    raw = secrets.token_urlsafe(24)
    now = datetime.now()
    u["password_reset"] = {
        "token": _token_digest(raw),
        "issued_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(seconds=RESET_TOKEN_TTL_SECONDS)).isoformat(timespec="seconds"),
    }
    save_users(users)
    return raw


def validate_password_reset_token(uname, raw_token):
    """Проверяет одноразовый код восстановления (существование, срок, совпадение)."""
    users = get_users()
    u = users.get(uname)
    if not u:
        return False
    rec = u.get("password_reset") or {}
    digest = rec.get("token")
    if not digest or not raw_token:
        return False
    if not hmac.compare_digest(str(digest), _token_digest(str(raw_token))):
        return False
    expires = rec.get("expires_at")
    try:
        if expires and datetime.fromisoformat(expires) < datetime.now():
            return False
    except (TypeError, ValueError):
        return False
    return True


def clear_password_reset_token(uname):
    """Удаляет использованный код восстановления."""
    users = get_users()
    u = users.get(uname)
    if u:
        u.pop("password_reset", None)
        save_users(users)


def get_history():
    return load_json("history.json", [])


def save_history(h):
    save_json("history.json", h)


USER_ACTIVITY_LOGIN_LOG_MAX = 500


def get_user_activity():
    return load_json("user_activity.json", {"users": {}, "login_log": []})


def save_user_activity(data):
    save_json("user_activity.json", data)


def _ensure_activity_user(act, uname):
    users = act.setdefault("users", {})
    if uname not in users:
        users[uname] = {
            "login_count": 0,
            "last_login": None,
            "calc_count": 0,
            "last_calc": None,
        }
    return users[uname]


def record_user_login(uname):
    if not uname:
        return
    act = get_user_activity()
    u = _ensure_activity_user(act, uname)
    now = datetime.now().isoformat(timespec="seconds")
    u["login_count"] = int(u.get("login_count") or 0) + 1
    u["last_login"] = now
    log = act.setdefault("login_log", [])
    log.insert(0, {"user": uname, "when": now})
    act["login_log"] = log[:USER_ACTIVITY_LOGIN_LOG_MAX]
    save_user_activity(act)


def record_user_calc(uname):
    if not uname:
        return
    act = get_user_activity()
    u = _ensure_activity_user(act, uname)
    now = datetime.now().isoformat(timespec="seconds")
    u["calc_count"] = int(u.get("calc_count") or 0) + 1
    u["last_calc"] = now
    save_user_activity(act)


def get_news_items():
    return load_json("news.json", [])


def save_news_items(items):
    save_json("news.json", items)


def get_news_reads():
    return load_json("news_reads.json", {})


def save_news_reads(data):
    save_json("news_reads.json", data)


def _new_news_id():
    return datetime.now().strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(4)


def get_unread_news(username):
    if not username:
        return []
    reads = set(get_news_reads().get(username) or [])
    items = [
        n for n in get_news_items()
        if n.get("active", True) and n.get("id") not in reads
    ]
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def mark_news_read(username, news_id):
    if not username or not news_id:
        return
    data = get_news_reads()
    seen = set(data.get(username) or [])
    seen.add(news_id)
    data[username] = sorted(seen)
    save_news_reads(data)


def saved_calc_counts():
    counts = {}
    for h in get_history():
        uname = h.get("user")
        if uname:
            counts[uname] = counts.get(uname, 0) + 1
    return counts


def build_user_stats_rows():
    users = get_users()
    act = get_user_activity()
    act_users = act.get("users") or {}
    saved = saved_calc_counts()
    rows = []
    for uname, u in users.items():
        if user_status(u) != USER_STATUS_APPROVED:
            continue
        au = act_users.get(uname) or {}
        rows.append({
            "username": uname,
            "full_name": u.get("full_name") or uname,
            "role": u.get("role", "employee"),
            "last_login": au.get("last_login"),
            "login_count": int(au.get("login_count") or 0),
            "calc_count": int(au.get("calc_count") or 0),
            "last_calc": au.get("last_calc"),
            "saved_count": saved.get(uname, 0),
        })
    rows.sort(key=lambda r: (r["full_name"].casefold(), r["username"]))
    return rows, act.get("login_log") or []


def save_print_costs_and_sync(
    print_4_0=None, print_1_0=None, xerox_print_4_0=None, xerox_print_1_0=None,
):
    """Сохраняет стоимость печати в settings и синхронизирует Конику во все продукты."""
    s = get_settings()
    if print_4_0 is not None:
        s["print_4_0"] = print_4_0
    if print_1_0 is not None:
        s["print_1_0"] = print_1_0
    if xerox_print_4_0 is not None:
        s["xerox_print_4_0"] = xerox_print_4_0
    if xerox_print_1_0 is not None:
        s["xerox_print_1_0"] = xerox_print_1_0
    save_json("settings.json", s)
    products = get_products()
    for prod in products.values():
        if print_4_0 is not None and "print_4_0" in prod:
            prod["print_4_0"] = print_4_0
        if print_1_0 is not None and "print_1_0" in prod:
            prod["print_1_0"] = print_1_0
    save_json("products.json", products)


def get_shared_films():
    """Плёнки опций — из первого продукта, где они заданы (общий справочник)."""
    for prod in get_products().values():
        if prod.get("films"):
            return prod["films"]
    return {}


def save_films_and_sync(films):
    """Сохраняет цены плёнок во все продукты с тем же набором ключей."""
    products = get_products()
    for prod in products.values():
        if not prod.get("films"):
            continue
        for key, film in prod["films"].items():
            if key in films:
                film["a3"] = films[key]["a3"]
    save_json("products.json", products)


DEFAULT_OPTION_KINDS = {
    "lamination": {"label": "Ламинация", "ui": "select", "formula": "lamination", "builtin": True},
    "corner": {"label": "Скругление углов", "ui": "checkbox", "formula": "corner", "builtin": True},
    "foil": {"label": "Фольгирование", "ui": "checkbox", "formula": "foil", "builtin": True},
    "crease": {"label": "Биговка", "ui": "count", "formula": "crease", "builtin": True},
    "ribbon_hole": {"label": "Отверстие для ленты", "ui": "checkbox", "formula": "work_unit", "builtin": True},
    "eyelet": {"label": "Люверсы", "ui": "checkbox", "formula": "eyelet", "builtin": True},
    "corner_cut": {"label": "Срез углов", "ui": "checkbox", "formula": "corner_cut", "builtin": True},
}


def get_option_kinds():
    """Справочник типов опций: формула расчёта и место в калькуляторе."""
    kinds = load_json("option_kinds.json", {})
    if not kinds:
        return dict(DEFAULT_OPTION_KINDS)
    return kinds


def save_option_kinds(kinds):
    save_json("option_kinds.json", kinds)


def option_kind_formula(kind):
    spec = get_option_kinds().get(kind)
    return spec.get("formula") if spec else kind


def validate_finish_options(prod, selected_opts):
    """Не более одной «отделки» (ламинация / фольгирование) из finish_select_kinds."""
    kinds = prod.get("finish_select_kinds")
    if not kinds:
        return
    finish = []
    for oname in selected_opts or []:
        o = next((x for x in prod.get("options", []) if x["name"] == oname), None)
        if o and o.get("kind") in kinds:
            finish.append(oname)
    if len(finish) > 1:
        raise ValueError(
            "Можно выбрать только один вид отделки: ламинацию или фольгирование."
        )


def validate_option_exclusions(prod, selected_opts):
    """Нельзя выбирать взаимоисключающие опции (поле excludes у опции)."""
    selected = set(selected_opts or [])
    if not selected:
        return
    by_name = {o["name"]: o for o in prod.get("options") or []}
    for oname in selected:
        opt = by_name.get(oname)
        if not opt:
            continue
        for ex in opt.get("excludes") or []:
            if ex in selected:
                raise ValueError(
                    f"Нельзя выбрать одновременно «{oname}» и «{ex}»."
                )


def add_option_kind(label, ui, formula):
    label = (label or "").strip()
    if not label:
        raise ValueError("Укажите название типа опции.")
    if ui not in ("select", "checkbox", "count"):
        raise ValueError("UI: «выпадающий список», «галочки» или «количество».")
    if formula not in ("lamination", "corner", "corner_cut", "crease", "foil", "fixed", "work_unit", "eyelet"):
        raise ValueError("Неизвестная формула.")
    kinds = get_option_kinds()
    if any(k["label"].lower() == label.lower() for k in kinds.values()):
        raise ValueError("Тип с таким названием уже есть.")
    key = _unique_key("", label, set(kinds.keys()))
    kinds[key] = {"label": label, "ui": ui, "formula": formula, "builtin": False}
    save_option_kinds(kinds)
    return key, kinds[key]


PLOTTER_NO_LAMINATION = "Без ламинации"


def get_plotter_products():
    return {pid: p for pid, p in get_products().items() if p.get("calc_type") == "plotter_grid"}


def build_plotter_lamination_admin():
    """Ламинации плоттера для админки."""
    films = get_shared_films()
    works = get_works()
    items = []
    for pid, prod in get_plotter_products().items():
        rows = []
        for name, cfg in _plotter_lamination_map(prod).items():
            if name == PLOTTER_NO_LAMINATION or cfg is None:
                rows.append({"name": name, "removable": False})
                continue
            film_key = cfg.get("film")
            work_key = cfg.get("work")
            rows.append({
                "name": name,
                "film": film_key,
                "film_label": films.get(film_key, {}).get("name", film_key or "—"),
                "work": work_key,
                "work_label": works.get(work_key, {}).get("name", work_key or "—"),
                "removable": True,
            })
        items.append({"id": pid, "title": prod.get("title", pid), "laminations": rows})
    return items


def add_plotter_lamination(product_id, name, film_key, work_key):
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название ламинации.")
    if name == PLOTTER_NO_LAMINATION:
        raise ValueError(f"«{PLOTTER_NO_LAMINATION}» зарезервировано.")
    films = get_shared_films()
    works = get_works()
    if not film_key or film_key not in films:
        raise ValueError("Выберите плёнку из списка.")
    if not work_key or work_key not in works:
        raise ValueError("Выберите вид работы.")
    products = get_products()
    prod = products.get(product_id)
    if not prod or prod.get("calc_type") != "plotter_grid":
        raise ValueError("Продукт не найден.")
    lam_map = dict(_plotter_lamination_map(prod))
    if name in lam_map:
        raise ValueError("Ламинация с таким названием уже есть.")
    lam_map[name] = {"film": film_key, "work": work_key}
    prod["plotter_lamination"] = lam_map
    save_json("products.json", products)
    return name


def remove_plotter_lamination(product_id, name):
    name = (name or "").strip()
    if not name or name == PLOTTER_NO_LAMINATION:
        raise ValueError(f"Нельзя удалить «{PLOTTER_NO_LAMINATION}».")
    products = get_products()
    prod = products.get(product_id)
    if not prod or prod.get("calc_type") != "plotter_grid":
        raise ValueError("Продукт не найден.")
    lam_map = dict(_plotter_lamination_map(prod))
    if name not in lam_map:
        raise ValueError("Ламинация не найдена.")
    lam_map.pop(name)
    prod["plotter_lamination"] = lam_map
    save_json("products.json", products)


MARKUP_PROFILE_ORDER = (
    "default", "standard", "designer",
    "0+0", "1+0", "4+0", "1+1", "4+4",
    "Картон", "Дизайнерская",
)


def _profile_sort_key(profile_id):
    try:
        return MARKUP_PROFILE_ORDER.index(profile_id)
    except ValueError:
        return len(MARKUP_PROFILE_ORDER)


def _tiers_signature(tiers):
    return tuple((int(q), float(m)) for q, m in (tiers or []))


def _markup_profiles_signature(markup_profiles):
    """Сигнатура всех профилей наценки размера."""
    items = []
    for pid in sorted(markup_profiles or {}):
        prof = markup_profiles[pid]
        items.append((
            pid,
            _tiers_signature(prof.get("tiers") or []),
            tuple(
                (int(ft["min"]), int(ft["max"]), float(ft["price"]))
                for ft in prof.get("flat_price_tiers") or []
            ),
            tuple(
                (int(mr["min"]), int(mr["max"]), float(mr["markup"]))
                for mr in prof.get("markup_ranges") or []
            ),
        ))
    return tuple(items)


def _group_tiers_by_markup(tiers):
    """Тиражи с одинаковой наценкой — одна строка в админке."""
    groups = []
    for i, (qty, markup) in enumerate(tiers or []):
        qty = int(qty)
        markup = float(markup)
        if groups and groups[-1]["markup"] == markup:
            g = groups[-1]
            g["qtys"].append(qty)
            g["tier_indices"].append(i)
        else:
            groups.append({
                "qtys": [qty],
                "tier_indices": [i],
                "markup": markup,
            })
    for g in groups:
        qtys = g["qtys"]
        if len(qtys) == 1:
            g["label"] = str(qtys[0])
        elif qtys == list(range(qtys[0], qtys[-1] + 1)):
            g["label"] = f"{qtys[0]}–{qtys[-1]}"
        else:
            g["label"] = ", ".join(str(q) for q in qtys)
    return groups


def _group_flat_by_price(flat_tiers):
    """Flat-тиражи с одинаковой ценой — одна строка."""
    groups = []
    for i, ft in enumerate(flat_tiers or []):
        price = float(ft["price"])
        lo, hi = int(ft["min"]), int(ft["max"])
        if groups and groups[-1]["price"] == price:
            g = groups[-1]
            g["ranges"].append((lo, hi))
            g["flat_indices"].append(i)
        else:
            groups.append({
                "ranges": [(lo, hi)],
                "flat_indices": [i],
                "price": price,
            })
    for g in groups:
        labels = []
        for lo, hi in g["ranges"]:
            labels.append(str(lo) if lo == hi else f"{lo}–{hi}")
        g["label"] = ", ".join(labels)
    return groups


def _group_ranges_by_markup(markup_ranges):
    groups = []
    for i, mr in enumerate(markup_ranges or []):
        markup = float(mr["markup"])
        lo, hi = int(mr["min"]), int(mr["max"])
        if groups and groups[-1]["markup"] == markup:
            g = groups[-1]
            g["ranges"].append((lo, hi))
            g["range_indices"].append(i)
        else:
            groups.append({
                "ranges": [(lo, hi)],
                "range_indices": [i],
                "markup": markup,
            })
    for g in groups:
        labels = []
        for lo, hi in g["ranges"]:
            labels.append(str(lo) if lo == hi else f"{lo}–{hi}")
        g["label"] = ", ".join(labels)
    return groups


def _format_profile_group_label(profile_ids, labels):
    if len(profile_ids) == 1:
        pid = profile_ids[0]
        return labels.get(pid, pid)
    return "; ".join(labels.get(p, p) for p in profile_ids)


def _format_size_cluster_label(names):
    if len(names) == 1:
        return names[0]
    return "; ".join(names)


def _profile_content_signature(prof):
    """Содержимое профиля без id — для объединения одинаковых."""
    return (
        _tiers_signature(prof.get("tiers") or []),
        tuple(
            (int(ft["min"]), int(ft["max"]), float(ft["price"]))
            for ft in prof.get("flat_price_tiers") or []
        ),
        tuple(
            (int(mr["min"]), int(mr["max"]), float(mr["markup"]))
            for mr in prof.get("markup_ranges") or []
        ),
    )


def _build_markup_profile_groups(markup_profiles, profile_labels):
    """Профили с одинаковыми ступенями — одна таблица."""
    by_sig = {}
    for pid, prof in (markup_profiles or {}).items():
        sig = _profile_content_signature(prof)
        bucket = by_sig.setdefault(sig, {"profile_ids": [], "prof": prof})
        bucket["profile_ids"].append(pid)
    groups = []
    for bucket in by_sig.values():
        prof = bucket["prof"]
        pids = sorted(bucket["profile_ids"], key=_profile_sort_key)
        groups.append({
            "profile_ids": pids,
            "profile_label": _format_profile_group_label(pids, profile_labels),
            "tier_groups": _group_tiers_by_markup(prof.get("tiers") or []),
            "flat_groups": _group_flat_by_price(prof.get("flat_price_tiers") or []),
            "range_groups": _group_ranges_by_markup(prof.get("markup_ranges") or []),
        })
    return groups


def build_markup_clusters_for_product(prod):
    """Размеры с одинаковыми наценками — один блок в админке."""
    sizes = prod.get("sizes") or []
    labels = prod.get("markup_profile_labels") or {}
    by_sig = {}
    for si, size in enumerate(sizes):
        mp = size.get("markup_profiles") or {}
        if not mp:
            continue
        sig = _markup_profiles_signature(mp)
        bucket = by_sig.setdefault(sig, {"size_indices": [], "markup_profiles": mp})
        bucket["size_indices"].append(si)
    clusters = []
    for bucket in by_sig.values():
        size_names = [sizes[i]["name"] for i in bucket["size_indices"]]
        clusters.append({
            "size_indices": bucket["size_indices"],
            "size_label": _format_size_cluster_label(size_names),
            "profile_groups": _build_markup_profile_groups(bucket["markup_profiles"], labels),
        })
    return clusters


CARDBOARD_DESIGNER_PRODUCTS = frozenset({
    "vizitki", "otkrytki_diplomy", "beizhi", "birki",
})


def _cardboard_material_profile(prod):
    """Профиль наценки, привязанный к бумаге «Картон 300-350 г»."""
    for m in resolve_product_materials(prod):
        name = (m.get("name") or "").lower()
        if "картон" in name and ("300" in name or "350" in name):
            return m.get("markup_profile", "default")
    return None


def _column_profile_groups(prod, profile_id, labels):
    """Группы профилей для одной колонки (картон / дизайнерские)."""
    sizes = prod.get("sizes") or []
    mp = (sizes[0].get("markup_profiles") or {}) if sizes else {}
    prof = mp.get(profile_id, {})
    return {
        "profile_ids": [profile_id],
        "profile_label": labels.get(profile_id, profile_id),
        "tier_groups": _group_tiers_by_markup(prof.get("tiers") or []),
        "flat_groups": _group_flat_by_price(prof.get("flat_price_tiers") or []),
        "range_groups": _group_ranges_by_markup(prof.get("markup_ranges") or []),
    }



def build_markup_products():
    """Продукты с наценками для админки."""
    items = []
    for pid, prod in get_products().items():
        labels = prod.get("markup_profile_labels") or {}
        single = []
        pf = prod.get("price_factor")
        if pf is not None:
            single.append({
                "field": "price_factor",
                "label": "Наценка price_factor",
                "hint": "множитель к себестоимости из таблицы цен",
                "value": float(pf),
            })
        fm = prod.get("fixed_markup")
        if fm is not None:
            single.append({
                "field": "fixed_markup",
                "label": "Фиксированная наценка",
                "hint": "множитель, не зависит от тиража",
                "value": float(fm),
            })
        entry = {
            "id": pid,
            "title": prod.get("title", pid),
            "markup_profile_labels": labels,
            "sizes": prod.get("sizes") or [],
            "calc_type": prod.get("calc_type"),
            "markup_clusters": build_markup_clusters_for_product(prod),
            "single": single,
        }
        if prod.get("calc_type") == "plotter_grid" and prod.get("markup_profiles"):
            entry["plotter_profile_groups"] = _build_markup_profile_groups(
                prod.get("markup_profiles"), labels,
            )
        if not entry["sizes"] and not entry["markup_clusters"] and not single and "plotter_profile_groups" not in entry:
            continue
        items.append(entry)
    return items


def _apply_grouped_markup_from_form(products):
    """Сохраняет наценки из сгруппированной формы админки."""
    for pid, prod in products.items():
        # Единая наценка продукта (price_factor / fixed_markup).
        for field in ("price_factor", "fixed_markup"):
            raw = request.form.get(f"sx_{pid}_{field}")
            if raw in (None, ""):
                continue
            try:
                val = float(str(raw).replace("\u00a0", "").replace(" ", "").replace(",", "."))
            except (TypeError, ValueError):
                continue
            if val > 0:
                prod[field] = val
        if prod.get("calc_type") == "plotter_grid":
            for pgi, pg in enumerate(
                _build_markup_profile_groups(prod.get("markup_profiles") or {}, prod.get("markup_profile_labels") or {})
            ):
                for tgi, tg in enumerate(pg["tier_groups"]):
                    v = request.form.get(f"mt_{pid}_prod_pg_{pgi}_tg_{tgi}")
                    if v is None or v == "":
                        continue
                    val = float(v)
                    for profile_id in pg["profile_ids"]:
                        tiers = prod["markup_profiles"][profile_id]["tiers"]
                        for ti in tg["tier_indices"]:
                            tiers[ti][1] = val
                for fgi, fg in enumerate(pg["flat_groups"]):
                    v = request.form.get(f"fp_{pid}_prod_pg_{pgi}_fg_{fgi}")
                    if v is None or v == "":
                        continue
                    val = float(v)
                    for profile_id in pg["profile_ids"]:
                        flats = prod["markup_profiles"][profile_id]["flat_price_tiers"]
                        for fi in fg["flat_indices"]:
                            flats[fi]["price"] = val
                for rgi, rg in enumerate(pg["range_groups"]):
                    v = request.form.get(f"mr_{pid}_prod_pg_{pgi}_rg_{rgi}")
                    if v is None or v == "":
                        continue
                    val = float(v)
                    for profile_id in pg["profile_ids"]:
                        ranges = prod["markup_profiles"][profile_id]["markup_ranges"]
                        for ri in rg["range_indices"]:
                            ranges[ri]["markup"] = val
            continue

        for ci, cluster in enumerate(build_markup_clusters_for_product(prod)):
            for pgi, pg in enumerate(cluster["profile_groups"]):
                for tgi, tg in enumerate(pg["tier_groups"]):
                    v = request.form.get(f"mt_{pid}_cl_{ci}_pg_{pgi}_tg_{tgi}")
                    if v is None or v == "":
                        continue
                    val = float(v)
                    for si in cluster["size_indices"]:
                        size = prod["sizes"][si]
                        for profile_id in pg["profile_ids"]:
                            tiers = size["markup_profiles"][profile_id]["tiers"]
                            for ti in tg["tier_indices"]:
                                tiers[ti][1] = val
                for fgi, fg in enumerate(pg["flat_groups"]):
                    v = request.form.get(f"fp_{pid}_cl_{ci}_pg_{pgi}_fg_{fgi}")
                    if v is None or v == "":
                        continue
                    val = float(v)
                    for si in cluster["size_indices"]:
                        size = prod["sizes"][si]
                        for profile_id in pg["profile_ids"]:
                            flats = size["markup_profiles"][profile_id]["flat_price_tiers"]
                            for fi in fg["flat_indices"]:
                                flats[fi]["price"] = val
                for rgi, rg in enumerate(pg["range_groups"]):
                    v = request.form.get(f"mr_{pid}_cl_{ci}_pg_{pgi}_rg_{rgi}")
                    if v is None or v == "":
                        continue
                    val = float(v)
                    for si in cluster["size_indices"]:
                        size = prod["sizes"][si]
                        for profile_id in pg["profile_ids"]:
                            ranges = size["markup_profiles"][profile_id]["markup_ranges"]
                            for ri in rg["range_indices"]:
                                ranges[ri]["markup"] = val

        for si, size in enumerate(prod.get("sizes") or []):
            for group, rows in (size.get("markup") or {}).items():
                for ri, row in enumerate(rows or []):
                    v = request.form.get(f"mk_{pid}_{si}_{group}_{ri}")
                    if v is not None and v != "":
                        row[1] = float(v)



def build_binding_products():
    """Продукты с привязками материалов для админки."""
    items = []
    for pid, prod in get_products().items():
        labels = prod.get("markup_profile_labels") or {"default": "Наценка"}
        bound = {r["id"]: r.get("markup_profile", "default") for r in prod.get("material_refs") or []}
        items.append({
            "id": pid,
            "title": prod.get("title", pid),
            "markup_profile_labels": labels,
            "bound": bound,
        })
    return items


def build_options_preview_by_product():
    """Предпросмотр прибавок опций по каждому продукту."""
    items = []
    for pid, prod in get_products().items():
        if not prod.get("options") or not prod.get("sizes"):
            continue
        preview = {s["name"]: options_preview(prod, s) for s in prod["sizes"]}
        items.append({
            "id": pid,
            "title": prod.get("title", pid),
            "sizes": prod["sizes"],
            "preview": preview,
        })
    return items


@app.context_processor
def inject_option_kinds():
    return {"option_kinds": get_option_kinds()}


# ----------------------------------------------------------------------------
# Пользователи / авторизация
# ----------------------------------------------------------------------------

def ensure_admin():
    """Создаёт администратора по умолчанию, если пользователей ещё нет."""
    users = load_json("users.json", {})
    changed = False
    for u in users.values():
        if not u.get("status"):
            u["status"] = USER_STATUS_APPROVED
            changed = True
    if not users:
        users["admin"] = {
            "password": generate_password_hash("admin"),
            "role": "admin",
            "full_name": "Администратор",
            "status": USER_STATUS_APPROVED,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        changed = True
    if changed:
        save_users(users)


def real_user():
    """Фактически вошедший пользователь (без учёта режима просмотра)."""
    uname = session.get("user")
    if not uname:
        return None
    users = get_users()
    u = users.get(uname)
    if not u:
        return None
    return {"username": uname, **u}


def current_user():
    u = real_user()
    if not u:
        return None
    if u.get("role") == "admin" and session.get("view_as") == "employee":
        return {**u, "role": "employee", "viewing_as_employee": True}
    return u


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        u = real_user()
        if not u or not is_user_active(u):
            session.pop("user", None)
            session.pop("view_as", None)
            if u and not is_user_active(u):
                flash("Доступ закрыт. Аккаунт ожидает одобрения или был отклонён.", "error")
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrap


def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        u = real_user()
        if not u or not is_user_active(u):
            session.pop("user", None)
            session.pop("view_as", None)
            return redirect(url_for("login", next=request.path))
        if u.get("role") != "admin":
            flash("Доступ только для администратора.", "error")
            return redirect(url_for("calculator"))
        return f(*a, **kw)
    return wrap


@app.context_processor
def inject_user():
    u = current_user()
    ru = real_user()
    return {
        "user": u,
        "real_user": ru,
        "viewing_as_employee": bool(
            ru and ru.get("role") == "admin" and session.get("view_as") == "employee"
        ),
    }


# ----------------------------------------------------------------------------
# Движок расчёта
# ----------------------------------------------------------------------------

def _markup_for(markup_list, qty):
    """Возвращает множитель наценки для точного тиража (или ближайшего меньшего)."""
    tiers = sorted([(int(q), float(m)) for q, m in markup_list], key=lambda x: x[0])
    chosen = tiers[0][1] if tiers else 1.0
    for q, m in tiers:
        if qty >= q:
            chosen = m
    for q, m in tiers:
        if q == qty:
            return m
    return chosen


def _markup_profile_data(size, profile="default"):
    """Данные наценки для профиля размера (с поддержкой legacy-форматов)."""
    profiles = size.get("markup_profiles")
    if profiles:
        if profile and profile in profiles:
            return profiles[profile]
        if "default" in profiles:
            return profiles["default"]
        return next(iter(profiles.values()), {})
    legacy_map = {"standard": "Картон", "designer": "Дизайнерская"}
    if size.get("markup"):
        key = legacy_map.get(profile, profile)
        tiers = size["markup"].get(key)
        if tiers is None and size["markup"]:
            tiers = next(iter(size["markup"].values()))
        return {"tiers": tiers or []}
    return {
        "tiers": size.get("markup_tiers") or [],
        "flat_price_tiers": size.get("flat_price_tiers") or [],
        "markup_ranges": size.get("markup_ranges") or [],
    }


def _resolve_markup(size, qty, profile="default"):
    """Возвращает (markup, flat_price). Если flat_price — фиксированная цена за шт."""
    prof = _markup_profile_data(size, profile)
    for ft in prof.get("flat_price_tiers") or []:
        if ft["min"] <= qty <= ft["max"]:
            return None, float(ft["price"])
    markup_ranges = prof.get("markup_ranges") or []
    for mr in markup_ranges:
        if mr["min"] <= qty <= mr["max"]:
            return float(mr["markup"]), None
    tiers = prof.get("tiers") or []
    return _markup_for(tiers, qty), None


def enrich_lanyard_option(lanyard_option, works):
    """Добавляет price_add к вариантам крепления ленты для UI."""
    if not lanyard_option:
        return None
    lo = dict(lanyard_option)
    factor = float(lo.get("price_factor", DEFAULT_PRICE_FACTOR))
    attachments = []
    for att in lo.get("attachments") or []:
        a = dict(att)
        raw_cost = a.get("cost")
        if raw_cost is None:
            work_key = a.get("material_work")
            raw_cost = float(works.get(work_key, {}).get("cost", 0)) if work_key else 0.0
        else:
            raw_cost = float(raw_cost)
        a["cost_add"] = round(raw_cost, 4)
        a["price_add"] = round(raw_cost * factor, 2)
        a["pending"] = bool(lo.get("pending") or a.get("pending") or raw_cost <= 0)
        attachments.append(a)
    lo["attachments"] = attachments
    lo["pending"] = any(a["pending"] for a in attachments)
    return lo


def _beizhi_lanyard_labels(prod, attachment_id, color_id):
    """Подписи выбранного крепления и цвета ленты."""
    lanyard = prod.get("lanyard_option") or {}
    att = next((a for a in lanyard.get("attachments") or [] if a["id"] == attachment_id), None)
    color = next((c for c in lanyard.get("colors") or [] if c["id"] == color_id), None)
    if not att:
        raise ValueError("Выберите тип крепления ленты.")
    if not color:
        raise ValueError("Выберите цвет ленты.")
    return att, color


def calc_beizhi_lanyard(prod, works, payload):
    """Опция «Лента для бейджа»: крепление × цвет, цена из works × price_factor."""
    lanyard_payload = payload.get("lanyard") or {}
    if not lanyard_payload.get("enabled"):
        return None
    attachment_id = (lanyard_payload.get("attachment") or "").strip()
    color_id = (lanyard_payload.get("color") or "").strip()
    att, color = _beizhi_lanyard_labels(prod, attachment_id, color_id)
    lanyard = prod.get("lanyard_option") or {}
    factor = float(lanyard.get("price_factor", DEFAULT_PRICE_FACTOR))
    raw_cost = att.get("cost")
    if raw_cost is None:
        work_key = att.get("material_work")
        raw_cost = float(works.get(work_key, {}).get("cost", 0)) if work_key else 0.0
    else:
        raw_cost = float(raw_cost)
    cost_add = round(raw_cost, 4)
    price_add = round(raw_cost * factor, 2)
    pending = bool(lanyard.get("pending") or att.get("pending") or raw_cost <= 0)
    name = f"Лента для бейджа ({att['name']}, {color['name']})"
    return {
        "name": name,
        "cost_add": cost_add,
        "price_add": price_add,
        "pending": pending,
        "attachment": att["name"],
        "color": color["name"],
    }


def _markup_qty_for_product(prod, size, qty):
    """Тираж для подбора наценки (бейджи: округление вверх до кратного раскладке на листе)."""
    qty = int(qty)
    if prod.get("id") != "beizhi" or qty <= 1:
        return qty
    per_sheet = int(size.get("per_sheet") or 1)
    if per_sheet <= 0:
        return qty
    return int(math.ceil(qty / per_sheet) * per_sheet)


def _magnetic_vinyl_tape_extra(works, material, per_sheet):
    """Поклейка на двусторонний скотч × 2 на лист А3 (магнитный винил в листовках)."""
    cfg = material.get("magnetic_vinyl")
    if not cfg:
        tc = material.get("through_cut") or {}
        if not tc.get("magnetic"):
            return 0.0
        cfg = tc
    tape_work_id = cfg.get("tape_work_id") or cfg.get("extra_work_id", "sublimation_tape_glue")
    tape_mult = float(cfg.get("tape_work_mult") or cfg.get("extra_work_mult", 2))
    per_sheet = float(per_sheet) or 1.0
    return float(works.get(tape_work_id, {}).get("cost", 0)) * tape_mult / per_sheet


def compute_option(opt, prod, works, per_sheet):
    """Считает доп. опцию по формуле для конкретного размера (per_sheet).

    Возвращает (cost_add, price_add) — прибавки к себестоимости и к цене за 1 шт.
    Все исходные «стоимости за лист» и работы берутся из справочников (редактируются в админке).
    """
    films = prod.get("films", {})
    factor = float(opt.get("price_factor", 1.6))
    kind = opt.get("kind")
    formula = option_kind_formula(kind)

    if formula == "lamination":
        film_a3 = float(films.get(opt["film"], {}).get("a3", 0))
        work_cost = float(works.get(opt["work"], {}).get("cost", 0))
        cost_add = (film_a3 + work_cost) / per_sheet
    elif formula == "corner":
        work_cost = float(works.get(opt["work"], {}).get("cost", 0))
        count = float(opt.get("count", 4))
        cost_add = work_cost * count
    elif formula == "crease":
        work_cost = float(works.get(opt["work"], {}).get("cost", 0))
        count = float(opt.get("count", 1))
        cost_add = work_cost * 2 * count
    elif formula == "work_unit":
        work_cost = float(works.get(opt["work"], {}).get("cost", 0))
        count = float(opt.get("count", 1))
        cost_add = work_cost * count
    elif formula == "eyelet":
        material_key = opt.get("material_work") or opt.get("material")
        material_cost = float(opt.get("material_cost", 0) or 0)
        if material_key:
            material_cost = float(works.get(material_key, {}).get("cost", 0))
        work_cost = float(works.get(opt["work"], {}).get("cost", 0))
        count = float(opt.get("count", 1))
        cost_add = (material_cost + work_cost) * count
    elif formula == "corner_cut":
        work_cost = float(works.get(opt.get("work") or "corner", {}).get("cost", 0))
        count = float(opt.get("count", 4))
        multiplier = float(opt.get("multiplier", 1.5))
        cost_add = work_cost * count * multiplier
    elif formula == "fixed_per_item":
        count = float(opt.get("count", 1))
        if kind == "extra_layout":
            count = max(0.0, count - 1.0)
        cost_add = float(opt.get("cost_add", 0)) * count
    elif formula == "foil":
        f = 1.0 if int(opt.get("print_sides", 1)) == 2 else 0.5
        settings = get_settings()
        print_1_0 = float(settings.get("print_1_0") or prod.get("print_1_0", 0))
        soft = float(films.get(opt["film"], {}).get("a3", 0))
        foil = float(films.get(opt["foil"], {}).get("a3", 0))
        print_work = float(works.get(prod["print_work_id"], {}).get("cost", 0))
        roll = float(works.get("lam_roll", {}).get("cost", 0))
        surcharge = float(opt.get("surcharge", 1.3))
        cost_add = (print_1_0 * f + soft + foil * f + print_work + roll * 2) / per_sheet * surcharge
    else:
        cost_add = float(opt.get("cost_add", 0))

    if kind == "extra_layout":
        price_add = cost_add
    else:
        price_add = cost_add * factor
    return cost_add, price_add


def options_preview(prod, size):
    """Список опций с рассчитанными прибавками для конкретного размера (для интерфейса)."""
    if prod.get("calc_type") == "notebook":
        works = get_works()
        settings = get_settings()
        mats = _notebook_material_prices(prod)
        films = prod.get("films") or {}
        fmt = size.get("format", "A4")
        res = []
        for opt in sort_product_options(prod.get("options") or []):
            kind = opt.get("kind")
            if option_kind_formula(kind) == "lamination":
                cost_add, price_add = compute_notebook_lamination(opt, prod, works, fmt)
                res.append({
                    "name": opt["name"],
                    "cost_add": round(cost_add, 4),
                    "price_add": price_add,
                })
            elif kind == "notebook_board":
                cost_add, price_add = compute_notebook_board_cover(
                    prod, works, settings, mats, films, fmt,
                )
                res.append({
                    "name": opt["name"],
                    "cost_add": round(cost_add, 4),
                    "price_add": price_add,
                })
        return res
    if prod.get("calc_type") == "board_cover":
        works = get_works()
        mats = _notebook_material_prices(prod)
        res = []
        for opt in sort_product_options(prod.get("options") or []):
            if opt.get("kind") == "board_mount":
                cost_add, price_add = compute_board_mount(opt, prod, works, mats, size.get("format", "A4"))
                res.append({
                    "name": opt["name"],
                    "cost_add": round(cost_add, 4),
                    "price_add": price_add,
                })
        return res
    if prod.get("calc_type") == "branded_cups":
        works = get_works()
        per_sheet = 1.0
        res = []
        for opt in sort_product_options(prod.get("options") or []):
            cost_add, price_add = compute_option(opt, prod, works, per_sheet)
            res.append({
                "name": opt["name"],
                "cost_add": round(cost_add, 4),
                "price_add": round(price_add, 2),
            })
        return res
    if prod.get("calc_type") == "sublimation":
        return []
    if prod.get("calc_type") == "offset_table":
        return []
    if prod.get("calc_type") == "pvd_bags":
        return []
    if prod.get("calc_type") == "paper_bags":
        return []
    if prod.get("calc_type") == "envelopes":
        works = get_works()
        per_sheet = float(size.get("print_divisor") or 1)
        kinds = get_option_kinds()
        res = []
        for opt in sort_product_options(prod.get("options") or []):
            o = dict(opt)
            if kinds.get(o.get("kind"), {}).get("ui") == "count":
                o["count"] = int(o.get("count") or 1)
            cost_add, price_add = compute_option(o, prod, works, per_sheet)
            res.append({"name": opt["name"], "cost_add": round(cost_add, 4), "price_add": round(price_add, 2)})
        return res
    if prod.get("calc_type") in ("quarterly_calendars", "wall_calendars"):
        modes = prod.get("print_modes") or [""]
        films = get_shared_films()
        works = get_works()
        # От режима печати зависит только ламинация перекидных (число страниц).
        mode_sensitive = bool(size.get("lamination_pages_per_a3"))
        res = []
        for opt in sort_product_options(prod.get("options") or []):
            if opt.get("hidden"):
                continue
            calc_modes = modes if (mode_sensitive and opt.get("film")) else modes[:1]
            cost_by_mode = {}
            price_by_mode = {}
            for mode in calc_modes:
                try:
                    cost_add, price_add = _quarterly_option_adds(
                        size, prod, opt["name"], print_mode=mode,
                        films=films, works=works,
                    )
                except ValueError:
                    continue
                cost_by_mode[mode] = cost_add
                price_by_mode[mode] = round(price_add, 2)
            if not price_by_mode:
                # Пропускаем опции, для которых нет себестоимости в этом размере
                continue
            first_mode = next(iter(price_by_mode))
            item = {
                "name": opt["name"],
                "cost_add": round(cost_by_mode[first_mode], 4),
                "price_add": price_by_mode[first_mode],
                "pending": bool(opt.get("pending")),
            }
            if len(set(price_by_mode.values())) > 1:
                item["price_add_by_mode"] = price_by_mode
            res.append(item)
        return res
    if prod.get("calc_type") == "brochure_menu":
        binding = size.get("binding", "spring")
        works = get_works()
        per_sheet = float(size["per_sheet"])
        kinds = get_option_kinds()
        res = []
        for opt in sort_product_options(prod.get("options", [])):
            if opt.get("scope") == "product":
                continue
            if not _brochure_lamination_allowed(prod, binding, opt):
                continue
            o = dict(opt)
            if kinds.get(o.get("kind"), {}).get("ui") == "count":
                o["count"] = int(o.get("count") or 1)
            cost_add, price_add = compute_option(o, prod, works, per_sheet)
            res.append({"name": opt["name"], "cost_add": round(cost_add, 4), "price_add": round(price_add, 2)})
        return res
    works = get_works()
    per_sheet = float(size["per_sheet"])
    kinds = get_option_kinds()
    res = []
    for opt in sort_product_options(prod.get("options", [])):
        o = dict(opt)
        if kinds.get(o.get("kind"), {}).get("ui") == "count":
            o["count"] = int(o.get("count") or 1)
        cost_add, price_add = compute_option(o, prod, works, per_sheet)
        res.append({"name": opt["name"], "cost_add": round(cost_add, 4), "price_add": round(price_add, 2)})
    return res


def _option_kind_ui(kind):
    return (get_option_kinds().get(kind) or {}).get("ui")


def _count_option_max(opt, qty=None):
    """Верхняя граница для опции с количеством (extra_layout — не больше тиража)."""
    if opt.get("kind") == "extra_layout" and qty is not None and qty > 0:
        return int(qty)
    return int(opt.get("count_max", 10))


def _calc_selected_options(prod, works, per_sheet, selected_opts, option_counts, excluded_opts, qty=None):
    """Считает прибавки опций: галочки/ламинация + опции с количеством (биговка).

    Возвращает (opt_details, price_add_per_item, price_add_order).
    extra_layout — фиксированная доплата к заказу, не к цене за штуку.
    """
    opt_details = []
    opts_price_add = 0.0
    opts_order_add = 0.0
    option_counts = option_counts or {}
    handled = set()

    for o in prod.get("options", []):
        if _option_kind_ui(o.get("kind")) != "count":
            continue
        cnt_max = _count_option_max(o, qty)
        cnt = int(option_counts.get(o["name"], o.get("count", 0)))
        if o.get("kind") == "extra_layout":
            if qty and cnt > qty:
                raise ValueError(
                    f"Макетов ({cnt}) не может быть больше тиража ({qty} шт)."
                )
            cnt = max(int(o.get("count_min", 1)), min(cnt_max, cnt))
            if cnt <= 1:
                continue
        else:
            cnt = max(0, min(cnt_max, cnt))
            if cnt <= 0:
                continue
        if o["name"] in excluded_opts:
            raise ValueError(f"Опция «{o['name']}» недоступна для выбранного материала.")
        _c, price_add = compute_option({**o, "count": cnt}, prod, works, per_sheet)
        if o.get("kind") == "extra_layout":
            opts_order_add += price_add
            extra = cnt - 1
            label = f"{o['name']} ×{cnt} ({extra} доп.)"
            opt_details.append({
                "name": label,
                "price_add": round(price_add, 2),
                "order_level": True,
            })
        else:
            opts_price_add += price_add
            label = o["name"] if cnt == 1 else f"{o['name']} ×{cnt}"
            opt_details.append({"name": label, "price_add": round(price_add, 2)})
        handled.add(o["name"])

    for oname in selected_opts or []:
        if oname in handled:
            continue
        if oname in excluded_opts:
            raise ValueError(f"Опция «{oname}» недоступна для выбранного материала.")
        o = next((x for x in prod.get("options", []) if x["name"] == oname), None)
        if o:
            _c, price_add = compute_option(o, prod, works, per_sheet)
            opts_price_add += price_add
            opt_details.append({"name": o["name"], "price_add": round(price_add, 2)})

    return opt_details, opts_price_add, opts_order_add


def _finalize_qty_pricing(base_price, qty, opts_per_item, opts_order, discount_pct):
    """Итог: (цена за шт × тираж) + доплаты к заказу; скидка на всю сумму."""
    qty = int(qty) or 0
    unit_base = round(base_price + opts_per_item, 2)
    subtotal = unit_base * qty + opts_order
    total = round(subtotal * (1 - discount_pct / 100.0), 2)
    unit_subtotal = round(subtotal / qty, 2) if qty else 0
    price_after_discount = round(total / qty, 2) if qty else 0
    discount_total = round(subtotal - total, 2)
    discount_per_item = round(discount_total / qty, 2) if qty else 0
    return {
        "price_per_item": unit_subtotal,
        "price_after_discount": price_after_discount,
        "options_order_add": round(opts_order, 2),
        "total": total,
        "discount_per_item": discount_per_item,
        "discount_total": discount_total,
    }


PLOTTER_TIER_KEYS = [100, 300, 500, 1000, 2000, 3000]
PLOTTER_MARGIN_SELF_ADHESIVE = 2
PLOTTER_MARGIN_THROUGH_CUT = 4


def _plotter_sheet_dims(prod):
    cfg = prod.get("sheet") or {}
    return (
        int(cfg.get("width_mm", 425)),
        int(cfg.get("height_mm", 260)),
    )


def _plotter_layout_margin(prod, material=None):
    """Припуск к ширине/высоте макета: стикеры 2 мм, магнит/картон 4 мм."""
    cfg = prod.get("sheet") or {}
    if material and material.get("calc_mode") == "through_cut":
        return int(cfg.get("margin_through_cut_mm", PLOTTER_MARGIN_THROUGH_CUT))
    return int(cfg.get("margin_self_adhesive_mm", PLOTTER_MARGIN_SELF_ADHESIVE))


def _plotter_material_by_name(prod, name):
    if not name:
        return None
    return next((m for m in resolve_product_materials(prod) if m["name"] == name), None)


def compute_plotter_per_sheet(width_mm, height_mm, sheet_w=425, sheet_h=260, margin=5):
    """Раскладка на листе — как формула Excel (строка 35), лучший поворот."""
    w, h = int(width_mm), int(height_mm)
    m = int(margin)
    if w <= 0 or h <= 0:
        raise ValueError("Размер изделия должен быть больше нуля.")
    a = (sheet_w // (w + m)) * (sheet_h // (h + m))
    b = (sheet_w // (h + m)) * (sheet_h // (w + m))
    per_sheet = max(a, b)
    if per_sheet <= 0:
        raise ValueError("Изделие не помещается на лист с учётом полей обрезки.")
    return per_sheet


def nearest_plotter_qty(tier_key, per_sheet):
    """Ближайший к tier_key тираж, кратный per_sheet (n × штук на листе)."""
    tier_key = int(tier_key)
    per_sheet = int(per_sheet)
    if per_sheet <= 0:
        raise ValueError("Некорректная раскладка.")
    max_k = max(1, (tier_key + per_sheet - 1) // per_sheet + 1)
    best_qty = per_sheet
    best_dist = abs(per_sheet - tier_key)
    for k in range(1, max_k + 2):
        qty = k * per_sheet
        dist = abs(qty - tier_key)
        if dist < best_dist or (dist == best_dist and qty < best_qty):
            best_dist = dist
            best_qty = qty
    return best_qty


def compute_plotter_qty_tiers(per_sheet, tier_keys=None):
    """Тиражи для клиента: ближайшее к стандарту (100, 300…) значение, кратное per_sheet."""
    per_sheet = int(per_sheet)
    tier_keys = tier_keys or PLOTTER_TIER_KEYS
    tiers = []
    for tier_key in tier_keys:
        qty = nearest_plotter_qty(tier_key, per_sheet)
        label = "от 2000" if tier_key == 3000 else str(qty)
        tiers.append({"qty": qty, "label": label, "tier_key": tier_key})
    return tiers


def _plotter_tier_entry(size, qty):
    """Найти строку тиража по qty из списка для размера."""
    for entry in size.get("qty_tiers") or []:
        if int(entry["qty"]) == int(qty):
            return entry
    return None


def _plotter_lamination_map(prod):
    """Справочник ламинации для стикеров под плоттер."""
    return prod.get("plotter_lamination") or prod.get("through_cut_lamination") or {}


def _plotter_lamination_a3(prod, works, lamination):
    """Доп. себестоимость листа А3 за ламинацию (плёнка + работа)."""
    if not lamination or lamination == "Без ламинации":
        return 0.0
    lam_cfg = _plotter_lamination_map(prod).get(lamination)
    if not lam_cfg:
        return 0.0
    films = prod.get("films") or {}
    film_a3 = float(films.get(lam_cfg.get("film"), {}).get("a3", 0))
    lam_work = float(works.get(lam_cfg.get("work"), {}).get("cost", 0))
    return film_a3 + lam_work


def _plotter_base_sheet(settings, works, prod, material, lamination=None):
    """Себестоимость одного листа А3: печать 4+0 + материал + работы + ламинация."""
    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0", 0))
    material_a3 = float(material["price_a3"])
    print_work = float(works.get(prod.get("print_work_id"), {}).get("cost", 0))
    sheet_work = float(works.get(prod.get("plotter_sheet_work_id"), {}).get("cost", 0))
    lam_a3 = _plotter_lamination_a3(prod, works, lamination)
    return print_4_0 + material_a3 + print_work + sheet_work + lam_a3


def _plotter_cost_per_sheet(base_sheet, per_sheet):
    """Себестоимость 1 шт: base_sheet ÷ per_sheet (единая формула для всех материалов)."""
    per_sheet = int(per_sheet)
    if per_sheet <= 0:
        raise ValueError("Некорректная раскладка.")
    return base_sheet / per_sheet


def _resolve_plotter_size(prod, payload):
    """Размер из мм в запросе → раскладка и тиражи."""
    width_mm = payload.get("width_mm")
    height_mm = payload.get("height_mm")
    if width_mm in (None, "") or height_mm in (None, ""):
        raise ValueError("Укажите размер изделия в мм.")
    width_mm, height_mm = int(width_mm), int(height_mm)
    sw, sh = _plotter_sheet_dims(prod)
    material = _plotter_material_by_name(prod, payload.get("material"))
    margin = _plotter_layout_margin(prod, material)
    per_sheet = compute_plotter_per_sheet(width_mm, height_mm, sw, sh, margin)
    qty_tiers = compute_plotter_qty_tiers(per_sheet)
    return {
        "name": f"{width_mm}×{height_mm} мм",
        "per_sheet": per_sheet,
        "qty_tiers": qty_tiers,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "layout_margin_mm": margin,
    }


def _plotter_through_cut_base(settings, works, prod, material, print_mode, lamination=None):
    """Себестоимость листа А3 для сквозной резки (магнит, картон + ламинация)."""
    print_4_0 = float(settings.get("print_4_0") or 0)
    material_a3 = float(material["price_a3"])
    print_work = float(works.get(prod.get("print_work_id"), {}).get("cost", 0))
    sheet_work = float(works.get(prod.get("plotter_sheet_work_id"), {}).get("cost", 0))
    tc = material.get("through_cut") or {}
    sides = 2 if print_mode == "4+4" else 1
    extra_work_id = tc.get("extra_work_id")
    extra_mult = float(tc.get("extra_work_mult", 0) or 0)
    extra = float(works.get(extra_work_id, {}).get("cost", 0)) * extra_mult if extra_work_id else 0.0

    if tc.get("magnetic"):
        base = print_4_0 + material_a3 + extra
    elif sides == 2:
        base = print_4_0 * 2 + material_a3
    else:
        base = print_4_0 + material_a3

    lam_cost = _plotter_lamination_a3(prod, works, lamination)

    return base + lam_cost + print_work + sheet_work * float(tc.get("sheet_work_mult", 3))


def calc_plotter_grid(product_id, prod, payload):
    """Стикеры под плоттер: base ÷ per_sheet × наценка по tier_key − скидка."""
    settings = get_settings()
    works = get_works()

    mat_name = payload.get("material")
    qty = int(payload.get("qty", 0))
    discount_pct = float(payload.get("discount", 0) or 0)

    if qty <= 0:
        raise ValueError("Укажите тираж.")

    size = _resolve_plotter_size(prod, payload)
    per_sheet = size.get("per_sheet")
    if not per_sheet:
        raise ValueError("Для выбранного размера не задана раскладка на листе.")

    prod_materials = resolve_product_materials(prod)
    material = next((m for m in prod_materials if m["name"] == mat_name), None)
    if not material:
        raise ValueError("Материал не найден.")

    tier_entry = _plotter_tier_entry(size, qty)
    if not tier_entry:
        raise ValueError("Выберите тираж из списка для этого размера.")

    tier_key = int(tier_entry["tier_key"])
    calc_mode = material.get("calc_mode", "self_adhesive")
    print_mode = payload.get("print_mode") or "4+0"
    lamination = payload.get("lamination") or "Без ламинации"

    if calc_mode == "through_cut":
        allowed = material.get("print_modes") or prod.get("print_modes") or ["4+0"]
        if print_mode not in allowed:
            raise ValueError(f"Для материала «{mat_name}» доступна печать: {', '.join(allowed)}.")
        base_sheet = _plotter_through_cut_base(
            settings, works, prod, material, print_mode, lamination
        )
    else:
        base_sheet = _plotter_base_sheet(settings, works, prod, material, lamination)

    cost = _plotter_cost_per_sheet(base_sheet, per_sheet)
    sheets = math.ceil(qty / int(per_sheet))

    print_work = float(works.get(prod.get("print_work_id"), {}).get("cost", 0))
    sheet_work = float(works.get(prod.get("plotter_sheet_work_id"), {}).get("cost", 0))
    print_4_0 = float(settings.get("print_4_0") or 0)
    lam_a3 = _plotter_lamination_a3(prod, works, lamination)
    cost_breakdown = {
        "base_sheet": round(base_sheet, 4),
        "sheets": sheets,
        "tier_key": tier_key,
        "calc_mode": calc_mode,
        "print_4_0": round(print_4_0, 4),
        "material_a3": round(float(material["price_a3"]), 4),
        "print_work": round(print_work, 4),
        "sheet_work": round(sheet_work, 4),
        "lamination": lamination,
        "lamination_a3": round(lam_a3, 4),
    }
    if calc_mode == "through_cut":
        cost_breakdown["print_mode"] = print_mode

    markup_profile = material.get("markup_profile", "default")
    markup_mult, flat_price = _resolve_markup(
        {"markup_profiles": prod.get("markup_profiles") or {}}, tier_key, markup_profile
    )
    if flat_price is not None:
        raise ValueError("Фиксированная цена для этого продукта не поддерживается.")
    markup = markup_mult
    base_price = cost * markup

    price_per_item = base_price
    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    result = {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "plotter_grid",
        "size": size["name"],
        "material": mat_name,
        "markup_profile": markup_profile,
        "per_sheet": int(per_sheet),
        "sheets": sheets,
        "tier_key": tier_key,
        "tier_label": tier_entry.get("label"),
        "qty": qty,
        "width_mm": size["width_mm"],
        "height_mm": size["height_mm"],
        "cost": round(cost, 2),
        "cost_breakdown": cost_breakdown,
        "markup": markup,
        "used_flat_price": False,
        "base_price": round(base_price, 2),
        "options": [],
        "options_price_add": 0,
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost, 2),
        "margin_total": round((price_after_discount - cost) * qty, 2),
        "lamination": lamination,
    }
    if calc_mode == "through_cut":
        result["print_mode"] = print_mode
    return result


NOTEBOOK_FORMAT_DIV = {"A3": 1, "A4": 2, "A5": 4, "A6": 8}
NOTEBOOK_BLOCK_SHEETS = {"A3": 40, "A4": 20, "A5": 10, "A6": 5}
NOTEBOOK_SPRING_MULT = {"A3": 2, "A4": 1, "A5": 1, "A6": 0.5}
NOTEBOOK_LAYOUT_MULT = {"A3": 40, "A4": 40, "A5": 20, "A6": 10}
NOTEBOOK_CUT_MULT = {"A3": 2, "A4": 2, "A5": 12, "A6": 12}
NOTEBOOK_LAM_SCALE = {"A3": 2.0, "A4": 1.0, "A5": 0.5, "A6": 0.25}
NOTEBOOK_BOARD_COEFFS = {
    "A4": {"print_mult": 2, "liner_mult": 2, "board_div": 2, "film_div": 2, "lam_work_div": 2, "finish_mult": 4},
    "A5": {"print_mult": 1, "liner_mult": 1, "board_div": 4, "film_div": 4, "lam_work_div": 4, "finish_mult": 4},
    "A6": {"print_mult": 0.5, "liner_mult": 0.5, "board_div": 8, "film_div": 8, "lam_work_div": 8, "finish_mult": 4},
}
DEFAULT_NOTEBOOK_WORK_IDS = {
    "print": "print_a3",
    "coil_install": "coil_install",
    "spiral_punch": "spiral_punch",
    "spiral_cut": "spiral_cut",
    "block_layout": "block_layout",
    "cut": "cut_item",
    "board_lam": "lam_roll",
    "board_finish": "plotter_mag_extra",
}

# --- Обложки из переплётного картона (нестандартные размеры) -----------------
# Стандартные форматы для подбора базы расчёта: (формат, ширина мм, высота мм)
BOARD_STANDARD_FORMATS = (
    ("A6", 105.0, 148.0),
    ("A5", 148.0, 210.0),
    ("A4", 210.0, 297.0),
    ("A3", 297.0, 420.0),
)
# Максимальный лист переплётного картона
BOARD_SHEET_MAX = (297.0, 420.0)


def board_cover_custom_cfg(prod):
    cfg = prod.get("custom_size")
    return cfg if isinstance(cfg, dict) else {}


def board_fits_sheet(width, height, max_width=BOARD_SHEET_MAX[0], max_height=BOARD_SHEET_MAX[1]):
    """Помещается ли деталь width×height в лист max_width×max_height (с учётом поворота)."""
    if width <= 0 or height <= 0:
        return False
    return (width <= max_width and height <= max_height) or (
        width <= max_height and height <= max_width
    )


def board_nearest_format(width, height):
    """Ближайший стандартный формат не меньше изделия (по площади)."""
    area = float(width) * float(height)
    for fmt, fw, fh in BOARD_STANDARD_FORMATS:
        if fw * fh >= area - 1e-6:
            return fmt
    return BOARD_STANDARD_FORMATS[-1][0]


def board_part_size(width, height, parts):
    """Размер одной части при делении обложки на parts частей (по большей стороне)."""
    parts = max(1, int(parts or 1))
    width = float(width)
    height = float(height)
    if parts <= 1:
        return width, height
    if width >= height:
        return float(math.ceil(width / parts)), height
    return width, float(math.ceil(height / parts))


def board_split_fits(width, height, parts, max_width=BOARD_SHEET_MAX[0], max_height=BOARD_SHEET_MAX[1]):
    """Можно ли вырезать каждую из parts частей из листа max_width×max_height.

    Возвращает (ок, ширина части, высота части). Проверяются оба варианта деления.
    """
    ok = False
    best = None
    for w, h in ((float(width), float(height)), (float(height), float(width))):
        pw, ph = board_part_size(w, h, parts)
        if board_fits_sheet(pw, ph, max_width, max_height):
            ok = True
            best = (pw, ph)
            break
        if best is None:
            best = (pw, ph)
    return ok, best


def _notebook_work_cost(works, work_ids, key):
    wid = (work_ids or DEFAULT_NOTEBOOK_WORK_IDS).get(key, DEFAULT_NOTEBOOK_WORK_IDS.get(key))
    return float(works.get(wid, {}).get("cost", 0))


def _notebook_material_prices(prod):
    """Цены материалов блокнота по ролям из material_refs."""
    by_id = materials_by_id()
    roles = {}
    for ref in prod.get("material_refs") or []:
        role = ref.get("role")
        if not role:
            label = str(ref.get("label") or "").lower()
            if "блок" in label:
                role = "block"
            elif "пружина" in label:
                role = "spring"
            elif "перепл" in label:
                role = "board"
            elif "обложка" in label or "картон" in label:
                role = "cover"
            elif "самокл" in label or "лайнер" in label:
                role = "board_liner"
        if not role:
            continue
        src = by_id.get(ref["id"])
        if src:
            roles[role] = float(src["price_a3"])
    return roles


def _notebook_side_modes(block_mode):
    """0+0 / 1+0 / 4+0 для формул; 1+1 и 4+4 считаются отдельно."""
    if block_mode in ("1+1", "1+0"):
        return "1+0"
    if block_mode in ("4+4", "4+0"):
        return "4+0"
    return "0+0"


def compute_notebook_base_cost(size, block_mode, prod, works, settings, mats=None):
    """Себестоимость блокнота на базовое число листов (40) — из справочников админки."""
    mats = mats if mats is not None else _notebook_material_prices(prod)
    work_ids = prod.get("notebook_work_ids") or DEFAULT_NOTEBOOK_WORK_IDS
    fmt = size["format"]
    cover = size["cover_print"]
    side_mode = _notebook_side_modes(block_mode)
    div = NOTEBOOK_FORMAT_DIV[fmt]
    sheets = NOTEBOOK_BLOCK_SHEETS[fmt]

    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0") or 0)
    print_1_0 = float(settings.get("print_1_0") or prod.get("print_1_0") or 0)
    cover_mat = mats.get("cover", 0)
    block_mat = mats.get("block", 0)
    spring_mat = mats.get("spring", 0)

    if cover == "4+0":
        cover_cost = (cover_mat * 2 + print_4_0) / div
    else:
        cover_cost = (cover_mat + print_4_0) / (div / 2)

    fixed = (
        _notebook_work_cost(works, work_ids, "coil_install")
        + _notebook_work_cost(works, work_ids, "spiral_punch") * 42
        + _notebook_work_cost(works, work_ids, "spiral_cut")
        + _notebook_work_cost(works, work_ids, "block_layout") * NOTEBOOK_LAYOUT_MULT[fmt]
        + _notebook_work_cost(works, work_ids, "cut") * NOTEBOOK_CUT_MULT[fmt]
        + spring_mat * NOTEBOOK_SPRING_MULT[fmt]
    )

    print_cost = _notebook_work_cost(works, work_ids, "print")
    if side_mode == "0+0":
        block = block_mat * sheets
        print_part = print_cost / div
    elif side_mode == "1+0":
        block = (block_mat + print_1_0) * sheets
        print_part = print_cost * (41 / div)
    else:
        block = (block_mat + print_4_0) * sheets
        print_part = print_cost * (41 / div)

    base = cover_cost + block + fixed + print_part
    if block_mode == "1+1":
        b0 = compute_notebook_base_cost(size, "0+0", prod, works, settings, mats)
        b1 = compute_notebook_base_cost(size, "1+0", prod, works, settings, mats)
        return round(b0 + 2 * (b1 - b0), 4)
    if block_mode == "4+4":
        b0 = compute_notebook_base_cost(size, "0+0", prod, works, settings, mats)
        b4 = compute_notebook_base_cost(size, "4+0", prod, works, settings, mats)
        return round(b0 + 2 * (b4 - b0), 4)
    return round(base, 4)


def compute_notebook_page_unit_cost(size, block_mode, prod, works, settings, mats=None):
    """Себестоимость одного дополнительного листа блока."""
    mats = mats if mats is not None else _notebook_material_prices(prod)
    work_ids = prod.get("notebook_work_ids") or DEFAULT_NOTEBOOK_WORK_IDS
    fmt = size["format"]
    side_mode = _notebook_side_modes(block_mode)
    div = NOTEBOOK_FORMAT_DIV[fmt]
    block_mat = mats.get("block", 0)
    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0") or 0)
    print_1_0 = float(settings.get("print_1_0") or prod.get("print_1_0") or 0)
    print_cost = _notebook_work_cost(works, work_ids, "print")
    cut_cost = _notebook_work_cost(works, work_ids, "cut")

    if side_mode == "0+0":
        unit = block_mat / div + cut_cost / 4
    elif side_mode == "1+0":
        unit = (block_mat + print_1_0 + print_cost) / div + cut_cost / 4
    else:
        unit = (block_mat + print_4_0 + print_cost) / div + cut_cost / 4
    return round(unit, 4)


def compute_notebook_lamination(opt, prod, works, fmt):
    """Ламинация обложки: плёнка + работа × масштаб формата."""
    films = prod.get("films") or {}
    film_a3 = float(films.get(opt.get("film"), {}).get("a3", 0))
    work_cost = float(works.get(opt.get("work"), {}).get("cost", 0))
    scale = NOTEBOOK_LAM_SCALE.get(fmt, 1.0)
    factor = float(opt.get("price_factor", 1.6))
    cost_add = (film_a3 + work_cost) * scale
    return cost_add, round(cost_add * factor, 2)


def compute_notebook_board_cover(prod, works, settings, mats, films, fmt, lamination_id="soft_touch_30"):
    """Обложка из переплётного картона — формула как в Excel."""
    work_ids = prod.get("notebook_work_ids") or DEFAULT_NOTEBOOK_WORK_IDS
    if fmt == "A3":
        base_cost, _ = compute_notebook_board_cover(
            prod, works, settings, mats, films, "A4", lamination_id,
        )
        factor = 1.6
        return round(base_cost * NOTEBOOK_LAM_SCALE["A3"], 4), round(base_cost * NOTEBOOK_LAM_SCALE["A3"] * factor, 2)

    coeffs = NOTEBOOK_BOARD_COEFFS.get(fmt)
    if not coeffs:
        return 0.0, 0.0

    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0") or 0)
    liner = mats.get("board_liner", 0)
    board = mats.get("board", 0)
    film_a3 = 0.0
    lam_work = 0.0
    if lamination_id and lamination_id != "none":
        film_a3 = float(films.get(lamination_id, {}).get("a3", 0))
        lam_work = _notebook_work_cost(works, work_ids, "board_lam")
    finish_work = _notebook_work_cost(works, work_ids, "board_finish")

    cost = (
        print_4_0 * coeffs["print_mult"]
        + liner * coeffs["liner_mult"]
        + board / coeffs["board_div"]
        + film_a3 / coeffs["film_div"]
        + lam_work / coeffs["lam_work_div"]
        + finish_work * coeffs["finish_mult"]
    )
    factor = 1.6
    return round(cost, 4), round(cost * factor, 2)


def compute_board_mount(opt, prod, works, mats, fmt="A4"):
    """Крепление обложки: кольца/болты — материал + дырокол; пружина — формула блокнотов."""
    role = opt.get("material_role")
    if not role:
        role = "rings" if "кольц" in opt.get("name", "").lower() else "bolts"
    if role == "spring":
        work_ids = prod.get("notebook_work_ids") or DEFAULT_NOTEBOOK_WORK_IDS
        spring_mat = float(mats.get("spring", 0))
        cost_add = (
            spring_mat * NOTEBOOK_SPRING_MULT.get(fmt, 1.0)
            + _notebook_work_cost(works, work_ids, "coil_install")
            + _notebook_work_cost(works, work_ids, "spiral_punch") * 42
            + _notebook_work_cost(works, work_ids, "spiral_cut")
        )
    else:
        mat_price = float(mats.get(role, 0))
        hole_cost = float(works.get("badge_hole", {}).get("cost", 0))
        cost_add = mat_price + hole_cost
    factor = float(opt.get("price_factor", 1.6))
    return round(cost_add, 4), round(cost_add * factor, 2)



def _resolve_notebook_page_count(prod, payload):
    """Количество листов в блоке (база 40); поддержка legacy extra_pages."""
    default_pages = int(prod.get("default_page_count", 40))
    min_pages = int(prod.get("min_page_count", 20))
    max_pages = int(prod.get("max_page_count", 90))
    raw = payload.get("page_count")
    if raw not in (None, ""):
        page_count = int(raw)
    elif payload.get("extra_pages") not in (None, ""):
        page_count = default_pages + int(payload.get("extra_pages") or 0)
    else:
        page_count = default_pages
    if page_count < min_pages or page_count > max_pages:
        raise ValueError(f"Количество страниц: от {min_pages} до {max_pages} листов.")
    if page_count % 10 != 0:
        raise ValueError(f"Количество страниц: кратно 10 листам (от {min_pages} до {max_pages}).")
    return page_count, default_pages



def calc_board_cover(product_id, prod, payload):
    """обложки из переплётного картона: себестоимость по формуле блокнотов x наценка тиража."""
    works = get_works()
    settings = get_settings()
    mats = _notebook_material_prices(prod)
    films = prod.get("films") or {}

    cfg = board_cover_custom_cfg(prod)
    mode = (payload.get("board_mode") or "standard").strip() or "standard"
    discount_pct = float(payload.get("discount", 0) or 0)
    option_counts = payload.get("option_counts") or {}
    lam_choices = {l["id"]: l for l in prod.get("laminations") or []}
    lam_id = payload.get("lamination") or (next(iter(lam_choices), "soft_touch_30"))
    if lam_id not in lam_choices:
        lam_id = next(iter(lam_choices), "soft_touch_30")
    lamination_label = lam_choices.get(lam_id, {}).get("label")

    custom_info = None
    parts = 1
    parts_cost = 0.0
    if mode == "custom" and cfg:
        max_width = float(cfg.get("max_width") or BOARD_SHEET_MAX[0])
        max_height = float(cfg.get("max_height") or BOARD_SHEET_MAX[1])
        min_side = float(cfg.get("min_size") or 50)
        max_parts = int(cfg.get("max_parts") or 10)
        try:
            width = float(str(payload.get("custom_width", "")).replace(",", "."))
            height = float(str(payload.get("custom_height", "")).replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError(
                "Укажите ширину и высоту обложки в разложенном виде, мм."
            )
        if width < min_side or height < min_side:
            raise ValueError("Минимальная сторона обложки — " + str(int(min_side)) + " мм.")
        if width > 4200 or height > 4200:
            raise ValueError("Проверьте размер: максимальное значение — 4200 мм.")
        raw_parts = payload.get("custom_parts")
        if raw_parts in (None, ""):
            raw_parts = cfg.get("default_parts") or 1
        try:
            parts = int(raw_parts)
        except (TypeError, ValueError):
            parts = 1
        parts = max(1, min(max_parts, parts))

        if parts <= 1:
            if not board_fits_sheet(width, height, max_width, max_height):
                raise ValueError(
                    "Размер {w:.0f}×{h:.0f} мм больше листа переплётного картона "
                    "{mw:.0f}×{mh:.0f} мм. Уменьшите размер изделия или посчитайте обложку "
                    "из нескольких отдельных частей (например, из двух), которые затем "
                    "соединяются между собой.".format(
                        w=width, h=height, mw=max_width, mh=max_height,
                    )
                )
            part_w, part_h = width, height
        else:
            split_ok, part_size = board_split_fits(
                width, height, parts, max_width, max_height
            )
            part_w, part_h = part_size or (width, height)
            if not split_ok:
                raise ValueError(
                    "При делении на {p} частей сторона части получается {pw:.0f}×{ph:.0f} мм — "
                    "это больше {mw:.0f}×{mh:.0f} мм. Увеличьте количество частей или уменьшите "
                    "размер изделия.".format(
                        p=parts, pw=part_w, ph=part_h, mw=max_width, mh=max_height,
                    )
                )
        fmt = board_nearest_format(width, height)
        base_size_name = next(
            (s["name"] for s in prod["sizes"] if s.get("format") == fmt), fmt
        )
        base_label = cfg.get("base_label") or "База расчёта"
        custom_info = {
            "width": round(width, 1),
            "height": round(height, 1),
            "parts": parts,
            "part_width": round(part_w, 1),
            "part_height": round(part_h, 1),
            "base_format": fmt,
            "base_size_name": base_size_name,
            "base_label": base_label,
            "max_width": max_width,
            "max_height": max_height,
        }
        size_label = "Нестандарт " + str(int(round(width))) + "×" + str(int(round(height))) + " мм"
        if parts > 1:
            size_label += " · " + str(parts) + " част."
        size = next((s for s in prod["sizes"] if s.get("format") == fmt), None)
        if not size:
            raise ValueError("Размер не найден.")
    else:
        mode = "standard"
        size_name = payload.get("size")
        size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
        if not size:
            raise ValueError("Размер не найден.")
        fmt = size.get("format", "A4")
        size_label = size["name"]

    raw_qty = payload.get("qty")
    if raw_qty in (None, ""):
        qty = 0
    else:
        try:
            qty = int(raw_qty)
        except (TypeError, ValueError):
            qty = 0
    qty_options = size.get("qty_options") or []
    qty_specified = qty > 0
    if qty_specified:
        if qty_options and qty not in qty_options:
            raise ValueError("Тираж не найден.")
    elif prod.get("qty_optional"):
        qty = 1
    else:
        raise ValueError("Тираж не найден.")

    cover_count = int(payload.get("board_covers") or 1)
    cover_count = 1 if cover_count < 1 else (2 if cover_count > 2 else cover_count)

    mounts = []
    for opt in prod.get("options") or []:
        if opt.get("kind") != "board_mount":
            continue
        cnt = int(option_counts.get(opt["name"], 0) or 0)
        if cnt > 0:
            mounts.append((opt, cnt))
    if len(mounts) > 1:
        raise ValueError("Выберите одно крепление: кольца или болты.")

    unit_cost, _ = compute_notebook_board_cover(prod, works, settings, mats, films, fmt, lam_id)
    if mode == "custom" and parts > 1:
        # каждая дополнительная часть (сверх первой): порезка части + поклейка на двусторонний скотч × 4
        cut_work_id = cfg.get("cut_work_id") or "cut_item"
        tape_work_id = cfg.get("tape_work_id") or "sublimation_tape_glue"
        tape_mult = float(cfg.get("tape_work_mult") or 4)
        cut_cost = float(works.get(cut_work_id, {}).get("cost", 0))
        tape_cost = float(works.get(tape_work_id, {}).get("cost", 0))
        parts_cost = round((parts - 1) * (cut_cost + tape_cost * tape_mult), 4)
    unit_cost = round(unit_cost + parts_cost, 4)
    cost = round(unit_cost * cover_count, 4)
    markup_mult, flat_price = _resolve_markup(size, qty, "default")
    if flat_price is not None:
        raise ValueError("Фиксированная цена не поддерживается.")
    base_price = cost * markup_mult

    opt_details = []
    opts_price_add = 0.0
    for opt, cnt in mounts:
        m_cost, m_price = compute_board_mount(opt, prod, works, mats, fmt)
        price_add = round(m_price * cnt, 2)
        opts_price_add += price_add
        label = opt["name"] if cnt == 1 else opt["name"] + " ×" + str(cnt)
        opt_details.append({"name": label, "price_add": price_add})

    price_per_item = base_price + opts_price_add
    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    mount_label = None
    if mounts:
        o, c = mounts[0]
        mount_label = o["name"] if c == 1 else o["name"] + " ×" + str(c)
    punch_per_sheet = None
    if mounts and mounts[0][0].get("material_role") != "spring":
        hole_cost = float(works.get("badge_hole", {}).get("cost", 0))
        punch_per_sheet = round(hole_cost * mounts[0][1], 2)

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "board_cover",
        "size": size_label,
        "board_mode": mode,
        "base_format": fmt,
        "custom_size": custom_info,
        "cover_parts_cost": round(parts_cost, 4) if parts_cost else None,
        "material": prod.get("material_label", ""),
        "board_covers": cover_count,
        "mount": mount_label,
        "punch_per_sheet": punch_per_sheet,
        "lamination": lamination_label,
        "qty": qty,
        "qty_specified": qty_specified,
        "cost": round(cost, 4),
        "markup": markup_mult,
        "used_flat_price": False,
        "base_price": round(base_price, 2),
        "options": opt_details,
        "options_price_add": round(opts_price_add, 2),
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost, 2),
        "margin_total": round((price_after_discount - cost) * qty, 2),
    }


def calc_notebook(product_id, prod, payload):
    """Расчёт блокнотов: себестоимость из справочников админки × наценка + опции."""
    works = get_works()
    settings = get_settings()
    mats = _notebook_material_prices(prod)
    size_name = payload.get("size")
    block_mode = payload.get("block_mode") or payload.get("print_mode") or "0+0"
    qty = int(payload.get("qty", 0))
    page_count, default_pages = _resolve_notebook_page_count(prod, payload)
    selected_opts = payload.get("options", []) or []
    option_counts = payload.get("option_counts") or {}
    discount_pct = float(payload.get("discount", 0) or 0)

    allowed_modes = prod.get("block_modes") or ["0+0", "1+0", "4+0"]
    if block_mode not in allowed_modes:
        raise ValueError(f"Доступная цветность блока: {', '.join(allowed_modes)}.")

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")

    validate_finish_options(prod, selected_opts)

    cost = compute_notebook_base_cost(size, block_mode, prod, works, settings, mats)
    page_delta = page_count - default_pages
    page_unit_cost = compute_notebook_page_unit_cost(size, block_mode, prod, works, settings, mats)
    adjusted_cost = cost + page_unit_cost * page_delta

    markup_mult, flat_price = _resolve_markup(size, qty, block_mode)
    if flat_price is not None:
        raise ValueError("Фиксированная цена для блокнотов не поддерживается.")
    markup = markup_mult
    base_price = adjusted_cost * markup
    used_flat_price = False

    fmt = size["format"]
    films = prod.get("films") or {}
    opt_by_name = {o["name"]: o for o in prod.get("options") or []}
    opt_details = []
    opts_price_add = 0.0

    for oname in selected_opts or []:
        o = opt_by_name.get(oname)
        if not o:
            continue
        if option_kind_formula(o.get("kind")) == "lamination":
            _c, pa = compute_notebook_lamination(o, prod, works, fmt)
            opts_price_add += pa
            opt_details.append({"name": oname, "price_add": round(pa, 2)})

    board_opt = next((o for o in prod.get("options") or [] if o.get("kind") == "notebook_board"), None)
    if board_opt:
        board_count = int(option_counts.get(board_opt["name"], 0))
        board_count = max(0, min(2, board_count))
        if board_count > 0:
            _bc, pa = compute_notebook_board_cover(prod, works, settings, mats, films, fmt)
            pa *= board_count
            opts_price_add += pa
            label = board_opt["name"] if board_count == 1 else f"{board_opt['name']} ×{board_count}"
            opt_details.append({"name": label, "price_add": round(pa, 2)})

    if page_delta != 0:
        sign = "+" if page_delta > 0 else "−"
        page_price_part = page_unit_cost * page_delta * markup
        opt_details.append({
            "name": f"Страницы блока ({page_count} л., {sign}{abs(page_delta)} к базе {default_pages})",
            "price_add": round(page_price_part, 2),
        })

    price_per_item = base_price + opts_price_add
    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    material_label = (
        f"Обложка {prod.get('cover_material', 'картон')} ({size.get('cover_print', '4+0')}), "
        f"блок {prod.get('block_spec', 'офсет')} · {page_count} л."
    )

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "notebook",
        "size": size_name,
        "material": material_label,
        "block_mode": block_mode,
        "page_count": page_count,
        "default_page_count": default_pages,
        "page_delta": page_delta,
        "print_mode": block_mode,
        "qty": qty,
        "cost": round(adjusted_cost, 2),
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": round(base_price, 2),
        "options": opt_details,
        "options_price_add": round(opts_price_add, 2),
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - adjusted_cost, 2),
        "margin_total": round((price_after_discount - adjusted_cost) * qty, 2),
    }


def _branded_cups_sticker_unit(settings, works, mats_by_id, prod):
    print_4_0 = float(settings.get("print_4_0") or 0)
    film_id = prod["sticker_film_material_id"]
    film_price = float(mats_by_id.get(film_id, {}).get("price_a3", 0))
    sheet_work = float(works.get(prod["sticker_sheet_work_id"], {}).get("cost", 0))
    print_work = float(works.get("print_a3", {}).get("cost", 0))
    return print_4_0 + film_price + sheet_work + print_work


def _branded_cups_paper_cost(prod, size_name, qty):
    """Себестоимость двухслойных бумажных стаканов с полноцветной запечаткой."""
    prices = (prod.get("paper_cup_prices") or {}).get(size_name)
    if not prices:
        raise ValueError("Цены для выбранного объёма не найдены.")
    key = str(qty)
    if key in prices:
        return float(prices[key])
    if qty in prices:
        return float(prices[qty])
    allowed = prod.get("paper_cup_qty_options") or []
    if allowed:
        raise ValueError(
            f"Для этого способа доступны тиражи: {', '.join(str(q) for q in allowed)} шт."
        )
    raise ValueError("Цена для выбранного тиража не найдена.")


def _branded_cups_base_cost(settings, works, mats_by_id, prod, size_name, app_profile):
    app_types = prod.get("application_types") or {}
    app = app_types.get(app_profile)
    if not app:
        raise ValueError("Способ нанесения не найден.")
    if app_profile == "paper_fullcolor":
        raise ValueError("Для бумажных стаканов используйте таблицу цен по тиражу.")
    if app_profile == "cardboard":
        return float(prod["cardboard_base"][size_name])
    cup_id = prod["cup_material_ids"][size_name]
    cup_price = float(mats_by_id[cup_id]["price_a3"])
    unit = _branded_cups_sticker_unit(settings, works, mats_by_id, prod)
    sticker = unit * float(app["area_pct"]) / 100.0
    print_part = float(works.get(prod["sticker_sheet_work_id"], {}).get("cost", 0)) / float(app["print_norm"])
    apply_part = float(works.get(prod["sticker_apply_work_id"], {}).get("cost", 0))
    return round(cup_price + sticker + print_part + apply_part, 6)


def _envelope_base_cost(settings, works, mats_by_id, prod, size, print_mode):
    env_id = size.get("envelope_material_id")
    if not env_id:
        env_ids = size.get("envelope_material_ids") or {}
        env_id = env_ids.get(print_mode)
    if not env_id:
        raise ValueError("Не задан конверт для расчёта.")
    env_price = float(mats_by_id[env_id]["price_a3"])
    if print_mode in (None, "без печати"):
        return round(env_price, 6)
    if print_mode == "4+0":
        print_price = float(settings.get("xerox_print_4_0") or 0)
    else:
        raise ValueError(f"Неизвестный режим печати: {print_mode}.")
    work_cost = float(works.get(prod.get("envelope_print_work_id"), {}).get("cost", 0))
    divisor = float(size.get("print_divisor") or 1)
    return round(env_price + print_price / divisor + work_cost / divisor, 6)


def _brochure_lamination_allowed(prod, binding, opt):
    if option_kind_formula(opt.get("kind")) != "lamination":
        return True
    allowed = (prod.get("lamination_by_binding") or {}).get(binding)
    if not allowed:
        return True
    return opt.get("film") in allowed


def _brochure_size_hidden(prod, size_name, binding, orientation, binding_position):
    for rule in prod.get("size_availability_rules") or []:
        when = rule.get("when") or {}
        if when.get("binding") and when["binding"] != binding:
            continue
        if when.get("orientation") and when["orientation"] != orientation:
            continue
        if when.get("binding_position") and when["binding_position"] != binding_position:
            continue
        if size_name in (rule.get("hide_size_names") or []):
            return True
    return False


def _brochure_size_allowed(prod, size, binding, orientation, binding_position):
    if _brochure_size_hidden(prod, size["name"], binding, orientation, binding_position):
        return False
    if binding == "staple" and "А3" in size["name"]:
        return False
    if binding == "spring" or size.get("ignore_orientation"):
        return size.get("binding") == binding
    return (
        size.get("binding") == binding
        and size.get("orientation") == orientation
    )


def _brochure_option_scopes(prod):
    scopes = {}
    for opt in prod.get("options") or []:
        scopes[opt["name"]] = opt.get("scope") or "section"
    return scopes


def _brochure_split_options(prod, selected_opts):
    scopes = _brochure_option_scopes(prod)
    product_opts, section_opts = [], []
    for name in selected_opts or []:
        if scopes.get(name) == "product":
            product_opts.append(name)
        else:
            section_opts.append(name)
    return section_opts, product_opts


def _brochure_size_key(size):
    if size.get("ignore_orientation"):
        return f"{size.get('binding')}|{size['name']}"
    return f"{size.get('binding')}|{size.get('orientation')}|{size['name']}"


def _brochure_find_size(prod, binding, orientation, size_name):
    matches = [
        s for s in prod["sizes"]
        if s["name"] == size_name and s.get("binding") == binding
    ]
    if not matches:
        return None
    if binding == "spring":
        return matches[0]
    for s in matches:
        if s.get("ignore_orientation") or s.get("orientation") == orientation:
            return s
    return None


def _brochure_cover_pages(print_mode):
    return 4 if print_mode in ("1+1", "4+4") else 2


def _brochure_page_unit(prod, binding, print_mode):
    return float((prod.get("page_units") or {}).get(binding, {}).get(print_mode, 1))


def _brochure_bundle_count(pages, page_unit):
    if pages <= 0:
        return 0
    mult = pages / page_unit
    if abs(mult - round(mult)) > 0.001:
        raise ValueError(
            f"Число страниц ({pages}) должно быть кратно {int(page_unit)} для выбранной цветности."
        )
    return int(round(mult))


def _brochure_staple_a4_body_ref(prod):
    """Эталон А4 скрепка, офсет 1+0 — строка 12 колонка G (масштаб формата)."""
    for s in prod.get("sizes") or []:
        if s.get("binding") == "staple" and str(s.get("name", "")).startswith("А4"):
            return float(s["body_ref_cost"])
    return 9.693333


def _brochure_paper_a3(material, settings, works, prod):
    """Цена материала за А3 из каталога materials.json (без печати и работ)."""
    return float(material["price_a3"])


def _brochure_spring_row12_ref(prod):
    """Эталон строки 12 листа пружины (офсет 1+0, полный блок А3/А4)."""
    for s in prod.get("sizes") or []:
        if s.get("binding") == "spring" and str(s.get("name", "")).startswith("А3"):
            return float(s["body_ref_cost"])
    return 9.693333


def _brochure_excel_raw_unit_a4(material, print_mode, settings, works, prod):
    """Себестоимость 1 единицы в масштабе строки 12 Excel (до деления на формат)."""
    price_a3 = _brochure_paper_a3(material, settings, works, prod)
    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0", 0))
    print_1_0 = float(settings.get("print_1_0") or prod.get("print_1_0", 0))
    print_work = float(works.get(prod.get("print_work_id", "print_a3"), {}).get("cost", 0))
    cut = float(works.get(prod.get("cut_work_id", "cut_item"), {}).get("cost", 0))

    if print_mode in ("1+0", "4+0"):
        print_unit = print_1_0 if print_mode == "1+0" else print_4_0
        return price_a3 + print_unit + print_work + cut
    if print_mode in ("1+1", "4+4"):
        print_unit = print_1_0 if print_mode == "1+1" else print_4_0
        return price_a3 + print_unit * 2 + print_work * 2 + cut
    raise ValueError(f"Неизвестная цветность: {print_mode}")


def _brochure_excel_scaled_unit_seb(material, print_mode, size, settings, works, prod, row_ref):
    """Себестоимость 1 печатной единицы: строка 12 ÷ (row_ref / body_ref_формата)."""
    raw = _brochure_excel_raw_unit_a4(material, print_mode, settings, works, prod)
    size_ref = float(size.get("body_ref_cost") or row_ref)
    scale = row_ref / size_ref if size_ref else 1.0
    return round(raw / scale, 6)


def _brochure_staple_excel_unit_seb(material, print_mode, size, settings, works, prod):
    """Себестоимость 1 печатной единицы скрепки — как строка 12/59 Excel.

    В прайсе в G10/J10… выпадающий список 1+0↔4+0 и 1+1↔4+4; формула
    IF(G10=\"1+0\", print_1_0, print_4_0) подставляет нужную печать.
    """
    return _brochure_excel_scaled_unit_seb(
        material, print_mode, size, settings, works, prod,
        _brochure_staple_a4_body_ref(prod),
    )


def _brochure_spring_excel_unit_seb(material, print_mode, size, settings, works, prod):
    """Себестоимость 1 печатной единицы пружины — строка 12/68 Excel (÷6 для Евро и т.д.)."""
    return _brochure_excel_scaled_unit_seb(
        material, print_mode, size, settings, works, prod,
        _brochure_spring_row12_ref(prod),
    )


def _brochure_unit_seb(material, size, offset_ref_seb):
    body_ref = float(size.get("body_ref_cost") or 0)
    mat_seb = float(material.get("brochure_ref_seb") or 0)
    factor = mat_seb / offset_ref_seb if offset_ref_seb else 1.0
    return body_ref * factor


def _brochure_mat_ref_seb(material, print_mode, settings, works, prod):
    """Себестоимость 1 печатной стр. как в строке 12 Excel для выбранной цветности."""
    ref_seb = float(material.get("brochure_ref_seb") or 0)
    default_print = material.get("brochure_default_print") or "4+0"
    if print_mode == default_print:
        return ref_seb
    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0", 0))
    print_1_0 = float(settings.get("print_1_0") or prod.get("print_1_0", 0))

    def _print_part(mode):
        if mode == "4+4":
            return print_4_0 * 2
        if mode == "4+0":
            return print_4_0
        if mode == "1+1":
            return print_1_0 * 2
        return print_1_0

    return round(ref_seb + _print_part(print_mode) - _print_part(default_print), 6)


def _brochure_offset_material(prod):
    for mat in resolve_product_materials(prod):
        if mat.get("brochure_ref_seb") is not None and "офсет" in mat["name"].lower():
            return mat
    mats = resolve_product_materials(prod)
    return mats[0] if mats else None


def _brochure_section_unit_seb(
    material, size, print_mode, prod, settings, works, offset_ref_seb,
):
    """Себестоимость 1 печатной стр. для выбранной цветности."""
    if size.get("binding") == "staple":
        return _brochure_staple_excel_unit_seb(
            material, print_mode, size, settings, works, prod,
        )
    if size.get("binding") == "spring":
        return _brochure_spring_excel_unit_seb(
            material, print_mode, size, settings, works, prod,
        )
    body_ref = float(size.get("body_ref_cost") or 0)
    mat_seb = _brochure_mat_ref_seb(material, print_mode, settings, works, prod)
    offset_mat = _brochure_offset_material(prod)
    offset_ref = float(
        (offset_mat or {}).get("brochure_ref_seb") or offset_ref_seb or 1.0,
    )
    factor = mat_seb / offset_ref if offset_ref else 1.0
    return body_ref * factor


def _brochure_section_body_contribution(
    material, size, print_mode, mult, prod, settings, works, offset_ref_seb,
):
    if mult <= 0:
        return 0.0
    unit = _brochure_section_unit_seb(
        material, size, print_mode, prod, settings, works, offset_ref_seb,
    )
    return unit * mult


def _brochure_offset_ref_seb(prod):
    for mat in resolve_product_materials(prod):
        if mat.get("brochure_ref_seb") is not None and "офсет" in mat["name"].lower():
            return float(mat["brochure_ref_seb"])
    mats = resolve_product_materials(prod)
    if mats and mats[0].get("brochure_ref_seb") is not None:
        return float(mats[0]["brochure_ref_seb"])
    return 1.0


def _brochure_binding_retail(prod, binding_cost):
    factor = float(prod.get("brochure_binding_factor") or 1.6)
    return round(float(binding_cost or 0) * factor, 2)


def calc_brochure_menu(product_id, prod, payload):
    """Брошюры и меню: обложка + блок страниц × множитель + крепление + опции."""
    works = get_works()
    settings = get_settings()
    binding = payload.get("binding", "spring")
    orientation = payload.get("orientation", "vertical")
    binding_position = payload.get("binding_position", "left")
    size_name = payload.get("size")
    qty = int(payload.get("qty", 0))
    discount_pct = float(payload.get("discount", 0) or 0)
    page_count = int(payload.get("page_count", prod.get("default_page_count", 8)))
    min_p = int(prod.get("min_page_count", 4))
    max_p = int(prod.get("max_page_count", 40))
    if page_count < min_p or page_count > max_p:
        raise ValueError(f"Страниц всего: от {min_p} до {max_p}.")

    if binding == "staple" and size_name and "А3" in size_name:
        raise ValueError("Формат А3 недоступен для скрепки.")

    if _brochure_size_hidden(prod, size_name, binding, orientation, binding_position):
        raise ValueError("Выбранный формат недоступен для этих параметров крепления.")

    size = _brochure_find_size(prod, binding, orientation, size_name)
    if not size or not _brochure_size_allowed(prod, size, binding, orientation, binding_position):
        raise ValueError("Размер не найден для выбранного переплёта и ориентации.")

    cover_payload = payload.get("cover") or {}
    pages_payload = payload.get("pages") or {}
    cover_print = cover_payload.get("print_mode", "4+0")
    pages_print = pages_payload.get("print_mode", "4+0")
    allowed = prod.get("print_modes") or ["1+0", "1+1", "4+0", "4+4"]
    if cover_print not in allowed or pages_print not in allowed:
        raise ValueError(f"Доступная печать: {', '.join(allowed)}.")

    cover_pages = _brochure_cover_pages(cover_print)
    if page_count < cover_pages:
        raise ValueError(f"Минимум {cover_pages} стр. (обложка при печати {cover_print}).")
    block_pages = page_count - cover_pages

    cover_mult = _brochure_bundle_count(
        cover_pages, _brochure_page_unit(prod, binding, cover_print),
    )
    pages_mult = _brochure_bundle_count(
        block_pages, _brochure_page_unit(prod, binding, pages_print),
    ) if block_pages > 0 else 0

    prod_materials = resolve_product_materials(prod)
    cover_mat = next((m for m in prod_materials if m["name"] == cover_payload.get("material")), None)
    pages_mat = next((m for m in prod_materials if m["name"] == pages_payload.get("material")), None)
    if not cover_mat or not pages_mat:
        raise ValueError("Материал не найден.")

    offset_ref_seb = _brochure_offset_ref_seb(prod)
    cover_seb = round(
        _brochure_section_body_contribution(
            cover_mat, size, cover_print, cover_mult, prod, settings, works, offset_ref_seb,
        ),
        4,
    )
    pages_seb = round(
        _brochure_section_body_contribution(
            pages_mat, size, pages_print, pages_mult, prod, settings, works, offset_ref_seb,
        ),
        4,
    )
    body_seb = round(cover_seb + pages_seb, 4)
    page_billing_units = cover_mult + pages_mult
    binding_cost = float(size.get("binding_cost") or 0)
    binding_retail = _brochure_binding_retail(prod, binding_cost)
    cost = round(body_seb + binding_cost, 4)

    markup_mult, flat_price = _resolve_markup(size, qty, "default")
    if flat_price is not None:
        cover_retail = round(flat_price * cover_mult, 2)
        pages_retail = round(flat_price * pages_mult, 2)
        body_retail = round(cover_retail + pages_retail, 2)
        markup = round(flat_price / _brochure_unit_seb(cover_mat, size, offset_ref_seb), 4) if cover_mat else 1.0
        base_price = round(body_retail + binding_retail, 2)
        used_flat_price = True
    else:
        markup = markup_mult
        cover_retail = round(cover_seb * markup, 2)
        pages_retail = round(pages_seb * markup, 2)
        body_retail = round(cover_retail + pages_retail, 2)
        unit_retail = round(body_retail / page_billing_units, 4) if page_billing_units else 0
        base_price = round(body_retail + binding_retail, 2)
        used_flat_price = False

    per_sheet = float(size["per_sheet"])
    product_opts_raw = payload.get("product_options") or []
    cover_opts_raw = cover_payload.get("options") or []
    pages_opts_raw = pages_payload.get("options") or []
    cover_counts = cover_payload.get("option_counts") or {}
    pages_counts = pages_payload.get("option_counts") or {}

    cover_section_opts, _ = _brochure_split_options(prod, cover_opts_raw)
    pages_section_opts, _ = _brochure_split_options(prod, pages_opts_raw)
    legacy_product = []
    for name in cover_opts_raw + pages_opts_raw:
        if _brochure_option_scopes(prod).get(name) == "product" and name not in legacy_product:
            legacy_product.append(name)
    product_opts = list(dict.fromkeys(list(product_opts_raw) + legacy_product))

    validate_finish_options(prod, cover_section_opts)
    validate_finish_options(prod, pages_section_opts)
    validate_finish_options(prod, product_opts)

    cover_excluded = set(cover_mat.get("excluded_options") or [])
    pages_excluded = set(pages_mat.get("excluded_options") or [])

    cover_details, cover_pi, cover_ord = _calc_selected_options(
        prod, works, per_sheet, cover_section_opts, cover_counts, cover_excluded, qty=qty,
    )
    pages_details, pages_pi, pages_ord = _calc_selected_options(
        prod, works, per_sheet, pages_section_opts, pages_counts, pages_excluded, qty=qty,
    )
    product_details, product_pi, product_ord = _calc_selected_options(
        prod, works, per_sheet, product_opts, {}, set(), qty=qty,
    )
    opts_per_item = round(cover_pi * cover_mult + pages_pi * pages_mult + product_pi, 2)
    opts_order = round(cover_ord + pages_ord + product_ord, 2)
    opt_details = []
    for o in cover_details:
        opt_details.append({**o, "name": f"Обложка: {o['name']}"})
    for o in pages_details:
        opt_details.append({**o, "name": f"Страницы: {o['name']}"})
    opt_details.extend(product_details)

    pricing = _finalize_qty_pricing(base_price, qty, opts_per_item, opts_order, discount_pct)
    price_per_item = pricing["price_per_item"]
    price_after_discount = pricing["price_after_discount"]
    total = pricing["total"]
    opts_price_add = opts_per_item

    binding_label = next(
        (b["label"] for b in prod.get("binding_types", []) if b["id"] == binding),
        binding,
    )
    binding_position_label = next(
        (b["label"] for b in prod.get("binding_positions", []) if b["id"] == binding_position),
        binding_position,
    )

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "brochure_menu",
        "binding": binding,
        "binding_label": binding_label,
        "binding_position": binding_position,
        "binding_position_label": binding_position_label,
        "orientation": orientation,
        "size": size_name,
        "page_count": page_count,
        "cover_pages": cover_pages,
        "block_pages": block_pages,
        "cover_print": cover_print,
        "pages_print": pages_print,
        "cover_material": cover_mat["name"],
        "pages_material": pages_mat["name"],
        "qty": qty,
        "page_billing_units": page_billing_units,
        "cover_mult": cover_mult,
        "pages_mult": pages_mult,
        "cover_seb": cover_seb,
        "pages_seb": pages_seb,
        "body_seb": round(body_seb, 4),
        "cover_retail": cover_retail,
        "pages_retail": pages_retail,
        "body_retail": round(body_retail, 2),
        "binding_cost": round(binding_cost, 2),
        "binding_retail": binding_retail,
        "staple_count": int(size.get("staple_count") or 0) or None,
        "unit_retail": round(body_retail / page_billing_units, 2) if page_billing_units else None,
        "cost": round(cost, 2),
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": base_price,
        "options": opt_details,
        "options_price_add": round(opts_per_item, 2),
        "options_order_add": pricing["options_order_add"],
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": pricing["discount_per_item"],
        "discount_total": pricing["discount_total"],
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost, 2),
        "margin_total": round(total - cost * qty, 2),
    }


def calc_envelopes(product_id, prod, payload):
    """Расчёт конвертов: заготовка + печать на ксероксе/divisor + работа/divisor × наценка."""
    works = get_works()
    settings = get_settings()
    mats_by_id = materials_by_id()

    size_name = payload.get("size")
    mat_name = payload.get("material")
    qty = int(payload.get("qty", 0))
    discount_pct = float(payload.get("discount", 0) or 0)
    option_counts = payload.get("option_counts") or {}

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")

    prod_materials = resolve_product_materials(prod)
    material = next((m for m in prod_materials if m["name"] == mat_name), None)
    if not material:
        raise ValueError("Материал не найден.")

    allowed = size.get("print_modes") or prod.get("print_modes") or ["4+0", "без печати"]
    default_mode = allowed[0] if len(allowed) == 1 else "4+0"
    print_mode = payload.get("print_mode", default_mode)
    if print_mode not in allowed:
        raise ValueError(f"Доступная печать: {', '.join(allowed)}.")
    markup_profile = "blank" if print_mode == "без печати" else "4+0"

    cost = _envelope_base_cost(settings, works, mats_by_id, prod, size, print_mode)
    markup_mult, flat_price = _resolve_markup(size, qty, markup_profile)
    if flat_price is not None:
        base_price = flat_price
        markup = round(flat_price / cost, 4) if cost else 1.0
        used_flat_price = True
    else:
        markup = markup_mult
        base_price = round(cost * markup, 2)
        used_flat_price = False

    per_sheet = float(size.get("print_divisor") or 1)
    opt_details, opts_per_item, opts_order = _calc_selected_options(
        prod, works, per_sheet, [], option_counts, set(), qty=qty
    )

    pricing = _finalize_qty_pricing(base_price, qty, opts_per_item, opts_order, discount_pct)
    price_per_item = pricing["price_per_item"]
    price_after_discount = pricing["price_after_discount"]
    total = pricing["total"]

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "envelopes",
        "size": size_name,
        "material": mat_name,
        "markup_profile": markup_profile,
        "print_mode": print_mode,
        "qty": qty,
        "cost": round(cost, 2),
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": base_price,
        "options": opt_details,
        "options_price_add": round(opts_per_item, 2),
        "options_order_add": pricing["options_order_add"],
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": pricing["discount_per_item"],
        "discount_total": pricing["discount_total"],
        "price_after_discount": price_after_discount,
        "total": total,
        "margin_per_item": round(price_after_discount - cost, 2),
        "margin_total": round(total - cost * qty, 2),
    }


def _sublimation_base_cost(mats_by_id, works, prod, variant_name):
    """Себестоимость сублимации: бланк + расходники + работа (+ плакетка/скотч для металла)."""
    variant = (prod.get("variants") or {}).get(variant_name)
    if not variant:
        raise ValueError("Изделие не найдено.")
    blank_id = variant["blank_material_id"]
    if blank_id not in mats_by_id:
        raise ValueError("Материал бланка не найден в каталоге.")
    blank = float(mats_by_id[blank_id]["price_a3"])
    divisor = float(variant.get("blank_divisor") or 1)
    blank_part = blank / divisor

    consumables = prod.get("consumable_material_ids") or {}
    ink_id = consumables.get("ink")
    paper_id = consumables.get("paper")
    for mid in (ink_id, paper_id):
        if not mid or mid not in mats_by_id:
            raise ValueError("Расходники сублимации не найдены в каталоге.")
    ink = float(mats_by_id[ink_id]["price_a3"])
    paper = float(mats_by_id[paper_id]["price_a3"])

    work_id = variant.get("work_id")
    if not work_id or work_id not in works:
        raise ValueError("Работа сублимации не найдена.")
    work_cost = float(works[work_id]["cost"])

    extra = 0.0
    extra_blank_id = variant.get("extra_blank_material_id")
    if extra_blank_id:
        if extra_blank_id not in mats_by_id:
            raise ValueError("Дополнительный материал не найден в каталоге.")
        extra += float(mats_by_id[extra_blank_id]["price_a3"])
    extra_work_id = variant.get("extra_work_id")
    if extra_work_id:
        if extra_work_id not in works:
            raise ValueError("Дополнительная работа не найдена.")
        extra += float(works[extra_work_id]["cost"])

    return round(blank_part + ink + paper + work_cost + extra, 6)


def calc_sublimation(product_id, prod, payload):
    """Расчёт сублимации: бланк + расходники + работа × наценка по тиражу."""
    works = get_works()
    mats_by_id = materials_by_id()

    size_name = payload.get("size")
    variant_name = payload.get("material")
    qty = int(payload.get("qty", 0))
    discount_pct = float(payload.get("discount", 0) or 0)
    qty_min = int(prod.get("qty_min") or 1)
    qty_max = int(prod.get("qty_max") or 1000)
    if qty < qty_min or qty > qty_max:
        raise ValueError(f"Тираж должен быть от {qty_min} до {qty_max} шт.")

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")
    variant = (prod.get("variants") or {}).get(variant_name)
    if not variant:
        raise ValueError("Изделие не найдено.")

    cost = _sublimation_base_cost(mats_by_id, works, prod, variant_name)
    markup_profile = variant.get("markup_profile", "white_mugs")
    prof = _markup_profile_data(size, markup_profile)
    markup_ranges = prof.get("markup_ranges") or []
    if markup_ranges and not any(mr["min"] <= qty <= mr["max"] for mr in markup_ranges):
        raise ValueError(f"Тираж должен быть от {qty_min} до {qty_max} шт.")
    markup_mult, flat_price = _resolve_markup(size, qty, markup_profile)
    if flat_price is not None:
        base_price = flat_price
        markup = round(flat_price / cost, 4) if cost else 1.0
        used_flat_price = True
    else:
        markup = markup_mult
        base_price = round(cost * markup, 2)
        used_flat_price = False

    price_per_item = base_price
    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "sublimation",
        "size": size_name,
        "material": variant_name,
        "markup_profile": markup_profile,
        "qty": qty,
        "cost": round(cost, 2),
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": base_price,
        "options": [],
        "options_price_add": 0.0,
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost, 2),
        "margin_total": round((price_after_discount - cost) * qty, 2),
    }


def _offset_material(prod, material_key):
    for m in prod.get("materials") or []:
        if m.get("id") == material_key or m.get("name") == material_key:
            return m
    return None


def _offset_size(prod, size_key):
    for s in prod.get("sizes") or []:
        if s.get("name") == size_key or s.get("id") == size_key:
            return s
    return None


def _offset_material_options(prod, size_id, material_id):
    cfg = (prod.get("size_material_config") or {}).get(size_id, {}).get(material_id)
    if cfg:
        return cfg.get("qty_options") or [], cfg.get("delivery_tiers") or []
    mat = _offset_material(prod, material_id)
    if not mat:
        return [], []
    qty_options = mat.get("qty_options") or prod.get("qty_options") or []
    delivery_tiers = mat.get("delivery_tiers") or prod.get("delivery_tiers") or []
    return qty_options, delivery_tiers


def calc_offset_table(product_id, prod, payload):
    """Офсет: цена из таблицы (себестоимость тиража) × price_factor."""
    size_key = payload.get("size")
    material_key = payload.get("material")
    print_mode = payload.get("print_mode")
    qty = int(payload.get("qty", 0))
    delivery_tier = payload.get("delivery_tier")
    lamination_id = payload.get("lamination") or "none"
    discount_pct = float(payload.get("discount", 0) or 0)

    size = _offset_size(prod, size_key)
    if not size:
        raise ValueError("Размер не найден.")
    size_id = size.get("id") or size["name"]

    mat = _offset_material(prod, material_key)
    if not mat:
        raise ValueError("Материал не найден.")
    material_id = mat["id"]

    if print_mode not in (prod.get("print_modes") or []):
        raise ValueError("Цветность не найдена.")
    lamination_modes = prod.get("lamination_modes") or []
    if lamination_modes:
        allowed_lam = {m["id"] for m in lamination_modes}
        if lamination_id not in allowed_lam:
            raise ValueError("Ламинация не найдена.")
    qty_options, delivery_tiers = _offset_material_options(prod, size_id, material_id)
    if qty not in qty_options:
        raise ValueError("Тираж не найден.")
    tier_ids = {t["id"] for t in delivery_tiers}
    if delivery_tier not in tier_ids:
        raise ValueError("Срок изготовления не найден.")

    cost_total = lookup_offset_cost(
        prod, size_id, material_id, print_mode, qty, delivery_tier,
        lamination_id=lamination_id if lamination_modes else None,
    )
    factor = float(prod.get("price_factor", DEFAULT_PRICE_FACTOR))
    cost_per_item = cost_total / qty if qty else 0.0
    price_total = round(cost_total * factor, 2)
    price_per_item = round(price_total / qty, 2) if qty else 0.0

    tiers_ui = {
        t["id"]: t
        for t in enrich_delivery_tiers({**prod, "delivery_tiers": delivery_tiers})
    }
    tier_info = tiers_ui.get(delivery_tier, {})
    print_labels = prod.get("print_mode_labels") or {}
    lam_labels = {m["id"]: m["name"] for m in lamination_modes}

    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "offset_table",
        "size": size["name"],
        "material": mat["name"],
        "material_id": material_id,
        "print_mode": print_mode,
        "print_mode_label": print_labels.get(print_mode, print_mode),
        "lamination": lamination_id if lamination_modes else None,
        "lamination_label": lam_labels.get(lamination_id) if lamination_modes else None,
        "delivery_tier": delivery_tier,
        "delivery_label": tier_info.get("display", ""),
        "working_days": tier_info.get("working_days"),
        "qty": qty,
        "cost_total": round(cost_total, 2),
        "cost": round(cost_per_item, 4),
        "markup": factor,
        "used_flat_price": True,
        "base_price": price_per_item,
        "options": [],
        "options_price_add": 0.0,
        "price_per_item": price_per_item,
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost_per_item, 2),
        "margin_total": round((price_after_discount - cost_per_item) * qty, 2),
    }


def _pvd_lookup_cost(prod, size_id, material_id, print_mode, bill_qty):
    """Себестоимость за штуку из таблицы прайса ПВД."""
    prices = prod.get("prices") or {}
    size_prices = prices.get(size_id)
    if not size_prices:
        raise ValueError("Цена для выбранного размера не найдена.")
    mat_prices = size_prices.get(material_id)
    if not mat_prices:
        raise ValueError("Цена для выбранного материала не найдена.")
    mode_prices = mat_prices.get(print_mode)
    if not mode_prices:
        raise ValueError("Цена для выбранной цветности не найдена.")
    key = str(bill_qty)
    if key not in mode_prices:
        raise ValueError("Цена для выбранного тиража не найдена.")
    return float(mode_prices[key])


def calc_pvd_bags(product_id, prod, payload):
    """Пакеты ПВД: себестоимость из прайса × price_factor."""
    size_name = payload.get("size")
    material_key = payload.get("material")
    print_mode = payload.get("print_mode")
    qty = int(payload.get("qty", 0))
    discount_pct = float(payload.get("discount", 0) or 0)

    if print_mode == "3+0":
        raise ValueError("Печать 3+0 — цена по макету. Уточните у менеджера.")

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")
    size_id = size.get("id") or size["name"]

    mat = next(
        (
            m for m in prod.get("materials") or []
            if m.get("id") == material_key or m.get("name") == material_key
        ),
        None,
    )
    if not mat:
        raise ValueError("Материал не найден.")
    material_id = mat["id"]

    qty_options = size.get("qty_options") or prod.get("qty_options") or []
    if qty not in qty_options:
        raise ValueError("Тираж не найден.")

    if print_mode not in (prod.get("print_modes") or []):
        raise ValueError("Цветность не найдена.")

    cost_per_item = _pvd_lookup_cost(prod, size_id, material_id, print_mode, qty)
    factor = float(prod.get("price_factor", DEFAULT_PRICE_FACTOR))
    price_per_item = round(cost_per_item * factor, 2)
    print_labels = prod.get("print_mode_labels") or {}

    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "pvd_bags",
        "size": size["name"],
        "material": mat["name"],
        "material_id": material_id,
        "print_mode": print_mode,
        "print_mode_label": print_labels.get(print_mode, print_mode),
        "qty": qty,
        "cost": round(cost_per_item, 4),
        "markup": factor,
        "used_flat_price": True,
        "base_price": price_per_item,
        "options": [],
        "options_price_add": 0.0,
        "price_per_item": price_per_item,
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost_per_item, 2),
        "margin_total": round((price_after_discount - cost_per_item) * qty, 2),
    }


def _paper_bags_delivery_label(delivery):
    wd = (delivery or {}).get("working_days")
    if wd is not None:
        return f"{wd} раб. дн."
    return (delivery or {}).get("display") or (delivery or {}).get("label") or ""


def _paper_bags_lookup_total(prod, size_id, qty):
    prices = (prod.get("prices") or {}).get(size_id)
    if not prices:
        raise ValueError("Цена для выбранного размера не найдена.")
    key = str(qty)
    if key not in prices:
        raise ValueError("Цена для выбранного тиража не найдена.")
    return float(prices[key])


def _validate_paper_bag_options(prod, bag_options):
    bag_options = bag_options or {}
    labels = {}
    for group in prod.get("mandatory_options") or []:
        gid = group["id"]
        val = (bag_options.get(gid) or "").strip()
        allowed = {c["id"] for c in group.get("choices") or []}
        if val not in allowed:
            raise ValueError(f"Выберите: {group.get('label', gid)}")
        choice = next(c for c in group["choices"] if c["id"] == val)
        labels[gid] = choice["name"]
    return labels


def calc_paper_bags(product_id, prod, payload):
    """Бумажные пакеты: итог за тираж из таблицы × price_factor + обязательные опции без наценки."""
    size_name = payload.get("size")
    qty = int(payload.get("qty") or 0)
    discount_pct = float(payload.get("discount") or 0)
    bag_options = payload.get("bag_options") or {}

    size = next((s for s in prod.get("sizes") or [] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")
    size_id = size.get("id") or size["name"]

    qty_options = size.get("qty_options") or []
    if qty not in qty_options:
        raise ValueError("Тираж не найден.")

    option_labels = _validate_paper_bag_options(prod, bag_options)
    order_total = _paper_bags_lookup_total(prod, size_id, qty)
    cost_per_item = order_total / qty if qty else order_total
    factor = float(prod.get("price_factor", DEFAULT_PRICE_FACTOR))
    price_per_item = round(cost_per_item * factor, 2)
    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    delivery = prod.get("delivery") or {}
    options_detail = [
        {
            "name": f"{g['label']}: {option_labels[g['id']]}",
            "cost_add": 0,
            "price_add": 0,
        }
        for g in (prod.get("mandatory_options") or [])
    ]

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "paper_bags",
        "size": size["name"],
        "qty": qty,
        "order_total": round(order_total, 2),
        "delivery_label": _paper_bags_delivery_label(delivery),
        "working_days": delivery.get("working_days"),
        "bag_options": option_labels,
        "lamination_label": option_labels.get("lamination"),
        "eyelets_label": option_labels.get("eyelets"),
        "cord_label": option_labels.get("cord"),
        "cost": round(cost_per_item, 4),
        "markup": factor,
        "used_flat_price": True,
        "base_price": price_per_item,
        "options": options_detail,
        "options_price_add": 0.0,
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost_per_item, 2),
        "margin_total": round((price_after_discount - cost_per_item) * qty, 2),
    }


def calc_branded_cups(product_id, prod, payload):
    """Расчёт брендированных стаканчиков: себестоимость из справочников × наценка по тиражу."""
    works = get_works()
    settings = get_settings()
    mats_by_id = materials_by_id()

    size_name = payload.get("size")
    mat_name = payload.get("material")
    qty = int(payload.get("qty", 0))
    discount_pct = float(payload.get("discount", 0) or 0)

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")

    prod_materials = prod.get("application_options") or []
    application = next((o for o in prod_materials if o["label"] == mat_name), None)
    if not application:
        raise ValueError("Способ нанесения не найден.")

    app_profile = application.get("profile", "default")
    qty_min = application.get("qty_min")
    if qty_min and qty < qty_min:
        raise ValueError(f"Для «{mat_name}» минимальный тираж {qty_min} шт.")

    if app_profile == "paper_fullcolor":
        cost = _branded_cups_paper_cost(prod, size_name, qty)
        factor = float(prod.get("paper_cup_price_factor", 1.6))
        base_price = round(cost * factor, 2)
        markup = factor
        used_flat_price = False
    else:
        cost = _branded_cups_base_cost(settings, works, mats_by_id, prod, size_name, app_profile)

        if app_profile == "cardboard" and qty == 5000:
            factor = float(prod.get("cardboard_qty5000_factor", 1.6))
            base_price = round(cost * factor, 2)
            markup = round(base_price / cost, 4) if cost else factor
            used_flat_price = False
        else:
            markup_mult, flat_price = _resolve_markup(size, qty, "default")
            if flat_price is not None:
                base_price = flat_price
                markup = round(flat_price / cost, 4) if cost else 1.0
                used_flat_price = True
            else:
                markup = markup_mult
                base_price = round(cost * markup, 2)
                used_flat_price = False

    selected_opts = payload.get("options", []) or []
    validate_option_exclusions(prod, selected_opts)
    opt_details, opts_per_item, opts_order = _calc_selected_options(
        prod, works, 1.0, selected_opts, {}, set(), qty=qty,
    )
    opts_cost_add = 0.0
    for oname in selected_opts:
        o = next((x for x in prod.get("options", []) if x["name"] == oname), None)
        if o:
            c, _p = compute_option(o, prod, works, 1.0)
            opts_cost_add += c

    pricing = _finalize_qty_pricing(base_price, qty, opts_per_item, opts_order, discount_pct)
    price_per_item = pricing["price_per_item"]
    price_after_discount = pricing["price_after_discount"]
    total = pricing["total"]
    unit_cost = round(cost + opts_cost_add, 4)

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "branded_cups",
        "size": size_name,
        "material": mat_name,
        "markup_profile": app_profile,
        "qty": qty,
        "cost": round(cost, 2),
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": base_price,
        "options": opt_details,
        "options_price_add": round(opts_per_item, 2),
        "options_order_add": pricing["options_order_add"],
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": pricing["discount_per_item"],
        "discount_total": pricing["discount_total"],
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - unit_cost, 2),
        "margin_total": round(total - unit_cost * qty, 2),
    }


def _flip_cursor_cost_add():
    """Себестоимость курсора перекидного календаря: курсор + одевание."""
    mats = materials_by_id()
    works = get_works()
    cursor_id = "mat_Курсор"
    work_key = "quarterly_tape_paste"
    if cursor_id not in mats:
        raise ValueError(f"Материал «{cursor_id}» не найден в каталоге.")
    if work_key not in works:
        raise ValueError("Работа «Одевание курсора» не найдена.")
    cursor = float(mats[cursor_id]["price_a3"])
    dress = float(works[work_key]["cost"])
    return round(cursor + dress, 6)


def _quarterly_magnetic_cursor_cost_add(size):
    """Себестоимость магнитного курсора: курсор + феррошит (по размеру) + поклейка."""
    mats = materials_by_id()
    works = get_works()
    cursor_id = "mat_Магнитный_курсор"
    ferro_mini_id = "mat_Феррошит_мини_миди"
    ferro_maxi_id = "mat_Феррошит_макси"
    work_key = "quarterly_tape_paste"
    for mid in (cursor_id, ferro_mini_id, ferro_maxi_id):
        if mid not in mats:
            raise ValueError(f"Материал «{mid}» не найден в каталоге.")
    if work_key not in works:
        raise ValueError("Работа «Поклейка на двусторонний скотч» не найдена.")
    cursor = float(mats[cursor_id]["price_a3"])
    size_name = (size.get("name") or "").strip().upper()
    ferro_id = ferro_maxi_id if size_name.startswith("МАКСИ") else ferro_mini_id
    ferro = float(mats[ferro_id]["price_a3"])
    tape = float(works[work_key]["cost"])
    return round(cursor + ferro + tape, 6)


def get_films_catalog():
    """Справочник плёнок (хранится в products.json, напр. у визиток)."""
    for prod in get_products().values():
        films = prod.get("films")
        if films:
            return films
    return {}


def _calendar_lamination_work(opt_def):
    """Работа для ламинации: 30 мкм и софт-тач — рулонная, 60+ — пакетная."""
    work_key = opt_def.get("work")
    if work_key:
        return work_key
    film = opt_def.get("film") or ""
    return "lam_roll" if film in ("gloss_30", "mat_30", "soft_touch_30") else "lam_pack"


def _calendar_lamination_cost_add(
    prod, size, opt_def, print_mode="", surfaces=1, films=None, works=None
):
    """Ламинация календарей по формуле: (плёнка А3 + работа) ÷ раскладка.

    Раскладка задаётся в размере:
    - lamination_surface_divisors — делители для каждой поверхности (квартальные);
    - lamination_pages_per_a3 — страниц на А3 (перекидные, pages из print_mode_pages);
    - lamination_divisor — изделий на А3 (домики, карманные).
    """
    films = get_shared_films() if films is None else films
    works = get_works() if works is None else works
    film_key = opt_def.get("film")
    if not film_key or film_key not in films:
        raise ValueError(f"Плёнка «{film_key}» не найдена.")
    work_key = _calendar_lamination_work(opt_def)
    if work_key not in works:
        raise ValueError(f"Работа «{work_key}» не найдена.")
    sheet_cost = float(films[film_key].get("a3", 0)) + float(works[work_key].get("cost", 0))

    pages_per_a3 = size.get("lamination_pages_per_a3")
    if pages_per_a3:
        pages = (prod.get("print_mode_pages") or {}).get(print_mode)
        if not pages:
            raise ValueError("Не задано количество страниц для выбранного варианта.")
        return sheet_cost * float(pages) / float(pages_per_a3)

    divisors = size.get("lamination_surface_divisors")
    if divisors:
        count = max(1, min(int(surfaces or 1), len(divisors)))
        return sum(sheet_cost / float(d or 1) for d in divisors[:count])

    divisor = float(size.get("lamination_divisor") or 1)
    return sheet_cost / divisor


def _quarterly_option_adds(size, prod, opt_name, surfaces=1, print_mode="", films=None, works=None):
    """Прибавка опции календаря: формула ламинации или cost_add из размера.

    surfaces — количество поверхностей ламинации (только квартальные).
    """
    opt_def = next((o for o in prod.get("options", []) if o["name"] == opt_name), None)
    if not opt_def:
        raise ValueError(f"Опция «{opt_name}» не найдена.")
    factor = float(opt_def.get("price_factor", 1.6))
    if opt_name == "Магнитный курсор":
        cost_add = _quarterly_magnetic_cursor_cost_add(size)
        return cost_add, round(cost_add * factor, 2)
    if opt_name == "Курсор":
        cost_add = _flip_cursor_cost_add()
        return cost_add, round(cost_add * factor, 2)
    if opt_def.get("film"):
        cost_add = _calendar_lamination_cost_add(
            prod, size, opt_def, print_mode=print_mode, surfaces=surfaces,
            films=films, works=works,
        )
        return cost_add, round(cost_add * factor, 2)
    entry = (size.get("option_addons") or {}).get(opt_name)
    if not entry:
        if opt_def.get("pending"):
            return 0.0, 0.0
        raise ValueError(f"Опция «{opt_name}» недоступна для выбранного размера.")
    cost_add = float(entry.get("cost_add", 0))
    return cost_add, round(cost_add * factor, 2)


def calc_quarterly_calendars(product_id, prod, payload):
    """Календари с фиксированной себестоимостью блока × наценка + опции (квартальные, перекидные, домики, карманные)."""
    size_name = payload.get("size")
    qty = int(payload.get("qty", 0))
    selected_opts = payload.get("options", []) or []
    discount_pct = float(payload.get("discount", 0) or 0)

    validate_finish_options(prod, selected_opts)

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")

    allowed = prod.get("print_modes") or []
    hide_print = bool(prod.get("hide_print_mode"))
    if hide_print or not allowed:
        print_mode = ""
        markup_profile = "default"
        cost = float(size.get("base_seb") or 0)
        if cost <= 0:
            seb_map = size.get("seb") or {}
            if seb_map:
                cost = float(next(iter(seb_map.values())))
    else:
        print_mode = payload.get("print_mode", allowed[0] if allowed else "")
        if print_mode not in allowed:
            raise ValueError(f"Доступные варианты: {', '.join(allowed)}.")
        markup_profile = print_mode
        seb_map = size.get("seb") or {}
        cost = float(seb_map.get(print_mode, 0))

    if cost <= 0:
        raise ValueError("Себестоимость для выбранного варианта не задана.")

    opt_by_name = {o["name"]: o for o in prod.get("options") or []}
    for oname in selected_opts:
        odef = opt_by_name.get(oname)
        if odef and odef.get("for_print_mode") and print_mode and odef["for_print_mode"] != print_mode:
            raise ValueError(f"Опция «{oname}» недоступна для выбранного варианта.")

    surfaces = int(payload.get("lamination_surfaces", 1) or 1)
    surface_options = size.get("lamination_surfaces_options")
    if surface_options:
        allowed_surfaces = sorted({int(x) for x in surface_options})
        if surfaces < allowed_surfaces[0]:
            surfaces = allowed_surfaces[0]
        elif surfaces > allowed_surfaces[-1]:
            surfaces = allowed_surfaces[-1]
        elif surfaces not in allowed_surfaces:
            surfaces = max(x for x in allowed_surfaces if x <= surfaces)
    else:
        surfaces = 1

    markup_mult, flat_price = _resolve_markup(size, qty, markup_profile)
    if flat_price is not None:
        base_price = flat_price
        markup = round(flat_price / cost, 4) if cost else 1.0
        used_flat_price = True
    else:
        markup = markup_mult
        base_price = round(cost * markup, 2)
        used_flat_price = False

    options_detail = []
    options_cost_add = 0.0
    options_price_add = 0.0
    films = get_shared_films()
    works = get_works()
    for oname in selected_opts:
        cost_add, price_add = _quarterly_option_adds(
            size, prod, oname, surfaces=surfaces, print_mode=print_mode,
            films=films, works=works,
        )
        options_cost_add += cost_add
        options_price_add += price_add
        options_detail.append({
            "name": oname,
            "cost_add": round(cost_add, 4),
            "price_add": round(price_add, 2),
        })

    cost_total = round(cost + options_cost_add, 2)
    price_per_item = round(base_price + options_price_add, 2)
    price_after_discount = price_per_item * (1 - discount_pct / 100.0)
    total = price_after_discount * qty

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": prod.get("calc_type", "quarterly_calendars"),
        "size": size_name,
        "print_mode": print_mode,
        "markup_profile": markup_profile,
        "qty": qty,
        "cost": cost_total,
        "base_cost": round(cost, 2),
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": base_price,
        "options": options_detail,
        "options_price_add": round(options_price_add, 2),
        "lamination_surfaces": surfaces,
        "price_per_item": price_per_item,
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount, 2),
        "discount_total": round((price_per_item - price_after_discount) * qty, 2),
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost_total, 2),
        "margin_total": round((price_after_discount - cost_total) * qty, 2),
    }


def _wide_format_material_for_resolution(material, dpi):
    base = 540 if dpi in (540, 630) else 720
    return int(material.get("base_resolution") or 720) == base


def _wide_resolve_material(prod, material_id, dpi):
    """Материал по id; при смене dpi — вариант с тем же названием."""
    mats = prod.get("materials") or []
    m = wide_find_material(prod, material_id)
    if m and _wide_format_material_for_resolution(m, dpi):
        return m
    name_key = capitalize_material_name(m.get("name", "")) if m else None
    if name_key:
        for cand in mats:
            if capitalize_material_name(cand.get("name", "")) == name_key \
               and _wide_format_material_for_resolution(cand, dpi):
                return cand
    if m:
        return m
    raise ValueError("Материал не найден.")


def calc_wide_format(product_id, prod, payload):
    """Широкоформат: цена за м² × площадь + опции + наценка 1,6, мин. заказ."""
    material_id = payload.get("material")
    dpi = int(payload.get("resolution") or 720)
    width_mm = float(payload.get("width_mm") or 0)
    height_mm = float(payload.get("height_mm") or 0)
    qty = int(payload.get("qty") or 1)
    discount_pct = float(payload.get("discount") or 0)
    perimeter_cut = bool(payload.get("perimeter_cut"))
    lamination_id = (payload.get("lamination") or "").strip()
    selected_opts = payload.get("options") or []
    option_counts = payload.get("option_counts") or {}
    eyelet_mode = (payload.get("eyelet_mode") or "").strip()

    if width_mm <= 0 or height_mm <= 0:
        raise ValueError("Укажите размер изделия в мм.")
    if qty <= 0:
        raise ValueError("Укажите количество изделий.")

    material = _wide_resolve_material(prod, material_id, dpi)
    if not material:
        raise ValueError("Материал не найден.")

    allowed_res = prod.get("resolutions") or [540, 630, 720, 1440]
    if dpi not in allowed_res:
        raise ValueError("Разрешение не найдено.")

    area_one = item_area_sqm(width_mm, height_mm)
    total_area = area_one * qty
    perimeter_one = item_perimeter_m(width_mm, height_mm)
    total_perimeter = perimeter_one * qty

    measure = total_perimeter if material.get("unit") == "linear_meter" else total_area

    unit_cost = material_unit_price(material, measure)
    surcharge = resolution_surcharge(prod, dpi)
    print_cost = (unit_cost + surcharge) * measure

    options_cost = 0.0
    opt_details = []

    if perimeter_cut:
        cut_opt = wide_find_option(prod, "perimeter_cut")
        if cut_opt:
            price_m = option_linear_price(cut_opt, total_perimeter)
            c = price_m * total_perimeter
            options_cost += c
            opt_details.append({"name": cut_opt["name"], "cost_add": round(c, 4), "price_add": 0})

    has_lamination = bool(lamination_id and lamination_id not in ("", "none"))
    lam_label = ""
    if has_lamination:
        lam_opt = wide_find_option(prod, lamination_id)
        if not lam_opt:
            raise ValueError("Ламинация не найдена.")
        allowed_keys = lam_opt.get("material_keys") or []
        mat_key = material.get("lamination_key")
        if allowed_keys and mat_key not in allowed_keys:
            raise ValueError("Ламинация недоступна для выбранного материала.")
        price_sqm = lamination_unit_price(lam_opt, total_area)
        c = price_sqm * total_area
        options_cost += c
        lam_label = lam_opt["name"]
        opt_details.append({"name": lam_opt["name"], "cost_add": round(c, 4), "price_add": 0})

    cat = material.get("category") or ""
    eyelet_mode_used = None
    eyelets_per_item_count = None
    for opt_id in selected_opts:
        opt = wide_find_option(prod, opt_id)
        if not opt or opt.get("kind") == "lamination":
            continue
        applies = opt.get("applies_to")
        if isinstance(applies, list) and cat not in applies:
            continue
        if opt.get("kind") == "piece":
            if opt_id == "eyelets":
                mode = eyelet_mode or opt.get("default_eyelet_mode") or "corners"
                if mode not in ("corners", "perimeter"):
                    mode = "corners"
                spacing_mm = int(opt.get("perimeter_spacing_mm") or EYELET_PERIMETER_SPACING_MM)
                per_item = eyelets_per_item(mode, width_mm, height_mm, spacing_mm)
                count = eyelets_total_count(mode, width_mm, height_mm, qty, spacing_mm)
                if count <= 0:
                    continue
                eyelet_mode_used = mode
                eyelets_per_item_count = per_item
                price = option_piece_price(opt, count)
                c = price * count
                if mode == "corners":
                    mode_note = "по углам"
                else:
                    mode_note = f"по периметру, каждые {spacing_mm // 10} см"
                label = f"{opt['name']} ×{count} ({mode_note})"
            else:
                count = int(option_counts.get(opt_id) or opt.get("default_count") or 0)
                if count <= 0:
                    continue
                price = option_piece_price(opt, count)
                c = price * count
                label = opt["name"] if count == 1 else f"{opt['name']} ×{count}"
        else:
            price_m = option_linear_price(opt, total_perimeter)
            c = price_m * total_perimeter
            label = opt["name"]
        options_cost += c
        opt_details.append({"name": label, "cost_add": round(c, 4), "price_add": 0})

    cost_total = print_cost + options_cost
    factor = float(prod.get("price_factor", DEFAULT_PRICE_FACTOR))
    min_order = min_order_for(material, has_lamination)
    retail_base = cost_total * factor
    min_applied = False
    if retail_base < min_order:
        retail_base = float(min_order)
        min_applied = True

    price_per_item = retail_base / qty if qty else retail_base
    price_after_discount_total = retail_base * (1 - discount_pct / 100.0)
    cost_per_item = cost_total / qty if qty else cost_total

    factor_display = round(retail_base / cost_total, 4) if cost_total else factor
    for od in opt_details:
        od["price_add"] = round(od["cost_add"] * factor_display, 2)

    size_label = f"{int(width_mm)}×{int(height_mm)} мм"
    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "calc_type": "wide_format",
        "size": size_label,
        "material": material["name"],
        "material_id": material_id,
        "resolution": dpi,
        "resolution_label": f"{dpi} dpi",
        "qty": qty,
        "area_sqm": round(total_area, 4),
        "area_one_sqm": round(area_one, 4),
        "perimeter_m": round(total_perimeter, 4),
        "unit_cost_sqm": round(unit_cost, 2),
        "resolution_surcharge": surcharge,
        "lamination": lam_label or None,
        "perimeter_cut": perimeter_cut,
        "eyelet_mode": eyelet_mode_used,
        "eyelets_per_item": eyelets_per_item_count,
        "min_order": min_order,
        "min_order_applied": min_applied,
        "cost_total": round(cost_total, 2),
        "cost": round(cost_per_item, 4),
        "markup": factor_display,
        "used_flat_price": False,
        "base_price": round(price_per_item, 2),
        "options": opt_details,
        "options_price_add": round(options_cost * factor_display, 2),
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": round(price_per_item - price_after_discount_total / qty, 2) if qty else 0,
        "discount_total": round(retail_base - price_after_discount_total, 2),
        "price_after_discount": round(price_after_discount_total / qty, 2) if qty else round(price_after_discount_total, 2),
        "total": round(price_after_discount_total, 2),
        "margin_per_item": round(price_after_discount_total / qty - cost_per_item, 2) if qty else 0,
        "margin_total": round(price_after_discount_total - cost_total, 2),
    }


def calc_product(product_id, payload):
    """Универсальный расчёт продукции."""
    products = get_products()
    works = get_works()
    settings = get_settings()
    prod = products.get(product_id)
    if not prod:
        raise ValueError("Продукт не найден.")
    payload["discount"] = parse_product_discount(prod, payload.get("discount"))

    if prod.get("calc_type") == "plotter_grid":
        return calc_plotter_grid(product_id, prod, payload)
    if prod.get("calc_type") == "wide_format":
        return calc_wide_format(product_id, prod, payload)
    if prod.get("calc_type") == "board_cover":
        return calc_board_cover(product_id, prod, payload)
    if prod.get("calc_type") == "notebook":
        return calc_notebook(product_id, prod, payload)
    if prod.get("calc_type") == "branded_cups":
        return calc_branded_cups(product_id, prod, payload)
    if prod.get("calc_type") == "sublimation":
        return calc_sublimation(product_id, prod, payload)
    if prod.get("calc_type") == "offset_table":
        return calc_offset_table(product_id, prod, payload)
    if prod.get("calc_type") == "pvd_bags":
        return calc_pvd_bags(product_id, prod, payload)
    if prod.get("calc_type") == "paper_bags":
        return calc_paper_bags(product_id, prod, payload)
    if prod.get("calc_type") == "envelopes":
        return calc_envelopes(product_id, prod, payload)
    if prod.get("calc_type") == "brochure_menu":
        return calc_brochure_menu(product_id, prod, payload)
    if prod.get("calc_type") in ("quarterly_calendars", "wall_calendars"):
        return calc_quarterly_calendars(product_id, prod, payload)

    size_name = payload.get("size")
    mat_name = payload.get("material")
    print_mode = payload.get("print_mode", "4+0")
    qty = int(payload.get("qty", 0))
    selected_opts = payload.get("options", []) or []
    option_counts = payload.get("option_counts") or {}
    discount_pct = float(payload.get("discount", 0) or 0)

    validate_finish_options(prod, selected_opts)
    validate_option_exclusions(prod, selected_opts)

    size = next((s for s in prod["sizes"] if s["name"] == size_name), None)
    if not size:
        raise ValueError("Размер не найден.")
    prod_materials = resolve_product_materials(prod)
    material = next((m for m in prod_materials if m["name"] == mat_name), None)
    if not material:
        raise ValueError("Материал не найден.")

    price_a3 = float(material["price_a3"])
    markup_profile = material.get("markup_profile", "default")
    prof_data = _markup_profile_data(size, markup_profile)
    if not material.get("custom_price_a3") and prof_data.get("price_a3") is not None:
        price_a3 = float(prof_data["price_a3"])
    if material.get("custom_price_a3"):
        raw = payload.get("material_price_a3")
        if raw is None or raw == "":
            raise ValueError("Укажите стоимость нестандартной бумаги за лист А3.")
        price_a3 = float(raw)
        if price_a3 <= 0:
            raise ValueError("Стоимость бумаги должна быть больше нуля.")

    allowed_print = material.get("print_modes") or prod.get("print_modes") or ["4+0", "4+4"]
    if print_mode not in allowed_print:
        raise ValueError(f"Для материала «{mat_name}» доступна печать: {', '.join(allowed_print)}.")

    per_sheet = float(size["per_sheet"])
    print_4_0 = float(settings.get("print_4_0") or prod.get("print_4_0", 0))
    print_1_0 = float(settings.get("print_1_0") or prod.get("print_1_0", 0))
    if print_mode == "4+4":
        print_unit, sides = print_4_0, 2
    elif print_mode == "1+1":
        print_unit, sides = print_1_0, 2
    elif print_mode == "1+0":
        print_unit, sides = print_1_0, 1
    else:
        print_unit, sides = print_4_0, 1
    print_work = float(works.get(prod["print_work_id"], {}).get("cost", 0))
    cut = float(works.get(prod["cut_work_id"], {}).get("cost", 0))

    material_part = price_a3 / per_sheet
    print_part = (print_unit * sides) / per_sheet
    print_work_part = (print_work * sides) / per_sheet
    tape_glue_part = _magnetic_vinyl_tape_extra(works, material, per_sheet)
    cost = material_part + print_part + print_work_part + cut + tape_glue_part

    markup_qty = _markup_qty_for_product(prod, size, qty)
    markup_mult, flat_price = _resolve_markup(size, markup_qty, markup_profile)
    if flat_price is not None:
        base_price = flat_price
        markup = round(flat_price / cost, 4) if cost else 1.0
        used_flat_price = True
    else:
        markup = markup_mult
        base_price = cost * markup
        used_flat_price = False

    excluded_opts = set(material.get("excluded_options") or [])
    opt_details, opts_per_item, opts_order = _calc_selected_options(
        prod, works, per_sheet, selected_opts, option_counts, excluded_opts, qty=qty
    )

    lanyard_detail = None
    if prod.get("id") == "beizhi":
        lanyard_detail = calc_beizhi_lanyard(prod, works, payload)
        if lanyard_detail:
            opt_details.append({
                "name": lanyard_detail["name"],
                "price_add": lanyard_detail["price_add"],
            })
            opts_per_item += lanyard_detail["price_add"]

    pricing = _finalize_qty_pricing(base_price, qty, opts_per_item, opts_order, discount_pct)
    price_per_item = pricing["price_per_item"]
    price_after_discount = pricing["price_after_discount"]
    total = pricing["total"]

    return {
        "product_id": product_id,
        "product": prod.get("title", product_id),
        "size": size_name,
        "material": mat_name,
        "markup_profile": markup_profile,
        "material_price_a3": round(price_a3, 2),
        "custom_material_price": bool(material.get("custom_price_a3")),
        "print_mode": print_mode,
        "qty": qty,
        "cost": round(cost, 2),
        "cost_breakdown": {
            "material": round(material_part, 3),
            "print": round(print_part, 3),
            "print_work": round(print_work_part, 3),
            "cut": round(cut, 3),
            "tape_glue": round(tape_glue_part, 3),
        },
        "markup": markup,
        "used_flat_price": used_flat_price,
        "base_price": round(base_price, 2),
        "options": opt_details,
        "options_price_add": round(opts_per_item, 2),
        "options_order_add": pricing["options_order_add"],
        "lanyard_label": lanyard_detail["name"] if lanyard_detail else None,
        "price_per_item": round(price_per_item, 2),
        "discount_pct": discount_pct,
        "discount_per_item": pricing["discount_per_item"],
        "discount_total": pricing["discount_total"],
        "price_after_discount": round(price_after_discount, 2),
        "total": round(total, 2),
        "margin_per_item": round(price_after_discount - cost, 2),
        "margin_total": round(total - cost * qty, 2),
    }


def calc_vizitki(payload):
    """Расчёт визиток (обёртка)."""
    return calc_product("vizitki", payload)


def _build_formula_docs_plotter(prod, works, settings):
    """Справка по формулам стикеров под плоттер."""
    markup_tiers = (prod.get("markup_profiles") or {}).get("default", {}).get("tiers", [])
    markup_summary = ", ".join(f"{int(t[0])} → ×{t[1]}" for t in markup_tiers)
    print_w = works.get(prod.get("print_work_id"), {})
    sheet_w = works.get(prod.get("plotter_sheet_work_id"), {})
    print_4_0 = float(settings.get("print_4_0") or 0)
    print_work_cost = float(print_w.get("cost", 0))
    sheet_work_cost = float(sheet_w.get("cost", 0))
    sw, sh = _plotter_sheet_dims(prod)

    base_formulas = [
        {
            "title": "Себестоимость листа А3",
            "lines": [
                "base_sheet = print_4_0 + price_a3 + print_work + плоттерная_резка [+ ламинация]",
                "print_4_0 — печать 4+0; price_a3 — материал; print_work — работа печати; плоттерная_резка — plotter_cut_sheet",
                "ламинация = film.a3 + work.cost (добавляется к base_sheet, затем ÷ per_sheet)",
            ],
            "params": [
                _fp("settings:print_4_0", "print_4_0", print_4_0),
                _fp(f"work:{prod.get('print_work_id') or ''}",
                    print_w.get("name", prod.get("print_work_id", "print_work")), print_work_cost),
                _fp(f"work:{prod.get('plotter_sheet_work_id') or ''}",
                    sheet_w.get("name", prod.get("plotter_sheet_work_id", "sheet_work")), sheet_work_cost),
            ] + [
                _fp(f"material:{m.get('id')}", f"{m.get('name')} (цена листа А3)", m.get("price_a3"))
                for m in resolve_product_materials(prod) if m.get("id")
            ],
        },
        {
            "title": "Себестоимость 1 шт",
            "lines": [
                f"per_sheet = раскладка на листе {sw}×{sh} мм (лучший поворот)",
                "припуск к макету: +2 мм (самоклейка бумага/плёнка), +4 мм (магнитный винил, картон)",
                "cost = base_sheet ÷ per_sheet",
                "qty в заказе — ближайшее к tier_key (100, 300…), кратное per_sheet",
            ],
            "params": [],
        },
        {
            "title": "Цена и итог",
            "lines": [
                "базовая цена = cost × наценка(tier_key)",
                "цена со скидкой = базовая цена × (1 − discount ÷ 100)",
                "итого = цена со скидкой × qty (тираж из списка)",
            ],
            "params": [
                _fp_link("Наценки по тиражу", markup_summary or "—"),
            ],
        },
    ]

    size_examples = []
    materials = resolve_product_materials(prod)
    if materials:
        for size in prod.get("sizes", [])[:2]:
            w, h = size.get("width_mm"), size.get("height_mm")
            if not w or not h:
                continue
            mat = materials[0]
            sw, sh = _plotter_sheet_dims(prod)
            margin = _plotter_layout_margin(prod, mat)
            per_sheet = compute_plotter_per_sheet(w, h, sw, sh, margin)
            tier_key = 100
            base_sheet = _plotter_base_sheet(settings, works, prod, mat)
            cost = _plotter_cost_per_sheet(base_sheet, per_sheet)
            markup = _markup_for(markup_tiers, tier_key)
            base_price = round(cost * markup, 2)
            qty = compute_plotter_qty_tiers(per_sheet)[0]["qty"]
            size_examples.append({
                "size_name": size["name"],
                "per_sheet": per_sheet,
                "material": mat["name"],
                "markup_profile": "default",
                "qty": qty,
                "print_mode": "4+0",
                "steps": [
                    {"label": "base_sheet", "calc": f"{print_4_0} + {mat['price_a3']} + {print_work_cost} + {sheet_work_cost}",
                     "value": round(base_sheet, 4)},
                    {"label": "÷ per_sheet", "calc": f"{round(base_sheet, 2)} ÷ {per_sheet}",
                     "value": round(cost, 4)},
                    {"label": "× наценка", "calc": f"tier_key {tier_key} → ×{markup}",
                     "value": markup},
                ],
                "cost": round(cost, 4),
                "markup": markup,
                "base_price": base_price,
            })

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": [],
        "print_modes": prod.get("print_modes", ["4+0"]),
        "discounts": prod.get("discounts", []),
    }



def _build_formula_docs_board_cover(prod, works):
    """справка по формулам обложек из переплётного картона."""
    settings = get_settings()
    mats = _notebook_material_prices(prod)
    films = prod.get("films") or {}
    size = next((s for s in prod.get("sizes", []) if s.get("format") == "A4"), prod["sizes"][0])
    cost, _ = compute_notebook_board_cover(prod, works, settings, mats, films, "A4")
    qty = 20
    markup_mult, _fp_flat = _resolve_markup(size, qty, "default")
    base_price = cost * markup_mult

    bw_ids = prod.get("notebook_work_ids") or DEFAULT_NOTEBOOK_WORK_IDS

    base_formulas = [
        {
            "title": "\u0421" + "ебестоимость обложки (формула блокнотов)",
            "lines": [
                "печать 4+0 x k + самоклейка x k + переплётный картон / d + плёнка софт-тач / d + ламинация / d + работа плоттера x 4",
                "коэффициенты по формату: а4 k=2 d=2; а5 k=1 d=4; а6 k=0.5 d=8; а3 = а4 x 2",
                "все цены материалов и работ — из справочников админки",
                "наценка 1,8 для любого тиража — тираж можно не указывать",
            ],
            "params": [
                _fp("product:fixed_markup", "Наценка", prod.get("fixed_markup", 1.8), "не зависит от тиража"),
                _fp(f"work:{bw_ids.get('print') or ''}", "Печать 4+0 обложки (работа печати)",
                    float(works.get(bw_ids.get("print") or "", {}).get("cost", 0))),
            ],
        },
        {
            "title": "\u0426" + "ена и итог",
            "lines": [
                "базовая цена = себестоимость x наценка (1,8)",
                "цена 1 шт = базовая цена + крепление (кольца, болты или пружина)",
                "итого = цена со скидкой x тираж (если тираж указан)",
            ],
            "params": [],
        },
        {
            "title": "Нестандартный размер (разложенный вид)",
            "lines": [
                "размер обложки в разложенном виде — не больше листа переплётного картона 297 x 420 мм",
                "если больше: уменьшите размер изделия или посчитайте обложку из нескольких частей, которые затем соединяются на двусторонний скотч",
                "себестоимость считается по ближайшему стандартному формату (например, больше А5 и меньше А4 → А4)",
                "каждая часть, кроме первой: + порезка части + склейка на двусторонний скотч x множитель",
                "крепление и ламинация считаются как для стандартного формата",
            ],
            "params": [
                _fp("custom:max_width", "Ширина листа переплётного картона, мм",
                    float((board_cover_custom_cfg(prod).get("max_width") or 0))),
                _fp("custom:max_height", "Высота листа переплётного картона, мм",
                    float((board_cover_custom_cfg(prod).get("max_height") or 0))),
                _fp(f"work:{board_cover_custom_cfg(prod).get('cut_work_id') or 'cut_item'}", "Порезка части",
                    float(works.get(board_cover_custom_cfg(prod).get("cut_work_id") or "cut_item", {}).get("cost", 0))),
                _fp(f"work:{board_cover_custom_cfg(prod).get('tape_work_id') or 'sublimation_tape_glue'}",
                    "Склейка на двусторонний скотч",
                    float(works.get(board_cover_custom_cfg(prod).get("tape_work_id") or "sublimation_tape_glue", {}).get("cost", 0))),
                _fp("custom:tape_work_mult", "Множитель скотча на часть",
                    float(board_cover_custom_cfg(prod).get("tape_work_mult") or 4)),
                {"label": "Форматы базы", "value": "А6 105x148; А5 148x210; А4 210x297; А3 297x420"},
            ],
        },
    ]

    size_examples = [{
        "size_name": size["name"],
        "material": prod.get("material_label", ""),
        "markup_profile": "default",
        "qty": qty,
        "print_mode": "4+0",
        "per_sheet": None,
        "steps": [
            {"label": "Себестоимость обложки", "calc": "формула материалов и работ", "value": round(cost, 4)},
            {"label": "x наценка", "calc": "1,8 — не зависит от тиража", "value": markup_mult},
        ],
        "cost": round(cost, 4),
        "markup": markup_mult,
        "base_price": round(base_price, 2),
    }]

    # Пример: нестандартная обложка 400×300 мм в разложенном виде из 2 частей
    cs_cfg = board_cover_custom_cfg(prod)
    cs_w, cs_h, cs_parts = 400.0, 300.0, 2
    cs_fmt = board_nearest_format(cs_w, cs_h)
    cs_size = next((s for s in prod.get("sizes", []) if s.get("format") == cs_fmt), size)
    cs_cost, _ = compute_notebook_board_cover(prod, works, settings, mats, films, cs_fmt)
    cs_cut = float(works.get(cs_cfg.get("cut_work_id") or "cut_item", {}).get("cost", 0))
    cs_tape = float(works.get(cs_cfg.get("tape_work_id") or "sublimation_tape_glue", {}).get("cost", 0))
    cs_tape_mult = float(cs_cfg.get("tape_work_mult") or 4)
    cs_parts_cost = round((cs_parts - 1) * (cs_cut + cs_tape * cs_tape_mult), 4)
    cs_markup, _fp_flat2 = _resolve_markup(cs_size, qty, "default")
    cs_pw, cs_ph = board_part_size(cs_w, cs_h, cs_parts)
    size_examples.append({
        "size_name": "Нестандарт 400×300 мм · 2 част. (часть " + ("%g" % cs_pw) + "×" + ("%g" % cs_ph) + " мм)",
        "material": prod.get("material_label", ""),
        "markup_profile": "default",
        "qty": qty,
        "print_mode": "4+0",
        "per_sheet": None,
        "steps": [
            {"label": "Себестоимость обложки", "calc": "база " + cs_fmt + " (формула материалов и работ)", "value": round(cs_cost, 4)},
            {"label": "+ доп. части", "calc": str(cs_parts - 1) + " x (порезка " + ("%g" % cs_cut) + " + скотч " + ("%g" % cs_tape) + " x " + ("%g" % cs_tape_mult) + ")", "value": cs_parts_cost},
            {"label": "x наценка", "calc": "1,8 — не зависит от тиража", "value": cs_markup},
        ],
        "cost": round(cs_cost, 4),
        "markup": cs_markup,
        "base_price": round((cs_cost + cs_parts_cost) * cs_markup, 2),
    })

    mount_name = next((m.get("label") or m.get("name") for m in prod.get("material_refs", []) if m.get("role") == "rings"), "")
    option_index_by_name = {o.get("name"): i for i, o in enumerate(prod.get("options") or [])}
    option_docs = []
    for opt in sort_product_options(prod.get("options") or []):
        if opt.get("kind") != "board_mount":
            continue
        role = opt.get("material_role") or ""
        if role == "spring":
            lines = [
                "цена = (материал пружины x масштаб формата + установка пружины + перфорация x 42 + резка пружины) x 1.6",
                "масштаб: а4 x1; а5 x1; а6 x0.5; а3 x2 (как в блокнотах)",
                "количество — 1 шт на изделие",
            ]
            calc_hint = "пружина: материал x масштаб + установка + перфорация"
        else:
            lines = [
                "цена = (материал крепежа за штуку + дырокол) x количество x 1.6",
                "дырокол — работа «круглое/овальное отверстие» x количество креплений",
                "цена материала — за штуку, из справочника материалов (админка)",
            ]
            calc_hint = "материал + дырокол (за 1 шт)"
        mat_ref = next((m for m in prod.get("material_refs", []) if m.get("role") == role), None)
        mat_label = (mat_ref or {}).get("label") or mount_name or "—"
        mat_catalog = materials_by_id().get((mat_ref or {}).get("id") or "")
        params = []
        if mat_catalog:
            params.append(_fp(f"material:{mat_catalog.get('id')}", f"{mat_label} — цена материала",
                              mat_catalog.get("price_a3")))
        if role == "spring":
            for wkey in ("coil_install", "spiral_punch", "spiral_cut"):
                wid = bw_ids.get(wkey) or DEFAULT_NOTEBOOK_WORK_IDS.get(wkey)
                if wid and wid in works:
                    params.append(_fp(f"work:{wid}", works[wid].get("name", wkey), works[wid].get("cost")))
        if not params:
            params.append({"label": "Материал", "value": mat_label})
        examples = []
        for s in prod.get("sizes", []):
            c_add, p_add = compute_board_mount(opt, prod, works, mats, s.get("format", "A4"))
            examples.append({
                "size_name": s["name"],
                "per_sheet": None,
                "calc": calc_hint,
                "cost_add": c_add,
                "price_add": p_add,
            })
        option_docs.append({
            "name": opt["name"],
            "kind": opt.get("kind"),
            "factor": float(opt.get("price_factor", 1.6)),
            "price_factor_key": f"option:{option_index_by_name.get(opt.get('name'), 0)}:price_factor",
            "lines": lines,
            "params": params,
            "examples": examples,
        })

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": option_docs,
        "print_modes": prod.get("print_modes", []),
        "discounts": prod.get("discounts", []),
    }


def _build_formula_docs_notebook(prod, works):
    """Справка по формулам блокнотов."""
    settings = get_settings()
    mats = _notebook_material_prices(prod)
    default_pages = int(prod.get("default_page_count") or 40)
    block_modes = prod.get("block_modes") or ["0+0", "1+0", "4+0"]
    size = next(
        (s for s in prod.get("sizes", []) if "А4" in s.get("name", "") and "4+0" in s.get("name", "")),
        prod["sizes"][0],
    )
    block_mode = "0+0"
    cost = compute_notebook_base_cost(size, block_mode, prod, works, settings, mats)
    page_unit = compute_notebook_page_unit_cost(size, block_mode, prod, works, settings, mats)
    qty = 20
    markup_mult, flat_price = _resolve_markup(size, qty, block_mode)
    base_price = flat_price if flat_price is not None else cost * markup_mult
    markup_display = round(flat_price / cost, 4) if flat_price is not None else markup_mult

    mat_params = [
        _fp(f"material:{m.get('id')}", f"{m['name']} (цена листа А3)", m.get("price_a3"))
        for m in resolve_product_materials(prod) if m.get("id")
    ]

    work_ids = prod.get("notebook_work_ids") or DEFAULT_NOTEBOOK_WORK_IDS
    work_specs = [
        ("print", "печать блока ÷ div или × 41 ÷ div"),
        ("coil_install", "установка пружины, фикс."),
        ("spiral_punch", "перфорация под пружину × 42"),
        ("spiral_cut", "резка пружины на бобине"),
        ("block_layout", "раскладка блоков × множитель формата"),
        ("cut", "автоматическая резка × множитель формата"),
    ]
    work_params = []
    for key, role in work_specs:
        wid = work_ids.get(key, DEFAULT_NOTEBOOK_WORK_IDS.get(key))
        w = works.get(wid) or {}
        if w:
            work_params.append(_fp(f"work:{wid}", f"{w.get('name', key)} — {role}", w.get("cost")))

    base_formulas = [
        {
            "title": "Себестоимость (базовый блок 40 листов)",
            "lines": [
                "себестоимость = обложка + блок + фиксированные работы + печать блока",
                "все цены материалов и работ — из справочников админки (пересчёт при каждом заказе)",
                "обложка: картон + печать 4+0; блок: офсет 80 г × листов + печать по цветности",
                "фиксированно: пружина + установка + перфорация×42 + резка пружины + раскладка + резка",
                "1+1 = 0+0 + 2×(1+0 − 0+0);  4+4 = 0+0 + 2×(4+0 − 0+0)",
                f"adjusted_cost = base_cost + (page_count − {default_pages}) × page_unit_cost",
                "базовая цена = adjusted_cost × наценка(тираж, цветность)",
                "малые тиражи: 1 шт → ×10; 8 и 10 шт → ×5",
            ],
            "params": [
                _fp("product:default_page_count", "Базовый блок, листов", default_pages),
                {"label": "Цветности блока", "value": ", ".join(block_modes)},
            ] + mat_params + work_params,
        },
        {
            "title": "Цена и итог",
            "lines": [
                "базовая цена = себестоимость × наценка(тираж, цветность)",
                "цена 1 шт = базовая цена + корректировка страниц + ламинация обложки + переплётный картон",
                "итого = цена со скидкой × тираж",
            ],
            "params": [
                _fp_link("Наценка 1 шт", "×10"),
                _fp_link("Наценка 8–10 шт", "×5"),
            ],
        },
    ]

    size_examples = [{
        "size_name": size["name"],
        "material": f"{prod.get('cover_material', 'обложка')} · {prod.get('block_spec', 'блок')}",
        "markup_profile": f"блок {block_mode}",
        "qty": qty,
        "print_mode": block_mode,
        "per_sheet": None,
        "steps": [
            {"label": "Себестоимость 40 л.", "calc": "формула материалов + работ", "value": round(cost, 2)},
            {"label": "Тариф за +1 лист", "calc": "page_unit_cost", "value": round(page_unit, 4)},
            {"label": "× наценка", "calc": f"тираж {qty}", "value": markup_display},
        ],
        "cost": round(cost, 4),
        "markup": markup_display,
        "base_price": round(base_price, 2),
    }]

    films = prod.get("films") or {}
    option_docs = []
    for opt in sort_product_options(prod.get("options") or []):
        kind = opt.get("kind")
        doc = {"name": opt["name"], "kind": kind, "factor": 1.6, "lines": [], "params": [], "examples": []}
        if kind == "notebook_board":
            doc["lines"] = [
                "price_add = себестоимость обложки из переплётного картона × 1.6 × количество (0–2)",
                "себестоимость = печать + самоклейка + переплётный картон + плёнка + работы (из справочников)",
            ]
            for sz in prod.get("sizes", [])[:3]:
                fmt = sz.get("format", "A4")
                cost_add, price_add = compute_notebook_board_cover(
                    prod, works, settings, mats, films, fmt,
                )
                doc["examples"].append({
                    "size_name": sz["name"],
                    "per_sheet": None,
                    "calc": f"{price_add} × 1",
                    "cost_add": round(cost_add, 4),
                    "price_add": price_add,
                })
        elif option_kind_formula(kind) == "lamination":
            factor = float(opt.get("price_factor", 1.6))
            doc["factor"] = factor
            doc["lines"] = [
                f"price_add = (film.a3 + work.cost) × масштаб формата × {factor:g}",
                "масштаб: А3×2, А4×1, А5×0.5, А6×0.25",
            ]
            for sz in prod.get("sizes", [])[:3]:
                fmt = sz.get("format", "A4")
                cost_add, price_add = compute_notebook_lamination(opt, prod, works, fmt)
                doc["examples"].append({
                    "size_name": sz["name"],
                    "per_sheet": None,
                    "calc": f"({opt.get('film')} + {opt.get('work')}) × scale",
                    "cost_add": round(cost_add, 4),
                    "price_add": price_add,
                })
        else:
            doc["lines"] = ["фиксированная прибавка из конфигурации"]
        option_docs.append(doc)

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": option_docs,
        "print_modes": block_modes,
        "discounts": prod.get("discounts", []),
    }


def _build_formula_docs_branded_cups(prod, works, settings):
    """Справка по формулам брендированных стаканчиков."""
    mats = materials_by_id()
    markup_summary = ", ".join(
        f"{int(t[0])} → ×{t[1]}" for t in (prod.get("markup_tiers") or [])
    )
    sheet_w = works.get(prod.get("sticker_sheet_work_id"), {})
    apply_w = works.get(prod.get("sticker_apply_work_id"), {})
    print_w = works.get("print_a3", {})
    print_4_0 = float(settings.get("print_4_0") or 0)
    film = mats.get(prod.get("sticker_film_material_id"), {})

    base_formulas = [
        {
            "title": "Себестоимость (стикер на стакан)",
            "lines": [
                "unit = print_4_0 + самоклейка_глянец + плоттерная_резка + print_work",
                "стикер = unit × area_pct ÷ 100 (доля листа под наклейку)",
                "себестоимость = цена_стакана + стикер + плоттерная_резка ÷ print_norm + поклейка_на_стакан",
                "цена = себестоимость × наценка(тираж); ламинации нет",
            ],
            "params": [
                _fp("settings:print_4_0", "print_4_0", print_4_0),
                _fp(f"material:{prod.get('sticker_film_material_id') or ''}",
                    film.get("name", "плёнка"), film.get("price_a3", "—")),
                _fp(f"work:{prod.get('sticker_sheet_work_id') or ''}",
                    sheet_w.get("name", "sheet_work"), sheet_w.get("cost", "—")),
                _fp(f"work:{prod.get('sticker_apply_work_id') or ''}",
                    apply_w.get("name", "apply_work"), apply_w.get("cost", "—")),
                _fp("work:print_a3", print_w.get("name", "print_a3"), print_w.get("cost", "—")),
                _fp("product:paper_cup_price_factor", "Наценка на стаканчики (paper_cup_price_factor)",
                    float(prod.get("paper_cup_price_factor") or 1.6)),
            ],
        },
        {
            "title": "Картонные с полноцветом",
            "lines": [
                "себестоимость = фиксированная база по объёму (250/350/450 мл)",
                "тираж 5000: цена = база × 1.6 (без ступенчатой наценки)",
                "доступны только при тираже от 5000 шт",
            ],
            "params": [
                _fp("pbase:250 мл", "База 250 мл", (prod.get("cardboard_base") or {}).get("250 мл", 0)),
                _fp("pbase:350 мл", "База 350 мл", (prod.get("cardboard_base") or {}).get("350 мл", 0)),
                _fp("pbase:450 мл", "База 450 мл", (prod.get("cardboard_base") or {}).get("450 мл", 0)),
            ],
        },
        {
            "title": "Наценки по тиражу (стикер)",
            "lines": [
                "базовая цена = себестоимость × множитель",
                "цена со скидкой = базовая × (1 − discount ÷ 100)",
                "итого = цена со скидкой × тираж",
            ],
            "params": [
                _fp_link("Наценки", markup_summary or "—"),
            ],
        },
    ]

    size_examples = []
    sizes = prod.get("sizes") or []
    materials = resolve_product_materials(prod)
    sticker_mat = next((m for m in materials if m.get("markup_profile") == "sticker_30mm"), None)
    if sizes and sticker_mat:
        size = sizes[0]
        qty = 100
        cost = _branded_cups_base_cost(
            settings, works, mats, prod, size["name"], "sticker_30mm",
        )
        markup = _markup_for(prod.get("markup_tiers") or [], qty)
        base_price = round(cost * markup, 2)
        size_examples.append({
            "size_name": size["name"],
            "material": sticker_mat["name"],
            "qty": qty,
            "steps": [
                {"label": "себестоимость", "calc": "стакан + стикер + работы", "value": round(cost, 4)},
                {"label": "× наценка", "calc": f"тираж {qty} → ×{markup}", "value": base_price},
            ],
        })

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": [],
        "print_modes": [],
        "discounts": prod.get("discounts", []),
    }


def _build_formula_docs_envelopes(prod, works):
    """Справка по формулам конвертов."""
    settings = get_settings()
    mats = materials_by_id()
    print_w = works.get(prod.get("envelope_print_work_id"), {})

    base_formulas = [
        {
            "title": "Себестоимость 1 шт",
            "lines": [
                "себестоимость = цена_конверта + ксерокс 4+0 ÷ divisor + работа_печати ÷ divisor",
                "без печати: только цена конверта",
                "divisor: С4 → 2, С5 → 4, Е65/С6/С7 → 6",
            ],
            "params": [
                _fp("settings:xerox_print_4_0", "Ксерокс 4+0", settings.get("xerox_print_4_0", "—")),
                _fp(f"work:{prod.get('envelope_print_work_id') or ''}",
                    print_w.get("name", "envelope_print"), print_w.get("cost", "—")),
                _fp_link("Наценки по тиражам", "раздел «Наценки по тиражам»"),
            ],
        },
        {
            "title": "Цена и итог",
            "lines": [
                "тиражи 1, 5, 10, 20, 50, 100… — одинаковые для 4+0 и без печати",
                "С7: только без печати",
                "20–50: ×5.8; 100 → ×4.8; 200 → ×3.8; …",
                "цена со скидкой = цена × (1 − discount ÷ 100); итого = цена × тираж",
            ],
            "params": [],
        },
    ]

    size_examples = []
    sizes = prod.get("sizes") or []
    if sizes:
        size = sizes[0]
        cost = _envelope_base_cost(settings, works, mats, prod, size, "4+0")
        markup = _markup_for((size.get("markup_profiles") or {}).get("4+0", {}).get("tiers") or [], 100)
        base_price = round(cost * markup, 2)
        size_examples.append({
            "size_name": size["name"],
            "material": "Офсет 80 г",
            "qty": 100,
            "print_mode": "4+0",
            "steps": [
                {"label": "себестоимость", "calc": "конверт + печать/div + работа/div", "value": round(cost, 4)},
                {"label": "× наценка", "calc": "тираж 100 → ×4.8", "value": base_price},
            ],
        })

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": [],
        "print_modes": prod.get("print_modes", []),
        "discounts": prod.get("discounts", []),
    }


def _build_formula_docs_brochure(prod, works, settings):
    """Справка по формулам брошюр и меню."""
    size = next(
        (s for s in prod.get("sizes", []) if s.get("name", "").startswith("А4") and s.get("binding") == "spring"),
        prod["sizes"][0],
    )
    mats = resolve_product_materials(prod)
    mat = mats[0] if mats else {}
    qty = 100
    cover_mult = 2
    pages_mult = 6
    page_units = cover_mult + pages_mult
    offset_ref = _brochure_offset_ref_seb(prod)
    unit_seb = _brochure_unit_seb(mat, size, offset_ref)
    body_seb = round(unit_seb * page_units, 4)
    binding_cost = float(size.get("binding_cost") or 0)
    binding_retail = _brochure_binding_retail(prod, binding_cost)
    cost = round(body_seb + binding_cost, 4)
    markup_mult, flat_price = _resolve_markup(size, qty, "default")
    body_retail = round(body_seb * markup_mult, 2) if flat_price is None else round(flat_price * page_units, 2)
    base_price = round(body_retail + binding_retail, 2)
    binding_factor = float(prod.get("brochure_binding_factor") or 1.6)

    mat_lines = []
    for m in mats:
        seb = m.get("brochure_ref_seb", "—")
        dp = m.get("brochure_default_print", "—")
        mat_lines.append(f"{m['name']}: unit_seb {round(float(seb) * float(size.get('body_ref_cost', 0)) / offset_ref, 4) if seb != '—' else '—'} ₽ ({dp})")

    base_formulas = [
        {
            "title": "Себестоимость и цена",
            "lines": [
                "unit_seb = (price_a3 + печать + работа + резка) ÷ масштаб_формата",
                "price_a3 — из каталога materials.json; печать и работы — из settings.json и works.json",
                "body_seb = unit × число_печатных_стр. (обложка и блок отдельно)",
                "body_retail = body_seb × наценка(тираж) — по секциям обложка + страницы",
                f"крепление (цена) = binding_cost × {binding_factor}",
                "базовая цена = body_retail + крепление (цена) + опции",
                "множитель страниц: пружина 1+0/4+0 → 1 стр.; 1+1/4+4 → 2 стр.",
                "множитель страниц: скрепка 1+0/4+0 → 2 стр.; 1+1/4+4 → 4 стр.",
            ],
            "params": [
                _fp(f"size:{next(i for i, s in enumerate(prod['sizes']) if s is size)}:body_ref_cost",
                    "body_ref (формат)", size.get("body_ref_cost", 0)),
                _fp("product:brochure_binding_factor", "Множитель крепления", binding_factor),
            ] + [
                _fp(f"material:{m.get('id')}", f"{m['name']} — brochure_ref_seb", m.get("brochure_ref_seb"))
                for m in mats
                if m.get("id") and m.get("brochure_ref_seb") not in (None, "—")
            ],
        },
        {
            "title": "Цена и итог",
            "lines": [
                "цена 1 шт = базовая цена + Σ price_add опций",
                "итого = цена со скидкой × тираж",
            ],
            "params": [
                _fp_link("Наценка", "ближайший меньший или равный тираж из Excel"),
            ],
        },
    ]

    size_examples = [{
        "size_name": f"{size['name']} · пружина",
        "material": mat.get("name", "—"),
        "markup_profile": "default",
        "qty": qty,
        "print_mode": mat.get("brochure_default_print", "4+0"),
        "per_sheet": size.get("per_sheet"),
        "steps": [
            {"label": "unit_seb", "calc": f"body_ref × seb/{offset_ref:.3f}", "value": round(unit_seb, 4)},
            {"label": f"body_seb ({page_units} стр.)", "calc": f"{round(unit_seb, 4)} × {page_units}", "value": body_seb},
            {"label": "× наценка", "calc": f"тираж {qty} → ×{markup_mult}", "value": body_retail},
            {"label": "+ крепление", "calc": f"{binding_cost} × {binding_factor}", "value": binding_retail},
            {"label": "базовая цена", "calc": "body_retail + крепление", "value": base_price},
        ],
    }]

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": [o["name"] for o in prod.get("options") or []],
        "print_modes": prod.get("print_modes") or [],
        "discounts": prod.get("discounts", []),
    }


def _build_formula_docs_quarterly_calendars(prod, works):
    """Справка по формулам квартальных календарей."""
    option_lines = [
        "ламинация: (плёнка А3 + работа) ÷ раскладка размера × price_factor опции",
        "price_factor: 1.6 для 30 мкм и софт-тач, 1.86 для 60 мкм и выше",
        "магнитный курсор: (курсор + феррошит + одевание курсора) × price_factor опции — цены из каталога материалов и видов работ",
    ]
    if prod.get("id") == "perekidnye_kalendari":
        option_lines.append(
            "курсор: (курсор 5,6 ₽ + одевание курсора) × price_factor опции — цены из каталога материалов и видов работ"
        )
    base_formulas = [
        {
            "title": "Себестоимость и цена",
            "lines": [
                "себестоимость 1 шт = значение «Себ-сть с учетом работ» для размера и типа блока (включая люверс полиграфии 0,96 ₽/шт)",
                "базовая цена = себестоимость × наценка(тираж)",
                *option_lines,
                "цена 1 шт = базовая цена + Σ price_add опций",
                "цена со скидкой = цена × (1 − discount ÷ 100); итого = цена × тираж",
            ],
            "params": [
                _fp_link("Наценки по тиражам", "раздел «Наценки по тиражам»"),
            ] + [
                _fp(f"size:{i}:seb:{mode}", f"«{s.get('name')}» · {mode} — себестоимость", value)
                for i, s in enumerate(prod.get("sizes") or [])
                for mode, value in sorted((s.get("seb") or {}).items())
                if isinstance(value, (int, float))
            ],
        },
    ]

    size_examples = []
    sizes = prod.get("sizes") or []
    if sizes:
        size = sizes[0]
        mode = (prod.get("print_modes") or ["с рекл-м блоком"])[0]
        cost = float((size.get("seb") or {}).get(mode, 0))
        markup = _markup_for(
            (size.get("markup_profiles") or {}).get(mode, {}).get("tiers") or [],
            100,
        )
        base_price = round(cost * markup, 2)
        size_examples.append({
            "size_name": size["name"],
            "material": mode,
            "qty": 100,
            "print_mode": mode,
            "steps": [
                {"label": "себестоимость", "calc": f"блок «{mode}»", "value": round(cost, 4)},
                {"label": "× наценка", "calc": "тираж 100 → ×1.8", "value": base_price},
            ],
        })

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": [],
        "print_modes": prod.get("print_modes", []),
        "discounts": prod.get("discounts", []),
    }


def _fp(key, label, value, extra=None):
    """Параметр формулы, редактируемый прямо на странице формул."""
    p = {"label": label, "value": value, "key": key}
    if extra:
        p["extra"] = extra
    return p


def _fp_link(label, value, hint=None):
    """Параметр формулы, который правится в разделе «Наценки по тиражам»."""
    p = {"label": label, "value": value, "link": "markup"}
    p["extra"] = hint or "правится в разделе «Наценки по тиражам»"
    return p


def _formula_number(raw):
    return float(str(raw).strip().replace("\u00a0", "").replace(" ", "").replace(",", "."))


FORMULA_INT_CUSTOM_FIELDS = ("max_width", "max_height", "min_size", "max_parts", "default_parts")


def apply_formula_form(product_id, form):
    """Сохраняет правки числовых параметров формул продукта. Возвращает список ошибок."""
    products = get_products()
    prod = products.get(product_id)
    if not prod:
        return ["Продукт не найден."]
    works = get_works()
    materials = get_materials()
    films = get_shared_films()
    settings = get_settings()
    errors = []
    products_dirty = works_dirty = materials_dirty = films_dirty = settings_dirty = False
    print_costs = {}

    for name, raw in sorted(form.items()):
        if not name.startswith("fp_"):
            continue
        key = name[3:]
        if str(raw).strip() == "":
            continue
        try:
            value = _formula_number(raw)
        except (TypeError, ValueError):
            errors.append(f"«{raw}» — не число (параметр {key}).")
            continue
        if value < 0:
            errors.append(f"Параметр {key}: значение не может быть отрицательным.")
            continue
        ns, _, rest = key.partition(":")
        try:
            if ns == "settings":
                if rest in ("print_4_0", "print_1_0", "xerox_print_4_0", "xerox_print_1_0"):
                    print_costs[rest] = value
                else:
                    settings[rest] = value
                    settings_dirty = True
            elif ns == "work":
                wk = works.get(rest)
                if wk is None:
                    errors.append(f"Вид работы «{rest}» не найден.")
                    continue
                wk["cost"] = value
                if float(wk.get("norm_per_min") or 0) > 0:
                    hour = float(settings.get("hour_cost", 440) or 440)
                    wk["norm_per_min"] = round((hour / 60.0) / value, 6) if value else 0.0
                works_dirty = True
            elif ns == "material":
                mid, _, field = rest.partition(":")
                field = field or "price_a3"
                mt = next((m for m in materials if m.get("id") == mid), None)
                if mt is None:
                    errors.append(f"Материал «{mid}» не найден в каталоге.")
                    continue
                mt[field] = int(value) if field == "count" else value
                materials_dirty = True
            elif ns == "film":
                if rest not in films:
                    errors.append(f"Плёнка «{rest}» не найдена.")
                    continue
                films[rest]["a3"] = value
                films_dirty = True
            elif ns == "pbase":
                base_cfg = prod.get("cardboard_base")
                if not isinstance(base_cfg, dict):
                    base_cfg = {}
                    prod["cardboard_base"] = base_cfg
                base_cfg[rest] = value
                products_dirty = True
            elif ns == "product":
                prod[rest] = int(value) if rest == "default_page_count" else value
                products_dirty = True
            elif ns == "custom":
                cfg = prod.get("custom_size")
                if not isinstance(cfg, dict):
                    cfg = {}
                    prod["custom_size"] = cfg
                cfg[rest] = int(value) if rest in FORMULA_INT_CUSTOM_FIELDS else value
                products_dirty = True
            elif ns in ("option", "wopt"):
                oi, _, field = rest.partition(":")
                items = prod.get("options" if ns == "option" else "wide_options") or []
                idx = int(oi)
                if not (0 <= idx < len(items)):
                    errors.append(f"Опция №{oi} не найдена.")
                    continue
                items[idx][field] = int(value) if field == "count" else value
                products_dirty = True
            elif ns == "wmat":
                oi, _, field = rest.partition(":")
                items = prod.get("materials") or []
                idx = int(oi)
                if not (0 <= idx < len(items)):
                    errors.append(f"Материал №{oi} не найден.")
                    continue
                items[idx][field] = value
                products_dirty = True
            elif ns == "size":
                parts = rest.split(":")
                idx = int(parts[0])
                sizes = prod.get("sizes") or []
                if not (0 <= idx < len(sizes)):
                    errors.append(f"Размер №{idx} не найден.")
                    continue
                size = sizes[idx]
                if parts[1] == "seb":
                    seb = size.get("seb")
                    if not isinstance(seb, dict):
                        seb = {}
                        size["seb"] = seb
                    seb[":".join(parts[2:])] = value
                else:
                    size[parts[1]] = value
                products_dirty = True
            else:
                errors.append(f"Неизвестный параметр «{key}».")
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"Параметр {key}: {exc}")

    if products_dirty:
        save_json("products.json", products)
    if films_dirty:
        save_films_and_sync(films)
    if print_costs:
        save_print_costs_and_sync(**print_costs)
    if works_dirty:
        save_json("works.json", works)
    if materials_dirty:
        save_materials(materials)
    if settings_dirty:
        save_json("settings.json", settings)
    return errors


def _formula_example_payload(prod):
    """Минимальный рабочий payload для числового примера (первые значения из конфига)."""
    ct = prod.get("calc_type")
    sizes = prod.get("sizes") or []
    payload = {}
    if sizes:
        payload["size"] = sizes[0].get("name")
    if ct == "offset_table":
        mats = prod.get("materials") or []
        if not mats or not sizes:
            return None
        size_id = sizes[0].get("id") or sizes[0].get("name")
        mat = mats[0]
        modes = [m for m in (prod.get("print_modes") or []) if m != "3+0"] or (prod.get("print_modes") or [])
        if not modes:
            return None
        qty_options, delivery_tiers = _offset_material_options(prod, size_id, mat.get("id"))
        if not qty_options or not delivery_tiers:
            return None
        payload["material"] = mat.get("id")
        payload["print_mode"] = modes[0]
        payload["qty"] = qty_options[0]
        payload["delivery_tier"] = delivery_tiers[0].get("id")
        lam_modes = prod.get("lamination_modes") or []
        if lam_modes:
            allowed = [m.get("id") for m in lam_modes]
            payload["lamination"] = "none" if "none" in allowed else allowed[0]
        return payload
    if ct == "pvd_bags":
        mats = prod.get("materials") or []
        if not mats or not sizes:
            return None
        modes = [m for m in (prod.get("print_modes") or []) if m != "3+0"] or (prod.get("print_modes") or [])
        qtys = sizes[0].get("qty_options") or prod.get("qty_options") or []
        if not modes or not qtys:
            return None
        payload["material"] = mats[0].get("id")
        payload["print_mode"] = modes[0]
        payload["qty"] = qtys[0]
        return payload
    if ct == "paper_bags":
        if not sizes:
            return None
        qtys = sizes[0].get("qty_options") or []
        if not qtys:
            return None
        bag = {}
        for group in prod.get("mandatory_options") or []:
            choices = group.get("choices") or []
            if choices:
                bag[group["id"]] = choices[0].get("id")
        payload["qty"] = qtys[0]
        payload["bag_options"] = bag
        return payload
    if ct == "wide_format":
        mats = prod.get("materials") or []
        if not mats:
            return None
        payload["material"] = mats[0].get("id")
        payload["resolution"] = (prod.get("resolutions") or [720])[0]
        payload["width_mm"] = 1000
        payload["height_mm"] = 1000
        payload["qty"] = 1
        return payload
    if ct == "sublimation":
        variants = list((prod.get("variants") or {}).keys())
        if not variants or not sizes:
            return None
        payload["material"] = variants[0]
        payload["qty"] = min(100, int(prod.get("qty_max") or 100))
        return payload
    return None


def _build_formula_docs_table(prod, works):
    """Справка по формулам продукции с таблицей цен (офсет, пакеты, широкоформат, сублимация)."""
    ct = prod.get("calc_type")
    factor = float(prod.get("price_factor", DEFAULT_PRICE_FACTOR))
    source = prod.get("price_source") or ""
    as_of = prod.get("price_as_of") or ""
    src_text = "; ".join(x for x in (source, f"обновлено {as_of}" if as_of else "") if x) or "таблица цен"

    if ct == "offset_table":
        title = "Себестоимость и цена (таблица цен офсета)"
        lines = [
            "себестоимость тиража = lookup_offset_cost(размер, материал, цветность, тираж, срок)",
            "цена 1 шт = (себестоимость × price_factor) ÷ тираж",
            "цена со скидкой = цена × (1 − discount ÷ 100); итого = цена × тираж",
        ]
    elif ct == "pvd_bags":
        title = "Себестоимость и цена (таблица цен ПВД)"
        lines = [
            "себестоимость 1 шт = lookup(размер, материал, цветность, тираж)",
            "цена 1 шт = себестоимость × price_factor",
            "цена со скидкой = цена × (1 − discount ÷ 100); итого = цена × тираж",
        ]
    elif ct == "paper_bags":
        title = "Себестоимость и цена (таблица цен пакетов)"
        lines = [
            "итого за тираж — из таблицы (размер × тираж)",
            "цена 1 шт = (итого за тираж × price_factor + обязательные опции без наценки) ÷ тираж",
            "цена со скидкой = цена × (1 − discount ÷ 100); итого = цена × тираж",
        ]
    elif ct == "wide_format":
        title = "Себестоимость и цена (широкоформат)"
        lines = [
            "площадь = ширина × высота (м²); для материалов в пог. м — периметр",
            "цена за единицу = (материал + наценка за разрешение) × price_factor",
            "итого = (цена за единицу × количество единиц + опции) × тираж",
        ]
    else:
        title = "Себестоимость и цена (сублимация)"
        lines = [
            "себестоимость = бланк ÷ делитель + расходники + работа (+ доп. материалы)",
            "базовая цена = себестоимость × наценка(диапазон тиража)",
            "цена со скидкой = цена × (1 − discount ÷ 100); итого = цена × тираж",
        ]

    params = [
        _fp("product:price_factor", "Наценка price_factor", factor, "множитель к себестоимости"),
        {"label": "Источник цен", "value": src_text},
    ]

    if ct == "wide_format":
        for i, mt in enumerate(prod.get("materials") or []):
            for field, label in (("price_under", "Цена до порога"), ("price_from", "Цена от порога")):
                if mt.get(field) is None:
                    continue
                extra = f"порог {mt.get('tier_threshold')} м²" if mt.get("tier_threshold") else None
                params.append(_fp(f"wmat:{i}:{field}", f"{mt.get('name')} — {label}", mt.get(field), extra))
    elif ct == "sublimation":
        mats_by_id = materials_by_id()
        cons = prod.get("consumable_material_ids") or {}
        for role, label in (("ink", "Краска"), ("paper", "Бумага для сублимации")):
            mid = cons.get(role)
            if mid and mid in mats_by_id:
                params.append(_fp(f"material:{mid}", label, mats_by_id[mid].get("price_a3")))
        first_variant = next(iter((prod.get("variants") or {}).values()), None) or {}
        blank_id = first_variant.get("blank_material_id")
        if blank_id and blank_id in mats_by_id:
            params.append(_fp(f"material:{blank_id}", "Бланк (первое изделие)", mats_by_id[blank_id].get("price_a3")))
        work_id = first_variant.get("work_id")
        if work_id and work_id in works:
            params.append(_fp(f"work:{work_id}", works[work_id].get("name", work_id), works[work_id].get("cost")))

    base_formulas = [
        {"title": title, "lines": lines, "params": params},
        {
            "title": "Цена и итог",
            "lines": [
                "базовая цена = себестоимость × множитель наценки",
                "скидка клиента применяется к цене за 1 шт",
                "итого = цена со скидкой × тираж",
            ],
            "params": [_fp_link("Наценки по тиражам", "раздел «Наценки по тиражам»")],
        },
    ]
    size_examples = []
    payload = _formula_example_payload(prod)
    if payload:
        try:
            res = calc_product(prod.get("id"), payload)
        except Exception:
            res = None
        if res:
            cost_value = res.get("cost_total")
            if cost_value is None:
                cost_value = res.get("cost") or 0
            size_examples.append({
                "size_name": res.get("size") or "—",
                "material": res.get("material") or "",
                "qty": res.get("qty"),
                "print_mode": res.get("print_mode") or res.get("resolution") or "",
                "per_sheet": None,
                "markup_profile": "",
                "steps": [
                    {"label": "Себестоимость", "calc": "значение из таблицы цен",
                     "value": round(float(cost_value or 0), 4)},
                    {"label": "× наценка", "calc": f"price_factor = {factor}", "value": res.get("markup")},
                    {"label": "Цена 1 шт", "calc": "себестоимость × наценка", "value": res.get("base_price")},
                ],
                "cost": round(float(cost_value or 0), 4),
                "markup": res.get("markup"),
                "base_price": res.get("base_price"),
            })

    option_docs = []
    for i, o in enumerate(prod.get("wide_options") or []):
        o_params = []
        for field, label in (("price_under", "Цена до порога"), ("price_from", "Цена от порога")):
            if o.get(field) is None:
                continue
            extra = f"порог {o.get('tier_threshold')}" if o.get("tier_threshold") else None
            o_params.append(_fp(f"wopt:{i}:{field}", label, o.get(field), extra))
        option_docs.append({
            "name": o.get("name"),
            "kind": o.get("kind"),
            "factor": None,
            "price_factor_key": None,
            "lines": [f"единица: {o.get('unit') or o.get('kind') or '—'}"],
            "params": o_params,
            "examples": [],
        })

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": option_docs,
        "print_modes": prod.get("print_modes", []),
        "discounts": prod.get("discounts", []),
    }


def build_formula_docs(prod, works):
    """Справка по формулам продукта с числовыми примерами из текущих справочников."""
    settings = get_settings()
    if prod.get("calc_type") == "plotter_grid":
        return _build_formula_docs_plotter(prod, works, settings)
    if prod.get("calc_type") == "board_cover":
        return _build_formula_docs_board_cover(prod, works)
    if prod.get("calc_type") == "notebook":
        return _build_formula_docs_notebook(prod, works)
    if prod.get("calc_type") == "branded_cups":
        return _build_formula_docs_branded_cups(prod, works, settings)
    if prod.get("calc_type") == "envelopes":
        return _build_formula_docs_envelopes(prod, works)
    if prod.get("calc_type") == "brochure_menu":
        return _build_formula_docs_brochure(prod, works, settings)
    if prod.get("calc_type") in ("quarterly_calendars", "wall_calendars"):
        return _build_formula_docs_quarterly_calendars(prod, works)
    if prod.get("calc_type") in ("offset_table", "pvd_bags", "paper_bags", "wide_format", "sublimation"):
        return _build_formula_docs_table(prod, works)

    print_w = works.get(prod.get("print_work_id") or "", {})
    cut_w = works.get(prod.get("cut_work_id") or "", {})
    print_4_0 = float(prod["print_4_0"])
    print_1_0 = float(prod.get("print_1_0", 0))
    print_work_cost = float(print_w.get("cost", 0))
    cut_cost = float(cut_w.get("cost", 0))
    films = prod.get("films", {})

    base_formulas = [
        {
            "title": "Себестоимость 1 шт",
            "lines": [
                "материал = price_a3 ÷ per_sheet",
                "печать = (print_4_0 × стороны) ÷ per_sheet",
                "работа печати = (print_work.cost × стороны) ÷ per_sheet",
                "резка = cut_work.cost  (за 1 изделие, без деления на лист)",
                "себестоимость = материал + печать + работа печати + резка",
            ],
            "params": [
                _fp("settings:print_4_0", "print_4_0 (печать Коника 4+0, ₽/лист А3)", print_4_0),
                _fp(f"work:{prod.get('print_work_id') or ''}", print_w.get("name", prod.get("print_work_id", "print_work")),
                    print_work_cost, f"норматив {print_w.get('norm_per_min')} шт/мин"),
                _fp(f"work:{prod.get('cut_work_id') or ''}", cut_w.get("name", prod.get("cut_work_id", "cut_work")),
                    cut_cost, f"норматив {cut_w.get('norm_per_min')} шт/мин"),
                {"label": "стороны", "value": "1 при 4+0, 2 при 4+4"},
            ] + [
                _fp(f"material:{m.get('id')}", f"{m.get('name')} (цена листа А3)", m.get("price_a3"))
                for m in resolve_product_materials(prod) if m.get("id")
            ],
        },
        {
            "title": "Цена и итог",
            "lines": [
                "базовая цена = себестоимость × множитель наценки(тираж, группа материала)",
                "цена 1 шт = базовая цена + Σ price_add опций",
                "цена со скидкой = цена 1 шт × (1 − discount ÷ 100)",
                "итого за тираж = цена со скидкой × тираж",
            ],
            "params": [
                _fp_link("Наценка", "точное совпадение тиража или ближайший меньший"),
            ],
        },
    ]

    size_examples = []
    prod_materials = resolve_product_materials(prod)
    labels = prod.get("markup_profile_labels") or {}
    for size in prod["sizes"]:
        per_sheet = float(size["per_sheet"])
        mat = prod_materials[0] if prod_materials else {"name": "—", "price_a3": 0, "markup_profile": "default"}
        profile = mat.get("markup_profile", "default")
        prof_data = _markup_profile_data(size, profile)
        markup_list = prof_data.get("tiers") or []
        qty = int(markup_list[0][0]) if markup_list else 1000
        sides = 1
        material_part = float(mat["price_a3"]) / per_sheet
        print_part = (print_4_0 * sides) / per_sheet
        print_work_part = (print_work_cost * sides) / per_sheet
        cost = material_part + print_part + print_work_part + cut_cost
        markup = _markup_for(markup_list, qty)
        base_price = cost * markup
        profile_label = labels.get(profile, profile)
        size_examples.append({
            "size_name": size["name"],
            "per_sheet": per_sheet,
            "material": mat["name"],
            "markup_profile": profile_label,
            "qty": qty,
            "print_mode": "4+0",
            "steps": [
                {"label": "Материал", "calc": f"{mat['price_a3']} ÷ {per_sheet}",
                 "value": round(material_part, 4)},
                {"label": "Печать 4+0", "calc": f"{print_4_0} × {sides} ÷ {per_sheet}",
                 "value": round(print_part, 4)},
                {"label": "Работа печати", "calc": f"{print_work_cost} × {sides} ÷ {per_sheet}",
                 "value": round(print_work_part, 4)},
                {"label": "Резка", "calc": str(cut_cost), "value": round(cut_cost, 4)},
            ],
            "cost": round(cost, 4),
            "markup": markup,
            "base_price": round(base_price, 2),
        })

    option_docs = []
    for opt_index, opt in enumerate(prod.get("options", [])):
        kind = opt.get("kind")
        factor = float(opt.get("price_factor", 1.6))
        doc = {
            "name": opt["name"],
            "kind": kind,
            "factor": factor,
            "price_factor_key": f"option:{opt_index}:price_factor",
            "lines": [],
            "params": [],
            "examples": [],
        }

        if kind == "lamination":
            film = films.get(opt["film"], {})
            work = works.get(opt["work"], {})
            doc["lines"] = [
                "cost_add = (film.a3 + work.cost) ÷ per_sheet",
                f"price_add = cost_add × {factor}",
            ]
            doc["params"] = [
                _fp(f"film:{opt.get('film') or ''}", film.get("name", opt.get("film", "")), film.get("a3")),
                _fp(f"work:{opt.get('work') or ''}", work.get("name", opt.get("work", "")), work.get("cost")),
            ]
        elif kind == "corner":
            work = works.get(opt["work"], {})
            count = int(opt.get("count", 4))
            doc["lines"] = [
                f"cost_add = work.cost × {count}  (без деления на per_sheet)",
                f"price_add = cost_add × {factor}",
            ]
            doc["params"] = [
                _fp(f"work:{opt.get('work') or ''}", work.get("name", opt.get("work", "")), work.get("cost")),
                _fp(f"option:{opt_index}:count", "количество углов", count),
            ]
        elif kind == "crease":
            work = works.get(opt["work"], {})
            count = int(opt.get("count", 1))
            doc["lines"] = [
                f"cost_add = work.cost × 2 × {count}  (без деления на per_sheet)",
                f"price_add = cost_add × {factor}",
            ]
            doc["params"] = [
                _fp(f"work:{opt.get('work') or ''}", work.get("name", opt.get("work", "")), work.get("cost")),
                _fp(f"option:{opt_index}:count", "количество линий", count),
            ]
        elif kind == "ribbon_hole" or option_kind_formula(kind) == "work_unit":
            work = works.get(opt["work"], {})
            count = int(opt.get("count", 1))
            doc["lines"] = [
                f"cost_add = work.cost × {count}  (без деления на per_sheet)",
                f"price_add = cost_add × {factor}",
            ]
            doc["params"] = [
                _fp(f"work:{opt.get('work') or ''}", work.get("name", opt.get("work", "")), work.get("cost")),
                _fp(f"option:{opt_index}:count", "количество", count),
            ]
        elif kind == "foil":
            soft = films.get(opt["film"], {})
            foil = films.get(opt["foil"], {})
            sides = int(opt.get("print_sides", 1))
            f_val = 1.0 if sides == 2 else 0.5
            surcharge = float(opt.get("surcharge", 1.3))
            roll = works.get("lam_roll", {})
            doc["lines"] = [
                f"f = {f_val}  ({'1+1' if sides == 2 else '1+0'})",
                "cost_add = (print_1_0×f + soft.a3 + foil.a3×f + print_work.cost + lam_roll.cost×2) ÷ per_sheet × surcharge",
                f"price_add = cost_add × {factor}",
            ]
            doc["params"] = [
                _fp("settings:print_1_0", "print_1_0", print_1_0),
                _fp(f"film:{opt.get('film') or ''}", soft.get("name", opt.get("film", "")), soft.get("a3")),
                _fp(f"film:{opt.get('foil') or ''}", foil.get("name", opt.get("foil", "")), foil.get("a3")),
                _fp(f"work:{prod.get('print_work_id') or ''}", print_w.get("name", prod.get("print_work_id", "")),
                    print_work_cost),
                _fp("work:lam_roll", roll.get("name", "lam_roll"), roll.get("cost")),
                _fp(f"option:{opt_index}:surcharge", "surcharge", surcharge),
            ]
        else:
            doc["lines"] = ["cost_add = фиксированное значение из конфига"]
            doc["params"] = [_fp(f"option:{opt_index}:cost_add", "cost_add", opt.get("cost_add", 0))]

        for size in prod["sizes"]:
            per_sheet = float(size["per_sheet"])
            cost_add, price_add = compute_option(opt, prod, works, per_sheet)
            if kind == "lamination":
                film_a3 = float(films.get(opt["film"], {}).get("a3", 0))
                work_cost = float(works.get(opt["work"], {}).get("cost", 0))
                calc = f"({film_a3} + {work_cost}) ÷ {per_sheet}"
            elif kind == "corner":
                work_cost = float(works.get(opt["work"], {}).get("cost", 0))
                calc = f"{work_cost} × {opt.get('count', 4)}"
            elif kind == "crease":
                work_cost = float(works.get(opt["work"], {}).get("cost", 0))
                calc = f"{work_cost} × 2 × {opt.get('count', 1)}"
            elif kind == "ribbon_hole" or option_kind_formula(kind) == "work_unit":
                work_cost = float(works.get(opt["work"], {}).get("cost", 0))
                calc = f"{work_cost} × {opt.get('count', 1)}"
            elif kind == "foil":
                sides = int(opt.get("print_sides", 1))
                f_val = 1.0 if sides == 2 else 0.5
                soft_a3 = float(films.get(opt["film"], {}).get("a3", 0))
                foil_a3 = float(films.get(opt["foil"], {}).get("a3", 0))
                roll_cost = float(works.get("lam_roll", {}).get("cost", 0))
                surcharge = float(opt.get("surcharge", 1.3))
                calc = (f"({print_1_0}×{f_val} + {soft_a3} + {foil_a3}×{f_val} + "
                        f"{print_work_cost} + {roll_cost}×2) ÷ {per_sheet} × {surcharge}")
            else:
                calc = str(opt.get("cost_add", 0))
            doc["examples"].append({
                "size_name": size["name"],
                "per_sheet": per_sheet,
                "calc": calc,
                "cost_add": round(cost_add, 4),
                "price_add": round(price_add, 2),
            })
        option_docs.append(doc)

    return {
        "base_formulas": base_formulas,
        "size_examples": size_examples,
        "options": option_docs,
        "print_modes": prod.get("print_modes", []),
        "discounts": prod.get("discounts", []),
    }


def _ru_num(value):
    """Число без лишних нулей в русском формате: 400.0 → «400», 12.5 → «12,5»."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return ("%g" % num).replace(".", ",")


def result_to_text(r):
    lines = [f"{r['product']} — {r['size']}"]
    calc_type = r.get("calc_type")
    if calc_type == "plotter_grid":
        if r.get("per_sheet"):
            lines.append(f"На листе: {r['per_sheet']} шт")
        if r.get("print_mode"):
            lines.append(f"Печать: {r['print_mode']}")
    elif calc_type == "board_cover":
        cs = r.get("custom_size") or {}
        if cs:
            lines.append(
                "Разложенный размер: " + _ru_num(cs.get("width")) + " × "
                + _ru_num(cs.get("height")) + " мм"
            )
            if cs.get("base_format"):
                lines.append(
                    (cs.get("base_label") or "База расчёта") + ": " + str(cs.get("base_format"))
                )
            if int(cs.get("parts") or 1) > 1:
                lines.append(
                    "Частей переплётного картона: " + str(cs.get("parts")) + " ("
                    + _ru_num(cs.get("part_width")) + "×" + _ru_num(cs.get("part_height"))
                    + " мм каждая, соединение на двусторонний скотч)"
                )
        if r.get("board_covers") and int(r.get("board_covers", 1)) != 1:
            lines.append("Количество обложек: " + str(r.get("board_covers")))
        if r.get("lamination"):
            lines.append("Ламинация: " + str(r.get("lamination")))
        if r.get("mount"):
            lines.append("Крепление: " + str(r.get("mount")))
        if r.get("punch_per_sheet") is not None:
            lines.append("Не забудьте добавить к внутренним листам стоимость дырокола - " + str(r["punch_per_sheet"]).replace(".", ",") + " р за лист")
        if r.get("qty_specified") is False:
            lines.append("Тираж: не указан")
        elif r.get("qty"):
            lines.append("Тираж: " + str(r["qty"]) + " шт")
    elif calc_type == "notebook":
        lines.append(f"Цветность блока: {r.get('block_mode', r.get('print_mode'))}")
        if r.get("page_count"):
            lines.append(f"Страниц в блоке: {r['page_count']} л.")
    elif calc_type == "branded_cups":
        lines.append(f"Способ: {r.get('material', '—')}")
    elif calc_type == "sublimation":
        lines.append(f"Изделие: {r.get('material', '—')}")
    elif calc_type == "offset_table":
        lines.append(f"Материал: {r.get('material', '—')}")
        lines.append(f"Печать: {r.get('print_mode_label', r.get('print_mode', '—'))}")
        if r.get("lamination_label"):
            lines.append(f"Ламинация: {r['lamination_label']}")
        if r.get("delivery_label"):
            lines.append(r["delivery_label"])
    elif calc_type == "pvd_bags":
        lines.append(f"Материал: {r.get('material', '—')}")
        lines.append(f"Печать: {r.get('print_mode_label', r.get('print_mode', '—'))}")
    elif calc_type == "paper_bags":
        if r.get("delivery_label"):
            lines.append(r["delivery_label"])
        if r.get("lamination_label"):
            lines.append(f"Ламинация: {r['lamination_label']}")
        if r.get("eyelets_label"):
            lines.append(f"Люверсы: {r['eyelets_label']}")
        if r.get("cord_label"):
            lines.append(f"Шнурок: {r['cord_label']}")
    elif calc_type == "wide_format":
        lines.append(f"Материал: {r.get('material', '—')}")
        lines.append(f"Разрешение: {r.get('resolution_label', r.get('resolution', '—'))}")
        if r.get("area_sqm") is not None:
            lines.append(f"Площадь: {r['area_sqm']} м²")
        if r.get("perimeter_cut"):
            lines.append("Порезка по периметру: да")
        if r.get("min_order_applied"):
            lines.append(f"Применён минимальный заказ: {r.get('min_order')} ₽")
    elif calc_type == "envelopes":
        lines.append(f"Печать: {r.get('print_mode')}")
    elif calc_type in ("quarterly_calendars", "wall_calendars"):
        if r.get("print_mode"):
            label = "Блок" if calc_type == "quarterly_calendars" else "Вариант"
            lines.append(f"{label}: {r.get('print_mode')}")
        if r.get("lamination_surfaces") and int(r["lamination_surfaces"]) > 1:
            lines.append(f"Поверхностей ламинации: {r['lamination_surfaces']}")
    elif calc_type == "brochure_menu":
        lines.append(f"Переплёт: {r.get('binding_label', r.get('binding'))}")
        lines.append(f"Крепление: {r.get('binding_position_label', r.get('binding_position'))}")
        lines.append(f"Ориентация: {r.get('orientation')}")
        lines.append(
            f"Страниц всего: {r.get('page_count')} "
            f"(обложка {r.get('cover_pages')}, блок {r.get('block_pages')})"
        )
        lines.append(f"Обложка: {r.get('cover_material')} · {r.get('cover_print')}")
        lines.append(f"Страницы: {r.get('pages_material')} · {r.get('pages_print')}")
        if r.get("cover_retail") is not None:
            lines.append(f"Стоимость обложки: {r['cover_retail']} ₽")
            lines.append(f"Стоимость страниц: {r['pages_retail']} ₽")
            lines.append(f"Крепление: {r.get('binding_retail')} ₽")
    else:
        mat_line = f"Материал: {r.get('material', '—')}"
        if r.get("custom_material_price"):
            mat_line += f" ({r['material_price_a3']} ₽/А3)"
        lines.append(mat_line)
        lines.append(f"Печать: {r.get('print_mode', '—')}")
    if r.get("lamination") and r["lamination"] != "Без ламинации":
        lines.append(f"Ламинация: {r['lamination']}")
    if r.get("lanyard_label"):
        lines.append(f"Лента: {r['lanyard_label']}")
    lines.append(f"Тираж: {r['qty']} шт.")
    if r["options"]:
        lines.append("Опции: " + ", ".join(o["name"] for o in r["options"]))
    lines.append(f"Цена за шт: {r['price_after_discount']} ₽")
    if r["discount_pct"]:
        lines.append(f"(с учётом скидки {r['discount_pct']:.0f}%)")
    lines.append(f"ИТОГО за тираж: {r['total']} ₽")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Маршруты: авторизация
# ----------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname = (request.form.get("username") or "").strip()
        pwd = request.form.get("password") or ""
        users = get_users()
        u = users.get(uname)
        if u and check_password_hash(u["password"], pwd):
            status = user_status(u)
            if status == USER_STATUS_PENDING:
                flash("Аккаунт ожидает одобрения администратора.", "error")
            elif status == USER_STATUS_REJECTED:
                flash("Регистрация отклонена. Обратитесь к администратору.", "error")
            else:
                session["user"] = uname
                record_user_login(uname)
                nxt = request.args.get("next") or url_for("calculator")
                return redirect(nxt)
        else:
            flash("Неверный логин или пароль.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if not registration_enabled():
        flash("Регистрация временно отключена.", "error")
        return redirect(url_for("login"))
    if request.method == "POST":
        uname = (request.form.get("username") or "").strip()
        full = (request.form.get("full_name") or "").strip()
        pwd = request.form.get("password") or ""
        pwd2 = request.form.get("password2") or ""
        users = get_users()
        if not uname or not pwd:
            flash("Заполните логин и пароль.", "error")
        elif uname in users:
            flash("Такой логин уже занят.", "error")
        elif pwd != pwd2:
            flash("Пароли не совпадают.", "error")
        elif len(pwd) < 4:
            flash("Пароль слишком короткий (мин. 4 символа).", "error")
        else:
            users[uname] = {
                "password": generate_password_hash(pwd),
                "role": "employee",
                "full_name": full or uname,
                "status": USER_STATUS_PENDING,
                "created": datetime.now().isoformat(timespec="seconds"),
            }
            save_users(users)
            flash(
                "Заявка отправлена. После одобрения администратором вы сможете войти в систему.",
                "ok",
            )
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/profile/password", methods=["GET", "POST"])
@login_required
def profile_password():
    """Смена собственного пароля (пользователь входит в систему)."""
    uname = session.get("user")
    if request.method == "POST":
        cur = request.form.get("current_password") or ""
        pwd = request.form.get("new_password") or ""
        pwd2 = request.form.get("new_password2") or ""
        users = get_users()
        u = users.get(uname)
        if not u:
            session.pop("user", None)
            return redirect(url_for("login"))
        if not check_password_hash(u["password"], cur):
            flash("Текущий пароль неверен.", "error")
        elif len(pwd) < 4:
            flash("Новый пароль слишком короткий (мин. 4 символа).", "error")
        elif pwd == cur:
            flash("Новый пароль должен отличаться от текущего.", "error")
        elif pwd != pwd2:
            flash("Новые пароли не совпадают.", "error")
        else:
            u["password"] = generate_password_hash(pwd)
            save_users(users)
            flash("Пароль изменён.", "ok")
            return redirect(url_for("profile_password"))
    return render_template("profile_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Восстановление забытого пароля по одноразовому коду от администратора."""
    if request.method == "POST":
        uname = (request.form.get("username") or "").strip()
        token = (request.form.get("token") or "").strip()
        pwd = request.form.get("password") or ""
        pwd2 = request.form.get("password2") or ""
        if not uname or not token or not pwd:
            flash("Заполните логин, код восстановления и новый пароль.", "error")
            return render_template("reset_password.html")
        if len(pwd) < 4:
            flash("Пароль слишком короткий (мин. 4 символа).", "error")
            return render_template("reset_password.html")
        if pwd != pwd2:
            flash("Пароли не совпадают.", "error")
            return render_template("reset_password.html")
        users = get_users()
        u = users.get(uname)
        if not u or not is_user_active(u):
            flash("Пользователь не найден или аккаунт не активен.", "error")
            return render_template("reset_password.html")
        if not validate_password_reset_token(uname, token):
            flash("Неверный или просроченный код восстановления.", "error")
            return render_template("reset_password.html")
        u["password"] = generate_password_hash(pwd)
        save_users(users)
        clear_password_reset_token(uname)
        flash("Пароль восстановлен. Войдите с новым паролем.", "ok")
        return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.route("/logout")
def logout():
    session.pop("view_as", None)
    session.pop("user", None)
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------
# Маршруты: калькулятор
# ----------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return redirect(url_for("calculator"))


@app.route("/calc")
@login_required
def calculator():
    products = get_products()
    disabled = disabled_product_ids()
    regular = [
        (pid, p) for pid, p in products.items()
        if pid not in CALENDAR_HUB_IDS
        and pid not in OFFSET_HUB_IDS
        and pid not in PAPKI_HUB_IDS
        and pid not in PAPER_BAGS_PRODUCT_IDS
        and pid != PVD_PRODUCT_ID
        and pid != WIDE_FORMAT_PRODUCT_ID
        and pid != BOARD_COVER_PRODUCT_ID
    ]
    # Бумажные пакеты: активный калькулятор (новый на прайсе Омега-Принт;
    # прежний — только если включён в админке, а новый отсутствует/отключён).
    paper_bags_product = None
    for pid in (PAPER_BAGS_PRODUCT_ID, "bumazhnye_pakety"):
        prod = products.get(pid)
        if prod and pid not in disabled:
            paper_bags_product = prod
            paper_bags_product_id = pid
            break
    else:
        paper_bags_product_id = None
    return render_template(
        "calculator_index.html",
        products=regular,
        calendar_hub=get_calendar_hub(),
        offset_hub=get_offset_hub(),
        pvd_product=products.get(PVD_PRODUCT_ID),
        wide_format_product=products.get(WIDE_FORMAT_PRODUCT_ID),
        paper_bags_product=paper_bags_product,
        paper_bags_product_id=paper_bags_product_id,
        board_covers_product=products.get(BOARD_COVER_PRODUCT_ID),
    )


@app.route("/info")
@login_required
def extra_info():
    return render_template("extra_info.html", data=get_sheet_layout_info())


@app.route("/design")
@login_required
def design_page():
    return render_template("design.html", data=get_design_info())


@app.route("/calc/ofset")
@login_required
def calculator_offset_hub():
    hub = get_offset_hub()
    if hub:
        return redirect(url_for("calculator_product", product_id=hub[0][0]))
    return redirect(url_for("calculator"))


@app.route("/calc/kalendari")
@login_required
def calculator_calendars_hub():
    return redirect(url_for("calculator_product", product_id="kvartalnye_kalendari"))


@app.route("/calc/<product_id>")
@login_required
def calculator_product(product_id):
    products = get_products()
    prod = products.get(product_id)
    if not prod:
        flash("Продукт не найден.", "error")
        return redirect(url_for("calculator"))
    if product_id in disabled_product_ids():
        flash("Этот калькулятор отключён администратором.", "error")
        return redirect(url_for("calculator"))
    ru = real_user()
    show_admin_details = bool(
        ru and ru.get("role") == "admin" and session.get("view_as") != "employee"
    )
    ui_product = enrich_ui_product(prod)
    if prod.get("calc_type") == "plotter_grid":
        return render_template(
            "calculator_plotter.html",
            product=ui_product,
            product_id=product_id,
            show_admin_details=show_admin_details,
        )
    if prod.get("calc_type") == "wide_format":
        return render_template(
            "calculator_wide_format.html",
            product=ui_product,
            product_id=product_id,
            show_admin_details=show_admin_details,
            price_source_note=price_source_note_for(prod),
        )
    if prod.get("calc_type") == "brochure_menu":
        works = get_works()
        opts_by_size = {}
        for s in prod["sizes"]:
            sk = _brochure_size_key(s)
            preview = list(options_preview(prod, s))
            per_sheet = float(s["per_sheet"])
            seen = {p["name"] for p in preview}
            for opt in sort_product_options(prod.get("options") or []):
                if opt.get("scope") != "product" or opt["name"] in seen:
                    continue
                o = dict(opt)
                cost_add, price_add = compute_option(o, prod, works, per_sheet)
                preview.append({
                    "name": opt["name"],
                    "cost_add": round(cost_add, 4),
                    "price_add": round(price_add, 2),
                })
            opts_by_size[sk] = preview
        return render_template(
            "calculator_brochure.html",
            product=ui_product,
            product_id=product_id,
            opts_by_size=opts_by_size,
            show_admin_details=show_admin_details,
            option_kinds=get_option_kinds(),
        )
    opts_by_size = {s["name"]: options_preview(prod, s) for s in prod["sizes"]}
    calendar_hub = get_calendar_hub() if prod.get("calendar_group") == "kalendari" else None
    offset_hub = get_offset_hub() if prod.get("offset_group") == "ofset" else None
    papki_hub = get_papki_hub() if prod.get("papki_group") == "papki" else None
    return render_template(
        "calculator.html",
        product=ui_product,
        product_id=product_id,
        opts_by_size=opts_by_size,
        show_admin_details=show_admin_details,
        option_kinds=get_option_kinds(),
        calendar_hub=calendar_hub,
        offset_hub=offset_hub,
        papki_hub=papki_hub,
        papki_hub_ids=list(PAPKI_HUB_IDS),
        price_source_note=price_source_note_for(prod),
    )


@app.route("/api/plotter/layout", methods=["POST"])
@login_required
def api_plotter_layout():
    """Раскладка и тиражи для произвольного размера (сквозная резка)."""
    try:
        payload = request.get_json(force=True)
        products = get_products()
        prod = products.get(payload.get("product") or "stickery_plotter")
        if not prod or prod.get("calc_type") != "plotter_grid":
            raise ValueError("Продукт не найден.")
        width_mm = int(payload.get("width_mm", 0))
        height_mm = int(payload.get("height_mm", 0))
        material = _plotter_material_by_name(prod, payload.get("material"))
        sw, sh = _plotter_sheet_dims(prod)
        margin = _plotter_layout_margin(prod, material)
        per_sheet = compute_plotter_per_sheet(width_mm, height_mm, sw, sh, margin)
        qty_tiers = compute_plotter_qty_tiers(per_sheet)
        return jsonify({
            "ok": True,
            "per_sheet": per_sheet,
            "qty_tiers": qty_tiers,
            "size_label": f"{width_mm}×{height_mm} мм",
            "layout_margin_mm": margin,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/calc", methods=["POST"])
@login_required
def api_calc():
    try:
        payload = request.get_json(force=True)
        product_id = payload.get("product") or "vizitki"
        r = calc_product(product_id, payload)
        r["text"] = result_to_text(r)
        record_user_calc(session.get("user"))
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/save_history", methods=["POST"])
@login_required
def api_save_history():
    payload = request.get_json(force=True)
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "Укажите название расчёта."}), 400
    result = payload.get("result")
    if not result:
        return jsonify({"ok": False, "error": "Нет данных расчёта."}), 400
    h = get_history()
    h.insert(0, {
        "user": session.get("user"),
        "when": datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "result": result,
    })
    save_history(h[:200])
    return jsonify({"ok": True})


@app.route("/history")
@login_required
def history():
    u = current_user()
    h = get_history()
    if u.get("role") != "admin":
        h = [x for x in h if x.get("user") == u["username"]]
    ru = real_user()
    show_admin_details = bool(
        ru and ru.get("role") == "admin" and session.get("view_as") != "employee"
    )
    return render_template("history.html", history=h, show_admin_details=show_admin_details)


@app.route("/api/news/unread")
@login_required
def api_news_unread():
    uname = session.get("user")
    items = get_unread_news(uname)
    return jsonify({
        "ok": True,
        "items": [
            {
                "id": n["id"],
                "title": n.get("title", ""),
                "body": n.get("body", ""),
                "created_at": n.get("created_at"),
            }
            for n in items
        ],
    })


@app.route("/api/news/dismiss", methods=["POST"])
@login_required
def api_news_dismiss():
    payload = request.get_json(force=True) or {}
    news_id = (payload.get("news_id") or "").strip()
    if not news_id:
        return jsonify({"ok": False, "error": "Не указана новость."}), 400
    mark_news_read(session.get("user"), news_id)
    return jsonify({"ok": True})


# ----------------------------------------------------------------------------
# Маршруты: админка
# ----------------------------------------------------------------------------

@app.route("/admin/stats")
@admin_required
def admin_stats():
    stats_rows, login_log = build_user_stats_rows()
    totals = {
        "logins": sum(r["login_count"] for r in stats_rows),
        "calcs": sum(r["calc_count"] for r in stats_rows),
        "saved": sum(r["saved_count"] for r in stats_rows),
    }
    return render_template(
        "admin_stats.html",
        stats_rows=stats_rows,
        recent_logins=login_log[:80],
        totals=totals,
    )


@app.route("/admin/offset")
@admin_required
def admin_offset():
    products = get_products()
    rows = offset_registry_status(products)
    return render_template("admin_offset.html", rows=rows, registry=load_registry(), products=get_products())


@app.route("/admin/offset/toggle", methods=["POST"])
@admin_required
def admin_offset_toggle():
    """Включить/отключить калькулятор офсета или бумажных пакетов."""
    pid = (request.form.get("product_id") or "").strip()
    products = get_products()
    prod = products.get(pid)
    if not prod:
        flash("Продукт не найден.", "error")
        return redirect(url_for("admin_offset"))
    entry = registry_entry(pid)
    currently_disabled = bool(entry and entry.get("status") == "disabled")
    set_registry_status(
        pid,
        "active" if currently_disabled else "disabled",
        title=prod.get("title", pid),
        tab=prod.get("offset_tab")
        or ("Бумажные пакеты" if prod.get("calc_type") == "paper_bags" else ""),
    )
    state = "включён" if currently_disabled else "отключён"
    flash(f"Калькулятор «{prod.get('title', pid)}» {state}.", "success")
    return redirect(url_for("admin_offset"))


@app.route("/admin/news", methods=["GET", "POST"])
@admin_required
def admin_news():
    items = get_news_items()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "create":
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            if not title or not body:
                flash("Укажите заголовок и текст новости.", "error")
            else:
                items.insert(0, {
                    "id": _new_news_id(),
                    "title": title,
                    "body": body,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "active": True,
                })
                save_news_items(items)
                flash("Новость опубликована.", "ok")
            return redirect(url_for("admin_news"))
        if action == "update":
            news_id = (request.form.get("news_id") or "").strip()
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            item = next((n for n in items if n.get("id") == news_id), None)
            if not item:
                flash("Новость не найдена.", "error")
            elif not title or not body:
                flash("Укажите заголовок и текст.", "error")
            else:
                item["title"] = title
                item["body"] = body
                save_news_items(items)
                flash("Новость сохранена.", "ok")
            return redirect(url_for("admin_news"))
        if action == "toggle":
            news_id = (request.form.get("news_id") or "").strip()
            item = next((n for n in items if n.get("id") == news_id), None)
            if item:
                item["active"] = not item.get("active", True)
                save_news_items(items)
                flash("Статус новости обновлён.", "ok")
            return redirect(url_for("admin_news"))
        if action == "delete":
            news_id = (request.form.get("news_id") or "").strip()
            items = [n for n in items if n.get("id") != news_id]
            save_news_items(items)
            flash("Новость удалена.", "ok")
            return redirect(url_for("admin_news"))
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return render_template("admin_news.html", news_items=items)


@app.route("/admin")
@admin_required
def admin():
    users = get_users()
    pending_users = {
        uname: u for uname, u in users.items()
        if user_status(u) == USER_STATUS_PENDING
    }
    active_users = {
        uname: u for uname, u in users.items()
        if user_status(u) == USER_STATUS_APPROVED
    }
    return render_template(
        "admin.html",
        settings=get_settings(),
        materials=get_materials(),
        works=get_works(),
        users=active_users,
        pending_users=pending_users,
        products=get_products(),
    )


@app.route("/admin/view-as/employee", methods=["POST"])
@admin_required
def admin_view_as_employee():
    session["view_as"] = "employee"
    flash("Режим просмотра: вы видите сайт как сотрудник.", "ok")
    return redirect(url_for("calculator"))


def _safe_next_url(fallback):
    nxt = (request.form.get("next") or request.referrer or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return fallback


@app.route("/admin/view-as", methods=["POST"])
@login_required
def admin_view_as_switch():
    u = real_user()
    if not u or u.get("role") != "admin":
        flash("Доступ только для администратора.", "error")
        return redirect(url_for("calculator"))
    mode = (request.form.get("mode") or "admin").strip()
    if mode == "employee":
        session["view_as"] = "employee"
        flash("Режим просмотра: вы видите сайт как сотрудник.", "ok")
        return redirect(_safe_next_url(url_for("calculator")))
    session.pop("view_as", None)
    flash("Режим администратора.", "ok")
    return redirect(_safe_next_url(url_for("admin")))


@app.route("/admin/view-as/admin", methods=["POST"])
@login_required
def admin_view_as_admin():
    u = real_user()
    if not u or u.get("role") != "admin":
        flash("Доступ только для администратора.", "error")
        return redirect(url_for("calculator"))
    session.pop("view_as", None)
    flash("Вы вернулись в режим администратора.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/formulas")
@admin_required
def admin_formulas():
    products = get_products()
    return render_template("admin_formulas_list.html", products=products)


@app.route("/admin/formulas/<product_id>", methods=["GET", "POST"])
@admin_required
def admin_formulas_product(product_id):
    products = get_products()
    prod = products.get(product_id)
    if not prod:
        flash("Продукт не найден.", "error")
        return redirect(url_for("admin_formulas"))
    if request.method == "POST":
        errors = apply_formula_form(product_id, request.form)
        if errors:
            for err in errors[:6]:
                flash(err, "error")
        else:
            flash("Параметры формул сохранены.", "ok")
        return redirect(url_for("admin_formulas_product", product_id=product_id))
    works = get_works()
    docs = build_formula_docs(prod, works)
    return render_template("admin_formulas.html", product=prod, docs=docs)


def _recompute_works(hour_cost):
    """Пересчитывает стоимость работ из норматива при изменении стоимости часа."""
    works = get_works()
    for key, wk in works.items():
        norm = float(wk.get("norm_per_min") or 0)
        if norm > 0:
            wk["cost"] = round((hour_cost / 60.0) / norm, 6)
    save_json("works.json", works)


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    s = get_settings()
    s["rate_usd"] = float(request.form.get("rate_usd") or s["rate_usd"])
    s["rate_eur"] = float(request.form.get("rate_eur") or s["rate_eur"])
    new_hour = float(request.form.get("hour_cost") or s["hour_cost"])
    hour_changed = new_hour != s["hour_cost"]
    s["hour_cost"] = new_hour
    save_json("settings.json", s)
    if hour_changed:
        _recompute_works(new_hour)
        flash("Настройки сохранены, стоимость работ пересчитана.", "ok")
    else:
        flash("Настройки сохранены.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/works", methods=["GET", "POST"])
@admin_required
def admin_works():
    works = get_works()
    settings = get_settings()
    if request.method == "POST":
        if request.form.get("action") == "add_work":
            try:
                add_work_type(
                    request.form.get("new_name"),
                    request.form.get("new_norm"),
                )
                flash("Вид работы добавлен.", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_works"))
        hour_cost = float(settings.get("hour_cost", 440))
        for key, wk in works.items():
            norm = request.form.get(f"norm_{key}")
            if norm is not None and norm != "":
                wk["norm_per_min"] = float(norm)
                if float(norm) > 0:
                    wk["cost"] = round((hour_cost / 60.0) / float(norm), 6)
        save_json("works.json", works)
        flash("Виды работ сохранены.", "ok")
        return redirect(url_for("admin_works"))
    return render_template("admin_works.html", works=works, settings=settings)


@app.route("/admin/product", methods=["GET", "POST"])
@admin_required
def admin_product():
    if request.method == "POST":
        if request.form.get("action") == "add_material":
            try:
                add_catalog_material(
                    request.form.get("new_name"),
                    request.form.get("new_price"),
                    custom_price_a3=bool(request.form.get("new_custom")),
                )
                flash("Материал добавлен в каталог. Подключите его в «Привязка материалов».", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_product"))
        print_4_0 = float(request.form.get("print_4_0") or get_settings().get("print_4_0", 0))
        print_1_0 = float(request.form.get("print_1_0") or get_settings().get("print_1_0", 0))
        xerox_print_4_0 = float(request.form.get("xerox_print_4_0") or get_settings().get("xerox_print_4_0", 0))
        xerox_print_1_0 = float(request.form.get("xerox_print_1_0") or get_settings().get("xerox_print_1_0", 0))
        materials = get_materials()
        for i, mt in enumerate(materials):
            v = request.form.get(f"mat_price_{i}")
            if v is not None and v != "":
                mt["price_a3"] = float(v)
        save_materials(materials)
        save_print_costs_and_sync(
            print_4_0=print_4_0,
            print_1_0=print_1_0,
            xerox_print_4_0=xerox_print_4_0,
            xerox_print_1_0=xerox_print_1_0,
        )
        flash("Материалы и печать сохранены.", "ok")
        return redirect(url_for("admin_product"))
    settings = get_settings()
    materials = get_admin_materials()
    return render_template(
        "admin_product.html",
        materials=list(enumerate(materials)),
        print_4_0=settings.get("print_4_0", 12.32),
        print_1_0=settings.get("print_1_0", 4.0),
        xerox_print_4_0=settings.get("xerox_print_4_0", 42.9),
        xerox_print_1_0=settings.get("xerox_print_1_0", 13.2),
    )


@app.route("/admin/bindings", methods=["GET", "POST"])
@admin_required
def admin_bindings():
    products = get_products()
    catalog = get_materials()
    if request.method == "POST":
        for pid, prod in products.items():
            labels = prod.get("markup_profile_labels") or {"default": "Наценка"}
            default_profile = next(iter(labels))
            refs = []
            for mat in catalog:
                mid = mat["id"]
                if request.form.get(f"bind_{pid}_{mid}"):
                    prof = request.form.get(f"profile_{pid}_{mid}", default_profile)
                    if prof not in labels:
                        prof = default_profile
                    refs.append({"id": mid, "markup_profile": prof})
            prod["material_refs"] = refs
            prod.pop("materials", None)
        save_json("products.json", products)
        flash("Привязки материалов сохранены.", "ok")
        return redirect(url_for("admin_bindings"))
    return render_template(
        "admin_bindings.html",
        catalog=catalog,
        binding_products=build_binding_products(),
    )


@app.route("/admin/markup", methods=["GET", "POST"])
@admin_required
def admin_markup():
    products = get_products()
    if request.method == "POST":
        _apply_grouped_markup_from_form(products)
        save_json("products.json", products)
        flash("Наценки сохранены.", "ok")
        return redirect(url_for("admin_markup"))
    return render_template("admin_markup.html", markup_products=build_markup_products())


@app.route("/admin/options", methods=["GET", "POST"])
@admin_required
def admin_options():
    films = get_shared_films()
    products = get_products()
    works = get_works()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "add_film":
            try:
                add_option_film(request.form.get("new_film_name"), request.form.get("new_film_price"))
                flash("Плёнка добавлена.", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_options"))
        if action == "add_kind":
            try:
                key, spec = add_option_kind(
                    request.form.get("kind_label"),
                    request.form.get("kind_ui"),
                    request.form.get("kind_formula"),
                )
                flash(f"Тип опции «{spec['label']}» добавлен (ключ: {key}).", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_options"))
        if action == "add_plotter_lam":
            try:
                name = add_plotter_lamination(
                    request.form.get("pl_product") or "stickery_plotter",
                    request.form.get("pl_name"),
                    request.form.get("pl_film"),
                    request.form.get("pl_work"),
                )
                flash(f"Ламинация плоттера «{name}» добавлена.", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_options"))
        if action == "remove_plotter_lam":
            try:
                remove_plotter_lamination(
                    request.form.get("pl_product") or "stickery_plotter",
                    request.form.get("pl_name"),
                )
                flash("Ламинация плоттера удалена.", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_options"))
        if action == "add_option":
            try:
                added = add_product_option(
                    request.form.get("opt_name"),
                    request.form.get("opt_kind"),
                    request.form.getlist("opt_product"),
                    {
                        "film": request.form.get("opt_film"),
                        "foil": request.form.get("opt_foil"),
                        "work": request.form.get("opt_work"),
                        "count": request.form.get("opt_count"),
                        "count_min": request.form.get("opt_count_min"),
                        "count_max": request.form.get("opt_count_max"),
                        "print_sides": request.form.get("opt_print_sides"),
                        "surcharge": request.form.get("opt_surcharge"),
                        "price_factor": request.form.get("opt_price_factor"),
                        "cost_add": request.form.get("opt_cost_add"),
                    },
                )
                flash("Опция добавлена: " + ", ".join(added) + ".", "ok")
            except (ValueError, TypeError) as e:
                flash(str(e), "error")
            return redirect(url_for("admin_options"))
        for key in films:
            fv = request.form.get(f"film_{key}")
            if fv is not None and fv != "":
                films[key]["a3"] = float(fv)
        save_films_and_sync(films)
        flash("Стоимости опций сохранены.", "ok")
        return redirect(url_for("admin_options"))
    return render_template(
        "admin_options.html",
        films=films,
        works=works,
        products=products,
        option_kinds=get_option_kinds(),
        plotter_laminations=build_plotter_lamination_admin(),
        preview_products=build_options_preview_by_product(),
    )


@app.route("/admin/users/<uname>/approve", methods=["POST"])
@admin_required
def admin_user_approve(uname):
    users = get_users()
    if uname not in users:
        return redirect(url_for("admin"))
    role = (request.form.get("role") or "employee").strip()
    if role not in ("employee", "admin"):
        role = "employee"
    users[uname]["status"] = USER_STATUS_APPROVED
    users[uname]["role"] = role
    users[uname]["approved_at"] = datetime.now().isoformat(timespec="seconds")
    save_users(users)
    flash(f"Пользователь «{uname}» одобрен (роль: {role}).", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<uname>/reject", methods=["POST"])
@admin_required
def admin_user_reject(uname):
    users = get_users()
    if uname in users and uname != session.get("user"):
        users[uname]["status"] = USER_STATUS_REJECTED
        save_users(users)
        flash(f"Заявка «{uname}» отклонена.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<uname>/role", methods=["POST"])
@admin_required
def admin_user_role(uname):
    users = get_users()
    new_role = request.form.get("role", "employee")
    if uname not in users:
        return redirect(url_for("admin"))
    if user_status(users[uname]) != USER_STATUS_APPROVED:
        flash("Сначала одобрите заявку пользователя.", "error")
        return redirect(url_for("admin"))
    if uname == session.get("user"):
        flash("Нельзя менять собственную роль — иначе можно потерять доступ к админке. "
              "Назначьте админом другого сотрудника, и он сменит вашу роль.", "error")
        return redirect(url_for("admin"))
    # не оставляем систему без администраторов
    if new_role != "admin":
        admins = [u for u, d in users.items() if d.get("role") == "admin" and u != uname]
        if not admins:
            flash("Нельзя понизить последнего администратора.", "error")
            return redirect(url_for("admin"))
    users[uname]["role"] = new_role
    save_users(users)
    flash("Роль обновлена.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<uname>/delete", methods=["POST"])
@admin_required
def admin_user_delete(uname):
    users = get_users()
    if uname in users and uname != session.get("user"):
        users.pop(uname)
        save_users(users)
        flash("Пользователь удалён.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<uname>/reset-password", methods=["POST"])
@admin_required
def admin_user_reset_password(uname):
    """Выпускает одноразовый код восстановления пароля для пользователя."""
    if uname not in get_users():
        flash("Пользователь не найден.", "error")
        return redirect(url_for("admin"))
    token = issue_password_reset_token(uname)
    link = url_for("reset_password", _external=True)
    return render_template(
        "admin_reset_link.html",
        username=uname,
        token=token,
        link=f"{link}?token={token}",
    )


# ----------------------------------------------------------------------------
# Запуск
# ----------------------------------------------------------------------------

def open_browser():
    webbrowser.open("http://127.0.0.1:5000")


def _port_in_use(host, port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


if __name__ == "__main__":
    ensure_runtime_data()
    ensure_admin()
    # По умолчанию 0.0.0.0:80 (Amvera). Локально Запустить.bat задаёт HOST=127.0.0.1 PORT=5000.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "80"))
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"\nПапка проекта: {APP_DIR}")
    print(f"Данные (DATA_DIR): {DATA_DIR}")
    if "AMVERA" in os.environ:
        print("Окружение Amvera: пользователи и статистика в /data (постоянное хранилище).")
    illus = os.path.join(APP_DIR, "templates", "_quarterly_calendar_illus.html")
    if os.path.isfile(illus):
        with open(illus, encoding="utf-8") as f:
            for line in f:
                if "quarterly-illus-v" in line:
                    print(f"Шаблон иллюстраций: {line.strip()}")
                    break
    if _port_in_use(host, port):
        print(f"\n[!] Порт {port} всё ещё занят — закройте другое окно Python и запустите снова.")
        raise SystemExit(1)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{browser_host}:{port}")).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)

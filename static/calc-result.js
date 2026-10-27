(function (global) {
  function rub(n) {
    return Number(n).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽";
  }

  function rubDiscount(n) {
    return "−" + Number(n).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽";
  }

  function discountBlockHtml(r) {
    const pct = r.discount_pct || 0;
    const pctLabel = pct ? "−" + pct + "%" : "0%";
    return (
      '<div class="res-block">' +
        '<div class="res-row section-title"><span>Размер скидки</span><span>' + pctLabel + "</span></div>" +
        '<div class="res-row"><span>За 1 шт</span><span>' + rubDiscount(r.discount_per_item || 0) + "</span></div>" +
        '<div class="res-row"><span>За тираж</span><span>' + rubDiscount(r.discount_total || 0) + "</span></div>" +
      "</div>"
    );
  }

  function optionsBlockHtml(options) {
    if (!options || !options.length) return "";
    const rows = options
      .map(
        (o) =>
          '<div class="res-row res-option-item">' +
            "<span>" + o.name + "</span>" +
            "<span>+" + Number(o.price_add).toFixed(2) + " ₽" +
            (o.order_level ? " <span class=\"muted small\">(к заказу)</span>" : "") +
            "</span>" +
          "</div>"
      )
      .join("");
    return (
      '<div class="res-block res-options-block">' +
        '<div class="res-row section-title"><span>Опции</span><span></span></div>' +
        rows +
      "</div>"
    );
  }

  function renderResultHtml(r, showAdminDetails) {
    const options = r.options || [];
    const qty = r.qty || 0;
    const hasQty = r.qty_specified !== false;
    const optionsHtml = optionsBlockHtml(options);

    let adminHtml = "";
    let brochureBreakdown = "";
    if (showAdminDetails) {
      if (r.calc_type === "brochure_menu") {
        adminHtml =
          '<div class="res-block">' +
            '<div class="res-row"><span>Обложка (себ.)</span><span>' + rub(r.cover_seb || 0) + " · " + (r.cover_mult || 0) + " стр.</span></div>" +
            '<div class="res-row"><span>Страницы (себ.)</span><span>' + rub(r.pages_seb || 0) + " · " + (r.pages_mult || 0) + " стр.</span></div>" +
            '<div class="res-row"><span>Крепление (себ.)</span><span>' + rub(r.binding_cost || 0) + "</span></div>" +
            '<div class="res-row"><span>Себестоимость 1 шт</span><span>' + rub(r.cost) + "</span></div>" +
            '<div class="res-row"><span>Наценка (тираж)</span><span>×' + r.markup + "</span></div>" +
            '<div class="res-row"><span>Обложка (цена)</span><span>' + rub(r.cover_retail || 0) + "</span></div>" +
            '<div class="res-row"><span>Страницы (цена)</span><span>' + rub(r.pages_retail || 0) + "</span></div>" +
            '<div class="res-row"><span>Крепление (цена)</span><span>' + rub(r.binding_retail || 0) + "</span></div>" +
            '<div class="res-row"><span>Базовая цена 1 шт</span><span>' + rub(r.base_price) + "</span></div>" +
          "</div>" +
          optionsHtml +
          '<div class="res-block muted-block">' +
            '<div class="res-row"><span>Прибыль с 1 шт</span><span>' + rub(r.margin_per_item || 0) + "</span></div>" +
            '<div class="res-row"><span>Прибыль с тиража</span><span>' + rub(r.margin_total || 0) + "</span></div>" +
          "</div>";
      } else {
        adminHtml =
          '<div class="res-block">' +
            '<div class="res-row"><span>Себестоимость 1 шт</span><span>' + rub(r.cost) + "</span></div>" +
            '<div class="res-row"><span>Наценка (тираж)</span><span>×' + r.markup + "</span></div>" +
            '<div class="res-row"><span>Базовая цена 1 шт</span><span>' + rub(r.base_price) + "</span></div>" +
          "</div>" +
          optionsHtml +
          '<div class="res-block muted-block">' +
            '<div class="res-row"><span>Прибыль с 1 шт</span><span>' + rub(r.margin_per_item || 0) + "</span></div>" +
            '<div class="res-row"><span>Прибыль с тиража</span><span>' + rub(r.margin_total || 0) + "</span></div>" +
          "</div>";
      }
    }

    if (!showAdminDetails && r.calc_type === "brochure_menu") {
      brochureBreakdown =
        '<div class="res-block">' +
          '<div class="res-row"><span>Обложка</span><span>' + rub(r.cover_retail || 0) + "</span></div>" +
          '<div class="res-row"><span>Страницы</span><span>' + rub(r.pages_retail || 0) + "</span></div>" +
          '<div class="res-row"><span>Крепление</span><span>' + rub(r.binding_retail || 0) + "</span></div>" +
        "</div>";
    }

    const detailsBlock = !showAdminDetails ? optionsHtml : "";

    let paramsRow;
    if (r.calc_type === "plotter_grid") {
      const perSheet = r.per_sheet ? r.per_sheet + " на листе · " : "";
      let extra = "";
      if (r.lamination && r.lamination !== "Без ламинации") {
        extra = " · " + r.lamination;
      }
      if (r.print_mode) {
        extra = " · " + r.print_mode + extra;
      }
      paramsRow =
        '<div class="res-row"><span>Тираж</span><span>' + perSheet + qty.toLocaleString("ru-RU") + " шт" + extra + "</span></div>";
    } else if (r.calc_type === "notebook") {
      let extra = r.block_mode || r.print_mode || "";
      if (r.page_count) extra += " · " + r.page_count + " л.";
      paramsRow =
        '<div class="res-row"><span>Блок / тираж</span><span>' + extra + " · " + qty.toLocaleString("ru-RU") + " шт</span></div>";
    } else if (r.calc_type === "brochure_menu") {
      paramsRow =
        '<div class="res-row"><span>Страниц / тираж</span><span>' +
        r.page_count + " стр. · " + qty.toLocaleString("ru-RU") + " шт</span></div>";
    } else if (r.calc_type === "offset_table") {
      paramsRow =
        '<div class="res-row"><span>Материал</span><span>' + (r.material || "—") + "</span></div>" +
        '<div class="res-row"><span>Цветность / тираж</span><span>' +
        (r.print_mode_label || r.print_mode) + " · " + qty.toLocaleString("ru-RU") + " шт</span></div>" +
        (r.lamination_label
          ? '<div class="res-row"><span>Ламинация</span><span>' + r.lamination_label + "</span></div>"
          : "") +
        (r.delivery_label
          ? '<div class="res-row"><span>Срок</span><span>' + r.delivery_label + "</span></div>"
          : "");
    } else if (r.calc_type === "pvd_bags") {
      paramsRow =
        '<div class="res-row"><span>Материал</span><span>' + (r.material || "—") + "</span></div>" +
        '<div class="res-row"><span>Цветность / тираж</span><span>' +
        (r.print_mode_label || r.print_mode) + " · " + qty.toLocaleString("ru-RU") + " шт</span></div>" +
        (r.billing_note
          ? '<div class="res-row muted"><span></span><span>' + r.billing_note + "</span></div>"
          : "");
    } else if (r.calc_type === "paper_bags") {
      paramsRow =
        (r.delivery_label
          ? '<div class="res-row"><span>Срок</span><span>' + r.delivery_label + "</span></div>"
          : "") +
        '<div class="res-row"><span>Тираж</span><span>' + qty.toLocaleString("ru-RU") + " шт</span></div>";
    } else if (r.calc_type === "wide_format") {
      let extra = (r.resolution_label || r.resolution + " dpi") +
        " · " + (r.area_sqm || 0).toLocaleString("ru-RU", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " м²";
      if (r.lamination) extra += " · " + r.lamination;
      if (r.min_order_applied) extra += " · мин. заказ";
      paramsRow =
        '<div class="res-row"><span>Материал</span><span>' + (r.material || "—") + "</span></div>" +
        '<div class="res-row"><span>Разрешение / расход</span><span>' + extra + "</span></div>" +
        '<div class="res-row"><span>Тираж</span><span>' + qty.toLocaleString("ru-RU") + " шт</span></div>";
    } else if (r.calc_type === "branded_cups" || r.product_id === "brendirovannye_stakanchiki") {
      paramsRow =
        '<div class="res-row"><span>Тираж</span><span>' + qty.toLocaleString("ru-RU") + " шт</span></div>";
    } else if (r.calc_type === "board_cover") {
      const fmtNum = function (n) {
        return Number(n).toLocaleString("ru-RU", { maximumFractionDigits: 1 });
      };
      let csRows = "";
      if (r.custom_size) {
        const cs = r.custom_size;
        csRows =
          '<div class="res-row"><span>Разложенный размер</span><span>' + fmtNum(cs.width) + " × " + fmtNum(cs.height) + " мм</span></div>" +
          '<div class="res-row"><span>' + (cs.base_label || "База расчёта") + "</span><span>" + cs.base_format + "</span></div>" +
          (cs.parts > 1
            ? '<div class="res-row"><span>Частей картона</span><span>' + cs.parts + " (часть " + fmtNum(cs.part_width) + " × " + fmtNum(cs.part_height) + " мм)</span></div>"
            : "");
      }
      paramsRow =
        csRows +
        '<div class="res-row"><span>Количество обложек</span><span>' + (r.board_covers || 1) + "</span></div>" +
        (r.lamination
          ? '<div class="res-row"><span>Ламинация</span><span>' + r.lamination + "</span></div>"
          : "") +
        (r.mount
          ? '<div class="res-row"><span>Крепление</span><span>' + r.mount + "</span></div>"
          : "") +
        '<div class="res-row"><span>Тираж</span><span>' +
        (r.qty_specified === false ? "не указан" : qty.toLocaleString("ru-RU") + " шт") +
        "</span></div>";
    } else {
      const printLabel = r.print_mode || r.print_mode_label || "—";
      paramsRow =
        '<div class="res-row"><span>Печать / тираж</span><span>' + printLabel + " · " + qty.toLocaleString("ru-RU") + " шт</span></div>";
    }

    return (
      '<div class="res-block">' +
        '<div class="res-row"><span>Продукт</span><span>' + r.product + " · " + r.size + "</span></div>" +
        (r.calc_type === "brochure_menu"
          ? '<div class="res-row"><span>Обложка</span><span>' + r.cover_material + " · " + r.cover_print + "</span></div>" +
            '<div class="res-row"><span>Страницы</span><span>' + r.pages_material + " · " + r.pages_print + "</span></div>" +
            '<div class="res-row"><span>Переплёт</span><span>' + (r.binding_label || r.binding) + "</span></div>" +
            (r.binding_position_label
              ? '<div class="res-row"><span>Крепление</span><span>' + r.binding_position_label + "</span></div>"
              : "")
          : r.calc_type === "offset_table" || r.calc_type === "pvd_bags" || r.calc_type === "wide_format" || r.calc_type === "paper_bags"
          ? ""
          : r.calc_type === "branded_cups" || r.product_id === "brendirovannye_stakanchiki"
          ? '<div class="res-row"><span>Способ нанесения</span><span>' + (r.material || "—") + "</span></div>"
          : '<div class="res-row"><span>Материал</span><span>' + (r.material || "—") +
            (r.custom_material_price ? ", " + rub(r.material_price_a3) + "/А3" : "") +
            "</span></div>") +
        paramsRow +
      "</div>" +
      adminHtml +
      brochureBreakdown +
      detailsBlock +
      discountBlockHtml(r) +
      (r.punch_per_sheet != null
        ? '<div class="res-block punch-note">Не забудьте добавить к внутренним листам стоимость дырокола - ' + Number(r.punch_per_sheet).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' р за лист</div>'
        : "") +
      discountBlockHtml(r) +
      '<div class="res-block highlight">' +
        '<div class="res-row big"><span>Цена за шт</span><span>' + rub(r.price_after_discount) + "</span></div>" +
        (hasQty
          ? '<div class="res-row big total"><span>ИТОГО за тираж</span><span>' + rub(r.total) + "</span></div>"
          : '<div class="res-row muted"><span>Тираж</span><span>не указан</span></div>') +
      "</div>"
    );
  }

  global.CalcResult = {
    rub: rub,
    rubDiscount: rubDiscount,
    renderResultHtml: renderResultHtml,
  };
})(window);

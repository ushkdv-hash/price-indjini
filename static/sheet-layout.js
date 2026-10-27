(function () {
  "use strict";

  var MAX_DRAW_ITEMS = 200;

  function toInt(value) {
    var n = parseInt(String(value).replace(",", "."), 10);
    return Number.isFinite(n) ? n : 0;
  }

  function layoutCount(usableW, usableH, itemW, itemH) {
    if (itemW <= 0 || itemH <= 0 || usableW <= 0 || usableH <= 0) {
      return { cols: 0, rows: 0, count: 0 };
    }
    var cols = Math.floor(usableW / itemW);
    var rows = Math.floor(usableH / itemH);
    return { cols: cols, rows: rows, count: Math.max(0, cols * rows) };
  }

  function computeLayout(sheet, itemW, itemH, cutMargin) {
    var usableW = sheet.sheet_w - sheet.margin_left - sheet.margin_right;
    var usableH = sheet.sheet_h - sheet.margin_top - sheet.margin_bottom;
    var stepW = itemW + cutMargin;
    var stepH = itemH + cutMargin;

    var a = layoutCount(usableW, usableH, stepW, stepH);
    var b = layoutCount(usableW, usableH, stepH, stepW);
    var useRotated = b.count > a.count;
    var best = useRotated ? b : a;

    return {
      count: best.count,
      cols: best.cols,
      rows: best.rows,
      rotated: useRotated,
      usableW: usableW,
      usableH: usableH,
      itemW: useRotated ? itemH : itemW,
      itemH: useRotated ? itemW : itemH,
      cutMargin: cutMargin,
      sheet: sheet,
    };
  }

  function pct(value, total) {
    if (!total) return "0%";
    return (value / total) * 100 + "%";
  }

  function renderPreview(el, result) {
    if (!el) return;
    el.innerHTML = "";

    if (!result || !result.sheet) {
      el.classList.add("is-empty");
      el.textContent = "Не помещается";
      return;
    }

    var sheet = result.sheet;
    el.classList.remove("is-empty");
    el.style.aspectRatio = sheet.sheet_w + " / " + sheet.sheet_h;

    var margin = document.createElement("div");
    margin.className = "sheet-canvas-margin";
    margin.style.left = pct(sheet.margin_left, sheet.sheet_w);
    margin.style.top = pct(sheet.margin_top, sheet.sheet_h);
    margin.style.width = pct(result.usableW, sheet.sheet_w);
    margin.style.height = pct(result.usableH, sheet.sheet_h);
    el.appendChild(margin);

    if (result.count <= 0 || result.cols <= 0 || result.rows <= 0) {
      var empty = document.createElement("div");
      empty.className = "sheet-canvas-empty-msg";
      empty.textContent = "Не помещается";
      el.appendChild(empty);
      return;
    }

    var stepW = result.itemW + result.cutMargin;
    var stepH = result.itemH + result.cutMargin;
    var drawn = 0;

    for (var row = 0; row < result.rows; row++) {
      for (var col = 0; col < result.cols; col++) {
        if (drawn >= MAX_DRAW_ITEMS) break;
        var item = document.createElement("div");
        item.className = "sheet-canvas-item";
        item.style.left = pct(sheet.margin_left + col * stepW, sheet.sheet_w);
        item.style.top = pct(sheet.margin_top + row * stepH, sheet.sheet_h);
        item.style.width = pct(result.itemW, sheet.sheet_w);
        item.style.height = pct(result.itemH, sheet.sheet_h);
        el.appendChild(item);
        drawn += 1;
      }
      if (drawn >= MAX_DRAW_ITEMS) break;
    }

    if (result.count > MAX_DRAW_ITEMS) {
      var more = document.createElement("div");
      more.className = "sheet-canvas-more";
      more.textContent = "Показаны первые " + MAX_DRAW_ITEMS + " из " + result.count;
      el.appendChild(more);
    }
  }

  function readSheet(card) {
    var isCustom = card.dataset.customSheet === "1";
    var sheetWInput = card.querySelector('[data-field="sheet_w"]');
    var sheetHInput = card.querySelector('[data-field="sheet_h"]');
    return {
      sheet_w: isCustom && sheetWInput
        ? toInt(sheetWInput.value)
        : toInt(card.dataset.sheetW),
      sheet_h: isCustom && sheetHInput
        ? toInt(sheetHInput.value)
        : toInt(card.dataset.sheetH),
      margin_top: toInt(card.dataset.marginTop),
      margin_bottom: toInt(card.dataset.marginBottom),
      margin_left: toInt(card.dataset.marginLeft),
      margin_right: toInt(card.dataset.marginRight),
    };
  }

  function updateCard(card) {
    var sheet = readSheet(card);
    var itemW = toInt(card.querySelector('[data-field="item_w"]').value);
    var itemH = toInt(card.querySelector('[data-field="item_h"]').value);
    var cutMargin = toInt(card.querySelector('[data-field="cut_margin"]').value);
    var result = computeLayout(sheet, itemW, itemH, cutMargin);

    var countEl = card.querySelector("[data-result-count]");
    var detailEl = card.querySelector("[data-result-detail]");
    if (countEl) {
      countEl.textContent = result.count > 0 ? result.count + " шт." : "0 шт.";
    }
    if (detailEl) {
      if (result.count > 0) {
        detailEl.textContent =
          result.cols + " × " + result.rows +
          (result.rotated ? " (повёрнуто)" : "") +
          " · макет " + result.itemW + "×" + result.itemH + " мм";
      } else {
        detailEl.textContent = card.dataset.customSheet === "1"
          ? "Измените размер листа, изделия или поля под обрезку"
          : "Измените размер изделия или поля под обрезку";
      }
    }
    renderPreview(card.querySelector("[data-preview]"), result);
  }

  function init() {
    var cards = document.querySelectorAll("[data-sheet-layout]");
    cards.forEach(function (card) {
      card.querySelectorAll("input").forEach(function (input) {
        input.addEventListener("input", function () {
          updateCard(card);
        });
        input.addEventListener("change", function () {
          updateCard(card);
        });
      });
      updateCard(card);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

(function () {
  const product = JSON.parse(document.getElementById("product-data").textContent);
  const optsBySize = JSON.parse(document.getElementById("opts-by-size").textContent);
  const calcMeta = JSON.parse(document.getElementById("calc-meta").textContent);
  if (!Array.isArray(product.options)) product.options = [];
  if (!Array.isArray(product.materials)) product.materials = [];
  if (!Array.isArray(product.sizes)) product.sizes = [];

  const showAdminDetails = !!calcMeta.show_admin_details;
  const productId = calcMeta.product_id || "brosyury_menyu";
  const optionKinds = calcMeta.option_kinds || {};
  const optionByName = new Map(product.options.map((o) => [o.name, o]));
  const finishSelectKinds = product.finish_select_kinds || null;
  const laminationByBinding = product.lamination_by_binding || {};
  const sizeRules = product.size_availability_rules || [];

  const elBinding = document.getElementById("f-binding");
  const elOrientation = document.getElementById("f-orientation");
  const elBindingPosition = document.getElementById("f-binding-position");
  const elSize = document.getElementById("f-size");
  const elPageCount = document.getElementById("f-page-count");
  const elPageSplitNote = document.getElementById("page-split-note");
  const elQty = document.getElementById("f-qty");
  const elDiscount = document.getElementById("f-discount");
  const discountField =
    typeof DiscountField !== "undefined"
      ? DiscountField.init(document.querySelector(".discount-field"))
      : null;
  const elProductOptions = document.getElementById("f-product-options");

  const sections = [
    {
      prefix: "cover",
      elMat: document.getElementById("f-cover-material"),
      elPrint: document.getElementById("f-cover-print"),
      elLamination: document.getElementById("f-cover-lamination"),
      elOptions: document.getElementById("f-cover-options"),
    },
    {
      prefix: "pages",
      elMat: document.getElementById("f-pages-material"),
      elPrint: document.getElementById("f-pages-print"),
      elLamination: document.getElementById("f-pages-lamination"),
      elOptions: document.getElementById("f-pages-options"),
    },
  ];

  const resultBox = document.getElementById("result");
  const resultEmpty = document.getElementById("result-empty");
  const resultActions = document.getElementById("result-actions");
  const copyBtn = document.getElementById("copy-btn");
  const saveBtn = document.getElementById("save-btn");
  const saveModal = document.getElementById("save-modal");
  const saveTitle = document.getElementById("save-title");
  const saveConfirmBtn = document.getElementById("save-confirm-btn");

  let lastResult = null;
  let calcTimer = null;
  let calcSeq = 0;

  function optionFormula(kind) {
    return (optionKinds[kind] && optionKinds[kind].formula) || kind;
  }

  function optionScope(opt) {
    return opt.scope || "section";
  }

  function isFinishSelectOption(opt) {
    if (finishSelectKinds) return finishSelectKinds.includes(opt.kind);
    return optionKinds[opt.kind] && optionKinds[opt.kind].ui === "select";
  }

  function isLaminationOptionName(name) {
    const o = optionByName.get(name);
    return o ? optionFormula(o.kind) === "lamination" : false;
  }

  function isCountOption(opt) {
    return optionKinds[opt.kind] && optionKinds[opt.kind].ui === "count";
  }

  function laminationAllowedForBinding(optName) {
    const opt = optionByName.get(optName);
    if (!opt) return false;
    const allowedFilms = laminationByBinding[elBinding.value];
    if (!allowedFilms) return true;
    return allowedFilms.includes(opt.film);
  }

  const finishSelectNames = new Set(
    product.options.filter((o) => isFinishSelectOption(o)).map((o) => o.name)
  );
  const countOptionDefs = product.options.filter((o) => isCountOption(o) && optionScope(o) !== "product");
  const productOptionDefs = product.options.filter(
    (o) => optionScope(o) === "product" && !isFinishSelectOption(o) && !isCountOption(o)
  );

  function coverPages(printMode) {
    return printMode === "1+1" || printMode === "4+4" ? 4 : 2;
  }

  function ruleMatches(rule) {
    const when = rule.when || {};
    const ctx = {
      binding: elBinding.value,
      orientation: elOrientation.value,
      binding_position: elBindingPosition ? elBindingPosition.value : "left",
    };
    if (when.binding && when.binding !== ctx.binding) return false;
    if (when.orientation && when.orientation !== ctx.orientation) return false;
    if (when.binding_position && when.binding_position !== ctx.binding_position) return false;
    return true;
  }

  function sizeHiddenByRules(sizeName) {
    return sizeRules.some((rule) => {
      if (!ruleMatches(rule)) return false;
      return (rule.hide_size_names || []).includes(sizeName);
    });
  }

  function ensureSizesFromRules(sizes) {
    const byName = new Map(sizes.map((s) => [s.name, s]));
    sizeRules.forEach((rule) => {
      if (!ruleMatches(rule)) return;
      (rule.ensure_size_names || []).forEach((name) => {
        if (byName.has(name)) return;
        const extra = product.sizes.find((s) => s.binding === elBinding.value && s.name === name);
        if (extra) {
          byName.set(name, extra);
        }
      });
    });
    return Array.from(byName.values()).sort(
      (a, b) => formatRank(a) - formatRank(b) || a.name.localeCompare(b.name, "ru")
    );
  }

  function formatRank(size) {
    if (size.format_order != null) return size.format_order;
    const n = size.name || "";
    if (n.startsWith("А3")) return 0;
    if (n.startsWith("А4")) return 1;
    if (n.startsWith("А5")) return 2;
    if (n.startsWith("А6")) return 3;
    if (n.startsWith("А7")) return 4;
    if (n.startsWith("Евро")) return 5;
    return 99;
  }

  function filteredSizes() {
    const binding = elBinding.value;
    const orientation = elOrientation.value;
    const sizes = product.sizes
      .filter((s) => {
        if (s.binding !== binding) return false;
        if (binding === "spring" || s.ignore_orientation) return true;
        if (s.orientation !== orientation) return false;
        if (binding === "staple" && s.name.includes("А3")) return false;
        if (sizeHiddenByRules(s.name)) return false;
        return true;
      })
      .sort((a, b) => formatRank(a) - formatRank(b) || a.name.localeCompare(b.name, "ru"));
    return ensureSizesFromRules(sizes);
  }

  function currentSize() {
    const sizes = filteredSizes();
    return sizes.find((s) => s.name === elSize.value) || sizes[0] || null;
  }

  function sizeOptsKey(size) {
    return size.size_key || (size.ignore_orientation
      ? `${size.binding}|${size.name}`
      : `${size.binding}|${size.orientation}|${size.name}`);
  }

  function fillSizes() {
    const sizes = filteredSizes();
    const prev = elSize.value;
    elSize.innerHTML = "";
    sizes.forEach((s) => {
      const o = document.createElement("option");
      o.value = s.name;
      o.textContent = s.name;
      elSize.appendChild(o);
    });
    if (prev && sizes.some((s) => s.name === prev)) elSize.value = prev;
    else if (sizes.length) elSize.value = sizes[0].name;
  }

  function fillQty() {
    const size = currentSize();
    const tiers = size && size.qty_options && size.qty_options.length ? size.qty_options : [100];
    const prev = elQty.value;
    elQty.innerHTML = "";
    tiers.forEach((q) => {
      const o = document.createElement("option");
      o.value = q;
      o.textContent = q.toLocaleString("ru-RU");
      elQty.appendChild(o);
    });
    if (prev && tiers.some((q) => String(q) === String(prev))) elQty.value = prev;
    else if (tiers.length) elQty.value = String(tiers[0]);
  }

  function fillMaterials(elMat) {
    const prev = elMat.value;
    elMat.innerHTML = "";
    product.materials.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.name;
      o.textContent = m.name;
      elMat.appendChild(o);
    });
    if (prev && product.materials.some((m) => m.name === prev)) elMat.value = prev;
    else if (product.materials.length) elMat.value = product.materials[0].name;
  }

  function laminationFamily(name) {
    const n = name.toLowerCase();
    if (n.includes("софт")) return 2;
    if (n.includes("мат")) return 1;
    if (n.includes("глянц")) return 0;
    return 3;
  }

  function laminationMicrons(name) {
    const m = name.match(/(\d+)\s*мк/i);
    return m ? parseInt(m[1], 10) : 999;
  }

  function sortLamination(opts) {
    return opts.slice().sort((a, b) => {
      const fa = laminationFamily(a.name);
      const fb = laminationFamily(b.name);
      if (fa !== fb) return fa - fb;
      return laminationMicrons(a.name) - laminationMicrons(b.name);
    });
  }

  function materialForSection(section) {
    return product.materials.find((m) => m.name === section.elMat.value) || null;
  }

  function fillLamination(section) {
    const size = currentSize();
    if (!size) return;
    const mat = materialForSection(section);
    const excluded = new Set((mat && mat.excluded_options) || []);
    const opts = optsBySize[sizeOptsKey(size)] || [];
    const prev = section.elLamination.value;
    section.elLamination.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = product.finish_none_label || "Без ламинации";
    section.elLamination.appendChild(none);
    sortLamination(
      opts.filter(
        (o) =>
          isLaminationOptionName(o.name) &&
          laminationAllowedForBinding(o.name) &&
          !excluded.has(o.name)
      )
    ).forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt.name;
      o.textContent = opt.name + "  (+" + opt.price_add.toFixed(2) + " ₽)";
      section.elLamination.appendChild(o);
    });
    if (prev && [...section.elLamination.options].some((x) => x.value === prev)) {
      section.elLamination.value = prev;
    } else if (prev) {
      section.elLamination.value = "";
    }
    const lamField =
      section.prefix === "cover"
        ? document.getElementById("field-cover-lamination")
        : document.getElementById("field-pages-lamination");
    if (lamField) lamField.classList.toggle("hidden", section.elLamination.options.length <= 1);
  }

  function formatCountOptionPrice(unitPrice, count) {
    const total = unitPrice * (Number.isFinite(count) ? count : 0);
    if (total <= 0) return "0.00 ₽";
    return "+" + total.toFixed(2) + " ₽";
  }

  function fillOtherOptions(section) {
    const size = currentSize();
    if (!size) return;
    const opts = optsBySize[sizeOptsKey(size)] || [];
    const checked = new Set(
      Array.from(section.elOptions.querySelectorAll('input[type="checkbox"]:checked')).map((i) => i.value)
    );
    const prevCounts = {};
    section.elOptions.querySelectorAll("input[data-count-option]").forEach((inp) => {
      prevCounts[inp.dataset.countOption] = inp.value;
    });
    section.elOptions.innerHTML = "";

    countOptionDefs.forEach((optDef) => {
      const preview = opts.find((o) => o.name === optDef.name);
      const unitPrice = preview ? preview.price_add : 0;
      const def = optDef.count != null ? optDef.count : 0;
      const min = optDef.count_min != null ? optDef.count_min : 0;
      const max = optDef.count_max != null ? optDef.count_max : 10;
      const prev = prevCounts[optDef.name];
      const val = prev !== undefined ? prev : String(def);
      const countNum = parseInt(val, 10) || 0;
      const wrap = document.createElement("label");
      wrap.className = "opt-item opt-count";
      wrap.innerHTML =
        "<span>" + optDef.name + "</span>" +
        '<input type="number" class="opt-count-input" min="' + min + '" max="' + max + '" value="' + val + '" data-count-option="' + optDef.name + '" data-unit-price="' + unitPrice.toFixed(4) + '">' +
        '<span class="opt-price">' + formatCountOptionPrice(unitPrice, countNum) + "</span>";
      section.elOptions.appendChild(wrap);
    });

    const otherField =
      section.prefix === "cover"
        ? document.getElementById("field-cover-options")
        : document.getElementById("field-pages-options");
    if (otherField) otherField.classList.toggle("hidden", !section.elOptions.children.length);
  }

  function fillProductOptions() {
    if (!elProductOptions) return;
    const size = currentSize();
    const opts = size ? optsBySize[sizeOptsKey(size)] || [] : [];
    const checked = new Set(
      Array.from(elProductOptions.querySelectorAll('input[type="checkbox"]:checked')).map((i) => i.value)
    );
    elProductOptions.innerHTML = "";
    productOptionDefs.forEach((optDef) => {
      const preview = opts.find((o) => o.name === optDef.name);
      const priceAdd = preview ? preview.price_add : 0;
      const wrap = document.createElement("label");
      wrap.className = "opt-item";
      const checkedAttr = checked.has(optDef.name) ? " checked" : "";
      wrap.innerHTML =
        '<input type="checkbox" value="' + optDef.name.replace(/"/g, "&quot;") + '"' + checkedAttr + ">" +
        "<span>" + optDef.name + "</span>" +
        '<span class="opt-price">+' + priceAdd.toFixed(2) + " ₽</span>";
      elProductOptions.appendChild(wrap);
    });
    const field = document.getElementById("field-product-options");
    if (field) field.classList.toggle("hidden", !elProductOptions.children.length);
  }

  function updatePageSplitNote() {
    if (!elPageSplitNote) return;
    const total = parseInt(elPageCount.value, 10) || 0;
    const cover = coverPages(sections[0].elPrint.value);
    const block = Math.max(0, total - cover);
    elPageSplitNote.textContent =
      "Обложка: " + cover + " стр. (печать " + sections[0].elPrint.value + ") · блок: " + block + " стр.";
  }

  function gatherSectionOptions(section) {
    const opts = [];
    if (section.elLamination.value) opts.push(section.elLamination.value);
    section.elOptions.querySelectorAll('input[type="checkbox"]:checked').forEach((i) => opts.push(i.value));
    return opts;
  }

  function gatherSectionCounts(section) {
    const counts = {};
    section.elOptions.querySelectorAll("input[data-count-option]").forEach((inp) => {
      const n = parseInt(inp.value, 10);
      counts[inp.dataset.countOption] = Number.isFinite(n) ? n : 0;
    });
    return counts;
  }

  function gatherProductOptions() {
    if (!elProductOptions) return [];
    return Array.from(elProductOptions.querySelectorAll('input[type="checkbox"]:checked')).map((i) => i.value);
  }

  function gatherPayload() {
    const size = currentSize();
    return {
      product: productId,
      binding: elBinding.value,
      orientation: elOrientation.value,
      binding_position: elBindingPosition ? elBindingPosition.value : "left",
      size: size ? size.name : "",
      page_count: parseInt(elPageCount.value, 10) || product.default_page_count || 8,
      qty: parseInt(elQty.value, 10),
      discount: discountField ? discountField.getValue() : parseFloat(elDiscount?.value || "0"),
      product_options: gatherProductOptions(),
      cover: {
        material: sections[0].elMat.value,
        print_mode: sections[0].elPrint.value,
        options: gatherSectionOptions(sections[0]),
        option_counts: gatherSectionCounts(sections[0]),
      },
      pages: {
        material: sections[1].elMat.value,
        print_mode: sections[1].elPrint.value,
        options: gatherSectionOptions(sections[1]),
        option_counts: gatherSectionCounts(sections[1]),
      },
    };
  }

  function renderResult(r) {
    resultBox.innerHTML = window.CalcResult.renderResultHtml(r, showAdminDetails);
    resultBox.classList.remove("hidden");
    resultEmpty.classList.add("hidden");
    resultActions.classList.remove("hidden");
    copyBtn.disabled = false;
    saveBtn.disabled = false;
    lastResult = { text: formatCopyText(r), data: r };
  }

  function formatCopyText(r) {
    const lines = [r.product + " · " + r.size];
    lines.push("Переплёт: " + (r.binding_label || r.binding));
    if (r.binding_position_label) lines.push("Крепление: " + r.binding_position_label);
    lines.push("Страниц: " + r.page_count + " (обложка " + r.cover_pages + ", блок " + r.block_pages + ")");
    lines.push("Обложка: " + r.cover_material + " · " + r.cover_print);
    lines.push("Страницы: " + r.pages_material + " · " + r.pages_print);
    if (r.cover_retail != null) {
      lines.push("Стоимость обложки: " + r.cover_retail + " ₽");
      lines.push("Стоимость страниц: " + r.pages_retail + " ₽");
      lines.push("Крепление: " + r.binding_retail + " ₽");
    }
    lines.push("Тираж: " + r.qty + " шт.");
    if (r.options && r.options.length) {
      lines.push("Опции: " + r.options.map((o) => o.name).join(", "));
    }
    lines.push("Цена за шт: " + r.price_after_discount + " ₽");
    lines.push("ИТОГО: " + r.total + " ₽");
    return lines.join("\n");
  }

  async function runCalc() {
    const seq = ++calcSeq;
    const payload = gatherPayload();
    try {
      const resp = await fetch("/api/calc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (seq !== calcSeq) return;
      if (data.ok) {
        renderResult(data.result);
      } else {
        resultEmpty.textContent = "Ошибка: " + data.error;
        resultEmpty.classList.remove("hidden");
        resultBox.classList.add("hidden");
        resultActions.classList.add("hidden");
        copyBtn.disabled = true;
        saveBtn.disabled = true;
        lastResult = null;
      }
    } catch (err) {
      if (seq !== calcSeq) return;
      resultEmpty.textContent = "Ошибка запроса: " + err;
      resultEmpty.classList.remove("hidden");
      resultBox.classList.add("hidden");
      resultActions.classList.add("hidden");
      copyBtn.disabled = true;
      saveBtn.disabled = true;
      lastResult = null;
    }
  }

  function scheduleCalc() {
    clearTimeout(calcTimer);
    calcTimer = setTimeout(runCalc, 180);
  }

  function rebuild() {
    fillSizes();
    fillQty();
    fillProductOptions();
    sections.forEach((s) => {
      fillLamination(s);
      fillOtherOptions(s);
    });
    updatePageSplitNote();
  }

  sections.forEach((s) => fillMaterials(s.elMat));

  [elBinding, elOrientation].forEach((el) => {
    el.addEventListener("change", () => {
      rebuild();
      scheduleCalc();
    });
  });
  if (elBindingPosition) {
    elBindingPosition.addEventListener("change", () => {
      fillSizes();
      fillQty();
      fillProductOptions();
      scheduleCalc();
    });
  }
  elSize.addEventListener("change", () => {
    fillQty();
    fillProductOptions();
    sections.forEach((s) => {
      fillLamination(s);
      fillOtherOptions(s);
    });
    scheduleCalc();
  });
  elPageCount.addEventListener("input", () => {
    updatePageSplitNote();
    scheduleCalc();
  });
  elQty.addEventListener("change", scheduleCalc);
  if (discountField) discountField.bind(scheduleCalc);
  else if (elDiscount) elDiscount.addEventListener("change", scheduleCalc);

  if (elProductOptions) {
    elProductOptions.addEventListener("change", scheduleCalc);
  }

  sections.forEach((section) => {
    section.elMat.addEventListener("change", () => {
      fillLamination(section);
      scheduleCalc();
    });
    section.elPrint.addEventListener("change", () => {
      updatePageSplitNote();
      scheduleCalc();
    });
    section.elLamination.addEventListener("change", scheduleCalc);
    section.elOptions.addEventListener("change", scheduleCalc);
    section.elOptions.addEventListener("input", (e) => {
      if (e.target && e.target.matches("input[data-count-option]")) {
        const inp = e.target;
        const unit = parseFloat(inp.dataset.unitPrice || "0");
        const count = parseInt(inp.value, 10);
        const priceEl = inp.closest(".opt-count")?.querySelector(".opt-price");
        if (priceEl) priceEl.textContent = formatCountOptionPrice(unit, count);
        scheduleCalc();
      }
    });
  });

  copyBtn.addEventListener("click", () => {
    if (!lastResult) return;
    navigator.clipboard.writeText(lastResult.text).then(() => {
      copyBtn.textContent = "Скопировано ✓";
      setTimeout(() => (copyBtn.textContent = "Скопировать"), 1500);
    });
  });

  function openSaveModal() {
    if (!lastResult) return;
    saveTitle.value = "";
    saveModal.classList.remove("hidden");
    saveTitle.focus();
  }
  function closeSaveModal() {
    saveModal.classList.add("hidden");
  }
  saveBtn.addEventListener("click", openSaveModal);
  saveModal.querySelectorAll("[data-close-modal]").forEach((el) => el.addEventListener("click", closeSaveModal));
  saveConfirmBtn.addEventListener("click", async () => {
    const title = saveTitle.value.trim();
    if (!title || !lastResult) return;
    saveConfirmBtn.disabled = true;
    try {
      const resp = await fetch("/api/save_history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, result: lastResult.data }),
      });
      const data = await resp.json();
      if (data.ok) {
        closeSaveModal();
        saveBtn.textContent = "Сохранено ✓";
        setTimeout(() => (saveBtn.textContent = "Сохранить расчёт"), 2000);
      } else {
        alert("Ошибка: " + data.error);
      }
    } catch (err) {
      alert("Ошибка запроса: " + err);
    } finally {
      saveConfirmBtn.disabled = false;
    }
  });

  rebuild();
  scheduleCalc();
})();

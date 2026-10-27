(function () {
  const productEl = document.getElementById("product-data");
  const metaEl = document.getElementById("calc-meta");
  if (!productEl || !metaEl) return;

  let product;
  let calcMeta;
  try {
    product = JSON.parse(productEl.textContent);
    calcMeta = JSON.parse(metaEl.textContent);
  } catch (err) {
    const empty = document.getElementById("result-empty");
    if (empty) empty.textContent = "Ошибка загрузки данных калькулятора.";
    return;
  }

  const showAdminDetails = !!calcMeta.show_admin_details;
  const productId = calcMeta.product_id || "stickery_plotter";

  const elMat = document.getElementById("f-material");
  const elWidth = document.getElementById("f-width");
  const elHeight = document.getElementById("f-height");
  const elLamination = document.getElementById("f-lamination");
  const elQty = document.getElementById("f-qty");
  const elDiscount = document.getElementById("f-discount");
  const discountField =
    typeof DiscountField !== "undefined"
      ? DiscountField.init(document.querySelector(".discount-field"))
      : null;
  const perSheetHint = document.getElementById("per-sheet-hint");
  const resultBox = document.getElementById("result");
  const resultEmpty = document.getElementById("result-empty");
  const resultActions = document.getElementById("result-actions");
  const copyBtn = document.getElementById("copy-btn");
  const saveBtn = document.getElementById("save-btn");
  const saveModal = document.getElementById("save-modal");
  const saveTitle = document.getElementById("save-title");
  const saveConfirmBtn = document.getElementById("save-confirm-btn");

  if (!elMat || !elQty || !elWidth || !elHeight) return;

  let lastResult = null;
  let calcTimer = null;
  let layoutTimer = null;
  let calcSeq = 0;
  let layout = null;

  function currentMaterial() {
    const opt = elMat.selectedOptions[0];
    if (!opt) return { calc_mode: "self_adhesive", print_modes: [] };
    return {
      name: opt.value,
      calc_mode: opt.dataset.calcMode || "self_adhesive",
      print_modes: (opt.dataset.printModes || "").split(",").filter(Boolean),
    };
  }

  function isThroughCut() {
    return currentMaterial().calc_mode === "through_cut";
  }

  function itemsOnSheetLabel(n) {
    n = parseInt(n, 10);
    if (n % 10 === 1 && n % 100 !== 11) return n + " изделие на листе";
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) return n + " изделия на листе";
    return n + " изделий на листе";
  }

  function fillQty(tiers) {
    const prev = elQty.value;
    elQty.innerHTML = "";
    (tiers || []).forEach((t) => {
      const o = document.createElement("option");
      o.value = t.qty;
      o.textContent = t.label + " шт";
      elQty.appendChild(o);
    });
    if (prev && tiers.some((t) => String(t.qty) === prev)) {
      elQty.value = prev;
    }
  }

  function updatePerSheetHint(perSheet) {
    if (!perSheetHint) return;
    if (!perSheet) {
      perSheetHint.classList.add("hidden");
      perSheetHint.textContent = "";
      return;
    }
    perSheetHint.textContent = itemsOnSheetLabel(perSheet);
    perSheetHint.classList.remove("hidden");
  }

  function updateMaterialMode() {
    /* ламинация доступна для всех материалов */
  }

  async function fetchLayout() {
    const w = parseInt(elWidth.value, 10);
    const h = parseInt(elHeight.value, 10);
    if (!w || !h) {
      layout = null;
      fillQty([]);
      updatePerSheetHint(null);
      return;
    }
    try {
      const resp = await fetch("/api/plotter/layout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product: productId,
          material: elMat.value,
          width_mm: w,
          height_mm: h,
        }),
      });
      const data = await resp.json();
      if (data.ok) {
        layout = data;
        fillQty(data.qty_tiers);
        updatePerSheetHint(data.per_sheet);
        scheduleCalc();
      } else {
        layout = null;
        fillQty([]);
        perSheetHint.textContent = data.error || "Ошибка раскладки";
        perSheetHint.classList.remove("hidden");
      }
    } catch (err) {
      layout = null;
      fillQty([]);
    }
  }

  function scheduleLayout() {
    if (layoutTimer) clearTimeout(layoutTimer);
    layoutTimer = setTimeout(fetchLayout, 200);
  }

  function renderResult(r) {
    lastResult = r;
    if (typeof CalcResult === "undefined") {
      resultEmpty.textContent = "Ошибка: не загружен модуль отображения результата.";
      resultEmpty.classList.remove("hidden");
      return;
    }
    resultBox.innerHTML = CalcResult.renderResultHtml(r, showAdminDetails);
    resultEmpty.classList.add("hidden");
    resultBox.classList.remove("hidden");
    resultActions.classList.remove("hidden");
    copyBtn.disabled = false;
    saveBtn.disabled = false;
  }

  function gatherPayload() {
    const mat = currentMaterial();
    const qty = parseInt(elQty.value, 10);
    const payload = {
      product: productId,
      material: mat.name,
      qty: qty,
      width_mm: parseInt(elWidth.value, 10),
      height_mm: parseInt(elHeight.value, 10),
      lamination: elLamination ? elLamination.value : "Без ламинации",
      options: [],
      discount: discountField ? discountField.getValue() : parseFloat(elDiscount?.value || "0") || 0,
    };
    if (mat.calc_mode === "through_cut") {
      payload.print_mode = mat.print_modes[0] || "4+0";
    }
    return payload;
  }

  function scheduleCalc() {
    if (calcTimer) clearTimeout(calcTimer);
    calcTimer = setTimeout(runCalc, 80);
  }

  async function runCalc() {
    const qty = parseInt(elQty.value, 10);
    const w = parseInt(elWidth.value, 10);
    const h = parseInt(elHeight.value, 10);
    if (!elMat.value || !qty || !w || !h || !layout) {
      resultEmpty.textContent = "Укажите материал, размер в мм и тираж.";
      resultEmpty.classList.remove("hidden");
      resultBox.classList.add("hidden");
      resultActions.classList.add("hidden");
      copyBtn.disabled = true;
      saveBtn.disabled = true;
      lastResult = null;
      return;
    }

    const seq = ++calcSeq;
    try {
      const resp = await fetch("/api/calc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(gatherPayload()),
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

  elMat.addEventListener("change", () => {
    updateMaterialMode();
    scheduleLayout();
  });
  elWidth.addEventListener("input", scheduleLayout);
  elHeight.addEventListener("input", scheduleLayout);
  if (elLamination) elLamination.addEventListener("change", scheduleCalc);
  elQty.addEventListener("change", scheduleCalc);
  if (discountField) discountField.bind(scheduleCalc);
  else if (elDiscount) elDiscount.addEventListener("change", scheduleCalc);

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
  saveModal.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", closeSaveModal);
  });

  saveConfirmBtn.addEventListener("click", async () => {
    const title = saveTitle.value.trim();
    if (!title) {
      saveTitle.focus();
      return;
    }
    if (!lastResult) return;
    saveConfirmBtn.disabled = true;
    try {
      const resp = await fetch("/api/save_history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, result: lastResult }),
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

  saveTitle.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveConfirmBtn.click();
    }
    if (e.key === "Escape") closeSaveModal();
  });

  updateMaterialMode();
  scheduleLayout();
})();

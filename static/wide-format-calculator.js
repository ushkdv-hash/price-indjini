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
  const productId = calcMeta.product_id || "shirokoformatnaya_pechat";

  const elResolution = document.getElementById("f-resolution");
  const elMat = document.getElementById("f-material");
  const elWidth = document.getElementById("f-width");
  const elHeight = document.getElementById("f-height");
  const elQty = document.getElementById("f-qty");
  const elPerimeterCut = document.getElementById("f-perimeter-cut");
  const elLamination = document.getElementById("f-lamination");
  const laminationField = document.getElementById("lamination-field");
  const bannerOptionsField = document.getElementById("banner-options-field");
  const meshWeldField = document.getElementById("mesh-weld-field");
  const elOptRope = document.getElementById("f-opt-rope");
  const elOptMeshWeld = document.getElementById("f-opt-mesh-weld");
  const elOptEyelets = document.getElementById("f-opt-eyelets");
  const elEyeletCorners = document.getElementById("f-eyelet-corners");
  const elEyeletPerimeter = document.getElementById("f-eyelet-perimeter");
  const eyeletsModeWrap = document.getElementById("eyelets-mode-wrap");
  const eyeletsHint = document.getElementById("eyelets-hint");
  const elDiscount = document.getElementById("f-discount");
  const discountField =
    typeof DiscountField !== "undefined"
      ? DiscountField.init(document.querySelector(".discount-field"))
      : null;
  const areaHint = document.getElementById("area-hint");
  const resultBox = document.getElementById("result");
  const resultEmpty = document.getElementById("result-empty");
  const resultActions = document.getElementById("result-actions");
  const copyBtn = document.getElementById("copy-btn");
  const saveBtn = document.getElementById("save-btn");
  const saveModal = document.getElementById("save-modal");
  const saveTitle = document.getElementById("save-title");
  const saveConfirmBtn = document.getElementById("save-confirm-btn");

  if (!elMat || !elWidth || !elHeight || !elQty) return;

  let lastResult = null;
  let calcTimer = null;
  let calcSeq = 0;

  const EYELET_SPACING_MM = 300;

  function getEyeletMode() {
    if (elEyeletPerimeter && elEyeletPerimeter.checked) return "perimeter";
    return "corners";
  }

  function eyeletsPerItem(mode, widthMm, heightMm) {
    if (mode === "corners") return 4;
    const perimeterM = (2 * (widthMm + heightMm)) / 1000;
    const spacingM = EYELET_SPACING_MM / 1000;
    return Math.max(1, Math.ceil(perimeterM / spacingM));
  }

  function updateEyeletsHint() {
    if (!eyeletsHint) return;
    if (!elOptEyelets || !elOptEyelets.checked) {
      eyeletsHint.textContent = "";
      return;
    }
    const w = parseFloat(elWidth.value);
    const h = parseFloat(elHeight.value);
    const qty = parseInt(elQty.value, 10) || 1;
    if (!w || !h) {
      eyeletsHint.textContent = "";
      return;
    }
    const mode = getEyeletMode();
    const perItem = eyeletsPerItem(mode, w, h);
    const total = perItem * qty;
    if (mode === "corners") {
      eyeletsHint.textContent = "4 шт на изделие · всего " + total + " шт";
    } else {
      eyeletsHint.textContent =
        "≈ " + perItem + " шт на изделие (каждые 30 см) · всего " + total + " шт";
    }
  }

  function baseResolutionForDpi(dpi) {
    return dpi === 540 || dpi === 630 ? 540 : 720;
  }

  function resolutionsForMaterial(baseRes) {
    if (baseRes === 540) return [540, 630];
    return [720, 1440];
  }

  const laminationKeysById = {};
  (product.wide_options || []).forEach((opt) => {
    if (opt.kind === "lamination") {
      laminationKeysById[opt.id] = opt.material_keys || [];
    }
  });

  function currentMaterialOption() {
    return elMat.selectedOptions[0] || null;
  }

  function currentLaminationKey() {
    const opt = currentMaterialOption();
    return opt ? opt.dataset.laminationKey || "" : "";
  }

  function currentCategory() {
    const opt = currentMaterialOption();
    return opt ? opt.dataset.category || "" : "";
  }

  function currentUIMaterial() {
    const id = elMat.value;
    return (product.materials || []).find((m) => m.id === id) || null;
  }

  function variantIdsForCurrentMaterial() {
    const m = currentUIMaterial();
    if (!m || !m.variant_ids) return {};
    return m.variant_ids;
  }

  function resolutionsForCurrentMaterial() {
    const variants = variantIdsForCurrentMaterial();
    const bases = Object.keys(variants).map((k) => parseInt(k, 10));
    if (bases.length) {
      const allowed = [];
      bases.forEach((base) => {
        resolutionsForMaterial(base).forEach((dpi) => {
          if (!allowed.includes(dpi)) allowed.push(dpi);
        });
      });
      return allowed.sort((a, b) => a - b);
    }
    const m = currentUIMaterial();
    const baseRes = parseInt(m?.base_resolution || "720", 10);
    return resolutionsForMaterial(baseRes);
  }

  function resolveMaterialId() {
    const dpi = parseInt(elResolution.value, 10);
    const base = baseResolutionForDpi(dpi);
    const variants = variantIdsForCurrentMaterial();
    const id = variants[base] || variants[String(base)];
    return id || elMat.value;
  }

  function updateResolutionOptions() {
    if (!elResolution) return;
    const allowed = resolutionsForCurrentMaterial();
    const prev = parseInt(elResolution.value, 10);
    Array.from(elResolution.options).forEach((o) => {
      const dpi = parseInt(o.value, 10);
      const show = allowed.includes(dpi);
      o.hidden = !show;
      o.disabled = !show;
    });
    if (!allowed.includes(prev)) {
      elResolution.value = String(allowed[0]);
    }
  }

  function laminationKeysForOption(opt) {
    const fromDom = (opt.dataset.materialKeys || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (fromDom.length) return fromDom;
    return laminationKeysById[opt.value] || [];
  }

  function updateLaminationOptions() {
    if (!elLamination) return;
    const matKey = currentLaminationKey();
    Array.from(elLamination.options).forEach((opt) => {
      if (opt.value === "none") {
        opt.hidden = false;
        opt.disabled = false;
        return;
      }
      const keys = laminationKeysForOption(opt);
      const show = !keys.length || (matKey && keys.includes(matKey));
      opt.hidden = !show;
      opt.disabled = !show;
    });
    const selected = elLamination.selectedOptions[0];
    if (!selected || selected.disabled) {
      elLamination.value = "none";
    }
  }

  function updateOptionVisibility() {
    const cat = currentCategory();
    const isSelfAdhesive = cat === "self-adhesive";
    const isBannerOrMesh = cat === "banner" || cat === "mesh";

    laminationField.classList.toggle("hidden", !isSelfAdhesive);
    if (!isSelfAdhesive && elLamination) {
      elLamination.value = "none";
    } else {
      updateLaminationOptions();
    }

    bannerOptionsField.classList.toggle("hidden", !isBannerOrMesh);
    meshWeldField.classList.toggle("hidden", cat !== "mesh");
    if (cat !== "mesh" && elOptMeshWeld) {
      elOptMeshWeld.checked = false;
    }
    if (!isBannerOrMesh) {
      if (elOptRope) elOptRope.checked = false;
      if (elOptEyelets) elOptEyelets.checked = false;
      if (eyeletsModeWrap) eyeletsModeWrap.classList.add("hidden");
    }
  }

  function onMaterialChange() {
    updateResolutionOptions();
    updateOptionVisibility();
    scheduleCalc();
  }

  function updateAreaHint() {
    if (!areaHint) return;
    const w = parseFloat(elWidth.value);
    const h = parseFloat(elHeight.value);
    const qty = parseInt(elQty.value, 10) || 1;
    if (!w || !h) {
      areaHint.classList.add("hidden");
      areaHint.textContent = "";
      return;
    }
    const areaOne = (w * h) / 1_000_000;
    const total = areaOne * qty;
    areaHint.textContent =
      "≈ " +
      areaOne.toFixed(3).replace(".", ",") +
      " м² за шт · всего " +
      total.toFixed(3).replace(".", ",") +
      " м²";
    areaHint.classList.remove("hidden");
  }

  function gatherPayload() {
    const options = [];
    const payload = {
      product: productId,
      material: resolveMaterialId(),
      resolution: parseInt(elResolution.value, 10),
      width_mm: parseFloat(elWidth.value),
      height_mm: parseFloat(elHeight.value),
      qty: parseInt(elQty.value, 10),
      perimeter_cut: !!(elPerimeterCut && elPerimeterCut.checked),
      lamination: elLamination ? elLamination.value : "none",
      options: options,
      discount: discountField ? discountField.getValue() : parseFloat(elDiscount?.value || "0") || 0,
    };
    if (elOptRope && elOptRope.checked) options.push("rope");
    if (elOptMeshWeld && elOptMeshWeld.checked) options.push("mesh_weld");
    if (elOptEyelets && elOptEyelets.checked) {
      options.push("eyelets");
      payload.eyelet_mode = getEyeletMode();
    }
    return payload;
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

  function scheduleCalc() {
    if (calcTimer) clearTimeout(calcTimer);
    calcTimer = setTimeout(runCalc, 80);
  }

  async function runCalc() {
    const w = parseFloat(elWidth.value);
    const h = parseFloat(elHeight.value);
    const qty = parseInt(elQty.value, 10);
    if (!elMat.value || !w || !h || !qty) {
      resultEmpty.textContent = "Укажите материал, размер в мм и количество.";
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

  elResolution.addEventListener("change", scheduleCalc);
  elMat.addEventListener("change", onMaterialChange);
  elWidth.addEventListener("input", () => {
    updateAreaHint();
    updateEyeletsHint();
    scheduleCalc();
  });
  elHeight.addEventListener("input", () => {
    updateAreaHint();
    updateEyeletsHint();
    scheduleCalc();
  });
  elQty.addEventListener("input", () => {
    updateAreaHint();
    updateEyeletsHint();
    scheduleCalc();
  });
  if (elPerimeterCut) elPerimeterCut.addEventListener("change", scheduleCalc);
  if (elLamination) elLamination.addEventListener("change", scheduleCalc);
  if (elOptRope) elOptRope.addEventListener("change", scheduleCalc);
  if (elOptMeshWeld) elOptMeshWeld.addEventListener("change", scheduleCalc);
  if (elOptEyelets) {
    elOptEyelets.addEventListener("change", () => {
      if (eyeletsModeWrap) eyeletsModeWrap.classList.toggle("hidden", !elOptEyelets.checked);
      updateEyeletsHint();
      scheduleCalc();
    });
  }
  [elEyeletCorners, elEyeletPerimeter].forEach((el) => {
    if (!el) return;
    el.addEventListener("change", () => {
      updateEyeletsHint();
      scheduleCalc();
    });
  });
  if (discountField) discountField.bind(scheduleCalc);
  else if (elDiscount) elDiscount.addEventListener("change", scheduleCalc);

  copyBtn.addEventListener("click", () => {
    if (!lastResult || !lastResult.text) return;
    navigator.clipboard.writeText(lastResult.text).then(() => {
      copyBtn.textContent = "Скопировано";
      setTimeout(() => {
        copyBtn.textContent = "Скопировать";
      }, 1500);
    });
  });

  saveBtn.addEventListener("click", () => {
    if (!lastResult) return;
    saveTitle.value = "";
    saveModal.classList.remove("hidden");
    saveTitle.focus();
  });

  saveConfirmBtn.addEventListener("click", async () => {
    const title = (saveTitle.value || "").trim();
    if (!title) {
      saveTitle.focus();
      return;
    }
    try {
      const resp = await fetch("/api/save_history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, result: lastResult }),
      });
      const data = await resp.json();
      if (data.ok) {
        saveModal.classList.add("hidden");
        saveBtn.textContent = "Сохранено";
        setTimeout(() => {
          saveBtn.textContent = "Сохранить расчёт";
        }, 1500);
      }
    } catch (err) {
      /* ignore */
    }
  });

  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", () => saveModal.classList.add("hidden"));
  });

  onMaterialChange();
  updateAreaHint();
  updateEyeletsHint();
  scheduleCalc();
})();

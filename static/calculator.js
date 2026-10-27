(function () {
  const product = JSON.parse(document.getElementById("product-data").textContent);
  const optsBySize = JSON.parse(document.getElementById("opts-by-size").textContent);
  const calcMeta = JSON.parse(document.getElementById("calc-meta").textContent);
  if (!Array.isArray(product.options)) product.options = [];
  if (!Array.isArray(product.materials)) product.materials = [];
  if (!Array.isArray(product.sizes)) product.sizes = [];
  const showAdminDetails = !!calcMeta.show_admin_details;
  const productId = calcMeta.product_id || "vizitki";
  const isNotebook = product.calc_type === "notebook";
  const isBrandedCups = product.calc_type === "branded_cups";
  const isSublimation = product.calc_type === "sublimation";
  const isOffsetTable = product.calc_type === "offset_table";
  const hasOffsetLamination = isOffsetTable && product.lamination_modes && product.lamination_modes.length;
  const isPvdBags = product.calc_type === "pvd_bags";
  const isPaperBags = product.calc_type === "paper_bags";
  const isEnvelopes = product.calc_type === "envelopes";
  const isBoardCover = product.calc_type === "board_cover";
  const isQuarterlyCalendars = product.calc_type === "quarterly_calendars";
  const isWallCalendars = product.calc_type === "wall_calendars";
  const isCalendarProduct = isQuarterlyCalendars || isWallCalendars;
  const isBeizhi = productId === "beizhi";
  const lanyardOption = product.lanyard_option || null;
  const elPapkiKind = document.getElementById("f-papki-kind");

  const elSize = document.getElementById("f-size");
  const elBoardCovers = document.getElementById("f-board-covers");
  const elBoardLamination = document.getElementById("f-board-lamination");
  const elBoardMode = document.getElementById("f-board-mode");
  const elBoardWidth = document.getElementById("f-board-width");
  const elBoardHeight = document.getElementById("f-board-height");
  const elBoardParts = document.getElementById("f-board-parts");
  const elBoardCustom = document.getElementById("field-board-custom");
  const elBoardCustomHint = document.getElementById("board-custom-hint");
  const elMat = document.getElementById("f-material");
  const elPrint = document.getElementById("f-print");
  const elPrintNote = document.getElementById("print-mode-note");
  const elPvdPrintNote = document.getElementById("pvd-print-note");
  const elBlockMode = document.getElementById("f-block-mode");
  const elPageCount = document.getElementById("f-page-count");
  const elNotebookCoverNote = document.getElementById("notebook-cover-note");
  const elQty = document.getElementById("f-qty");
  const elDelivery = document.getElementById("f-delivery");
  const elLaminationMode = document.getElementById("f-lamination-mode");
  const elLamination = document.getElementById("f-lamination");
  const elLaminationSurfaces = document.getElementById("f-lamination-surfaces");
  const elDiscount = document.getElementById("f-discount");
  const discountField =
    typeof DiscountField !== "undefined"
      ? DiscountField.init(document.querySelector(".discount-field"))
      : null;
  const elOptions = document.getElementById("f-options");
  const elLanyardEnabled = document.getElementById("f-lanyard-enabled");
  const elLanyardPanel = document.getElementById("beizhi-lanyard-panel");
  const elLanyardPrice = document.getElementById("f-lanyard-price");
  const elLanyardColor = document.getElementById("f-lanyard-color");
  const resultBox = document.getElementById("result");
  const resultEmpty = document.getElementById("result-empty");
  const resultActions = document.getElementById("result-actions");
  const copyBtn = document.getElementById("copy-btn");
  const saveBtn = document.getElementById("save-btn");
  const saveModal = document.getElementById("save-modal");
  const saveTitle = document.getElementById("save-title");
  const saveConfirmBtn = document.getElementById("save-confirm-btn");
  const elCustomPrice = document.getElementById("f-custom-price");
  const customMatBox = document.getElementById("custom-material-box");

  const customMaterial = product.materials.find((m) => m.custom_price_a3) || null;
  const optionKinds = calcMeta.option_kinds || {};
  const optionByName = new Map(product.options.map((o) => [o.name, o]));
  const finishSelectKinds = product.finish_select_kinds || null;
  const selectKinds = new Set(
    Object.entries(optionKinds)
      .filter(([, spec]) => spec.ui === "select")
      .map(([id]) => id)
  );

  function optionFormula(kind) {
    return (optionKinds[kind] && optionKinds[kind].formula) || kind;
  }

  function isFinishSelectOption(opt) {
    if (finishSelectKinds) return finishSelectKinds.includes(opt.kind);
    return selectKinds.has(opt.kind);
  }

  function isLaminationOptionName(name) {
    const o = optionByName.get(name);
    return o ? optionFormula(o.kind) === "lamination" : false;
  }

  function isFoilOptionName(name) {
    const o = optionByName.get(name);
    return o ? optionFormula(o.kind) === "foil" : false;
  }

  function isCountOption(opt) {
    return optionKinds[opt.kind] && optionKinds[opt.kind].ui === "count";
  }

  const finishSelectNames = new Set(
    product.options.filter((o) => isFinishSelectOption(o)).map((o) => o.name)
  );
  const otherOptionNames = new Set(
    product.options
      .filter((o) => !finishSelectNames.has(o.name) && !isCountOption(o))
      .map((o) => o.name)
  );
  const countOptionDefs = product.options.filter((o) => isCountOption(o));

  function optionExcludes(name) {
    const opt = optionByName.get(name);
    return opt && opt.excludes ? opt.excludes : [];
  }

  function applyOptionExclusions(changedInput) {
    if (!changedInput || changedInput.type !== "checkbox" || !changedInput.checked) return;
    const excludes = optionExcludes(changedInput.value);
    excludes.forEach((exName) => {
      elOptions.querySelectorAll('input[type="checkbox"]').forEach((inp) => {
        if (inp.value === exName) inp.checked = false;
      });
    });
  }

  function updateOptionExclusionState() {
    const selected = new Set(
      Array.from(elOptions.querySelectorAll('input[type="checkbox"]:checked')).map((i) => i.value)
    );
    const blockedBy = new Map();
    selected.forEach((name) => {
      optionExcludes(name).forEach((ex) => {
        if (!blockedBy.has(ex)) blockedBy.set(ex, new Set());
        blockedBy.get(ex).add(name);
      });
    });
    elOptions.querySelectorAll('input[type="checkbox"]').forEach((inp) => {
      const blockers = blockedBy.get(inp.value);
      const blocked = !!(blockers && blockers.size && !inp.checked);
      inp.disabled = blocked;
      const wrap = inp.closest(".opt-item");
      if (wrap) wrap.classList.toggle("opt-item--disabled", blocked);
    });
  }

  let lastResult = null;
  let calcTimer = null;
  let calcSeq = 0;

  let fillMaterials = null;
  let ensureBrandedCupsQtyForMaterial = null;

  if (elMat) {
    function materialsForQty(qty) {
      return product.materials.filter((m) => !m.qty_min || qty >= m.qty_min);
    }

    fillMaterials = function () {
      const prev = elMat.value;
      const mats = isBrandedCups ? product.materials : materialsForQty(parseInt(elQty.value, 10) || 0);
      elMat.innerHTML = "";
      mats.forEach((m) => {
        const o = document.createElement("option");
        o.value = m.name;
        o.textContent = m.name;
        elMat.appendChild(o);
      });
      if (prev && mats.some((m) => m.name === prev)) elMat.value = prev;
      else if (mats.length) elMat.value = mats[0].name;
    };

    ensureBrandedCupsQtyForMaterial = function () {
      if (!isBrandedCups || !elQty) return;
      const mat = brandedCupsMaterial();
      const minQty = mat && mat.qty_min ? parseInt(mat.qty_min, 10) : 0;
      if (!minQty) return;
      const qty = parseInt(elQty.value, 10) || 0;
      if (qty >= minQty) return;
      fillQty();
      const tierQtys = Array.from(elQty.options).map((o) => parseInt(o.value, 10));
      const nextQty = tierQtys.find((q) => q >= minQty);
      if (nextQty != null) elQty.value = String(nextQty);
    };

    if (!isBrandedCups && !isOffsetTable) {
      product.materials.forEach((m) => {
        const o = document.createElement("option");
        o.value = m.name;
        o.textContent = m.name;
        elMat.appendChild(o);
      });
    }
  }

  function markupProfileData(size, profile) {
    const profiles = size.markup_profiles;
    if (profiles) {
      if (profile && profiles[profile]) return profiles[profile];
      if (profiles.default) return profiles.default;
      const keys = Object.keys(profiles);
      return keys.length ? profiles[keys[0]] : {};
    }
    return {
      tiers: size.markup_tiers || [],
    };
  }

  function currentMaterial() {
    if (isNotebook || !elMat) return null;
    if (isOffsetTable || isPvdBags) {
      const val = elMat.value;
      return (
        product.materials.find((m) => m.id === val || m.name === val) || product.materials[0]
      );
    }
    return product.materials.find((m) => m.name === elMat.value) || product.materials[0];
  }

  function brandedCupsMaterial() {
    if (!isBrandedCups) return currentMaterial();
    return currentMaterial();
  }

  function updateBrandedCupsNote() {
    const noteEl = document.getElementById("branded-cups-note");
    if (!noteEl || !isBrandedCups) return;
    const mat = brandedCupsMaterial();
    if (mat && mat.note) {
      noteEl.textContent = mat.note;
      noteEl.classList.remove("hidden");
    } else {
      noteEl.textContent = "";
      noteEl.classList.add("hidden");
    }
  }

  function materialsForOffsetSize(size) {
    const sid = size.id || size.name;
    return product.materials.filter((m) => {
      if (!m.size_ids || !m.size_ids.length) return true;
      return m.size_ids.includes(sid);
    });
  }

  function fillOffsetMaterials() {
    if (!isOffsetTable || !elMat) return;
    const prev = elMat.value;
    const mats = materialsForOffsetSize(currentSize());
    elMat.innerHTML = "";
    mats.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.name;
      elMat.appendChild(o);
    });
    if (prev && mats.some((m) => m.id === prev)) elMat.value = prev;
    else if (mats.length) elMat.value = mats[0].id;
  }

  function offsetMaterialConfig() {
    const cfg = product.size_material_config;
    if (!cfg) return null;
    const size = currentSize();
    const mat = currentMaterial();
    if (!size || !mat) return null;
    const sid = size.id || size.name;
    return (cfg[sid] && cfg[sid][mat.id]) || null;
  }

  function fillDelivery() {
    if (!isOffsetTable || !elDelivery) return;
    const mat = currentMaterial();
    const smc = offsetMaterialConfig();
    const tiers =
      smc && smc.delivery_tiers && smc.delivery_tiers.length
        ? smc.delivery_tiers
        : mat && mat.delivery_tiers && mat.delivery_tiers.length
          ? mat.delivery_tiers
          : product.delivery_tiers || [];
    const prev = elDelivery.value;
    elDelivery.innerHTML = "";
    tiers.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.id;
      o.textContent = t.display || t.label;
      elDelivery.appendChild(o);
    });
    if (prev && tiers.some((t) => t.id === prev)) elDelivery.value = prev;
    else if (tiers.length) elDelivery.value = tiers[0].id;
  }

  if (isSublimation) {
    const sizeField = document.getElementById("field-size");
    if (sizeField) sizeField.classList.add("hidden");
  }

  function allowedPrintModes() {
    if (isEnvelopes) {
      const size = currentSize();
      if (size && size.print_modes && size.print_modes.length) return size.print_modes;
      return product.print_modes || ["4+0", "без печати"];
    }
    if (isQuarterlyCalendars || isWallCalendars) {
      return product.print_modes || [];
    }
    const mat = currentMaterial();
    if (mat && mat.print_modes && mat.print_modes.length) return mat.print_modes;
    return product.print_modes || ["4+0", "4+4"];
  }

  function togglePvdPrintNote() {
    if (!elPvdPrintNote || !elPrint) return;
    elPvdPrintNote.classList.toggle("hidden", elPrint.value !== "3+0");
  }

  function togglePrintModeNote() {
    if (!elPrintNote || !elPrint) return;
    const mode = elPrint.value;
    elPrintNote.classList.toggle("hidden", mode !== "1+0" && mode !== "1+1");
  }

  function fillPrint() {
    if (!elPrint) return;
    const modes = allowedPrintModes();
    const prev = elPrint.value;
    elPrint.innerHTML = "";
    modes.forEach((pm) => {
      const o = document.createElement("option");
      o.value = pm;
      o.textContent = pm;
      elPrint.appendChild(o);
    });
    if (prev && modes.includes(prev)) elPrint.value = prev;
    else elPrint.value = modes[0];
    togglePrintModeNote();
    togglePvdPrintNote();
    if (isQuarterlyCalendars) updateQuarterlyPreview();
  }

  product.sizes.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.name;
    o.textContent = s.name;
    elSize.appendChild(o);
  });

  function currentSize() {
    return product.sizes.find((s) => s.name === elSize.value) || product.sizes[0];
  }

  // --- Обложки из переплётного картона: нестандартный размер ---
  let boardLastBaseFmt = null;

  function boardCustomSize() {
    return {
      w: elBoardWidth ? parseFloat(elBoardWidth.value) : NaN,
      h: elBoardHeight ? parseFloat(elBoardHeight.value) : NaN,
    };
  }

  function boardFitsSheet(w, h, maxW, maxH) {
    if (!(w > 0) || !(h > 0)) return false;
    return (w <= maxW && h <= maxH) || (w <= maxH && h <= maxW);
  }

  function boardNearestFormat(w, h) {
    const fmts = [["A6", 105, 148], ["A5", 148, 210], ["A4", 210, 297], ["A3", 297, 420]];
    const area = w * h;
    for (let i = 0; i < fmts.length; i += 1) {
      if (fmts[i][1] * fmts[i][2] >= area - 1e-6) return fmts[i][0];
    }
    return "A3";
  }

  function boardSizeNameByFormat(fmt) {
    const s = product.sizes.find((x) => x.format === fmt);
    return s ? s.name : fmt;
  }

  function syncBoardCustom() {
    if (!isBoardCover) return false;
    const custom = product.custom_size || {};
    const isCustom = elBoardMode && elBoardMode.value === "custom";
    const sizeField = document.getElementById("field-size");
    if (elBoardCustom) elBoardCustom.classList.toggle("hidden", !isCustom);
    if (sizeField) sizeField.classList.toggle("hidden", isCustom);
    if (!isCustom) {
      if (elBoardCustomHint) elBoardCustomHint.classList.add("hidden");
      return false;
    }
    const maxW = parseFloat(custom.max_width || 300);
    const maxH = parseFloat(custom.max_height || 420);
    const { w, h } = boardCustomSize();
    let hint = "";
    let changed = false;
    if (w > 0 && h > 0) {
      const parts = elBoardParts ? (parseInt(elBoardParts.value, 10) || 1) : 1;
      if (parts <= 1 && !boardFitsSheet(w, h, maxW, maxH)) {
        hint = custom.too_big_hint || ("Размер больше " + maxW + "×" + maxH + " мм — уменьшите размер изделия или посчитайте обложку из нескольких частей.");
      }
      const fmt = boardNearestFormat(w, h);
      if (fmt && fmt !== boardLastBaseFmt) {
        boardLastBaseFmt = fmt;
        const name = boardSizeNameByFormat(fmt);
        if (name && elSize && Array.from(elSize.options).some((o) => o.value === name)) {
          elSize.value = name;
          changed = true;
        }
      }
    }
    if (elBoardCustomHint) {
      elBoardCustomHint.textContent = hint;
      elBoardCustomHint.classList.toggle("hidden", !hint);
    }
    return changed;
  }

  function quarterlyBlockSuffix(mode) {
    const m = (mode || "").toLowerCase();
    if (m.includes("без реклам")) return "no_ad";
    if (m.includes("индивиду")) return "individual";
    return "with_ad";
  }

  function quarterlyPreviewKey() {
    const sizeName = (currentSize() && currentSize().name) || "";
    const family = /три календарных/i.test(sizeName) ? "tri" : "uno";
    const mode = elPrint ? elPrint.value : "";
    return family + "_" + quarterlyBlockSuffix(mode);
  }

  function updateQuarterlyPreview() {
    if (!isQuarterlyCalendars) return;
    const root = document.getElementById("quarterly-preview");
    if (!root) return;
    const key = quarterlyPreviewKey();
    root.querySelectorAll(".quarterly-illus[data-preview]").forEach((fig) => {
      const match = fig.getAttribute("data-preview") === key;
      fig.classList.toggle("hidden", !match);
      fig.setAttribute("aria-hidden", match ? "false" : "true");
    });
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function parseBagDimsFromName(name) {
    const m = String(name || "").match(/(\d+)\s*[x×хX]\s*(\d+)\s*[x×хX]\s*(\d+)/i);
    if (!m) return null;
    return {
      height_mm: parseInt(m[1], 10),
      width_mm: parseInt(m[2], 10),
      depth_mm: parseInt(m[3], 10),
      label: m[1] + "×" + m[2] + "×" + m[3],
    };
  }

  function bagDimsForSize(size) {
    const parsed = parseBagDimsFromName(size && size.name);
    return {
      width_mm: size.width_mm || (parsed && parsed.width_mm) || 100,
      height_mm: size.height_mm || (parsed && parsed.height_mm) || 100,
      depth_mm: size.depth_mm || (parsed && parsed.depth_mm) || 50,
      label: (parsed && parsed.label) || null,
    };
  }

  function svgEl(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
    }
    return el;
  }

  function svgDimHoriz(parent, x1, x2, y, label, offset) {
    const g = svgEl("g", { class: "pb-dim" });
    const off = offset || 0;
    const yLine = y + off;
    g.appendChild(svgEl("line", { x1, y1: y, x2: x1, y2: y, stroke: "currentColor", "stroke-opacity": 0.25, "stroke-width": 0.6 }));
    g.appendChild(svgEl("line", { x1, y1: y, x2: x2, y2: y, stroke: "currentColor", "stroke-opacity": 0.25, "stroke-width": 0.6 }));
    g.appendChild(svgEl("line", { x1, y1: yLine, x2: x2, y2: yLine, "stroke-width": 1 }));
    g.appendChild(svgEl("line", { x1, y1: yLine - 3, x2: x1, y2: yLine + 3, "stroke-width": 1 }));
    g.appendChild(svgEl("line", { x1: x2, y1: yLine - 3, x2: x2, y2: yLine + 3, "stroke-width": 1 }));
    const text = svgEl("text", {
      x: (x1 + x2) / 2,
      y: yLine + 14,
      "text-anchor": "middle",
    });
    text.textContent = label;
    g.appendChild(text);
    parent.appendChild(g);
  }

  function svgDimVert(parent, x, y1, y2, label, offset) {
    const g = svgEl("g", { class: "pb-dim" });
    const off = offset || 0;
    const xLine = x + off;
    g.appendChild(svgEl("line", { x1: x, y1: y1, x2: x, y2: y2, stroke: "currentColor", "stroke-opacity": 0.25, "stroke-width": 0.6 }));
    g.appendChild(svgEl("line", { x1: xLine, y1: y1, x2: xLine, y2: y2, "stroke-width": 1 }));
    g.appendChild(svgEl("line", { x1: xLine - 3, y1: y1, x2: xLine + 3, y2: y1, "stroke-width": 1 }));
    g.appendChild(svgEl("line", { x1: xLine - 3, y1: y2, x2: xLine + 3, y2: y2, "stroke-width": 1 }));
    const text = svgEl("text", {
      x: xLine - 8,
      y: (y1 + y2) / 2,
      "text-anchor": "middle",
      transform: `rotate(-90 ${xLine - 8} ${(y1 + y2) / 2})`,
    });
    text.textContent = label;
    g.appendChild(text);
    parent.appendChild(g);
  }

  function svgDimDepth(parent, x1, y1, x2, y2, label, labelDx, labelDy) {
    const g = svgEl("g", { class: "pb-dim" });
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    const off = 10;
    const tick = 3;
    const lx1 = x1 + nx * off;
    const ly1 = y1 + ny * off;
    const lx2 = x2 + nx * off;
    const ly2 = y2 + ny * off;
    const tdx = labelDx || 0;
    const tdy = labelDy || 0;

    g.appendChild(
      svgEl("line", {
        x1,
        y1,
        x2: lx1,
        y2: ly1,
        stroke: "currentColor",
        "stroke-opacity": 0.25,
        "stroke-width": 0.6,
      })
    );
    g.appendChild(
      svgEl("line", {
        x1: x2,
        y1: y2,
        x2: lx2,
        y2: ly2,
        stroke: "currentColor",
        "stroke-opacity": 0.25,
        "stroke-width": 0.6,
      })
    );
    g.appendChild(svgEl("line", { x1: lx1, y1: ly1, x2: lx2, y2: ly2, "stroke-width": 1 }));
    g.appendChild(
      svgEl("line", {
        x1: lx1 - nx * tick,
        y1: ly1 - ny * tick,
        x2: lx1 + nx * tick,
        y2: ly1 + ny * tick,
        "stroke-width": 1,
      })
    );
    g.appendChild(
      svgEl("line", {
        x1: lx2 - nx * tick,
        y1: ly2 - ny * tick,
        x2: lx2 + nx * tick,
        y2: ly2 + ny * tick,
        "stroke-width": 1,
      })
    );

    const mx = (lx1 + lx2) / 2;
    const my = (ly1 + ly2) / 2;
    const text = svgEl("text", {
      x: mx + nx * 8 + tdx,
      y: my + ny * 8 + 4 + tdy,
      "text-anchor": "middle",
    });
    text.textContent = label;
    g.appendChild(text);
    parent.appendChild(g);
  }

  function paperBagCategoryLabel(size) {
    const cat = (size && size.category) || "";
    if (cat === "small") return "Маленькие";
    if (cat === "medium") return "Средние";
    if (cat === "large") return "Большие";
    const m = String(size && size.name || "").match(/пакеты\s+(\S+)/i);
    return m ? m[1].charAt(0).toUpperCase() + m[1].slice(1) : "Пакет";
  }

  function updatePaperBagPreview() {
    if (!isPaperBags) return;
    const svg = document.getElementById("paper-bag-preview-svg");
    const caption = document.getElementById("paper-bag-preview-caption");
    if (!svg) return;
    const size = currentSize();
    const dims = bagDimsForSize(size);
    const w = dims.width_mm;
    const h = dims.height_mm;
    const d = dims.depth_mm;

    if (caption) {
      const sizeLabel = dims.label || w + "×" + h + "×" + d;
      caption.textContent = paperBagCategoryLabel(size) + " · " + sizeLabel + " мм";
    }

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const perspX = 0.62;
    const perspY = 0.22;
    const padL = 46;
    const padR = 36;
    const padT = 22;
    const padB = 38;
    const vbW = 240;
    const vbH = 268;
    const areaW = vbW - padL - padR;
    const areaH = vbH - padT - padB;
    const totalW = w + d * perspX;
    const totalH = h + d * perspY;
    const scale = Math.min(areaW / totalW, areaH / totalH);
    const sw = w * scale;
    const sh = h * scale;
    const sd = d * scale;
    const ox = padL + (areaW - (sw + sd * perspX)) / 2;
    const oy = padT + (areaH - (sh + sd * perspY)) / 2;

    const sx = ox + sw;
    const sy = oy;
    const sTopR = { x: sx + sd * perspX, y: sy - sd * perspY };
    const sBotR = { x: sx + sd * perspX, y: sy + sh - sd * perspY };

    const bag = svgEl("g");

    bag.appendChild(
      svgEl("path", {
        d: `M ${sx} ${sy + sh} L ${sBotR.x} ${sBotR.y} L ${sTopR.x} ${sTopR.y} L ${sx} ${sy} Z`,
        fill: "currentColor",
        "fill-opacity": 0.12,
        stroke: "currentColor",
        "stroke-opacity": 0.35,
        "stroke-width": 0.9,
        "stroke-linejoin": "round",
      })
    );

    bag.appendChild(
      svgEl("rect", {
        x: ox,
        y: oy,
        width: sw,
        height: sh,
        rx: 1.5,
        fill: "var(--card)",
        stroke: "currentColor",
        "stroke-opacity": 0.45,
        "stroke-width": 1,
      })
    );

    bag.appendChild(
      svgEl("rect", {
        x: ox + sw * 0.12,
        y: oy + sh * 0.28,
        width: sw * 0.76,
        height: sh * 0.42,
        rx: 1,
        fill: "currentColor",
        "fill-opacity": 0.14,
        stroke: "currentColor",
        "stroke-opacity": 0.28,
        "stroke-width": 0.7,
      })
    );

    bag.appendChild(
      svgEl("path", {
        d: `M ${ox + sw * 0.08} ${oy} Q ${ox + sw * 0.5} ${oy - sh * 0.1} ${ox + sw * 0.92} ${oy}`,
        fill: "none",
        stroke: "currentColor",
        "stroke-opacity": 0.35,
        "stroke-width": 0.8,
      })
    );

    bag.appendChild(
      svgEl("path", {
        d: `M ${sTopR.x} ${sTopR.y} L ${sx + sd * perspX * 0.55} ${sy - sd * perspY - sh * 0.06} L ${sx} ${sy}`,
        fill: "none",
        stroke: "currentColor",
        "stroke-opacity": 0.3,
        "stroke-width": 0.7,
      })
    );

    const eyeY = oy + sh * 0.06;
    [ox + sw * 0.22, ox + sw * 0.78].forEach((ex) => {
      bag.appendChild(
        svgEl("circle", {
          cx: ex,
          cy: eyeY,
          r: 2.2,
          fill: "var(--accent)",
          "fill-opacity": 0.55,
          stroke: "currentColor",
          "stroke-opacity": 0.4,
          "stroke-width": 0.6,
        })
      );
    });

    bag.appendChild(
      svgEl("path", {
        d: `M ${ox + sw * 0.22} ${eyeY} Q ${ox + sw * 0.5} ${oy - sh * 0.14} ${ox + sw * 0.78} ${eyeY}`,
        fill: "none",
        stroke: "var(--accent)",
        "stroke-opacity": 0.65,
        "stroke-width": 1,
        "stroke-dasharray": "2,2",
      })
    );

    bag.appendChild(
      svgEl("line", {
        x1: ox,
        y1: oy + sh,
        x2: sx,
        y2: sy + sh,
        stroke: "currentColor",
        "stroke-opacity": 0.25,
        "stroke-width": 0.6,
      })
    );

    svg.appendChild(bag);

    svgDimHoriz(svg, ox, ox + sw, oy + sh, w + " мм", 10);
    svgDimVert(svg, ox, oy, oy + sh, h + " мм", -12);
    svgDimDepth(svg, sx, sy + sh, sBotR.x, sBotR.y, d + " мм", 10, 6);
  }

  function updateNotebookCoverNote() {
    if (!isNotebook || !elNotebookCoverNote) return;
    const cover = currentSize().cover_print || "4+0";
    if (cover === "4+4") {
      elNotebookCoverNote.textContent =
        "Обложка 4+4: печать на лицевой стороне передней и задней обложек.";
    } else {
      elNotebookCoverNote.textContent =
        "Обложка 4+0: печать на лицевой стороне передней обложки, оборот задней — без печати (белый).";
    }
  }

  function qtyTierValue(entry) {
    if (entry == null) return null;
    if (typeof entry === "object") return entry.qty != null ? entry.qty : entry.value;
    return entry;
  }

  function qtyTierLabel(entry) {
    const qty = qtyTierValue(entry);
    if (qty == null) return "—";
    if (typeof entry === "object" && entry.label) return entry.label;
    return Number(qty).toLocaleString("ru-RU");
  }

  function selectedLanyardAttachment() {
    if (!lanyardOption) return null;
    const checked = document.querySelector('input[name="lanyard-attachment"]:checked');
    const id = checked ? checked.value : (lanyardOption.attachments[0] && lanyardOption.attachments[0].id);
    return (lanyardOption.attachments || []).find((a) => a.id === id) || null;
  }

  function updateLanyardUi() {
    if (!lanyardOption || !elLanyardEnabled) return;
    const enabled = elLanyardEnabled.checked;
    if (elLanyardPanel) elLanyardPanel.classList.toggle("hidden", !enabled);
    const att = selectedLanyardAttachment();
    if (elLanyardPrice) {
      if (!enabled) {
        elLanyardPrice.textContent = "";
      } else if (att && att.pending) {
        elLanyardPrice.textContent = "цена уточняется";
      } else if (att) {
        elLanyardPrice.textContent = "+" + Number(att.price_add || 0).toFixed(2) + " ₽";
      } else {
        elLanyardPrice.textContent = "";
      }
    }
  }

  function gatherLanyardPayload() {
    if (!lanyardOption || !elLanyardEnabled || !elLanyardEnabled.checked) {
      return { enabled: false };
    }
    const att = document.querySelector('input[name="lanyard-attachment"]:checked');
    return {
      enabled: true,
      attachment: att ? att.value : "",
      color: elLanyardColor ? elLanyardColor.value : "",
    };
  }

  function fillQty() {
    if (isSublimation) return;
    const size = currentSize();
    let tiers;
    if (isBrandedCups) {
      const mat = brandedCupsMaterial();
      if (
        mat &&
        mat.markup_profile === "paper_fullcolor" &&
        product.paper_cup_qty_options &&
        product.paper_cup_qty_options.length
      ) {
        tiers = product.paper_cup_qty_options;
      } else if (size.qty_options && size.qty_options.length) {
        tiers = size.qty_options;
      } else {
        tiers = [];
      }
    } else if (size.qty_options && size.qty_options.length) {
      tiers = size.qty_options;
    } else if (isOffsetTable) {
      const smc = offsetMaterialConfig();
      if (smc && smc.qty_options && smc.qty_options.length) {
        tiers = smc.qty_options;
      } else {
        const mat = currentMaterial();
        if (mat && mat.qty_options && mat.qty_options.length) {
          tiers = mat.qty_options;
        } else if (product.qty_options && product.qty_options.length) {
          tiers = product.qty_options;
        } else {
          tiers = [];
        }
      }
    } else if (isNotebook) {
      tiers = [10];
    } else {
      const mat = currentMaterial();
      const profile = (mat && mat.markup_profile) || "default";
      const prof = markupProfileData(size, profile);
      if (prof.tiers && prof.tiers.length) {
        tiers = prof.tiers.map((r) => r[0]);
      } else {
        const legacy = size.markup || {};
        const legacyKey = profile === "standard" ? "Картон" : profile === "designer" ? "Дизайнерская" : null;
        tiers = (legacyKey && legacy[legacyKey] ? legacy[legacyKey] : []).map((r) => r[0]);
      }
    }
    elQty.innerHTML = "";
    if (isBoardCover && product.qty_optional) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = product.qty_optional_label || "Не указан";
      elQty.appendChild(o);
    }
    tiers.forEach((entry) => {
      const qty = qtyTierValue(entry);
      if (qty == null) return;
      const o = document.createElement("option");
      o.value = qty;
      o.textContent = qtyTierLabel(entry);
      elQty.appendChild(o);
    });
    if (tiers.length) {
      const prev = elQty.value;
      const tierQtys = tiers.map((entry) => qtyTierValue(entry)).filter((q) => q != null);
      if (prev !== "" && (!prev || !tierQtys.some((q) => String(q) === String(prev)))) {
        elQty.value = isBoardCover && product.qty_optional ? "" : String(tierQtys[0]);
      }
    }
  }

  function laminationFamily(name) {
    const n = name.toLowerCase();
    if (n.includes("софт")) return 2;
    if (n.includes("матов")) return 1;
    if (n.includes("глянц")) return 0;
    return 3;
  }

  function laminationMicrons(name) {
    const m = name.match(/(\d+)\s*мк/i);
    return m ? parseInt(m[1], 10) : 999;
  }

  function sortLaminationByDisplayOrder(opts) {
    return opts.slice().sort((a, b) => {
      const fa = laminationFamily(a.name);
      const fb = laminationFamily(b.name);
      if (fa !== fb) return fa - fb;
      const ma = laminationMicrons(a.name);
      const mb = laminationMicrons(b.name);
      if (ma !== mb) return ma - mb;
      return a.name.localeCompare(b.name, "ru");
    });
  }

  function optionMatchesPrintMode(optName) {
    const def = optionByName.get(optName);
    if (!def || !def.for_print_mode) return true;
    if (!elPrint) return true;
    return def.for_print_mode === elPrint.value;
  }

  function fillLaminationSurfaces() {
    if (!isQuarterlyCalendars || !elLaminationSurfaces) return;
    const size = currentSize();
    const values =
      (size && size.lamination_surfaces_options) ||
      product.lamination_surfaces_options ||
      [1];
    const prev = parseInt(elLaminationSurfaces.value, 10);
    elLaminationSurfaces.innerHTML = "";
    values.forEach((v) => {
      const o = document.createElement("option");
      o.value = String(v);
      o.textContent = String(v);
      elLaminationSurfaces.appendChild(o);
    });
    if (values.some((v) => parseInt(v, 10) === prev)) {
      elLaminationSurfaces.value = String(prev);
    } else {
      elLaminationSurfaces.value = String(values[0] || 1);
    }
  }

  function fillLamination() {
    const size = currentSize();
    const mat = currentMaterial();
    const excluded = new Set((mat && mat.excluded_options) || []);
    const opts = optsBySize[size.name] || [];
    const prev = elLamination.value;
    elLamination.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = product.finish_none_label || "Без ламинации";
    elLamination.appendChild(none);
    const available = opts.filter(
      (opt) =>
        finishSelectNames.has(opt.name) &&
        !excluded.has(opt.name) &&
        optionMatchesPrintMode(opt.name)
    );
    const lamOpts = isCalendarProduct
      ? available
      : available.filter((opt) => isLaminationOptionName(opt.name));
    const foilOpts = available.filter((opt) => isFoilOptionName(opt.name));
    // Квартальные календари: глянец (30 → 200 мкм), мат (30 → 150 мкм), затем софт-тач.
    // Остальные календари — по алфавиту, как раньше.
    const sortedLamOpts = isCalendarProduct && !isQuarterlyCalendars
      ? lamOpts.slice().sort((a, b) => a.name.localeCompare(b.name, "ru"))
      : sortLaminationByDisplayOrder(lamOpts);
    sortedLamOpts.forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt.name;
      const priceAdd =
        opt.price_add_by_mode && elPrint && opt.price_add_by_mode[elPrint.value] != null
          ? opt.price_add_by_mode[elPrint.value]
          : opt.price_add;
      const suffix = opt.pending ? "  (цена уточняется)" : "  (+" + priceAdd.toFixed(2) + " ₽)";
      o.textContent = opt.name + suffix;
      elLamination.appendChild(o);
    });
    foilOpts
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name, "ru"))
      .forEach((opt) => {
        const o = document.createElement("option");
        o.value = opt.name;
        o.textContent = opt.name + "  (+" + opt.price_add.toFixed(2) + " ₽)";
        elLamination.appendChild(o);
      });
    if (prev && [...elLamination.options].some((x) => x.value === prev)) {
      elLamination.value = prev;
    }
  }

  function formatCountOptionPrice(unitPrice, count, optDef) {
    const total = unitPrice * (Number.isFinite(count) ? count : 0);
    if (total <= 0) return "0.00 ₽";
    const suffix = optDef && optDef.kind === "extra_layout" ? " к заказу" : "";
    return "+" + total.toFixed(2) + " ₽" + suffix;
  }

  function countOptionUnitPrice(optDef, preview) {
    if (optDef.kind === "extra_layout") {
      return optDef.cost_add || 0;
    }
    return preview ? preview.price_add : 0;
  }

  function countOptionBillable(optDef, count) {
    if (optDef.kind === "extra_layout") {
      const n = Number.isFinite(count) ? count : 0;
      return Math.max(0, n - 1);
    }
    return Number.isFinite(count) ? count : 0;
  }

  function countOptionDefault(optDef) {
    if (optDef.count != null) return optDef.count;
    return optDef.kind === "extra_layout" ? 1 : 0;
  }

  function currentQty() {
    if (!elQty) return 0;
    const q = parseInt(elQty.value, 10);
    return Number.isFinite(q) && q > 0 ? q : 0;
  }

  function countOptionMax(optDef) {
    if (optDef.kind === "extra_layout") {
      const q = currentQty();
      return q > 0 ? q : 1;
    }
    return optDef.count_max != null ? optDef.count_max : 10;
  }

  function clampCountInput(inp) {
    const min = parseInt(inp.min, 10);
    const max = parseInt(inp.max, 10);
    let v = parseInt(inp.value, 10);
    if (!Number.isFinite(v)) v = 0;
    if (Number.isFinite(min)) v = Math.max(min, v);
    if (Number.isFinite(max)) v = Math.min(max, v);
    inp.value = String(v);
    return v;
  }

  function updateCountOptionLimits() {
    elOptions.querySelectorAll("input[data-count-option]").forEach((inp) => {
      const name = inp.dataset.countOption;
      const optDef = countOptionDefs.find((o) => o.name === name);
      if (!optDef) return;
      const max = countOptionMax(optDef);
      inp.max = String(max);
      clampCountInput(inp);
    });
  }

  function updateCountOptionPrices() {
    elOptions.querySelectorAll("input[data-count-option]").forEach((inp) => {
      const name = inp.dataset.countOption;
      const optDef = countOptionDefs.find((o) => o.name === name);
      const unit = parseFloat(inp.dataset.unitPrice || "0");
      const count = parseInt(inp.value, 10);
      const billable = optDef ? countOptionBillable(optDef, count) : count;
      const priceEl = inp.closest(".opt-count")?.querySelector(".opt-price");
      if (priceEl) priceEl.textContent = formatCountOptionPrice(unit, billable, optDef);
    });
  }

  function boardMountIcon(iconId) {
    if (iconId === "rings") {
      return '<img class="mount-icon" src="/static/img/mount-rings.webp" alt="">';
    }
    if (iconId === "bolts") {
      return '<img class="mount-icon" src="/static/img/mount-bolts.jpg" alt="">';
    }
    if (iconId === "spring") {
      return '<svg class="mount-icon" viewBox="0 0 24 32" width="20" height="28" aria-hidden="true">'
        + '<path d="M9 3 L15 6 L9 9 L15 12 L9 15 L15 18 L9 21 L15 24 L9 27 L15 30" fill="none" stroke="currentColor" stroke-width="1.5"/>'
        + "</svg>";
    }
    return "";
  }

  function setupMountImageLightbox() {
    const overlay = document.getElementById("mount-lightbox");
    if (!overlay) return;
    const imgEl = overlay.querySelector("img");
    document.addEventListener("click", (e) => {
      const img = e.target.closest(".mount-icon");
      if (!img) return;
      imgEl.src = img.getAttribute("src");
      overlay.classList.remove("hidden");
    });
    overlay.addEventListener("click", () => overlay.classList.add("hidden"));
  }

  function updateBoardMountLock() {
    if (!isBoardCover) return;
    const inputs = Array.from(elOptions.querySelectorAll("input[data-count-option]"));
    const mountInputs = inputs.filter((inp) => {
      const def = countOptionDefs.find((o) => o.name === inp.dataset.countOption);
      return def && def.kind === "board_mount";
    });
    if (mountInputs.length < 2) return;
    const active = mountInputs.find((inp) => parseInt(inp.value, 10) > 0);
    mountInputs.forEach((inp) => {
      if (active && inp !== active) {
        inp.disabled = true;
        inp.value = "0";
      } else {
        inp.disabled = false;
      }
    });
  }

  function fillOtherOptions() {
    const size = currentSize();
    const opts = optsBySize[size.name] || [];
    const mat = currentMaterial();
    const excluded = new Set((mat && mat.excluded_options) || []);
    const checked = new Set(
      Array.from(elOptions.querySelectorAll('input[type="checkbox"]:checked')).map((i) => i.value)
    );
    const prevCounts = {};
    elOptions.querySelectorAll("input[data-count-option]").forEach((inp) => {
      prevCounts[inp.dataset.countOption] = inp.value;
    });
    elOptions.innerHTML = "";

    countOptionDefs
      .filter((optDef) => !excluded.has(optDef.name))
      .forEach((optDef) => {
        const preview = opts.find((o) => o.name === optDef.name);
        const unitPrice = countOptionUnitPrice(optDef, preview);
        const def = countOptionDefault(optDef);
        const min = optDef.count_min != null ? optDef.count_min : 0;
        const max = countOptionMax(optDef);
        const prev = prevCounts[optDef.name];
        let val = prev !== undefined ? prev : String(def);
        const prevNum = parseInt(val, 10);
        if (Number.isFinite(prevNum) && prevNum > max) val = String(max);
        if (optDef.kind === "extra_layout") {
          const n = parseInt(val, 10);
          if (!Number.isFinite(n) || n < min) val = String(min);
        }
        const countNum = parseInt(val, 10) || 0;
        const billable = countOptionBillable(optDef, countNum);
        const hint = optDef.count_hint
          ? '<span class="muted small opt-count-hint">' + optDef.count_hint + "</span>"
          : "";
        const mountIcon = optDef.icon ? boardMountIcon(optDef.icon) : "";
        const wrap = document.createElement("label");
        wrap.className = "opt-item opt-count";
        const safeName = optDef.name.replace(/"/g, "&quot;");
        wrap.innerHTML =
          "<span class=\"opt-count-label\">" +
          (mountIcon ? '<span class="opt-count-title">' + mountIcon + optDef.name + "</span>" : optDef.name) +
          hint +
          "</span>" +
          '<input type="number" class="opt-count-input" data-count-option="' +
          safeName +
          '" data-unit-price="' +
          unitPrice +
          '" min="' +
          min +
          '" max="' +
          max +
          '" value="' +
          val +
          '">' +
          '<span class="opt-price">' +
          formatCountOptionPrice(unitPrice, billable, optDef) +
          "</span>";
        elOptions.appendChild(wrap);
      });

    opts
      .filter((opt) => otherOptionNames.has(opt.name) && !excluded.has(opt.name))
      .forEach((opt) => {
        const wrap = document.createElement("label");
        wrap.className = "opt-item";
        const checkedAttr = checked.has(opt.name) ? " checked" : "";
        const priceLabel = opt.pending
          ? "цена уточняется"
          : "+" + opt.price_add.toFixed(2) + " ₽";
        wrap.innerHTML =
          '<input type="checkbox" value="' +
          opt.name.replace(/"/g, "&quot;") +
          '"' +
          checkedAttr +
          ">" +
          "<span>" +
          opt.name +
          "</span>" +
          '<span class="opt-price">' +
          priceLabel +
          "</span>";
        elOptions.appendChild(wrap);
      });

    updateCountOptionLimits();
    updateOptionExclusionState();
  }

  function toggleOptionFields() {
    const lamField = document.getElementById("field-lamination");
    const lamSurfacesField = document.getElementById("field-lamination-surfaces");
    const otherField = document.getElementById("field-other-options");
    const hideOpts = isOffsetTable || isSublimation || isPvdBags || isPaperBags;
    if (lamField) lamField.classList.toggle("hidden", hideOpts || elLamination.options.length <= 1);
    if (lamSurfacesField) {
      const size = currentSize();
      const surfaceValues =
        (size && size.lamination_surfaces_options) ||
        product.lamination_surfaces_options ||
        [];
      const showSurfaces =
        isQuarterlyCalendars &&
        !!elLaminationSurfaces &&
        !hideOpts &&
        elLamination.options.length > 1 &&
        surfaceValues.length > 1;
      lamSurfacesField.classList.toggle("hidden", !showSurfaces);
    }
    if (otherField) otherField.classList.toggle("hidden", hideOpts || !elOptions.children.length);
  }

  function rebuild() {
    if (isOffsetTable) fillOffsetMaterials();
    fillQty();
    if (isOffsetTable) fillDelivery();
    if (isBrandedCups && fillMaterials) fillMaterials();
    updateBrandedCupsNote();
    if (isEnvelopes || isCalendarProduct) fillPrint();
    if (isQuarterlyCalendars) fillLaminationSurfaces();
    fillLamination();
    fillOtherOptions();
    if (isBoardCover) {
      syncBoardCustom();
      updateBoardMountLock();
    }
    toggleOptionFields();
    if (isNotebook) updateNotebookCoverNote();
    if (isQuarterlyCalendars) {
      elOptions.querySelectorAll("input[type=checkbox]").forEach((inp) => {
        const preview = (optsBySize[currentSize().name] || []).find((o) => o.name === inp.value);
        if (preview && preview.pending) {
          const priceEl = inp.closest(".opt-item")?.querySelector(".opt-price");
          if (priceEl) priceEl.textContent = "цена уточняется";
        }
      });
      updateQuarterlyPreview();
    }
    if (isPaperBags) updatePaperBagPreview();
    updateLanyardUi();
  }

  function isCustomMaterialSelected() {
    return !isNotebook && customMaterial && elMat && elMat.value === customMaterial.name;
  }

  function toggleCustomMaterialBox() {
    if (!customMatBox) return;
    if (isCustomMaterialSelected()) {
      customMatBox.classList.remove("hidden");
      if (!elCustomPrice.value && customMaterial.price_a3) {
        elCustomPrice.placeholder = Number(customMaterial.price_a3).toFixed(2);
      }
      elCustomPrice.focus();
    } else {
      customMatBox.classList.add("hidden");
    }
  }

  function gatherOptions() {
    const opts = [];
    if (elLamination.value) opts.push(elLamination.value);
    Array.from(elOptions.querySelectorAll('input[type="checkbox"]:checked')).forEach((i) =>
      opts.push(i.value)
    );
    return opts;
  }

  function gatherOptionCounts() {
    const counts = {};
    elOptions.querySelectorAll("input[data-count-option]").forEach((inp) => {
      const n = parseInt(inp.value, 10);
      counts[inp.dataset.countOption] = Number.isFinite(n) ? n : 0;
    });
    return counts;
  }

  function gatherBagOptions() {
    const bagOptions = {};
    document.querySelectorAll("[data-bag-option]").forEach((sel) => {
      bagOptions[sel.dataset.bagOption] = sel.value;
    });
    return bagOptions;
  }

  function gatherPayload() {
    const payload = {
      product: productId,
      size: elSize.value,
      qty: elQty && elQty.value !== "" ? parseInt(elQty.value, 10) : 0,
      options: gatherOptions(),
      option_counts: gatherOptionCounts(),
      discount: discountField ? discountField.getValue() : parseFloat(elDiscount?.value || "0"),
    };
    if (isNotebook) {
      payload.block_mode = elBlockMode ? elBlockMode.value : "0+0";
      payload.page_count = elPageCount ? parseInt(elPageCount.value, 10) || product.default_page_count || 40 : 40;
      payload.material = product.cover_material || "Блокнот";
    } else if (isCalendarProduct) {
      payload.print_mode = elPrint ? elPrint.value : "";
      if (elLaminationSurfaces) {
        payload.lamination_surfaces = parseInt(elLaminationSurfaces.value, 10) || 1;
      }
    } else if (isSublimation) {
      payload.material = elMat ? elMat.value : "";
    } else if (isOffsetTable) {
      payload.material = elMat ? elMat.value : "";
      payload.print_mode = elPrint ? elPrint.value : "";
      payload.delivery_tier = elDelivery ? elDelivery.value : "";
      if (hasOffsetLamination && elLaminationMode) {
        payload.lamination = elLaminationMode.value;
      }
    } else if (isPvdBags) {
      payload.material = elMat ? elMat.value : "";
      payload.print_mode = elPrint ? elPrint.value : "";
    } else if (isPaperBags) {
      payload.bag_options = gatherBagOptions();
    } else if (isBoardCover) {
      payload.board_covers = elBoardCovers ? parseInt(elBoardCovers.value, 10) : 1;
      payload.lamination = elBoardLamination ? elBoardLamination.value : "none";
      if (elBoardMode) payload.board_mode = elBoardMode.value;
      if (elBoardMode && elBoardMode.value === "custom") {
        payload.custom_width = elBoardWidth ? elBoardWidth.value : "";
        payload.custom_height = elBoardHeight ? elBoardHeight.value : "";
        payload.custom_parts = elBoardParts ? (parseInt(elBoardParts.value, 10) || 1) : 1;
      }
    } else if (isBrandedCups) {
      payload.material = elMat ? elMat.value : "";
    } else {
      payload.material = elMat ? elMat.value : "";
      payload.print_mode = elPrint ? elPrint.value : "4+0";
      if (isCustomMaterialSelected()) {
        const v = elCustomPrice.value.trim();
        if (v !== "") payload.material_price_a3 = parseFloat(v);
      }
    }
    if (isBeizhi && lanyardOption) {
      payload.lanyard = gatherLanyardPayload();
    }
    return payload;
  }

  function showPendingCustomPrice() {
    resultEmpty.textContent = "Укажите стоимость нестандартной бумаги за лист А3.";
    resultEmpty.classList.remove("hidden");
    resultBox.classList.add("hidden");
    resultActions.classList.add("hidden");
    copyBtn.disabled = true;
    saveBtn.disabled = true;
    lastResult = null;
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

  function handleCountOptionChange(inp) {
    if (!inp || !inp.matches("input[data-count-option]")) return;
    clampCountInput(inp);
    updateCountOptionPrices();
    updateBoardMountLock();
    if (calcTimer) clearTimeout(calcTimer);
    calcTimer = null;
    runCalc();
  }

  async function runCalc() {
    if (isCustomMaterialSelected() && !elCustomPrice.value.trim()) {
      showPendingCustomPrice();
      return;
    }
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

  elSize.addEventListener("change", () => {
    rebuild();
    if (isPaperBags) updatePaperBagPreview();
    scheduleCalc();
  });
  elBoardCovers && elBoardCovers.addEventListener("change", scheduleCalc);
  elBoardLamination && elBoardLamination.addEventListener("change", scheduleCalc);
  elBoardMode &&
    elBoardMode.addEventListener("change", () => {
      if (syncBoardCustom()) {
        fillOtherOptions();
        toggleOptionFields();
        updateBoardMountLock();
      }
      scheduleCalc();
    });
  [elBoardWidth, elBoardHeight, elBoardParts].forEach((inp) => {
    inp &&
      inp.addEventListener("input", () => {
        if (syncBoardCustom()) {
          fillOtherOptions();
          toggleOptionFields();
          updateBoardMountLock();
        }
        scheduleCalc();
      });
  });
  elMat && elMat.addEventListener("change", () => {
    if (isOffsetTable) {
      fillQty();
      fillDelivery();
    }
    fillPrint();
    fillQty();
    if (isBrandedCups) ensureBrandedCupsQtyForMaterial && ensureBrandedCupsQtyForMaterial();
    updateBrandedCupsNote();
    fillLamination();
    fillOtherOptions();
    toggleOptionFields();
    toggleCustomMaterialBox();
    scheduleCalc();
  });
  elCustomPrice && elCustomPrice.addEventListener("input", scheduleCalc);
  elPrint &&
    elPrint.addEventListener("change", () => {
      togglePrintModeNote();
      togglePvdPrintNote();
      if (isWallCalendars) {
        fillLamination();
        toggleOptionFields();
      }
      if (isQuarterlyCalendars) updateQuarterlyPreview();
      scheduleCalc();
    });
  elBlockMode && elBlockMode.addEventListener("change", scheduleCalc);
  elPageCount && elPageCount.addEventListener("change", scheduleCalc);
  elDelivery && elDelivery.addEventListener("change", scheduleCalc);
  elLaminationMode && elLaminationMode.addEventListener("change", scheduleCalc);
  if (elQty) {
    elQty.addEventListener(isSublimation ? "input" : "change", () => {
      if (isBrandedCups) {
        const mat = brandedCupsMaterial();
        const minQty = mat && mat.qty_min ? parseInt(mat.qty_min, 10) : 0;
        const qty = parseInt(elQty.value, 10) || 0;
        if (minQty && qty < minQty && product.materials.length) {
          const fallback =
            product.materials.find((m) => !m.qty_min || m.qty_min <= qty) || product.materials[0];
          if (fallback && elMat.value !== fallback.name) elMat.value = fallback.name;
          updateBrandedCupsNote();
        }
      }
      updateCountOptionLimits();
      updateCountOptionPrices();
      scheduleCalc();
    });
  }
  elLamination.addEventListener("change", scheduleCalc);
  if (elLaminationSurfaces) elLaminationSurfaces.addEventListener("change", scheduleCalc);
  if (discountField) discountField.bind(scheduleCalc);
  else if (elDiscount) elDiscount.addEventListener("change", scheduleCalc);
  elOptions.addEventListener("change", (e) => {
    if (e.target && e.target.matches('input[type="checkbox"]')) {
      applyOptionExclusions(e.target);
      updateOptionExclusionState();
      scheduleCalc();
      return;
    }
    if (e.target && e.target.matches("input[data-count-option]")) {
      handleCountOptionChange(e.target);
      return;
    }
    scheduleCalc();
  });
  elOptions.addEventListener("input", (e) => {
    if (e.target && e.target.matches("input[data-count-option]")) {
      const inp = e.target;
      requestAnimationFrame(() => handleCountOptionChange(inp));
    }
  });
  elOptions.addEventListener("keyup", (e) => {
    if (
      e.target &&
      e.target.matches("input[data-count-option]") &&
      (e.key === "ArrowUp" || e.key === "ArrowDown")
    ) {
      handleCountOptionChange(e.target);
    }
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

  document.querySelectorAll("[data-bag-option]").forEach((sel) => {
    sel.addEventListener("change", scheduleCalc);
  });

  if (elLanyardEnabled) {
    elLanyardEnabled.addEventListener("change", () => {
      updateLanyardUi();
      scheduleCalc();
    });
  }
  document.querySelectorAll('input[name="lanyard-attachment"]').forEach((inp) => {
    inp.addEventListener("change", () => {
      updateLanyardUi();
      scheduleCalc();
    });
  });
  elLanyardColor && elLanyardColor.addEventListener("change", scheduleCalc);

  if (elPapkiKind) {
    elPapkiKind.addEventListener("change", () => {
      const nextId = elPapkiKind.value;
      if (nextId && nextId !== productId) {
        window.location.assign(`/calc/${encodeURIComponent(nextId)}`);
      }
    });
  }

  if (!isNotebook && !isCalendarProduct && !isSublimation && !isOffsetTable && !isPvdBags && !isPaperBags) fillPrint();
  rebuild();
  if (isQuarterlyCalendars) updateQuarterlyPreview();
  if (isPaperBags) updatePaperBagPreview();
  updateLanyardUi();
  toggleCustomMaterialBox();
  setupMountImageLightbox();
  scheduleCalc();
})();

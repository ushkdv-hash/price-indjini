(function (global) {
  const CUSTOM_VALUE = "__custom__";

  function clampDiscount(value, maxPct) {
    const n = parseFloat(value);
    if (!Number.isFinite(n) || n < 0) return 0;
    return Math.min(n, maxPct);
  }

  function init(container) {
    if (!container) return null;

    const config = {
      max_percent: parseFloat(container.dataset.discountMax || "20"),
      custom_only: container.dataset.discountCustomOnly === "1",
    };
    const select = container.querySelector("#f-discount");
    const customWrap = container.querySelector("#discount-custom-wrap");
    const customInput = container.querySelector("#f-discount-custom");

    function isCustomMode() {
      if (config.custom_only) return true;
      return select && select.value === CUSTOM_VALUE;
    }

    function updateCustomVisibility() {
      if (!customWrap) return;
      customWrap.classList.toggle("hidden", !isCustomMode());
    }

    function getValue() {
      if (isCustomMode() && customInput) {
        return clampDiscount(customInput.value, config.max_percent);
      }
      if (select) return clampDiscount(select.value, config.max_percent);
      return 0;
    }

    function bind(onChange) {
      if (select) select.addEventListener("change", onChange);
      if (customInput) customInput.addEventListener("input", onChange);
      return api;
    }

    if (select) select.addEventListener("change", updateCustomVisibility);
    if (customInput) {
      customInput.addEventListener("input", () => {
        const v = parseFloat(customInput.value);
        if (Number.isFinite(v) && v > config.max_percent) {
          customInput.value = String(config.max_percent);
        }
      });
    }
    updateCustomVisibility();

    const api = { getValue, bind, config };
    return api;
  }

  global.DiscountField = { init, CUSTOM_VALUE };
})(window);

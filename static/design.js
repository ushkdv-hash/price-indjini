(function () {
  const searchInput = document.getElementById("design-search");
  const catButtons = document.querySelectorAll(".design-cat-btn");
  const categoryBlocks = document.querySelectorAll("[data-category-block]");
  const items = document.querySelectorAll(".design-service");
  const resultCount = document.getElementById("design-result-count");
  const emptyMsg = document.getElementById("design-empty");

  let activeCategory = "all";

  function normalize(s) {
    return (s || "").toLowerCase().trim();
  }

  function applyFilter() {
    const q = normalize(searchInput ? searchInput.value : "");
    let visible = 0;

    categoryBlocks.forEach((block) => {
      const catId = block.dataset.categoryBlock;
      const catMatch = activeCategory === "all" || activeCategory === catId;
      let catVisible = 0;

      block.querySelectorAll(".design-service").forEach((item) => {
        const text = item.dataset.search || "";
        const match = catMatch && (!q || text.includes(q));
        item.classList.toggle("hidden", !match);
        if (match) {
          visible += 1;
          catVisible += 1;
        }
      });

      block.classList.toggle("hidden", !catMatch || catVisible === 0);
    });

    if (resultCount) {
      if (activeCategory === "all" && !q) {
        resultCount.textContent = items.length + " услуг";
      } else {
        resultCount.textContent = "Показано: " + visible + " из " + items.length;
      }
    }
    if (emptyMsg) emptyMsg.classList.toggle("hidden", visible > 0);
  }

  if (searchInput) {
    searchInput.addEventListener("input", applyFilter);
  }

  catButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      catButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeCategory = btn.dataset.category || "all";
      applyFilter();
    });
  });

  applyFilter();
})();

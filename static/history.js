(function () {
  const historyData = JSON.parse(document.getElementById("history-data").textContent);
  const showAdminDetails = !!JSON.parse(document.getElementById("history-meta").textContent).show_admin_details;

  const modal = document.getElementById("history-detail-modal");
  const titleEl = document.getElementById("history-detail-title");
  const metaEl = document.getElementById("history-detail-meta");
  const bodyEl = document.getElementById("history-detail-body");
  const copyBtn = document.getElementById("history-copy-btn");

  let currentEntry = null;

  function formatWhen(iso) {
    if (!iso) return "";
    return iso.replace("T", " ").slice(0, 16);
  }

  function openModal(entry) {
    currentEntry = entry;
    const r = entry.result;
    titleEl.textContent = entry.title || "Расчёт без названия";
    const metaParts = [formatWhen(entry.when)];
    if (entry.user) metaParts.push(entry.user);
    metaEl.textContent = metaParts.join(" · ");
    bodyEl.innerHTML = CalcResult.renderResultHtml(r, showAdminDetails);
    modal.classList.remove("hidden");
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.add("hidden");
    document.body.classList.remove("modal-open");
    currentEntry = null;
  }

  document.querySelectorAll(".history-row").forEach((row) => {
    row.addEventListener("click", () => {
      const idx = parseInt(row.dataset.index, 10);
      if (!Number.isNaN(idx) && historyData[idx]) openModal(historyData[idx]);
    });
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        row.click();
      }
    });
  });

  modal.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
  });

  copyBtn.addEventListener("click", () => {
    if (!currentEntry || !currentEntry.result) return;
    const text = currentEntry.result.text || bodyEl.innerText;
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.textContent = "Скопировано ✓";
      setTimeout(() => (copyBtn.textContent = "Скопировать"), 1500);
    });
  });
})();

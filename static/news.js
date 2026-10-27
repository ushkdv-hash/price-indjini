(function () {
  const modal = document.getElementById("news-modal");
  if (!modal) return;

  const titleEl = document.getElementById("news-modal-title");
  const bodyEl = document.getElementById("news-modal-body");
  const dateEl = document.getElementById("news-modal-date");
  const dismissBtn = document.getElementById("news-dismiss-btn");
  const queueEl = document.getElementById("news-modal-queue");
  let queue = [];
  let current = null;

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleString("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function openModal() {
    modal.classList.remove("hidden");
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    modal.classList.add("hidden");
    document.body.classList.remove("modal-open");
  }

  function showCurrent() {
    current = queue[0] || null;
    if (!current) {
      closeModal();
      return;
    }
    if (titleEl) titleEl.textContent = current.title || "Новость";
    if (bodyEl) {
      bodyEl.textContent = "";
      (current.body || "").split("\n").forEach(function (line, i) {
        if (i) bodyEl.appendChild(document.createElement("br"));
        bodyEl.appendChild(document.createTextNode(line));
      });
    }
    if (dateEl) dateEl.textContent = formatDate(current.created_at);
    if (queueEl) {
      queueEl.textContent =
        queue.length > 1 ? "Ещё " + (queue.length - 1) + " новост(и/ей)" : "";
      queueEl.classList.toggle("hidden", queue.length <= 1);
    }
    openModal();
  }

  async function dismissCurrent() {
    if (!current) return;
    const id = current.id;
    if (dismissBtn) dismissBtn.disabled = true;
    try {
      await fetch("/api/news/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ news_id: id }),
      });
    } catch (_err) {
      /* скрываем локально даже при ошибке сети */
    }
    queue = queue.filter(function (item) {
      return item.id !== id;
    });
    if (dismissBtn) dismissBtn.disabled = false;
    showCurrent();
  }

  modal.querySelectorAll("[data-close-news]").forEach(function (el) {
    el.addEventListener("click", dismissCurrent);
  });
  if (dismissBtn) dismissBtn.addEventListener("click", dismissCurrent);

  fetch("/api/news/unread")
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (!data.ok || !Array.isArray(data.items) || !data.items.length) return;
      queue = data.items.slice();
      showCurrent();
    })
    .catch(function () {});
})();

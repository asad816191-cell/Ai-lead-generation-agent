(() => {
  "use strict";
  document.querySelectorAll(".js-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      if (!confirm(`Delete search #${id} and its saved leads?`)) return;
      try {
        const res = await fetch(`/api/search/${id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Delete failed");
        btn.closest("tr").remove();
      } catch (e) {
        const box = document.querySelector("#alerts");
        box.innerHTML = '<div class="alert alert-danger">Could not delete that search.</div>';
      }
    });
  });
})();

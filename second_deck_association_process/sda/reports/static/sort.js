"use strict";

document.querySelectorAll("table.sortable").forEach((table) => {
  table.querySelectorAll("thead th[data-sort]").forEach((header) => {
    header.tabIndex = 0;
    header.setAttribute("role", "button");
    header.setAttribute("aria-label", `Sort by ${header.textContent.trim()}`);
    const sort = () => {
      const body = table.tBodies[0];
      const rows = Array.from(body.rows);
      const columnIndex = Number(header.dataset.column ?? header.cellIndex);
      const ascending = header.dataset.direction !== "asc";
      table.querySelectorAll("thead th[data-sort]").forEach((item) => delete item.dataset.direction);
      header.dataset.direction = ascending ? "asc" : "desc";
      rows.sort((left, right) => {
        const a = left.cells[columnIndex]?.textContent.trim() ?? "";
        const b = right.cells[columnIndex]?.textContent.trim() ?? "";
        const numericA = Number(a.replace(/[^0-9.+-]/g, ""));
        const numericB = Number(b.replace(/[^0-9.+-]/g, ""));
        const comparison = a && b && Number.isFinite(numericA) && Number.isFinite(numericB)
          ? numericA - numericB
          : a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
        return ascending ? comparison : -comparison;
      });
      rows.forEach((row) => body.appendChild(row));
    };
    header.addEventListener("click", sort);
    header.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        sort();
      }
    });
  });
});
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

document.querySelectorAll("form[data-table-filter]").forEach((form) => {
  const table = document.querySelector(form.dataset.tableFilter);
  if (!table?.tBodies[0]) return;

  const rows = Array.from(table.tBodies[0].rows).filter((row) => !row.classList.contains("empty-row"));
  const controls = Object.fromEntries(
    Array.from(form.querySelectorAll("[data-filter]")).map((control) => [control.dataset.filter, control]),
  );
  const status = form.querySelector("[data-filter-status]");

  const applyFilters = () => {
    const search = (controls.search?.value ?? "").trim().toLocaleLowerCase();
    const year = controls["fa-year"]?.value ?? "";
    const position = controls.position?.value ?? "";
    const team = controls.team?.value ?? "";
    let visible = 0;
    for (const row of rows) {
      const matches = (!year || row.dataset.faYear === year)
        && (!position || row.dataset.position?.includes(`|${position}|`))
        && (!team || row.dataset.team === team)
        && (!search || `${row.dataset.search ?? ""} ${row.textContent}`.toLocaleLowerCase().includes(search));
      row.hidden = !matches;
      if (matches) visible += 1;
    }
    if (status) status.textContent = `${visible} of ${rows.length} players`;
  };

  form.addEventListener("input", applyFilters);
  form.addEventListener("change", applyFilters);
  form.addEventListener("reset", () => window.setTimeout(applyFilters, 0));
  applyFilters();
});
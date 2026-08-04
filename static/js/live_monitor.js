/* ============================================================
   SafeExam — live_monitor.js
   Admin dashboard: polls the live monitor feed + violation stats
   and renders the student status table and analytics chart.
   ============================================================ */
(function () {
  "use strict";

  const cfg = window.SEB_MONITOR || {};
  const body = document.getElementById("monitorBody");
  const empty = document.getElementById("monitorEmpty");
  let chart = null;
  const alerted = new Set();

  function statusClass(status) {
    return "status-" + (status.split(" ")[0]); // "High Risk" -> status-High
  }

  function actionButtons(row) {
    const lock = `<form method="POST" action="${cfg.lockUrl}/${row.submission_id}/lock" class="inline-form">
        <input type="hidden" name="csrf_token" value="${cfg.csrf}">
        <button class="btn btn-sm btn-warning" title="Lock attempt">Lock</button></form>`;
    const dq = `<form method="POST" action="${cfg.lockUrl}/${row.submission_id}/disqualify" class="inline-form">
        <input type="hidden" name="csrf_token" value="${cfg.csrf}">
        <button class="btn btn-sm btn-danger" title="Disqualify">DQ</button></form>`;
    const view = `<a class="btn btn-sm btn-ghost" href="${cfg.detailUrl}/${row.submission_id}">View</a>`;
    const live = row.completed ? '' : `<a class="btn btn-sm btn-danger" href="${cfg.liveUrl}/${row.submission_id}" title="Watch live screen">Live</a>`;
    return `<div style="display:flex;gap:6px;flex-wrap:wrap;">${live}${view}${lock}${dq}</div>`;
  }

  function loadTable() {
    fetch(cfg.feedUrl)
      .then((r) => r.json())
      .then((rows) => {
        if (!body) return;
        if (!rows.length) {
          body.innerHTML = "";
          if (empty) empty.style.display = "block";
          return;
        }
        if (empty) empty.style.display = "none";
        body.innerHTML = rows
          .map((row) => {
            if (row.status === "High Risk" && !alerted.has(row.submission_id)) {
              alerted.add(row.submission_id);
            }
            const time = row.remaining
              ? Math.floor(row.remaining / 60) + "m " + (row.remaining % 60) + "s"
              : "—";
            return `<tr>
              <td>${escapeHtml(row.student)}</td>
              <td>${escapeHtml(row.exam)}</td>
              <td>${row.violations} <span class="tag">(sev ${row.severity})</span></td>
              <td>${row.snapshots}</td>
              <td>${time}</td>
              <td class="${statusClass(row.status)}">${row.status}</td>
              <td>${actionButtons(row)}</td>
            </tr>`;
          })
          .join("");
      })
      .catch(() => {});
  }

  function loadChart() {
    if (typeof Chart === "undefined") return;
    fetch(cfg.statsUrl)
      .then((r) => r.json())
      .then((data) => {
        const labels = Object.keys(data);
        const values = Object.values(data);
        const ctx = document.getElementById("violationChart");
        if (!ctx) return;
        if (chart) chart.destroy();
        chart = new Chart(ctx, {
          type: "bar",
          data: {
            labels: labels,
            datasets: [
              {
                label: "Violations",
                data: values,
                backgroundColor: "rgba(239, 68, 68, 0.55)",
                borderColor: "rgba(239, 68, 68, 1)",
                borderWidth: 1,
                borderRadius: 6,
              },
            ],
          },
          options: {
            plugins: { legend: { labels: { color: "#6b7a90" } } },
            scales: {
              x: { ticks: { color: "#6b7a90" }, grid: { color: "#e4e9f2" } },
              y: { ticks: { color: "#6b7a90" }, grid: { color: "#e4e9f2" }, beginAtZero: true },
            },
          },
        });
      })
      .catch(() => {});
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  loadTable();
  loadChart();
  setInterval(loadTable, 3000);
  setInterval(loadChart, 6000);
})();

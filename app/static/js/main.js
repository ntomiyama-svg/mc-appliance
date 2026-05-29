// mc-appliance front-end helpers (vanilla JS, no framework).

// Live-refresh dashboard metrics every 5s if the metric elements are present.
function refreshMetrics() {
  const cpu = document.getElementById("m-cpu");
  if (!cpu) return;
  fetch("/api/metrics")
    .then((r) => r.json())
    .then((m) => {
      setMetric("cpu", m.cpu_percent);
      setMetric("mem", m.mem_percent);
      setMetric("disk", m.disk_percent);
    })
    .catch(() => {});
}

function setMetric(key, pct) {
  const valEl = document.getElementById("m-" + key);
  const barEl = document.getElementById("bar-" + key);
  if (valEl) valEl.textContent = pct + "%";
  if (barEl) {
    barEl.style.width = pct + "%";
    barEl.parentElement.classList.remove("warn", "danger");
    if (pct >= 90) barEl.parentElement.classList.add("danger");
    else if (pct >= 70) barEl.parentElement.classList.add("warn");
  }
}

// Auto-refresh latest.log tail on the server detail page.
function refreshLog() {
  const box = document.getElementById("logbox");
  if (!box) return;
  const url = box.dataset.url;
  if (!url) return;
  fetch(url)
    .then((r) => r.text())
    .then((t) => {
      box.textContent = t;
      box.scrollTop = box.scrollHeight;
    })
    .catch(() => {});
}

// Confirm before potentially disruptive stop/restart actions.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form.dataset.confirm) {
    if (!window.confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  }
});

document.addEventListener("DOMContentLoaded", () => {
  refreshMetrics();
  setInterval(refreshMetrics, 5000);

  const box = document.getElementById("logbox");
  if (box) {
    box.scrollTop = box.scrollHeight;
    setInterval(refreshLog, 7000);
  }
});

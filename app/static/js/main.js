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

// ----- RCON console (server detail page) -----------------------------------

function rconServerId() {
  const el = document.getElementById("rcon-console") || document.getElementById("rcon");
  return el ? el.dataset.serverId : null;
}

// Render an {ok, message, diagnosis, response} result into a target box.
function rconShow(targetId, result) {
  const box = document.getElementById(targetId);
  if (!box) return;
  let text = (result.ok ? "✓ " : "✗ ") + (result.message || "");
  if (result.diagnosis) text += "\nHint: " + result.diagnosis;
  if (result.response) text += "\n\n" + result.response;
  box.textContent = text;
  box.classList.remove("ok-box", "err-box");
  box.classList.add(result.ok ? "ok-box" : "err-box");
}

// POST to an RCON endpoint and parse JSON. If the session has expired the
// AuthMiddleware redirects to /login (HTML), which we detect and surface.
function rconPost(path, params) {
  const id = rconServerId();
  const body = new URLSearchParams(params || {});
  return fetch("/servers/" + id + path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  }).then((r) => {
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("application/json")) {
      throw new Error("Not authenticated or unexpected response — try reloading the page.");
    }
    return r.json();
  });
}

function initRcon() {
  if (!document.getElementById("rcon-console") && !document.getElementById("rcon")) return;

  const testBtn = document.getElementById("rcon-test-btn");
  if (testBtn) {
    testBtn.addEventListener("click", () => {
      rconShow("rcon-test-result", { ok: true, message: "Testing…" });
      rconPost("/rcon/test", {})
        .then((res) => rconShow("rcon-test-result", res))
        .catch((e) => rconShow("rcon-test-result", { ok: false, message: e.message }));
    });
  }

  const genBtn = document.getElementById("rcon-genpw-btn");
  if (genBtn) {
    genBtn.addEventListener("click", () => {
      rconPost("/rcon/generate-password", {})
        .then((res) => {
          const inp = document.getElementById("rcon-password");
          if (res.ok && inp) {
            inp.value = res.password;
            inp.focus();
          }
        })
        .catch((e) => alert("Could not generate password: " + e.message));
    });
  }

  function sendCommand(cmd) {
    if (!cmd) return;
    rconShow("rcon-output", { ok: true, message: "Running: " + cmd });
    rconPost("/rcon/command", { command: cmd })
      .then((res) => rconShow("rcon-output", res))
      .catch((e) => rconShow("rcon-output", { ok: false, message: e.message }));
  }

  const sendBtn = document.getElementById("rcon-send-btn");
  const cmdInput = document.getElementById("rcon-cmd-input");
  if (sendBtn) {
    sendBtn.addEventListener("click", () => sendCommand(cmdInput ? cmdInput.value.trim() : ""));
  }
  if (cmdInput) {
    cmdInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        sendCommand(cmdInput.value.trim());
      }
    });
  }

  document.querySelectorAll("[data-rcon-cmd], [data-rcon-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.rconConfirm && !window.confirm(btn.dataset.rconConfirm)) return;
      if (btn.dataset.rconPrompt) {
        const answer = window.prompt(btn.dataset.rconLabel || "Argument:");
        if (answer === null) return;
        const trimmed = answer.trim();
        if (!trimmed) return;
        sendCommand(btn.dataset.rconPrompt + " " + trimmed);
      } else {
        sendCommand(btn.dataset.rconCmd);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  refreshMetrics();
  setInterval(refreshMetrics, 5000);

  const box = document.getElementById("logbox");
  if (box) {
    box.scrollTop = box.scrollHeight;
    setInterval(refreshLog, 7000);
  }

  initRcon();
});

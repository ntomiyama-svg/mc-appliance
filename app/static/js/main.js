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

  // Show / hide the masked password field.
  const showBtn = document.getElementById("rcon-showpw-btn");
  if (showBtn) {
    showBtn.addEventListener("click", () => {
      const inp = document.getElementById("rcon-password");
      if (!inp) return;
      const showing = inp.type === "text";
      inp.type = showing ? "password" : "text";
      showBtn.textContent = showing ? "👁 Show" : "🙈 Hide";
    });
  }

  const genBtn = document.getElementById("rcon-genpw-btn");
  if (genBtn) {
    genBtn.addEventListener("click", () => {
      const inp = document.getElementById("rcon-password");
      // Confirm before clobbering an already-configured password.
      const form = genBtn.closest("form");
      const alreadySet = form && form.dataset.rconPasswordSet === "1";
      if ((alreadySet || (inp && inp.value)) &&
          !window.confirm("Overwrite the current RCON password with a new random one?")) {
        return;
      }
      rconPost("/rcon/generate-password", {})
        .then((res) => {
          if (!res.ok || !inp) return;
          inp.value = res.password;
          // Reveal the freshly generated password so the admin can note it.
          inp.type = "text";
          if (showBtn) showBtn.textContent = "🙈 Hide";
          inp.focus();
          // Generating a password implies the operator wants RCON on.
          const enable = document.getElementById("rcon-enabled");
          if (enable) enable.checked = true;
          const port = document.getElementById("rcon-port");
          if (port && (!port.value || !port.value.trim())) port.value = "25575";
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

// ----- Server console / live-log viewer (server detail + /console page) -----

// The console JSON endpoints live under /servers/... (not /api/...), so an
// expired session is bounced to /login as an HTML redirect rather than a 401.
// We detect both forms and send the user to the login screen.
function consoleAuthRedirect() {
  window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
}

function consoleFetchJSON(url, opts) {
  return fetch(url, opts).then((r) => {
    if (r.status === 401) {
      consoleAuthRedirect();
      throw new Error("auth");
    }
    if (r.redirected && /\/login/.test(r.url)) {
      consoleAuthRedirect();
      throw new Error("auth");
    }
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("application/json")) {
      if (/\/login/.test(r.url)) {
        consoleAuthRedirect();
        throw new Error("auth");
      }
      throw new Error("Unexpected response (HTTP " + r.status + "). Try reloading.");
    }
    if (r.status >= 500) {
      throw new Error("Server error (HTTP " + r.status + ").");
    }
    return r.json();
  });
}

function conSetText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function initConsole() {
  const root = document.getElementById("console");
  if (!root) return;

  const id = root.dataset.serverId;
  const rconEnabled = root.dataset.rconEnabled === "1";
  const logbox = document.getElementById("console-logbox");
  const linesSel = document.getElementById("con-lines");
  const autoRefresh = document.getElementById("con-autorefresh");
  const autoScroll = document.getElementById("con-autoscroll");
  let activeLog = "latest";

  function lineCount() {
    return linesSel ? linesSel.value : "300";
  }

  function showStatusError(msg) {
    const el = document.getElementById("con-status-error");
    if (el) {
      el.textContent = msg;
      el.style.display = "";
    }
  }
  function hideStatusError() {
    const el = document.getElementById("con-status-error");
    if (el) el.style.display = "none";
  }

  function renderStatus(s) {
    hideStatusError();
    const stateEl = document.getElementById("con-state");
    if (stateEl) {
      stateEl.textContent = s.detected_state || "unknown";
      stateEl.className = "badge " + (s.detected_state || "unknown");
    }
    conSetText("con-reason", s.detected_reason || "");
    conSetText("con-db-status", s.db_status || "—");
    conSetText("con-actual-status", s.actual_status || "—");
    conSetText("con-pid", s.pid != null ? s.pid : "—");
    conSetText("con-proc", s.process_exists ? "yes" : "no");
    let portText = "—";
    if (s.server_port) {
      if (s.port_listening === true) portText = s.server_port + " (listening)";
      else if (s.port_listening === false) portText = s.server_port + " (not listening)";
      else portText = String(s.server_port);
    }
    conSetText("con-port", portText);
    conSetText("con-startup", s.startup_duration_seconds != null ? s.startup_duration_seconds + "s" : "—");
    conSetText("con-stop-method", s.last_stop_method || "—");
    conSetText("con-last-started", s.last_started_at || "—");
    conSetText("con-last-stopped", s.last_stopped_at || "—");
    conSetText("con-latest-mtime", s.latest_log_exists ? (s.latest_log_mtime || "—") : "no file");
    conSetText("con-managed-mtime", s.managed_log_exists ? (s.managed_log_mtime || "—") : "no file");

    const errEl = document.getElementById("con-last-error");
    if (errEl) {
      if (s.last_error_summary) {
        errEl.textContent = "⚠ " + s.last_error_summary;
        errEl.style.display = "";
      } else {
        errEl.style.display = "none";
      }
    }
  }

  function refreshStatus() {
    consoleFetchJSON("/servers/" + id + "/console/status")
      .then(renderStatus)
      .catch((e) => { if (e.message !== "auth") showStatusError(e.message); });
  }

  function refreshLog() {
    if (!logbox) return;
    const path = activeLog === "managed" ? "managed-log" : "latest-log";
    consoleFetchJSON("/servers/" + id + "/console/" + path + "?lines=" + lineCount())
      .then((r) => {
        logbox.textContent = r.text || "";
        if (autoScroll && autoScroll.checked) logbox.scrollTop = logbox.scrollHeight;
      })
      .catch((e) => { if (e.message !== "auth") logbox.textContent = "Error loading log: " + e.message; });
  }

  function refreshAll() {
    refreshStatus();
    refreshLog();
  }

  // Tabs (latest.log / managed log).
  root.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      root.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      activeLog = tab.dataset.log;
      refreshLog();
    });
  });

  const refreshBtn = document.getElementById("con-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshAll);
  if (linesSel) linesSel.addEventListener("change", refreshLog);

  // RCON command box (only present/active when RCON is enabled).
  if (rconEnabled) {
    function consoleSend(cmd) {
      if (!cmd) return;
      rconShow("con-rcon-output", { ok: true, message: "Running: " + cmd });
      const body = new URLSearchParams({ command: cmd });
      consoleFetchJSON("/servers/" + id + "/console/rcon-command", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      })
        .then((res) => rconShow("con-rcon-output", res))
        .catch((e) => { if (e.message !== "auth") rconShow("con-rcon-output", { ok: false, message: e.message }); });
    }

    const sendBtn = document.getElementById("con-send-btn");
    const input = document.getElementById("con-cmd-input");
    if (sendBtn) sendBtn.addEventListener("click", () => consoleSend(input ? input.value.trim() : ""));
    if (input) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          consoleSend(input.value.trim());
        }
      });
    }
    root.querySelectorAll("[data-con-cmd]").forEach((btn) => {
      btn.addEventListener("click", () => consoleSend(btn.dataset.conCmd));
    });
  }

  // Initial paint, then a 5s tick that only runs while the page is visible and
  // auto-refresh is on.
  refreshAll();
  setInterval(() => {
    if (document.hidden) return;
    if (autoRefresh && !autoRefresh.checked) return;
    refreshAll();
  }, 5000);
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
  initConsole();
});

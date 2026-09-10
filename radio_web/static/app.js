(() => {
  "use strict";

  const region = document.getElementById("now-playing");

  let requestInFlight = false;

  async function refreshNowPlaying() {
    if (!region || requestInFlight || document.hidden) return;
    requestInFlight = true;
    try {
      const response = await fetch("/dashboard/now-playing", {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });
      if (!response.ok) return;
      const markup = await response.text();
      if (markup && markup !== region.innerHTML) region.innerHTML = markup;
    } catch (_error) {
      // Keep the last known state and quietly retry on the next interval.
    } finally {
      requestInFlight = false;
    }
  }

  if (region) {
    window.setInterval(refreshNowPlaying, 5000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshNowPlaying();
    });
  }

  const profileSelect = document.getElementById("profile-select");
  const profileNote = document.getElementById("profile-note");
  if (profileSelect && profileNote) {
    const updateProfileNote = () => {
      const option = profileSelect.options[profileSelect.selectedIndex];
      profileNote.textContent = option ? option.getAttribute("data-note") || "" : "";
    };
    profileSelect.addEventListener("change", updateProfileNote);
    updateProfileNote();
  }

  const eqGraph = document.getElementById("eq-graph");
  if (eqGraph) {
    const ns = "http://www.w3.org/2000/svg";
    const bands = [...document.querySelectorAll("[data-eq-band]")];
    const colors = ["#ef5b5b", "#d5a84f", "#9dc74e", "#4fc469", "#57c8aa", "#55a6cc", "#675ed0", "#bd55c4", "#dc5b5b", "#d6aa52"];
    const left = 58, right = 884, top = 18, bottom = 286;
    const xForFrequency = (frequency) => left + Math.log10(frequency / 20) / 3 * (right - left);
    const frequencyForX = (x) => 20 * Math.pow(10, (x - left) / (right - left) * 3);
    const yForDb = (db) => top + (18 - db) / 36 * (bottom - top);
    const dbForY = (y) => 18 - (y - top) / (bottom - top) * 36;
    const add = (name, attrs, text = "") => {
      const node = document.createElementNS(ns, name);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      node.textContent = text; eqGraph.appendChild(node); return node;
    };

    [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000].forEach((frequency) => {
      const x = xForFrequency(frequency);
      add("line", { x1: x, y1: top, x2: x, y2: bottom, class: "eq-grid-line" });
      add("text", { x, y: 307, class: "eq-axis-label", "text-anchor": "middle" }, frequency >= 1000 ? `${frequency / 1000}k` : frequency);
    });
    [-18, -12, -6, 0, 6, 12, 18].forEach((db) => {
      const y = yForDb(db);
      add("line", { x1: left, y1: y, x2: right, y2: y, class: db === 0 ? "eq-zero-line" : "eq-grid-line" });
      add("text", { x: 48, y: y + 4, class: "eq-axis-label", "text-anchor": "end" }, `${db}`);
    });
    const curve = add("path", { class: "eq-curve" });
    const points = bands.map((band, index) => add("circle", { r: 8, fill: colors[index], class: "eq-point", tabindex: 0 }));
    const enabledControl = document.querySelector('[name="eq_enabled"]');
    const preampControl = document.querySelector('[name="eq_preamp_db"]');
    const resetControl = document.querySelector('[value="restore_equalizer"]');
    const flatFrequencies = [60, 120, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000];

    function bandValues(band) {
      const get = (suffix) => band.querySelector(`[name$="_${suffix}"]`);
      return { enabled: get("enabled").checked, type: get("type").value,
        frequency: Number(get("frequency").value), gain: Number(get("gain_db").value), q: Number(get("q").value) };
    }
    function pointEnabled(index) {
      return enabledControl.checked && bandValues(bands[index]).enabled;
    }
    function coefficients(value) {
      const w0 = 2 * Math.PI * value.frequency / 48000;
      const c = Math.cos(w0), s = Math.sin(w0), alpha = s / (2 * value.q);
      const a = Math.pow(10, value.gain / 40), root = 2 * Math.sqrt(a) * alpha;
      let b0, b1, b2, a0, a1, a2;
      if (value.type === "low_shelf") {
        b0 = a * ((a + 1) - (a - 1) * c + root); b1 = 2 * a * ((a - 1) - (a + 1) * c);
        b2 = a * ((a + 1) - (a - 1) * c - root); a0 = (a + 1) + (a - 1) * c + root;
        a1 = -2 * ((a - 1) + (a + 1) * c); a2 = (a + 1) + (a - 1) * c - root;
      } else if (value.type === "high_shelf") {
        b0 = a * ((a + 1) + (a - 1) * c + root); b1 = -2 * a * ((a - 1) + (a + 1) * c);
        b2 = a * ((a + 1) + (a - 1) * c - root); a0 = (a + 1) - (a - 1) * c + root;
        a1 = 2 * ((a - 1) - (a + 1) * c); a2 = (a + 1) - (a - 1) * c - root;
      } else if (value.type === "high_pass") {
        b0 = (1 + c) / 2; b1 = -(1 + c); b2 = b0;
        a0 = 1 + alpha; a1 = -2 * c; a2 = 1 - alpha;
      } else if (value.type === "low_pass") {
        b0 = (1 - c) / 2; b1 = 1 - c; b2 = b0;
        a0 = 1 + alpha; a1 = -2 * c; a2 = 1 - alpha;
      } else {
        b0 = 1 + alpha * a; b1 = -2 * c; b2 = 1 - alpha * a;
        a0 = 1 + alpha / a; a1 = -2 * c; a2 = 1 - alpha / a;
      }
      return [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0];
    }
    function magnitude(value, frequency) {
      if (!value.enabled) return 0;
      const [b0, b1, b2, a1, a2] = coefficients(value);
      const w = 2 * Math.PI * frequency / 48000, c1 = Math.cos(w), s1 = Math.sin(w);
      const c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
      const nr = b0 + b1 * c1 + b2 * c2, ni = -b1 * s1 - b2 * s2;
      const dr = 1 + a1 * c1 + a2 * c2, di = -a1 * s1 - a2 * s2;
      return 10 * Math.log10(Math.max(1e-12, (nr * nr + ni * ni) / (dr * dr + di * di)));
    }
    function redraw() {
      const values = bands.map(bandValues);
      const equalizerEnabled = enabledControl.checked;
      const preamp = equalizerEnabled ? Number(preampControl.value) || 0 : 0;
      let path = "";
      for (let pixel = left; pixel <= right; pixel += 3) {
        const frequency = frequencyForX(pixel);
        const response = equalizerEnabled
          ? values.reduce((sum, value) => sum + magnitude(value, frequency), 0)
          : 0;
        const db = Math.max(-18, Math.min(18, preamp + response));
        path += `${path ? "L" : "M"}${pixel.toFixed(1)},${yForDb(db).toFixed(1)}`;
      }
      curve.setAttribute("d", path);
      values.forEach((value, index) => {
        const interactive = equalizerEnabled && value.enabled;
        points[index].setAttribute("cx", xForFrequency(value.frequency));
        points[index].setAttribute("cy", yForDb(["high_pass", "low_pass"].includes(value.type) ? 0 : value.gain));
        points[index].setAttribute("opacity", interactive ? "1" : "0.35");
        points[index].setAttribute("tabindex", interactive ? "0" : "-1");
        points[index].setAttribute("aria-disabled", interactive ? "false" : "true");
        points[index].classList.toggle("enabled", interactive);
      });
    }
    function drag(index, event) {
      const rect = eqGraph.getBoundingClientRect();
      const x = Math.max(left, Math.min(right, (event.clientX - rect.left) / rect.width * 900));
      const y = Math.max(top, Math.min(bottom, (event.clientY - rect.top) / rect.height * 320));
      const band = bands[index];
      band.querySelector('[name$="_frequency"]').value = String(Math.round(frequencyForX(x)));
      const type = band.querySelector('[name$="_type"]').value;
      if (!['high_pass', 'low_pass'].includes(type)) band.querySelector('[name$="_gain_db"]').value = (Math.round(dbForY(y) * 10) / 10).toFixed(1);
      redraw();
    }
    points.forEach((point, index) => point.addEventListener("pointerdown", (event) => {
      if (!pointEnabled(index)) return;
      point.setPointerCapture(event.pointerId); drag(index, event);
      const move = (moveEvent) => {
        if (pointEnabled(index)) drag(index, moveEvent);
      };
      point.addEventListener("pointermove", move);
      point.addEventListener("pointerup", () => point.removeEventListener("pointermove", move), { once: true });
    }));
    bands.forEach((band) => band.addEventListener("input", redraw));
    enabledControl.addEventListener("input", redraw);
    preampControl.addEventListener("input", redraw);
    // Reset the controls and graph immediately on pointer activation. The form
    // submission still performs the authoritative server-side reset/apply; this
    // prevents the old curve and dragged dot positions lingering while service
    // restarts are in progress.
    resetControl.addEventListener("click", () => {
      enabledControl.checked = false;
      preampControl.value = "-3";
      bands.forEach((band, index) => {
        const get = (suffix) => band.querySelector(`[name$="_${suffix}"]`);
        get("enabled").checked = false;
        get("type").value = "bell";
        get("frequency").value = String(flatFrequencies[index]);
        get("gain_db").value = "0";
        get("q").value = "1";
      });
      redraw();
    });
    // Progressive enhancement: submit the EQ form via fetch so the page never
    // reloads and the user keeps their scroll position at the equalizer. The
    // three buttons share one form, so record which op was pressed; a hidden
    // ajax field tells the server to answer with JSON instead of a redirect.
    // Any network/parse failure falls back to a native submit so no-JS and
    // error paths still work.
    const eqForm = document.getElementById("equalizer-form");
    const eqFlash = document.getElementById("eq-flash");
    const ajaxField = eqForm ? eqForm.querySelector('[name="ajax"]') : null;
    if (eqForm && ajaxField) {
      let pendingOp = "apply_equalizer";
      let submitting = false;
      eqForm.querySelectorAll('button[name="op"]').forEach((button) => {
        button.addEventListener("click", () => { pendingOp = button.value; });
      });
      const showFlash = (message, ok) => {
        if (!eqFlash) return;
        eqFlash.textContent = message;
        eqFlash.className = `eq-flash ${ok ? "ok" : "warn"}`;
      };
      eqForm.addEventListener("submit", async (event) => {
        if (submitting) return;
        event.preventDefault();
        const body = new URLSearchParams(new FormData(eqForm));
        body.set("op", pendingOp);
        body.set("ajax", "1");
        submitting = true;
        showFlash(pendingOp === "apply_equalizer" ? "Applying…" : "Saving…", true);
        let payload;
        try {
          const response = await fetch("/audio-hardware", {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
              Accept: "application/json",
            },
            body,
          });
          payload = await response.json();
          if (!response.ok || typeof payload.ok !== "boolean") throw new Error("bad response");
        } catch (_error) {
          // Fall back to a full submit (also handles a 403/redirect that is not JSON).
          submitting = false;
          ajaxField.value = "";
          eqForm.submit();
          return;
        }
        submitting = false;
        showFlash(payload.message || (payload.ok ? "Done." : "Could not apply the equalizer."), payload.ok);
      });
    }
    redraw();
  }
  const wizard = document.getElementById("firmware-wizard");
  if (wizard) {
    const open = document.getElementById("firmware-update-open");
    const close = wizard.querySelector(".wizard-close");
    const authForm = document.getElementById("firmware-auth-form");
    const password = document.getElementById("firmware-password");
    const csrf = document.getElementById("firmware-csrf");
    const fileInput = document.getElementById("firmware-file");
    const confirm = document.getElementById("firmware-confirm");
    const start = document.getElementById("firmware-start");
    const done = document.getElementById("firmware-done");
    const phase = document.getElementById("firmware-phase");
    const progress = document.getElementById("firmware-progress");
    const detail = document.getElementById("firmware-progress-detail");
    const result = document.getElementById("firmware-result");
    let grant = "";
    let trackingToken = "";
    let locked = false;
    let statusTimer;
    const labels = {
      queued: "Upload complete. Preparing the package…",
      inspecting: "Inspecting the firmware package…",
      validating: "Validating compatibility and integrity…",
      installing: "Writing the inactive firmware slot…",
      syncing: "Syncing firmware to storage…",
      preparing_trial_boot: "Preparing the new firmware for a trial boot…",
      rebooting: "Installation complete. Waiting for the radio to reboot…",
      checking_health: "The radio is back. Checking the new firmware…",
      accepted: "Update complete. The new firmware is active.",
      trial_failed: "The new firmware failed health checks. Automatic recovery continues…",
      automatic_rollback: "Recovery complete. The previous firmware is active.",
      failed: "Firmware installation failed.",
      reboot_failed: "Firmware was installed, but automatic reboot failed.",
    };

    function formatBytes(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
    }

    function showStep(number) {
      wizard.querySelectorAll(".wizard-panel").forEach((panel) => {
        panel.hidden = panel.dataset.step !== String(number);
      });
      wizard.querySelectorAll(".wizard-steps li").forEach((item, index) => {
        item.classList.toggle("current", index === number - 1);
        item.classList.toggle("complete", index < number - 1);
      });
      document.getElementById("firmware-step-label").textContent = `Step ${number} of 4`;
    }

    function fail(message) {
      locked = false;
      close.disabled = false;
      window.clearTimeout(statusTimer);
      sessionStorage.removeItem("firmwareTrackingToken");
      result.className = "warn";
      result.textContent = message;
      done.hidden = false;
    }

    function finish(message, rolledBack) {
      locked = false;
      close.disabled = false;
      window.clearTimeout(statusTimer);
      progress.value = 100;
      phase.textContent = message;
      result.className = rolledBack ? "warn" : "ok";
      result.textContent = rolledBack
        ? "The update was not accepted; your previous firmware was restored safely."
        : "The firmware update completed successfully.";
      done.hidden = false;
      sessionStorage.removeItem("firmwareTrackingToken");
    }

    function discardUnavailableTracking() {
      window.clearTimeout(statusTimer);
      sessionStorage.removeItem("firmwareTrackingToken");
      trackingToken = "";
      grant = "";
      locked = false;
      close.disabled = false;
      if (wizard.open) wizard.close();
    }

    async function pollTracking(token, restoring = false) {
      try {
        const response = await fetch(wizard.dataset.trackingUrl, {
          cache: "no-store",
          headers: { Accept: "application/json", "X-Firmware-Tracking": token },
        });
        if (response.status === 404) {
          discardUnavailableTracking();
          return;
        }
        if (!response.ok) throw new Error("unavailable");
        const payload = await response.json();
        const status = payload.update || {};
        if (restoring && !wizard.open) {
          wizard.showModal();
          locked = true;
          close.disabled = true;
          showStep(4);
        }
        phase.textContent = labels[status.state] || status.message || "Update in progress…";
        detail.textContent = status.message || "";
        if (Number.isInteger(status.percent)) progress.value = status.percent;
        if (status.state === "accepted") return finish(labels.accepted, false);
        if (status.state === "automatic_rollback") return finish(labels.automatic_rollback, true);
        if (["failed", "reboot_failed"].includes(status.state)) return fail(status.result || labels[status.state]);
      } catch (_error) {
        phase.textContent = "Waiting for the radio to restart and reconnect…";
        detail.textContent = "Temporary connection failures are expected during reboot.";
      }
      statusTimer = window.setTimeout(() => pollTracking(token, restoring), 2500);
    }

    function upload(file) {
      locked = true;
      showStep(4);
      close.disabled = true;
      phase.textContent = "Uploading firmware…";
      result.textContent = "";
      const request = new XMLHttpRequest();
      request.open("POST", wizard.dataset.uploadUrl);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.setRequestHeader("X-CSRF-Token", csrf.value);
      request.setRequestHeader("X-Firmware-Grant", grant);
      request.setRequestHeader("X-Firmware-Name", encodeURIComponent(file.name));
      request.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        progress.value = percent;
        detail.textContent = `${formatBytes(event.loaded)} of ${formatBytes(event.total)} (${percent}%)`;
      });
      request.addEventListener("load", () => {
        let payload = {};
        try { payload = JSON.parse(request.responseText); } catch (_error) { /* handled below */ }
        if (!(request.status >= 200 && request.status < 300 && payload.ok)) {
          fail(payload.message || "The firmware update could not be started.");
          return;
        }
        phase.textContent = labels.queued;
        progress.value = 0;
        pollTracking(trackingToken);
      });
      request.addEventListener("error", () => fail("The upload connection failed."));
      request.send(file);
    }

    open.addEventListener("click", () => { showStep(1); wizard.showModal(); password.focus(); });
    close.addEventListener("click", () => {
      if (!locked) {
        sessionStorage.removeItem("firmwareTrackingToken");
        trackingToken = "";
        grant = "";
        wizard.close();
      }
    });
    done.addEventListener("click", () => { wizard.close(); window.location.reload(); });
    authForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      let response;
      let payload;
      try {
        const body = new URLSearchParams({ csrf_token: csrf.value, password: password.value });
        response = await fetch(wizard.dataset.authorizeUrl, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
          body,
        });
        payload = await response.json();
      } catch (_error) {
        password.setCustomValidity("The radio could not confirm the password. Try again.");
        password.reportValidity();
        return;
      }
      if (!response.ok || !payload.ok) { password.setCustomValidity(payload.message || "Password rejected."); password.reportValidity(); return; }
      password.setCustomValidity("");
      password.value = "";
      grant = payload.grant;
      trackingToken = payload.tracking_token;
      sessionStorage.setItem("firmwareTrackingToken", trackingToken);
      showStep(2);
      fileInput.focus();
    });
    wizard.querySelector('[data-next="3"]').addEventListener("click", () => {
      const file = fileInput.files[0];
      if (!file || !file.name.toLowerCase().endsWith(".swu") || file.size <= 0 || file.size > 768 * 1024 * 1024) {
        fileInput.setCustomValidity("Select a non-empty .swu file no larger than 768 MiB."); fileInput.reportValidity(); return;
      }
      fileInput.setCustomValidity("");
      document.getElementById("firmware-review-name").textContent = file.name;
      document.getElementById("firmware-review-size").textContent = formatBytes(file.size);
      showStep(3);
    });
    start.addEventListener("click", () => {
      if (!confirm.checked) { confirm.setCustomValidity("Confirm the automatic reboot to continue."); confirm.reportValidity(); return; }
      confirm.setCustomValidity(""); upload(fileInput.files[0]);
    });

    const savedToken = sessionStorage.getItem("firmwareTrackingToken");
    if (savedToken) {
      pollTracking(savedToken, true);
    }
  }

  document.querySelectorAll(".maintenance-dialog").forEach((dialog) => {
    const opener = document.getElementById(dialog.id === "backup-dialog" ? "backup-open" : "restore-open");
    const firstInput = dialog.querySelector('input:not([type="hidden"])');
    const close = () => {
      dialog.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ""; });
      dialog.querySelectorAll('input[type="file"]').forEach((input) => { input.value = ""; });
      dialog.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
      dialog.close();
    };
    opener?.addEventListener("click", () => {
      dialog.showModal();
      firstInput?.focus();
    });
    dialog.querySelector(".dialog-close")?.addEventListener("click", close);
    dialog.querySelector(".dialog-cancel")?.addEventListener("click", close);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
  });

  const debug = document.getElementById("adc-debug");
  if (!debug) return;
  const connection = document.getElementById("adc-connection");
  const updated = document.getElementById("adc-updated");
  const latest = {};
  const captures = {};
  let retryTimer;

  function setConnection(label, online) {
    connection.textContent = label;
    connection.classList.toggle("on", online);
    connection.classList.toggle("off", !online);
  }

  function updateChannel(number, data) {
    const card = debug.querySelector(`[data-channel="${number}"]`);
    if (!card || !data) return;
    const value = Number(data.raw_mv);
    latest[number] = value;
    card.querySelector("[data-adc-raw]").textContent = Number.isFinite(value)
      ? value.toFixed(1) : "—";
    card.querySelector("[data-adc-meter]").value = Number.isFinite(value)
      ? Math.max(0, value) : 0;
    let meaning = "Raw wiring diagnostic";
    if (number === "0") {
      meaning = `Mapped ${data.mapped_percent ?? "—"}% · applied ${data.applied_percent ?? "—"}%`;
    } else if (number === "1") {
      meaning = data.button ? `Button ${data.button}` : "No button";
    } else if (number === "2") {
      meaning = data.on ? "ON" : "OFF";
    }
    card.querySelector("[data-adc-meaning]").textContent = meaning;
  }

  function connect() {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${window.location.host}${debug.dataset.wsPath}`);
    setConnection("Connecting…", false);
    ws.addEventListener("open", () => setConnection("Live", true));
    ws.addEventListener("message", (event) => {
      try {
        const sample = JSON.parse(event.data);
        if (!sample.available || sample.stale) {
          setConnection(sample.available ? "Stale" : "Player unavailable", false);
          return;
        }
        setConnection("Live", true);
        Object.entries(sample.channels || {}).forEach(([number, data]) => {
          updateChannel(number, data);
        });
        updated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
      } catch (_error) {
        setConnection("Invalid sample", false);
      }
    });
    ws.addEventListener("close", () => {
      setConnection("Disconnected", false);
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, 1500);
    });
    ws.addEventListener("error", () => ws.close());
  }

  function reading(button) {
    const value = latest[button.dataset.channel];
    if (!Number.isFinite(value)) window.alert("No live reading is available yet.");
    return Number.isFinite(value) ? value : null;
  }

  debug.querySelectorAll("[data-adc-capture]").forEach((button) => {
    button.addEventListener("click", () => {
      const value = reading(button);
      if (value !== null) {
        document.getElementById(`adc-${button.dataset.adcCapture}`).value = value.toFixed(3);
      }
    });
  });
  debug.querySelectorAll("[data-button-end]").forEach((button) => {
    button.addEventListener("click", () => {
      const value = reading(button);
      if (value === null) return;
      captures[`button${button.dataset.buttonEnd}`] = value;
      button.textContent = `Button ${button.dataset.buttonEnd}: ${value.toFixed(1)} mV`;
      const first = captures.button1;
      const last = captures.button6;
      if (Number.isFinite(first) && Number.isFinite(last) && first !== last) {
        const low = Math.min(first, last);
        const high = Math.max(first, last);
        const step = (high - low) / 5;
        document.getElementById("adc-button_min").value = (low - step / 2).toFixed(3);
        document.getElementById("adc-button_max").value = (high + step / 2).toFixed(3);
      }
    });
  });
  debug.querySelectorAll("[data-power-end]").forEach((button) => {
    button.addEventListener("click", () => {
      const value = reading(button);
      if (value === null) return;
      captures[button.dataset.powerEnd] = value;
      button.textContent = `${button.dataset.powerEnd.toUpperCase()}: ${value.toFixed(1)} mV`;
      if (Number.isFinite(captures.on) && Number.isFinite(captures.off)) {
        document.getElementById("adc-switch_threshold").value = (
          (captures.on + captures.off) / 2
        ).toFixed(3);
      }
    });
  });
  connect();
})();
(() => {
  "use strict";

  const region = document.getElementById("now-playing");

  let refreshRequest = null;
  let controlRequestInFlight = false;
  let controlMessage = "";
  let controlFailed = false;

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isPlayerPayload(player) {
    if (!isObject(player) || typeof player.available !== "boolean" ||
        typeof player.stale !== "boolean" || !isObject(player.metadata) ||
        !isObject(player.sources)) return false;
    if (player.power !== null && typeof player.power !== "boolean") return false;
    if (player.active_source !== null && typeof player.active_source !== "string") return false;
    if (player.playing !== null && typeof player.playing !== "boolean") return false;
    if (typeof player.metadata.name !== "string" ||
        typeof player.metadata.title !== "string" ||
        !isObject(player.metadata.artwork)) return false;
    return Object.values(player.sources).every(
      (entry) => isObject(entry) && typeof entry.playing === "boolean",
    );
  }

  function setBadge(element, text, enabled) {
    if (!element) return;
    element.textContent = text;
    element.className = `badge ${enabled ? "on" : "off"}`;
  }

  // Sources without native cover art fall back to a source glyph that mirrors
  // the SPI display instead of the generic music note. URLs are allowlisted.
  const SOURCE_GLYPHS = {
    usb: "/static/usb-symbol.svg",
    bluetooth: "/static/bluetooth-symbol.svg",
  };

  function renderArtworkFallback(element, source) {
    const glyphUrl = typeof source === "string" ? SOURCE_GLYPHS[source] : undefined;
    if (glyphUrl) {
      let glyph = element.querySelector(".source-glyph");
      if (!glyph) {
        element.textContent = "";
        glyph = document.createElement("span");
        glyph.className = "source-glyph";
        glyph.setAttribute("role", "img");
        glyph.setAttribute("aria-label", "Audio source");
        element.appendChild(glyph);
      }
      glyph.dataset.source = source;
    } else if (element.textContent !== "♪") {
      element.textContent = "♪";
    }
  }

  function renderPlayer(player, unavailableMessage = "") {
    if (!region) return;
    const available = player.available === true;
    const powerOn = player.power === true;
    const playing = typeof player.playing === "boolean" ? player.playing : null;
    const metadata = isObject(player.metadata) ? player.metadata : {};
    const artwork = isObject(metadata.artwork) ? metadata.artwork : {};
    const artworkUrl = typeof artwork.url === "string" &&
      artwork.url.startsWith("/dashboard/artwork?") ? artwork.url : "";

    const stale = region.querySelector("[data-player-stale]");
    const unavailable = region.querySelector("[data-player-unavailable]");
    const details = region.querySelector("[data-player-details]");
    if (stale) stale.hidden = !available || player.stale !== true;
    if (unavailable) {
      unavailable.hidden = available;
      unavailable.textContent = unavailableMessage ||
        "Player status unavailable — the radio process may be starting or stopped.";
    }
    if (details) details.hidden = !available;

    const power = region.querySelector("[data-player-power]");
    if (player.power === true) setBadge(power, "on", true);
    else if (player.power === false) setBadge(power, "off", false);
    else setBadge(power, "Unknown", false);
    setBadge(
      region.querySelector("[data-player-playing]"),
      playing === true ? "Playing" : playing === false ? "Not playing" : "Unknown",
      playing === true,
    );

    const values = {
      "[data-player-source]": typeof player.active_source === "string" ? player.active_source : "",
      "[data-player-name]": typeof metadata.name === "string" ? metadata.name : "",
      "[data-player-title]": typeof metadata.title === "string" ? metadata.title : "",
    };
    Object.entries(values).forEach(([selector, value]) => {
      const element = region.querySelector(selector);
      if (element) element.textContent = value;
    });

    const artworkContainer = region.querySelector("[data-player-artwork]");
    const artworkImage = region.querySelector("[data-player-artwork-image]");
    const artworkFallback = region.querySelector("[data-player-artwork-fallback]");
    if (artworkContainer) artworkContainer.hidden = !available || !artworkUrl;
    if (artworkFallback) {
      artworkFallback.hidden = available && Boolean(artworkUrl);
      if (!artworkUrl) renderArtworkFallback(artworkFallback, player.active_source);
    }
    if (artworkImage) {
      if (available && artworkUrl) {
        artworkImage.setAttribute("src", artworkUrl);
        artworkImage.setAttribute("alt", `Artwork for ${metadata.name || "now playing"}`);
      } else {
        artworkImage.removeAttribute("src");
        artworkImage.setAttribute("alt", "");
      }
    }

    region.querySelectorAll("[data-playback-action]").forEach((button) => {
      let enabled = available && powerOn;
      if (button.dataset.playbackAction === "play" && playing === true) enabled = false;
      if (button.dataset.playbackAction === "pause" && playing === false) enabled = false;
      button.dataset.playbackEnabled = String(enabled);
    });
    const controlStatus = region.querySelector(".playback-control-status");
    if (controlStatus) {
      controlStatus.dataset.defaultMessage = !available
        ? "Playback controls unavailable."
        : !powerOn ? "Playback controls disabled while power is off." : "";
    }

    const sources = player.sources;
    document.querySelectorAll("[data-player-source-state]").forEach((element) => {
      const source = element.dataset.playerSourceState;
      const entry = isObject(sources[source]) ? sources[source] : {};
      if (!available) {
        element.textContent = "—";
        element.className = "";
      } else if (source === player.active_source && entry.playing === true) {
        setBadge(element, "Active", true);
      } else if (entry.playing === true) {
        setBadge(element, "Playing", true);
      } else {
        setBadge(element, "Ready", false);
      }
    });
    syncPlaybackControls();
  }

  function syncPlaybackControls() {
    if (!region) return;
    region.querySelectorAll("[data-playback-action]").forEach((button) => {
      button.disabled = controlRequestInFlight || button.dataset.playbackEnabled !== "true";
    });
    const status = region.querySelector(".playback-control-status");
    if (!status) return;
    status.textContent = controlMessage || status.dataset.defaultMessage || "";
    status.classList.toggle("error", controlFailed);
  }

  async function refreshNowPlaying(force = false) {
    if (!region || (document.hidden && !force)) return;
    if (refreshRequest) {
      await refreshRequest;
      if (!force) return;
    }
    refreshRequest = (async () => {
      try {
        const response = await fetch("/api/v1/player", {
          cache: "no-store",
          credentials: "omit",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("metadata request failed");
        const player = await response.json();
        if (!isPlayerPayload(player)) throw new Error("invalid metadata response");
        renderPlayer(player);
      } catch (_error) {
        renderPlayer(
          { available: false, stale: true, power: null, active_source: null,
            playing: null, metadata: {}, sources: {} },
          "Player metadata API unavailable.",
        );
      }
    })();
    try {
      await refreshRequest;
    } finally {
      refreshRequest = null;
    }
  }

  async function sendPlaybackCommand(action, label) {
    const paths = {
      previous: "/api/v1/player/previous",
      play: "/api/v1/player/play",
      pause: "/api/v1/player/pause",
      next: "/api/v1/player/next",
    };
    const path = paths[action];
    if (!path || controlRequestInFlight) return;

    controlRequestInFlight = true;
    controlFailed = false;
    controlMessage = `Sending ${label.toLowerCase()} command...`;
    syncPlaybackControls();
    try {
      const response = await fetch(path, {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({}),
      });
      let payload = {};
      try { payload = await response.json(); } catch (_error) { /* handled below */ }
      if (!response.ok || payload.ok !== true) {
        controlFailed = true;
        controlMessage = payload.message || "The playback command failed.";
        return;
      }
      controlMessage = "";
      await refreshNowPlaying(true);
    } catch (_error) {
      controlFailed = true;
      controlMessage = "The playback command could not reach the radio.";
    } finally {
      controlRequestInFlight = false;
      syncPlaybackControls();
    }
  }

  if (region) {
    region.addEventListener("click", (event) => {
      const button = event.target.closest("[data-playback-action]");
      if (!button || !region.contains(button) || button.disabled) return;
      sendPlaybackCommand(button.dataset.playbackAction, button.getAttribute("aria-label") || "playback");
    });
    refreshNowPlaying(true);
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
    const preampDisplay = document.querySelector('[data-eq-preamp]');
    const loudnessAmountControl = document.querySelector('[name="loudness_amount"]');
    const resetControl = document.querySelector('[value="restore_equalizer"]');
    const flatFrequencies = [60, 120, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000];
    // Preview loudness at a representative low volume so the graph shows the
    // boost shape; on the device the boost tapers live with the volume knob.
    // Keep these in sync with radio_equalizer.c and equalizer_store.py.
    const LOUDNESS_PREVIEW_VOLUME = 25;
    const LOUDNESS_MAX_AMOUNT = 10;
    // Preamp headroom range and the worst-case loudness low-shelf boost, mirroring
    // equalizer_store.computed_preamp_db so the read-only field matches the server.
    const LOUDNESS_LOW_MAX_DB = 10;
    const PREAMP_MIN_DB = -24;
    const PREAMP_MAX_DB = 0;
    const LOUDNESS_LOW = { frequency: 120, maxDb: 10, q: 0.7, type: "low_shelf" };
    const LOUDNESS_HIGH = { frequency: 10000, maxDb: 4, q: 0.7, type: "high_shelf" };

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
    function loudnessAmount() {
      return Math.max(0, Math.min(LOUDNESS_MAX_AMOUNT, Number(loudnessAmountControl && loudnessAmountControl.value) || 0));
    }
    function loudnessResponse(frequency) {
      const amount = loudnessAmount();
      if (!enabledControl.checked || amount <= 0) return 0;
      const scale = (amount / LOUDNESS_MAX_AMOUNT) * (1 - LOUDNESS_PREVIEW_VOLUME / 100);
      if (scale <= 0) return 0;
      const low = { enabled: true, type: LOUDNESS_LOW.type, frequency: LOUDNESS_LOW.frequency,
        gain: LOUDNESS_LOW.maxDb * scale, q: LOUDNESS_LOW.q };
      const high = { enabled: true, type: LOUDNESS_HIGH.type, frequency: LOUDNESS_HIGH.frequency,
        gain: LOUDNESS_HIGH.maxDb * scale, q: LOUDNESS_HIGH.q };
      return magnitude(low, frequency) + magnitude(high, frequency);
    }
    // Mirror equalizer_store.computed_preamp_db: reserve headroom for the largest
    // boost in the chain (enabled band gains + the worst-case loudness low shelf).
    function computedPreamp(values) {
      if (!enabledControl.checked) return 0;
      let boost = 0;
      values.forEach((value) => { if (value.enabled && value.gain > boost) boost = value.gain; });
      const amount = loudnessAmount();
      if (amount > 0) {
        const loudnessBoost = LOUDNESS_LOW_MAX_DB * (amount / LOUDNESS_MAX_AMOUNT);
        if (loudnessBoost > boost) boost = loudnessBoost;
      }
      return Math.max(PREAMP_MIN_DB, Math.min(PREAMP_MAX_DB, -boost));
    }
    function redraw() {
      const values = bands.map(bandValues);
      const equalizerEnabled = enabledControl.checked;
      const preamp = computedPreamp(values);
      // The preamp is an automatic, informational read-out (plain text, not a
      // form field): the server recomputes it, so we only mirror it here.
      if (preampDisplay) preampDisplay.textContent = (Math.round(preamp * 10) / 10).toFixed(1);
      let path = "";
      for (let pixel = left; pixel <= right; pixel += 3) {
        const frequency = frequencyForX(pixel);
        const response = equalizerEnabled
          ? values.reduce((sum, value) => sum + magnitude(value, frequency), 0) + loudnessResponse(frequency)
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
    // When the equalizer is disabled, gray out and disable the dependent
    // controls (loudness level and the per-band inputs) so it is clear they
    // only take effect with the equalizer on. The Enable checkbox and the
    // Save/Apply/Reset buttons stay active so the user can still turn the
    // equalizer off and save.
    const eqCard = document.getElementById("parametric-equalizer");
    function dependentFields() {
      const dependents = [];
      if (loudnessAmountControl) dependents.push(loudnessAmountControl);
      bands.forEach((band) => {
        band.querySelectorAll("input, select").forEach((field) => dependents.push(field));
      });
      return dependents;
    }
    function syncDependentState() {
      const on = enabledControl.checked;
      dependentFields().forEach((field) => { field.disabled = !on; });
      if (eqCard) eqCard.classList.toggle("eq-disabled", !on);
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
    enabledControl.addEventListener("input", () => { syncDependentState(); redraw(); });
    if (loudnessAmountControl) {
      loudnessAmountControl.addEventListener("input", redraw);
      loudnessAmountControl.addEventListener("change", redraw);
    }
    // Reset the controls and graph immediately on pointer activation. The form
    // submission still performs the authoritative server-side reset/apply; this
    // prevents the old curve and dragged dot positions lingering while service
    // restarts are in progress.
    resetControl.addEventListener("click", () => {
      enabledControl.checked = false;
      if (loudnessAmountControl) loudnessAmountControl.value = "0";
      bands.forEach((band, index) => {
        const get = (suffix) => band.querySelector(`[name$="_${suffix}"]`);
        get("enabled").checked = false;
        get("type").value = "bell";
        get("frequency").value = String(flatFrequencies[index]);
        get("gain_db").value = "0";
        get("q").value = "1";
      });
      syncDependentState();
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
        // Disabled fields are omitted from FormData; the server needs every band
        // value even when the equalizer is off, so momentarily re-enable the
        // dependents while snapshotting the form, then restore the gray-out.
        const on = enabledControl.checked;
        dependentFields().forEach((field) => { field.disabled = false; });
        const body = new URLSearchParams(new FormData(eqForm));
        if (!on) syncDependentState();
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
          // Re-enable dependents so a disabled equalizer still posts every band.
          submitting = false;
          ajaxField.value = "";
          dependentFields().forEach((field) => { field.disabled = false; });
          eqForm.submit();
          return;
        }
        submitting = false;
        showFlash(payload.message || (payload.ok ? "Done." : "Could not apply the equalizer."), payload.ok);
      });
    }
    syncDependentState();
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

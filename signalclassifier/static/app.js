"use strict";
(() => {
  const $ = (q, root = document) => root.querySelector(q);
  const $$ = (q, root = document) => [...root.querySelectorAll(q)];
  const state = {
    view: "empfang",
    status: {},
    settings: {},
    recordings: [],
    emitters: [],
    topologies: [],
    experiments: [],
    models: [],
    drones: [],
    selectedExperiment: null,
    selectedTopology: null,
    spectrum: null,
    waterfall: [],
    map: null,
    mapMarkers: null,
    mapTiles: null,
    mapGrid: null,
    mapFitted: false,
    polling: false,
    experimentPolling: false,
  };
  const titles = {
    empfang: [
      "Empfang & Spektrum",
      "Signale erfassen. Merkmale verstehen. Entscheidungen nachvollziehen.",
    ],
    aufnahmen: [
      "Aufnahmen & Datensätze",
      "IQ-Daten organisieren, Labels bestätigen und Versuche vorbereiten.",
    ],
    emitter: [
      "Emitter & Labeling",
      "Funkdienste, Standards und Modulationen als nachvollziehbares Vorwissen.",
    ],
    topologien: [
      "Topologien & Ansätze",
      "Vom einfachen Signalmerkmal bis zur neuronalen Architektur.",
    ],
    experimente: [
      "Experimente & Auswertung",
      "Annahmen prüfen. Robustheit messen. Ergebnisse reproduzieren.",
    ],
    modelle: [
      "Modelle & Transfer",
      "Auf der Workstation trainieren. Auf dem Laptop einsetzen.",
    ],
    drohnen: [
      "Drohnen & Telemetrie",
      "Empfangene Positionen, Höhen und Protokollfähigkeiten im Überblick.",
    ],
  };
  const fmt = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
  const fmtPrecise = new Intl.NumberFormat("de-DE", {
    maximumFractionDigits: 6,
  });
  let toastTimer;
  function text(value, fallback = "—") {
    return value === null || value === undefined || value === ""
      ? fallback
      : String(value);
  }
  function num(value, digits = 2) {
    return Number.isFinite(Number(value)) && value !== null && value !== ""
      ? new Intl.NumberFormat("de-DE", {
          maximumFractionDigits: digits,
        }).format(Number(value))
      : "—";
  }
  function percent(value) {
    return value === null ||
      value === undefined ||
      !Number.isFinite(Number(value))
      ? "—"
      : `${num(Number(value) <= 1 ? Number(value) * 100 : value, 1)} %`;
  }
  function mhz(value) {
    return Number.isFinite(Number(value)) && value !== null
      ? `${fmtPrecise.format(Number(value) / 1e6)} MHz`
      : "—";
  }
  function bytes(value) {
    if (!Number.isFinite(Number(value)) || value === null) return "—";
    const v = Number(value);
    if (v >= 1073741824) return `${num(v / 1073741824)} GiB`;
    if (v >= 1048576) return `${num(v / 1048576)} MiB`;
    if (v >= 1024) return `${num(v / 1024)} KiB`;
    return `${v} B`;
  }
  function duration(value) {
    if (!Number.isFinite(Number(value)) || value === null) return "—";
    const v = Number(value);
    return v >= 60 ? `${num(v / 60)} min` : `${num(v)} s`;
  }
  function date(value) {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.getTime())
      ? text(value)
      : d.toLocaleString("de-DE", {
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
  }
  function arr(value) {
    return Array.isArray(value) ? value : [];
  }
  function itemArray(value, key) {
    return Array.isArray(value) ? value : arr(value?.[key]);
  }
  function el(tag, props = {}, children = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k === "dataset") Object.assign(n.dataset, v);
      else if (k === "onClick") n.addEventListener("click", v);
      else if (k in n && !["form", "list"].includes(k)) n[k] = v;
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of Array.isArray(children) ? children : [children]) {
      if (c !== null && c !== undefined)
        n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return n;
  }
  function icon(name) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    n.setAttribute("class", "icon");
    n.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    n.append(use);
    return n;
  }
  function button(label, fn, cls = "button button-quiet", iconName = null) {
    return el("button", { type: "button", class: cls, onClick: fn }, [
      ...(iconName ? [icon(iconName)] : []),
      label,
    ]);
  }
  function pill(label, tone = "") {
    return el("span", { class: `pill ${tone}`, text: label });
  }
  function download(path, label, iconOnly = false) {
    const a = el(
      "a",
      {
        href: path,
        class: iconOnly ? "icon-button" : "button button-quiet",
        download: "",
        title: label,
        "aria-label": label,
      },
      [icon("download")],
    );
    if (!iconOnly) a.append(label);
    return a;
  }
  function empty(message) {
    return el("div", { class: "empty-state compact", text: message });
  }
  function detailCell(primary, secondary, className = "") {
    return el("td", { class: className }, [
      el("strong", { text: text(primary) }),
      secondary !== undefined ? el("small", { text: text(secondary) }) : null,
    ]);
  }
  function errorMessage(error) {
    return error?.message || String(error);
  }
  function showError(error) {
    const message = errorMessage(error);
    const dialog = $("dialog[open]");
    if (dialog) {
      let notice = $(".dialog-error", dialog);
      if (!notice) {
        notice = el("div", {
          class: "notice error dialog-error",
          role: "alert",
        });
        ($(".dialog-body", dialog) || dialog).prepend(notice);
      }
      notice.textContent = message;
      notice.scrollIntoView({ block: "nearest" });
    }
    $("#global-error span").textContent = message;
    $("#global-error").hidden = false;
    console.error(error);
  }
  function toast(message) {
    clearTimeout(toastTimer);
    $("#toast").textContent = message;
    $("#toast").hidden = false;
    toastTimer = setTimeout(() => {
      $("#toast").hidden = true;
    }, 4500);
  }
  async function api(path, options = {}) {
    const init = { ...options };
    if (options.body && !(options.body instanceof FormData)) {
      init.headers = { ...options.headers, "Content-Type": "application/json" };
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    let body;
    const content = response.headers.get("content-type") || "";
    if (content.includes("json")) body = await response.json();
    else body = await response.text();
    if (!response.ok) {
      let message =
        body?.detail || body?.error || body || `HTTP ${response.status}`;
      if (Array.isArray(message))
        message = message
          .map((v) => `${arr(v.loc).join(".")}: ${v.msg}`)
          .join("; ");
      if (typeof message === "object") message = JSON.stringify(message);
      throw new Error(String(message));
    }
    return body;
  }
  async function action(fn, target = null) {
    const buttons = target
      ? $$("button[type=submit],button:not([type])", target)
      : [];
    const disabled = buttons.map((b) => b.disabled);
    buttons.forEach((b) => {
      b.disabled = true;
    });
    target?.classList.add("busy");
    try {
      return await fn();
    } catch (e) {
      showError(e);
      return null;
    } finally {
      buttons.forEach((b, i) => {
        b.disabled = disabled[i];
      });
      target?.classList.remove("busy");
    }
  }
  function closeDialog(dialog) {
    if (dialog?.open) dialog.close();
  }
  async function openDialog(id) {
    const d = document.getElementById(id);
    if (!d) return;
    $(".dialog-error", d)?.remove();
    if (id === "settings-dialog") populateSettings();
    if (id === "bridge-dialog") action(loadBridge);
    if (!d.open) d.showModal();
  }
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("signalclassifier-theme", theme);
    $("#theme-toggle").setAttribute(
      "aria-label",
      theme === "dark"
        ? "Helles Farbschema aktivieren"
        : "Dunkles Farbschema aktivieren",
    );
    drawSpectrum();
    drawWaterfall();
    state.mapGrid?.redraw();
  }
  async function changeView(view) {
    if (!titles[view]) return;
    state.view = view;
    $$(".view").forEach((v) => {
      v.hidden = v.id !== `view-${view}`;
    });
    $$(".rail-btn[data-view]").forEach((b) => {
      const active = b.dataset.view === view;
      b.classList.toggle("active", active);
      if (active) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    $("#page-title").replaceChildren(
      titles[view][0],
      el("span", { class: "title-dot", text: "." }),
    );
    $("#page-description").textContent = titles[view][1];
    const actions = {
      empfang: ["play", "Empfang starten"],
      aufnahmen: ["upload", "SigMF importieren"],
      emitter: ["plus", "Eintrag hinzufügen"],
      topologien: ["plus", "Eigene Topologie"],
      experimente: ["flask", "Experiment konfigurieren"],
      modelle: ["upload", "Modell importieren"],
      drohnen: ["drone", "Telemetrie verbinden"],
    };
    $("#heading-action").replaceChildren(
      icon(actions[view][0]),
      el("span", { text: actions[view][1] }),
    );
    if (location.hash !== `#${view}`)
      history.replaceState(null, "", `#${view}`);
    try {
      if (view === "aufnahmen") await loadRecordings();
      if (view === "emitter") await loadEmitters();
      if (view === "topologien") await loadTopologies();
      if (view === "experimente") {
        await Promise.all([
          loadExperiments(),
          loadTopologies(),
          loadRecordings(),
        ]);
      }
      if (view === "modelle") await loadModels();
      if (view === "drohnen") {
        await loadDrones();
        initializeMap();
        setTimeout(() => state.map?.invalidateSize(), 40);
      }
      if (view === "empfang") {
        drawSpectrum();
        drawWaterfall();
      }
    } catch (e) {
      showError(e);
    }
  }
  function receiverRunning(rx = state.status.receiver || {}) {
    return (
      rx.running === true ||
      ["running", "recording", "receiving", "starting"].includes(rx.status) ||
      rx.active === true
    );
  }
  function sourceLabel(source) {
    return (
      {
        pluto: "PlutoSDR",
        rsp1a: "SDRplay RSP1A",
        recording: "SigMF Replay",
        synthetic: "Synthetisches Testsignal",
      }[source] || text(source, "Keine Quelle")
    );
  }
  function renderStatus() {
    const status = state.status;
    const rx = status.receiver || {};
    const config = rx.config || rx;
    const running = receiverRunning(rx);
    const hasError =
      !!rx.error || rx.status === "error" || rx.state === "error";
    if (hasError) resetReceiverDisplay("Empfangsfehler · keine aktuelle Entscheidung");
    $("#connection-dot").className = "status-dot online";
    $("#connection-state").textContent = running
      ? config.source === "synthetic"
        ? "Synthetische Quelle aktiv"
        : "Empfang aktiv"
      : "Lokales Backend verbunden";
    $("#rx-source-label").textContent = running
      ? sourceLabel(config.source)
      : "Kein Empfang aktiv";
    $("#rx-detail").textContent = hasError
      ? text(rx.error)
      : running
        ? `${text(config.mode, "monitor") === "energy" ? "Signalaufnahme" : config.mode === "wideband" ? "Weitbandaufnahme" : "Beobachtung"} · ${mhz(config.center_frequency_hz ?? rx.center_frequency_hz)}`
        : "Live, Recording oder synthetische Quelle wählen";
    $("#receiver-state").textContent = hasError
      ? "Fehler"
      : running
        ? "Aktiv"
        : "Gestoppt";
    $("#receiver-state").className =
      `pill ${hasError ? "red" : running ? "cyan" : ""}`;
    $("#rx-start").disabled = running;
    $("#rx-stop").disabled = !running;
    $("#spectrum-state").textContent = running
      ? config.source === "synthetic"
        ? "SYNTHETISCH"
        : config.source === "recording"
          ? "REPLAY"
          : "LIVE"
      : "STANDBY";
    const elapsed = rx.processing_ms ?? rx.pipeline_ms;
    const samples = rx.samples_received ?? rx.samples ?? rx.sample_count;
    $("#rx-performance").textContent =
      elapsed === undefined
        ? running
          ? "IQ-Stream aktiv"
          : "—"
        : `${num(elapsed)} ms / Block`;
    $("#rx-samples").textContent =
      samples !== undefined
        ? `${num(samples, 0)} Samples · ${bytes(rx.bytes_written ?? rx.recorded_bytes)}`
        : running
          ? "Empfang läuft"
          : "Warte auf IQ-Daten";
    const active = status.active_model;
    $("#active-model-label").textContent =
      active?.name ||
      active?.id ||
      (typeof active === "string" ? active : "Feature-Kaskade");
    if (status.version) $("#version").textContent = `v${status.version}`;
    const hw = status.hardware || {};
    const device = hw.gpu_name || hw.gpu || hw.device;
    const cpu = hw.cpu || hw.processor;
    $("#footer-hardware").textContent =
      [cpu, device].filter((v) => typeof v === "string" && v).join(" · ") ||
      "Linux / WSL · CPU / optionale CUDA-GPU";
    if (state.view === "empfang") {
      $("#heading-action span").textContent = running
        ? "Empfang stoppen"
        : "Empfang starten";
      $("#heading-action use").setAttribute(
        "href",
        running ? "#i-stop" : "#i-play",
      );
    }
  }
  function resetReceiverDisplay(message = "Warte auf neue IQ-Daten") {
    state.spectrum = null;
    state.waterfall = [];
    $("#spectrum-empty").hidden = false;
    $("#spectrum-center").textContent = "—";
    $("#spectrum-bandwidth").textContent = "Keine Daten";
    $("#classification-label").textContent = "—";
    $("#classification-confidence").textContent = message;
    $("#classification-time").textContent = "— ms";
    $("#decision-steps").replaceChildren(empty(message));
    $("#features-grid").replaceChildren();
    $("#model-explanation-details").hidden = true;
    $("#model-explanation").replaceChildren();
    renderDetections([]);
    drawSpectrum();
    drawWaterfall();
  }
  async function loadStatus() {
    try {
      state.status = await api("/api/status");
      renderStatus();
    } catch (e) {
      $("#connection-dot").className = "status-dot error";
      $("#connection-state").textContent = "Backend nicht erreichbar";
    }
  }
  function updateReceiverFields() {
    const source = $("#rx-source").value;
    const mode = $("#receiver-form [name=mode]").value;
    $("#rx-recording-field").hidden = source !== "recording";
    $("#rx-uri-field").hidden = !["pluto", "rsp1a"].includes(source);
    $("#synthetic-field").hidden = source !== "synthetic";
    $("#capture-budget-field").hidden = mode === "monitor";
    $("#threshold-field").hidden = mode !== "energy";
    $("#rx-uri").placeholder =
      source === "pluto" ? "ip:192.168.2.1" : "Automatisch (RSP1A)";
    const help = {
      pluto:
        "PlutoSDR benötigt pyadi-iio/libiio. Im werkseitigen Frequenzbereich sind UKW-Rundfunk und Flugfunk nicht direkt erreichbar.",
      rsp1a:
        "RSP1A benötigt SDRplay API und SoapySDR. Ein Empfänger ist gleichzeitig aktiv.",
      recording:
        "Replay verwendet Frequenz und Abtastrate der SigMF-Metadaten. Die Daten durchlaufen dieselbe Auswertung.",
      synthetic:
        "Explizites synthetisches Testsignal. Kein HF-Empfang und kein Nachweis realer Empfangsleistung.",
    };
    $("#source-help").textContent = help[source];
    $$(
      "[name=center_frequency_mhz],[name=sample_rate_msps],[name=gain_db]",
      $("#receiver-form"),
    ).forEach((n) => {
      n.disabled = source === "recording";
    });
  }
  async function startReceiver() {
    const form = $("#receiver-form");
    if (!form.reportValidity()) return;
    const f = new FormData(form);
    const config = {
      source: f.get("source"),
      uri: f.get("uri") || "",
      recording_id: f.get("recording_id") || null,
      center_frequency_hz: Number(f.get("center_frequency_mhz")) * 1e6,
      sample_rate: Number(f.get("sample_rate_msps")) * 1e6,
      gain_db: Number(f.get("gain_db")),
      mode: f.get("mode"),
      threshold_db: Number(f.get("threshold_db")),
      duration_seconds: Number(f.get("duration_seconds")),
      max_bytes: Number(f.get("max_mib")) * 1048576,
      synthetic_modulation: f.get("synthetic_modulation"),
    };
    if (config.source === "recording") {
      if (!config.recording_id)
        throw new Error(
          "Bitte zuerst eine SigMF-Aufnahme importieren und auswählen.",
        );
      delete config.center_frequency_hz;
      delete config.sample_rate;
      delete config.gain_db;
    }
    resetReceiverDisplay();
    await api("/api/receiver/start", { method: "POST", body: config });
    await loadStatus();
    toast(
      config.source === "synthetic"
        ? "Synthetische Quelle gestartet."
        : "Empfang gestartet.",
    );
  }
  async function stopReceiver() {
    await api("/api/receiver/stop", { method: "POST" });
    await loadStatus();
    await loadRecordings();
    toast("Empfang gestoppt.");
  }
  function canvasSetup(canvas) {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = canvas.clientWidth || 300,
      height = canvas.clientHeight || 160;
    const targetW = Math.round(width * ratio),
      targetH = Math.round(height * ratio);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { ctx, width, height };
  }
  function css(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
  }
  function spectrumArrays() {
    const s = state.spectrum || {};
    return {
      freq: arr(s.frequencies_hz || s.frequencies),
      power: arr(s.power_dbfs || s.spectrum_dbfs || s.spectrum),
    };
  }
  function drawSpectrum() {
    const canvas = $("#spectrum-canvas");
    if (!canvas || state.view !== "empfang") return;
    const { ctx, width: w, height: h } = canvasSetup(canvas);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = css("--plot");
    ctx.fillRect(0, 0, w, h);
    const left = 45,
      right = 17,
      top = 34,
      bottom = 30,
      pw = w - left - right,
      ph = h - top - bottom;
    ctx.font = "10px Consolas,monospace";
    ctx.lineWidth = 1;
    ctx.strokeStyle = css("--grid");
    ctx.fillStyle = css("--faint");
    for (let i = 0; i <= 6; i++) {
      let y = top + (i * ph) / 6;
      ctx.beginPath();
      ctx.moveTo(left, y + 0.5);
      ctx.lineTo(w - right, y + 0.5);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(String(-i * 20), left - 9, y + 3);
    }
    const { freq, power } = spectrumArrays();
    for (let i = 0; i <= 6; i++) {
      const x = left + (pw * i) / 6;
      ctx.beginPath();
      ctx.moveTo(x + 0.5, top);
      ctx.lineTo(x + 0.5, h - bottom);
      ctx.stroke();
      ctx.textAlign = "center";
      if (freq.length) {
        const f = freq[0] + ((freq[freq.length - 1] - freq[0]) * i) / 6;
        ctx.fillText(num(f / 1e6, 3), x, h - 10);
      }
    }
    if (!power.length) return;
    const count = Math.min(power.length, 2000);
    ctx.beginPath();
    for (let i = 0; i < count; i++) {
      const index = Math.floor(
        (i * (power.length - 1)) / Math.max(1, count - 1),
      );
      const p = Number(power[index]);
      const y =
        top +
        Math.max(0, Math.min(1, -(Number.isFinite(p) ? p : -120) / 120)) * ph;
      const x = left + (pw * i) / Math.max(1, count - 1);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = css("--cyan");
    ctx.lineWidth = 1.35;
    ctx.stroke();
    ctx.lineTo(w - right, h - bottom);
    ctx.lineTo(left, h - bottom);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, top, 0, h - bottom);
    gradient.addColorStop(0, "rgba(109,214,220,.14)");
    gradient.addColorStop(1, "rgba(109,214,220,.01)");
    ctx.fillStyle = gradient;
    ctx.fill();
  }
  function drawWaterfall() {
    const canvas = $("#waterfall-canvas");
    if (!canvas || state.view !== "empfang" || !$("#waterfall-toggle").checked)
      return;
    const { ctx, width: w, height: h } = canvasSetup(canvas);
    ctx.fillStyle = css("--plot");
    ctx.fillRect(0, 0, w, h);
    const rows = state.waterfall;
    if (!rows.length) {
      ctx.strokeStyle = css("--grid");
      ctx.lineWidth = 1;
      for (let x = 45; x < w; x += (w - 62) / 6) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }
      return;
    }
    const bins = rows[0].length;
    const pw = w - 62;
    for (let r = 0; r < rows.length; r++) {
      const y = h - ((r + 1) * h) / 100;
      const row = rows[r];
      for (let b = 0; b < bins; b++) {
        const v = Math.max(0, Math.min(1, (Number(row[b]) + 115) / 85));
        const red = Math.round(10 + v * 95),
          green = Math.round(24 + v * 190),
          blue = Math.round(35 + v * 185);
        ctx.fillStyle = `rgb(${red},${green},${blue})`;
        ctx.fillRect(
          45 + (b * pw) / bins,
          y,
          Math.ceil(pw / bins) + 0.3,
          Math.ceil(h / 100) + 0.3,
        );
      }
    }
    ctx.fillStyle = css("--faint");
    ctx.font = "9px Consolas,monospace";
    ctx.fillText("ZEIT", 6, 14);
  }
  function addWaterfall(power) {
    if (!power.length) return;
    const bins = Math.min(256, power.length);
    const row = [];
    for (let i = 0; i < bins; i++) {
      const a = Math.floor((i * power.length) / bins),
        b = Math.max(a + 1, Math.floor(((i + 1) * power.length) / bins));
      let sum = 0;
      for (let j = a; j < b; j++) sum += Number(power[j]);
      row.push(sum / (b - a));
    }
    state.waterfall.unshift(row);
    state.waterfall.length = Math.min(100, state.waterfall.length);
  }
  function classificationScore(result) {
    if (["feature_rejection_gate", "insufficient_segment"].includes(result.confidence_kind))
      return "Modellinferenz übersprungen";
    if (result.confidence === undefined) return "Kein Score angegeben";
    return `${percent(result.confidence)} · ${result.confidence_kind === "heuristic_score" ? "Heuristikwert" : "Unkalibrierter Modellscore"}`;
  }
  function renderClassification(result, container = null) {
    if (!result) return;
    const steps = arr(result.steps || result.decision_path);
    if (container) {
      container.replaceChildren(
        el("div", { class: "classification-result" }, [
          el("span", { class: "metric-label", text: "ERGEBNIS" }),
          el("strong", { text: text(result.label) }),
          el("span", {
            text: classificationScore(result),
          }),
        ]),
        makeSteps(steps),
        el("pre", { text: JSON.stringify(result.features || {}, null, 2) }),
        result.explanation ? explanationNode(result.explanation) : null,
      );
      return;
    }
    $("#classification-label").textContent = text(result.label);
    $("#classification-confidence").textContent = classificationScore(result);
    $("#classification-time").textContent = `${num(result.elapsed_ms)} ms`;
    $("#decision-steps").replaceChildren(
      ...(steps.length
        ? makeSteps(steps).childNodes
        : [
            empty(
              "Für diese Entscheidung wurde kein Erklärungspfad geliefert.",
            ),
          ]),
    );
    $("#model-explanation-details").hidden = !result.explanation;
    if (result.explanation)
      $("#model-explanation").replaceChildren(
        explanationNode(result.explanation),
      );
    const features = result.features || {};
    $("#features-grid").replaceChildren(
      ...Object.entries(features).map(([key, value]) =>
        el("div", { class: "feature" }, [
          el("span", { text: key.replaceAll("_", " ") }),
          el("strong", {
            text:
              typeof value === "number"
                ? num(value, 5)
                : typeof value === "object"
                  ? JSON.stringify(value)
                  : text(value),
          }),
        ]),
      ),
    );
  }
  function explanationNode(data) {
    const contributions = arr(data.contributions);
    const maximum = Math.max(
      1e-12,
      ...contributions.map((v) => Math.abs(v.contribution ?? v.magnitude ?? 0)),
    );
    return el("div", { class: "model-attributions" }, [
      el("p", {
        class: "secondary",
        text: `${text(data.method)} · Vergleich ${arr(data.compared_classes).join(" / ")}`,
      }),
      ...contributions.map((item) => {
        const value = item.contribution ?? item.magnitude ?? 0;
        const label =
          item.feature ||
          (item.window !== undefined
            ? `Zeitfenster ${item.window}`
            : item.frequency_bin !== undefined
              ? `Frequenz-Bin ${item.frequency_bin}`
              : `Samples ${item.sample_start}–${item.sample_end}`);
        return el("div", { class: "attribution-row" }, [
          el("span", { text: label }),
          el("div", { class: "attribution-track" }, [
            el("i", {
              style: `width:${(Math.abs(value) / maximum) * 100}%;background:${value < 0 ? "var(--amber)" : "var(--cyan)"}`,
            }),
          ]),
          el("strong", {
            text: `${value > 0 && item.contribution !== undefined ? "+" : ""}${num(value, 4)}`,
          }),
        ]);
      }),
      el("p", { class: "field-help", text: text(data.note) }),
    ]);
  }
  function makeSteps(steps) {
    const node = el("div", { class: "decision-steps" });
    steps.forEach((step, i) =>
      node.append(
        el("div", { class: "decision-step" }, [
          el("span", { class: "step-marker", text: String(i + 1) }),
          el("div", {}, [
            el("h3", {
              text: [step.stage, step.decision].filter(Boolean).join(" · "),
            }),
            el("p", { text: text(step.reason || step.explanation) }),
          ]),
        ]),
      ),
    );
    return node;
  }
  function renderDetections(detections) {
    const rows = arr(detections);
    $("#detection-count").textContent = String(rows.length);
    $("#detection-list").replaceChildren(
      ...(rows.length
        ? rows.map((d) =>
            el("div", { class: "detection-card" }, [
              el("strong", {
                text: mhz(
                  d.center_frequency_hz ?? d.frequency_hz ?? d.center_hz,
                ),
              }),
              el("span", {
                text: `Bandbreite ${num((d.bandwidth_hz ?? d.bandwidth ?? 0) / 1e3)} kHz`,
              }),
              el("span", {
                text: `${num(d.snr_db ?? d.excess_db ?? d.peak_above_noise_db)} dB über Rauschschätzung`,
              }),
            ]),
          )
        : [empty("Detektionen erscheinen während des Empfangs.")]),
    );
  }
  async function loadSpectrum() {
    const snapshot = await api("/api/spectrum");
    state.spectrum = snapshot;
    const { freq, power } = spectrumArrays();
    $("#spectrum-empty").hidden = power.length > 0;
    const center =
      snapshot.center_frequency_hz ??
      snapshot.receiver?.center_frequency_hz ??
      snapshot.center_frequency ??
      (freq.length ? (freq[0] + freq[freq.length - 1]) / 2 : null);
    $("#spectrum-center").textContent = mhz(center);
    $("#spectrum-bandwidth").textContent = freq.length
      ? `${num((freq[freq.length - 1] - freq[0]) / 1e6, 3)} MHz · ${power.length} Bins`
      : "Keine Daten";
    if (power.length) addWaterfall(power);
    drawSpectrum();
    drawWaterfall();
    renderDetections(snapshot.detections);
    if (snapshot.classification) renderClassification(snapshot.classification);
  }
  function recordingMeta(r) {
    const g = r.metadata?.global || r.global || {};
    const c = arr(r.metadata?.captures || r.captures)[0] || {};
    return {
      id: r.id,
      name: r.name || r.id,
      sample_rate: r.sample_rate ?? r.sample_rate_hz ?? g["core:sample_rate"],
      center_frequency_hz:
        r.center_frequency_hz ?? r.center_frequency ?? c["core:frequency"],
      duration_seconds: r.duration_seconds ?? r.duration,
      source: r.source || r.metadata?.source || g["signalclassifier:source"],
      label:
        r.annotation?.label ||
        (typeof r.label === "object" ? r.label?.label : r.label),
      verified:
        r.annotation?.verified ??
        r.verified ??
        r.label_verified ??
        r.label?.verified,
      notes: r.annotation?.notes ?? r.notes ?? r.label?.notes,
    };
  }
  async function loadRecordings() {
    state.recordings = itemArray(await api("/api/recordings"), "recordings");
    renderRecordings();
  }
  function renderRecordings() {
    const records = state.recordings.map(recordingMeta);
    $("#recording-count").textContent =
      `${records.length} ${records.length === 1 ? "Aufnahme" : "Aufnahmen"}`;
    $("#recordings-empty").hidden = records.length > 0;
    const table = $("#recordings-table");
    table.replaceChildren(
      ...records.map((r) => {
        const label = r.label
          ? pill(
              `${r.label}${r.verified ? " · bestätigt" : " · Vorschlag"}`,
              r.verified ? "green" : "amber",
            )
          : pill("Ohne Label");
        const actions = el("div", { class: "table-actions" }, [
          button("Replay", () =>
            action(async () => {
              await changeView("empfang");
              $("#rx-source").value = "recording";
              updateReceiverFields();
              $("#rx-recording").value = r.id;
              await startReceiver();
            }),
          ),
          button("Label", () => {
            const form = $("#label-form");
            form.elements.recording_id.value = r.id;
            form.elements.label.value = r.label || "";
            form.elements.notes.value = r.notes || "";
            form.elements.verified.checked = !!r.verified;
            $("#label-dialog-title").textContent = r.name;
            openDialog("label-dialog");
          }),
          button("Prüfen", () =>
            action(async () => {
              const result = await api(
                `/api/recordings/${encodeURIComponent(r.id)}/classify`,
                {
                  method: "POST",
                  body: { use_priors: !!state.settings.use_priors },
                },
              );
              renderClassification(result, $("#recording-classification"));
              openDialog("classification-dialog");
            }),
          ),
          download(
            `/api/recordings/${encodeURIComponent(r.id)}/download`,
            "SigMF herunterladen",
            true,
          ),
        ]);
        return el("tr", {}, [
          detailCell(r.name, sourceLabel(r.source)),
          el("td", { class: "mono", text: mhz(r.center_frequency_hz) }),
          detailCell(
            `${num(Number(r.sample_rate) / 1e6, 3)} MS/s`,
            duration(r.duration_seconds),
            "mono",
          ),
          el("td", {}, [label]),
          el("td", {}, [actions]),
        ]);
      }),
    );
    const previous = $("#rx-recording").value;
    $("#rx-recording").replaceChildren(
      ...(records.length
        ? records.map((r) =>
            el("option", {
              value: r.id,
              text: `${r.name} · ${mhz(r.center_frequency_hz)}`,
            }),
          )
        : [el("option", { value: "", text: "Keine Aufnahme vorhanden" })]),
    );
    if (records.some((r) => r.id === previous))
      $("#rx-recording").value = previous;
    const selected = new Set(
      $$("#experiment-recordings input:checked").map((n) => n.value),
    );
    $("#experiment-recordings").replaceChildren(
      ...(records.filter((r) => r.verified && r.label).length
        ? records
            .filter((r) => r.verified && r.label)
            .map((r) =>
              el("label", { class: "checkbox" }, [
                el("input", {
                  type: "checkbox",
                  value: r.id,
                  checked: selected.has(r.id),
                }),
                el("span", {}, [
                  text(r.name),
                  el("small", {
                    text: `${r.label} · ${duration(r.duration_seconds)}`,
                  }),
                ]),
              ]),
            )
        : [
            el("p", {
              class: "field-help",
              text: "Zuerst Aufnahmen importieren und Labels bestätigen.",
            }),
          ]),
    );
  }
  async function loadEmitters() {
    state.emitters = itemArray(await api("/api/emitters"), "emitters");
    renderEmitters();
  }
  function renderEmitters() {
    const q = $("#emitter-filter").value.toLocaleLowerCase("de");
    const rows = state.emitters.filter((r) =>
      [r.name, r.service, r.standard, r.modulation, r.region].some((v) =>
        String(v || "")
          .toLocaleLowerCase("de")
          .includes(q),
      ),
    );
    $("#emitter-count").textContent = `${rows.length} Einträge`;
    $("#emitters-table").replaceChildren(
      ...rows.map((r) =>
        el("tr", {}, [
          detailCell(r.name, r.service),
          el("td", { text: text(r.standard) }),
          el("td", {}, [pill(text(r.modulation))]),
          detailCell(
            `${mhz(r.frequency_min_hz)} – ${mhz(r.frequency_max_hz)}`,
            r.notes,
            "mono",
          ),
          detailCell(r.region, r.source),
        ]),
      ),
    );
  }
  function renderSuggestions(result) {
    const rows = itemArray(result, "suggestions");
    $("#suggestions").hidden = false;
    $("#suggestions").replaceChildren(
      ...(rows.length
        ? rows.map((r) =>
            el("div", { class: "suggestion" }, [
              el("div", {}, [
                el("strong", {
                  text: `${text(r.name || r.service)} · ${text(r.modulation)}`,
                }),
                el("small", { text: text(r.reason || r.notes || r.source) }),
              ]),
              pill("Unbestätigter Vorschlag", "amber"),
            ]),
          )
        : [
            el("p", {
              class: "secondary",
              text: "Keine passenden Einträge. Das Signal bleibt ohne Frequenzvorschlag.",
            }),
          ]),
    );
  }
  async function loadTopologies() {
    state.topologies = itemArray(await api("/api/topologies"), "topologies");
    renderTopologies();
    const previous = $("#experiment-topology").value;
    $("#experiment-topology").replaceChildren(
      ...state.topologies.map((t) =>
        el("option", {
          value: t.id,
          text: `${t.name} · ${t.family}${t.status === "requires_torch" ? " · PyTorch nötig" : ""}`,
        }),
      ),
    );
    if (state.topologies.some((t) => t.id === previous))
      $("#experiment-topology").value = previous;
    else if (state.topologies.some((t) => t.id === "feature-lda"))
      $("#experiment-topology").value = "feature-lda";
  }
  function renderTopologies() {
    const selected = $("#topology-family").value;
    const families = [
      ...new Set(state.topologies.map((t) => t.family).filter(Boolean)),
    ];
    $("#topology-family").replaceChildren(
      el("option", { value: "", text: "Alle Familien" }),
      ...families.map((f) => el("option", { value: f, text: f })),
    );
    $("#topology-family").value = selected;
    const q = $("#topology-search").value.toLocaleLowerCase("de");
    const rows = state.topologies.filter(
      (t) =>
        (!selected || t.family === selected) &&
        [t.name, t.family, t.description, t.origin].some((v) =>
          String(v || "")
            .toLocaleLowerCase("de")
            .includes(q),
        ),
    );
    $("#topology-count").textContent =
      `${rows.length} / ${state.topologies.length} Topologien`;
    $("#topologies-empty").hidden = rows.length > 0;
    $("#topology-grid").replaceChildren(
      ...rows.map((t) =>
        el("article", { class: "topology-card" }, [
          el("div", { class: "topology-card-body" }, [
            el("div", { class: "topology-card-top" }, [
              el("span", { class: "family-label", text: text(t.family) }),
              pill(
                { low: "Klein", medium: "Mittel", high: "Groß" }[
                  t.complexity
                ] || text(t.complexity, "Architektur"),
              ),
            ]),
            el("h3", { text: t.name }),
            el("p", { text: text(t.description) }),
            el("div", { class: "topology-tags" }, [
              el("span", { text: text(t.input, "Signalfeatures") }),
              el("span", {
                text:
                  t.status === "requires_torch"
                    ? "PyTorch erforderlich"
                    : "Verfügbar",
              }),
              el("span", { text: "Untrainierte Topologie" }),
            ]),
          ]),
          el("div", { class: "topology-card-actions" }, [
            button("Details & JSON", () => openTopology(t), "text-button"),
            button(
              "Experiment",
              () => selectTopology(t.id),
              "button button-quiet",
            ),
          ]),
        ]),
      ),
    );
  }
  function openTopology(t) {
    state.selectedTopology = t;
    $("#topology-dialog-title").textContent = t.name || "Eigene Topologie";
    $("#topology-description").textContent = t.rationale || t.description || "";
    $("#topology-properties").replaceChildren(
      ...[
        ["Familie", t.family],
        ["Eingang", t.input],
        ["Herkunft", t.origin],
        ["Eignung", arr(t.recommended_for).join(", ")],
        ["Status", t.status],
        ["Grenzen", t.limitations],
      ]
        .filter(([, v]) => v)
        .map(([k, v]) =>
          el("div", {}, [
            el("span", { text: k }),
            el("strong", {
              text: typeof v === "object" ? JSON.stringify(v) : String(v),
            }),
          ]),
        ),
    );
    $("#topology-json").value = JSON.stringify(t, null, 2);
    $("#topology-download").hidden = !t.id;
    $("#topology-download").href =
      `/api/topologies/${encodeURIComponent(t.id || "")}/download`;
    $("#topology-experiment").hidden = !t.id;
    openDialog("topology-dialog");
  }
  function newTopology() {
    openTopology({
      name: "Eigene Architektur",
      family: "feature_mlp",
      input: "features",
      parameters: { width: 32, depth: 2 },
      description: "Eigene Topologie für den AM/FM-Vergleich.",
    });
  }
  async function selectTopology(id) {
    closeDialog($("#topology-dialog"));
    await changeView("experimente");
    $("#experiment-topology").value = id;
    $("#experiment-topology").focus();
    toast("Topologie für das nächste Experiment gewählt.");
  }
  async function loadExperiments() {
    state.experiments = itemArray(await api("/api/experiments"), "experiments");
    renderExperiments();
    if (!state.selectedExperiment && state.experiments.length) {
      const finished = state.experiments.find((e) =>
        ["completed", "finished", "done"].includes(e.status),
      );
      if (finished) await loadExperiment(finished.id);
    }
  }
  function statusName(s) {
    return (
      {
        queued: "Warteschlange",
        running: "Läuft",
        completed: "Abgeschlossen",
        finished: "Abgeschlossen",
        done: "Abgeschlossen",
        failed: "Fehlgeschlagen",
        error: "Fehler",
        cancelled: "Abgebrochen",
        interrupted: "Unterbrochen",
        cancelling: "Wird abgebrochen",
      }[s] || text(s)
    );
  }
  function renderExperiments() {
    $("#experiment-list").replaceChildren(
      ...(state.experiments.length
        ? state.experiments.map((e) => {
            const running = ["running", "queued", "cancelling"].includes(
              e.status,
            );
            const row = el(
              "div",
              {
                class: `experiment-row${state.selectedExperiment === e.id ? " selected" : ""}`,
                tabIndex: 0,
                role: "button",
                "aria-label": `${e.name || e.config?.name || e.id} öffnen`,
                onClick: () => action(() => loadExperiment(e.id)),
              },
              [
                el("div", { class: "experiment-row-main" }, [
                  el("strong", { text: e.name || e.config?.name || e.id }),
                  el("small", {
                    text: `${e.config?.source === "synthetic" ? "Synthetisch · " : ""}${e.config?.topology_id || e.topology_id || ""} · ${date(e.created_at || e.started_at)}`,
                  }),
                  running
                    ? el("div", { class: "progress-bar" }, [
                        el("i", {
                          style: `width:${Math.max(2, Math.min(100, typeof e.progress === "number" ? (e.progress <= 1 ? e.progress * 100 : e.progress) : 0))}%`,
                        }),
                      ])
                    : null,
                ]),
                pill(
                  statusName(e.status),
                  running
                    ? "cyan"
                    : ["failed", "error"].includes(e.status)
                      ? "red"
                      : e.status === "completed"
                        ? "green"
                        : "",
                ),
              ],
            );
            row.addEventListener("keydown", (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                action(() => loadExperiment(e.id));
              }
            });
            if (running)
              row.append(
                button(
                  "Abbrechen",
                  (event) => {
                    event.stopPropagation();
                    action(async () => {
                      await api(
                        `/api/experiments/${encodeURIComponent(e.id)}/cancel`,
                        { method: "POST" },
                      );
                      await loadExperiments();
                    });
                  },
                  "text-button",
                ),
              );
            return row;
          })
        : [empty("Noch kein Experiment durchgeführt.")]),
    );
  }
  function parseList(value, name, integer = false) {
    const values = String(value)
      .split(/[,;\s]+/)
      .filter(Boolean)
      .map(Number);
    if (
      !values.length ||
      values.some(
        (v) => !Number.isFinite(v) || (integer && !Number.isInteger(v)),
      )
    )
      throw new Error(`${name}: bitte gültige Zahlen mit Komma trennen.`);
    return values;
  }
  function experimentConfig() {
    const form = $("#experiment-form");
    const f = new FormData(form);
    const lengths = parseList(f.get("segment_lengths"), "Segmentlängen", true);
    const config = {
      name: f.get("name"),
      source: f.get("source"),
      topology_id: f.get("topology_id"),
      samples_per_class: Number(f.get("samples_per_class")),
      seed: Number(f.get("seed")),
      segment_length: lengths[0],
      segment_lengths: lengths,
      snr_db: parseList(f.get("snr_db"), "SNR"),
      jitter: parseList(f.get("jitter"), "Jitter"),
      epochs: Number(f.get("epochs")),
      batch_size: parseList(f.get("batch_sizes"), "Batchgrößen", true)[0],
      batch_sizes: parseList(f.get("batch_sizes"), "Batchgrößen", true),
      learning_rate: parseList(f.get("learning_rates"), "Lernraten")[0],
      learning_rates: parseList(f.get("learning_rates"), "Lernraten"),
      device: f.get("device"),
      use_priors: f.has("use_priors"),
    };
    config.augmentation = Object.fromEntries(
      [
        "frequency_offset_hz",
        "sample_clock_ppm",
        "iq_gain_db",
        "iq_phase_deg",
        "dc_offset",
        "clip_level",
        "echo_amplitude",
        "echo_delay_samples",
      ].map((k) => [k, Number(f.get(k))]),
    );
    if (config.source === "recordings")
      config.recording_ids = $$("#experiment-recordings input:checked").map(
        (n) => n.value,
      );
    return config;
  }
  async function startExperiment() {
    const form = $("#experiment-form");
    if (!form.reportValidity()) return;
    const config = experimentConfig();
    const result = await api("/api/experiments", {
      method: "POST",
      body: config,
    });
    state.selectedExperiment = result.id;
    await loadExperiments();
    await loadExperiment(result.id);
    toast("Experiment eingeplant. Fortschritt wird automatisch aktualisiert.");
  }
  async function loadExperiment(id) {
    const e = await api(`/api/experiments/${encodeURIComponent(id)}`);
    state.selectedExperiment = id;
    renderExperimentResult(e);
    renderExperiments();
    return e;
  }
  function metric(label, value, note) {
    return el("div", { class: "result-metric" }, [
      el("span", { class: "metric-label", text: label }),
      el("strong", { text: value }),
      el("small", { text: note }),
    ]);
  }
  function renderExperimentResult(experiment) {
    const report = experiment.report || experiment.result || experiment;
    const metrics = report.metrics || {};
    const source =
      report.source || report.config?.source || experiment.config?.source;
    const body = $("#experiment-result-body");
    $("#experiment-result-title").textContent =
      experiment.name ||
      experiment.config?.name ||
      report.config?.name ||
      experiment.id ||
      "Ergebnisse";
    $("#experiment-result-empty").hidden = true;
    body.hidden = false;
    $("#experiment-download").hidden =
      !["completed", "finished", "done"].includes(experiment.status) &&
      !report.metrics;
    $("#experiment-download").href =
      `/api/experiments/${encodeURIComponent(experiment.id || state.selectedExperiment)}/download`;
    if (
      ["failed", "error", "cancelled", "cancelling", "interrupted"].includes(
        experiment.status,
      )
    ) {
      body.replaceChildren(
        el("div", {
          class: "result-error",
          text:
            experiment.error ||
            experiment.message ||
            statusName(experiment.status),
        }),
      );
      return;
    }
    if (["queued", "running"].includes(experiment.status) && !report.metrics) {
      const progress =
        typeof experiment.progress === "number"
          ? experiment.progress
          : experiment.progress?.fraction || 0;
      body.replaceChildren(
        el("div", { class: "form-body" }, [
          pill(statusName(experiment.status), "cyan"),
          el("p", {
            class: "secondary",
            text:
              typeof experiment.progress === "object"
                ? JSON.stringify(experiment.progress)
                : experiment.message ||
                  "Training und Auswertung laufen im Hintergrund.",
          }),
          el("div", { class: "progress-bar" }, [
            el("i", {
              style: `width:${Math.max(2, Math.min(100, progress <= 1 ? progress * 100 : progress))}%`,
            }),
          ]),
          el("p", {
            class: "field-help",
            text: "Messwerte erscheinen nach Abschluss. Die Oberfläche bleibt bedienbar.",
          }),
        ]),
      );
      return;
    }
    const n = el("div");
    if (source === "synthetic" || report.synthetic)
      n.append(
        el("p", {
          class: "result-notice",
          text: "Synthetischer Versuch · Diese Ergebnisse beschreiben generierte AM/FM-Signale. Reale RF-Aufnahmen müssen unabhängig geprüft werden.",
        }),
      );
    n.append(
      el("div", { class: "result-metrics" }, [
        metric(
          "GENAUIGKEIT",
          percent(metrics.accuracy),
          "Unabhängiger Testsatz",
        ),
        metric(
          "MACRO-F1",
          num(metrics.macro_f1, 3),
          "Klassen gleich gewichtet",
        ),
        metric(
          "TRAININGSDAUER",
          duration(metrics.training_seconds),
          "Gemessene Wandzeit",
        ),
        metric(
          "KLASSIFIKATION · P50",
          `${num(metrics.latency_ms_p50, 3)} ms`,
          `P95 ${num(metrics.latency_ms_p95, 3)} ms`,
        ),
        metric(
          "MODELLGRÖSSE",
          bytes(metrics.model_bytes),
          "Gespeichertes Artefakt",
        ),
        metric(
          "ECHTZEITFAKTOR",
          num(metrics.real_time_factor, 4),
          "Rechenzeit / Signaldauer",
        ),
        metric(
          "EMPFINDLICHKEIT · 90 %",
          metrics.sensitivity_snr90_db === null ||
            metrics.sensitivity_snr90_db === undefined
            ? "—"
            : `${num(metrics.sensitivity_snr90_db)} dB`,
          "Niedrigster geprüfter SNR mit ≥ 90 %",
        ),
        metric(
          "NORMIERTE LATENZ",
          num(metrics.normalized_latency, 3),
          "Verhältnis zur lokalen CPU-Referenz",
        ),
        metric(
          "KALIBRIERUNGSFEHLER",
          num(metrics.ece, 4),
          "ECE · kleiner ist besser",
        ),
      ]),
    );
    const confusion = el("div", { class: "result-chart" }, [
      el("h3", { text: "Verwechslungsmatrix" }),
    ]);
    const classes = arr(report.classes).length ? report.classes : ["AM", "FM"];
    const matrix = arr(report.confusion_matrix);
    if (matrix.length) {
      const grid = el(
        "div",
        {
          class: "confusion-matrix",
          style: `grid-template-columns:38px repeat(${classes.length},minmax(55px,1fr));min-width:${38 + classes.length * 60}px;max-width:none`,
        },
        [
          el("span"),
          ...classes.map((c) => el("span", { class: "matrix-label", text: c })),
        ],
      );
      matrix.forEach((row, i) => {
        grid.append(
          el("span", { class: "matrix-label", text: classes[i] || String(i) }),
        );
        arr(row).forEach((v, j) =>
          grid.append(
            el("div", {
              class: "matrix-cell",
              style:
                i === j ? "border-color:var(--cyan);color:var(--cyan)" : "",
              text: num(v, 0),
            }),
          ),
        );
      });
      confusion.append(
        grid,
        el("p", {
          class: "matrix-axis",
          text: "Zeilen: Referenz · Spalten: Vorhersage",
        }),
      );
    } else confusion.append(empty("Keine Matrix im Bericht."));
    const robustness = el("div", { class: "result-chart" }, [
      el("h3", { text: "Genauigkeit über SNR · gewähltes Modell" }),
      el("canvas", {
        id: "snr-chart",
        role: "img",
        "aria-label":
          "Gemessene Klassifikationsgenauigkeit über den SNR-Teststufen",
      }),
      el("p", {
        class: "matrix-axis",
        text: "Mittelwert über die konfigurierten Jitter-Stufen",
      }),
    ]);
    n.append(el("div", { class: "result-charts" }, [confusion, robustness]));
    const extra = el("details", { class: "result-details" }, [
      el("summary", { text: "Messbedingungen, Kalibrierung & Hardware" }),
      el("pre", {
        text: JSON.stringify(
          {
            metrics,
            hardware: report.hardware,
            split:
              report.split_groups ||
              report.split ||
              report.dataset ||
              report.provenance,
            selection: report.selection,
            evaluation_policy: report.evaluation_policy,
            limitations: report.limitations,
            config: report.config,
          },
          null,
          2,
        ),
      }),
    ]);
    n.append(extra);
    const trials = arr(report.trials);
    if (trials.length) {
      const table = el("table", { class: "data-table" }, [
        el("thead", {}, [
          el(
            "tr",
            {},
            [
              "Kandidat",
              "Samples",
              "SNR / dB",
              "Jitter / Samples",
              "Genauigkeit",
              "Macro-F1",
            ].map((x) => el("th", { text: x })),
          ),
        ]),
        el(
          "tbody",
          {},
          trials.map((t) =>
            el(
              "tr",
              {},
              [
                t.candidate,
                t.segment_length,
                t.snr_db,
                t.jitter,
                percent(t.accuracy),
                num(t.macro_f1, 3),
              ].map((x) => el("td", { class: "mono", text: text(x) })),
            ),
          ),
        ),
      ]);
      n.append(
        el("details", { class: "result-details" }, [
          el("summary", { text: `Alle ${trials.length} Robustheitsmessungen` }),
          el("div", { class: "table-scroll" }, [table]),
        ]),
      );
    }
    body.replaceChildren(n);
    requestAnimationFrame(() =>
      drawSNR(
        trials.filter(
          (t) =>
            !report.selected_candidate ||
            t.candidate === report.selected_candidate,
        ),
      ),
    );
  }
  function drawSNR(trials) {
    const canvas = $("#snr-chart");
    if (!canvas) return;
    const { ctx, width: w, height: h } = canvasSetup(canvas);
    ctx.clearRect(0, 0, w, h);
    const left = 37,
      right = 14,
      top = 13,
      bottom = 28,
      pw = w - left - right,
      ph = h - top - bottom;
    ctx.font = "10px Consolas,monospace";
    ctx.strokeStyle = css("--grid");
    ctx.fillStyle = css("--faint");
    for (let i = 0; i <= 4; i++) {
      const y = top + (ph * i) / 4;
      ctx.beginPath();
      ctx.moveTo(left, y + 0.5);
      ctx.lineTo(w - right, y + 0.5);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(`${100 - i * 25}`, left - 7, y + 3);
    }
    const points = new Map();
    trials.forEach((t) => {
      if (
        !Number.isFinite(Number(t.snr_db)) ||
        !Number.isFinite(Number(t.accuracy))
      )
        return;
      const key = Number(t.snr_db);
      if (!points.has(key)) points.set(key, []);
      points.get(key).push(Number(t.accuracy));
    });
    const sorted = [...points]
      .sort((a, b) => a[0] - b[0])
      .map(([x, values]) => ({
        x,
        y: values.reduce((a, b) => a + b, 0) / values.length,
      }));
    if (!sorted.length) {
      ctx.fillStyle = css("--faint");
      ctx.textAlign = "center";
      ctx.fillText("Keine SNR-Messreihe", w / 2, h / 2);
      return;
    }
    const min = sorted[0].x,
      max = sorted[sorted.length - 1].x;
    const coords = sorted.map((p) => ({
      x:
        sorted.length === 1
          ? left + pw / 2
          : left + ((p.x - min) / Math.max(1, max - min)) * pw,
      y: top + (1 - p.y) * ph,
      value: p.x,
    }));
    ctx.beginPath();
    coords.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.strokeStyle = css("--cyan");
    ctx.lineWidth = 2;
    ctx.stroke();
    coords.forEach((p) => {
      ctx.fillStyle = css("--cyan");
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = css("--faint");
      ctx.textAlign = "center";
      ctx.fillText(num(p.value, 0), p.x, h - 9);
    });
  }
  async function loadModels() {
    state.models = itemArray(await api("/api/models"), "models");
    renderModels();
  }
  function renderModels() {
    $("#models-empty").hidden = state.models.length > 0;
    $("#model-grid").replaceChildren(
      ...state.models.map((m) => {
        const meta = m.metadata || m;
        const metrics = meta.metrics || m.report?.metrics || {};
        const active =
          m.active ||
          state.status.active_model?.model_id === m.id ||
          state.status.active_model === m.id;
        const card = el("article", { class: "panel model-card" }, [
          el("div", { class: "panel-heading" }, [
            el("div", {}, [
              el("span", {
                class: "panel-kicker",
                text: text(
                  meta.family || meta.topology_id || meta.topology?.family,
                  "TRAINIERTES MODELL",
                ),
              }),
              el("h2", { text: m.name || meta.name || m.id }),
            ]),
            pill(active ? "Aktiv" : "Gespeichert", active ? "cyan" : ""),
          ]),
          el(
            "div",
            { class: "model-stats" },
            [
              ["GENAUIGKEIT", percent(metrics.accuracy)],
              ["GRÖSSE", bytes(metrics.model_bytes ?? m.size_bytes)],
              ["KLASSEN", arr(meta.classes).join(" / ") || "AM / FM"],
              ["ERSTELLT", date(m.created_at || meta.created_at)],
            ].map(([k, v]) =>
              el("div", {}, [
                el("span", { class: "metric-label", text: k }),
                el("strong", { text: v }),
              ]),
            ),
          ),
          el("div", {
            class: "panel-note",
            text:
              meta.source === "synthetic" ||
              meta.config?.source === "synthetic" ||
              meta.provenance?.synthetic ||
              meta.synthetic
                ? "Trainiert mit synthetischen Daten · Realempfang separat validieren."
                : text(
                    meta.description,
                    "Vorverarbeitung und Topologie im Modellpaket enthalten.",
                  ),
          }),
          el("div", { class: "panel-actions" }, [
            button(
              active ? "Aktiv" : "Für Klassifikation aktivieren",
              () =>
                action(async () => {
                  await api(
                    `/api/models/${encodeURIComponent(m.id)}/activate`,
                    { method: "POST" },
                  );
                  await Promise.all([loadStatus(), loadModels()]);
                  toast("Modell aktiviert.");
                }),
              active ? "button button-quiet" : "button button-primary",
            ),
            download(
              `/api/models/${encodeURIComponent(m.id)}/download`,
              "Modell exportieren",
            ),
          ]),
        ]);
        return card;
      }),
    );
  }
  function renderBenchmark(data) {
    const hw = data.hardware || data;
    const metrics = data.metrics || data.benchmark || data;
    const items = [
      ["CPU", hw.cpu || hw.processor || hw.machine],
      ["GPU", hw.gpu_name || hw.gpu || hw.cuda_device || "CPU-Betrieb"],
      [
        "ARBEITSSPEICHER",
        bytes(hw.memory_bytes || hw.ram_bytes || hw.ram_total_bytes),
      ],
      [
        "REFERENZLAUF",
        `${num(metrics.reference_latency_ms ?? metrics.latency_ms ?? metrics.elapsed_ms, 3)} ms`,
      ],
      [
        "MESSVERFAHREN",
        metrics.method ||
          data.method ||
          data.reference ||
          "Lokaler Referenzbenchmark",
      ],
      ["SOFTWARE", hw.python || hw.python_version || "Siehe Bericht"],
    ];
    $("#benchmark-result").replaceChildren(
      el(
        "div",
        { class: "benchmark-grid" },
        items.map(([label, value]) =>
          el("div", {}, [
            el("span", { class: "metric-label", text: label }),
            el("strong", {
              text:
                typeof value === "object" ? JSON.stringify(value) : text(value),
            }),
          ]),
        ),
      ),
      el("details", { class: "result-details" }, [
        el("summary", { text: "Vollständige Messbedingungen" }),
        el("pre", { text: JSON.stringify(data, null, 2) }),
      ]),
    );
  }
  async function loadSettings() {
    state.settings = await api("/api/settings");
    $("#live-priors").checked = !!state.settings.use_priors;
    $("#online-map").checked = !!state.settings.online_map;
    $("#label-region").textContent =
      state.settings.region || "Deutschland · Bayern";
    populateSettings();
  }
  function populateSettings() {
    const f = $("#settings-form");
    f.elements.region.value =
      state.settings.region || "Deutschland, Bayern, Bamberg / Nürnberg";
    f.elements.station_lat.value = state.settings.station_lat ?? "";
    f.elements.station_lon.value = state.settings.station_lon ?? "";
    f.elements.use_priors.checked = !!state.settings.use_priors;
    f.elements.online_map.checked = !!state.settings.online_map;
  }
  async function saveSettings(values) {
    const merged = { ...state.settings, ...values };
    const payload = Object.fromEntries(
      ["use_priors", "region", "station_lat", "station_lon", "online_map"].map(
        (k) => [k, merged[k]],
      ),
    );
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: payload,
    });
    $("#live-priors").checked = !!state.settings.use_priors;
    $("#online-map").checked = !!state.settings.online_map;
    $("#label-region").textContent =
      state.settings.region || "Deutschland · Bayern";
    updateMapTiles();
    return state.settings;
  }
  async function loadDrones() {
    const [tracks, capabilities] = await Promise.all([
      api("/api/drones/tracks"),
      api("/api/drones/capabilities"),
    ]);
    state.drones = itemArray(tracks, "tracks");
    $("#drone-count").textContent = String(state.drones.length);
    renderDroneTracks();
    $("#drone-capabilities").replaceChildren(
      ...itemArray(capabilities, "capabilities").map((c) =>
        el("tr", {}, [
          detailCell(c.protocol, c.name),
          el("td", {}, [
            pill(
              text(c.status),
              String(c.status).includes("available") ? "green" : "",
            ),
          ]),
          detailCell(
            typeof c.position === "boolean"
              ? c.position
                ? "Position unterstützt"
                : "Keine Position"
              : text(c.position),
            typeof c.video === "boolean"
              ? c.video
                ? "Video unterstützt"
                : "Kein Videodecoder"
              : text(c.video),
          ),
          detailCell(
            typeof c.hardware === "object"
              ? JSON.stringify(c.hardware)
              : c.hardware,
            Array.isArray(c.notes) ? c.notes.join(" ") : c.notes,
          ),
        ]),
      ),
    );
    if (state.map) renderMapTracks();
  }
  function positionText(p) {
    return p &&
      Number.isFinite(Number(p.lat)) &&
      Number.isFinite(Number(p.lon)) &&
      p.lat !== null &&
      p.lon !== null
      ? `${num(p.lat, 5)}°, ${num(p.lon, 5)}°`
      : "Keine Position gemeldet";
  }
  function hasPosition(p) {
    return (
      !!p &&
      p.lat !== null &&
      p.lon !== null &&
      Number.isFinite(Number(p.lat)) &&
      Number.isFinite(Number(p.lon))
    );
  }
  function droneHeights(p = {}) {
    return (
      [
        ["altitude_geodetic_m", "m WGS84 HAE"],
        ["altitude_pressure_m", "m Druckhöhe"],
        ["height_agl_m", "m AGL"],
        ["height_above_takeoff_m", "m über Startpunkt"],
      ]
        .filter(([key]) => p[key] !== null && p[key] !== undefined)
        .map(([key, unit]) => `${num(p[key], 1)} ${unit}`)
        .join(" · ") || "Keine Höhe gemeldet"
    );
  }
  function renderDroneTracks() {
    $("#drone-tracks").replaceChildren(
      ...(state.drones.length
        ? state.drones.map((t) =>
            el("div", { class: "drone-track" }, [
              el("div", { class: "drone-track-heading" }, [
                el("strong", { text: t.id || t.uas_id }),
                pill(
                  t.position_stale || t.stale ? "Veraltet" : "Empfangen",
                  t.position_stale || t.stale ? "amber" : "cyan",
                ),
              ]),
              el("p", { text: `Drohne: ${positionText(t.drone)}` }),
              el("p", { text: droneHeights(t.drone) }),
              el("p", { text: `Steuerung: ${positionText(t.operator)}` }),
              t.home
                ? el("p", { text: `Home: ${positionText(t.home)}` })
                : null,
              el("p", { text: `${text(t.protocol)} · ${text(t.source)}` }),
              el("p", {
                text: `Positionsalter ${duration(t.position_age_seconds)} · Empfang ${date(t.received_at)}`,
              }),
              hasPosition(t.drone)
                ? button(
                    "Auf Karte zeigen",
                    () => {
                      initializeMap();
                      state.map?.setView([t.drone.lat, t.drone.lon], 14);
                    },
                    "text-button",
                  )
                : null,
            ]),
          )
        : [
            empty(
              "Noch keine Telemetrie. Eine passende Empfangsbrücke verbinden oder einen Datensatz importieren.",
            ),
          ]),
    );
  }
  function initializeMap() {
    if (state.map || state.view !== "drohnen") return;
    if (!window.L) {
      $("#drone-map").replaceChildren(
        el("div", { class: "map-empty-grid" }),
        el("div", {
          class: "empty-state",
          text: "Kartenbibliothek nicht verfügbar. Koordinaten stehen in der Trackliste.",
        }),
      );
      return;
    }
    const center = [
      state.settings.station_lat ?? 49.65,
      state.settings.station_lon ?? 11.0,
    ];
    state.map = L.map("drone-map", {
      attributionControl: true,
      zoomControl: true,
    }).setView(center, 9);
    const Grid = L.GridLayer.extend({
      createTile(coords) {
        const tile = document.createElement("canvas");
        tile.width = 256;
        tile.height = 256;
        const ctx = tile.getContext("2d");
        ctx.fillStyle = css("--plot");
        ctx.fillRect(0, 0, 256, 256);
        ctx.strokeStyle = css("--grid");
        ctx.lineWidth = 0.6;
        ctx.strokeRect(0, 0, 256, 256);
        ctx.beginPath();
        ctx.moveTo(128, 0);
        ctx.lineTo(128, 256);
        ctx.moveTo(0, 128);
        ctx.lineTo(256, 128);
        ctx.stroke();
        const latlng = state.map.unproject(
          [coords.x * 256 + 7, coords.y * 256 + 15],
          coords.z,
        );
        ctx.fillStyle = css("--faint");
        ctx.font = "11px Consolas,monospace";
        ctx.fillText(
          `${latlng.lat.toFixed(3)}° N  ${latlng.lng.toFixed(3)}° E`,
          9,
          18,
        );
        return tile;
      },
    });
    state.mapGrid = new Grid({
      tileSize: 256,
      attribution: "Offline-Koordinatenraster · WGS84",
    });
    state.mapMarkers = L.layerGroup().addTo(state.map);
    updateMapTiles();
    renderMapTracks();
  }
  function updateMapTiles() {
    if (!state.map) return;
    if (state.mapTiles) {
      state.map.removeLayer(state.mapTiles);
      state.mapTiles = null;
    }
    if (state.mapGrid && state.map.hasLayer(state.mapGrid))
      state.map.removeLayer(state.mapGrid);
    if (state.settings.online_map) {
      state.mapTiles = L.tileLayer(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
          maxZoom: 19,
          attribution:
            '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        },
      ).addTo(state.map);
      $("#map-mode-label").textContent = "Online · OpenStreetMap";
    } else {
      state.mapGrid.addTo(state.map);
      $("#map-mode-label").textContent = "Offline · Koordinatenraster";
    }
  }
  function droneBounds() {
    return state.drones.flatMap((t) =>
      [t.drone, t.operator, t.home]
        .filter(hasPosition)
        .map((p) => [Number(p.lat), Number(p.lon)]),
    );
  }
  function fitMap() {
    initializeMap();
    const bounds = droneBounds();
    if (bounds.length)
      state.map?.fitBounds(bounds, { padding: [35, 35], maxZoom: 15 });
    else toast("Noch keine gemeldeten Positionen vorhanden.");
  }
  function renderMapTracks() {
    if (!state.mapMarkers) return;
    state.mapMarkers.clearLayers();
    for (const t of state.drones) {
      for (const [key, label, cls] of [
        ["drone", "Drohne", "drone-marker"],
        ["operator", "Steuerung", "operator-marker"],
        ["home", "Home", "home-marker"],
      ]) {
        const p = t[key];
        if (!hasPosition(p)) continue;
        const stale =
          p.stale ??
          (key === "drone" ? (t.position_stale ?? t.stale) : t.stale);
        const marker = L.marker([p.lat, p.lon], {
          icon: L.divIcon({
            className: `${cls}${stale ? " stale" : ""}`,
            iconSize: [16, 16],
            iconAnchor: [8, 8],
          }),
        });
        const content = el("div", {}, [
          el("strong", { text: `${label} · ${text(t.id)}` }),
          el("p", { text: positionText(p) }),
          key === "drone" ? el("p", { text: droneHeights(p) }) : null,
          el("p", { text: `${stale ? "Veraltet · " : ""}${text(t.source)}` }),
        ]);
        marker.bindPopup(content);
        marker.addTo(state.mapMarkers);
      }
      if (hasPosition(t.drone) && hasPosition(t.operator))
        L.polyline(
          [
            [t.drone.lat, t.drone.lon],
            [t.operator.lat, t.operator.lon],
          ],
          { color: css("--amber"), weight: 1, dashArray: "4 6", opacity: 0.65 },
        ).addTo(state.mapMarkers);
    }
    if (!state.mapFitted && droneBounds().length) {
      fitMap();
      state.mapFitted = true;
    }
  }
  async function loadBridge() {
    const data = await api("/api/drones/bridge");
    $("#bridge-status").textContent = data.running
      ? `Aktiv · ${data.host}:${data.port} · ${text(data.accepted, 0)} angenommen / ${text(data.rejected, 0)} verworfen${data.last_error ? " · " + data.last_error : ""}`
      : "Brücke gestoppt. Externen Decoder mit diesem UDP-Eingang verbinden.";
    if (data.host) $("#bridge-form [name=host]").value = data.host;
    if (data.port) $("#bridge-form [name=port]").value = data.port;
  }
  function exportExperimentConfig() {
    const config = experimentConfig();
    const blob = new Blob(
      [
        JSON.stringify(
          {
            schema_version: 1,
            kind: "signalclassifier-experiment-config",
            config,
          },
          null,
          2,
        ) + "\n",
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = el("a", {
      href: url,
      download: "signalclassifier-experiment.json",
    });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Versuchskonfiguration gespeichert.");
  }
  async function importExperimentConfig(file) {
    if (!file) return;
    if (file.size > 1048576)
      throw new Error("Die Konfigurationsdatei ist zu groß.");
    const payload = JSON.parse(await file.text());
    const c = payload.config || payload.report?.config || payload;
    if (!c || typeof c !== "object" || Array.isArray(c))
      throw new Error(
        "Eine Versuchskonfiguration als JSON-Objekt ist erforderlich.",
      );
    const form = $("#experiment-form");
    const allowed = [
      "name",
      "topology_id",
      "source",
      "seed",
      "samples_per_class",
      "epochs",
      "device",
    ];
    if (c.topology_id && !state.topologies.some((t) => t.id === c.topology_id))
      throw new Error(
        "Die Topologie dieser Konfiguration zuerst im Tab Topologien laden.",
      );
    if (c.source && !["synthetic", "recordings"].includes(c.source))
      throw new Error("Ungültige Datenquelle.");
    for (const key of [
      "segment_lengths",
      "snr_db",
      "jitter",
      "learning_rates",
      "batch_sizes",
    ])
      if (
        c[key] !== undefined &&
        (!Array.isArray(c[key]) ||
          c[key].some((v) => typeof v !== "number" || !Number.isFinite(v)))
      )
        throw new Error(`${key} muss eine Liste endlicher Zahlen sein.`);
    for (const key of allowed) {
      if (c[key] !== undefined && form.elements[key])
        form.elements[key].value = String(c[key]);
    }
    form.elements.segment_lengths.value = arr(c.segment_lengths).length
      ? c.segment_lengths.join(", ")
      : String(c.segment_length || 2048);
    if (c.snr_db) form.elements.snr_db.value = c.snr_db.join(", ");
    if (c.jitter) form.elements.jitter.value = c.jitter.join(", ");
    form.elements.learning_rates.value = arr(c.learning_rates).length
      ? c.learning_rates.join(", ")
      : String(c.learning_rate || 0.001);
    form.elements.batch_sizes.value = arr(c.batch_sizes).length
      ? c.batch_sizes.join(", ")
      : String(c.batch_size || 32);
    for (const key of [
      "frequency_offset_hz",
      "sample_clock_ppm",
      "iq_gain_db",
      "iq_phase_deg",
      "dc_offset",
      "clip_level",
      "echo_amplitude",
      "echo_delay_samples",
    ]) {
      const value = c.augmentation?.[key];
      if (value !== undefined) {
        if (typeof value !== "number" || !Number.isFinite(value))
          throw new Error("Ungültiger Kanalparameter.");
        form.elements[key].value = String(value);
      }
    }
    form.elements.use_priors.checked = c.use_priors === true;
    const selected = new Set(arr(c.recording_ids));
    $$("#experiment-recordings input").forEach((input) => {
      input.checked = selected.has(input.value);
    });
    $("#experiment-recordings-field").hidden =
      form.elements.source.value !== "recordings";
    toast(
      "Versuchskonfiguration geladen. Der Versuch wurde noch nicht gestartet.",
    );
  }
  function setupEvents() {
    document.addEventListener("click", (event) => {
      const view = event.target.closest("[data-view]");
      if (view) changeView(view.dataset.view);
      const open = event.target.closest("[data-open]");
      if (open) openDialog(open.dataset.open);
      const close = event.target.closest("[data-close]");
      if (close) closeDialog(close.closest("dialog"));
    });
    $$("dialog").forEach((d) =>
      d.addEventListener("click", (event) => {
        if (event.target === d) {
          const rect = d.getBoundingClientRect();
          if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          )
            d.close();
        }
      }),
    );
    $("#dismiss-error").addEventListener("click", () => {
      $("#global-error").hidden = true;
    });
    $("#theme-toggle").addEventListener("click", () =>
      setTheme(
        document.documentElement.dataset.theme === "dark" ? "light" : "dark",
      ),
    );
    $("#heading-action").addEventListener("click", () => {
      if (state.view === "empfang")
        action(
          () => (receiverRunning() ? stopReceiver() : startReceiver()),
          $("#receiver-form"),
        );
      else if (state.view === "aufnahmen")
        openDialog("recording-import-dialog");
      else if (state.view === "emitter") openDialog("emitter-dialog");
      else if (state.view === "topologien") newTopology();
      else if (state.view === "experimente") {
        $("#experiment-form input[name=name]").focus();
        $("#experiment-form").scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      } else if (state.view === "modelle") openDialog("model-import-dialog");
      else if (state.view === "drohnen") openDialog("bridge-dialog");
    });
    $("#receiver-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(startReceiver, form);
    });
    $("#rx-stop").addEventListener("click", () => action(stopReceiver));
    $("#rx-source").addEventListener("change", updateReceiverFields);
    $("#receiver-form [name=mode]").addEventListener(
      "change",
      updateReceiverFields,
    );
    $("#waterfall-toggle").addEventListener("change", () => {
      $(".waterfall-stage").hidden = !$("#waterfall-toggle").checked;
      drawWaterfall();
    });
    $("#live-priors").addEventListener("change", () =>
      action(() => saveSettings({ use_priors: $("#live-priors").checked })),
    );
    $("#recording-import-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(async () => {
        await api("/api/recordings/import", {
          method: "POST",
          body: new FormData(form),
        });
        closeDialog($("#recording-import-dialog"));
        form.reset();
        await loadRecordings();
        toast("SigMF-Aufnahme importiert.");
      }, form);
    });
    $("#label-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const f = new FormData(form);
      action(async () => {
        await api(
          `/api/recordings/${encodeURIComponent(f.get("recording_id"))}/label`,
          {
            method: "POST",
            body: {
              label: f.get("label"),
              verified: f.has("verified"),
              notes: f.get("notes"),
            },
          },
        );
        closeDialog($("#label-dialog"));
        await loadRecordings();
        toast("Bestätigtes Label gespeichert.");
      }, form);
    });
    $("#emitter-filter").addEventListener("input", renderEmitters);
    $("#suggest-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(
        async () =>
          renderSuggestions(
            await api(
              `/api/labels/suggest?frequency_hz=${Number($("#suggest-frequency").value) * 1e6}`,
            ),
          ),
        form,
      );
    });
    $("#emitter-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const f = new FormData(form);
      const payload = Object.fromEntries(f);
      payload.frequency_min_hz = Number(f.get("frequency_min_mhz")) * 1e6;
      payload.frequency_max_hz = Number(f.get("frequency_max_mhz")) * 1e6;
      delete payload.frequency_min_mhz;
      delete payload.frequency_max_mhz;
      payload.confidence = 0.5;
      action(async () => {
        await api("/api/emitters", { method: "POST", body: payload });
        closeDialog($("#emitter-dialog"));
        form.reset();
        await loadEmitters();
        toast("Wissensbasis erweitert.");
      }, form);
    });
    $("#topology-search").addEventListener("input", renderTopologies);
    $("#topology-family").addEventListener("change", renderTopologies);
    $("#new-topology").addEventListener("click", newTopology);
    $("#topology-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(async () => {
        const config = JSON.parse($("#topology-json").value);
        config.id = `custom-${Date.now().toString(36)}`;
        config.name = `${config.name || "Eigene Topologie"} · Kopie`;
        await api("/api/topologies", { method: "POST", body: config });
        closeDialog($("#topology-dialog"));
        await loadTopologies();
        toast("Eigene Topologie gespeichert.");
      }, form);
    });
    $("#topology-experiment").addEventListener(
      "click",
      () =>
        state.selectedTopology?.id && selectTopology(state.selectedTopology.id),
    );
    $("#topology-import-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(async () => {
        const file = form.elements.file.files[0];
        if (!file) throw new Error("Bitte eine JSON-Datei auswählen.");
        const config = JSON.parse(await file.text());
        await api("/api/topologies", { method: "POST", body: config });
        closeDialog($("#topology-import-dialog"));
        await loadTopologies();
        toast("Topologie geladen.");
      }, form);
    });
    $("#experiment-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(startExperiment, form);
    });
    $("#experiment-config-export").addEventListener("click", () =>
      action(async () => exportExperimentConfig()),
    );
    $("#experiment-config-import").addEventListener("click", () =>
      $("#experiment-config-file").click(),
    );
    $("#experiment-config-file").addEventListener("change", (event) => {
      const input = event.currentTarget;
      action(async () => {
        await importExperimentConfig(input.files[0]);
        input.value = "";
      });
    });
    $("#experiment-source").addEventListener("change", () => {
      $("#experiment-recordings-field").hidden =
        $("#experiment-source").value !== "recordings";
    });
    $("#refresh-experiments").addEventListener("click", () =>
      action(loadExperiments),
    );
    $("#model-import-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(async () => {
        await api("/api/models/import", {
          method: "POST",
          body: new FormData(form),
        });
        closeDialog($("#model-import-dialog"));
        form.reset();
        await loadModels();
        toast("Modellpaket importiert.");
      }, form);
    });
    $("#run-benchmark").addEventListener("click", () =>
      action(async () => {
        const btn = $("#run-benchmark");
        btn.disabled = true;
        btn.textContent = "Referenz wird gemessen …";
        try {
          renderBenchmark(await api("/api/benchmark"));
        } finally {
          btn.disabled = false;
          btn.textContent = "Referenz messen";
        }
      }),
    );
    $("#settings-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const f = new FormData(form);
      action(async () => {
        await saveSettings({
          region: f.get("region"),
          station_lat:
            f.get("station_lat") === "" ? null : Number(f.get("station_lat")),
          station_lon:
            f.get("station_lon") === "" ? null : Number(f.get("station_lon")),
          use_priors: f.has("use_priors"),
          online_map: f.has("online_map"),
        });
        closeDialog($("#settings-dialog"));
        toast("Einstellungen gespeichert.");
      }, form);
    });
    $("#online-map").addEventListener("change", () =>
      action(() => saveSettings({ online_map: $("#online-map").checked })),
    );
    $("#map-fit").addEventListener("click", fitMap);
    $("#drone-load-example").addEventListener("click", () =>
      action(async () => {
        $("#drone-json").value = JSON.stringify(
          await api("/api/drones/example"),
          null,
          2,
        );
      }),
    );
    $("#drone-ingest-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      action(async () => {
        const payload = JSON.parse($("#drone-json").value);
        await api("/api/drones/ingest", { method: "POST", body: payload });
        closeDialog($("#drone-ingest-dialog"));
        await loadDrones();
        toast("Telemetrie übernommen.");
      }, form);
    });
    $("#bridge-form [name=port]").value = "5700";
    $("#bridge-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const f = new FormData(form);
      action(async () => {
        await api("/api/drones/bridge/start", {
          method: "POST",
          body: { host: f.get("host"), port: Number(f.get("port")) },
        });
        await loadBridge();
        toast("Telemetriebrücke gestartet.");
      }, form);
    });
    $("#bridge-stop").addEventListener("click", () =>
      action(async () => {
        await api("/api/drones/bridge/stop", { method: "POST" });
        await loadBridge();
      }),
    );
    $("#show-system").addEventListener("click", () => {
      $("#system-json").textContent = JSON.stringify(state.status, null, 2);
      openDialog("system-dialog");
    });
    window.addEventListener("hashchange", () =>
      changeView(location.hash.slice(1)),
    );
    const observer = new ResizeObserver(() => {
      drawSpectrum();
      drawWaterfall();
    });
    observer.observe($(".plot-stage"));
  }
  async function poll() {
    if (state.polling) return;
    state.polling = true;
    try {
      await loadStatus();
      if (state.view === "empfang" && (receiverRunning() || !state.spectrum)) {
        await loadSpectrum();
      }
      if (state.view === "drohnen") await loadDrones();
      if (state.view === "experimente") {
        await loadExperiments();
        if (state.selectedExperiment)
          await loadExperiment(state.selectedExperiment);
      }
    } catch (e) {
      console.warn("Aktualisierung fehlgeschlagen", e);
    } finally {
      state.polling = false;
    }
  }
  async function init() {
    try {
      setTheme(localStorage.getItem("signalclassifier-theme") || "dark");
    } catch (e) {
      document.documentElement.dataset.theme = "dark";
    }
    setupEvents();
    updateReceiverFields();
    const clock = () => {
      $("#clock").textContent =
        new Date().toLocaleTimeString("de-DE", {
          timeZone: "UTC",
          hour12: false,
        }) + " UTC";
    };
    clock();
    setInterval(clock, 1000);
    const initial = await Promise.allSettled([
      loadStatus(),
      loadSettings(),
      loadRecordings(),
      loadTopologies(),
    ]);
    for (const result of initial) {
      if (result.status === "rejected") showError(result.reason);
    }
    await changeView(
      titles[location.hash.slice(1)] ? location.hash.slice(1) : "empfang",
    );
    drawSpectrum();
    drawWaterfall();
    setInterval(poll, 1800);
  }
  init().catch(showError);
})();

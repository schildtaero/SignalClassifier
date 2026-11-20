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
    topologySelectionInitialized: false,
    comparisonInspection: null,
    comparisonRankingMode: "score",
    checkedLiveModels: new Set(),
    liveComparisonSelection: { model_ids: [], models: [], active: false },
    liveSelectionLoaded: false,
    spectrum: null,
    waterfall: [],
    map: null,
    mapMarkers: null,
    mapTiles: null,
    mapGrid: null,
    mapFitted: false,
    polling: false,
    experimentPolling: false,
    selectedRecordings: new Set(),
    datasetReadiness: null,
    readinessSequence: 0,
    wasReceiving: false,
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
  let readinessTimer;
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
  function stopReasonText(reason) {
    return (
      {
        storage_limit: "Dateibudget erreicht · Aufnahme beendet",
        duration_limit: "Eingestellte Betriebsdauer erreicht",
        end_of_recording: "Recording vollständig wiedergegeben",
        user_stop: "Empfang durch Benutzer gestoppt",
        receiver_error: "Empfängerfehler",
        writer_error: "Schreibfehler",
      }[reason] || (reason ? `Empfang beendet: ${reason}` : "")
    );
  }
  function captureBudgetEstimate(sampleRate, seconds, budgetBytes) {
    if (
      ![sampleRate, seconds, budgetBytes].every(
        (v) => Number.isFinite(v) && v > 0,
      )
    )
      return null;
    const bytesPerSecond = sampleRate * 8;
    return {
      bytesPerSecond,
      requestedBytes: bytesPerSecond * seconds,
      budgetSeconds: budgetBytes / bytesPerSecond,
    };
  }
  function updateCaptureSizeEstimate() {
    const form = $("#receiver-form"),
      mode = form.elements.mode.value,
      holder = $("#capture-size-estimate");
    holder.hidden = mode === "monitor";
    if (holder.hidden) return;
    let rate = Number(form.elements.sample_rate_msps.value) * 1e6;
    if (form.elements.source.value === "recording") {
      const recording = state.recordings.find(
        (r) => r.id === form.elements.recording_id.value,
      );
      rate = recording ? Number(recordingMeta(recording).sample_rate) : NaN;
    }
    const seconds = Number(form.elements.duration_seconds.value),
      budget = Number(form.elements.max_mib.value) * 1048576,
      estimate = captureBudgetEstimate(rate, seconds, budget);
    if (!estimate) {
      holder.textContent =
        "Für die Größenabschätzung Abtastrate, Betriebsdauer und Dateibudget angeben.";
      return;
    }
    const reference = `${num(rate / 1e6, 3)} MS/s entsprechen bei cf32_le ${num(estimate.bytesPerSecond / 1e6, 2)} MB/s`;
    holder.textContent =
      mode === "energy"
        ? `Signalaufnahme: Die Datenmenge hängt von Kanalzahl und Kanalbandbreiten ab. Weitbandreferenz: ${reference}; dies ist keine Größenprognose für die Signalaufnahme. Dateibudget ${bytes(budget)}.`
        : `${reference}. ${num(seconds, 1)} s benötigen etwa ${num(estimate.requestedBytes / 1e9, 2)} GB IQ (${bytes(estimate.requestedBytes)}). Das Dateibudget von ${bytes(budget)} reicht für etwa ${num(estimate.budgetSeconds, 1)} s. Betriebsdauer oder Dateibudget beenden die Aufnahme, je nachdem welcher Grenzwert zuerst erreicht wird.`;
    holder.classList.toggle(
      "budget-short",
      mode === "wideband" && estimate.budgetSeconds < seconds,
    );
  }
  function renderStatus() {
    const status = state.status;
    const rx = status.receiver || {};
    const config = rx.config || rx;
    const running = receiverRunning(rx);
    const hasError =
      !!rx.error || rx.status === "error" || rx.state === "error";
    if (hasError)
      resetReceiverDisplay("Empfangsfehler · keine aktuelle Entscheidung");
    $("#connection-dot").className = "status-dot online";
    $("#connection-state").textContent = running
      ? config.source === "synthetic"
        ? "Synthetische Quelle aktiv"
        : "Empfang aktiv"
      : "Lokales Backend verbunden";
    $("#rx-source-label").textContent = running
      ? sourceLabel(config.source)
      : rx.reason
        ? `${sourceLabel(config.source)} · beendet`
        : "Kein Empfang aktiv";
    $("#rx-detail").textContent = hasError
      ? text(rx.error)
      : running
        ? `${text(config.mode, "monitor") === "energy" ? "Signalaufnahme" : config.mode === "wideband" ? "Weitbandaufnahme" : "Beobachtung"} · ${mhz(config.center_frequency_hz ?? rx.center_frequency_hz)}`
        : stopReasonText(rx.reason) ||
          "Live, Recording oder synthetische Quelle wählen";
    $("#receiver-state").textContent = hasError
      ? "Fehler"
      : running
        ? "Aktiv"
        : rx.reason === "storage_limit"
          ? "Budget-Stopp"
          : rx.reason === "duration_limit"
            ? "Zeitlimit erreicht"
            : rx.reason === "end_of_recording"
              ? "Replay beendet"
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
    renderCapturePerformance(rx);
    renderLiveComparison(state.spectrum);
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
  function renderCapturePerformance(receiver) {
    const p = receiver.performance || {};
    const rate = p.actual_sample_rate ?? receiver.sample_rate;
    $("#capture-throughput").textContent = rate
      ? `${num(rate / 1e6, 3)} MS/s · IQ-Takt`
      : "—";
    $("#capture-queue").textContent =
      p.queue_blocks !== undefined
        ? `Puffer ${num(p.queue_blocks, 0)} / ${num(p.queue_capacity_blocks, 0)} Blöcke · ${bytes(p.queue_bytes)} / ${bytes(p.queue_capacity_bytes)}`
        : "Warte auf Stream-Telemetrie";
    const ratio = p.processing_rtf;
    $("#capture-budget").textContent =
      ratio === undefined || ratio === null
        ? "—"
        : `${num(ratio * 100, 1)} % der IQ-Zeit`;
    $("#capture-stage").textContent =
      p.processing_ms !== undefined
        ? `Lesen ${num(p.reader_ms, 1)} ms · Verarbeitung ${num(p.processing_ms, 1)} ms · davon Schreiben ${num(p.writer_ms, 1)} ms`
        : "Noch keine Verarbeitung gemessen";
    const gaps = p.gaps,
      overflows = p.overflows;
    $("#capture-gaps").textContent =
      gaps === undefined || gaps === null
        ? "Nicht verifiziert"
        : `${num(gaps, 0)} gemeldete Lücken`;
    $("#capture-gap-detail").textContent =
      overflows === undefined
        ? "Treiber- und Warteschlangentelemetrie werden getrennt ausgewiesen."
        : `${num(overflows, 0)} Pufferüberläufe · HF-/USB-Lückenfreiheit nicht verifiziert`;
    const warnings = [];
    if (p.capture_error) warnings.push(text(p.capture_error));
    if (Number(overflows) > 0)
      warnings.push(
        "IQ-Pufferüberlauf gemeldet. Die betroffene Aufnahme auf Datenlücken prüfen.",
      );
    if (Number(gaps) > 0)
      warnings.push("Der Empfänger meldet Unterbrechungen im IQ-Strom.");
    if (Number(ratio) > 1)
      warnings.push(
        "Die Verarbeitung benötigt mehr Zeit als die IQ-Blöcke enthalten. Pufferfüllstand und Durchsatz beobachten.",
      );
    const alert = $("#capture-quality-warning");
    alert.hidden = warnings.length === 0;
    alert.textContent = warnings.join(" ");
  }
  function trainingOnly(value) {
    return (
      value?.evaluation_available === false ||
      value?.evaluation_mode === "training_only" ||
      value?.config?.evaluation_mode === "training_only" ||
      value?.validation_status === "unvalidated"
    );
  }
  function resetReceiverDisplay(message = "Warte auf neue IQ-Daten") {
    state.spectrum = null;
    state.waterfall = [];
    state.waterfallSourceKey = null;
    state.waterfallGeometry = null;
    $("#waterfall-readout").textContent =
      "Frequenz und Messwert mit dem Zeiger ablesen.";
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
    renderLiveComparison(null);
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
    updateCaptureSizeEstimate();
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
      max_channels: Number(f.get("max_channels")),
      queue_capacity_bytes: Number(f.get("queue_mib")) * 1048576,
      spectrum_refresh_hz: Number(f.get("spectrum_refresh_hz")),
      detection_interval_seconds: Number(f.get("detection_interval_ms")) / 1000,
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
    drawSpectrumDetections(ctx, { left, top, width: pw, height: ph });
  }
  const WATERFALL_PALETTES = {
    viridis: [
      [68, 1, 84],
      [72, 40, 120],
      [62, 73, 137],
      [49, 104, 142],
      [38, 130, 142],
      [31, 158, 137],
      [53, 183, 121],
      [110, 206, 88],
      [181, 222, 43],
      [253, 231, 37],
    ],
    cividis: [
      [0, 34, 78],
      [0, 53, 111],
      [40, 69, 111],
      [72, 85, 109],
      [99, 102, 111],
      [124, 123, 120],
      [151, 145, 119],
      [181, 169, 110],
      [213, 195, 86],
      [253, 231, 55],
    ],
    inferno: [
      [0, 0, 4],
      [26, 12, 65],
      [74, 12, 107],
      [120, 28, 109],
      [165, 44, 96],
      [207, 68, 70],
      [237, 105, 37],
      [251, 155, 6],
      [247, 209, 61],
      [252, 255, 164],
    ],
  };
  function paletteRGB(name, value) {
    const anchors = WATERFALL_PALETTES[name] || WATERFALL_PALETTES.viridis;
    const scaled =
      Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) *
      (anchors.length - 1);
    const low = Math.min(anchors.length - 2, Math.floor(scaled)),
      fraction = scaled - low;
    return anchors[low].map((v, c) =>
      Math.round(v + (anchors[low + 1][c] - v) * fraction),
    );
  }
  function averagePowerBins(power, maximum = 512) {
    const count = Math.min(maximum, power.length),
      output = [];
    for (let i = 0; i < count; i++) {
      const begin = Math.floor((i * power.length) / count),
        end = Math.max(begin + 1, Math.floor(((i + 1) * power.length) / count));
      let linear = 0,
        valid = 0;
      for (let j = begin; j < end; j++)
        if (power[j] !== null && Number.isFinite(Number(power[j]))) {
          linear += 10 ** (Math.max(-200, Math.min(60, Number(power[j]))) / 10);
          valid++;
        }
      output.push(
        valid ? 10 * Math.log10(Math.max(1e-20, linear / valid)) : null,
      );
    }
    return output;
  }
  function finiteNumber(value) {
    return value !== null &&
      value !== undefined &&
      value !== "" &&
      Number.isFinite(Number(value))
      ? Number(value)
      : null;
  }
  function detectionBounds(detection) {
    let center = finiteNumber(
      detection.center_frequency_hz ??
        detection.frequency_hz ??
        detection.center_hz,
    );
    const bandwidth = finiteNumber(
      detection.bandwidth_hz ?? detection.bandwidth,
    );
    let low = finiteNumber(detection.frequency_min_hz),
      high = finiteNumber(detection.frequency_max_hz);
    if (low === null || high === null) {
      if (center === null || bandwidth === null || bandwidth <= 0) return null;
      low = center - bandwidth / 2;
      high = center + bandwidth / 2;
    }
    if (high <= low) return null;
    if (center === null) center = (low + high) / 2;
    return {
      low,
      high,
      center,
      bandwidth: bandwidth > 0 ? bandwidth : high - low,
    };
  }
  function associatedDetectionIndex(detections, classification) {
    const label = String(classification?.label || "")
      .trim()
      .toLocaleLowerCase("de");
    if (
      !label ||
      [
        "unknown",
        "unbekannt",
        "no_signal",
        "noise",
        "kein signal",
        "kein_signal",
      ].includes(label)
    )
      return -1;
    if (
      ["feature_rejection_gate", "insufficient_segment"].includes(
        classification.confidence_kind,
      )
    )
      return -1;
    const center = finiteNumber(classification.center_frequency_hz),
      width = finiteNumber(
        classification.bandwidth_hz ?? classification.sample_rate,
      );
    if (center === null || width === null || width <= 0) return -1;
    let chosen = -1,
      distance = Infinity;
    detections.forEach((raw, i) => {
      const d = detectionBounds(raw);
      if (!d) return;
      const overlap =
        Math.min(d.high, center + width / 2) -
        Math.max(d.low, center - width / 2);
      const delta = Math.abs(d.center - center);
      if (overlap > 0 && delta < distance) {
        chosen = i;
        distance = delta;
      }
    });
    return chosen;
  }
  function classifiedDetections(detections, classification) {
    const index = associatedDetectionIndex(detections, classification);
    return arr(detections).map((d, i) => ({
      ...d,
      detected_modulation:
        i === index ? String(classification.label) : "unbekannt",
      modulation_evaluated: i === index,
    }));
  }
  function detectionTone(detection) {
    const label = String(detection.detected_modulation || "").toUpperCase();
    return label === "AM"
      ? "am"
      : ["FM", "NFM", "WFM"].includes(label)
        ? "fm"
        : "unknown";
  }
  function detectionColor(detection) {
    const tone = detectionTone(detection);
    return (
      css(`--det-${tone}`) ||
      { am: "#ffbf69", fm: "#67e8f9", unknown: "#c5cfda" }[tone]
    );
  }
  function frequencyBox(detection, minimum, maximum, left, width) {
    const bounds = detectionBounds(detection);
    if (
      !bounds ||
      !Number.isFinite(minimum) ||
      !Number.isFinite(maximum) ||
      maximum <= minimum ||
      width <= 0
    )
      return null;
    const low = Math.max(minimum, bounds.low),
      high = Math.min(maximum, bounds.high);
    if (high <= low) return null;
    const x = left + ((low - minimum) / (maximum - minimum)) * width;
    return {
      x,
      width: Math.min(
        left + width - x,
        Math.max(1, ((high - low) / (maximum - minimum)) * width),
      ),
      center:
        left +
        Math.max(
          0,
          Math.min(1, (bounds.center - minimum) / (maximum - minimum)),
        ) *
          width,
    };
  }
  function frameFromSnapshot(snapshot) {
    const timeReference =
      snapshot.analysis_timestamp || snapshot.timestamp || snapshot.captured_at;
    const timestamp = Date.parse(timeReference || "");
    const frequencies = arr(snapshot.frequencies_hz || snapshot.frequencies),
      power = arr(
        snapshot.power_dbfs || snapshot.spectrum_dbfs || snapshot.spectrum,
      );
    if (!Number.isFinite(timestamp) || frequencies.length < 2 || !power.length)
      return null;
    const step = Number(frequencies[1]) - Number(frequencies[0]);
    const minimum = Number(frequencies[0]) - step / 2,
      maximum = Number(frequencies[frequencies.length - 1]) + step / 2;
    if (
      !Number.isFinite(minimum) ||
      !Number.isFinite(maximum) ||
      maximum <= minimum
    )
      return null;
    const rate = finiteNumber(
      snapshot.sample_rate ?? snapshot.receiver?.sample_rate,
    );
    const center =
      finiteNumber(
        snapshot.center_frequency_hz ?? snapshot.receiver?.center_frequency_hz,
      ) ?? (minimum + maximum) / 2;
    const source = snapshot.source || snapshot.receiver?.source || "unknown";
    const session =
      snapshot.receiver?.session_id ||
      snapshot.session_id ||
      snapshot.receiver?.config?.recording_id ||
      "";
    const key = JSON.stringify([
      source,
      session,
      center,
      rate,
      minimum,
      maximum,
    ]);
    const analysisSamples = finiteNumber(snapshot.analysis_sample_count);
    const blockSamples = finiteNumber(snapshot.block_sample_count);
    const analysisDuration =
      finiteNumber(snapshot.analysis_duration_seconds) ??
      (rate > 0 && analysisSamples > 0 ? analysisSamples / rate : null);
    const knownDuration =
      analysisDuration ??
      finiteNumber(snapshot.block_duration_seconds) ??
      (rate > 0 && blockSamples > 0 ? blockSamples / rate : 0);
    return {
      id: String(timeReference),
      key,
      timeMs: timestamp,
      duration: Math.max(0, knownDuration),
      timestampVerified: snapshot.rf_timestamp_verified === true,
      timestampSource: snapshot.timestamp_source || "host_timestamp",
      minimum,
      maximum,
      rate,
      center,
      power: averagePowerBins(power),
      detections: classifiedDetections(
        arr(snapshot.detections),
        snapshot.classification,
      ),
    };
  }
  function addWaterfall(snapshot) {
    const frame = frameFromSnapshot(snapshot);
    if (!frame) return false;
    if (state.waterfallSourceKey !== frame.key) {
      state.waterfall = [];
      state.waterfallSourceKey = frame.key;
    }
    const previous = state.waterfall.find((row) => row.id === frame.id);
    if (previous) {
      previous.detections = frame.detections;
      return false;
    }
    state.waterfall.push(frame);
    state.waterfall.sort((a, b) => b.timeMs - a.timeMs);
    state.waterfall.length = Math.min(240, state.waterfall.length);
    return true;
  }
  function waterfallOptions() {
    let minimum = finiteNumber($("#waterfall-min").value) ?? -120,
      maximum = finiteNumber($("#waterfall-max").value) ?? -20;
    if (maximum <= minimum) maximum = minimum + 5;
    return {
      palette: $("#waterfall-palette").value,
      minimum,
      maximum,
      windowSeconds: Number($("#waterfall-window").value) || 180,
      overlay: $("#detection-overlay-toggle").checked,
    };
  }
  function waterfallGeometry(frame, rows, width, height, options) {
    const left = 65,
      right = 18,
      plotWidth = Math.max(20, width - left - right);
    const latest = arr(frame?.detections)
      .slice()
      .sort(
        (a, b) => (Number(b.peak_dbfs) || -200) - (Number(a.peak_dbfs) || -200),
      )
      .slice(0, 4)
      .sort(
        (a, b) =>
          (detectionBounds(a)?.center || 0) - (detectionBounds(b)?.center || 0),
      );
    const columns = Math.max(
        1,
        Math.min(latest.length || 1, Math.floor((plotWidth + 8) / 185)),
      ),
      badgeRows =
        options.overlay && latest.length
          ? Math.ceil(latest.length / columns)
          : 0;
    const top = badgeRows ? badgeRows * 39 + 23 : 26,
      bottom = 34,
      plotHeight = Math.max(30, height - top - bottom);
    const badgeWidth = (plotWidth - 8 * (columns - 1)) / columns;
    const badges = options.overlay
      ? latest.map((d, i) => ({
          detection: d,
          x: left + (i % columns) * (badgeWidth + 8),
          y: 8 + Math.floor(i / columns) * 39,
          width: badgeWidth,
          height: 33,
        }))
      : [];
    return {
      left,
      right,
      top,
      bottom,
      plotWidth,
      plotHeight,
      badges,
      latestTime: frame?.timeMs ?? 0,
      windowMs: options.windowSeconds * 1000,
    };
  }
  function observationCell(frame, geometry) {
    const age = geometry.latestTime - frame.timeMs;
    if (age < 0 || age > geometry.windowMs) return null;
    const y = geometry.top + (age / geometry.windowMs) * geometry.plotHeight;
    const height = Math.min(
      geometry.top + geometry.plotHeight - y,
      Math.max(
        1,
        ((frame.duration * 1000) / geometry.windowMs) * geometry.plotHeight,
      ),
    );
    return height > 0 ? { y, height } : null;
  }
  function observationTrack(detection, rows, geometry) {
    const target = detectionBounds(detection);
    if (!target) return [];
    const observations = [];
    let previous = rows[0]?.timeMs;
    for (const row of rows) {
      if (
        geometry.latestTime - row.timeMs > 60000 ||
        previous - row.timeMs > 6000
      )
        break;
      const match = row.detections
        .map((d) => ({ d, b: detectionBounds(d) }))
        .filter(
          (x) =>
            x.b &&
            Math.min(x.b.high, target.high) > Math.max(x.b.low, target.low),
        )
        .sort(
          (a, b) =>
            Math.abs(a.b.center - target.center) -
            Math.abs(b.b.center - target.center),
        )[0];
      if (!match) break;
      const cell = observationCell(row, geometry);
      if (cell) observations.push({ frame: row, detection: match.d, cell });
      previous = row.timeMs;
    }
    return observations;
  }
  function rowStrip(row, options) {
    const key = `${options.palette}|${options.minimum}|${options.maximum}`;
    if (row.strip && row.stripKey === key) return row.strip;
    const strip = document.createElement("canvas");
    strip.width = row.power.length;
    strip.height = 1;
    const ctx = strip.getContext("2d"),
      pixels = ctx.createImageData(row.power.length, 1);
    row.power.forEach((p, i) => {
      if (p === null) return;
      const color = paletteRGB(
        options.palette,
        (p - options.minimum) / (options.maximum - options.minimum),
      );
      pixels.data.set([...color, 255], i * 4);
    });
    ctx.putImageData(pixels, 0, 0);
    row.strip = strip;
    row.stripKey = key;
    return strip;
  }
  function drawWaterfallScale(options) {
    const canvas = $("#waterfall-scale-canvas"),
      { ctx, width, height } = canvasSetup(canvas);
    for (let x = 0; x < width; x++) {
      const color = paletteRGB(options.palette, x / Math.max(1, width - 1));
      ctx.fillStyle = `rgb(${color.join(",")})`;
      ctx.fillRect(x, 0, 1, height);
    }
    $("#waterfall-scale-low").textContent = `${num(options.minimum, 0)} dBFS`;
    $("#waterfall-scale-high").textContent = `${num(options.maximum, 0)} dBFS`;
  }
  function drawWaterfall() {
    const canvas = $("#waterfall-canvas");
    if (!canvas || state.view !== "empfang" || !$("#waterfall-toggle").checked)
      return;
    const { ctx, width: w, height: h } = canvasSetup(canvas),
      options = waterfallOptions(),
      rows = state.waterfall,
      frame = rows[0],
      geometry = waterfallGeometry(frame, rows, w, h, options);
    state.waterfallGeometry = geometry;
    ctx.fillStyle = css("--plot");
    ctx.fillRect(0, 0, w, h);
    ctx.font = "11px Consolas,monospace";
    ctx.fillStyle = css("--muted");
    ctx.textAlign = "left";
    const visible = rows.filter(
      (row) => geometry.latestTime - row.timeMs <= geometry.windowMs,
    );
    $("#waterfall-frame-count").textContent =
      `${visible.length} / ${rows.length} Stichproben`;
    drawWaterfallScale(options);
    ctx.save();
    ctx.beginPath();
    ctx.rect(
      geometry.left,
      geometry.top,
      geometry.plotWidth,
      geometry.plotHeight,
    );
    ctx.clip();
    ctx.imageSmoothingEnabled = false;
    for (const row of visible) {
      const cell = observationCell(row, geometry);
      if (!cell) continue;
      ctx.drawImage(
        rowStrip(row, options),
        geometry.left,
        cell.y,
        geometry.plotWidth,
        cell.height,
      );
    }
    ctx.restore();
    ctx.strokeStyle = css("--grid");
    ctx.lineWidth = 0.7;
    ctx.fillStyle = css("--faint");
    for (let i = 0; i <= 4; i++) {
      const y = geometry.top + (geometry.plotHeight * i) / 4;
      ctx.beginPath();
      ctx.moveTo(geometry.left, y + 0.5);
      ctx.lineTo(w - geometry.right, y + 0.5);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(
        i ? `−${num((options.windowSeconds * i) / 4, 0)} s` : "0 s",
        geometry.left - 9,
        y + 4,
      );
    }
    const ticks = Math.max(2, Math.min(6, Math.floor(geometry.plotWidth / 95)));
    for (let i = 0; i <= ticks; i++) {
      const x = geometry.left + (geometry.plotWidth * i) / ticks;
      ctx.beginPath();
      ctx.moveTo(x + 0.5, geometry.top);
      ctx.lineTo(x + 0.5, geometry.top + geometry.plotHeight);
      ctx.stroke();
      if (frame) {
        ctx.textAlign = "center";
        ctx.fillText(
          num(
            (frame.minimum + ((frame.maximum - frame.minimum) * i) / ticks) /
              1e6,
            3,
          ),
          x,
          h - 12,
        );
      }
    }
    ctx.textAlign = "left";
    ctx.fillStyle = css("--muted");
    ctx.font = "10px Consolas,monospace";
    ctx.fillText(
      frame
        ? `${frame.timestampVerified ? "UTC" : "UTC ≈"} ${new Date(frame.timeMs).toISOString().slice(11, 19)} · Analyseende`
        : "Warte auf Spektrum mit Zeitstempel",
      geometry.left,
      geometry.top - 7,
    );
    if (!frame) {
      ctx.textAlign = "center";
      ctx.font = "13px system-ui,sans-serif";
      ctx.fillText(
        "Noch keine Spektrum-Stichproben",
        geometry.left + geometry.plotWidth / 2,
        geometry.top + geometry.plotHeight / 2,
      );
      return;
    }
    if (!options.overlay) return;
    ctx.save();
    ctx.beginPath();
    ctx.rect(
      geometry.left,
      geometry.top,
      geometry.plotWidth,
      geometry.plotHeight,
    );
    ctx.clip();
    for (const badge of geometry.badges) {
      const detection = badge.detection,
        color = detectionColor(detection),
        track = observationTrack(detection, visible, geometry),
        box = frequencyBox(
          detection,
          frame.minimum,
          frame.maximum,
          geometry.left,
          geometry.plotWidth,
        );
      if (!box) continue;
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      for (const observation of track) {
        const band = frequencyBox(
          observation.detection,
          frame.minimum,
          frame.maximum,
          geometry.left,
          geometry.plotWidth,
        );
        if (!band) continue;
        ctx.strokeStyle = detectionColor(observation.detection);
        ctx.setLineDash([]);
        ctx.strokeRect(
          band.x,
          observation.cell.y,
          Math.max(1, band.width),
          Math.max(1.5, observation.cell.height),
        );
      }
      if (track.length > 1) {
        const bottom =
          track[track.length - 1].cell.y + track[track.length - 1].cell.height;
        ctx.strokeStyle = css("--det-unknown");
        ctx.globalAlpha = 0.7;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(
          box.x,
          geometry.top + 0.5,
          box.width,
          Math.max(1, bottom - geometry.top),
        );
        ctx.globalAlpha = 1;
      }
      ctx.setLineDash([]);
    }
    ctx.restore();
    for (const badge of geometry.badges) {
      const d = badge.detection,
        bounds = detectionBounds(d),
        box = frequencyBox(
          d,
          frame.minimum,
          frame.maximum,
          geometry.left,
          geometry.plotWidth,
        );
      if (!bounds || !box) continue;
      const color = detectionColor(d);
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.7;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(badge.x + badge.width / 2, badge.y + badge.height);
      ctx.lineTo(box.center, geometry.top);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = css("--panel");
      ctx.fillRect(badge.x, badge.y, badge.width, badge.height);
      ctx.strokeStyle = color;
      ctx.strokeRect(
        badge.x + 0.5,
        badge.y + 0.5,
        badge.width - 1,
        badge.height - 1,
      );
      ctx.save();
      ctx.beginPath();
      ctx.rect(badge.x + 5, badge.y + 2, badge.width - 10, badge.height - 4);
      ctx.clip();
      ctx.font = "11px Consolas,monospace";
      ctx.textAlign = "left";
      ctx.fillStyle = color;
      ctx.fillText(
        `${text(d.detected_modulation, "unbekannt")} · ${num(bounds.center / 1e6, 4)} MHz`,
        badge.x + 7,
        badge.y + 13,
      );
      ctx.font = "10px Consolas,monospace";
      ctx.fillStyle = css("--muted");
      ctx.fillText(
        `BW ≈ ${num(bounds.bandwidth / 1e3, 1)} kHz · aktuell`,
        badge.x + 7,
        badge.y + 26,
      );
      ctx.restore();
    }
  }
  function drawSpectrumDetections(ctx, geometry) {
    if (!$("#detection-overlay-toggle").checked) return;
    const snapshot = state.spectrum || {},
      freq = arr(snapshot.frequencies_hz || snapshot.frequencies);
    if (freq.length < 2) return;
    const step = Number(freq[1]) - Number(freq[0]),
      minimum = Number(freq[0]) - step / 2,
      maximum = Number(freq[freq.length - 1]) + step / 2;
    ctx.save();
    ctx.beginPath();
    ctx.rect(geometry.left, geometry.top, geometry.width, geometry.height);
    ctx.clip();
    for (const d of classifiedDetections(
      arr(snapshot.detections),
      snapshot.classification,
    )) {
      const box = frequencyBox(
        d,
        minimum,
        maximum,
        geometry.left,
        geometry.width,
      );
      if (!box) continue;
      ctx.strokeStyle = detectionColor(d);
      ctx.fillStyle = detectionColor(d);
      ctx.globalAlpha = 0.055;
      ctx.fillRect(box.x, geometry.top, box.width, geometry.height);
      ctx.globalAlpha = 0.7;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.strokeRect(box.x, geometry.top, box.width, geometry.height);
    }
    ctx.restore();
  }
  function waterfallPointer(event) {
    const canvas = $("#waterfall-canvas"),
      geometry = state.waterfallGeometry,
      frame = state.waterfall[0];
    if (!geometry || !frame) return;
    const rect = canvas.getBoundingClientRect(),
      x = event.clientX - rect.left,
      y = event.clientY - rect.top;
    if (
      x < geometry.left ||
      x > geometry.left + geometry.plotWidth ||
      y < geometry.top ||
      y > geometry.top + geometry.plotHeight
    )
      return;
    const frequency =
        frame.minimum +
        ((x - geometry.left) / geometry.plotWidth) *
          (frame.maximum - frame.minimum),
      time =
        geometry.latestTime -
        ((y - geometry.top) / geometry.plotHeight) * geometry.windowMs;
    const nearest = state.waterfall.reduce(
      (best, row) =>
        !best || Math.abs(row.timeMs - time) < Math.abs(best.timeMs - time)
          ? row
          : best,
      null,
    );
    const cell = nearest ? observationCell(nearest, geometry) : null;
    if (!cell || Math.abs(y - cell.y) > Math.max(3, cell.height)) {
      $("#waterfall-readout").textContent =
        `${mhz(frequency)} · kein Spektrum an dieser Zeitposition erfasst`;
      return;
    }
    const index = Math.max(
        0,
        Math.min(
          nearest.power.length - 1,
          Math.floor(
            ((frequency - frame.minimum) / (frame.maximum - frame.minimum)) *
              nearest.power.length,
          ),
        ),
      ),
      power = nearest.power[index];
    $("#waterfall-readout").textContent =
      `${mhz(frequency)} · ${power === null ? "kein Pegel" : `${num(power, 1)} dBFS`} · ${new Date(nearest.timeMs).toISOString().slice(11, 23)} UTC · Messstichprobe`;
  }

  function classificationScore(result) {
    if (
      ["feature_rejection_gate", "insufficient_segment"].includes(
        result.confidence_kind,
      )
    )
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
  function renderDetections(detections, classification = null) {
    const rows = classifiedDetections(arr(detections), classification);
    $("#detection-count").textContent = String(rows.length);
    $("#detection-list").replaceChildren(
      ...(rows.length
        ? rows.map((d) => {
            const bounds = detectionBounds(d);
            return el(
              "div",
              { class: `detection-card detection-tone-${detectionTone(d)}` },
              [
                el("div", { class: "detection-card-heading" }, [
                  el("strong", { text: mhz(bounds?.center) }),
                  pill(
                    text(d.detected_modulation, "unbekannt"),
                    d.modulation_evaluated ? "cyan" : "",
                  ),
                ]),
                el("span", {
                  text: `Geschätzte Bandbreite ${num((bounds?.bandwidth ?? 0) / 1e3, 2)} kHz`,
                }),
                el("span", {
                  text: `${num(d.excess_db ?? d.peak_above_noise_db)} dB über Rauschschätzung · ${num(d.peak_dbfs, 1)} dBFS Spitze`,
                }),
                el("span", {
                  class: "detection-class-note",
                  text: d.modulation_evaluated
                    ? "Modulation für diesen Kanal ausgewertet · keine Emitterbestätigung"
                    : "Modulation dieses Kanals noch nicht ausgewertet",
                }),
              ],
            );
          })
        : [empty("Detektionen erscheinen während des Empfangs.")]),
    );
  }
  async function loadSpectrum() {
    const snapshot = await api("/api/spectrum");
    state.spectrum = snapshot;
    renderLiveComparison(snapshot);
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
    if (power.length) addWaterfall(snapshot);
    drawSpectrum();
    drawWaterfall();
    renderDetections(snapshot.detections, snapshot.classification);
    if (snapshot.classification) renderClassification(snapshot.classification);
  }
  async function loadLiveComparison() {
    const selection = await api("/api/live/compare");
    state.liveComparisonSelection = selection;
    if (!state.liveSelectionLoaded) {
      state.checkedLiveModels = new Set(arr(selection.model_ids));
      state.liveSelectionLoaded = true;
    }
    renderLiveComparison(state.spectrum);
    return selection;
  }
  async function configureLiveComparison(modelIds, showReception = false) {
    const ids = [...new Set(modelIds)];
    if (ids.length > 3)
      throw new Error("Höchstens drei Modelle für Live-Stichproben auswählen.");
    const selection = await api("/api/live/compare", {
      method: "POST",
      body: { model_ids: ids },
    });
    state.liveComparisonSelection = selection;
    state.checkedLiveModels = new Set(arr(selection.model_ids));
    state.liveSelectionLoaded = true;
    if (showReception) await changeView("empfang");
    if (receiverRunning() && ids.length) await loadSpectrum();
    else renderLiveComparison(null);
    if (!ids.length)
      toast(
        "Live-Vergleich beendet. Der Empfänger läuft unabhängig davon weiter.",
      );
    else
      toast(
        receiverRunning()
          ? "Modelle für die nächsten Live-Stichproben gewählt."
          : "Modelle gewählt. Zum Empfang eine Quelle auswählen und selbst starten.",
      );
  }
  function liveSampleMetrics(item) {
    const rolling = item.rolling || {};
    const observations = Number(rolling.observations) || 0;
    return {
      observations,
      p50: observations > 0 ? rolling.latency_ms_p50 : null,
      p95: observations > 0 ? (rolling.latency_ms_p95 ?? rolling.p95) : null,
      rtf: observations > 0 ? rolling.real_time_factor_p95 : null,
      warmup: rolling.warmup === true || item.warmup === true,
    };
  }
  function renderLiveComparison(snapshot) {
    const holder = $("#live-comparison-cards");
    if (!holder) return;
    const selection = state.liveComparisonSelection || {};
    const ids = arr(selection.model_ids);
    $("#live-comparison-count").textContent = `${ids.length} / 3 Modelle`;
    $("#live-comparison-stop").disabled = ids.length === 0;
    const samples = arr(snapshot?.classification_comparison);
    const byId = new Map(samples.map((item) => [item.model_id, item]));
    const selected = new Map(
      arr(selection.models).map((model) => [model.id, model]),
    );
    const running = receiverRunning();
    $("#live-comparison-status").textContent = ids.length
      ? running
        ? "Neueste empfangene IQ-Stichproben werden verglichen."
        : "Empfang gestoppt. Angezeigte Ergebnisse gehören zur letzten verfügbaren Stichprobe."
      : "Im Experiment bis zu drei trainierte Modelle auswählen oder die Top-3-Empfehlung übernehmen.";
    const cpu = state.status.hardware?.cpu || state.status.hardware?.processor;
    $("#live-comparison-hardware").textContent =
      `Aktuelle Inferenz: CPU${cpu ? ` · ${cpu}` : ""}. P50/P95: bis zu 30 vollständige Pipeline-Inferenzen nach 3 Aufwärmläufen. Keine Genauigkeit ohne Referenzlabel. Unterschiedliche Modelle können unterschiedliche Ausschnittlängen aus demselben IQ-Ring verwenden; dies ist keine lückenlose Echtzeitauswertung.`;
    holder.replaceChildren(
      ...(ids.length
        ? ids.map((id) => {
            const item = byId.get(id) || {};
            const model = selected.get(id) || {};
            const prediction = item.classification;
            const values = liveSampleMetrics(item);
            const hasResult = prediction && typeof prediction === "object";
            const sampleRate = Number(item.sample_rate),
              length = Number(item.model_segment_length);
            const card = el("article", { class: "live-model-card" }, [
              el("div", { class: "live-model-heading" }, [
                el("h3", { text: item.name || model.name || id }),
                model.evaluation_available === false
                  ? pill("Unbewertet", "amber")
                  : null,
              ]),
              el("div", { class: "live-model-decision" }, [
                el("strong", {
                  text: hasResult ? text(prediction.label) : "—",
                }),
                el("span", {
                  text: item.error
                    ? "Keine gültige Inferenz"
                    : hasResult
                      ? classificationScore(prediction)
                      : "Warte auf passende IQ-Daten",
                }),
              ]),
              item.error
                ? el("p", { class: "live-model-error", text: text(item.error) })
                : null,
              el("div", { class: "live-model-metrics" }, [
                el("div", {}, [
                  el("span", { class: "metric-label", text: "AKTUELL" }),
                  el("strong", { text: `${num(item.elapsed_ms, 3)} ms` }),
                ]),
                el("div", {}, [
                  el("span", { class: "metric-label", text: "P50 / P95" }),
                  el("strong", {
                    text: values.observations
                      ? `${num(values.p50, 3)} / ${num(values.p95, 3)} ms`
                      : "—",
                  }),
                ]),
                el("div", {}, [
                  el("span", {
                    class: "metric-label",
                    text: "P95-ECHTZEITFAKTOR",
                  }),
                  el("strong", { text: num(values.rtf, 4) }),
                ]),
                el("div", {}, [
                  el("span", { class: "metric-label", text: "MESSUNGEN" }),
                  el("strong", {
                    text: values.observations
                      ? String(values.observations)
                      : values.warmup
                        ? "Aufwärmen"
                        : "0",
                  }),
                ]),
              ]),
              el("p", {
                class: "field-help",
                text:
                  Number.isFinite(length) && length > 0
                    ? `${num(length, 0)} Samples${Number.isFinite(sampleRate) && sampleRate > 0 ? ` · ${num((length / sampleRate) * 1000, 3)} ms IQ bei ${num(sampleRate / 1e3, 3)} kS/s` : ""}`
                    : "Die Eingangssegmentlänge erscheint mit der ersten Inferenz.",
              }),
            ]);
            return card;
          })
        : [empty("Noch keine Modelle für Live-Stichproben ausgewählt.")]),
    );
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
      suggestion: r.label_suggestion || null,
      suggestions: arr(r.label_suggestions),
      suggestion_reason:
        r.label_suggestion_reason || r.label_suggestion?.reason,
      group_id: r.group_id,
      capture_quality:
        r.capture_quality || r.metadata?.global?.["sc:capture_quality"],
    };
  }
  async function loadRecordings() {
    state.recordings = itemArray(await api("/api/recordings"), "recordings");
    renderRecordings();
  }
  function visibleRecordings() {
    const filter = $("#recording-filter").value;
    return state.recordings
      .map(recordingMeta)
      .filter((r) =>
        filter === "unconfirmed"
          ? !r.verified
          : filter === "confirmed"
            ? !!r.verified
            : true,
      );
  }
  const MAX_BATCH_RECORDINGS = 1000;
  function recordingSelectionWithAdditions(
    current,
    additions,
    maximum = MAX_BATCH_RECORDINGS,
  ) {
    const selected = new Set([...current, ...additions]);
    return selected.size <= maximum ? selected : null;
  }
  function addRecordingSelection(ids) {
    const selected = recordingSelectionWithAdditions(
      state.selectedRecordings,
      ids,
    );
    if (!selected) {
      showError(
        new Error(
          `Eine gemeinsame Bestätigung ist auf ${MAX_BATCH_RECORDINGS} Aufnahmen begrenzt. Die Auswahl wurde nicht erweitert; bitte weniger Aufnahmen auswählen.`,
        ),
      );
      updateRecordingSelection();
      return false;
    }
    state.selectedRecordings = selected;
    updateRecordingSelection();
    return true;
  }
  function updateRecordingSelection() {
    const visible = visibleRecordings();
    const selectedVisible = visible.filter((r) =>
      state.selectedRecordings.has(r.id),
    ).length;
    $("#recording-select-all").checked =
      visible.length > 0 && selectedVisible === visible.length;
    $("#recording-select-all").indeterminate =
      selectedVisible > 0 && selectedVisible < visible.length;
    $("#recording-select-all").disabled = visible.length === 0;
    $("#recording-selection-count").textContent =
      `${state.selectedRecordings.size} / ${MAX_BATCH_RECORDINGS} ausgewählt`;
    $("#recording-batch-open").disabled =
      state.selectedRecordings.size === 0 ||
      state.selectedRecordings.size > MAX_BATCH_RECORDINGS;
    $$("#recordings-table input[data-recording-select]").forEach((input) => {
      input.checked = state.selectedRecordings.has(input.value);
      input.disabled =
        !input.checked && state.selectedRecordings.size >= MAX_BATCH_RECORDINGS;
    });
  }
  function suggestionLabel(recording) {
    const suggestion = recording.suggestion;
    return suggestion && !suggestion.ambiguous && suggestion.label
      ? String(suggestion.label)
      : "";
  }
  function openRecordingLabel(recording) {
    const form = $("#label-form");
    form.elements.recording_id.value = recording.id;
    form.elements.label.value = recording.label || suggestionLabel(recording);
    form.elements.notes.value = recording.notes || "";
    form.elements.verified.checked = !!recording.verified;
    const notice = $("#label-suggestion-notice");
    notice.hidden = !recording.suggestion && !recording.suggestion_reason;
    if (!notice.hidden)
      notice.textContent = `${suggestionLabel(recording) ? `Vorausgewählt: ${suggestionLabel(recording)}. ` : "Kein eindeutiges Vorlabel. "}${text(recording.suggestion_reason || recording.suggestion?.source, "Frequenzvorwissen")} Vorschlag ist unbestätigt.`;
    $("#label-dialog-title").textContent = recording.name;
    openDialog("label-dialog");
  }
  function renderRecordings() {
    const records = state.recordings.map(recordingMeta);
    const known = new Set(records.map((r) => r.id));
    state.selectedRecordings = new Set(
      [...state.selectedRecordings].filter((id) => known.has(id)),
    );
    const visible = visibleRecordings();
    $("#recording-count").textContent =
      `${visible.length} / ${records.length} Aufnahmen`;
    $("#recordings-empty").hidden = records.length > 0;
    $("#recordings-table").replaceChildren(
      ...visible.map((r) => {
        const suggested = suggestionLabel(r);
        const label = r.label
          ? pill(
              `${r.label}${r.verified ? " · bestätigt" : " · vorläufig"}`,
              r.verified ? "green" : "amber",
            )
          : suggested
            ? pill(`${suggested} · Frequenzvorschlag`, "amber")
            : pill(
                r.suggestion?.ambiguous
                  ? "Mehrdeutiger Vorschlag"
                  : "Ohne Label",
              );
        const checkbox = el("input", {
          type: "checkbox",
          value: r.id,
          checked: state.selectedRecordings.has(r.id),
          "data-recording-select": "",
          "aria-label": `Aufnahme ${r.name} auswählen`,
        });
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) addRecordingSelection([r.id]);
          else {
            state.selectedRecordings.delete(r.id);
            updateRecordingSelection();
          }
        });
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
          button("Label", () => openRecordingLabel(r)),
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
          el("td", { class: "selection-cell" }, [checkbox]),
          detailCell(r.name, sourceLabel(r.source)),
          el("td", { class: "mono", text: mhz(r.center_frequency_hz) }),
          detailCell(
            `${num(Number(r.sample_rate) / 1e6, 3)} MS/s`,
            duration(r.duration_seconds),
            "mono",
          ),
          el("td", {}, [
            label,
            !r.verified && (r.suggestion_reason || r.suggestion?.source)
              ? el("small", {
                  text: text(r.suggestion_reason || r.suggestion.source),
                })
              : null,
          ]),
          el("td", {}, [actions]),
        ]);
      }),
    );
    updateRecordingSelection();
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
    const verified = records.filter((r) => r.verified && r.label);
    $("#experiment-recordings").replaceChildren(
      ...(verified.length
        ? verified.map((r) =>
            el("label", { class: "checkbox" }, [
              el("input", {
                type: "checkbox",
                value: r.id,
                checked: selected.has(r.id),
              }),
              el("span", {}, [
                text(r.name),
                el("small", {
                  text: `${r.label} · ${duration(r.duration_seconds)} · Sitzung ${text(r.group_id, r.id).slice(0, 12)}`,
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
    scheduleReadiness();
    updateCaptureSizeEstimate();
  }
  function batchLabelPreview() {
    const form = $("#batch-label-form");
    const useSuggestions = form.elements.batch_mode.value === "suggestions";
    const sharedLabel = form.elements.label.value.trim();
    $("#batch-label-field").hidden = useSuggestions;
    form.elements.label.required = !useSuggestions;
    const records = state.recordings
      .map(recordingMeta)
      .filter((r) => state.selectedRecordings.has(r.id));
    const preview = records.map((r) => {
      const label = useSuggestions ? suggestionLabel(r) : sharedLabel;
      const reason = r.verified
        ? "Bestätigtes Label bleibt erhalten"
        : !label
          ? useSuggestions
            ? "Kein eindeutiger Vorschlag; wird übersprungen"
            : "Gemeinsames Label fehlt"
          : useSuggestions
            ? text(
                r.suggestion_reason || r.suggestion?.source,
                "Frequenzvorschlag",
              )
            : "Gemeinsames Label aus deiner Auswahl";
      return { recording: r, label, reason, update: !r.verified && !!label };
    });
    const updated = preview.filter((r) => r.update);
    const summary = new Map();
    updated.forEach((r) =>
      summary.set(r.label, (summary.get(r.label) || 0) + 1),
    );
    $("#batch-label-summary").textContent =
      `${updated.length} von ${records.length} Aufnahmen werden bestätigt${summary.size ? `: ${[...summary].map(([label, count]) => `${count} × ${label}`).join(", ")}` : ""}. ${records.length - updated.length} werden übersprungen.`;
    $("#batch-label-submit").disabled = updated.length === 0;
    $("#batch-label-submit").textContent =
      `${updated.length} Labels bestätigen`;
    $("#batch-label-preview").replaceChildren(
      ...preview.map((row) =>
        el("tr", {}, [
          detailCell(
            row.recording.name,
            mhz(row.recording.center_frequency_hz),
          ),
          el("td", {}, [
            pill(
              row.recording.label || "Ohne Label",
              row.recording.verified ? "green" : "",
            ),
          ]),
          el("td", {}, [
            pill(
              row.update ? row.label : "Überspringen",
              row.update ? "cyan" : "amber",
            ),
          ]),
          el("td", { text: row.reason }),
        ]),
      ),
    );
    return {
      recording_ids: records.map((r) => r.id),
      use_suggestions: useSuggestions,
      label: useSuggestions ? undefined : sharedLabel,
      verified: true,
      overwrite_confirmed: false,
      notes: form.elements.notes.value,
      expected_labels: useSuggestions
        ? Object.fromEntries(
            updated.map((row) => [row.recording.id, row.label]),
          )
        : undefined,
    };
  }
  function openBatchLabels() {
    if (state.selectedRecordings.size > MAX_BATCH_RECORDINGS) {
      showError(
        new Error(
          `Bitte höchstens ${MAX_BATCH_RECORDINGS} Aufnahmen gemeinsam bestätigen.`,
        ),
      );
      return;
    }
    const form = $("#batch-label-form");
    form.reset();
    batchLabelPreview();
    openDialog("batch-label-dialog");
  }
  async function submitBatchLabels() {
    const payload = batchLabelPreview();
    if (payload.recording_ids.length > MAX_BATCH_RECORDINGS)
      throw new Error(
        `Bitte höchstens ${MAX_BATCH_RECORDINGS} Aufnahmen gemeinsam bestätigen.`,
      );
    $("#batch-label-submit").disabled = true;
    const result = await api("/api/recordings/labels/batch", {
      method: "POST",
      body: payload,
    });
    const skipped = arr(result.skipped);
    const updated = arr(result.updated);
    updated.forEach((r) =>
      state.selectedRecordings.delete(r.recording_id || r.id),
    );
    closeDialog($("#batch-label-dialog"));
    await loadRecordings();
    const notice = $("#batch-label-result");
    notice.hidden = false;
    notice.textContent = `${result.count ?? updated.length} Labels bestätigt.${skipped.length ? ` ${skipped.length} übersprungen: ${skipped.map((r) => `${r.id}: ${text(r.reason)}`).join("; ")}` : ""}`;
    toast(`${result.count ?? updated.length} Aufnahmen bestätigt.`);
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
  function selectedTopologyIds() {
    return [...$("#experiment-topology").options]
      .filter((option) => option.selected && option.value)
      .map((option) => option.value);
  }
  function updateTopologySelection() {
    const count = selectedTopologyIds().length;
    $("#topology-selection-count").textContent = `${count} / 50 ausgewählt`;
    $("#topology-selection-count").classList.toggle(
      "missing-count",
      count === 0 || count > 50,
    );
  }
  function setTopologySelection(ids) {
    const selected = new Set(ids);
    for (const option of $("#experiment-topology").options)
      option.selected = selected.has(option.value);
    state.topologySelectionInitialized = true;
    updateTopologySelection();
    scheduleReadiness();
  }
  async function loadTopologies() {
    const previous = new Set(selectedTopologyIds());
    const needsDefault = !state.topologySelectionInitialized;
    state.topologies = itemArray(await api("/api/topologies"), "topologies");
    renderTopologies();
    if (needsDefault && previous.size === 0) {
      const reference =
        state.topologies.find((t) => t.id === "feature-lda") ||
        state.topologies[0];
      if (reference) previous.add(reference.id);
    }
    $("#experiment-topology").replaceChildren(
      ...state.topologies.map((t) =>
        el("option", {
          value: t.id,
          selected: previous.has(t.id),
          text: `${t.name} · ${t.family}${t.status === "requires_torch" ? " · PyTorch nötig" : ""}`,
        }),
      ),
    );
    state.topologySelectionInitialized = true;
    updateTopologySelection();
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
    const ids = selectedTopologyIds();
    if (!ids.includes(id)) ids.push(id);
    setTopologySelection(ids);
    $("#experiment-topology").focus();
    toast("Topologie zur Vergleichsauswahl hinzugefügt.");
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
                    text: `${e.config?.source === "synthetic" ? "Synthetisch · " : ""}${e.config?.topology_id || e.topology_id || ""} · ${trainingOnly(e) ? "nur trainieren · unbewertet · " : ""}${date(e.created_at || e.started_at)}`,
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
    const topologyIds = selectedTopologyIds();
    if (topologyIds.length < 1 || topologyIds.length > 50)
      throw new Error("Bitte 1–50 Topologien für das Experiment auswählen.");
    const config = {
      name: f.get("name"),
      source: f.get("source"),
      evaluation_mode:
        f.get("source") === "recordings"
          ? f.get("evaluation_mode")
          : "independent",
      topology_id: topologyIds[0],
      topology_ids: topologyIds,
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
  function scheduleReadiness() {
    clearTimeout(readinessTimer);
    readinessTimer = setTimeout(() => refreshReadiness(), 350);
  }
  async function refreshReadiness() {
    const seq = ++state.readinessSequence;
    let config;
    try {
      config = experimentConfig();
      if (!config.topology_id) return;
    } catch (e) {
      $("#dataset-readiness").replaceChildren(
        el("p", {
          class: "field-help",
          text: "Die Datengrundlage wird nach vollständiger Konfiguration geprüft.",
        }),
      );
      return;
    }
    try {
      const data = await api("/api/experiments/validate", {
        method: "POST",
        body: config,
      });
      if (seq === state.readinessSequence) renderReadiness(data);
    } catch (e) {
      if (seq === state.readinessSequence)
        $("#dataset-readiness").replaceChildren(
          el("p", { class: "readiness-error", text: errorMessage(e) }),
        );
    }
  }
  function renderReadiness(data) {
    state.datasetReadiness = data;
    const trainingOnly = data.evaluation_mode === "training_only";
    const classes = arr(data.classes);
    const body = el("div", { class: "readiness-body" }, [
      el("div", { class: "readiness-heading" }, [
        el("strong", {
          text: trainingOnly
            ? "Training ohne unabhängige Bewertung"
            : "Datengrundlage für unabhängige Bewertung",
        }),
        pill(
          data.ready ? "Bereit" : "Weitere Daten nötig",
          data.ready ? "green" : "amber",
        ),
      ]),
      classes.length
        ? el("div", { class: "table-scroll" }, [
            el("table", { class: "data-table readiness-table" }, [
              el("thead", {}, [
                el(
                  "tr",
                  {},
                  ["Klasse", "Aufnahmen", "Sitzungen", "Segmente"].map((t) =>
                    el("th", { text: t }),
                  ),
                ),
              ]),
              el(
                "tbody",
                {},
                classes.map((c) =>
                  el("tr", {}, [
                    el("td", { text: c.label }),
                    el("td", { text: num(c.recordings, 0) }),
                    el("td", {
                      class: c.ready ? "ready-count" : "missing-count",
                      text: `${num(c.groups, 0)} / ${num(c.required_groups ?? (trainingOnly ? 1 : 3), 0)}`,
                    }),
                    el("td", {
                      text:
                        typeof c.segments === "object"
                          ? Object.entries(c.segments || {})
                              .map(([k, v]) => `${k}: ${v}`)
                              .join(" · ")
                          : num(c.segments, 0),
                    }),
                  ]),
                ),
              ),
            ]),
          ])
        : null,
      ...arr(data.errors).map((e) =>
        el("p", {
          class: "readiness-error",
          text: typeof e === "string" ? e : JSON.stringify(e),
        }),
      ),
      ...arr(data.warnings).map((e) =>
        el("p", {
          class: "field-help",
          text: typeof e === "string" ? e : JSON.stringify(e),
        }),
      ),
      trainingOnly
        ? el("p", {
            class: "field-help",
            text: "Das Modell wird als unbewertet gespeichert. Keine Testgenauigkeit, kein unabhängiger Robustheitsnachweis und keine Verwechslungsmatrix.",
          })
        : el("p", {
            class: "field-help",
            text: "Erforderliche Sitzungen je Klasse: Training, Validierung und Test. Aus einer Sitzung erzeugte Clips bleiben zusammen.",
          }),
    ]);
    $("#dataset-readiness").replaceChildren(body);
  }
  async function startExperiment() {
    const form = $("#experiment-form");
    if (!form.reportValidity()) return;
    const config = experimentConfig();
    const readiness = await api("/api/experiments/validate", {
      method: "POST",
      body: config,
    });
    renderReadiness(readiness);
    if (!readiness.ready) {
      $("#dataset-readiness").scrollIntoView({ block: "nearest" });
      throw new Error(
        arr(readiness.errors)
          .map((v) => (typeof v === "string" ? v : JSON.stringify(v)))
          .join(" · ") ||
          "Die ausgewählten Daten reichen für diesen Bewertungsmodus noch nicht aus.",
      );
    }
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
  function comparisonIsEvaluated(entry) {
    return (
      entry.evaluation_available !== false &&
      !trainingOnly(entry.report || entry)
    );
  }
  function rankedComparisonEntries(entries, report, mode) {
    const key = mode === "accuracy" ? "accuracy" : "score";
    const listed = arr(report.rankings?.[key]);
    const rankById = new Map(listed.map((id, index) => [id, index + 1]));
    return entries
      .map((entry, index) => ({
        entry,
        index,
        rank: comparisonIsEvaluated(entry)
          ? (rankById.get(entry.topology_id) ??
            (key === "accuracy" ? entry.accuracy_rank : entry.score_rank) ??
            null)
          : null,
      }))
      .sort(
        (a, b) =>
          (a.rank ?? Infinity) - (b.rank ?? Infinity) || a.index - b.index,
      );
  }
  function liveModelRecommendations(report, entries) {
    const byId = new Map(entries.map((entry) => [entry.topology_id, entry]));
    return [
      ...new Set(
        arr(report.recommended_topology_ids)
          .map((id) => byId.get(id))
          .filter(
            (entry) =>
              entry?.model_id && entry.score?.eligible_for_live === true,
          )
          .map((entry) => entry.model_id),
      ),
    ].slice(0, 3);
  }
  function scoreComponentsNode(entry) {
    const score = entry.score || {};
    const names = {
      accuracy: "Validierungsgenauigkeit",
      robustness: "Robustheit",
      realtime: "Echtzeitreserve",
      training: "Trainingsdauer",
    };
    return el("details", { class: "score-components" }, [
      el("summary", { text: "Score-Anteile" }),
      ...Object.entries(names).map(([key, label]) => {
        const part = score.components?.[key] || {};
        return el("div", {}, [
          el("span", { text: label }),
          el("strong", {
            text: `${num(part.points, 1)} / ${num(part.weight ?? score.weights?.[key], 0)}`,
          }),
        ]);
      }),
      ...arr(score.reasons).map((reason) =>
        el("p", { class: "field-help", text: text(reason) }),
      ),
    ]);
  }
  function comparisonTable(experiment, report, entries, inspectedIndex) {
    const unvalidated =
      trainingOnly(report) ||
      entries.every((entry) => !comparisonIsEvaluated(entry));
    const mode = state.comparisonRankingMode;
    const recommended = liveModelRecommendations(report, entries);
    const ranking = el(
      "select",
      { "aria-label": "Rangfolge wählen", disabled: unvalidated },
      [
        el("option", {
          value: "score",
          text: "Rang nach Gesamtscore",
          selected: mode === "score",
        }),
        el("option", {
          value: "accuracy",
          text: "Rang nach Validierungsgenauigkeit",
          selected: mode === "accuracy",
        }),
      ],
    );
    ranking.addEventListener("change", () => {
      state.comparisonRankingMode = ranking.value;
      renderExperimentResult(experiment);
    });
    const rows = rankedComparisonEntries(entries, report, mode).map(
      ({ entry, index, rank }) => {
        const metrics = entry.metrics || entry.report?.metrics || {};
        const validation = entry.validation || entry.report?.validation || {};
        const evaluated = comparisonIsEvaluated(entry);
        const winner =
          !unvalidated && report.selected_topology_id === entry.topology_id;
        const inspect = () => {
          state.comparisonInspection = {
            experimentId: experiment.id || state.selectedExperiment,
            topologyId: entry.topology_id,
          };
          renderExperimentResult(experiment);
        };
        const select = button(
          entry.name || entry.topology_id,
          (event) => {
            event.stopPropagation();
            inspect();
          },
          "comparison-select",
        );
        select.setAttribute("aria-pressed", String(index === inspectedIndex));
        const checkbox = el("input", {
          type: "checkbox",
          checked: state.checkedLiveModels.has(entry.model_id),
          disabled:
            !entry.model_id ||
            (!state.checkedLiveModels.has(entry.model_id) &&
              state.checkedLiveModels.size >= 3),
          "aria-label": `${entry.name || entry.topology_id} für Live-Stichproben auswählen`,
        });
        checkbox.addEventListener("click", (event) => event.stopPropagation());
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            if (state.checkedLiveModels.size >= 3) {
              checkbox.checked = false;
              toast("Für Live-Stichproben höchstens drei Modelle auswählen.");
              return;
            }
            state.checkedLiveModels.add(entry.model_id);
          } else state.checkedLiveModels.delete(entry.model_id);
          renderExperimentResult(experiment);
        });
        const scored =
          evaluated &&
          entry.score?.quality_score_available !== false &&
          Number.isFinite(entry.score?.total);
        return el(
          "tr",
          {
            class:
              index === inspectedIndex
                ? "comparison-row selected"
                : "comparison-row",
            onClick: inspect,
          },
          [
            el("td", {
              class: "mono comparison-rank",
              text: rank === null ? "—" : String(rank),
            }),
            el("td", { class: "selection-cell live-selection-cell" }, [
              checkbox,
            ]),
            el("td", {}, [
              select,
              el("small", { text: entry.family || entry.topology_id }),
              winner
                ? pill("Berichtsmodell · Val-F1", "cyan")
                : !evaluated
                  ? pill("Unbewertet", "amber")
                  : null,
            ]),
            el("td", { class: "comparison-score-cell" }, [
              el("strong", {
                text: scored
                  ? `${num(entry.score.total, 1)} / 100`
                  : evaluated
                    ? "—"
                    : "Unbewertet",
              }),
              evaluated && entry.score ? scoreComponentsNode(entry) : null,
            ]),
            el("td", {
              class: "mono",
              text: evaluated
                ? percent(
                    validation.balanced_accuracy ??
                      validation.metrics?.balanced_accuracy,
                  )
                : "Unbewertet",
            }),
            el("td", {
              class: "mono",
              text: evaluated
                ? num(validation.macro_f1 ?? validation.metrics?.macro_f1, 3)
                : "Unbewertet",
            }),
            el("td", { class: "mono" }, [
              el("strong", {
                text: evaluated ? num(metrics.macro_f1, 3) : "Unbewertet",
              }),
              evaluated
                ? el("small", {
                    text: `Genauigkeit ${percent(metrics.accuracy)}`,
                  })
                : null,
            ]),
            el("td", {
              class: "mono",
              text: duration(metrics.training_seconds),
            }),
            el("td", { class: "mono" }, [
              el("strong", { text: `${num(metrics.latency_ms_p50, 3)} ms` }),
              el("small", { text: `P95 ${num(metrics.latency_ms_p95, 3)} ms` }),
            ]),
            el("td", { class: "mono", text: bytes(metrics.model_bytes) }),
            el("td", {
              class: "mono",
              text: num(metrics.real_time_factor_p95, 4),
            }),
            el("td", {}, [
              el("strong", {
                text: `Training: ${text(entry.report?.hardware?.training_device || entry.device || entry.report?.hardware?.device || report.hardware?.device)}`,
              }),
              el("small", {
                text: `Inferenz: ${text(metrics.inference_device || entry.report?.hardware?.inference_device)}`,
              }),
            ]),
          ],
        );
      },
    );
    const top3 = button(
      "Top 3 live vergleichen",
      () =>
        action(async () => {
          state.checkedLiveModels = new Set(recommended);
          await configureLiveComparison(recommended, true);
        }),
      "button button-primary",
    );
    top3.disabled = recommended.length === 0;
    const apply = button(
      `${state.checkedLiveModels.size} gewählte Modelle live vergleichen`,
      () =>
        action(() =>
          configureLiveComparison([...state.checkedLiveModels], true),
        ),
      "button",
    );
    apply.disabled = state.checkedLiveModels.size === 0;
    const clear = button(
      "Live-Vergleich beenden",
      () =>
        action(async () => {
          await configureLiveComparison([], false);
          renderExperimentResult(experiment);
        }),
      "button button-quiet",
    );
    clear.disabled =
      !state.liveComparisonSelection.active &&
      state.checkedLiveModels.size === 0;
    return el(
      "section",
      { class: "comparison-panel", "aria-label": "Topologievergleich" },
      [
        el("div", { class: "comparison-heading" }, [
          el("div", {}, [
            el("span", {
              class: "panel-kicker",
              text: `${entries.length} TOPOLOGIEN / GLEICHE VERSUCHSBEDINGUNGEN`,
            }),
            el("h3", { text: "Modelle vergleichen" }),
          ]),
          unvalidated ? pill("Alle unbewertet", "amber") : null,
        ]),
        el("p", {
          class: "field-help comparison-note",
          text: unvalidated
            ? "Nur trainiert: Es gibt keine unabhängigen Gütemetriken, Qualitätsscores oder Gewinner. Manuell ausgewählte Modelle lassen sich dennoch an Live-Stichproben erproben."
            : "Die Rangfolge verwendet ausschließlich Validierungsdaten. Das Berichtsmodell folgt dem Validierungs-Macro-F1; die Top-3-Empfehlung folgt dem Gesamtscore. Finaltestwerte bestimmen weder Rang noch Empfehlung.",
        }),
        el("div", { class: "comparison-score-toolbar" }, [
          el("label", {}, [
            el("span", { class: "sr-only", text: "Rangfolge" }),
            ranking,
          ]),
          el("span", {
            class: "secondary",
            text: `${state.checkedLiveModels.size} / 3 für Live-Stichproben ausgewählt`,
          }),
        ]),
        el("div", { class: "table-scroll" }, [
          el(
            "table",
            { class: "data-table comparison-table score-comparison-table" },
            [
              el("thead", {}, [
                el(
                  "tr",
                  {},
                  [
                    "Rang",
                    "Live",
                    "Topologie",
                    "Score",
                    "Val. bal. Genauigkeit",
                    "Val. F1",
                    "Finaltest F1 / Genauigkeit",
                    "Training",
                    "P50 / P95",
                    "Größe",
                    "P95-Echtzeitfaktor",
                    "Training / Inferenz",
                  ].map((label) => el("th", { text: label })),
                ),
              ]),
              el("tbody", {}, rows),
            ],
          ),
        ]),
        el("details", { class: "comparison-score-definition" }, [
          el("summary", { text: "Wie setzt sich der Gesamtscore zusammen?" }),
          el("p", {
            class: "field-help",
            text: "Bis zu 100 Punkte: 45 für zufallsbereinigte ausgewogene Validierungsgenauigkeit, 35 für Robustheit über Validierungs-SNR und Jitter (70 % Mittelwert, 30 % schlechtester Fall), 15 für die P95-Echtzeitreserve und 5 für kurze Trainingsdauer. Volle Echtzeitpunkte erfordern fünfmal schnellere Verarbeitung als das IQ-Zeitfenster. Trainingspunkte: 5 / (1 + Trainingssekunden / 300). Zeitpunkte gelten für die gemessene Hardware. Ohne unabhängige Bewertung gibt es keinen Qualitätsscore.",
          }),
          el("pre", {
            text: JSON.stringify(report.score_definition || {}, null, 2),
          }),
        ]),
        el("div", { class: "panel-actions comparison-live-actions" }, [
          top3,
          apply,
          clear,
        ]),
      ],
    );
  }
  function renderExperimentResult(experiment) {
    const fullReport = experiment.report || experiment.result || experiment;
    const comparison = arr(fullReport.comparison);
    const experimentId = experiment.id || state.selectedExperiment;
    const rememberedIndex =
      state.comparisonInspection?.experimentId === experimentId
        ? comparison.findIndex(
            (entry) =>
              entry.topology_id === state.comparisonInspection.topologyId,
          )
        : -1;
    const selectedIndex = Number.isInteger(fullReport.selected_comparison_index)
      ? fullReport.selected_comparison_index
      : comparison.findIndex(
          (entry) => entry.topology_id === fullReport.selected_topology_id,
        );
    const inspectedIndex =
      rememberedIndex >= 0
        ? rememberedIndex
        : selectedIndex >= 0 && selectedIndex < comparison.length
          ? selectedIndex
          : 0;
    const inspected = comparison[inspectedIndex];
    const report = inspected?.report || fullReport;
    const metrics = inspected?.metrics || report.metrics || {};
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
    if (comparison.length) {
      n.append(
        comparisonTable(experiment, fullReport, comparison, inspectedIndex),
      );
      n.append(
        el("div", { class: "comparison-inspection-heading" }, [
          el("span", { class: "panel-kicker", text: "EINZELAUSWERTUNG" }),
          el("h3", {
            text: inspected?.name || inspected?.topology_id || "Modell",
          }),
        ]),
      );
    }
    if (
      trainingOnly(report) ||
      trainingOnly(fullReport) ||
      inspected?.evaluation_available === false ||
      trainingOnly(experiment)
    ) {
      n.append(
        el("div", { class: "result-training-only" }, [
          pill("Unbewertet · nur trainiert", "amber"),
          el("p", {
            class: "result-notice",
            text: "Dieses Modell wurde ohne unabhängigen Validierungs- und Testsatz trainiert. Eine Genauigkeit oder Verwechslungsmatrix wird deshalb nicht ausgewiesen. Für eine unabhängige Bewertung sind je Klasse mindestens drei getrennte Aufnahmesitzungen nötig.",
          }),
          el("div", { class: "result-metrics" }, [
            metric(
              "TRAININGSDAUER",
              duration(metrics.training_seconds),
              "Gemessene Wandzeit",
            ),
            metric(
              "MODELLGRÖSSE",
              bytes(metrics.model_bytes),
              "Gespeichertes, unbewertetes Modell",
            ),
            metric(
              "KLASSIFIKATION · P50",
              `${num(metrics.latency_ms_p50, 3)} ms`,
              "Rechenzeit, kein Gütenachweis",
            ),
          ]),
          el("details", { class: "result-details" }, [
            el("summary", {
              text: "Trainingskonfiguration, Datenherkunft & Hardware",
            }),
            el("pre", {
              text: JSON.stringify(
                {
                  evaluation_mode: report.evaluation_mode,
                  evaluation_available: report.evaluation_available,
                  validation_status: report.validation_status,
                  classes: report.classes,
                  split_groups: report.split_groups,
                  config: report.config,
                  hardware: report.hardware,
                  limitations: report.limitations,
                },
                null,
                2,
              ),
            }),
          ]),
        ]),
      );
      body.replaceChildren(n);
      return;
    }
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
  function portableModelSummary(model) {
    const meta = model.metadata || model;
    const report = model.report || meta.report || {};
    return {
      meta,
      report,
      metrics: model.metrics || meta.metrics || report.metrics || {},
      score: model.score || meta.score || report.score || null,
      hardware:
        model.assessment_hardware ||
        meta.assessment_hardware ||
        model.hardware ||
        meta.hardware ||
        report.hardware ||
        {},
      unvalidated:
        trainingOnly(meta) ||
        trainingOnly(report) ||
        model.evaluation_available === false ||
        report.evaluation_available === false,
    };
  }
  function modelAssessmentHardware(hardware) {
    const details = [];
    if (hardware.cpu || hardware.processor)
      details.push(String(hardware.cpu || hardware.processor));
    if (hardware.gpu || hardware.gpu_name)
      details.push(`GPU: ${text(hardware.gpu || hardware.gpu_name)}`);
    if (hardware.device)
      details.push(`Gerät im Trainingsbericht: ${text(hardware.device)}`);
    return (
      details.join(" · ") ||
      "Die ursprüngliche Messhardware ist im Modellpaket nicht dokumentiert."
    );
  }
  function renderModels() {
    const count = state.checkedLiveModels.size;
    $("#models-live-count").textContent = `${count} / 3 Modelle ausgewählt`;
    $("#models-live-start").disabled = count === 0 || count > 3;
    $("#models-live-clear").disabled = count === 0;
    $("#models-live-stop").disabled = !state.liveComparisonSelection?.active;
    $("#models-empty").hidden = state.models.length > 0;
    $("#model-grid").replaceChildren(
      ...state.models.map((model) => {
        const { meta, metrics, score, hardware, unvalidated } =
          portableModelSummary(model);
        const active =
          model.active ||
          state.status.active_model?.model_id === model.id ||
          state.status.active_model === model.id;
        const selected = state.checkedLiveModels.has(model.id);
        const checkbox = el("input", {
          type: "checkbox",
          checked: selected,
          disabled: !selected && count >= 3,
          "aria-label": `${model.name || meta.name || model.id} für Live-Stichproben auswählen`,
        });
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            if (state.checkedLiveModels.size >= 3) {
              checkbox.checked = false;
              toast("Höchstens drei Modelle für Live-Stichproben auswählen.");
              return;
            }
            state.checkedLiveModels.add(model.id);
          } else state.checkedLiveModels.delete(model.id);
          renderModels();
        });
        const scored =
          !unvalidated &&
          score?.quality_score_available !== false &&
          Number.isFinite(score?.total);
        return el(
          "article",
          { class: `panel model-card${selected ? " live-selected" : ""}` },
          [
            el("div", { class: "panel-heading" }, [
              el("div", {}, [
                el("span", {
                  class: "panel-kicker",
                  text: text(
                    meta.family || meta.topology_id || meta.topology?.family,
                    "TRAINIERTES MODELL",
                  ),
                }),
                el("h2", { text: model.name || meta.name || model.id }),
              ]),
              el("div", { class: "model-state-badges" }, [
                pill(active ? "Aktiv" : "Gespeichert", active ? "cyan" : ""),
                unvalidated ? pill("Unbewertet", "amber") : null,
                model.imported ? pill("Importiert") : null,
              ]),
            ]),
            el("label", { class: "checkbox model-live-choice" }, [
              checkbox,
              "Für Live-Stichproben auswählen",
            ]),
            el(
              "div",
              { class: "model-stats" },
              [
                [
                  unvalidated ? "BEWERTUNG" : "GENAUIGKEIT · URSPRUNGSBERICHT",
                  unvalidated ? "Unbewertet" : percent(metrics.accuracy),
                ],
                ["GRÖSSE", bytes(metrics.model_bytes ?? model.size_bytes)],
                ["KLASSEN", arr(meta.classes).join(" / ") || "—"],
                ["ERSTELLT", date(model.created_at || meta.created_at)],
                [
                  "GESAMTSCORE · URSPRUNGSRECHNER",
                  scored
                    ? `${num(score.total, 1)} / 100`
                    : unvalidated
                      ? "Unbewertet"
                      : "—",
                ],
                [
                  "P95 · URSPRÜNGLICHE MESSUNG",
                  `${num(metrics.latency_ms_p95, 3)} ms`,
                ],
              ].map(([label, value]) =>
                el("div", {}, [
                  el("span", { class: "metric-label", text: label }),
                  el("strong", { text: value }),
                ]),
              ),
            ),
            el("div", { class: "model-assessment-hardware" }, [
              el("span", {
                class: "metric-label",
                text: "TRAININGS-/BEWERTUNGSRECHNER",
              }),
              el("p", { text: modelAssessmentHardware(hardware) }),
              el("small", {
                text: "Gespeicherte Bewertung und Laufzeit gelten für den ursprünglichen Versuchsrechner. Aktuelle CPU-Zeiten auf diesem Gerät werden unter Empfang / Live-Stichproben neu gemessen.",
              }),
            ]),
            el("div", {
              class: "panel-note",
              text: unvalidated
                ? "Nur trainiert · noch keine unabhängige Bewertung. Reale Tests mit getrennten Aufnahmesitzungen erforderlich."
                : meta.source === "synthetic" ||
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
                      `/api/models/${encodeURIComponent(model.id)}/activate`,
                      { method: "POST" },
                    );
                    await Promise.all([loadStatus(), loadModels()]);
                    toast("Modell aktiviert.");
                  }),
                active ? "button button-quiet" : "button button-primary",
              ),
              download(
                `/api/models/${encodeURIComponent(model.id)}/download`,
                "Modell exportieren",
              ),
            ]),
          ],
        );
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
      "source",
      "evaluation_mode",
      "seed",
      "samples_per_class",
      "epochs",
      "device",
    ];
    const topologyIds =
      c.topology_ids !== undefined
        ? c.topology_ids
        : c.topology_id
          ? [c.topology_id]
          : selectedTopologyIds();
    if (
      !Array.isArray(topologyIds) ||
      topologyIds.length < 1 ||
      topologyIds.length > 50 ||
      topologyIds.some((id) => typeof id !== "string")
    )
      throw new Error("Die Konfiguration benötigt 1–50 Topologie-IDs.");
    if (topologyIds.some((id) => !state.topologies.some((t) => t.id === id)))
      throw new Error(
        "Die Topologien dieser Konfiguration zuerst im Tab Topologien laden.",
      );
    if (
      c.evaluation_mode &&
      !["independent", "training_only"].includes(c.evaluation_mode)
    )
      throw new Error("Ungültiger Bewertungsmodus.");
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
    setTopologySelection(topologyIds);
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
    scheduleReadiness();
    toast(
      "Versuchskonfiguration geladen. Der Versuch wurde noch nicht gestartet.",
    );
  }
  function setupEvents() {
    $("#models-live-start").addEventListener("click", () =>
      action(() => configureLiveComparison([...state.checkedLiveModels], true)),
    );
    $("#models-live-clear").addEventListener("click", () => {
      state.checkedLiveModels.clear();
      renderModels();
    });
    $("#models-live-stop").addEventListener("click", () =>
      action(async () => {
        await configureLiveComparison([], false);
        renderModels();
      }),
    );

    $("#receiver-form").addEventListener("input", updateCaptureSizeEstimate);
    $("#receiver-form").addEventListener("change", updateCaptureSizeEstimate);
    $("#live-comparison-stop").addEventListener("click", () =>
      action(() => configureLiveComparison([], false)),
    );

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
      $("#waterfall-section").hidden = !$("#waterfall-toggle").checked;
      drawWaterfall();
    });
    [
      "waterfall-palette",
      "waterfall-min",
      "waterfall-max",
      "waterfall-window",
      "detection-overlay-toggle",
    ].forEach((id) =>
      document.getElementById(id).addEventListener("change", () => {
        const minimum = Number($("#waterfall-min").value),
          maximum = Number($("#waterfall-max").value);
        if (
          !Number.isFinite(minimum) ||
          !Number.isFinite(maximum) ||
          maximum <= minimum
        ) {
          $("#waterfall-max").value = String(
            (Number.isFinite(minimum) ? minimum : -120) + 5,
          );
          toast("Das Skalenmaximum muss größer als das Minimum sein.");
        }
        drawSpectrum();
        drawWaterfall();
      }),
    );
    $("#waterfall-canvas").addEventListener("pointermove", waterfallPointer);
    $("#waterfall-expand").addEventListener("click", () => {
      const expanded = $(".receive-grid").classList.toggle("monitor-expanded");
      $("#waterfall-expand").setAttribute("aria-pressed", String(expanded));
      $("#waterfall-expand").setAttribute(
        "aria-label",
        expanded
          ? "RF-Monitor wieder verkleinern"
          : "Spektrum und Wasserfall vergrößern",
      );
      requestAnimationFrame(() => {
        drawSpectrum();
        drawWaterfall();
      });
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
    $("#recording-filter").addEventListener("change", renderRecordings);
    $("#recording-select-all").addEventListener("change", (event) => {
      const checked = event.currentTarget.checked;
      if (checked) addRecordingSelection(visibleRecordings().map((r) => r.id));
      else {
        visibleRecordings().forEach((r) =>
          state.selectedRecordings.delete(r.id),
        );
        updateRecordingSelection();
      }
    });
    $("#recording-select-unconfirmed").addEventListener("click", () => {
      addRecordingSelection(
        visibleRecordings()
          .filter((r) => !r.verified)
          .map((r) => r.id),
      );
    });
    $("#recording-clear-selection").addEventListener("click", () => {
      state.selectedRecordings.clear();
      updateRecordingSelection();
    });
    $("#recording-batch-open").addEventListener("click", openBatchLabels);
    $("#batch-label-form").addEventListener("input", batchLabelPreview);
    $("#batch-label-form").addEventListener("change", batchLabelPreview);
    $("#batch-label-form").addEventListener("submit", (event) => {
      event.preventDefault();
      action(submitBatchLabels, event.currentTarget);
    });
    $("#experiment-form").addEventListener("input", scheduleReadiness);
    $("#experiment-form").addEventListener("change", scheduleReadiness);
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
    $("#experiment-topology").addEventListener("change", () => {
      state.topologySelectionInitialized = true;
      updateTopologySelection();
      scheduleReadiness();
    });
    $("#topology-select-all").addEventListener("click", () =>
      setTopologySelection(
        state.topologies
          .filter((t) => t.status !== "requires_torch")
          .map((t) => t.id),
      ),
    );
    $("#topology-select-none").addEventListener("click", () =>
      setTopologySelection([]),
    );
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
    observer.observe($(".waterfall-stage"));
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
      if (
        state.view === "aufnahmen" &&
        (receiverRunning() || state.wasReceiving)
      )
        await loadRecordings();
      state.wasReceiving = receiverRunning();
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
      loadLiveComparison(),
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

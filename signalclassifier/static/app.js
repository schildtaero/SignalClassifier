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
    cleanupPlan: null,
    cleanupBusy: false,
    cleanupSequence: 0,
    analysis: null,
    analysisCatalogue: null,
    analysisExpertDrafts: new Map(),
    analysisSettingsLoaded: false,
    analysisPolling: false,
    analysisSelectedId: null,
    analysisDetail: null,
    analysisScanPlan: null,
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
    analyse: [
      "Analyse & Entscheidungswege",
      "Mehrere Signale einzeln prüfen. Kandidaten und Belege nachvollziehen.",
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
  function presentChildren(children) {
    return children.filter(
      (child) => child !== null && child !== undefined && child !== false,
    );
  }
  function replaceContent(node, ...children) {
    node.replaceChildren(...presentChildren(children));
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
      analyse: ["refresh", "Analyse aktualisieren"],
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
      if (view === "analyse") await loadAnalysis(true);
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
  function telemetrySummary(sample, now = Date.now()) {
    const source =
      sample && typeof sample === "object" && !Array.isArray(sample)
        ? sample
        : {};
    const numeric = (value, minimum = 0, maximum = Infinity) =>
      typeof value === "number" &&
      Number.isFinite(value) &&
      value >= minimum &&
      value <= maximum
        ? value
        : null;
    const timestamp =
      typeof source.sampled_at === "string" && source.sampled_at.trim()
        ? Date.parse(source.sampled_at)
        : NaN;
    const validTime =
      Number.isFinite(timestamp) &&
      typeof now === "number" &&
      Number.isFinite(now);
    const interval = numeric(source.interval_seconds, Number.MIN_VALUE);
    const staleAfterSeconds = Math.max(10, 3 * (interval ?? 0));
    const backendAge = numeric(source.age_seconds);
    const ageSeconds =
      backendAge ?? (validTime ? (now - timestamp) / 1000 : null);
    const future = backendAge === null && ageSeconds !== null && ageSeconds < 0;
    const stale =
      source.stale === true ||
      (ageSeconds !== null && ageSeconds > staleAfterSeconds);
    const fresh = validTime && !future && !stale;
    let reason = !validTime
      ? "Keine gültige Messzeit verfügbar."
      : future
        ? "Die Messzeit liegt in der Zukunft. Der Uhrversatz zwischen Browser und Backend verhindert eine verlässliche Altersangabe."
        : stale
          ? "Die Messung ist als veraltet gemeldet oder liegt außerhalb des Aktualitätsfensters. Auslastungswerte sind nicht mehr aktuell."
          : "";
    if (typeof source.sampler_error === "string" && source.sampler_error)
      reason = `${reason ? `${reason} ` : ""}Messfehler: ${source.sampler_error}`;
    const cpu = source.cpu && typeof source.cpu === "object" ? source.cpu : {};
    const gpu = source.gpu && typeof source.gpu === "object" ? source.gpu : {};
    const available = gpu.available === true;
    const devices =
      available && Array.isArray(gpu.devices)
        ? gpu.devices.map((device) => {
            const d = device && typeof device === "object" ? device : {};
            let used = numeric(d.memory_used_bytes),
              total = numeric(d.memory_total_bytes, Number.MIN_VALUE);
            const inconsistentMemory =
              used !== null && total !== null && used > total;
            if (!fresh || inconsistentMemory) {
              used = null;
              total = null;
            }
            return {
              index: Number.isInteger(d.index) && d.index >= 0 ? d.index : null,
              name: typeof d.name === "string" && d.name ? d.name : "GPU-Gerät",
              utilizationPercent: fresh
                ? numeric(d.utilization_percent, 0, 100)
                : null,
              memoryUsedBytes: used,
              memoryTotalBytes: total,
              inconsistentMemory,
            };
          })
        : [];
    return {
      fresh,
      stale,
      future,
      reason,
      sampledAt: validTime ? new Date(timestamp).toISOString() : null,
      ageSeconds: ageSeconds !== null && !future ? ageSeconds : null,
      intervalSeconds: interval,
      staleAfterSeconds,
      cpu: {
        systemPercent: fresh ? numeric(cpu.system_percent, 0, 100) : null,
        processPercent: fresh ? numeric(cpu.process_percent) : null,
        processScope:
          typeof cpu.process_percent_scope === "string"
            ? cpu.process_percent_scope
            : null,
        appSystemPercent: fresh
          ? numeric(cpu.app_system_percent, 0, 100)
          : null,
        processCount:
          Number.isInteger(cpu.process_count) && cpu.process_count > 0
            ? cpu.process_count
            : null,
        warmingProcesses:
          Number.isInteger(cpu.warming_processes) && cpu.warming_processes >= 0
            ? cpu.warming_processes
            : null,
        reason:
          typeof cpu.reason === "string" && cpu.reason ? cpu.reason : null,
        logicalCpus:
          Number.isInteger(cpu.logical_cpus) && cpu.logical_cpus > 0
            ? cpu.logical_cpus
            : null,
      },
      gpu: {
        available,
        provider:
          typeof gpu.provider === "string" && gpu.provider
            ? gpu.provider
            : null,
        scope: typeof gpu.scope === "string" ? gpu.scope : null,
        reason:
          typeof gpu.reason === "string" && gpu.reason
            ? gpu.reason
            : !available
              ? "GPU-Messquelle nicht verfügbar."
              : !devices.length
                ? "Die Messquelle hat keine GPU-Gerätedaten geliefert."
                : null,
        devices,
      },
    };
  }
  function renderTelemetry(sample) {
    const holder = $("#resource-telemetry-content");
    if (!holder) return;
    const summary = telemetrySummary(sample);
    const value = (percentValue) =>
      percentValue === null ? "—" : `${num(percentValue, 1)} %`;
    const status = $("#resource-telemetry-status");
    status.textContent = summary.fresh
      ? "Aktuelle Messung"
      : summary.future
        ? "Uhrversatz"
        : summary.stale
          ? "Messung veraltet"
          : "Keine aktuelle Messung";
    status.className = `pill ${summary.fresh ? "cyan" : "amber"}`;
    const timestamp = summary.sampledAt
      ? `${new Date(summary.sampledAt).toLocaleTimeString("de-DE", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })} UTC`
      : "—";
    $("#resource-telemetry-time").textContent =
      `Messung ${timestamp}${summary.ageSeconds === null ? "" : ` · vor ${num(summary.ageSeconds, 1)} s`}${summary.intervalSeconds === null ? " · Messintervall nicht angegeben" : ` · Messintervall ${num(summary.intervalSeconds, 1)} s`}`;
    const notice = $("#resource-telemetry-notice");
    notice.hidden = !summary.reason;
    notice.textContent = summary.reason;
    const cpuCard = el(
      "article",
      { class: "resource-telemetry-card resource-cpu-card" },
      [
        el("div", { class: "resource-telemetry-card-heading" }, [
          el("h3", { text: "CPU-Auslastung" }),
          el("span", {
            class: "resource-device-caption",
            text: summary.cpu.logicalCpus
              ? `${num(summary.cpu.logicalCpus, 0)} logische CPUs`
              : "CPU-Anzahl nicht gemeldet",
          }),
        ]),
        el("div", { class: "resource-cpu-metrics" }, [
          el("div", {}, [
            el("span", { class: "metric-label", text: "SYSTEM" }),
            el("strong", { text: value(summary.cpu.systemPercent) }),
            el("small", { text: "Gesamtsystem · 0–100 %" }),
          ]),
          el("div", {}, [
            el("span", { class: "metric-label", text: "APP / GESAMTSYSTEM" }),
            el("strong", { text: value(summary.cpu.appSystemPercent) }),
            el("small", {
              text: "App und Trainingworker · auf 0–100 % der verfügbaren CPU-Kapazität normiert",
            }),
          ]),
          el("div", {}, [
            el("span", { class: "metric-label", text: "APP + WORKER" }),
            el("strong", { text: value(summary.cpu.processPercent) }),
            el("small", {
              text: [
                "one_logical_cpu=100%",
                "application_process_tree_one_logical_cpu=100%",
              ].includes(summary.cpu.processScope)
                ? `100 % entsprechen einem logischen CPU-Kern; der Wert kann über 100 % liegen.${summary.cpu.processCount ? ` ${summary.cpu.processCount} Prozesse gemessen.` : ""}`
                : "Der Messbereich des Prozesswerts wurde nicht angegeben.",
            }),
          ]),
        ]),
        summary.cpu.reason
          ? el("p", { class: "field-help", text: summary.cpu.reason })
          : null,
      ],
    );
    const gpu = summary.gpu;
    const gpuCards = gpu.devices.length
      ? gpu.devices.map((device) =>
          el(
            "article",
            { class: "resource-telemetry-card resource-gpu-card" },
            [
              el("div", { class: "resource-telemetry-card-heading" }, [
                el("h3", { text: device.name }),
                el("span", {
                  class: "resource-device-caption",
                  text:
                    device.index === null
                      ? "GPU-Gerätemessung"
                      : `GPU ${device.index} · Gerätemessung`,
                }),
              ]),
              el("strong", {
                class: "resource-gpu-utilization",
                text: value(device.utilizationPercent),
              }),
              device.utilizationPercent === null
                ? el("p", {
                    class: "field-help",
                    text:
                      gpu.reason ||
                      (!summary.fresh
                        ? "Keine aktuelle Auslastungsmessung."
                        : "Der GPU-Auslastungswert wurde von der Messquelle nicht geliefert."),
                  })
                : null,
              el("div", { class: "resource-vram" }, [
                el("span", {
                  class: "metric-label",
                  text: "VRAM BELEGT / GESAMT",
                }),
                el("strong", {
                  text: `${bytes(device.memoryUsedBytes)} / ${bytes(device.memoryTotalBytes)}`,
                }),
              ]),
              el("small", {
                class: "resource-device-caption",
                text: device.inconsistentMemory
                  ? "Inkonsistente VRAM-Messwerte; keine Belegung angezeigt."
                  : `Quelle: ${gpu.provider || "nicht angegeben"}${gpu.scope === "whole_device" ? " · gesamtes Gerät einschließlich anderer Prozesse" : " · Messbereich nicht angegeben"}`,
              }),
            ],
          ),
        )
      : [
          el(
            "article",
            { class: "resource-telemetry-card resource-gpu-card" },
            [
              el("div", { class: "resource-telemetry-card-heading" }, [
                el("h3", { text: "GPU-Gerätemessung" }),
                el("span", {
                  class: "resource-device-caption",
                  text: gpu.provider || "Messquelle nicht verfügbar",
                }),
              ]),
              el("strong", { class: "resource-gpu-utilization", text: "—" }),
              el("p", {
                class: "field-help",
                text: gpu.reason || "Noch keine GPU-Messwerte vorhanden.",
              }),
            ],
          ),
        ];
    holder.replaceChildren(cpuCard, ...gpuCards);
  }
  function renderStatus() {
    const status = state.status;
    renderTelemetry(status.telemetry);
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
    if (
      state.topologies.length &&
      state.topologyTorchAvailable !== hw.torch_available
    ) {
      state.topologyTorchAvailable = hw.torch_available;
      refreshTopologyAvailability();
    }
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
      analysis_max_channels: Number(f.get("analysis_max_channels")),
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
  function analysisDecision(value) {
    return value?.decision && typeof value.decision === "object"
      ? value.decision
      : value || {};
  }
  function analysisStatus(value) {
    return (
      {
        unknown: "Unbekannt",
        hypothesis: "Merkmalshypothese",
        candidate: "Modellkandidat",
        model_candidate: "Modellkandidat",
        observed: "Beobachtet",
        conflict: "Widerspruch",
        no_signal: "Kein Signal",
        invalid: "Ungültige Eingabe",
        insufficient_samples: "Zu wenige Samples",
        budget_exhausted: "Rechenbudget erschöpft",
        not_run_budget: "Wartet auf Rechenbudget",
        not_evaluated: "Nicht geprüft",
        unsupported: "Nicht unterstützt",
        run: "Ausgeführt",
        skipped: "Übersprungen",
        error: "Fehler",
        rejected: "Abgelehnt",
        passed: "Bestanden",
        completed: "Abgeschlossen",
        running: "Läuft",
        idle: "Bereit",
        stopped: "Gestoppt",
        starting: "Startet",
        stopping: "Stoppt",
        analyzing: "Wertet Empfangsfenster aus",
        cancelled: "Abgebrochen",
      }[value] || text(value, "Unbekannt")
    );
  }
  function analysisLabel(
    label,
    level = null,
    taxonomy = state.analysisCatalogue?.taxonomy,
  ) {
    const value = text(label, "Unbekannt");
    const candidates = level
      ? arr(taxonomy?.[level])
      : Object.values(taxonomy || {}).flatMap((items) => arr(items));
    const item = candidates.find(
      (entry) =>
        String(entry.id).toLocaleLowerCase("de") ===
        value.toLocaleLowerCase("de"),
    );
    return item?.name || value;
  }
  function analysisScoreKind(kind) {
    return (
      {
        heuristic_support_not_probability:
          "heuristischer Hinweis, keine Wahrscheinlichkeit",
        uncalibrated_model_scores_not_averaged:
          "unkalibrierte Modellwerte, nicht gemittelt",
        uncalibrated_model_score: "unkalibrierter Modellwert",
      }[kind] || "unkalibrierter Bewertungswert"
    );
  }
  function analysisConclusionRows(value) {
    const decision = analysisDecision(value);
    return [
      ["waveform_family", "Signalform / Übertragungsverfahren"],
      ["modulation", "Modulation"],
      ["protocol", "Protokoll"],
      ["service", "Funkdienst"],
      ["emitter", "Emitter"],
    ].map(([key, title]) => {
      const source = decision.conclusions?.[key] || {};
      const status = source.status || "unknown";
      const hasLabel =
        !["unknown", "conflict", "not_evaluated", "unsupported"].includes(
          status,
        ) && !!source.label;
      return {
        key,
        title,
        status,
        label: hasLabel ? String(source.label) : "Unbekannt",
        reason:
          source.reason ||
          "Für diese Ebene liegt keine belastbare Aussage vor.",
        candidates: arr(source.candidates),
        evidenceIds: arr(source.evidence_ids),
      };
    });
  }
  function analysisStageOrder(value) {
    const stages = arr(analysisDecision(value).stages);
    const ids = new Set(stages.map((s) => String(s.id)));
    const done = new Set(),
      ordered = [],
      pending = [...stages];
    while (pending.length) {
      const index = pending.findIndex((s) =>
        arr(s.parents).every(
          (id) => !ids.has(String(id)) || done.has(String(id)),
        ),
      );
      if (index < 0) {
        ordered.push(
          ...pending.map((s) => ({ ...s, dependency_unresolved: true })),
        );
        break;
      }
      const [stage] = pending.splice(index, 1);
      ordered.push({
        ...stage,
        dependency_unresolved: arr(stage.parents).some(
          (id) => !ids.has(String(id)),
        ),
      });
      done.add(String(stage.id));
    }
    return ordered;
  }
  function matchedSignalClassifications(detections, observations, snapshot) {
    const now = Date.parse(
      snapshot?.analysis_timestamp || snapshot?.timestamp || "",
    );
    if (!Number.isFinite(now)) return new Map();
    const session = snapshot?.session_id || snapshot?.receiver?.session_id;
    const pairs = [];
    arr(observations).forEach((observation, observationIndex) => {
      const when = Date.parse(observation.observed_at || "");
      if (!Number.isFinite(when) || Math.abs(now - when) > 2000) return;
      if (
        session &&
        observation.session_id &&
        observation.session_id !== session
      )
        return;
      const input = analysisDecision(observation).input || {};
      const bounds = detectionBounds({ ...input, ...observation });
      const rate = finiteNumber(observation.sample_rate ?? input.sample_rate);
      if (
        !bounds ||
        rate === null ||
        rate <= 0 ||
        bounds.bandwidth > rate * 1.01
      )
        return;
      const receiverRate = finiteNumber(snapshot.sample_rate);
      if (receiverRate > 0 && rate > receiverRate * 1.01) return;
      arr(detections).forEach((detection, detectionIndex) => {
        const d = detectionBounds(detection);
        if (!d) return;
        if (
          detection.channel_id &&
          observation.channel_id &&
          String(detection.channel_id) !== String(observation.channel_id)
        )
          return;
        const overlap =
          Math.min(d.high, bounds.high) - Math.max(d.low, bounds.low);
        if (
          overlap <= 0 ||
          Math.abs(d.center - bounds.center) >
            Math.max(d.bandwidth, bounds.bandwidth) / 2
        )
          return;
        pairs.push({
          observation,
          observationIndex,
          detectionIndex,
          overlap: overlap / Math.min(d.bandwidth, bounds.bandwidth),
          distance: Math.abs(d.center - bounds.center),
        });
      });
    });
    pairs.sort((a, b) => b.overlap - a.overlap || a.distance - b.distance);
    const result = new Map(),
      used = new Set();
    for (const pair of pairs) {
      if (result.has(pair.detectionIndex) || used.has(pair.observationIndex))
        continue;
      result.set(pair.detectionIndex, pair.observation);
      used.add(pair.observationIndex);
    }
    return result;
  }
  function classifiedDetections(
    detections,
    classification,
    observations = null,
    snapshot = null,
  ) {
    if (Array.isArray(observations)) {
      const matched = matchedSignalClassifications(
        detections,
        observations,
        snapshot,
      );
      return arr(detections).map((d, index) => {
        const observation = matched.get(index);
        const conclusion = analysisConclusionRows(observation).find(
          (row) => row.key === "modulation",
        );
        return {
          ...d,
          detected_modulation: conclusion.label,
          modulation_evaluated: !!observation,
          modulation_status: conclusion.status,
          analysis_signal_id: observation?.signal_id || observation?.id,
        };
      });
    }
    const index = associatedDetectionIndex(detections, classification);
    return arr(detections).map((d, i) => ({
      ...d,
      detected_modulation:
        i === index ? String(classification.label) : "unbekannt",
      modulation_evaluated: i === index,
    }));
  }
  function detectionModulationLabel(detection) {
    const label = text(detection.detected_modulation, "unbekannt");
    return ["candidate", "model_candidate", "hypothesis"].includes(
      detection.modulation_status,
    )
      ? `${label} · ${detection.modulation_status === "hypothesis" ? "Hypothese" : "Kandidat"}`
      : label;
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
        snapshot.signal_classifications,
        snapshot,
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
        `${detectionModulationLabel(d)} · ${num(bounds.center / 1e6, 4)} MHz`,
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
      snapshot.signal_classifications,
      snapshot,
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
      replaceContent(
        container,
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
  function renderDetections(
    detections,
    classification = null,
    observations = null,
    snapshot = null,
  ) {
    const rows = classifiedDetections(
      arr(detections),
      classification,
      observations,
      snapshot,
    );
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
                    detectionModulationLabel(d),
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
                    ? `${d.modulation_status ? analysisStatus(d.modulation_status) : "Ausgewertet"} für diesen Kanal · keine Emitterbestätigung`
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
    renderDetections(
      snapshot.detections,
      snapshot.classification,
      snapshot.signal_classifications,
      snapshot,
    );
    renderReceptionAnalysis(snapshot);
    if (Array.isArray(snapshot.signal_classifications))
      renderReceptionDecision(snapshot);
    else if (snapshot.classification)
      renderClassification(snapshot.classification);
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
              if (result.signal_analysis)
                renderAnalysisDetail(
                  result.signal_analysis,
                  $("#recording-classification"),
                );
              else renderClassification(result, $("#recording-classification"));
              openDialog("classification-dialog");
            }),
          ),
          button("Analyse", () =>
            action(async () => {
              const signal = await api(
                `/api/analysis/recordings/${encodeURIComponent(r.id)}`,
                { method: "POST", body: {} },
              );
              state.analysisSelectedId = signal.id || signal.signal_id;
              state.analysisDetail = signal;
              await changeView("analyse");
              renderAnalysisDetail(signal);
              $("#analysis-detail").scrollIntoView({
                behavior: "smooth",
                block: "start",
              });
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
    const invalidIds = new Set(
      state.recordings.filter((raw) => raw.error).map((raw) => raw.id),
    );
    const verified = records.filter(
      (r) => r.verified && r.label && !invalidIds.has(r.id),
    );
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
    updateExperimentRecordingSelection();
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
  const CLEANUP_DURATION_SECONDS = 0.5;
  function cleanupDeletionPlan(preview) {
    if (
      !preview ||
      preview.max_duration_seconds !== CLEANUP_DURATION_SECONDS ||
      !Array.isArray(preview.recordings)
    )
      throw new Error(
        "Die Bereinigungsvorschau ist ungültig; bitte aktualisieren.",
      );
    if (preview.blocked_reason) throw new Error(String(preview.blocked_reason));
    if (
      preview.recordings.length > 10000 ||
      preview.count !== preview.recordings.length
    )
      throw new Error(
        "Die Vorschau überschreitet die Grenze von 10000 Aufnahmen oder ist unvollständig.",
      );
    const expected = preview.expected_files;
    if (!expected || typeof expected !== "object" || Array.isArray(expected))
      throw new Error(
        "Dateiabgleich fehlt in der Vorschau; bitte aktualisieren.",
      );
    const ids = [],
      fingerprints = Object.create(null),
      seen = new Set();
    for (const row of preview.recordings) {
      if (
        typeof row.id !== "string" ||
        !row.id ||
        seen.has(row.id) ||
        typeof row.duration_seconds !== "number" ||
        !Number.isFinite(row.duration_seconds) ||
        row.duration_seconds < 0 ||
        row.duration_seconds >= CLEANUP_DURATION_SECONDS
      )
        throw new Error(
          "Die Vorschau enthält keine eindeutig kurze, gültige Aufnahme; es wurde keine Löschung freigegeben.",
        );
      const fingerprint = expected[row.id];
      if (
        typeof fingerprint !== "string" ||
        !fingerprint ||
        (row.fingerprint !== undefined && row.fingerprint !== fingerprint)
      )
        throw new Error(
          "Dateiabgleich der Vorschau ist nicht vollständig; bitte aktualisieren.",
        );
      seen.add(row.id);
      ids.push(row.id);
      fingerprints[row.id] = fingerprint;
    }
    return Object.freeze({
      recording_ids: Object.freeze(ids),
      max_duration_seconds: CLEANUP_DURATION_SECONDS,
      expected_files: Object.freeze(fingerprints),
    });
  }
  function updateCleanupControls() {
    const count = state.cleanupPlan?.recording_ids.length || 0;
    const busy = state.cleanupBusy;
    $("#recording-cleanup-confirm").disabled = busy || count === 0;
    $("#recording-cleanup-confirm").textContent = busy
      ? "Löschung läuft …"
      : `${count} Aufnahmen endgültig löschen`;
    $("#recording-cleanup-refresh").disabled = busy;
    $$("[data-close]", $("#recording-cleanup-dialog")).forEach((button) => {
      button.disabled = busy;
    });
  }
  async function loadCleanupPreview() {
    if (state.cleanupBusy) return;
    const sequence = ++state.cleanupSequence;
    state.cleanupPlan = null;
    $("#recording-cleanup-summary").textContent = "Vorschau wird geladen …";
    $("#recording-cleanup-preview").replaceChildren();
    $("#recording-cleanup-blocked").hidden = true;
    $("#recording-cleanup-skipped-details").hidden = true;
    $(".dialog-error", $("#recording-cleanup-dialog"))?.remove();
    updateCleanupControls();
    try {
      const preview = await api(
        `/api/recordings/cleanup-preview?max_duration_seconds=${CLEANUP_DURATION_SECONDS}`,
      );
      if (
        sequence !== state.cleanupSequence ||
        !$("#recording-cleanup-dialog").open
      )
        return;
      const rows = arr(preview.recordings),
        verified = rows.filter(
          (row) => row.verified === true && row.label,
        ).length;
      const referenced = rows.filter(
        (row) => Number(row.experiment_count) > 0,
      ).length;
      $("#recording-cleanup-summary").textContent =
        `${num(rows.length, 0)} geschlossene Aufnahmen unter 0,5 s · ${bytes(preview.total_bytes)} einschließlich Metadaten. ${verified} bestätigte Labels werden mitgelöscht${referenced ? `; ${referenced} Aufnahmen werden von früheren Experimenten referenziert` : ""}.`;
      $("#recording-cleanup-preview").replaceChildren(
        ...rows.map((row) =>
          el("tr", {}, [
            detailCell(
              row.filename || row.id,
              Number(row.experiment_count) > 0
                ? `${num(row.experiment_count, 0)} historische Experimentreferenzen`
                : undefined,
            ),
            el("td", {
              class: "mono",
              text:
                typeof row.duration_seconds === "number" &&
                Number.isFinite(row.duration_seconds)
                  ? `${String(row.duration_seconds).replace(".", ",")} s`
                  : "Ungültige Dauer",
            }),
            el("td", { class: "mono", text: bytes(row.bytes) }),
            el("td", {}, [
              row.label
                ? pill(
                    `${row.label}${row.verified ? " · bestätigt" : " · vorläufig"}`,
                    row.verified ? "green" : "amber",
                  )
                : pill("Ohne Label"),
            ]),
          ]),
        ),
      );
      const skipped = arr(preview.skipped);
      $("#recording-cleanup-skipped-details").hidden = skipped.length === 0;
      $("#recording-cleanup-skipped-count").textContent =
        `${skipped.length} geschützte oder übersprungene Aufnahmen`;
      $("#recording-cleanup-skipped").replaceChildren(
        ...skipped.map((row) =>
          el("p", { text: `${text(row.id)}: ${text(row.reason)}` }),
        ),
      );
      try {
        state.cleanupPlan = cleanupDeletionPlan(preview);
      } catch (error) {
        state.cleanupPlan = null;
        $("#recording-cleanup-blocked").hidden = false;
        $("#recording-cleanup-blocked").textContent = errorMessage(error);
      }
      if (!rows.length && !preview.blocked_reason)
        $("#recording-cleanup-summary").textContent =
          "Keine löschbaren geschlossenen Aufnahmen unter 0,5 Sekunden gefunden.";
      updateCleanupControls();
    } catch (error) {
      if (
        sequence !== state.cleanupSequence ||
        !$("#recording-cleanup-dialog").open
      )
        return;
      state.cleanupPlan = null;
      $("#recording-cleanup-summary").textContent =
        "Die Vorschau konnte nicht geladen werden. Es ist keine Löschung freigegeben.";
      showError(error);
      updateCleanupControls();
    }
  }
  async function openCleanupDialog() {
    if (state.cleanupBusy) return;
    await openDialog("recording-cleanup-dialog");
    await loadCleanupPreview();
  }
  async function submitCleanupDeletion() {
    if (state.cleanupBusy) return;
    const plan = state.cleanupPlan;
    if (!plan || !plan.recording_ids.length) return;
    state.cleanupBusy = true;
    updateCleanupControls();
    try {
      const result = await api("/api/recordings/delete-batch", {
        method: "POST",
        body: plan,
      });
      const deleted = new Set(arr(result.deleted));
      deleted.forEach((id) => state.selectedRecordings.delete(id));
      $$("#experiment-recordings input").forEach((input) => {
        if (deleted.has(input.value)) input.checked = false;
      });
      updateExperimentRecordingSelection();
      scheduleReadiness();
      state.cleanupPlan = null;
      const skipped = arr(result.skipped),
        notice = $("#recording-cleanup-result");
      notice.hidden = false;
      notice.className = `notice${result.cleanup_warning ? " error" : ""}`;
      notice.textContent = `${num(result.count ?? deleted.size, 0)} Aufnahmen gelöscht · ${bytes(result.bytes_deleted)} freigegeben.${skipped.length ? ` ${skipped.length} übersprungen: ${skipped.map((row) => `${text(row.id)}: ${text(row.reason)}`).join("; ")}` : ""}${result.cleanup_warning ? ` Hinweis: ${result.cleanup_warning}` : ""}`;
      closeDialog($("#recording-cleanup-dialog"));
      try {
        await loadRecordings();
      } catch (error) {
        showError(error);
        notice.append(
          " Die Bibliotheksansicht konnte noch nicht aktualisiert werden.",
        );
      }
      toast(
        `${num(result.count ?? deleted.size, 0)} Kurzaufnahmen gelöscht${skipped.length ? `; ${skipped.length} übersprungen` : ""}.`,
      );
    } catch (error) {
      state.cleanupPlan = null;
      $("#recording-cleanup-blocked").hidden = false;
      $("#recording-cleanup-blocked").textContent =
        "Das Löschergebnis wurde nicht bestätigt. Vor einer weiteren Bestätigung die Vorschau aktualisieren; die Anfrage wird nicht automatisch wiederholt.";
      showError(error);
      try {
        await loadRecordings();
      } catch (_ignored) {}
    } finally {
      state.cleanupBusy = false;
      updateCleanupControls();
    }
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
  function topologyAvailable(
    topology,
    torchAvailable = state.status.hardware?.torch_available,
  ) {
    if (topology.family === "feature_lda") return true;
    if (
      torchAvailable === false ||
      topology.status === "requires_torch" ||
      topology.trainable === false
    )
      return false;
    return torchAvailable === true || topology.status === "available";
  }
  function validateTopologyIds(
    ids,
    catalog,
    torchAvailable = state.status.hardware?.torch_available,
  ) {
    if (
      !Array.isArray(ids) ||
      ids.length > 50 ||
      ids.some((id) => typeof id !== "string" || !id)
    )
      throw new Error("Höchstens 50 gültige Topologie-IDs auswählen.");
    const unique = [...new Set(ids)];
    for (const id of unique) {
      const topology = catalog.find((t) => t.id === id);
      if (!topology)
        throw new Error(
          "Die Topologien dieser Konfiguration zuerst im Tab Topologien laden.",
        );
      if (!topologyAvailable(topology, torchAvailable))
        throw new Error(
          `${topology.name || id}: nicht verfügbar. Für neuronale Ansätze wird PyTorch benötigt.`,
        );
    }
    return unique;
  }
  function selectedExperimentRecordingIds() {
    return $$("#experiment-recordings input:checked").map(
      (input) => input.value,
    );
  }
  function updateExperimentRecordingSelection() {
    const inputs = $$("#experiment-recordings input");
    const count = inputs.filter((input) => input.checked).length;
    inputs.forEach((input) => {
      input.disabled = !input.checked && count >= 1000;
    });
    $("#experiment-recording-selection-count").textContent =
      `${count} / 1000 ausgewählt · ${inputs.length} verfügbar`;
    $("#experiment-recordings-all").disabled = inputs.length === 0;
    $("#experiment-recordings-none").disabled = count === 0;
  }
  function setExperimentRecordingSelection(ids) {
    const selected = recordingSelectionWithAdditions(new Set(), ids, 1000);
    if (!selected) {
      showError(
        new Error(
          "Höchstens 1000 Aufnahmen je Experiment auswählen. Die bestehende Auswahl wurde nicht geändert.",
        ),
      );
      return false;
    }
    $$("#experiment-recordings input").forEach((input) => {
      input.checked = selected.has(input.value);
    });
    updateExperimentRecordingSelection();
    scheduleReadiness();
    return true;
  }
  function refreshTopologyAvailability() {
    let changed = false;
    for (const option of $("#experiment-topology").options) {
      const topology = state.topologies.find((t) => t.id === option.value);
      const disabled = !topology || !topologyAvailable(topology);
      if (option.disabled !== disabled) changed = true;
      option.disabled = disabled;
      if (disabled && option.selected) {
        option.selected = false;
        changed = true;
      }
    }
    updateTopologySelection();
    if (changed) scheduleReadiness();
  }
  function selectedTopologyIds() {
    return [...$("#experiment-topology").options]
      .filter((option) => option.selected && !option.disabled && option.value)
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
    let valid;
    try {
      valid = validateTopologyIds(ids, state.topologies);
    } catch (error) {
      showError(error);
      return false;
    }
    const selected = new Set(valid);
    for (const option of $("#experiment-topology").options)
      option.selected = !option.disabled && selected.has(option.value);
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
        state.topologies.find(
          (t) => t.id === "feature-lda" && topologyAvailable(t),
        ) || state.topologies.find((t) => topologyAvailable(t));
      if (reference) previous.add(reference.id);
    }
    $("#experiment-topology").replaceChildren(
      ...state.topologies.map((t) =>
        el("option", {
          value: t.id,
          selected: previous.has(t.id) && topologyAvailable(t),
          disabled: !topologyAvailable(t),
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
    if (setTopologySelection(ids) === false) return;
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
  function normalizeExecutionConfig(value) {
    const defaults = {
      mode: "auto",
      max_workers: 0,
      cpu_threads: 0,
      preprocessing_workers: 0,
      gpu_workers: 0,
    };
    if (value === undefined || value === null) return defaults;
    if (typeof value !== "object" || Array.isArray(value))
      throw new Error("execution muss ein JSON-Objekt sein.");
    if (Object.keys(value).some((key) => !Object.hasOwn(defaults, key)))
      throw new Error(
        "Unbekannte Ausführungsparameter; bitte Feldnamen prüfen.",
      );
    const execution = { ...defaults, ...value };
    if (!["auto", "serial", "parallel"].includes(execution.mode))
      throw new Error("Ausführung muss auto, serial oder parallel sein.");
    for (const [key, maximum] of Object.entries({
      max_workers: 8,
      cpu_threads: 32,
      preprocessing_workers: 8,
      gpu_workers: 4,
    }))
      if (
        !Number.isInteger(execution[key]) ||
        execution[key] < 0 ||
        execution[key] > maximum
      )
        throw new Error(
          `execution.${key}: ganze Zahl von 0 (automatisch) bis ${maximum} erforderlich.`,
        );
    return execution;
  }
  function executionConfigFromForm(form) {
    return normalizeExecutionConfig({
      mode: form.elements.execution_mode.value,
      ...Object.fromEntries(
        [
          "max_workers",
          "cpu_threads",
          "preprocessing_workers",
          "gpu_workers",
        ].map((key) => [
          key,
          finiteNumber(form.elements[`execution_${key}`].value),
        ]),
      ),
    });
  }
  function populateExecutionForm(execution) {
    const form = $("#experiment-form");
    for (const [key, value] of Object.entries(execution))
      form.elements[`execution_${key}`].value = String(value);
  }
  function normalizeOptimizationConfig(value, baseLength = 2048) {
    const base =
      Number.isInteger(baseLength) && baseLength >= 256 && baseLength <= 65536
        ? baseLength
        : 2048;
    const defaults = {
      enabled: false,
      method: "random",
      max_trials: 12,
      timeout_seconds: 300,
      objective: "score",
      search_space: {
        segment_lengths: [
          ...new Set([
            Math.max(256, Math.floor(base / 2)),
            base,
            Math.min(65536, base * 2),
          ]),
        ],
        learning_rate_min: 0.00001,
        learning_rate_max: 0.01,
        batch_sizes: [16, 32, 64],
        epochs: [5, 10, 20],
      },
    };
    if (value === undefined) return defaults;
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw new Error("optimization muss ein JSON-Objekt sein.");
    const allowed = new Set([
      "enabled",
      "method",
      "max_trials",
      "timeout_seconds",
      "objective",
      "search_space",
    ]);
    if (Object.keys(value).some((key) => !allowed.has(key)))
      throw new Error(
        "Unbekannte HPO-Konfigurationsfelder; bitte Schreibweise und Version prüfen.",
      );
    if (value.enabled !== undefined && typeof value.enabled !== "boolean")
      throw new Error("optimization.enabled muss ein boolescher Wert sein.");
    const o = { ...defaults, ...value, enabled: value.enabled ?? false };
    if (o.method !== "random")
      throw new Error(
        "Als automatische Suchmethode ist Random Search verfügbar.",
      );
    if (
      !Number.isInteger(o.max_trials) ||
      o.max_trials < 1 ||
      o.max_trials > 50
    )
      throw new Error("HPO: 1–50 Trials je Topologie auswählen.");
    if (
      typeof o.timeout_seconds !== "number" ||
      !Number.isFinite(o.timeout_seconds) ||
      o.timeout_seconds < 0 ||
      o.timeout_seconds > 86400
    )
      throw new Error(
        "HPO: Zeitbudget 0–86400 Sekunden je Topologie erforderlich.",
      );
    if (!["score", "macro_f1", "balanced_accuracy"].includes(o.objective))
      throw new Error("Ungültiges HPO-Optimierungsziel.");
    if (
      value.search_space !== undefined &&
      (!value.search_space ||
        typeof value.search_space !== "object" ||
        Array.isArray(value.search_space))
    )
      throw new Error("Ungültiger HPO-Suchraum.");
    const searchAllowed = new Set([
      "segment_lengths",
      "learning_rate_min",
      "learning_rate_max",
      "batch_sizes",
      "epochs",
    ]);
    if (
      Object.keys(value.search_space || {}).some(
        (key) => !searchAllowed.has(key),
      )
    )
      throw new Error(
        "Unbekannte HPO-Suchraumfelder; bitte Schreibweise prüfen.",
      );
    const space = { ...defaults.search_space, ...(value.search_space || {}) };
    for (const [key, min, max] of [
      ["segment_lengths", 256, 65536],
      ["batch_sizes", 4, 256],
      ["epochs", 1, 100],
    ]) {
      const list = space[key];
      if (
        !Array.isArray(list) ||
        list.length < 1 ||
        list.length > 8 ||
        list.some((n) => !Number.isInteger(n) || n < min || n > max)
      )
        throw new Error(
          `HPO ${key}: 1–8 ganze Zahlen zwischen ${min} und ${max} erforderlich.`,
        );
      space[key] = [...new Set(list)];
    }
    for (const key of ["learning_rate_min", "learning_rate_max"])
      if (
        typeof space[key] !== "number" ||
        !Number.isFinite(space[key]) ||
        space[key] < 0.00001 ||
        space[key] > 0.1
      )
        throw new Error(
          "HPO-Lernraten müssen zwischen 0,00001 und 0,1 liegen.",
        );
    if (space.learning_rate_min > space.learning_rate_max)
      throw new Error(
        "Die minimale HPO-Lernrate darf die maximale nicht überschreiten.",
      );
    return { ...o, search_space: space };
  }
  function optimizationBaseConfig(optimization) {
    const space = optimization.search_space;
    return {
      segment_length: space.segment_lengths[0],
      segment_lengths: [space.segment_lengths[0]],
      learning_rate: space.learning_rate_min,
      learning_rates: [space.learning_rate_min],
      batch_size: space.batch_sizes[0],
      batch_sizes: [space.batch_sizes[0]],
      epochs: space.epochs[0],
    };
  }
  function optimizationConfigFromForm(form, includeDisabled = false) {
    const enabled = form.elements.optimization_enabled.checked;
    if (!enabled && !includeDisabled) return { enabled: false };
    const field = (name) => form.elements[name].value;
    return normalizeOptimizationConfig({
      enabled,
      method: "random",
      max_trials: finiteNumber(field("optimization_max_trials")),
      timeout_seconds: finiteNumber(field("optimization_timeout_seconds")),
      objective: field("optimization_objective"),
      search_space: {
        segment_lengths: parseList(
          field("optimization_segment_lengths"),
          "HPO-Segmentlängen",
          true,
        ),
        learning_rate_min: finiteNumber(
          field("optimization_learning_rate_min"),
        ),
        learning_rate_max: finiteNumber(
          field("optimization_learning_rate_max"),
        ),
        batch_sizes: parseList(
          field("optimization_batch_sizes"),
          "HPO-Batchgrößen",
          true,
        ),
        epochs: parseList(field("optimization_epochs"), "HPO-Epochen", true),
      },
    });
  }
  function populateOptimizationForm(optimization) {
    const form = $("#experiment-form"),
      o = optimization;
    form.elements.optimization_enabled.checked = o.enabled;
    form.elements.optimization_objective.value = o.objective;
    form.elements.optimization_max_trials.value = String(o.max_trials);
    form.elements.optimization_timeout_seconds.value = String(
      o.timeout_seconds,
    );
    for (const [key, name] of [
      ["segment_lengths", "optimization_segment_lengths"],
      ["batch_sizes", "optimization_batch_sizes"],
      ["epochs", "optimization_epochs"],
    ])
      form.elements[name].value = o.search_space[key].join(", ");
    form.elements.optimization_learning_rate_min.value = String(
      o.search_space.learning_rate_min,
    );
    form.elements.optimization_learning_rate_max.value = String(
      o.search_space.learning_rate_max,
    );
    updateOptimizationControls();
  }
  function updateOptimizationControls() {
    const form = $("#experiment-form"),
      enabled = form.elements.optimization_enabled.checked;
    $("#optimization-fields").disabled = !enabled;
    $$("input,select", $("#optimization-fields")).forEach((input) => {
      input.required = true;
    });
    for (const name of [
      "segment_lengths",
      "learning_rates",
      "batch_sizes",
      "epochs",
    ])
      form.elements[name].disabled = enabled;
    const count = Number(form.elements.optimization_max_trials.value),
      seconds = Number(form.elements.optimization_timeout_seconds.value);
    $("#optimization-budget-note").textContent = enabled
      ? `${num(count, 0)} Trials je Topologie · ${seconds === 0 ? "ohne Zeitgrenze" : `${num(seconds, 1)} Sekunden Zeitbudget je Topologie`}. Manuelle Rasterwerte bleiben gespeichert, sind während der automatischen Suche jedoch nicht aktiv. Eine laufende Rechenstufe kann das Budget bis zum nächsten sicheren Prüfpunkt überschreiten.`
      : "Bei Aktivierung wird pro Topologie automatisch im angegebenen Suchraum gesucht. Ohne automatische Suche gelten die manuellen Rasterwerte.";
    $("#optimization-incompatible").hidden = !(
      enabled &&
      form.elements.source.value === "recordings" &&
      form.elements.evaluation_mode.value === "training_only"
    );
  }
  function optimizationBestConfig(optimization) {
    const c = optimization?.best_config;
    if (!c || typeof c !== "object")
      throw new Error(
        "Für diese Suche liegt noch keine gültige beste Konfiguration vor.",
      );
    for (const [key, min, max] of [
      ["segment_length", 256, 65536],
      ["batch_size", 4, 256],
      ["epochs", 1, 100],
    ])
      if (!Number.isInteger(c[key]) || c[key] < min || c[key] > max)
        throw new Error(
          "Die beste HPO-Konfiguration ist unvollständig oder ungültig.",
        );
    if (
      typeof c.learning_rate !== "number" ||
      !Number.isFinite(c.learning_rate) ||
      c.learning_rate < 0.00001 ||
      c.learning_rate > 0.1
    )
      throw new Error(
        "Die beste HPO-Konfiguration enthält keine gültige Lernrate.",
      );
    return {
      segment_length: c.segment_length,
      learning_rate: c.learning_rate,
      batch_size: c.batch_size,
      epochs: c.epochs,
    };
  }
  function applyBestOptimizationConfig(optimization) {
    const values = optimizationBestConfig(optimization),
      form = $("#experiment-form");
    form.elements.optimization_enabled.checked = false;
    form.elements.segment_lengths.value = String(values.segment_length);
    form.elements.learning_rates.value = String(values.learning_rate);
    form.elements.batch_sizes.value = String(values.batch_size);
    form.elements.epochs.value = String(values.epochs);
    updateOptimizationControls();
    scheduleReadiness();
    form.elements.segment_lengths.focus();
    toast(
      "Beste Hyperparameter ins manuelle Setup übernommen. Topologieauswahl bleibt erhalten; es wurde kein Experiment gestartet.",
    );
  }
  function experimentConfig({
    allowEmptyTopologies = false,
    includeDisabledOptimization = false,
  } = {}) {
    const form = $("#experiment-form");
    const f = new FormData(form);
    const optimization = optimizationConfigFromForm(
      form,
      includeDisabledOptimization,
    );
    const activeSearch = optimization.enabled && !includeDisabledOptimization;
    const activeBase = activeSearch
      ? optimizationBaseConfig(optimization)
      : null;
    const lengths = activeSearch
      ? activeBase.segment_lengths
      : parseList(form.elements.segment_lengths.value, "Segmentlängen", true);
    const topologyIds = selectedTopologyIds();
    if (
      (!allowEmptyTopologies && topologyIds.length < 1) ||
      topologyIds.length > 50
    )
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
      epochs: activeSearch
        ? activeBase.epochs
        : Number(form.elements.epochs.value),
      batch_size: activeSearch
        ? activeBase.batch_size
        : parseList(form.elements.batch_sizes.value, "Batchgrößen", true)[0],
      batch_sizes: activeSearch
        ? activeBase.batch_sizes
        : parseList(form.elements.batch_sizes.value, "Batchgrößen", true),
      learning_rate: activeSearch
        ? activeBase.learning_rate
        : parseList(form.elements.learning_rates.value, "Lernraten")[0],
      learning_rates: activeSearch
        ? activeBase.learning_rates
        : parseList(form.elements.learning_rates.value, "Lernraten"),
      experiment_notes: String(f.get("experiment_notes") || ""),
      device: f.get("device"),
      use_priors: f.has("use_priors"),
    };
    config.optimization = optimization;
    config.execution = executionConfigFromForm(form);
    if (
      config.optimization.enabled &&
      config.evaluation_mode === "training_only"
    )
      throw new Error(
        "Automatische HPO benötigt eine unabhängige Bewertung; im Modus „Nur trainieren“ ist sie nicht verfügbar.",
      );
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
      config.recording_ids = selectedExperimentRecordingIds();
    return config;
  }
  function scheduleReadiness() {
    ++state.readinessSequence;
    state.datasetReadiness = null;
    $("#dataset-readiness").replaceChildren(
      el("p", {
        class: "field-help",
        text: "Datengrundlage wird aktualisiert …",
      }),
    );
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
                ? pill(
                    `Berichtsmodell · ${selectionObjectiveLabel(report)}`,
                    "cyan",
                  )
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
            : `Die Rangfolge verwendet ausschließlich Validierungsdaten. Das Berichtsmodell folgt ${selectionObjectiveLabel(report)}; die Top-3-Empfehlung folgt dem Gesamtscore. Finaltestwerte bestimmen weder Rang noch Empfehlung.`,
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
  function selectionObjectiveLabel(report) {
    const objective =
      report.selection_objective ||
      (report.config?.optimization?.enabled
        ? report.config.optimization.objective
        : "macro_f1");
    return (
      {
        score: "Val-Score",
        macro_f1: "Val-F1",
        balanced_accuracy: "Val-bal. Genauigkeit",
      }[objective] || "Validierung"
    );
  }
  function partialOptimizationReports(experiment, report) {
    const found = new Map();
    const direct = report.optimization || experiment.optimization;
    if (direct?.enabled === true)
      found.set("primary", {
        label: "Teilweise abgeschlossene Suche",
        optimization: direct,
      });
    for (const entry of arr(experiment.partial_reports)) {
      const data = entry.report;
      const optimization =
        entry.kind === "optimization" ? data : data?.optimization;
      if (optimization?.enabled !== true) continue;
      const key = String(entry.path || "partial")
        .replace(/\/(optimization|report)\.json$/, " ")
        .trim();
      if (entry.kind === "optimization" || !found.has(key))
        found.set(key, {
          label:
            data?.topology?.name ||
            optimization.topology_id ||
            String(entry.path || "Teilbericht"),
          optimization,
        });
    }
    return [...found.values()];
  }
  function executionModeLabel(mode) {
    return (
      { auto: "Automatisch", serial: "Seriell", parallel: "Parallel" }[mode] ||
      text(mode)
    );
  }
  function executionPhaseLabel(phase) {
    return (
      {
        queued: "Warteschlange",
        initializing: "Initialisierung",
        starting: "Worker startet",
        prepare: "Vorbereitung / Vorverarbeitung",
        evaluate: "Auswertung",
        compute: "Berechnung",
        execution: "Koordination",
        waiting_for_cpu_benchmark: "Warten auf CPU-Benchmarkfreigabe",
        paused_for_cpu_benchmark: "Für CPU-Benchmark pausiert",
        preprocessing: "CPU-Vorverarbeitung",
        device_transfer: "Geräteübertragung",
        training: "Training",
        train: "Training",
        validation: "Validierung",
        checkpoint: "Modell speichern",
        validation_grid: "Validierungsraster",
        cpu_benchmark: "CPU-Inferenzbenchmark",
        benchmark_wait: "Warten auf Benchmarkfreigabe",
        optimize: "HPO-Suche",
        complete: "Abgeschlossen",
        completed: "Abgeschlossen",
      }[phase] || text(phase, "Noch keine Phase gemeldet")
    );
  }
  function phaseTimingRows(report) {
    const timings = report?.phase_timings || {};
    const labels = {
      source_loading_seconds: "Quellen laden / Signale vorbereiten",
      fingerprint_seconds: "Datenfingerprints berechnen",
      preprocessing_seconds: "Vorverarbeitung",
      device_transfer_seconds: "Übertragung aufs Trainingsgerät",
      training_epoch_seconds: "Trainingsepochen",
      validation_seconds: "Validierung",
      checkpoint_seconds: "Modell speichern",
      validation_grid_seconds: "Robustheitsraster der Validierung",
      cpu_benchmark_seconds: "CPU-Inferenzbenchmark",
      benchmark_wait_seconds: "Warten auf Benchmarkfreigabe",
    };
    return Object.entries(labels)
      .filter(
        ([key]) =>
          typeof timings[key] === "number" &&
          Number.isFinite(timings[key]) &&
          timings[key] >= 0,
      )
      .map(([key, label]) => ({ key, label, seconds: timings[key] }));
  }
  function phaseTime(value) {
    return typeof value !== "number" || !Number.isFinite(value) || value < 0
      ? "—"
      : value < 1
        ? `${num(value * 1000, 2)} ms`
        : duration(value);
  }
  function trainingPhasesNode(report, compact = false) {
    const rows = phaseTimingRows(report),
      training = report.training_execution || {};
    const details = el(
      "details",
      { class: `training-phase-details${compact ? " compact" : ""}` },
      [
        el("summary", {
          text: compact
            ? "Ressourcen und Abschnittszeiten"
            : "Tatsächliche Trainingsressourcen und Abschnittszeiten",
        }),
        el("div", { class: "training-execution-facts" }, [
          ...[
            ["TRAININGSGERÄT", training.device],
            ["TRAININGSBATCH", training.training_batch_size],
            ["VALIDIERUNGSBATCH", training.validation_batch_size],
            ["CPU-THREADS / WORKER", training.cpu_threads],
            ["VORVERARBEITER / WORKER", training.preprocessing_workers],
            ["CHECKPOINT-GERÄT", training.checkpoint_device],
            ["TRAININGSBATCHES", training.training_batches],
            ["VALIDIERUNGSBATCHES", training.validation_batches],
            ["LOSS-KOPIEN ZUR CPU", training.loss_host_transfers],
            ["VALIDIERUNGSÜBERTRAGUNGEN", training.validation_tensor_transfers],
          ]
            .filter(([, value]) => value !== undefined && value !== null)
            .map(([label, value]) =>
              el("div", {}, [
                el("span", { class: "metric-label", text: label }),
                el("strong", { text: text(value) }),
              ]),
            ),
        ]),
        el("p", {
          class: "field-help training-phase-note",
          text: "Gemessene Abschnittszeiten dieses Modells beziehungsweise Trials. Bei parallelen Workern überlappen Zeiten; ihre Summe ist keine Gesamtwandzeit.",
        }),
        Number.isFinite(report.metrics?.training_wall_seconds) &&
        Number.isFinite(report.metrics?.managed_training_pause_seconds)
          ? el("p", {
              class: "field-help training-phase-note",
              text: `Trainingswandzeit ${phaseTime(report.metrics.training_wall_seconds)} · davon verwaltete Koordinationspausen ${phaseTime(report.metrics.managed_training_pause_seconds)} · aktive Trainingszeit ${phaseTime(report.metrics.training_seconds)}. Der Trainingsanteil des Scores verwendet die um diese Pausen bereinigte Zeit.`,
            })
          : null,
        report.phase_timing_scope
          ? el("p", {
              class: "field-help training-phase-note",
              text: `Messbereich: ${typeof report.phase_timing_scope === "string" ? report.phase_timing_scope : JSON.stringify(report.phase_timing_scope)}`,
            })
          : null,
      ],
    );
    details.append(
      rows.length
        ? el("div", { class: "table-scroll" }, [
            el("table", { class: "data-table training-phases-table" }, [
              el("thead", {}, [
                el("tr", {}, [
                  el("th", { text: "Abschnitt" }),
                  el("th", { text: "Gemessene Zeit" }),
                ]),
              ]),
              el(
                "tbody",
                {},
                rows.map((row) =>
                  el("tr", {}, [
                    el("td", { text: row.label }),
                    el("td", { class: "mono", text: phaseTime(row.seconds) }),
                  ]),
                ),
              ),
            ]),
          ])
        : el("p", {
            class: "field-help training-phase-note",
            text: "Für diesen Bericht wurden noch keine Abschnittszeiten gemeldet.",
          }),
    );
    return details;
  }
  function executionProgressNode(experiment) {
    const progress = experiment.execution_progress;
    if (!progress || typeof progress !== "object") return null;
    const current = ["queued", "running", "cancelling"].includes(
      experiment.status,
    );
    const workers = arr(progress.workers);
    return el(
      "section",
      {
        class: "execution-progress-panel",
        "aria-label": "Gemeldete Worker und Verarbeitungsphasen",
      },
      [
        el("div", { class: "execution-report-heading" }, [
          el("h3", {
            text: current
              ? "Worker und laufende Phasen"
              : "Letzter gemeldeter Workerstand",
          }),
          pill(
            `${num(progress.active_workers, 0)} aktive Worker`,
            current ? "cyan" : "",
          ),
        ]),
        el("p", {
          class: "field-help execution-report-note",
          text: `Jobs: ${num(progress.completed_jobs, 0)} / ${num(progress.total_jobs, 0)} abgeschlossen · ${num(progress.queued_jobs, 0)} in der Warteschlange. Workerzahlen stammen vom Coordinator; sie sind keine Anzahl ausgewählter Topologien.`,
        }),
        workers.length
          ? el("div", { class: "table-scroll" }, [
              el("table", { class: "data-table execution-workers-table" }, [
                el("thead", {}, [
                  el(
                    "tr",
                    {},
                    [
                      "Job / Topologie",
                      "Trial",
                      "Status / Phase",
                      "Trainingsgerät",
                      "Fortschritt",
                      "Prozess",
                    ].map((label) => el("th", { text: label })),
                  ),
                ]),
                el(
                  "tbody",
                  {},
                  workers.slice(0, 64).map((worker) => {
                    const percentage =
                      typeof worker.progress === "number" &&
                      Number.isFinite(worker.progress)
                        ? `${num(Math.max(0, Math.min(100, worker.progress <= 1 ? worker.progress * 100 : worker.progress)), 1)} %`
                        : "—";
                    const topology = state.topologies.find(
                      (t) => t.id === worker.topology_id,
                    );
                    return el("tr", {}, [
                      detailCell(
                        topology?.name || worker.topology_id,
                        worker.job_index !== undefined
                          ? `Job ${worker.job_index}`
                          : undefined,
                      ),
                      el("td", { class: "mono", text: text(worker.trial) }),
                      detailCell(
                        statusName(worker.status),
                        executionPhaseLabel(worker.phase),
                      ),
                      el("td", { class: "mono", text: text(worker.device) }),
                      el("td", { class: "mono", text: percentage }),
                      el("td", { class: "mono", text: text(worker.pid) }),
                    ]);
                  }),
                ),
              ]),
            ])
          : el("p", {
              class: "field-help execution-report-note",
              text: "Noch keine einzelnen Worker gemeldet.",
            }),
        workers.length > 64
          ? el("p", {
              class: "field-help execution-report-note",
              text: `64 von ${workers.length} Worker-Einträgen angezeigt.`,
            })
          : null,
      ],
    );
  }
  function resourceMetricSummary(metric) {
    const count =
      Number.isInteger(metric?.sample_count) && metric.sample_count > 0
        ? metric.sample_count
        : 0;
    const measured = (value) =>
      count > 0 &&
      typeof value === "number" &&
      Number.isFinite(value) &&
      value >= 0
        ? value
        : null;
    return {
      count,
      mean: measured(metric?.mean),
      maximum: measured(metric?.maximum),
    };
  }
  function resourceHistoryNode(summary) {
    const cpu = summary.cpu || {},
      gpu = arr(summary.gpu_devices);
    const percentage = (value) => (value === null ? "—" : `${num(value, 1)} %`);
    const cpuMetrics = [
      ["CPU · Gesamtsystem", cpu.system_percent],
      ["CPU · App + Worker je logischem Kern", cpu.process_percent],
      ["CPU · App / Gesamtsystem", cpu.app_system_percent],
    ];
    const stamp = (value) =>
      typeof value === "string" && Number.isFinite(Date.parse(value))
        ? new Date(value).toISOString().replace("T", " ").replace("Z", " UTC")
        : "—";
    return el(
      "section",
      {
        class: "resource-history-panel",
        "aria-label": "Historische CPU- und GPU-Messreihe dieses Experiments",
      },
      [
        el("div", { class: "execution-report-heading" }, [
          el("div", {}, [
            el("span", {
              class: "panel-kicker",
              text: "GEMESSEN WÄHREND DIESES EXPERIMENTS",
            }),
            el("h3", { text: "CPU / GPU im Versuchszeitraum" }),
          ]),
          pill(`${num(summary.sample_count, 0)} Stichproben`),
        ]),
        el("p", {
          class: "field-help execution-report-note",
          text: `${stamp(summary.first_sample_utc)} bis ${stamp(summary.last_sample_utc)}. Werte umfassen den gesamten Versuch einschließlich CPU- und Wartephasen; sie sind keine reine GPU-Trainingsauslastung.`,
        }),
        el("div", { class: "table-scroll" }, [
          el("table", { class: "data-table resource-history-table" }, [
            el("thead", {}, [
              el(
                "tr",
                {},
                ["CPU-Messgröße", "Mittelwert", "Maximum", "Messpunkte"].map(
                  (label) => el("th", { text: label }),
                ),
              ),
            ]),
            el(
              "tbody",
              {},
              cpuMetrics.map(([label, metric]) => {
                const value = resourceMetricSummary(metric);
                return el("tr", {}, [
                  el("td", { text: label }),
                  el("td", { class: "mono", text: percentage(value.mean) }),
                  el("td", { class: "mono", text: percentage(value.maximum) }),
                  el("td", { class: "mono", text: num(value.count, 0) }),
                ]);
              }),
            ),
          ]),
        ]),
        gpu.length
          ? el("div", { class: "table-scroll" }, [
              el(
                "table",
                {
                  class: "data-table resource-history-table gpu-history-table",
                },
                [
                  el("thead", {}, [
                    el(
                      "tr",
                      {},
                      [
                        "GPU-Gerät",
                        "Auslastung Ø / max.",
                        "VRAM belegt Ø / max.",
                        "VRAM gesamt max.",
                        "GPU-Messpunkte",
                      ].map((label) => el("th", { text: label })),
                    ),
                  ]),
                  el(
                    "tbody",
                    {},
                    gpu.map((device) => {
                      const utilization = resourceMetricSummary(
                          device.utilization_percent,
                        ),
                        used = resourceMetricSummary(device.memory_used_bytes),
                        total = resourceMetricSummary(
                          device.memory_total_bytes,
                        );
                      return el("tr", {}, [
                        detailCell(
                          device.name,
                          device.index !== undefined
                            ? `GPU ${device.index}`
                            : undefined,
                        ),
                        el("td", {
                          class: "mono",
                          text: `${percentage(utilization.mean)} / ${percentage(utilization.maximum)}`,
                        }),
                        el("td", {
                          class: "mono",
                          text: `${bytes(used.mean)} / ${bytes(used.maximum)}`,
                        }),
                        el("td", { class: "mono", text: bytes(total.maximum) }),
                        el("td", {
                          class: "mono",
                          text: num(utilization.count, 0),
                        }),
                      ]);
                    }),
                  ),
                ],
              ),
            ])
          : el("p", {
              class: "field-help execution-report-note",
              text: "Im Versuchszeitraum lagen keine GPU-Gerätemessungen vor.",
            }),
        el("p", {
          class: "field-help execution-report-note",
          text: "GPU-Werte beziehen sich auf das gesamte Gerät einschließlich anderer Anwendungen. Der rohe App-Prozessbaumwert kann über 100 % liegen: Ein logischer CPU-Kern entspricht 100 %. Kurzzeitige Rechenbursts können zwischen Messpunkten fehlen.",
        }),
        summary.sampling_note
          ? el("p", {
              class: "field-help execution-report-note",
              text: text(summary.sampling_note),
            })
          : null,
        el("details", { class: "result-details" }, [
          el("summary", {
            text: "Messumfang und vollständige Ressourcenstatistik",
          }),
          el("pre", { text: JSON.stringify(summary, null, 2) }),
        ]),
      ],
    );
  }
  function executionReportNode(execution) {
    const requested = execution.requested || {},
      resolved = execution.resolved || {};
    const options = [
      ["Ausführungsmodus", "mode"],
      ["Maximale Trainingworker", "max_workers"],
      ["CPU-Threads je Trainingworker", "cpu_threads"],
      ["Vorverarbeiter je Trainingworker", "preprocessing_workers"],
      ["Maximale GPU-Worker", "gpu_workers"],
    ];
    const requestedValue = (key) =>
      key === "mode"
        ? executionModeLabel(requested[key])
        : requested[key] === 0
          ? "Automatisch"
          : num(requested[key], 0);
    return el(
      "section",
      {
        class: "execution-report-panel",
        "aria-label": "Tatsächlich verwendeter Ausführungsplan",
      },
      [
        el("div", { class: "execution-report-heading" }, [
          el("div", {}, [
            el("span", {
              class: "panel-kicker",
              text: "AUSFÜHRUNGSPLAN / GEMESSENE GESAMTDAUER",
            }),
            el("h3", { text: "Angefordert und tatsächlich verwendet" }),
          ]),
          pill(
            { topologies: "Jobtyp: Topologien", trials: "Jobtyp: Suchtrials" }[
              execution.axis
            ] || text(execution.axis, "Ausführung"),
          ),
        ]),
        el("div", { class: "execution-report-metrics" }, [
          metric(
            "JOB-WANDZEIT",
            phaseTime(execution.wall_seconds),
            "Verstrichene Dauer des Coordinatorlaufs",
          ),
          metric(
            "WORKER-SPITZE",
            num(execution.peak_active_workers, 0),
            "Höchste gemeldete Zahl gleichzeitig aktiver Worker",
          ),
          metric(
            "JOBS ABGESCHLOSSEN",
            num(execution.completed_jobs, 0),
            `${num(execution.failed_jobs, 0)} fehlgeschlagen · ${num(execution.timed_out_jobs, 0)} Zeitlimit · ${num(execution.cancelled_jobs, 0)} abgebrochen`,
          ),
        ]),
        el("div", { class: "table-scroll" }, [
          el("table", { class: "data-table execution-plan-table" }, [
            el("thead", {}, [
              el(
                "tr",
                {},
                ["Einstellung", "Angefordert", "Aufgelöst"].map((label) =>
                  el("th", { text: label }),
                ),
              ),
            ]),
            el(
              "tbody",
              {},
              options.map(([label, key]) =>
                el("tr", {}, [
                  el("td", { text: label }),
                  el("td", { text: requestedValue(key) }),
                  el("td", {
                    text:
                      key === "mode"
                        ? executionModeLabel(resolved[key])
                        : num(resolved[key], 0),
                  }),
                ]),
              ),
            ),
          ]),
        ]),
        el("p", {
          class: "field-help execution-report-note",
          text: `CPU-Budget ${num(resolved.cpu_budget, 0)} · RAM-Budget ${bytes(resolved.ram_budget_bytes)} · geschätzt ${bytes(resolved.estimated_ram_per_worker_bytes)} je Worker. GPU-Speicherbudget ${bytes(resolved.gpu_memory_budget_bytes)} · geschätzt ${bytes(resolved.estimated_gpu_per_worker_bytes)} je GPU-Worker.`,
        }),
        ...arr(execution.warnings).map((warning) =>
          el("p", { class: "execution-warning", text: text(warning) }),
        ),
        el("p", {
          class: "field-help execution-report-note",
          text: "Angeforderte Obergrenzen und tatsächliche Auflösung können abweichen. Abschnitts- und HPO-Zeiten mehrerer Worker sind summierte Arbeitszeiten und überlappen bei Parallelität; mit der Job-Wandzeit oben vergleichen. Fremdlast auf dem Rechner ist dadurch nicht ausgeschlossen.",
        }),
        el("details", { class: "result-details" }, [
          el("summary", {
            text: "Ressourcen-Snapshot und vollständiger Ausführungsplan",
          }),
          el("pre", { text: JSON.stringify(execution, null, 2) }),
        ]),
      ],
    );
  }
  function optimizationSummaryNode(summary) {
    return el(
      "section",
      {
        class: "optimization-report optimization-all-summary",
        "aria-label": "Gesamtkosten der automatischen Suche",
      },
      [
        el("div", { class: "optimization-report-heading" }, [
          el("div", {}, [
            el("span", {
              class: "panel-kicker",
              text: "GESAMTER TOPOLOGIEVERGLEICH",
            }),
            el("h3", {
              text: `Automatische Suche · ${num(summary.topologies, 0)} Topologien`,
            }),
          ]),
        ]),
        el("div", { class: "optimization-report-metrics" }, [
          metric(
            "SUCHZEIT · SUMMIERT",
            duration(summary.elapsed_seconds),
            "Summe der Suchläufe; parallel überlappend",
          ),
          metric(
            "TRAINING · SUMMIERT",
            duration(summary.total_training_seconds),
            "Summierte Workerzeit aller Suchtrials",
          ),
          metric(
            "TRIALS · ALLE",
            num(summary.completed_trials, 0),
            `${num(summary.failed_trials, 0)} fehlgeschlagen · ${num(summary.timed_out_trials, 0)} Zeitlimit`,
          ),
        ]),
        el("p", {
          class: "field-help optimization-report-note",
          text: `${num(summary.test_evaluations, 0)} Finaltestauswertungen nach der Suche. Die folgenden Einzelberichte zeigen die Parameter und Suchkosten jeweils einer Topologie.`,
        }),
      ],
    );
  }
  function optimizationReportNode(optimization) {
    const trials = arr(optimization.trials),
      objective =
        {
          score: "Gesamtscore",
          macro_f1: "Macro-F1",
          balanced_accuracy: "Ausgewogene Genauigkeit",
        }[optimization.objective] || text(optimization.objective);
    const status =
      {
        max_trials: "Trialbudget erreicht",
        timeout: "Zeitbudget erreicht",
        search_space_exhausted: "Suchraum ausgeschöpft",
        all_trials_failed: "Alle Trials fehlgeschlagen",
        cancelled: "Suche abgebrochen",
        user_cancellation: "Durch Benutzer abgebrochen",
        interrupted: "Suche unterbrochen",
      }[optimization.stop_reason] ||
      text(optimization.stop_reason, "Zwischenstand");
    let best = null;
    try {
      best = optimizationBestConfig(optimization);
    } catch (_ignored) {}
    const apply = button(
      "Beste Werte ins Setup übernehmen",
      () => action(async () => applyBestOptimizationConfig(optimization)),
      "button button-quiet",
    );
    apply.disabled = !best;
    const node = el(
      "section",
      {
        class: "optimization-report",
        "aria-label": "Automatische Hyperparameteroptimierung",
      },
      [
        el("div", { class: "optimization-report-heading" }, [
          el("div", {}, [
            el("span", {
              class: "panel-kicker",
              text: "AUTOMATISCHE HYPERPARAMETERSUCHE",
            }),
            el("h3", { text: "Random Search · Validierung" }),
          ]),
          pill(status, optimization.stop_reason === "timeout" ? "amber" : ""),
        ]),
        el("p", {
          class: "field-help optimization-report-note",
          text: `Ziel: ${objective} · Seed ${text(optimization.seed)} · Budget ${text(optimization.max_trials)} Trials / ${optimization.timeout_seconds === 0 ? "ohne Zeitgrenze" : `${duration(optimization.timeout_seconds)}`} je Topologie. Suchtrials verwenden keine Finaltestwerte; Finaltestauswertungen: ${text(optimization.test_evaluations)}.`,
        }),
        el("div", { class: "optimization-report-metrics" }, [
          metric(
            "SUCHDAUER GESAMT",
            duration(optimization.elapsed_seconds),
            "Alle Trials und Suchaufwand",
          ),
          metric(
            "TRAINING GESAMT",
            duration(optimization.total_training_seconds),
            "Summierte Trainingszeit aller Suchtrials",
          ),
          metric(
            "TRIALS ABGESCHLOSSEN",
            num(
              optimization.completed_trials ??
                trials.filter((t) => t.status === "completed").length,
              0,
            ),
            `${num(optimization.failed_trials ?? trials.filter((t) => t.status === "failed").length, 0)} fehlgeschlagen · ${num(optimization.timed_out_trials ?? trials.filter((t) => t.status === "timed_out").length, 0)} Zeitlimit`,
          ),
        ]),
        el("div", { class: "optimization-best-settings" }, [
          el("strong", {
            text: best
              ? `Beste Konfiguration · Trial ${text(optimization.best_trial)}`
              : "Noch kein gültiger Gewinner",
          }),
          best
            ? el("p", {
                text: `${num(best.segment_length, 0)} Samples · Lernrate ${best.learning_rate} · Batch ${num(best.batch_size, 0)} · ${num(best.epochs, 0)} Epochen`,
              })
            : null,
          apply,
          el("span", {
            class: "field-help",
            text: "Übernimmt nur Hyperparameter und schaltet auf manuelle Werte um. Die Topologieauswahl bleibt erhalten; es wird kein Experiment gestartet.",
          }),
        ]),
      ],
    );
    if (trials.length)
      node.append(
        el("details", { class: "optimization-trials" }, [
          el("summary", { text: `Alle ${trials.length} Suchtrials ansehen` }),
          el("div", { class: "table-scroll" }, [
            el("table", { class: "data-table" }, [
              el("thead", {}, [
                el(
                  "tr",
                  {},
                  [
                    "Trial",
                    "Status",
                    "Samples",
                    "Lernrate",
                    "Batch",
                    "Epochen",
                    "Zielfunktionswert",
                    "Val. F1",
                    "Val. bal. Genauigkeit",
                    "Score",
                    "Trialzeit",
                  ].map((label) => el("th", { text: label })),
                ),
              ]),
              el(
                "tbody",
                {},
                trials.map((trial) => {
                  const config = trial.config || {},
                    validation = trial.validation || {};
                  return el(
                    "tr",
                    {
                      class:
                        trial.trial === optimization.best_trial
                          ? "optimization-best-trial"
                          : "",
                    },
                    [
                      el("td", { class: "mono", text: text(trial.trial) }),
                      el("td", {}, [
                        el("strong", {
                          text:
                            {
                              completed: "Abgeschlossen",
                              failed: "Fehlgeschlagen",
                              timed_out: "Zeitlimit",
                            }[trial.status] || text(trial.status),
                        }),
                        trial.error
                          ? el("small", { text: text(trial.error) })
                          : null,
                        trial.training_execution ||
                        phaseTimingRows(trial).length
                          ? trainingPhasesNode(trial, true)
                          : null,
                      ]),
                      ...[
                        config.segment_length,
                        config.learning_rate,
                        config.batch_size,
                        config.epochs,
                      ].map((value) =>
                        el("td", { class: "mono", text: text(value) }),
                      ),
                      el("td", {
                        class: "mono",
                        text: num(trial.objective_value, 5),
                      }),
                      el("td", {
                        class: "mono",
                        text: num(validation.macro_f1, 4),
                      }),
                      el("td", {
                        class: "mono",
                        text: percent(validation.balanced_accuracy),
                      }),
                      el("td", {
                        class: "mono",
                        text: num(trial.score?.total, 2),
                      }),
                      el("td", {
                        class: "mono",
                        text: duration(trial.elapsed_seconds),
                      }),
                    ],
                  );
                }),
              ),
            ]),
          ]),
        ]),
      );
    const excluded = arr(optimization.excluded_segment_lengths);
    if (excluded.length)
      node.append(
        el("details", { class: "result-details" }, [
          el("summary", {
            text: `${excluded.length} ausgeschlossene Segmentlängen`,
          }),
          el(
            "div",
            { class: "optimization-excluded" },
            excluded.map((item) =>
              el("p", {
                text: `${text(item.segment_length)} Samples: ${arr(item.reasons)
                  .map((reason) =>
                    typeof reason === "string"
                      ? reason
                      : JSON.stringify(reason),
                  )
                  .join(" · ")}`,
              }),
            ),
          ),
        ]),
      );
    node.append(
      el("details", { class: "result-details" }, [
        el("summary", { text: "Suchraum und vollständige HPO-Metadaten" }),
        el("pre", { text: JSON.stringify(optimization, null, 2) }),
      ]),
    );
    return node;
  }
  function updateExperimentBundleExport(experiment) {
    const identifier =
      typeof experiment.id === "string" && experiment.id
        ? experiment.id
        : state.selectedExperiment;
    const available = typeof identifier === "string" && identifier.length > 0;
    const link = $("#experiment-report-bundle");
    link.hidden = !available;
    $("#experiment-report-bundle-note").hidden = !available;
    if (available)
      link.href = `/api/experiments/${encodeURIComponent(identifier)}/report-bundle`;
    else link.removeAttribute("href");
  }
  function renderExperimentResult(experiment) {
    updateExperimentBundleExport(experiment);
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
      const lastWorkers = executionProgressNode(experiment);
      if (lastWorkers) body.append(lastWorkers);
      if (experiment.resource_telemetry)
        body.append(resourceHistoryNode(experiment.resource_telemetry));
      for (const partial of partialOptimizationReports(experiment, report))
        body.append(
          el("div", {}, [
            el("h3", {
              class: "optimization-partial-title",
              text: partial.label,
            }),
            optimizationReportNode(partial.optimization),
          ]),
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
          experiment.optimization_trial !== undefined
            ? el("p", {
                class: "field-help",
                text: `Automatische Suche · Trial ${text(experiment.optimization_trial)} / ${text(experiment.optimization_max_trials)}`,
              })
            : null,
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
      const workers = executionProgressNode(experiment);
      if (workers) body.append(workers);
      return;
    }
    const n = el("div");
    if (fullReport.execution)
      n.append(executionReportNode(fullReport.execution));
    if (fullReport.resource_telemetry)
      n.append(resourceHistoryNode(fullReport.resource_telemetry));
    if (fullReport.optimization_summary?.scope === "all_topologies")
      n.append(optimizationSummaryNode(fullReport.optimization_summary));
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
    if (report.training_execution || phaseTimingRows(report).length)
      n.append(trainingPhasesNode(report));
    if (report.optimization?.enabled === true && !trainingOnly(report))
      n.append(optimizationReportNode(report.optimization));
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
          report.optimization?.enabled
            ? "TRAINING · GEWINNER"
            : "TRAININGSDAUER",
          duration(metrics.training_seconds),
          report.optimization?.enabled
            ? "Nur der ausgewählte Trial"
            : "Gemessene Wandzeit",
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
  const ANALYSIS_LEVELS = {
    waveform_family: "Signalform / Übertragungsverfahren",
    modulation: "Modulation",
    protocol: "Protokoll",
    service: "Funkdienst",
    emitter: "Emitter",
  };
  function analysisExpertSelection(models, drafts) {
    const known = new Set(arr(models).map((m) => String(m.id || m.model_id)));
    return arr(drafts)
      .map((d) => ({
        model_id: String(d.model_id || ""),
        enabled: d.enabled === true,
        level: Object.hasOwn(ANALYSIS_LEVELS, d.level) ? d.level : "modulation",
        families: [
          ...new Set(
            arr(d.families)
              .map((v) => String(v).trim())
              .filter(Boolean),
          ),
        ],
        available: known.has(String(d.model_id)),
      }))
      .filter((d) => d.model_id);
  }
  function analysisSettingsPayload(values, experts) {
    const number = (key) => {
      const value = finiteNumber(values[key]);
      if (value === null)
        throw new Error(`Analyse: gültige Zahl für ${key} erforderlich.`);
      return value;
    };
    const start = number("start_frequency_hz"),
      stop = number("stop_frequency_hz");
    if (start < 0 || stop > 6e9 || stop <= start)
      throw new Error(
        "Analysebereich: Beginn und Ende zwischen 0 und 6 000 MHz, Ende größer als Beginn.",
      );
    const maxSignals = number("max_signals_per_cycle"),
      interval = number("interval_seconds");
    const minScore = number("min_model_score"),
      margin = number("min_margin");
    const budget = number("budget_ms"),
      maxModels = number("max_models");
    if (
      !Number.isInteger(maxSignals) ||
      maxSignals < 1 ||
      maxSignals > 16 ||
      interval < 0.1 ||
      interval > 30 ||
      !Number.isInteger(maxModels) ||
      maxModels < 0 ||
      maxModels > 8 ||
      budget < 0 ||
      budget > 60000 ||
      minScore < 0.5 ||
      minScore > 1 ||
      margin < 0 ||
      margin > 1
    )
      throw new Error(
        "Ungültiges Analysebudget: 1–16 Signale, Intervall 0,1–30 s, 0–8 Modelle, Budget 0–60 000 ms, Klassenwert 0,5–1 und Abstand 0–1 erforderlich.",
      );
    if (
      arr(experts).length > 64 ||
      arr(experts).some(
        (e) =>
          !e.model_id ||
          !Object.hasOwn(ANALYSIS_LEVELS, e.level) ||
          arr(e.families).length > 32 ||
          arr(e.families).some((f) => f.length > 80),
      )
    )
      throw new Error(
        "Experten benötigen eine gültige Zielebene; höchstens 64 Einträge mit jeweils 32 Familien (bis 80 Zeichen) sind zulässig.",
      );
    return {
      enabled: values.enabled === true,
      use_priors: values.use_priors === true,
      use_active_model: values.use_active_model === true,
      start_frequency_hz: start,
      stop_frequency_hz: stop,
      max_signals_per_cycle: maxSignals,
      interval_seconds: interval,
      policy: {
        min_model_score: minScore,
        min_margin: margin,
        budget_ms: budget,
        max_models: maxModels,
        require_evaluated_models: values.require_evaluated_models === true,
        explain_models: values.explain_models === true,
      },
      experts: arr(experts).map(({ model_id, enabled, level, families }) => ({
        model_id,
        enabled,
        level,
        families,
      })),
    };
  }
  function analysisRangeText(range) {
    const start = finiteNumber(range?.start_frequency_hz),
      stop = finiteNumber(range?.stop_frequency_hz);
    return start !== null && stop !== null && stop > start
      ? `${mhz(start)} – ${mhz(stop)}`
      : "Bereich unbekannt";
  }
  function analysisCoverageSummary(coverage) {
    const c = coverage || {},
      w = c.current_window || {};
    const start = finiteNumber(w.start_frequency_hz),
      stop = finiteNumber(w.stop_frequency_hz);
    const known = start !== null && stop !== null && stop > start;
    return {
      requested: analysisRangeText(c.requested_range),
      current: known
        ? analysisRangeText(w)
        : "Kein beobachtetes Empfangsfenster",
      active: w.active === true,
      observedAt: w.observed_at || null,
      source: w.source || null,
      simultaneous: c.simultaneous === true,
      fullRangeCovered: c.full_range_covered === true,
      notes: arr(c.notes),
    };
  }
  function analysisJSONDetails(title, value, open = false) {
    return el("details", { class: "analysis-json-details", open }, [
      el("summary", { text: title }),
      el("pre", { text: JSON.stringify(value ?? null, null, 2) }),
    ]);
  }
  function analysisNotice(message, tone = "") {
    return el("p", { class: `notice ${tone}`, text: message });
  }
  function populateAnalysisSettings(settings) {
    const values = {
      "analysis-start-mhz": settings.start_frequency_hz / 1e6,
      "analysis-stop-mhz": settings.stop_frequency_hz / 1e6,
      "analysis-max-signals": settings.max_signals_per_cycle,
      "analysis-interval": settings.interval_seconds,
      "analysis-min-score": settings.policy?.min_model_score,
      "analysis-min-margin": settings.policy?.min_margin,
      "analysis-budget-ms": settings.policy?.budget_ms,
      "analysis-max-models": settings.policy?.max_models,
    };
    for (const [id, value] of Object.entries(values))
      if (value !== undefined && Number.isFinite(value))
        $(`#${id}`).value = value;
    for (const [id, value] of Object.entries({
      "analysis-enabled": settings.enabled,
      "analysis-use-priors": settings.use_priors,
      "analysis-use-active-model": settings.use_active_model,
      "analysis-require-evaluated": settings.policy?.require_evaluated_models,
      "analysis-explain-models": settings.policy?.explain_models,
    }))
      $(`#${id}`).checked = value === true;
    state.analysisExpertDrafts = new Map(
      arr(settings.experts).map((e) => [
        e.model_id,
        { ...e, families: arr(e.families) },
      ]),
    );
    state.analysisSettingsLoaded = true;
  }
  function renderAnalysisExperts() {
    const cards = itemArray(state.analysisCatalogue?.models, "models");
    const known = new Map(cards.map((m) => [String(m.id || m.model_id), m]));
    const ids = [
      ...new Set([...known.keys(), ...state.analysisExpertDrafts.keys()]),
    ];
    $("#analysis-experts").replaceChildren(
      ...(ids.length
        ? ids.map((id) => {
            const card = known.get(id),
              draft = state.analysisExpertDrafts.get(id) || {
                model_id: id,
                enabled: false,
                level: "modulation",
                families: [],
              };
            const checked = el("input", {
              type: "checkbox",
              checked: draft.enabled === true,
              "aria-label": `${text(card?.name, id)} als Experte`,
            });
            const level = el(
              "select",
              { "aria-label": `Zielebene für ${text(card?.name, id)}` },
              Object.entries(ANALYSIS_LEVELS).map(([value, title]) =>
                el("option", {
                  value,
                  text: title,
                  selected: draft.level === value,
                }),
              ),
            );
            const families = el("input", {
              type: "text",
              value: arr(draft.families).join(", "),
              placeholder: "Alle Familien",
              "aria-label": `Signalfamilien für ${text(card?.name, id)}`,
            });
            const update = () =>
              state.analysisExpertDrafts.set(id, {
                model_id: id,
                enabled: checked.checked,
                level: level.value,
                families: families.value
                  .split(",")
                  .map((v) => v.trim())
                  .filter(Boolean),
              });
            checked.addEventListener("change", update);
            level.addEventListener("change", update);
            families.addEventListener("input", update);
            return el("div", { class: "analysis-expert-row" }, [
              el("label", {}, [
                checked,
                el("span", {}, [
                  el("strong", { text: text(card?.name, id) }),
                  el("small", { text: id }),
                ]),
              ]),
              pill(
                !card
                  ? "Nicht in Bibliothek"
                  : card.evaluation_available === true
                    ? "Bewertet"
                    : "Bewertung nicht belegt",
                card?.evaluation_available === true ? "green" : "amber",
              ),
              el("label", {}, [el("span", { text: "Zielebene" }), level]),
              el("label", {}, [
                el("span", {
                  text: "Familien-Kennungen, durch Komma getrennt",
                }),
                families,
              ]),
              card?.classes
                ? el("small", {
                    text: `Gelernte Klassen: ${arr(card.classes).join(", ")}`,
                  })
                : null,
              card?.error
                ? el("small", {
                    text: `Modellmetadaten nicht verfügbar: ${card.error}`,
                  })
                : null,
            ]);
          })
        : [
            empty(
              "Keine Modelle in der Bibliothek. Merkmale können Hypothesen liefern; für Modellkandidaten zunächst ein Netz trainieren oder importieren.",
            ),
          ]),
    );
    const taxonomy = state.analysisCatalogue?.taxonomy;
    if (taxonomy)
      $("#analysis-experts").append(
        el("details", { class: "analysis-json-details" }, [
          el("summary", { text: "Klassensystem: Aussagen und Grenzen" }),
          ...Object.entries(ANALYSIS_LEVELS).map(([key, title]) =>
            el("div", {}, [
              el("strong", { text: title }),
              el("p", {
                text:
                  arr(taxonomy[key])
                    .map((v) =>
                      v.name && v.name !== v.id ? `${v.name} (${v.id})` : v.id,
                    )
                    .join(" · ") || "Keine Einträge",
              }),
            ]),
          ),
          ...arr(taxonomy.limitations).map((note) =>
            el("p", { text: String(note) }),
          ),
        ]),
      );
  }
  function analysisSettingsFromForm() {
    const value = (id) => $(`#analysis-${id}`).value;
    const payload = analysisSettingsPayload(
      {
        enabled: $("#analysis-enabled").checked,
        use_priors: $("#analysis-use-priors").checked,
        use_active_model: $("#analysis-use-active-model").checked,
        start_frequency_hz: Number(value("start-mhz")) * 1e6,
        stop_frequency_hz: Number(value("stop-mhz")) * 1e6,
        max_signals_per_cycle: value("max-signals"),
        interval_seconds: value("interval"),
        min_model_score: value("min-score"),
        min_margin: value("min-margin"),
        budget_ms: value("budget-ms"),
        max_models: value("max-models"),
        require_evaluated_models: $("#analysis-require-evaluated").checked,
        explain_models: $("#analysis-explain-models").checked,
      },
      analysisExpertSelection(
        itemArray(state.analysisCatalogue?.models, "models"),
        [...state.analysisExpertDrafts.values()],
      ),
    );
    return {
      ...state.analysis?.settings,
      ...payload,
      policy: { ...state.analysis?.settings?.policy, ...payload.policy },
    };
  }
  function renderAnalysisCoverage() {
    const coverage = state.analysis?.coverage || {},
      c = analysisCoverageSummary(coverage);
    const window = coverage.current_window || {};
    const limits = coverage.receiver_limits || {};
    replaceContent(
      $("#analysis-coverage"),
      pill(
        c.active ? "Empfangsfenster aktiv" : "Kein aktives Empfangsfenster",
        c.active ? "green" : "",
      ),
      el("strong", { text: c.current }),
      el("p", {
        text: `Angeforderter Suchbereich: ${c.requested}. Quelle: ${sourceLabel(c.source)}. Letzte Beobachtung: ${date(c.observedAt)}.`,
      }),
      el("p", {
        text: `${c.simultaneous ? "Gleichzeitige Abdeckung gemeldet" : "Ein Empfängerfenster zu einer Zeit"} · ${c.fullRangeCovered ? "Gesamtbereich laut Backend abgedeckt" : "Keine vollständige Abdeckung von 0–6 GHz belegt"}.`,
      }),
      finiteNumber(window.sample_rate) !== null
        ? el("small", {
            text: `Abtastrate ${num(window.sample_rate / 1e6, 3)} MS/s`,
          })
        : null,
      finiteNumber(limits.min_frequency_hz) !== null
        ? el("small", {
            text: `Empfängergrenzen: ${mhz(limits.min_frequency_hz)} – ${mhz(limits.max_frequency_hz)}`,
          })
        : null,
      ...c.notes.map((note) => el("small", { text: String(note) })),
    );
  }
  function renderAnalysisStats() {
    const stats = state.analysis?.stats || {},
      settings = state.analysis?.settings || {};
    const cards = [
      [
        "ANALYSE",
        settings.enabled === true ? "Aktiviert" : "Deaktiviert",
        `${num(stats.cycles, 0)} Auswertungszyklen`,
      ],
      [
        "SIGNALE",
        num(stats.signal_count, 0),
        `${num(stats.observation_count ?? stats.analyzed_observations, 0)} Beobachtungen · ${num(stats.pending_signals, 0)} ausstehend`,
      ],
      [
        "EXPERTEN",
        num(arr(settings.experts).filter((e) => e.enabled).length, 0),
        settings.use_active_model
          ? "Aktives Modell zusätzlich, sofern zugelassen"
          : "Aktives Modell nicht zusätzlich verwenden",
      ],
      [
        "ZYKLUSDAUER",
        stats.last_cycle_ms === null || stats.last_cycle_ms === undefined
          ? "—"
          : `${num(stats.last_cycle_ms, 1)} ms`,
        `${num(stats.unbuffered_detections, 0)} Detektionen ohne IQ-Puffer`,
      ],
    ];
    $("#analysis-stats").replaceChildren(
      ...cards.map(([label, value, note]) =>
        el("div", { class: "analysis-stat" }, [
          el("span", { class: "metric-label", text: label }),
          el("strong", { text: value }),
          el("small", { text: note }),
        ]),
      ),
      ...(stats.last_error
        ? [
            analysisNotice(
              `Letzter Analysefehler: ${stats.last_error}`,
              "error",
            ),
          ]
        : []),
    );
  }
  function analysisSignalRow(signal, compact = false) {
    const decision = analysisDecision(signal),
      levels = analysisConclusionRows(decision);
    const byKey = Object.fromEntries(levels.map((r) => [r.key, r]));
    const id = signal.id || signal.signal_id;
    const inspect = button(
      "Entscheidungsweg",
      () => action(() => inspectAnalysisSignal(id)),
      "button button-quiet",
    );
    inspect.disabled = !id;
    if (compact)
      return el("tr", {}, [
        detailCell(
          mhz(signal.center_frequency_hz),
          `${num(signal.bandwidth_hz / 1e3)} kHz`,
        ),
        detailCell(
          analysisLabel(byKey.modulation.label, "modulation"),
          analysisStatus(byKey.modulation.status),
        ),
        detailCell(
          analysisLabel(byKey.protocol.label, "protocol"),
          analysisStatus(byKey.protocol.status),
        ),
        el("td", {}, [inspect]),
      ]);
    return el(
      "tr",
      { class: state.analysisSelectedId === id ? "selected" : "" },
      [
        detailCell(
          text(signal.channel_id, id),
          `${sourceLabel(signal.source)} · ${num(signal.observation_count, 0)} Beobachtungen`,
        ),
        detailCell(
          mhz(signal.center_frequency_hz),
          `${num(signal.sample_rate / 1e3)} kS/s`,
        ),
        el("td", { text: `${num(signal.bandwidth_hz / 1e3)} kHz` }),
        detailCell(
          analysisLabel(byKey.modulation.label, "modulation"),
          analysisStatus(byKey.modulation.status),
        ),
        detailCell(
          analysisLabel(byKey.protocol.label, "protocol"),
          analysisStatus(byKey.protocol.status),
        ),
        detailCell(
          analysisLabel(byKey.service.label, "service"),
          `Emitter: ${analysisLabel(byKey.emitter.label, "emitter")}`,
        ),
        detailCell(
          analysisStatus(decision.status || signal.status),
          `Zuletzt ${date(signal.last_seen || signal.observed_at)}`,
        ),
        el("td", {}, [
          el("div", { class: "table-actions" }, [
            inspect,
            id
              ? download(
                  `/api/analysis/signals/${encodeURIComponent(id)}/download`,
                  "Signalbericht JSON",
                  true,
                )
              : null,
          ]),
        ]),
      ],
    );
  }
  function renderReceptionAnalysis(snapshot) {
    const holder = $("#reception-analysis-body");
    if (!holder) return;
    const channel = snapshot.analysis_status || {},
      stats = snapshot.analysis_stats || {};
    $("#reception-analysis-status").textContent =
      `Empfangsfenster: ${num(channel.detected_channels, 0)} detektiert · ${num(channel.buffered_channels, 0)} gepuffert (Filterlimit ${num(channel.channel_limit, 0)}) · ${num(channel.unbuffered_detections_count, 0)} ohne IQ-Puffer. Nachgelagerte Analyse: ${num(stats.pending_signals, 0)} ausstehend. Mehr Filterkanäle benötigen zusätzliche CPU-Zeit; unbekannte oder ungepufferte Signale sind keine negativen Klassifikationsergebnisse.`;
    if (holder.contains(document.activeElement)) return;
    const signals = [
      ...matchedSignalClassifications(
        arr(snapshot.detections),
        snapshot.signal_classifications,
        snapshot,
      ).values(),
    ];
    holder.replaceChildren(
      ...(signals.length
        ? signals.map((s) => analysisSignalRow(s, true))
        : [
            el("tr", {}, [
              el("td", { colSpan: 4 }, [
                empty(
                  "Noch keine kanalspezifische Analyse. Im Tab Analyse aktivieren; jede Auswertung gehört zu genau einem IQ-Kanal.",
                ),
              ]),
            ]),
          ]),
    );
  }
  function renderReceptionDecision(snapshot) {
    if ($("#decision-steps").contains(document.activeElement)) return;
    const observations = [
      ...matchedSignalClassifications(
        arr(snapshot.detections),
        snapshot.signal_classifications,
        snapshot,
      ).values(),
    ];
    $("#classification-label").textContent =
      observations.length > 1
        ? `${observations.length} Signale einzeln ausgewertet`
        : observations.length
          ? analysisConclusionRows(observations[0]).find(
              (r) => r.key === "modulation",
            ).label
          : "Keine aktuelle Kanalanalyse";
    $("#classification-confidence").textContent =
      "Hierarchische Analyse · Modellwerte sind unkalibrierte Kandidaten";
    $("#classification-time").textContent =
      observations.length === 1
        ? `${num(analysisDecision(observations[0]).elapsed_ms, 1)} ms`
        : "Je Kanal";
    $("#decision-steps").replaceChildren(
      ...(observations.length
        ? observations.map((o) => {
            const conclusion = analysisConclusionRows(o).find(
              (r) => r.key === "modulation",
            );
            return el("div", { class: "analysis-reception-summary" }, [
              el("strong", {
                text: `${mhz(o.center_frequency_hz)} · ${analysisLabel(conclusion.label, "modulation")} · ${analysisStatus(conclusion.status)}`,
              }),
              el("p", { text: conclusion.reason }),
              button("Alle Aussageebenen und Belege", () =>
                action(() => inspectAnalysisSignal(o.signal_id || o.id)),
              ),
            ]);
          })
        : [
            empty(
              "Noch kein aktuelles, zu diesem Empfangsfenster passendes Analyseergebnis. Im Tab Analyse aktivieren; unbekannte Signale bleiben unbekannt.",
            ),
          ]),
    );
    $("#features-grid").replaceChildren(
      ...(observations.length === 1
        ? Object.entries(analysisDecision(observations[0]).features || {}).map(
            ([key, value]) =>
              el("div", { class: "feature" }, [
                el("span", { text: key.replaceAll("_", " ") }),
                el("strong", {
                  text: typeof value === "number" ? num(value, 4) : text(value),
                }),
              ]),
          )
        : [
            el("span", {
              class: "secondary",
              text: "Merkmale sind im Bericht des jeweiligen Signals gespeichert.",
            }),
          ]),
    );
    $("#model-explanation-details").hidden = true;
  }
  function renderAnalysisSignals() {
    if ($("#analysis-signals-body").contains(document.activeElement)) return;
    const signals = itemArray(state.analysis?.signals, "signals");
    $("#analysis-signals-body").replaceChildren(
      ...(signals.length
        ? signals.map((s) => analysisSignalRow(s))
        : [
            el("tr", {}, [
              el("td", { colSpan: 8 }, [
                empty(
                  "Noch keine Signale analysiert. Empfang oder Scan starten, oder eine gespeicherte Aufnahme über „Analyse“ auswerten.",
                ),
              ]),
            ]),
          ]),
    );
    // The inspected report is deliberately stable while the list refreshes.
    // Polling must not collapse an expanded trace or move keyboard focus.
  }
  async function inspectAnalysisSignal(id) {
    if (!id) return;
    state.analysisSelectedId = String(id);
    const signal = await api(`/api/analysis/signals/${encodeURIComponent(id)}`);
    state.analysisDetail = signal;
    await changeView("analyse");
    renderAnalysisDetail(signal);
    $("#analysis-detail").scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }
  function analysisPredictionExplanation(prediction, limit = 12) {
    const explanation = prediction?.explanation;
    if (
      !explanation ||
      typeof explanation !== "object" ||
      Array.isArray(explanation)
    )
      return null;
    const contributions = arr(explanation.contributions);
    const maximum =
      Number.isInteger(limit) && limit > 0 ? Math.min(limit, 32) : 12;
    return {
      ...explanation,
      contributions: contributions.slice(0, maximum),
      total_contributions: contributions.length,
    };
  }
  function renderAnalysisPredictions(decision) {
    const predictions = arr(decision.predictions);
    return el(
      "div",
      { class: "analysis-model-predictions" },
      predictions.length
        ? predictions.map((prediction) => {
            const explanation = analysisPredictionExplanation(prediction);
            const name =
              arr(decision.routes).find(
                (route) => route.model_id === prediction.model_id,
              )?.name || prediction.model_id;
            return el("section", { class: "analysis-conclusion" }, [
              el("h3", { text: text(name) }),
              el("p", {
                text: `Ausgabe: ${analysisLabel(prediction.model_label, prediction.label_level)} · ${analysisStatus(prediction.status)} · Klassenwert ${num(prediction.score, 3)} (unkalibriert)`,
              }),
              explanation
                ? el("details", { class: "analysis-json-details" }, [
                    el("summary", {
                      text: "Modelleinflüsse dieser Vorhersage",
                    }),
                    el("p", {
                      text: "Beiträge und Sensitivitäten beschreiben die lokale Reaktion des geladenen Modells nach Vorverarbeitung. Daraus folgt keine physikalische Kausalität oder bestätigte Identität.",
                    }),
                    explanationNode(explanation),
                    explanation.total_contributions >
                    explanation.contributions.length
                      ? el("p", {
                          class: "field-help",
                          text: `Erste ${explanation.contributions.length} von ${explanation.total_contributions} Einträgen dargestellt; sämtliche Werte stehen in den vollständigen Einflussdaten.`,
                        })
                      : null,
                    analysisJSONDetails(
                      "Vollständige Einflussdaten",
                      prediction.explanation,
                    ),
                  ])
                : el("p", {
                    class: "field-help",
                    text: "Für diese Beobachtung wurden keine Modelleinflüsse gespeichert. Die Berechnung kann in den Auswahlregeln aktiviert werden.",
                  }),
            ]);
          })
        : [empty("Keine Modellvorhersage im Bericht gespeichert.")],
    );
  }
  function renderAnalysisDetail(signal, container = null) {
    const holder = container || $("#analysis-detail"),
      decision = analysisDecision(signal);
    const id = signal.id || signal.signal_id;
    const rows = analysisConclusionRows(decision);
    const stages = analysisStageOrder(decision);
    const evidence = Array.isArray(decision.evidence)
      ? decision.evidence
      : Object.entries(decision.evidence || {}).map(([key, value]) => ({
          id: key,
          ...value,
        }));
    const routes = arr(decision.routes);
    replaceContent(
      holder,
      el("div", { class: "analysis-detail-heading" }, [
        el("strong", {
          text: `${mhz(signal.center_frequency_hz ?? decision.input?.center_frequency_hz)} · ${analysisStatus(decision.status)}`,
        }),
        id
          ? download(
              `/api/analysis/signals/${encodeURIComponent(id)}/download`,
              "Vollständigen Signalbericht als JSON",
            )
          : null,
      ]),
      el("p", {
        class: "secondary",
        text: `Berichtsstand: ${date(signal.last_seen || signal.observed_at)} · ${num(decision.input?.analyzed_samples, 0)} ausgewertete IQ-Samples. Dieser Detailbericht bleibt beim Lesen stabil; die Signalliste wird separat aktualisiert.`,
      }),
      analysisNotice(
        "Ein Modellkandidat ist eine plausible Zuordnung innerhalb der gelernten Klassen. Ohne unabhängigen Protokollbeleg ist damit weder Funkdienst noch konkreter Emitter bestätigt.",
      ),
      el(
        "div",
        { class: "analysis-conclusions" },
        rows.map((r) =>
          el("section", { class: "analysis-conclusion" }, [
            el("h3", { text: r.title }),
            pill(
              analysisStatus(r.status),
              r.status === "observed"
                ? "green"
                : r.status === "conflict"
                  ? "amber"
                  : "",
            ),
            el("strong", { text: analysisLabel(r.label, r.key) }),
            el("p", { text: r.reason }),
            ...r.candidates.map((candidate) =>
              el("small", {
                text: `${analysisLabel(candidate.label, r.key)}${finiteNumber(candidate.score) !== null ? ` · Wert ${num(candidate.score, 3)}` : ""} · ${analysisScoreKind(candidate.score_kind)}`,
              }),
            ),
            r.evidenceIds.length
              ? el("small", { text: `Belege: ${r.evidenceIds.join(", ")}` })
              : null,
          ]),
        ),
      ),
      el("h3", { text: "So entstand die Entscheidung" }),
      stages.length
        ? el(
            "ol",
            { class: "analysis-stage-list" },
            stages.map((stage) =>
              el("li", {}, [
                el("div", {}, [
                  el("strong", {
                    text: text(
                      stage.name ||
                        stage.title ||
                        {
                          quality: "Signalqualität prüfen",
                          features: "Signalmerkmale messen",
                          hypotheses: "Breite Hypothesen bilden",
                          routing: "Geeignete Modelle auswählen",
                          models: "Modelle auswerten",
                          fusion: "Aussagen getrennt zusammenführen",
                        }[stage.id] ||
                        stage.id,
                    ),
                  }),
                  pill(analysisStatus(stage.status)),
                ]),
                el("small", {
                  text: `Abhängig von: ${arr(stage.parents).length ? stage.parents.join(", ") : "Eingangssignal"} · ${num(stage.elapsed_ms, 2)} ms`,
                }),
                el("p", {
                  text: text(
                    stage.reason,
                    "Kein zusätzlicher Grund protokolliert.",
                  ),
                }),
                stage.decision !== undefined
                  ? el("p", {
                      text:
                        typeof stage.decision === "string"
                          ? stage.decision
                          : JSON.stringify(stage.decision),
                    })
                  : null,
                stage.dependency_unresolved
                  ? analysisNotice(
                      "Abhängigkeit fehlt oder bildet einen Kreis. Reihenfolge kann nicht vollständig aufgelöst werden.",
                      "amber",
                    )
                  : null,
              ]),
            ),
          )
        : empty("Keine Entscheidungsschritte im Bericht gespeichert."),
      el("h3", { text: "Modellauswahl und Rechenbudget" }),
      routes.length
        ? el(
            "div",
            {
              class: "table-scroll analysis-routes-scroll",
              tabIndex: 0,
              role: "region",
              "aria-label":
                "Modellauswahl und Rechenbudget, horizontal scrollbar",
            },
            [
              el("table", { class: "data-table analysis-routes-table" }, [
                el("thead", {}, [
                  el(
                    "tr",
                    {},
                    ["Modell", "Ebene", "Ausführung", "Grund"].map((v) =>
                      el("th", { text: v }),
                    ),
                  ),
                ]),
                el(
                  "tbody",
                  {},
                  routes.map((r) =>
                    el("tr", {}, [
                      detailCell(text(r.name, r.model_id), r.model_id),
                      el("td", {
                        text: ANALYSIS_LEVELS[r.level] || text(r.level),
                      }),
                      el("td", { text: analysisStatus(r.status) }),
                      el("td", {
                        text: Array.isArray(r.why)
                          ? r.why.join(" ")
                          : text(r.why || r.reason),
                      }),
                    ]),
                  ),
                ),
              ]),
            ],
          )
        : empty(
            "Kein Modell ausgeführt oder keine Route protokolliert. Merkmalsregeln können dennoch Hypothesen liefern.",
          ),
      el("h3", { text: "Was wurde im Signal gemessen?" }),
      el("p", {
        text: "Hüllkurve beschreibt die Stärke über der Zeit, Phase beschreibt die Schwingungslage, das Spektrum verteilt die Energie auf Frequenzen. Diese Merkmale begründen Hypothesen; sie identifizieren allein keinen Sender.",
      }),
      el(
        "dl",
        { class: "analysis-feature-list" },
        Object.entries(decision.features || {}).flatMap(([key, value]) => [
          el("dt", {
            text:
              {
                amplitude_cv: "Schwankung der Hüllkurve",
                amplitude_kurtosis: "Spitzen in der Hüllkurve",
                phase_std: "Streuung der Phasenänderung [Radiant/Winkelmaß]",
                phase_dispersion: "Verteilung der Phase",
                spectral_flatness: "Flachheit des Spektrums",
                spectral_entropy: "Verteilung der Spektralenergie",
                occupied_bandwidth_fraction: "Belegter Bandbreitenanteil",
                spectral_kurtosis: "Spektrale Spitzen",
                peak_to_rms_db: "Spitzenpegel über Effektivpegel [dB]",
                active_fraction: "Anteil aktiver Signalabschnitte",
                ridge_linear_r2: "Geradlinigkeit des Frequenzverlaufs (0–1)",
                ridge_span_fraction: "Relativer Frequenzdurchlauf",
                ridge_peak_fraction:
                  "Energieanteil der wandernden Spektralspitze",
                fourth_cumulant:
                  "Kumulantenmerkmal vierter Ordnung (Form der Verteilung)",
                envelope_correlation:
                  "Ähnlichkeit aufeinanderfolgender Hüllkurvenwerte",
                rms_dbfs: "Digitaler Effektivpegel [dBFS]",
                sample_rate: "Abtastrate [Samples/s]",
              }[key] || key.replaceAll("_", " "),
          }),
          el("dd", {
            text: typeof value === "number" ? num(value, 5) : text(value),
          }),
        ]),
      ),
      analysisJSONDetails("Technische Merkmalswerte", decision.features),
      analysisJSONDetails(`Belege und Herkunft (${evidence.length})`, evidence),
      el("h3", { text: "Modellvorhersagen und lokale Einflüsse" }),
      renderAnalysisPredictions(decision),
      analysisJSONDetails(
        "Modellvorhersagen mit Gültigkeitsbereich",
        decision.predictions,
      ),
      analysisJSONDetails(
        "Vorwissen (getrennt von Signalbelegen)",
        decision.prior_hints,
      ),
      ...arr(decision.warnings).map((v) => analysisNotice(String(v), "amber")),
      ...arr(decision.limitations).map((v) =>
        el("p", { class: "muted", text: String(v) }),
      ),
      analysisJSONDetails(
        "Gesamter Entscheidungsbericht mit Eingang und Budget",
        signal,
      ),
    );
  }
  function analysisScanConfig() {
    const f = $("#receiver-form").elements;
    return {
      start_frequency_hz: Number($("#analysis-start-mhz").value) * 1e6,
      stop_frequency_hz: Number($("#analysis-stop-mhz").value) * 1e6,
      source: $("#analysis-scan-source").value,
      sample_rate: Number($("#analysis-scan-sample-rate").value),
      dwell_seconds: Number($("#analysis-scan-dwell").value),
      overlap_fraction: Number($("#analysis-scan-overlap").value),
      repeat: $("#analysis-scan-repeat").checked,
      receiver_config: {
        mode: "monitor",
        gain_db: Number($("#analysis-scan-gain").value),
        uri: $("#analysis-scan-uri").value,
        threshold_db: Number(f.threshold_db.value),
        max_channels: Number(f.max_channels.value),
        duration_seconds: Number(f.duration_seconds.value),
        analysis_max_channels: Number($("#analysis-scan-max-channels").value),
        queue_capacity_bytes: Number(f.queue_mib.value) * 1048576,
        spectrum_refresh_hz: Number(f.spectrum_refresh_hz.value),
        detection_interval_seconds:
          Number(f.detection_interval_ms.value) / 1000,
      },
    };
  }
  function renderAnalysisScanPlan(plan) {
    const ranges = arr(plan.uncovered_ranges);
    $("#analysis-scan-plan").replaceChildren(
      el("strong", {
        text: `${num(plan.step_count, 0)} geplante Schritte · mindestens ${duration(plan.estimated_min_sweep_seconds)} je Durchlauf`,
      }),
      el("p", {
        text: `Angefordert: ${analysisRangeText(plan.requested_range)}. Geplante Abdeckung ${percent(plan.coverage_fraction)}. Dies ist ein Plan, kein Nachweis empfangener Signale.`,
      }),
      analysisNotice(
        "Frequenzen werden nacheinander angefahren. Signale außerhalb des aktuellen Empfangsfensters können währenddessen übersehen werden.",
      ),
      ranges.length
        ? el("div", {}, [
            el("strong", { text: "Nicht abgedeckte Bereiche" }),
            ...ranges.map((r) =>
              el("p", {
                text: `${analysisRangeText(r)}${r.reason ? ` · ${r.reason}` : ""}`,
              }),
            ),
          ])
        : el("p", {
            text: "Keine Bereichslücken im Plan gemeldet. Tatsächlicher Empfang ist damit nicht verifiziert.",
          }),
      ...arr(plan.notes).map((note) => el("p", { text: String(note) })),
      analysisJSONDetails("Geplante Schritte und Empfängergrenzen", plan),
    );
  }
  function renderAnalysisScan(scan) {
    const running = scan?.running === true;
    const holder = $("#analysis-scan-status");
    let summary = $(".analysis-scan-summary", holder);
    if (!summary) {
      summary = el("div", { class: "analysis-scan-summary" });
      holder.replaceChildren(summary);
    }
    replaceContent(
      summary,
      pill(analysisStatus(scan?.state), running ? "green" : ""),
      el("span", {
        text: `Schritte ${num(scan?.completed_steps, 0)} · Abgeschlossene Durchläufe ${num(scan?.completed_cycles ?? scan?.cycles, 0)}`,
      }),
      scan?.current_step
        ? el("span", {
            text: `Aktuell: ${mhz(scan.current_step.center_frequency_hz)}`,
          })
        : null,
      ...arr(scan?.errors).map((error) =>
        analysisNotice(
          typeof error === "string" ? error : JSON.stringify(error),
          "error",
        ),
      ),
      scan?.error ? analysisNotice(String(scan.error), "error") : null,
      arr(scan?.observation_gaps).length
        ? analysisNotice(
            `${scan.observation_gaps.length} protokollierte Beobachtungslücken im begrenzten Verlauf. Ein Scan sieht jeden Frequenzbereich nur zeitweise.`,
            "amber",
          )
        : null,
    );
    const previous = $("details", holder);
    // Keep the same DOM subtree while the user reads or focuses the history.
    // The status summary and stop control still update independently.
    if (
      scan?.observed_windows &&
      !(previous?.open || previous?.contains(document.activeElement))
    ) {
      const detail = analysisJSONDetails(
        "Beobachtete Fenster und zeitliche Lücken · Stand bleibt beim Lesen erhalten",
        {
          observed_windows: scan.observed_windows,
          observation_gaps: scan.observation_gaps,
        },
      );
      if (previous) previous.replaceWith(detail);
      else holder.append(detail);
    }
    $("#analysis-scan-start").disabled = running;
    $("#analysis-scan-stop").disabled = !running;
  }
  async function loadAnalysis(withCatalogue = false) {
    const results = await Promise.allSettled([
      api("/api/analysis"),
      ...(withCatalogue ? [api("/api/analysis/catalogue")] : []),
    ]);
    if (results[1]?.status === "fulfilled")
      state.analysisCatalogue = results[1].value;
    if (results[0].status === "rejected") throw results[0].reason;
    state.analysis = results[0].value;
    if (!state.analysisSettingsLoaded && state.analysis.settings)
      populateAnalysisSettings(state.analysis.settings);
    if (withCatalogue) renderAnalysisExperts();
    renderAnalysisCoverage();
    renderAnalysisStats();
    renderAnalysisSignals();
    renderAnalysisScan(state.analysis.scan);
    if (results[1]?.status === "rejected") throw results[1].reason;
  }
  async function pollAnalysis() {
    if (state.view !== "analyse" || state.analysisPolling) return;
    state.analysisPolling = true;
    try {
      await loadAnalysis();
    } catch (error) {
      $("#analysis-stats").replaceChildren(
        analysisNotice(
          `Analyse nicht aktualisiert: ${errorMessage(error)}`,
          "error",
        ),
      );
    } finally {
      state.analysisPolling = false;
    }
  }
  function setupAnalysisEvents() {
    $("#rx-analysis-laptop").addEventListener("click", () => {
      $("#rx-analysis-max-channels").value = 4;
    });
    $("#analysis-scan-laptop").addEventListener("click", () => {
      $("#analysis-scan-max-channels").value = 4;
      $("#analysis-scan-form").dispatchEvent(
        new Event("input", { bubbles: true }),
      );
    });
    $("#analysis-settings-form").addEventListener("submit", (event) => {
      event.preventDefault();
      action(async () => {
        const settings = await api("/api/analysis/settings", {
          method: "POST",
          body: analysisSettingsFromForm(),
        });
        populateAnalysisSettings(settings.settings || settings);
        renderAnalysisExperts();
        await loadAnalysis();
        toast("Analysekonfiguration gespeichert.");
      }, event.currentTarget);
    });
    $("#analysis-scan-form").addEventListener("submit", (event) =>
      event.preventDefault(),
    );
    const runScan = (operation) =>
      action(async () => {
        if (
          !$("#analysis-scan-form").reportValidity() ||
          !$("#analysis-settings-form").reportValidity()
        )
          return;
        const result = await api(`/api/analysis/scan/${operation}`, {
          method: "POST",
          body: analysisScanConfig(),
        });
        if (operation === "plan") {
          state.analysisScanPlan = result.plan || result;
          renderAnalysisScanPlan(state.analysisScanPlan);
        } else {
          state.analysisScanPlan = null;
          await loadAnalysis();
          toast("Scan gestartet. Der Empfänger wird schrittweise abgestimmt.");
        }
      }, $("#analysis-scan-form"));
    $("#analysis-scan-plan-button").addEventListener("click", () =>
      runScan("plan"),
    );
    $("#analysis-scan-start").addEventListener("click", () => runScan("start"));
    $("#analysis-scan-stop").addEventListener("click", () =>
      action(async () => {
        await api("/api/analysis/scan/stop", { method: "POST", body: {} });
        await loadAnalysis();
      }),
    );
    $("#analysis-scan-form").addEventListener("input", () => {
      state.analysisScanPlan = null;
      $("#analysis-scan-plan").replaceChildren(
        empty(
          "Scanparameter geändert. Vor dem Start einen aktuellen Plan berechnen.",
        ),
      );
    });
    for (const id of ["analysis-start-mhz", "analysis-stop-mhz"])
      $(`#${id}`).addEventListener("input", () => {
        state.analysisScanPlan = null;
        $("#analysis-scan-plan").replaceChildren(
          empty("Frequenzbereich geändert. Plan neu berechnen."),
        );
      });
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
    const config = experimentConfig({
      allowEmptyTopologies: true,
      includeDisabledOptimization: true,
    });
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
  function prepareExperimentDraft(
    c,
    catalog,
    availableRecordings,
    torchAvailable,
  ) {
    if (!c || typeof c !== "object" || Array.isArray(c))
      throw new Error(
        "Eine Versuchskonfiguration als JSON-Objekt ist erforderlich.",
      );
    const topologyIds = validateTopologyIds(
      c.topology_ids !== undefined
        ? c.topology_ids
        : c.topology_id
          ? [c.topology_id]
          : [],
      catalog,
      torchAvailable,
    );
    if (
      c.source !== undefined &&
      !["synthetic", "recordings"].includes(c.source)
    )
      throw new Error("Ungültige Datenquelle.");
    if (
      c.evaluation_mode !== undefined &&
      !["independent", "training_only"].includes(c.evaluation_mode)
    )
      throw new Error("Ungültiger Bewertungsmodus.");
    if (c.device !== undefined && !["auto", "cpu", "cuda"].includes(c.device))
      throw new Error("Ungültiges Rechengerät.");
    const execution = normalizeExecutionConfig(c.execution);
    const optimization = normalizeOptimizationConfig(
      c.optimization,
      c.segment_length || arr(c.segment_lengths)[0] || 2048,
    );
    if (optimization.enabled && c.evaluation_mode === "training_only")
      throw new Error(
        "Automatische HPO benötigt eine unabhängige Bewertung und unterstützt „Nur trainieren“ nicht.",
      );
    if (
      c.experiment_notes !== undefined &&
      (typeof c.experiment_notes !== "string" ||
        c.experiment_notes.length > 12000)
    )
      throw new Error(
        "Die Versuchsbeschreibung darf höchstens 12000 Zeichen enthalten.",
      );
    const integers = {
      seed: [0, 2147483647],
      samples_per_class: [12, 1000],
      epochs: [1, 100],
      segment_length: [256, 65536],
      batch_size: [4, 256],
    };
    for (const [key, [minimum, maximum]] of Object.entries(integers))
      if (
        c[key] !== undefined &&
        (!Number.isInteger(c[key]) || c[key] < minimum || c[key] > maximum)
      )
        throw new Error(
          `${key}: Ganzzahl zwischen ${minimum} und ${maximum} erforderlich.`,
        );
    if (
      c.name !== undefined &&
      (typeof c.name !== "string" || c.name.length > 200)
    )
      throw new Error("Ungültiger Experimentname.");
    if (c.use_priors !== undefined && typeof c.use_priors !== "boolean")
      throw new Error("Vorwissen muss als boolescher Wert gespeichert sein.");
    const listBounds = {
      segment_lengths: [256, 65536, 4, true],
      snr_db: [-30, 80, 12, false],
      jitter: [0, 2, 8, false],
      learning_rates: [0.00001, 0.1, 4, false],
      batch_sizes: [4, 256, 4, true],
    };
    for (const [key, [minimum, maximum, limit, integer]] of Object.entries(
      listBounds,
    )) {
      const values = c[key];
      if (
        values !== undefined &&
        (!Array.isArray(values) ||
          values.length < 1 ||
          values.length > limit ||
          values.some(
            (v) =>
              typeof v !== "number" ||
              !Number.isFinite(v) ||
              v < minimum ||
              v > maximum ||
              (integer && !Number.isInteger(v)),
          ))
      )
        throw new Error(
          `${key}: 1–${limit} gültige Zahlen zwischen ${minimum} und ${maximum} erforderlich.`,
        );
    }
    if (
      c.learning_rate !== undefined &&
      (typeof c.learning_rate !== "number" ||
        !Number.isFinite(c.learning_rate) ||
        c.learning_rate < 0.00001 ||
        c.learning_rate > 0.1)
    )
      throw new Error("Ungültige Lernrate.");
    const augmentationLimits = {
      frequency_offset_hz: 1000000,
      sample_clock_ppm: 1000,
      iq_gain_db: 12,
      iq_phase_deg: 45,
      dc_offset: 1,
      clip_level: 10,
      echo_amplitude: 0.95,
      echo_delay_samples: 128,
    };
    if (
      c.augmentation !== undefined &&
      (!c.augmentation ||
        typeof c.augmentation !== "object" ||
        Array.isArray(c.augmentation))
    )
      throw new Error("Ungültige Kanalparameter.");
    for (const [key, value] of Object.entries(c.augmentation || {}))
      if (
        !(key in augmentationLimits) ||
        typeof value !== "number" ||
        !Number.isFinite(value) ||
        value < 0 ||
        value > augmentationLimits[key]
      )
        throw new Error(`Ungültiger Kanalparameter: ${key}`);
    const ids = c.recording_ids === undefined ? [] : c.recording_ids;
    if (
      !Array.isArray(ids) ||
      ids.length > 1000 ||
      ids.some((id) => typeof id !== "string" || !id)
    )
      throw new Error(
        "Höchstens 1000 gültige Aufnahme-IDs in einer Konfiguration zulässig.",
      );
    const available = new Set(availableRecordings);
    return {
      config: c,
      optimization,
      execution,
      topologyIds,
      recordingIds: [...new Set(ids)].filter((id) => available.has(id)),
      missingRecordingIds: [...new Set(ids)].filter((id) => !available.has(id)),
    };
  }
  async function importExperimentConfig(file) {
    if (!file) return;
    if (file.size > 1048576)
      throw new Error("Die Konfigurationsdatei ist zu groß.");
    const payload = JSON.parse(await file.text());
    const form = $("#experiment-form");
    const prepared = prepareExperimentDraft(
      payload?.config || payload?.report?.config || payload,
      state.topologies,
      $$("#experiment-recordings input").map((input) => input.value),
      state.status.hardware?.torch_available,
    );
    const c = prepared.config;
    // All validation above completes before any form field or selection changes.
    for (const key of [
      "name",
      "source",
      "evaluation_mode",
      "seed",
      "samples_per_class",
      "epochs",
      "device",
    ])
      if (c[key] !== undefined && form.elements[key])
        form.elements[key].value = String(c[key]);
    setTopologySelection(prepared.topologyIds);
    form.elements.segment_lengths.value = arr(c.segment_lengths).length
      ? c.segment_lengths.join(", ")
      : String(c.segment_length || 2048);
    form.elements.snr_db.value = (c.snr_db || [0, 10, 20]).join(", ");
    form.elements.jitter.value = (c.jitter || [0, 0.05]).join(", ");
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
    ])
      form.elements[key].value = String(
        c.augmentation?.[key] ?? (key === "echo_delay_samples" ? 8 : 0),
      );
    form.elements.use_priors.checked = c.use_priors === true;
    form.elements.experiment_notes.value = c.experiment_notes || "";
    populateOptimizationForm(prepared.optimization);
    populateExecutionForm(prepared.execution);
    setExperimentRecordingSelection(prepared.recordingIds);
    $("#experiment-recordings-field").hidden =
      form.elements.source.value !== "recordings";
    scheduleReadiness();
    toast(
      prepared.missingRecordingIds.length
        ? `Konfiguration geladen. ${prepared.missingRecordingIds.length} Aufnahmen sind hier nicht verfügbar oder nicht bestätigt und wurden nicht ausgewählt.`
        : "Versuchskonfiguration geladen. Der Versuch wurde noch nicht gestartet.",
    );
  }
  function setupEvents() {
    setupAnalysisEvents();
    $("#optimization-enabled").addEventListener(
      "change",
      updateOptimizationControls,
    );
    $("#optimization-fields").addEventListener(
      "input",
      updateOptimizationControls,
    );
    $("#experiment-source").addEventListener(
      "change",
      updateOptimizationControls,
    );
    $("#experiment-evaluation-mode").addEventListener(
      "change",
      updateOptimizationControls,
    );

    $("#recording-cleanup-open").addEventListener("click", () =>
      action(openCleanupDialog),
    );
    $("#recording-cleanup-refresh").addEventListener("click", () =>
      action(loadCleanupPreview),
    );
    $("#recording-cleanup-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitCleanupDeletion();
    });
    $("#recording-cleanup-dialog").addEventListener("cancel", (event) => {
      if (state.cleanupBusy) event.preventDefault();
    });
    $("#recording-cleanup-dialog").addEventListener("close", () => {
      ++state.cleanupSequence;
      state.cleanupPlan = null;
      updateCleanupControls();
    });

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
      else if (state.view === "analyse") action(() => loadAnalysis(true));
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
    $("#experiment-recordings-all").addEventListener("click", () =>
      setExperimentRecordingSelection(
        $$("#experiment-recordings input").map((input) => input.value),
      ),
    );
    $("#experiment-recordings-none").addEventListener("click", () =>
      setExperimentRecordingSelection([]),
    );
    $("#experiment-recordings").addEventListener(
      "change",
      updateExperimentRecordingSelection,
    );
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
        state.topologies.filter((t) => topologyAvailable(t)).map((t) => t.id),
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
    updateOptimizationControls();
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
    setInterval(pollAnalysis, 1000);
  }
  init().catch(showError);
})();

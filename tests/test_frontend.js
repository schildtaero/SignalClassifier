/* Pure frontend regressions. No server, browser, RF hardware or DOM emulation. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.join(
  __dirname,
  "..",
  "signalclassifier",
  "static",
  "app.js",
);
const original = fs.readFileSync(sourcePath, "utf8");
const init = "init().catch(showError);";
assert.equal(
  original.split(init).length,
  2,
  "One startup hook must be replaced to test pure helpers",
);
const source = original.replace(
  init,
  `globalThis.helpers = {
  state, paletteRGB, averagePowerBins, associatedDetectionIndex, classifiedDetections,
  frequencyBox, frameFromSnapshot, addWaterfall, waterfallGeometry, observationCell,
  observationTrack, captureBudgetEstimate, stopReasonText, trainingOnly,
  rankedComparisonEntries, liveModelRecommendations, liveSampleMetrics, recordingSelectionWithAdditions,
  topologyAvailable, validateTopologyIds, selectedTopologyIds, prepareExperimentDraft, cleanupDeletionPlan,
  normalizeOptimizationConfig, optimizationBaseConfig, optimizationBestConfig, selectionObjectiveLabel, partialOptimizationReports,
  normalizeExecutionConfig, telemetrySummary, phaseTimingRows, phaseTime, resourceMetricSummary,
  analysisDecision, analysisStatus, analysisConclusionRows, analysisStageOrder,
  matchedSignalClassifications, analysisExpertSelection, analysisSettingsPayload, analysisCoverageSummary,
  analysisPredictionExplanation, presentChildren, replaceContent, analysisLabel, analysisScoreKind
};`,
);
const sandbox = { Intl, console, Set, Map, Date, window: {}, document: {} };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: sourcePath });
const f = sandbox.helpers;
const labels = {
  waveform_family: [
    { id: "multicarrier", name: "Mehrträgerartig" },
    { id: "single_carrier", name: "Einträgerartig" },
  ],
};
assert.equal(
  f.analysisLabel("multicarrier", "waveform_family", labels),
  "Mehrträgerartig",
);
assert.equal(
  f.analysisLabel("single_carrier", "waveform_family", labels),
  "Einträgerartig",
);
assert.equal(
  f.analysisLabel("Eigene Klasse", "modulation", labels),
  "Eigene Klasse",
);
assert.equal(
  f.analysisScoreKind("heuristic_support_not_probability"),
  "heuristischer Hinweis, keine Wahrscheinlichkeit",
);
assert.ok(
  f
    .analysisScoreKind("uncalibrated_model_scores_not_averaged")
    .includes("nicht gemittelt"),
);
assert.deepEqual(
  Array.from(f.presentChildren([null, undefined, false, 0, "", "Signal"])),
  [0, "", "Signal"],
);
let replaced;
f.replaceContent(
  {
    replaceChildren(...children) {
      replaced = children;
    },
  },
  null,
  "Status",
  null,
  undefined,
  0,
);
assert.deepEqual(
  Array.from(replaced),
  ["Status", 0],
  "Conditional absent nodes must not become literal null text",
);

// Averaging dB values would erase the stronger signal's actual contribution.
assert.ok(
  Math.abs(f.averagePowerBins([-20, -40], 1)[0] - -22.967086218813387) < 1e-8,
);
assert.equal(f.averagePowerBins([null, undefined, NaN], 1)[0], null);
for (const name of ["viridis", "cividis", "inferno"]) {
  let previous = -1;
  for (let i = 0; i < 256; i++) {
    const rgb = f.paletteRGB(name, i / 255);
    assert.ok(
      rgb.every(
        (value) => Number.isInteger(value) && value >= 0 && value <= 255,
      ),
    );
    const linear = rgb.map((value) => {
      const channel = value / 255;
      return channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4;
    });
    const luminance =
      0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    assert.ok(
      luminance >= previous - 0.002,
      `${name}: power-to-lightness mapping must not reverse`,
    );
    previous = luminance;
  }
}

// A classifier operating on one extracted channel cannot classify its neighbours.
const detections = [
  { frequency_hz: 100e6, bandwidth_hz: 20_000 },
  { frequency_hz: 100.5e6, bandwidth_hz: 40_000 },
  { frequency_hz: 100.8e6, bandwidth_hz: 10_000 },
];
const prediction = {
  label: "FM",
  center_frequency_hz: 100.5e6,
  sample_rate: 50_000,
};
assert.equal(f.associatedDetectionIndex(detections, prediction), 1);
const classified = f.classifiedDetections(detections, prediction);
assert.deepEqual(
  Array.from(classified, (value) => value.detected_modulation),
  ["unbekannt", "FM", "unbekannt"],
);
assert.equal(
  f.associatedDetectionIndex(detections, { label: "AM" }),
  -1,
  "Untuned predictions have no RF association",
);
assert.equal(
  f.associatedDetectionIndex(detections, { ...prediction, label: "unknown" }),
  -1,
);
assert.equal(
  f.associatedDetectionIndex(detections, {
    ...prediction,
    center_frequency_hz: 300e6,
  }),
  -1,
);
assert.equal(
  f.associatedDetectionIndex(detections, {
    ...prediction,
    confidence_kind: "insufficient_segment",
  }),
  -1,
);

for (const detection of [
  { frequency_min_hz: 90, frequency_max_hz: 110 },
  { frequency_min_hz: 195, frequency_max_hz: 220 },
  { frequency_min_hz: 140, frequency_max_hz: 140.0001 },
]) {
  const box = f.frequencyBox(detection, 100, 200, 65, 500);
  assert.ok(box.x >= 65 && box.width > 0 && box.x + box.width <= 565 + 1e-9);
}
assert.equal(
  f.frequencyBox({ frequency_hz: 20, bandwidth_hz: 2 }, 100, 200, 65, 500),
  null,
);

const epoch = Date.parse("2026-11-20T10:00:00Z");
const snapshot = {
  timestamp: new Date(epoch).toISOString(),
  source: "synthetic",
  sample_rate: 256_000,
  block_sample_count: 4096,
  center_frequency_hz: 100e6,
  frequencies_hz: [99_999_000, 100_000_000, 100_001_000],
  power_dbfs: [-60, -20, -60],
  detections,
  classification: prediction,
};
// FFTs may cover only the first half of a receiver block. The time box must
// describe that analyzed prefix, never its unanalyzed tail or publication time.
const analysisTime = new Date(epoch + 256).toISOString();
const analyzedPrefix = f.frameFromSnapshot({
  ...snapshot,
  timestamp: new Date(epoch + 512).toISOString(),
  analysis_timestamp: analysisTime,
  analysis_sample_count: 65536,
  analysis_duration_seconds: 0.256,
  block_sample_count: 131072,
  block_duration_seconds: 0.512,
  rf_timestamp_verified: false,
});
assert.equal(analyzedPrefix.timeMs, epoch + 256);
assert.equal(analyzedPrefix.id, analysisTime);
assert.equal(analyzedPrefix.duration, 0.256);
assert.equal(analyzedPrefix.timestampVerified, false);
assert.equal(
  f.frameFromSnapshot({
    ...snapshot,
    analysis_sample_count: 65536,
    block_sample_count: 131072,
    block_duration_seconds: 0.512,
  }).duration,
  0.256,
  "Analyzed sample count takes priority over the full receiver block",
);

assert.equal(f.addWaterfall(snapshot), true);
assert.equal(
  f.addWaterfall(snapshot),
  false,
  "Polling an unchanged frame cannot manufacture another row",
);
assert.equal(f.state.waterfall.length, 1);
assert.equal(f.state.waterfall[0].duration, 4096 / 256_000);
assert.equal(
  f.addWaterfall({ ...snapshot, timestamp: null }),
  false,
  "Missing capture time must not use the browser clock",
);
assert.equal(f.state.waterfall.length, 1);
assert.equal(
  f.addWaterfall({
    ...snapshot,
    timestamp: new Date(epoch + 20_000).toISOString(),
  }),
  true,
);
assert.equal(
  f.state.waterfall.length,
  2,
  "A twenty-second observation gap must stay a gap, not twenty fabricated rows",
);
assert.equal(f.state.waterfall[0].timeMs - f.state.waterfall[1].timeMs, 20_000);
assert.equal(
  f.addWaterfall({
    ...snapshot,
    timestamp: new Date(epoch + 21_000).toISOString(),
    sample_rate: 512_000,
  }),
  true,
);
assert.equal(
  f.state.waterfall.length,
  1,
  "Retuning/rate changes reset incompatible waterfall history",
);
for (let i = 0; i < 250; i++)
  f.addWaterfall({
    ...snapshot,
    timestamp: new Date(epoch + i * 1000).toISOString(),
  });
assert.equal(
  f.state.waterfall.length,
  240,
  "History is bounded on laptop memory",
);
const frame = {
  ...f.state.waterfall[0],
  detections: [...detections, ...detections],
};
const geometry = f.waterfallGeometry(frame, [], 300, 410, {
  windowSeconds: 180,
  overlay: true,
});
assert.equal(geometry.badges.length, 4);
for (let i = 0; i < geometry.badges.length; i++) {
  const a = geometry.badges[i];
  assert.ok(
    a.x >= geometry.left && a.x + a.width <= 300 - geometry.right + 1e-8,
  );
  for (let j = i + 1; j < geometry.badges.length; j++) {
    const b = geometry.badges[j];
    assert.ok(
      a.x + a.width <= b.x ||
        b.x + b.width <= a.x ||
        a.y + a.height <= b.y ||
        b.y + b.height <= a.y,
    );
  }
}
assert.equal(
  f.observationCell(
    { timeMs: geometry.latestTime - 181_000, duration: 0.1 },
    geometry,
  ),
  null,
);
const observed = (timeMs) => ({ ...frame, timeMs, duration: 0.02 });
assert.equal(
  f.observationTrack(
    detections[0],
    [
      observed(frame.timeMs),
      observed(frame.timeMs - 1000),
      observed(frame.timeMs - 8000),
    ],
    geometry,
  ).length,
  2,
);

const budget = f.captureBudgetEstimate(5e6, 180, 512 * 1048576);
assert.equal(budget.bytesPerSecond, 40e6);
assert.equal(budget.requestedBytes, 7.2e9);
assert.equal(budget.budgetSeconds, 13.4217728);
assert.ok(f.stopReasonText("storage_limit").includes("Dateibudget"));

// Rankings supplied by validation remain independent of the final-test score.
const entries = [
  {
    topology_id: "fast",
    model_id: "fast-model",
    evaluation_available: true,
    score: { eligible_for_live: true },
    metrics: { accuracy: 0.1 },
    score_rank: 1,
    accuracy_rank: 2,
  },
  {
    topology_id: "accurate",
    model_id: "accurate-model",
    evaluation_available: true,
    score: { eligible_for_live: true },
    metrics: { accuracy: 0.99 },
    score_rank: 2,
    accuracy_rank: 1,
  },
  {
    topology_id: "unvalidated",
    model_id: "unvalidated-model",
    evaluation_available: false,
    score: { eligible_for_live: false },
    score_rank: null,
  },
];
const report = {
  rankings: { score: ["fast", "accurate"], accuracy: ["accurate", "fast"] },
  recommended_topology_ids: ["fast", "unvalidated", "accurate"],
};
assert.deepEqual(
  Array.from(
    f.rankedComparisonEntries(entries, report, "score"),
    (value) => value.entry.topology_id,
  ),
  ["fast", "accurate", "unvalidated"],
);
assert.deepEqual(
  Array.from(
    f.rankedComparisonEntries(entries, report, "accuracy"),
    (value) => value.entry.topology_id,
  ),
  ["accurate", "fast", "unvalidated"],
);
assert.deepEqual(Array.from(f.liveModelRecommendations(report, entries)), [
  "fast-model",
  "accurate-model",
]);
assert.equal(f.trainingOnly({ evaluation_available: false }), true);
assert.equal(
  f.liveSampleMetrics({
    rolling: {
      observations: 0,
      latency_ms_p50: 0,
      latency_ms_p95: 0,
      warmup: true,
    },
  }).p50,
  null,
);

// Oversized library selections are rejected atomically, never silently truncated.
const selection = new Set(
  Array.from({ length: 999 }, (_, i) => `recording-${i}`),
);
const fullSelection = f.recordingSelectionWithAdditions(selection, [
  "one-more",
]);
assert.equal(fullSelection.size, 1000);
assert.equal(
  selection.size,
  999,
  "Preview does not mutate the existing selection",
);
assert.equal(
  f.recordingSelectionWithAdditions(fullSelection, ["too-many"]),
  null,
);
assert.equal(fullSelection.size, 1000);
assert.equal(
  f.recordingSelectionWithAdditions(
    new Set(),
    Array.from({ length: 1001 }, (_, i) => String(i)),
  ),
  null,
);

// Select-all uses only actually available topologies; disabled ANN cannot leak
// back into an experiment through selected options or an imported draft.
const catalog = [
  { id: "baseline", name: "LDA", family: "feature_lda", status: "available" },
  { id: "ann", name: "CNN", family: "iq_cnn", status: "requires_torch" },
  { id: "custom-ann", name: "Custom CNN", family: "iq_cnn" },
];
assert.equal(f.topologyAvailable(catalog[0], false), true);
assert.equal(f.topologyAvailable(catalog[1], false), false);
assert.equal(f.topologyAvailable(catalog[2], false), false);
assert.deepEqual(
  Array.from(f.validateTopologyIds(["baseline"], catalog, false)),
  ["baseline"],
);
assert.throws(() => f.validateTopologyIds(["ann"], catalog, false), /PyTorch/);
assert.throws(
  () =>
    f.validateTopologyIds(
      Array.from({ length: 51 }, (_, i) => String(i)),
      catalog,
      false,
    ),
  /50/,
);
const options = [
  { value: "baseline", selected: true, disabled: false },
  { value: "ann", selected: true, disabled: true },
];
sandbox.document.querySelector = (selector) =>
  selector === "#experiment-topology" ? { options } : null;
assert.deepEqual(Array.from(f.selectedTopologyIds()), ["baseline"]);
options.forEach((option) => {
  option.selected = false;
});
assert.deepEqual(
  Array.from(f.selectedTopologyIds()),
  [],
  "Clear selection must stay intentionally empty",
);
const draft = {
  source: "recordings",
  topology_ids: ["baseline"],
  recording_ids: ["available-a", "missing-b"],
  segment_lengths: [2048],
  augmentation: { frequency_offset_hz: 50 },
};
const prepared = f.prepareExperimentDraft(
  draft,
  catalog,
  ["available-a"],
  false,
);
assert.deepEqual(Array.from(prepared.recordingIds), ["available-a"]);
assert.deepEqual(Array.from(prepared.missingRecordingIds), ["missing-b"]);
assert.deepEqual(
  Array.from(
    f.prepareExperimentDraft(
      { ...draft, topology_ids: [] },
      catalog,
      ["available-a"],
      false,
    ).topologyIds,
  ),
  [],
  "Drafts preserve a deliberately cleared topology selection",
);
assert.throws(
  () =>
    f.prepareExperimentDraft(
      { ...draft, topology_ids: ["ann"] },
      catalog,
      ["available-a"],
      false,
    ),
  /PyTorch/,
);
assert.throws(
  () =>
    f.prepareExperimentDraft(
      { ...draft, augmentation: { frequency_offset_hz: NaN } },
      catalog,
      ["available-a"],
      false,
    ),
  /Kanalparameter/,
);
assert.equal(
  draft.augmentation.frequency_offset_hz,
  50,
  "Validation must not partially mutate the draft",
);

// Cleanup sends exactly the reviewed immutable file identities, including labels.
const preview = {
  max_duration_seconds: 0.5,
  recordings: [
    {
      id: "short-a",
      filename: "short-a.sigmf-meta",
      duration_seconds: 0.4999,
      bytes: 100,
      verified: true,
      label: "AM",
      fingerprint: "fp-a",
    },
    {
      id: "short-b",
      filename: "short-b.sigmf-meta",
      duration_seconds: 0.02,
      bytes: 20,
      fingerprint: "fp-b",
    },
  ],
  count: 2,
  total_bytes: 120,
  expected_files: { "short-a": "fp-a", "short-b": "fp-b" },
  skipped: [{ id: "protected", reason: "Experiment" }],
  blocked_reason: null,
};
const plan = f.cleanupDeletionPlan(preview);
assert.deepEqual(Array.from(plan.recording_ids), ["short-a", "short-b"]);
assert.equal(plan.max_duration_seconds, 0.5);
assert.equal(plan.expected_files["short-a"], "fp-a");
assert.ok(
  Object.isFrozen(plan) &&
    Object.isFrozen(plan.recording_ids) &&
    Object.isFrozen(plan.expected_files),
);
preview.recordings.push({
  id: "newly-closed",
  duration_seconds: 0.1,
  fingerprint: "fp-new",
});
assert.equal(
  plan.recording_ids.length,
  2,
  "A library refresh cannot add newly closed files to an existing confirmation",
);
preview.recordings.pop();
for (const invalid of [0.5, 0.5001, -0.1, NaN, undefined, "0.4"]) {
  assert.throws(
    () =>
      f.cleanupDeletionPlan({
        ...preview,
        recordings: [{ ...preview.recordings[0], duration_seconds: invalid }],
        count: 1,
      }),
    /Vorschau/,
  );
}
assert.throws(
  () => f.cleanupDeletionPlan({ ...preview, blocked_reason: "Empfang läuft" }),
  /Empfang/,
);
assert.throws(
  () => f.cleanupDeletionPlan({ ...preview, expected_files: {} }),
  /Dateiabgleich/,
);
assert.throws(
  () =>
    f.cleanupDeletionPlan({
      ...preview,
      expected_files: { "short-a": "changed", "short-b": "fp-b" },
    }),
  /Dateiabgleich/,
);
assert.throws(
  () => f.cleanupDeletionPlan({ ...preview, count: 3 }),
  /unvollständig/,
);
assert.throws(
  () => f.cleanupDeletionPlan({ ...preview, max_duration_seconds: 1 }),
  /ungültig/,
);
assert.equal(
  f.cleanupDeletionPlan({
    ...preview,
    recordings: [],
    count: 0,
    expected_files: {},
  }).recording_ids.length,
  0,
);

// Automatic optimization uses independent validation and a bounded portable search config.
assert.equal(f.normalizeOptimizationConfig({}).enabled, false);
assert.throws(
  () => f.normalizeOptimizationConfig({ enabled: true, max_trial: 1 }),
  /Unbekannte HPO-Konfigurationsfelder/,
);
assert.throws(
  () =>
    f.normalizeOptimizationConfig({
      enabled: true,
      search_space: { lr_min: 0.0001 },
    }),
  /Unbekannte HPO-Suchraumfelder/,
);
assert.equal(
  f.selectionObjectiveLabel({ selection_objective: "score" }),
  "Val-Score",
);
assert.equal(
  f.selectionObjectiveLabel({ selection_objective: "balanced_accuracy" }),
  "Val-bal. Genauigkeit",
);
assert.equal(f.selectionObjectiveLabel({}), "Val-F1");
const partialSearch = {
  enabled: true,
  best_trial: 1,
  best_config: {
    segment_length: 1024,
    learning_rate: 0.001,
    batch_size: 16,
    epochs: 3,
  },
  trials: [{ trial: 1, status: "completed" }],
};
const partial = f.partialOptimizationReports(
  {
    partial_reports: [
      {
        path: "topology-a/report.json",
        kind: "report",
        report: { optimization: partialSearch },
      },
      {
        path: "topology-a/optimization.json",
        kind: "optimization",
        report: { ...partialSearch, stop_reason: "user_cancellation" },
      },
      {
        path: "topology-b/optimization.json",
        kind: "optimization",
        report: { ...partialSearch, topology_id: "b" },
      },
    ],
  },
  {},
);
assert.equal(
  partial.length,
  2,
  "Persisted optimization and model reports for one topology are deduplicated",
);
assert.equal(partial[0].optimization.stop_reason, "user_cancellation");
const hpo = f.normalizeOptimizationConfig({ enabled: true }, 2048);
assert.equal(hpo.method, "random");
assert.equal(hpo.max_trials, 12);
assert.equal(hpo.timeout_seconds, 300);
assert.equal(
  f.normalizeOptimizationConfig({ enabled: true, timeout_seconds: 0.5 })
    .timeout_seconds,
  0.5,
);
const baseFields = f.optimizationBaseConfig(hpo);
assert.deepEqual(Array.from(baseFields.segment_lengths), [1024]);
assert.deepEqual(Array.from(baseFields.learning_rates), [0.00001]);
assert.deepEqual(Array.from(baseFields.batch_sizes), [16]);
assert.equal(baseFields.epochs, 5);
assert.equal(hpo.objective, "score");
assert.deepEqual(
  Array.from(hpo.search_space.segment_lengths),
  [1024, 2048, 4096],
);
assert.deepEqual(
  Array.from(
    f.normalizeOptimizationConfig({ enabled: false }, 65536).search_space
      .segment_lengths,
  ),
  [32768, 65536],
);
assert.equal(
  f.normalizeOptimizationConfig({ enabled: true, timeout_seconds: 0 })
    .timeout_seconds,
  0,
);
assert.throws(
  () => f.normalizeOptimizationConfig({ enabled: "yes" }),
  /boolescher/,
);
assert.throws(
  () => f.normalizeOptimizationConfig({ enabled: true, method: "bayesian" }),
  /Random Search/,
);
assert.throws(
  () => f.normalizeOptimizationConfig({ enabled: true, max_trials: 51 }),
  /1–50/,
);
assert.throws(
  () => f.normalizeOptimizationConfig({ enabled: true, timeout_seconds: -1 }),
  /Zeitbudget/,
);
assert.throws(
  () =>
    f.normalizeOptimizationConfig({
      enabled: true,
      search_space: { learning_rate_min: 0.01, learning_rate_max: 0.001 },
    }),
  /minimale HPO-Lernrate/,
);
assert.throws(
  () =>
    f.normalizeOptimizationConfig({
      enabled: true,
      search_space: {
        segment_lengths: [256, 512, 768, 1024, 1280, 1536, 1792, 2048, 2304],
      },
    }),
  /1–8/,
);
assert.equal(
  f.normalizeOptimizationConfig({
    enabled: true,
    search_space: {
      segment_lengths: [256, 512, 768, 1024, 1280, 1536, 1792, 2048],
    },
  }).search_space.segment_lengths.length,
  8,
);
assert.throws(
  () =>
    f.normalizeOptimizationConfig({
      enabled: true,
      search_space: { epochs: [0] },
    }),
  /epochs/,
);
const hpoDraft = {
  ...draft,
  evaluation_mode: "independent",
  optimization: {
    enabled: true,
    max_trials: 8,
    objective: "macro_f1",
    timeout_seconds: 90,
    search_space: {
      segment_lengths: [512, 1024],
      learning_rate_min: 0.0001,
      learning_rate_max: 0.003,
      batch_sizes: [16, 32],
      epochs: [2, 4],
    },
  },
  experiment_notes: "AM/FM-Versuch · Empfänger und Antenne dokumentiert",
};
const normalizedDraft = f.prepareExperimentDraft(
  hpoDraft,
  catalog,
  ["available-a"],
  false,
);
assert.equal(normalizedDraft.optimization.enabled, true);
assert.equal(normalizedDraft.optimization.objective, "macro_f1");
assert.deepEqual(
  Array.from(normalizedDraft.optimization.search_space.epochs),
  [2, 4],
);
assert.equal(
  normalizedDraft.config.experiment_notes,
  hpoDraft.experiment_notes,
);
assert.throws(
  () =>
    f.prepareExperimentDraft(
      { ...hpoDraft, evaluation_mode: "training_only" },
      catalog,
      ["available-a"],
      false,
    ),
  /unabhängige Bewertung/,
);
assert.throws(
  () =>
    f.prepareExperimentDraft(
      { ...hpoDraft, experiment_notes: "x".repeat(12001) },
      catalog,
      ["available-a"],
      false,
    ),
  /12000/,
);
assert.throws(
  () =>
    f.prepareExperimentDraft(
      {
        ...hpoDraft,
        optimization: {
          enabled: true,
          search_space: { learning_rate_min: NaN },
        },
      },
      catalog,
      ["available-a"],
      false,
    ),
  /Lernraten/,
);
const best = f.optimizationBestConfig({
  best_trial: 3,
  best_config: {
    segment_length: 1024,
    learning_rate: 0.002,
    batch_size: 16,
    epochs: 4,
  },
});
assert.equal(best.segment_length, 1024);
assert.equal(best.learning_rate, 0.002);
assert.equal(best.batch_size, 16);
assert.equal(best.epochs, 4);
assert.throws(
  () => f.optimizationBestConfig({ best_config: null }),
  /beste Konfiguration/,
);
assert.throws(
  () =>
    f.optimizationBestConfig({
      best_config: {
        segment_length: 10,
        learning_rate: 0.002,
        batch_size: 16,
        epochs: 4,
      },
    }),
  /unvollständig/,
);

// Resource limits are portable requests; zero remains Auto and is not resolved in the UI.
const automaticExecution = f.normalizeExecutionConfig();
assert.equal(automaticExecution.mode, "auto");
for (const key of [
  "max_workers",
  "cpu_threads",
  "preprocessing_workers",
  "gpu_workers",
])
  assert.equal(automaticExecution[key], 0);
const execution = {
  mode: "parallel",
  max_workers: 4,
  cpu_threads: 6,
  preprocessing_workers: 2,
  gpu_workers: 1,
};
const executionDraft = f.prepareExperimentDraft(
  { ...hpoDraft, execution },
  catalog,
  ["available-a"],
  false,
);
assert.equal(executionDraft.execution.mode, "parallel");
assert.equal(executionDraft.execution.max_workers, 4);
assert.equal(executionDraft.execution.cpu_threads, 6);
assert.equal(executionDraft.execution.preprocessing_workers, 2);
assert.equal(executionDraft.execution.gpu_workers, 1);
assert.equal(
  f.prepareExperimentDraft(draft, catalog, ["available-a"], false).execution
    .mode,
  "auto",
  "Legacy drafts default to automatic scheduling",
);
assert.equal(
  execution.max_workers,
  4,
  "Validation does not mutate requested resources",
);
assert.equal(f.normalizeExecutionConfig(null).mode, "auto");
for (const invalid of [
  [],
  { mode: "threads" },
  { max_workers: 9 },
  { max_workers: 1.5 },
  { cpu_threads: 33 },
  { preprocessing_workers: 9 },
  { gpu_workers: 5 },
  { gpu_workers: -1 },
  { max_workers: true },
  { max_worker: 2 },
  { max_workers: NaN },
])
  assert.throws(() => f.normalizeExecutionConfig(invalid));
assert.throws(
  () =>
    f.prepareExperimentDraft(
      { ...hpoDraft, execution: { mode: "parallel", max_workers: 9 } },
      catalog,
      ["available-a"],
      false,
    ),
  /execution.max_workers/,
);

// Current resource telemetry distinguishes missing, stale and actual zero values.
const sampledAt = "2026-11-20T12:00:00Z",
  now = Date.parse(sampledAt) + 1000;
const measurement = {
  sampled_at: sampledAt,
  interval_seconds: 2,
  cpu: {
    system_percent: 0,
    process_percent: 0,
    app_system_percent: 0,
    process_count: 1,
    process_percent_scope: "application_process_tree_one_logical_cpu=100%",
    logical_cpus: 8,
  },
  gpu: {
    available: true,
    provider: "nvidia-smi",
    scope: "whole_device",
    devices: [
      {
        index: 0,
        name: "Measured GPU",
        utilization_percent: 0,
        memory_used_bytes: 0,
        memory_total_bytes: 8 * 1024 ** 3,
      },
    ],
  },
};
const measured = f.telemetrySummary(measurement, now);
assert.equal(measured.fresh, true);
assert.equal(measured.cpu.systemPercent, 0);
assert.equal(measured.cpu.processPercent, 0);
assert.equal(measured.cpu.appSystemPercent, 0);
assert.equal(measured.cpu.processCount, 1);
assert.equal(measured.gpu.devices[0].utilizationPercent, 0);
assert.equal(measured.gpu.devices[0].memoryUsedBytes, 0);
const busyTree = f.telemetrySummary(
  {
    ...measurement,
    cpu: {
      ...measurement.cpu,
      process_percent: 250,
      app_system_percent: 31.25,
      process_count: 5,
    },
  },
  now,
);
assert.equal(busyTree.cpu.processPercent, 250);
assert.equal(busyTree.cpu.appSystemPercent, 31.25);
const stale = f.telemetrySummary(measurement, Date.parse(sampledAt) + 11000);
assert.equal(stale.stale, true);
assert.equal(stale.cpu.systemPercent, null);
assert.equal(stale.gpu.devices[0].utilizationPercent, null);
assert.equal(stale.gpu.devices[0].memoryUsedBytes, null);
assert.equal(
  f.telemetrySummary(measurement, Date.parse(sampledAt) - 1000).future,
  true,
);
const backendAgeSample = f.telemetrySummary(
  { ...measurement, age_seconds: 1, stale: false },
  Date.parse(sampledAt) - 1000,
);
assert.equal(backendAgeSample.fresh, true);
assert.equal(backendAgeSample.future, false);
assert.equal(backendAgeSample.ageSeconds, 1);
const backendStale = f.telemetrySummary(
  { ...measurement, age_seconds: 7, stale: true },
  now,
);
assert.equal(backendStale.stale, true);
assert.equal(backendStale.cpu.systemPercent, null);
assert.equal(f.telemetrySummary({}, now).cpu.systemPercent, null);
const unavailable = f.telemetrySummary(
  {
    ...measurement,
    gpu: {
      available: false,
      provider: "nvidia-smi",
      reason: "Not installed",
      devices: measurement.gpu.devices,
    },
  },
  now,
);
assert.equal(unavailable.gpu.devices.length, 0);
assert.equal(unavailable.gpu.reason, "Not installed");
const unsupported = f.telemetrySummary(
  {
    ...measurement,
    gpu: {
      ...measurement.gpu,
      devices: [
        {
          index: 0,
          name: "Known GPU",
          utilization_percent: null,
          memory_used_bytes: null,
          memory_total_bytes: null,
        },
      ],
    },
  },
  now,
);
assert.equal(unsupported.gpu.available, true);
assert.equal(unsupported.gpu.devices[0].utilizationPercent, null);
assert.equal(
  f.telemetrySummary(
    { ...measurement, cpu: { system_percent: NaN, process_percent: -1 } },
    now,
  ).cpu.systemPercent,
  null,
);

// Missing phase measurements are not turned into zero or added into a fake wall time.
const phases = f.phaseTimingRows({
  phase_timings: {
    preprocessing_seconds: 0,
    device_transfer_seconds: 0.0002,
    training_epoch_seconds: 1.5,
    validation_seconds: null,
    cpu_benchmark_seconds: NaN,
    checkpoint_seconds: -1,
  },
});
assert.deepEqual(
  Array.from(phases, (row) => row.key),
  [
    "preprocessing_seconds",
    "device_transfer_seconds",
    "training_epoch_seconds",
  ],
);
assert.equal(f.phaseTime(null), "—");
assert.equal(f.phaseTime(0), "0 ms");
assert.equal(f.phaseTime(0.0002), "0,2 ms");

const noSamples = f.resourceMetricSummary({
  sample_count: 0,
  mean: 0,
  maximum: 0,
});
assert.equal(noSamples.mean, null);
assert.equal(noSamples.maximum, null);
const realZero = f.resourceMetricSummary({
  sample_count: 2,
  mean: 0,
  maximum: 0,
});
assert.equal(realZero.mean, 0);
assert.equal(realZero.maximum, 0);
assert.equal(realZero.count, 2);
assert.equal(
  f.resourceMetricSummary({ sample_count: 2, mean: NaN, maximum: null }).mean,
  null,
);
assert.equal(f.resourceMetricSummary(undefined).count, 0);

const warmProcesses = f.telemetrySummary(
  {
    ...measurement,
    cpu: {
      ...measurement.cpu,
      warming_processes: 2,
      reason: "Neu gestartete Prozesse benötigen zwei Messpunkte.",
    },
  },
  now,
);
assert.equal(warmProcesses.cpu.warmingProcesses, 2);
assert.ok(warmProcesses.cpu.reason.includes("zwei Messpunkte"));
const samplerError = f.telemetrySummary(
  { ...measurement, sampler_error: "Messfehler" },
  now,
);
assert.ok(samplerError.reason.includes("Messfehler"));

// Multi-signal decisions keep levels separate. A modulation candidate cannot
// supply a protocol, service or emitter label, even if a stale label is present.
const hierarchy = f.analysisConclusionRows({
  label: "FM",
  status: "candidate",
  conclusions: {
    modulation: { status: "candidate", label: "FM", reason: "Netzvotum" },
    protocol: { status: "unknown", label: "DVB-T" },
    service: { status: "conflict", label: "Radio" },
    emitter: {
      status: "observed",
      label: "Decoder-Beleg",
      evidence_ids: ["ev1"],
    },
  },
});
assert.equal(hierarchy.length, 5);
assert.equal(hierarchy[1].label, "FM");
assert.equal(hierarchy[1].status, "candidate");
assert.equal(hierarchy[2].label, "Unbekannt");
assert.equal(hierarchy[3].label, "Unbekannt");
assert.equal(hierarchy[4].label, "Decoder-Beleg");
assert.equal(f.analysisStatus("candidate"), "Modellkandidat");
assert.equal(f.analysisConclusionRows({ label: "AM" })[1].label, "Unbekannt");
const ofdmAndQam = f.analysisConclusionRows({
  conclusions: {
    waveform_family: { status: "candidate", label: "OFDM" },
    modulation: { status: "candidate", label: "QAM" },
  },
});
assert.equal(ofdmAndQam[0].label, "OFDM");
assert.equal(ofdmAndQam[1].label, "QAM");
assert.ok(
  ofdmAndQam.slice(0, 2).every((row) => row.status === "candidate"),
  "Waveform and modulation candidates remain distinct statements",
);

const graph = f.analysisStageOrder({
  stages: [
    { id: "fusion", parents: ["models", "hypotheses"] },
    { id: "models", parents: ["routing"] },
    { id: "routing", parents: ["hypotheses"] },
    { id: "hypotheses", parents: ["quality"] },
    { id: "quality", parents: [] },
  ],
});
assert.deepEqual(
  Array.from(graph, (v) => v.id),
  ["quality", "hypotheses", "routing", "models", "fusion"],
);
assert.ok(graph.every((v) => !v.dependency_unresolved));
assert.ok(
  f
    .analysisStageOrder({
      stages: [
        { id: "a", parents: ["b"] },
        { id: "b", parents: ["a"] },
      ],
    })
    .every((v) => v.dependency_unresolved),
);
assert.equal(
  f.analysisStageOrder({ stages: [{ id: "child", parents: ["missing"] }] })[0]
    .dependency_unresolved,
  true,
);

const multiSnapshot = {
  timestamp: new Date(epoch).toISOString(),
  sample_rate: 2e6,
  session_id: "now",
};
const decisions = [
  {
    channel_id: "fm",
    session_id: "now",
    observed_at: new Date(epoch).toISOString(),
    center_frequency_hz: 100.5e6,
    bandwidth_hz: 40000,
    sample_rate: 50000,
    decision: {
      conclusions: { modulation: { status: "candidate", label: "FM" } },
    },
  },
  {
    channel_id: "am",
    session_id: "now",
    observed_at: new Date(epoch - 500).toISOString(),
    center_frequency_hz: 100e6,
    bandwidth_hz: 20000,
    sample_rate: 25000,
    decision: {
      conclusions: { modulation: { status: "candidate", label: "AM" } },
    },
  },
];
const multi = f.classifiedDetections(
  detections,
  prediction,
  decisions,
  multiSnapshot,
);
assert.deepEqual(
  Array.from(multi, (v) => v.detected_modulation),
  ["AM", "FM", "Unbekannt"],
);
assert.equal(
  f.matchedSignalClassifications(
    detections,
    decisions.slice(0, 1),
    multiSnapshot,
  ).size,
  1,
  "One channel observation must never be cloned to all detections",
);
assert.ok(
  f
    .classifiedDetections(detections, prediction, [], multiSnapshot)
    .every((v) => !v.modulation_evaluated),
  "Present but empty pipeline data suppresses legacy fallback",
);
for (const mutation of [
  { observed_at: new Date(epoch - 2001).toISOString() },
  { observed_at: null },
  { session_id: "old" },
  { sample_rate: 1000 },
  { sample_rate: 3e6 },
  { center_frequency_hz: 500e6 },
])
  assert.equal(
    f.matchedSignalClassifications(
      detections,
      [{ ...decisions[0], ...mutation }],
      multiSnapshot,
    ).size,
    0,
  );
assert.equal(f.matchedSignalClassifications(detections, decisions, {}).size, 0);
assert.equal(
  f.matchedSignalClassifications(
    [{ ...detections[1], channel_id: "other" }],
    decisions,
    multiSnapshot,
  ).size,
  0,
);

const experts = f.analysisExpertSelection(
  [{ id: "m1" }],
  [
    {
      model_id: "m1",
      enabled: false,
      level: "protocol",
      families: ["chirp", "chirp"],
    },
    { model_id: "imported", enabled: true, level: "modulation", families: [] },
  ],
);
assert.equal(
  experts[0].enabled,
  false,
  "A disabled active model also blocks its automatic fallback",
);
assert.equal(experts[0].families.length, 1);
assert.equal(experts[1].available, false);
const waveformExpert = f.analysisExpertSelection(
  [{ id: "ofdm" }],
  [{ model_id: "ofdm", enabled: true, level: "waveform_family", families: [] }],
)[0];
assert.equal(waveformExpert.level, "waveform_family");
assert.equal(
  f.analysisExpertSelection([{ id: "default" }], [{ model_id: "default" }])[0]
    .level,
  "modulation",
);
assert.equal(
  f.analysisExpertSelection([{ id: "m1" }], []).length,
  0,
  "Catalogue loading never selects models",
);
const analysisConfig = {
  enabled: true,
  use_priors: false,
  use_active_model: true,
  start_frequency_hz: 0,
  stop_frequency_hz: 6e9,
  max_signals_per_cycle: 4,
  interval_seconds: 0.5,
  min_model_score: 0.65,
  min_margin: 0.15,
  budget_ms: 500,
  max_models: 3,
  require_evaluated_models: true,
};
const featureOnly = f.analysisSettingsPayload(
  { ...analysisConfig, budget_ms: 0, max_models: 0 },
  experts,
);
assert.equal(
  f.analysisSettingsPayload(analysisConfig, [waveformExpert]).experts[0].level,
  "waveform_family",
);
assert.throws(() =>
  f.analysisSettingsPayload(analysisConfig, [
    { ...waveformExpert, level: "invalid" },
  ]),
);
assert.equal(featureOnly.policy.budget_ms, 0);
assert.equal(featureOnly.policy.max_models, 0);
assert.equal(
  featureOnly.policy.explain_models,
  false,
  "Model influences stay opt-in",
);
assert.equal(
  f.analysisSettingsPayload({ ...analysisConfig, explain_models: true }, [])
    .policy.explain_models,
  true,
);
assert.equal(featureOnly.experts[0].enabled, false);
assert.equal(Object.hasOwn(featureOnly.experts[0], "available"), false);
const registeredExperts = Array.from({ length: 64 }, (_, index) => ({
  model_id: `model-${index}`,
  enabled: false,
  level: "modulation",
  families: [],
}));
assert.equal(
  f.analysisSettingsPayload(analysisConfig, registeredExperts).experts.length,
  64,
);
assert.throws(() =>
  f.analysisSettingsPayload(analysisConfig, [
    ...registeredExperts,
    { model_id: "overflow", families: [] },
  ]),
);
for (const mutation of [
  { start_frequency_hz: 6e9 },
  { stop_frequency_hz: 7e9 },
  { max_signals_per_cycle: 17 },
  { max_signals_per_cycle: 1.5 },
  { interval_seconds: 0 },
  { min_model_score: 0.49 },
  { max_models: 9 },
  { budget_ms: -1 },
])
  assert.throws(() =>
    f.analysisSettingsPayload({ ...analysisConfig, ...mutation }, []),
  );
const noCoverage = f.analysisCoverageSummary({
  requested_range: { start_frequency_hz: 0, stop_frequency_hz: 6e9 },
  current_window: null,
  simultaneous: false,
  full_range_covered: false,
});
assert.equal(noCoverage.active, false);
assert.equal(noCoverage.fullRangeCovered, false);
assert.equal(noCoverage.current, "Kein beobachtetes Empfangsfenster");
assert.equal(noCoverage.simultaneous, false);
const stoppedCoverage = f.analysisCoverageSummary({
  current_window: {
    start_frequency_hz: 99e6,
    stop_frequency_hz: 101e6,
    active: false,
  },
});
assert.equal(
  stoppedCoverage.active,
  false,
  "A remembered receiver window is not live coverage",
);

const attributionSource = {
  method: "local gradient",
  contributions: Array.from({ length: 20 }, (_, index) => ({
    window: index,
    magnitude: index / 20,
  })),
};
const attributionPreview = f.analysisPredictionExplanation({
  explanation: attributionSource,
});
assert.equal(attributionPreview.total_contributions, 20);
assert.equal(attributionPreview.contributions.length, 12);
assert.equal(
  attributionPreview.contributions[11].window,
  11,
  "Preview preserves order and discloses truncation",
);
assert.equal(
  attributionSource.contributions.length,
  20,
  "Preview must retain complete exported data",
);
assert.equal(f.analysisPredictionExplanation({ explanation: null }), null);
assert.equal(f.analysisPredictionExplanation({}), null);

const staticHTML = fs.readFileSync(
  path.join(__dirname, "..", "signalclassifier", "static", "index.html"),
  "utf8",
);
assert.match(
  staticHTML,
  /<tbody[^>]*id="reception-analysis-body"/,
  "Multi-signal rows require a real table body",
);
for (const id of [
  "view-analyse",
  "analysis-settings-form",
  "analysis-scan-form",
  "analysis-signals-body",
  "analysis-detail",
  "analysis-explain-models",
  "rx-analysis-max-channels",
  "analysis-scan-max-channels",
]) {
  assert.equal(
    staticHTML.split(`id="${id}"`).length,
    2,
    `${id} exists exactly once`,
  );
}

console.log(
  "Frontend pure-logic regressions passed: waterfall, selection/drafts, cleanup, HPO, execution/telemetry, multi-signal hierarchy/association, expert policy and coverage.",
);

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
  normalizeOptimizationConfig, optimizationBaseConfig, optimizationBestConfig, selectionObjectiveLabel, partialOptimizationReports
};`,
);
const sandbox = { Intl, console, Set, Map, Date, window: {}, document: {} };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: sourcePath });
const f = sandbox.helpers;

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

console.log(
  "Frontend pure-logic regressions passed: waterfall, selection/drafts, reviewed cleanup, automatic HPO, capture budget, validation rankings and live warmup.",
);

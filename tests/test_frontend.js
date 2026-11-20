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
  rankedComparisonEntries, liveModelRecommendations, liveSampleMetrics, recordingSelectionWithAdditions
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

console.log(
  "Frontend pure-logic regressions passed: waterfall, channel association, clipping, capture budget, validation rankings and live warmup.",
);

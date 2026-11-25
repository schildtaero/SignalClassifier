# Initial implementation contracts

Python 3.11+, FastAPI, NumPy/SciPy, SQLite. Local offline-capable static German UI; no Node build. Optional PyTorch for neural training. No implicit synthetic fallback. Single receiver; default idle/live selection. Runtime state outside repository.

## Ownership

- Root: `app.py`, `db.py`, `labels.py`, CLI, packaging/setup, integration, README.
- Acquisition: `receivers.py`, `sigmf.py`, capture tests and capture docs.
- ML: `ml.py`, `topologies.py`, ML tests, topology docs.
- Drone: `drones.py`, drone tests, drone capability docs.
- UI: all `static/index.html`, `static/app.js`, `static/styles.css` (local Leaflet vendor supplied separately).

## Python interfaces

- `sigmf.read_recording(meta_path, start=0, count=None) -> (complex ndarray, metadata dict)`; `sigmf.write_recording(base_path, iq, sample_rate, center_frequency, annotations=None, extra=None) -> metadata_path`; safe cf32_le and ci16_le support, package import validated by root.
- `receivers.CaptureManager(data_dir)`; `.start(config: dict) -> dict`, `.stop() -> dict`, `.status() -> dict`, `.snapshot() -> dict`, `.list_recordings() -> list[dict]`. Thread-managed receiver and bounded recording. Config keys: `source` = `pluto|rsp1a|recording|synthetic`, `uri`, `recording_id` (root resolves to managed `recording_path`), `center_frequency_hz`, `sample_rate`, `gain_db`, `mode` = `monitor|wideband|energy`, `threshold_db`, `duration_seconds`, `synthetic_modulation` = `AM|FM`, `max_bytes`. snapshot includes frequency/spectrum arrays (`frequencies_hz`, `power_dbfs`), `detections`, and `classification` if available; root may classify `.latest_iq` (array) and `.latest_sample_rate` and `.latest_center_frequency`. live receiver errors must surface; no automatic synthetic fallback. Replay read must be bounded and paced. Recordings saved in `data_dir/recordings`.
- `ml.list_topologies() -> list[dict]`, `ml.save_topology(config: dict, models_dir) -> dict`, `ml.classify(iq, sample_rate, center_frequency_hz=None, use_priors=False, model_path=None) -> dict`. Result: `label`, `confidence` (heuristic labelled as such), `features`, `steps` list `{stage,decision,reason}`, `probabilities`, `elapsed_ms`. `ml.run_experiment(config: dict, output_dir, progress=None) -> dict`: config includes `topology_id`, `seed`, `samples_per_class`, `segment_length`, `snr_db` (list), `jitter` (list), `epochs`, `learning_rate`, `batch_size`, `device=auto|cpu|cuda`, and optional `recordings` (list with paths/labels/group IDs) and `use_priors`. Baseline always available, other models require torch. Persist model artifact + full report, return report with `model_path`, `metrics`, `hardware`, `trials`, `confusion_matrix`, `classes`, `config`. `ml.load_model_metadata(path) -> dict`. Topology descriptions distinguish dissertation-inspired adaptations from verified reproductions. Never unpickle untrusted files.
- `drones.capabilities() -> list[dict]`, `drones.DroneStore(db_path)`, `.ingest(payload: dict) -> dict`, `.tracks() -> list[dict]`. Validated normalized OpenDroneID JSON bridge plus real documented payload decoder where feasible; source and freshness required, no fabricated coordinates. UI capability fields: `protocol`, `status`, `hardware`, `position`, `video`, `notes`. UDP adapter optional explicit start/stop. Preserve drone vs operator/home coordinates and altitude datum. Offline Leaflet map blank/grid if no tile data; online tiles explicit opt-in. No universal proprietary/decryption claim.

## HTTP contract (root implements)

All APIs use JSON except upload/download. Errors `{detail: string}`.

- GET `/api/status`: `{version,receiver,hardware,counts:{recordings,labels,experiments,models},active_experiment}`
- GET `/api/receiver/status`, GET `/api/spectrum` (snapshot), POST `/api/receiver/start` config above, POST `/api/receiver/stop`.
- GET `/api/recordings` list; POST `/api/recordings/import` multipart `metadata` + `data`; GET `/api/recordings/{id}/download` SigMF ZIP; POST `/api/recordings/{id}/label` `{label,verified,notes}`; POST `/api/recordings/{id}/classify` `{model_id?,use_priors?}`.
- GET `/api/emitters` list seed service database; POST `/api/emitters` user entry; GET `/api/labels` annotation list; GET `/api/labels/suggest?frequency_hz=...` `{suggestions:[...]}`. Seed entries with `{id,name,service,standard,modulation,frequency_min_hz,frequency_max_hz,region,confidence,source,notes}`. Frequency hints unverified; confirmed recording labels separate.
- GET `/api/topologies` list; POST `/api/topologies` save validated custom config; GET `/api/topologies/{id}/download` JSON.
- GET `/api/experiments` list; POST `/api/experiments` config above (async return `{id,status:'queued'}`), GET `/api/experiments/{id}` report/status/progress; GET `/api/experiments/{id}/download` JSON report. Experiment source `synthetic|recordings`, `recording_ids` with verified labels for real runs. Errors visible. POST `/api/experiments/{id}/cancel` cooperative.
- GET `/api/models` list; GET `/api/models/{id}/download` portable ZIP (weights+topology+preprocessing+report); POST `/api/models/import` multipart `file`; POST `/api/models/{id}/activate`; status includes active model. GET `/api/benchmark` hardware and reference benchmark measurements.
- GET `/api/drones/capabilities`; GET `/api/drones/tracks`; POST `/api/drones/ingest` normalized JSON; GET `/api/drones/example` returns schema example, no auto ingest; GET `/api/drones/bridge` status; POST `/api/drones/bridge/start` optional explicit UDP config, POST `/api/drones/bridge/stop`.
- GET `/api/settings`, PUT `/api/settings` `{use_priors,region,station_lat,station_lon,online_map}`.

Frontend receives bounded latest-only spectrum frames over WebSocket; status/model metadata remain separately throttled. Generator status is cached; planning never opens hardware. Empty state shows unknown metrics as em dash, no seeded fake results. Buttons perform actual actions and catch errors. Render uploaded data as text, never raw HTML. Explicit synthetic experiment/demo label. Theme toggle, laptop responsive, offline local assets. Brand matching Kraken: dark #10161b, panels #171e24, border #2a343d, cyan #6dd6dc, left rail, technical console, light theme.

## Generator interfaces (0.7)

- `waveforms.catalogue/normalize_waveform_spec/estimate_bandwidth/generate_waveform`: pure native numeric/encoder profiles; return finite bounded complex IQ and separated facets/provenance. No device or RF operations.
- `onair.catalogue/normalize_spec/generate`: optional separately installed OnAir CLI with a pinned audited mode registry; synthetic export only, bounded subprocess/files/logs, explicit prefix provenance and fixed-payload limitations.
- `txhardware.PlutoTxAdapter`: explicit open/configure/start_cyclic/stop/close; cached status, one SDK owner, optional same-handle RX view. Manual gain ceiling minus nonnegative attenuation variations; SDK acknowledgements are not RF measurement.
- `generation.GenerationManager`: one exclusive receiver lease, immutable bounded plans, independent reader/mute coordinators, exact retained sample windows, partial evidence and generated-reference labels. No startup resumption. A hanging reader/cleanup retains its lease; unresolved RF state stays unknown.
- GET `/api/generator/catalog`, POST `/api/generator/plan`, POST `/api/generator/start` `{plan_id,plan_sha256,transmit_enabled:true,setup_confirmed:true}`, GET `/api/generator/status`, POST `/api/generator/stop`, GET `/api/generator/runs`, GET `/api/generator/runs/{id}`, GET `/api/generator/runs/{id}/download` (private JSON manifest).
- Recording `sc:generation` preserves target facet, expected label, source/payload roots, planned split, actual TX/RX settings, software-channel parameters, hashes and shutdown evidence. `verified` automatic labels mean usable controlled generator reference, with `human_verified:false`; they never establish independent protocol/RF verification.
- Experiment option `respect_generation_splits:true` requires recordings and `allow_same_session:true`; readiness/loader/HPO use one resolver after origin/duplicate union. Fixed external templates and controls remain training-only unless independent reference sources exist.

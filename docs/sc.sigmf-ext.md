# SignalClassifier capture metadata extension

- Namespace: `sc`
- Version: `1.0.0`
- Optional: yes; these fields do not change the core IQ byte format.
- Scope: all fields in the SigMF `global` object unless specified otherwise.

This extension records capture provenance and the processing of derived narrowband
clips. Frequencies and sample rates use Hz / samples per second; source indices
refer to the original receiver stream before filtering or decimation. A new
capture session does not establish an independent dataset group if it derives
from an existing recording.

| Field | Type | Meaning |
| --- | --- | --- |
| `sc:source` | string | `pluto`, `rsp1a`, `recording` or explicit `synthetic` |
| `sc:mode` | string | `monitor`, `wideband` or `energy` |
| `sc:session_id` | string | One capture session, shared across its clips |
| `sc:origin_group_id` | string | Original dataset group retained through replay/derivation |
| `sc:parent_recording_id` | string | Immediate original recording identifier for a replay |
| `sc:recording_id` | string | Stable identifier assigned when the recording is written, retained on import/export |
| `sc:origin_recording_id` | string | Original recording identity retained through replay/derivation; separate from the session group |
| `sc:origin_recording_resolution` | object | Whether a replay ancestor chain could be resolved (`resolved`), method and ancestor count; missing ancestors retain the best known identity with `resolved:false` |
| `sc:requested_gain_db` | number | Requested manual gain; not a calibrated antenna power |
| `sc:gap_free_verified` | boolean | Whether gap-free reception has been independently established; currently false |
| `sc:digital_scale` | string | Digital amplitude convention |
| `sc:synthetic_modulation` | string or null | Known generated modulation; provenance only |
| `sc:parent_center_frequency_hz` | number | Original wideband receiver center frequency |
| `sc:parent_sample_rate` | number | Original receiver sample rate |
| `sc:source_sample_start` | unsigned integer | First source sample supplied to a channelizer |
| `sc:source_sample_stop` | unsigned integer | Exclusive end of source samples supplied to the clip |
| `sc:selected_bandwidth_hz` | number | Selection width including detection guard margin |
| `sc:decimation` | positive integer | Original sample rate / output sample rate |
| `sc:fir_group_delay_input_samples` | number | Causal FIR group delay in original sample units |
| `sc:detection_threshold_db` | number | Threshold above median FFT-bin power |
| `sc:detection_method` | string | Currently `median_bin_energy`; not calibrated SNR |
| `sc:pretrigger_seconds` | number | Retrospective source time; currently zero |
| `sc:posttrigger_seconds` | number | Configured minimum post-trigger hold, block-rounded |
| `sc:stop_reason` | string | Limit, user stop, gate close, retune, bandwidth change or receiver error |
| `sc:sample_count` | unsigned integer | Stored complex sample count |

`core:sample_rate` and `captures[].core:frequency` always describe the **stored**
IQ data, including the changed center frequency and rate of a channelized clip.
The source indices are provenance, not an instruction to insert missing samples
into that stored dataset. FIR startup samples remain present; callers can account
for the explicitly recorded group delay when aligning a clip to a source.

See [aufnahme.md](aufnahme.md) for detection limits and scaling.

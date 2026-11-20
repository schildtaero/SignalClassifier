# SignalClassifier application metadata extension

- Namespace: `signalclassifier`
- Version: `1.0.0`
- Optional: yes; all fields leave the core numeric dataset format unchanged.
- Scope: SigMF `global` object.

| Field | Type | Meaning |
| --- | --- | --- |
| `signalclassifier:session_id` | string | Capture-session provenance (compatibility alias of `sc:session_id`) |
| `signalclassifier:group_id` | string | Original dataset group, retained across derived recordings |
| `signalclassifier:parent_recording_id` | string | Immediate source recording of a replay/derivation |
| `signalclassifier:label` | string | Human-assigned classification label |
| `signalclassifier:label_verified` | boolean | Whether a user confirmed the label |
| `signalclassifier:label_notes` | string | Human label notes |
| `signalclassifier:label_provenance` | string | Human assignment or explicit human confirmation of a frequency prior |
| `signalclassifier:label_suggestion` | object | Confirmed suggestion snapshot, including label, reason, source and emitter IDs |

Group identity takes precedence over session identity when creating independent
training/test splits. Adjacent clips from the same original recording are not
independent examples merely because they were replayed in separate sessions.

An assigned label is independent of the `sc:synthetic_modulation` provenance and
of an unverified bandplan hint. Labels and frequency metadata must not silently
become inputs to a frequency-independent AM/FM classifier.

Frequency proposals are computed separately from confirmed annotations. Batch
confirmation changes neither IQ samples nor original session/group identities.
Ambiguous proposals are not confirmed by the suggested-label batch action;
users can instead explicitly assign a common label to their selection.

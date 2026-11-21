"""Bounded, frequency-independent evidence routing for one contiguous IQ window.

Feature rules propose hypotheses, never calibrated probabilities. Saved models
remain closed-set candidates unless actual decoder evidence is supplied. RF band
hints are reported separately and cannot vote in routing or fusion.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import math
from pathlib import Path
import time

import numpy as np

from . import ml

POLICY_ID = "conservative_v1"
DEFAULT_POLICY = {"id": POLICY_ID, "min_samples": 256, "max_samples": 65536,
                  "max_models": 8, "budget_ms": 500., "min_model_score": .65,
                  "min_margin": .15, "require_evaluated_models": True, "explain_models": False,
                  "expert_alignment": "end"}
MAX_REGISTERED_MODELS = 65
MODEL_LEVELS = ("waveform_family", "modulation", "protocol", "service", "emitter")
LEVELS = MODEL_LEVELS

_MODULATIONS = [
    ("AM", "Amplitudenmodulation"), ("FM", "Frequenzmodulation"),
    ("SSB", "Einseitenbandmodulation"), ("OOK", "Ein-/Austastung"),
    ("ASK", "Amplitudenumtastung"), ("FSK", "Frequenzumtastung"),
    ("PSK", "Phasenumtastung"), ("QAM", "Quadratur-Amplitudenmodulation"),
]
_WAVEFORMS = [("single_carrier", "Einträgerartig", None),
              ("multicarrier", "Mehrträgerartig", None), ("chirp", "Chirp-artig", None),
              ("pulse", "Pulsartig", None), ("OFDM", "Orthogonale Mehrträgerübertragung", "multicarrier"),
              ("CSS", "Chirp-Spreizverfahren", "chirp"), ("pulsed", "Gepulstes Signal", "pulse")]
_PROTOCOLS = ["POCSAG", "LoRa", "LoRaWAN", "ADS-B", "Mode S", "AIS", "ACARS",
              "VDL2", "AX.25", "APRS", "DMR", "dPMR", "TETRA", "DAB+",
              "DVB-T", "DVB-T2", "Wi-Fi", "Bluetooth", "Remote ID"]
_SERVICES = ["Flugfunk", "Rundfunk", "Funkruf", "Seefunk", "Amateurfunk",
             "Sprechfunk", "Telemetrie", "Drohnenidentifikation"]
_ALIASES = {"WFM": "FM", "NFM": "FM", "USB": "SSB", "LSB": "SSB",
            "2FSK": "FSK", "4FSK": "FSK", "GFSK": "FSK", "GMSK": "FSK",
            "BPSK": "PSK", "QPSK": "PSK", "8PSK": "PSK", "D8PSK": "PSK",
            "16QAM": "QAM", "64QAM": "QAM", "256QAM": "QAM"}
_LABEL_LEVEL = {label.casefold(): "modulation" for label, _ in _MODULATIONS}
_LABEL_LEVEL.update({label.casefold(): "waveform_family" for label, _, _ in _WAVEFORMS})
_LABEL_LEVEL.update({label.casefold(): "protocol" for label in _PROTOCOLS})
_LABEL_LEVEL.update({label.casefold(): "service" for label in _SERVICES})
_LABEL_LEVEL.update({label.casefold(): "modulation" for label in _ALIASES})
_CANONICAL_LABEL = {label.casefold(): label for label, _ in _MODULATIONS}
_CANONICAL_LABEL.update({label.casefold(): label for label, _, _ in _WAVEFORMS})
_CANONICAL_LABEL.update({label.casefold(): label for label in [*_PROTOCOLS, *_SERVICES]})
_ABSTENTIONS = {"unknown", "unbekannt", "noise", "rauschen", "no signal", "kein signal", "ood"}


def get_taxonomy() -> dict:
    """Return a catalogue of label levels, not a list of working decoders."""
    return {"schema_version": 1, "id": "radio-label-levels-v1",
            "waveform_family": [{"id": x, "name": y, "parent": parent} for x, y, parent in _WAVEFORMS],
            "modulation": [{"id": x, "name": y} for x, y in _MODULATIONS],
            "protocol": [{"id": x, "name": x, "decoder_available": False,
                          "decoder_scope": "No built-in decoder; checked external evidence may be supplied"} for x in _PROTOCOLS],
            "service": [{"id": x, "name": x} for x in _SERVICES],
            "emitter": [],
            "limitations": ["Katalogeinträge sind keine Erkennungs- oder Decoderzusage.",
                            "Modulation, Protokoll, Dienst und Senderidentität sind getrennte Aussagen.",
                            "OFDM kann QAM- oder PSK-Unterträger tragen; Wellenform und Modulation sind keine gegenseitigen Alternativen.",
                            "LoRa bezeichnet hier das Verfahren/Protokoll; die Modulationsfamilie ist CSS."]}


def normalize_policy(policy=None) -> dict:
    if policy is None:
        return dict(DEFAULT_POLICY)
    if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
        raise ValueError("Ungültige Entscheidungsregeln oder unbekannte Policy-Felder.")
    result = {**DEFAULT_POLICY, **policy}
    if result["id"] != POLICY_ID:
        raise ValueError(f"Unterstützte Entscheidungspolicy: {POLICY_ID}.")
    if result["expert_alignment"] not in ("start", "end"):
        raise ValueError("expert_alignment muss start oder end sein.")
    for key, low, high in (("min_samples", 64, 65536), ("max_samples", 256, 65536), ("max_models", 0, 8)):
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key}: ganze Zahl zwischen {low} und {high} erforderlich.")
    if result["min_samples"] > result["max_samples"]:
        raise ValueError("min_samples darf max_samples nicht überschreiten.")
    for key, low, high in (("budget_ms", 0, 60000), ("min_model_score", .5, 1), ("min_margin", 0, 1)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key}: endliche Zahl zwischen {low} und {high} erforderlich.")
        result[key] = float(value)
    for key in ("require_evaluated_models", "explain_models"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} muss true oder false sein.")
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _label_level(label, declared=None):
    return _LABEL_LEVEL.get(str(label).strip().casefold(), declared)


def _canonical_label(label):
    text = str(label).strip()
    return _ALIASES.get(text.upper(), _CANONICAL_LABEL.get(text.casefold(), text))


def _specific_waveform(labels):
    """Return a most-specific label only when all labels form one parent chain."""
    parents = {label: parent for label, _, parent in _WAVEFORMS}
    for label in labels:
        chain = {label}
        parent = parents.get(label)
        while parent is not None and parent not in chain:
            chain.add(parent)
            parent = parents.get(parent)
        if set(labels) <= chain:
            return label
    return None


def _domain(manifest):
    rates = manifest.get("sample_rates", [manifest.get("sample_rate")])
    if not isinstance(rates, list):
        rates = [manifest.get("sample_rate")]
    return {"classes": list(manifest["classes"]), "input": manifest.get("input"),
            "segment_length": manifest["segment_length"],
            "sample_rates": sorted({float(x) for x in rates if _number(x) is not None and float(x) > 0}),
            "evaluation_available": manifest.get("evaluation_available"),
            "validation_status": manifest.get("validation_status", "unknown"),
            "ood_validated": False}


@functools.lru_cache(maxsize=32)
def _hash_files(directory, signature):
    hashes = {}
    for name, _, _, _ in signature:
        digest = hashlib.sha256()
        with (Path(directory) / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1_048_576), b""):
                digest.update(block)
        hashes[name] = digest.hexdigest()
    digest = hashlib.sha256("\n".join(f"{name}:{hashes[name]}" for name in sorted(hashes)).encode()).hexdigest()
    return {"sha256": digest, "files": hashes,
            "scope": "model.json, weights and normalization; cached by file size/mtime/inode"}


def _model_hash(path, manifest):
    directory = Path(path)
    directory = directory if directory.is_dir() else directory.parent
    names = ["model.json", manifest["weights_file"]]
    if manifest["family"] != "feature_lda":
        names.append("normalization.npz")
    signature = []
    for name in names:
        stat = (directory / name).stat()
        limit = 1_048_576 if name == "model.json" else 65536 if name == "normalization.npz" else 134_217_728
        if stat.st_size > limit:
            raise ValueError("Modelldatei überschreitet das zulässige Größenbudget.")
        signature.append((name, stat.st_mtime_ns, stat.st_size, stat.st_ino))
    return copy.deepcopy(_hash_files(str(directory.resolve()), tuple(sorted(signature))))


def _extra_features(x, features):
    a = np.abs(x.astype(np.complex128))
    rms = max(float(np.sqrt(np.mean(a * a))), 1e-20)
    p95 = float(np.percentile(a, 95))
    result = {**features, "peak_to_rms_db": float(20 * np.log10(max(float(a.max()), 1e-20) / rms)),
              "active_fraction": float(np.mean(a > max(.25 * p95, .15 * rms)))}
    # A coarse ridge is only a chirp hypothesis; it never decodes CSS/LoRa.
    size = 128 if len(x) >= 2048 else 64
    starts = np.linspace(0, len(x) - size, min(16, len(x) // size), dtype=int)
    p = np.abs(np.fft.fft(np.stack([x[s:s + size] for s in starts]) * np.hanning(size), axis=1)) ** 2
    ridge = np.unwrap(2 * np.pi * np.argmax(p, axis=1) / size) / (2 * np.pi)
    t = np.arange(len(ridge), dtype=float)
    fit = np.polyval(np.polyfit(t, ridge, 1), t) if len(t) > 1 else ridge
    spread = float(np.sum((ridge - ridge.mean()) ** 2))
    result.update(ridge_linear_r2=float(max(0, 1 - np.sum((ridge - fit) ** 2) / spread)) if spread > 1e-12 else 0.,
                  ridge_span_fraction=float(np.ptp(ridge)),
                  ridge_peak_fraction=float(np.mean(p.max(axis=1) / np.maximum(p.sum(axis=1), 1e-20))))
    return result


def _hypotheses(f):
    rows = []
    def add(key, families, waveform, support, reason, measurements):
        rows.append({"id": key, "families": families, "waveform_family": waveform,
                     "score": float(min(.85, max(0, support))),
                     "score_kind": "heuristic_support_not_probability", "reason": reason,
                     "measurements": {name: f[name] for name in measurements}})
    if f["active_fraction"] < .55:
        add("pulse_like", ["OOK", "pulsed"], "pulse", .75,
            "Ein großer Teil des Fensters besitzt deutlich geringere Hüllkurvenenergie; Pulse/Pausen sind möglich.", ["active_fraction", "amplitude_cv"])
    if f["ridge_linear_r2"] > .8 and f["ridge_span_fraction"] > .15 and f["ridge_peak_fraction"] > .1:
        add("chirp_like", ["CSS"], "chirp", .7,
            "Eine konzentrierte Spektralspitze verschiebt sich annähernd linear; kein LoRa- oder Spreizfaktornachweis.", ["ridge_linear_r2", "ridge_span_fraction", "ridge_peak_fraction"])
    if f["spectral_flatness"] > .35 and f["spectral_entropy"] > .85:
        add("noise_like_or_spread", ["OFDM", "CSS", "QAM"], "multicarrier", .5,
            "Flaches, verteiltes Spektrum: Rauschen, Mehrträger- oder Spreizsignal bleiben mehrdeutig; keine globale Ablehnung.", ["spectral_flatness", "spectral_entropy"])
    if f["amplitude_cv"] > .15:
        add("amplitude_variation", ["AM", "SSB", "OOK", "ASK", "QAM"], "single_carrier", .55,
            "Die Hüllkurve variiert; mehrere analoge und digitale Modulationsarten können dies verursachen.", ["amplitude_cv", "envelope_correlation", "phase_std"])
    if f["amplitude_cv"] < .25 and f["phase_std"] > .03:
        add("angle_variation", ["FM", "FSK", "PSK", "CSS"], "single_carrier", .55,
            "Relativ gleichmäßige Hüllkurve bei Phasenänderung; dies trennt FM nicht sicher von digitalen Winkelmodulationen.", ["amplitude_cv", "phase_std", "phase_dispersion"])
    if not rows:
        add("ambiguous", [name for name, _ in _MODULATIONS] + ["OFDM", "CSS", "pulsed"], None, .2,
            "Diese wenigen Merkmale liefern keine trennende Familienhypothese; auch ein unmodulierter Träger ist möglich.", ["amplitude_cv", "phase_std", "spectral_entropy"])
    return rows


def _empty_conclusion(reason):
    return {"status": "unknown", "label": None, "candidates": [], "reason": reason,
            "evidence_ids": [], "method": "abstention"}


def analyze_signal(iq, sample_rate, center_frequency_hz=None, bandwidth_hz=None, *,
                   models=None, policy=None, context=None, prior_hints=None) -> dict:
    """Explain a bounded IQ analysis; model scores never become RF identities.

    ``budget_ms=0`` disables the cooperative time limit; eight experts and 65536
    analyzed samples remain hard limits. A running native model/hash operation
    cannot be preempted. Up to 65 registered profiles may compete for eight
    inference calls; skipped profiles do not load weights or manifests after
    the call/time budget. ``models[].level`` is one of MODEL_LEVELS; known class
    labels correct a mistaken level without silently claiming emitter identity.

    Optional decoder evidence in context must carry decoder, validated=True,
    checks_passed=True, validation_method and the matching sample_sha256. This
    records supplied checked frames, not authenticated transmitter identity.
    """
    begin = time.perf_counter()
    p = normalize_policy(policy)
    rate = _number(sample_rate)
    if rate is None or not 1 <= rate <= 100_000_000:
        raise ValueError("Abtastrate muss zwischen 1 und 100.000.000 S/s liegen.")
    for name, value in (("center_frequency_hz", center_frequency_hz), ("bandwidth_hz", bandwidth_hz)):
        if value is not None and (_number(value) is None or float(value) < 0):
            raise ValueError(f"{name} muss eine endliche nichtnegative Zahl sein.")
    models = [] if models is None else models
    if not isinstance(models, list) or len(models) > MAX_REGISTERED_MODELS or any(not isinstance(m, dict) for m in models):
        raise ValueError("Maximal 65 registrierte Modellprofile pro Analyse erlaubt; höchstens acht Modellaufrufe.")
    if len({str(m.get("id", i)) for i, m in enumerate(models)}) != len(models):
        raise ValueError("Modell-IDs müssen innerhalb einer Analyse eindeutig sein.")
    if any(not 1 <= len(str(m.get("id", i))) <= 128 for i, m in enumerate(models)):
        raise ValueError("Modell-IDs müssen 1 bis 128 Zeichen enthalten.")
    context = context if isinstance(context, dict) else {}
    if prior_hints is not None and (not isinstance(prior_hints, list) or len(prior_hints) > 32):
        raise ValueError("Maximal 32 getrennte Bandhinweise erlaubt.")
    hints = []
    for hint in prior_hints or []:
        if isinstance(hint, dict):
            hints.append({k: str(hint[k])[:500] for k in ("id", "name", "label", "reason", "source", "service", "standard", "modulation") if k in hint})
        elif isinstance(hint, str):
            hints.append({"reason": hint[:500]})
    result = {"schema_version": 1, "policy": p, "status": "unknown", "label": "unbekannt",
              "conclusions": {level: _empty_conclusion("Keine ausreichende Evidenz auf dieser Aussageebene.") for level in LEVELS},
              "stages": [], "edges": [], "features": {}, "hypotheses": [], "routes": [],
              "predictions": [], "evidence": [], "prior_hints": {"used_for_decision": False, "hints": hints},
              "input": {"sample_rate": rate, "center_frequency_hz": _number(center_frequency_hz), "bandwidth_hz": _number(bandwidth_hz),
                        **{k: str(context[k])[:200] for k in ("channel_id", "source_epoch", "origin_group_id", "observation_id", "observed_at") if k in context}},
              "budget": {"limit_ms": p["budget_ms"], "max_models": p["max_models"], "models_run": 0,
                         "exhausted": False, "enforcement": "cooperative between experts; native calls may overrun"},
              "warnings": [], "limitations": ["Merkmalshypothesen und Modell-Scores sind keine kalibrierten Wahrscheinlichkeiten.",
                  "Geschlossene Klassenmodelle belegen keine Erkennung unbekannter oder außerhalb der Trainingsdomäne liegender Signale.",
                  "Frequenz- und Bandhinweise werden weder für Routing noch Fusion verwendet.",
                  "Modellübereinstimmung beweist weder Protokolldecodierung noch Dienst oder Senderidentität."]}
    def expired():
        return bool(p["budget_ms"] and (time.perf_counter() - begin) * 1000 >= p["budget_ms"])
    def stage(identifier, parents, status, decision, reason, started=None):
        result["stages"].append({"id": identifier, "parents": parents, "status": status,
                                 "decision": decision, "reason": reason,
                                 "elapsed_ms": max(0., (time.perf_counter() - (started if started is not None else time.perf_counter())) * 1000)})
        result["edges"].extend({"from": parent, "to": identifier} for parent in parents)
    def finish():
        if models and not result["routes"]:
            reason = result["stages"][0]["reason"] if result["stages"] else "Signalprüfung nicht bestanden."
            result["routes"] = [{"model_id": str(model.get("id", i)),
                                 "name": str(model.get("name", model.get("id", i)))[:200],
                                 "level": model.get("level", "modulation"), "status": "skipped",
                                 "why": [reason], "domain": None, "model_hash": None, "priority": 0}
                                for i, model in enumerate(models)]
        ids = {a["id"] for a in result["stages"]}
        for identifier, parents in (("features", ["quality"]), ("hypotheses", ["features"]), ("routing", ["hypotheses"]), ("models", ["routing"]), ("fusion", ["models", "hypotheses"])):
            if identifier not in ids:
                stage(identifier, parents, "skipped", "übersprungen", "Vorgelagerte Signalprüfung erlaubt keine weitere Aussage.")
        result["elapsed_ms"] = max(0., (time.perf_counter() - begin) * 1000)
        result["budget"].update(elapsed_ms=result["elapsed_ms"], overrun_ms=max(0., result["elapsed_ms"] - p["budget_ms"]) if p["budget_ms"] else 0.)
        return result
    try:
        raw = np.asarray(iq)
        count = len(raw) if raw.ndim else 0
        if raw.ndim != 1:
            raise ValueError("Eindimensionale komplexe IQ-Daten erforderlich.")
        window_start = max(0, count - p["max_samples"]) if p["expert_alignment"] == "end" else 0
        x = np.asarray(raw[window_start:window_start + p["max_samples"]], dtype=np.complex64)
    except (ValueError, TypeError, OverflowError) as exc:
        result.update(status="invalid", label="ungültige Eingabe")
        stage("quality", [], "rejected", "ungültige Eingabe", str(exc), begin)
        return finish()
    result["input"].update(sample_count=count, analyzed_samples=len(x), truncated=count > len(x),
                           window_start=window_start, window_stop=window_start + len(x),
                           feature_window_scope="All bounded analysis samples; individual experts may use a shorter aligned window")
    if count > len(x):
        position = "letzte" if p["expert_alignment"] == "end" else "erste"
        result["warnings"].append(f"Nur das {position} Fenster von {len(x)} der {count} Samples analysiert.")
    if not np.isfinite(x).all():
        result.update(status="invalid", label="nichtendliche Samples")
        stage("quality", [], "rejected", result["label"], "NaN oder unendliche IQ-Werte verhindern jede Modellinferenz.", begin)
        return finish()
    if len(x) < p["min_samples"]:
        result.update(status="insufficient_samples", label="zu wenige Samples")
        stage("quality", [], "rejected", result["label"], f"{len(x)} von mindestens {p['min_samples']} Samples verfügbar.", begin)
        return finish()
    result["input"]["sample_sha256"] = hashlib.sha256(x.tobytes()).hexdigest()
    quality = context.get("quality") if isinstance(context.get("quality"), dict) else {}
    rms = float(np.sqrt(np.mean(np.abs(x.astype(np.complex128)) ** 2)))
    if rms < 1e-7:
        result.update(status="no_signal", label="kein Signal")
        stage("quality", [], "rejected", result["label"], "Digitaler RMS-Pegel unter −140 dBFS; kein kalibrierter RF-Grenzpegel.", begin)
        return finish()
    if quality.get("contiguous") is False:
        stage("quality", [], "rejected", "unterbrochenes Fenster", "Bekannter Sprung im analysierten Zeitfenster; durchgängige Samples erforderlich.", begin)
        return finish()
    clipping = _number(quality.get("clipping_fraction"))
    if clipping is not None and clipping >= .2:
        stage("quality", [], "rejected", "starkes Clipping gemeldet", "Mindestens 20 % der Samples laut Quellenprüfung gesättigt; keine verlässliche Modulationsaussage.", begin)
        return finish()
    if quality.get("reported_gaps") or quality.get("overflows"):
        result["warnings"].append("Quelle meldete Lücken/Überläufe; ohne Fensterbezug wird kein interner Sprung unterstellt.")
    if quality.get("filter_settled") is False:
        result["warnings"].append("Kanalfilter ist noch nicht eingeschwungen; ein Filterübergang kann die Merkmale beeinflussen.")
    parent_rate = _number(context.get("parent_sample_rate"))
    parent_center = _number(context.get("parent_center_frequency_hz"))
    detection = context.get("detection") if isinstance(context.get("detection"), dict) else {}
    low, high = _number(detection.get("frequency_min_hz")), _number(detection.get("frequency_max_hz"))
    if parent_rate is not None and parent_rate > 0 and parent_center is not None:
        result["input"].update(parent_sample_rate=parent_rate, parent_center_frequency_hz=parent_center)
        if low is not None and high is not None and low <= high:
            # One approximate detection bin: a warning about the observed parent
            # window, never a label vote or proof of a second/overlapping signal.
            margin = parent_rate / 2048
            if low <= parent_center - parent_rate / 2 + margin or high >= parent_center + parent_rate / 2 - margin:
                result["warnings"].append("Detektion berührt den Rand des Elternspektrums; das Signal könnte angeschnitten sein. Dies beweist keine Überlagerung.")
    if detection.get("possible_overlap") is True:
        result["warnings"].append("Erfassung meldet eine mögliche Überlagerung; Energie-/Formmerkmale allein beweisen weder mehrere Sender noch eine eindeutige Einzelklasse.")
    stage("quality", [], "passed", "endliches Analysefenster", "Kein bekannter fensterinterner Sprung; dies zertifiziert keine physische Lückenfreiheit. Kein globales Rauschgate: OFDM-/Spreizsignale bleiben im Prüfpfad.", begin)
    started = time.perf_counter()
    f = _extra_features(x, ml.extract_features(x, rate))
    result["features"] = f
    stage("features", ["quality"], "completed", "Hüllkurve, Phase und Spektrum", "Begrenzte CPU-Merkmale ohne RF-Mittenfrequenz; keine trainierte Universalentscheidung.", started)
    result["hypotheses"] = _hypotheses(f)
    wave = [h for h in result["hypotheses"] if h["waveform_family"]]
    if wave:
        result["conclusions"]["waveform_family"] = {"status": "hypothesis", "label": None,
            "candidates": [{"label": h["waveform_family"], "score": h["score"], "score_kind": h["score_kind"], "model_ids": [], "evidence_ids": [h["id"]]} for h in wave],
            "reason": "Nicht ausschließende Merkmalsvorschläge; Mehrträger, Spreizung und Rauschen können ähnlich aussehen.", "evidence_ids": [h["id"] for h in wave], "method": "feature_hypotheses"}
    for h in result["hypotheses"]:
        result["evidence"].append({"id": h["id"], "kind": "feature_hypothesis", "validated": False, **h})
    stage("hypotheses", ["features"], "completed", "breite, nicht ausschließende Hypothesen", "Die Merkmale priorisieren Experten, bestätigen aber keine konkrete Modulation oder Funkanwendung.")
    started = time.perf_counter()
    possible = {family for h in result["hypotheses"] for family in h["families"]}
    possible.update(h["waveform_family"] for h in result["hypotheses"] if h["waveform_family"])
    runnable = []
    for i, model in enumerate(models):
        scope = model.get("scope") if isinstance(model.get("scope"), dict) else {}
        declared = model.get("level", scope.get("level", "modulation"))
        route = {"model_id": str(model.get("id", i)), "name": str(model.get("name", model.get("id", i)))[:200],
                 "level": declared, "declared_level": declared, "status": "skipped", "why": [],
                 "domain": None, "model_hash": None, "priority": 0, "matched_families": []}
        result["routes"].append(route)
        if model.get("unavailable_reason"):
            route.update(status="error", why=[str(model["unavailable_reason"])[:400]])
            continue
        path = model.get("model_path")
        if not isinstance(path, (str, Path)) or not str(path).strip():
            route.update(status="error", why=["Modellpfad fehlt; kein Rückfall auf das aktuelle Arbeitsverzeichnis."])
            continue
        if declared not in MODEL_LEVELS:
            route["why"].append("Nicht unterstützte Aussageebene; erlaubt sind waveform_family/modulation/protocol/service/emitter.")
            continue
        try:
            families = model.get("families", scope.get("families", []))
            if not isinstance(families, list) or len(families) > 32:
                raise ValueError("Modellfamilien müssen eine Liste mit höchstens 32 Einträgen sein.")
            families = {_canonical_label(label) for label in families}
            route["matched_families"] = sorted(possible & families)
            route["priority"] = len(route["matched_families"])
            route["why"].append(f"Profil passt zu den Merkmalsvorschlägen {', '.join(route['matched_families'])}; bevorzugte Prüfposition." if route["priority"] else
                                 "Kein spezifischer Profil-/Merkmalstreffer; explorative Prüfung innerhalb des Budgets.")
            runnable.append((i, model, route))
        except Exception as exc:
            route.update(status="error", why=route["why"] + [f"{type(exc).__name__}: {str(exc)[:400]}"])
    stage("routing", ["hypotheses"], "completed", f"{len(runnable)} registrierte Prüfprofile", "Profilfamilien priorisieren die Route ohne Dateizugriff; Klassen/Segmentlängen werden erst beim budgetierten Experten geprüft. Keine Frequenz-/Bandhint-Stimme.", started)
    started = time.perf_counter()
    for _, model, route in sorted(runnable, key=lambda row: (-row[2]["priority"], row[0])):
        if result["budget"]["models_run"] >= p["max_models"]:
            route["skip_reason"] = "model_budget"
            route["why"].append("Maximale Zahl von Modellaufrufen laut Policy erreicht.")
            continue
        if expired():
            result["budget"]["exhausted"] = True
            route["skip_reason"] = "time_budget"
            route["why"].append("Zeitbudget vor Modellaufruf erreicht.")
            continue
        expert_begin = time.perf_counter()
        try:
            manifest = ml.load_model_metadata(model.get("model_path", ""))
            route["domain"] = _domain(manifest)
            known = {_label_level(label) for label in manifest["classes"]} - {None}
            # Correct a uniform known taxonomy, but retain the original level as
            # the fallback for unknown/custom labels in a mixed classifier.
            if all(_label_level(label) is not None for label in manifest["classes"]) and len(known) == 1:
                route["level"] = next(iter(known))
            if route["level"] != route["declared_level"]:
                route["why"].append(f"Labeltaxonomie korrigiert die deklarierte Ebene {route['declared_level']} zu {route['level']}.")
            if len(known) > 1:
                route["why"].append("Modellklassen mischen Aussageebenen; einzelne Modelllabels werden getrennt zugeordnet.")
            if len(x) < manifest["segment_length"]:
                route["skip_reason"] = "insufficient_samples"
                route["why"].append(f"Unzureichendes Fenster: {len(x)} von {manifest['segment_length']} benötigten Samples.")
                continue
            if p["require_evaluated_models"] and manifest.get("evaluation_available") is not True:
                route["skip_reason"] = "evaluation_unknown_or_unvalidated"
                route["why"].append("Unabhängige Modellevaluation fehlt oder ist unbekannt; Policy überspringt diesen Experten.")
                continue
            route["model_hash"] = _model_hash(model["model_path"], manifest)
            if expired():
                result["budget"]["exhausted"] = True
                route["skip_reason"] = "time_budget"
                route["why"].append("Zeitbudget nach Artefaktprüfung erreicht; keine Inferenz gestartet.")
                continue
            result["budget"]["models_run"] += 1
            route["status"] = "run"
            # The existing AM/FM rejection is local to that expert. It never
            # prevents a generic OFDM/CSS or protocol expert from running.
            local_start = len(x) - manifest["segment_length"] if p["expert_alignment"] == "end" else 0
            segment = x[local_start:local_start + manifest["segment_length"]]
            output = ml.classify(segment, rate, use_priors=False,
                                 model_path=model["model_path"], explain=p["explain_models"], attribution=p["explain_models"])
            scores = output.get("probabilities", {})
            if not isinstance(scores, dict) or (scores and set(scores) != set(manifest["classes"])):
                raise ValueError("Modellausgabe passt nicht zur gespeicherten Klassenliste.")
            if any(_number(value) is None or not 0 <= float(value) <= 1 for value in scores.values()) or (scores and abs(sum(scores.values()) - 1) > .001):
                raise ValueError("Ungültige numerische Modell-Scores.")
            ordered = sorted(scores.items(), key=lambda item: -item[1])
            score = float(ordered[0][1]) if ordered else None
            margin = float(ordered[0][1] - ordered[1][1]) if len(ordered) > 1 else None
            label = output.get("label")
            status = "candidate" if label in scores and score >= max(p["min_model_score"], manifest.get("confidence_threshold", .6)) and margin >= p["min_margin"] else "rejected"
            if label not in scores and str(label).casefold() not in _ABSTENTIONS:
                raise ValueError("Modell lieferte ein unbekanntes oder fehlendes Ausgabelabel.")
            actual_level = _label_level(label, route["declared_level"])
            prediction = {"model_id": route["model_id"], "model_label": label,
                          "declared_level": route["declared_level"], "label_level": actual_level,
                          "status": status, "score": score, "margin": margin,
                          "probabilities": {key: float(value) for key, value in scores.items()},
                          "calibrated": False, "domain": route["domain"], "model_hash": route["model_hash"],
                          "method": "model_candidate", "ood_validated": False,
                          "elapsed_ms": (time.perf_counter() - expert_begin) * 1000,
                          "reason": "Geschlossene Modellklassen; Score und Übereinstimmung sind kein Open-set- oder Identitätsnachweis.",
                          "steps": output.get("steps", []), "explanation": output.get("explanation") if p["explain_models"] else None,
                          "window": {"sample_start": local_start, "sample_stop": local_start + len(segment),
                                     "sample_count": len(segment), "alignment": p["expert_alignment"],
                                     "input_sample_start": window_start + local_start,
                                     "input_sample_stop": window_start + local_start + len(segment),
                                     "sample_sha256": hashlib.sha256(segment.tobytes()).hexdigest(),
                                     "duration_seconds": len(segment) / rate}}
            if rate not in route["domain"]["sample_rates"]:
                prediction["reason"] += " Diese Abtastrate ist nicht als Trainingsrate dokumentiert."
            if manifest.get("evaluation_available") is not True:
                prediction["reason"] += " Dieses Modell besitzt keinen dokumentierten unabhängigen Evaluationsnachweis."
            if status == "rejected":
                route["why"].append("Experte lehnt lokal ab oder unterschreitet Score-/Abstandsgrenze; andere Experten bleiben aktiv.")
            result["predictions"].append(prediction)
            result["evidence"].append({"id": f"model:{route['model_id']}", "kind": "model_prediction", "validated": False,
                                       "level": actual_level, "label": label, "model_id": route["model_id"]})
        except Exception as exc:
            route.update(status="error", why=route["why"] + [f"{type(exc).__name__}: {str(exc)[:400]}"])
        finally:
            route["elapsed_ms"] = (time.perf_counter() - expert_begin) * 1000
    result["budget"]["exhausted"] = result["budget"]["exhausted"] or expired()
    stage("models", ["routing"], "completed" if result["predictions"] else "skipped", f"{len(result['predictions'])} Expertenausgaben", "Fehler/Ablehnung eines Experten isoliert; keine Ersatzvorhersage aus Frequenz oder einer Heuristik.", started)
    started = time.perf_counter()
    for level in MODEL_LEVELS:
        candidates = [a for a in result["predictions"] if a["status"] == "candidate" and a["label_level"] == level and str(a["model_label"]).casefold() not in _ABSTENTIONS]
        grouped = {}
        for a in candidates:
            label = _canonical_label(a["model_label"])
            entry = grouped.setdefault(label, {"label": label, "score": None, "score_kind": "uncalibrated_model_scores_not_averaged", "model_ids": [], "evidence_ids": [], "model_scores": {}})
            entry["model_ids"].append(a["model_id"]); entry["evidence_ids"].append(f"model:{a['model_id']}")
            entry["model_scores"][a["model_id"]] = a["score"]
        if grouped:
            specific = _specific_waveform(grouped) if level == "waveform_family" else None
            compatible_chain = len(grouped) > 1 and specific is not None
            conflict = len(grouped) > 1 and not compatible_chain
            previous = result["conclusions"][level]
            result["conclusions"][level] = {"status": "conflict" if conflict else "candidate", "label": None if conflict else specific or next(iter(grouped)),
                "candidates": list(grouped.values()), "evidence_ids": [e for a in grouped.values() for e in a["evidence_ids"]],
                "model_ids": [model_id for a in grouped.values() for model_id in a["model_ids"]], "method": "model_candidate",
                "reason": "Widersprechende Modellkandidaten: bewusste Ablehnung statt Mittelung nicht vergleichbarer Scores." if conflict else "Modellkandidat innerhalb gespeicherter Klassen; unbekannte Alternativen bleiben offen. Kein Decoder-/Dienst-/Identitätsbeweis."}
            if compatible_chain:
                result["conclusions"][level]["reason"] = "Generische und spezifische Wellenformlabels sind entlang der Taxonomie vereinbar; der spezifischste Modellkandidat wird genannt. Alle Vorschläge bleiben erhalten; kein zusätzlicher Decoder- oder Open-set-Nachweis."
            if level == "waveform_family":
                result["conclusions"][level].update(feature_hypotheses=previous["candidates"],
                                                     feature_evidence_ids=previous["evidence_ids"])
    decoder_rows = context.get("decoder_evidence", [])
    if isinstance(decoder_rows, list):
        if len(decoder_rows) > 16:
            result["warnings"].append("Decoder-Evidenz auf die ersten 16 Einträge begrenzt.")
        for i, evidence in enumerate(decoder_rows[:16]):
            if not isinstance(evidence, dict):
                continue
            level = evidence.get("level")
            if level not in MODEL_LEVELS or evidence.get("validated") is not True or evidence.get("checks_passed") is not True or not evidence.get("decoder") or not evidence.get("validation_method") or evidence.get("sample_sha256") != result["input"]["sample_sha256"] or not isinstance(evidence.get("label"), str):
                result["warnings"].append("Decoderhinweis ohne bestandene, fensterbezogene Prüfprovenienz nicht als Evidenz übernommen.")
                continue
            label = _canonical_label(evidence["label"][:120])
            if not label or label.casefold() in _ABSTENTIONS:
                result["warnings"].append("Leeres Decoderlabel oder Ablehnungslabel ist kein positiver Decodernachweis.")
                continue
            actual = _label_level(label, level)
            identifier = f"decoder:{i}"
            result["evidence"].append({"id": identifier, "kind": "supplied_decoder_evidence", "validated": True,
                                       "level": actual, "label": label, "decoder": str(evidence["decoder"])[:120],
                                       "validation_method": str(evidence["validation_method"])[:200], "sample_sha256": evidence["sample_sha256"]})
            previous = result["conclusions"][actual]
            if previous["method"] == "supplied_decoder_evidence":
                existing = {a["label"] for a in result["evidence"] if a["id"] in previous["evidence_ids"]}
                if existing != {label}:
                    previous.update(status="conflict", label=None, reason="Mehrere unterschiedliche, geprüfte Decoderlabels im selben Fenster; keine eindeutige Einzelzuordnung.")
                previous["evidence_ids"].append(identifier)
            else:
                result["conclusions"][actual] = {"status": "observed", "label": label, "candidates": [], "evidence_ids": [identifier], "method": "supplied_decoder_evidence",
                    "reason": "Übergebene Decoderprüfung gehört zu diesem IQ-Fenster; Inhalte/Identität sind dadurch nicht kryptographisch authentifiziert."}
                if actual == "waveform_family":
                    result["conclusions"][actual].update(
                        feature_hypotheses=previous.get("feature_hypotheses", previous["candidates"] if previous["method"] == "feature_hypotheses" else []),
                        feature_evidence_ids=previous.get("feature_evidence_ids", previous["evidence_ids"] if previous["method"] == "feature_hypotheses" else []))
    if any(result["conclusions"][level]["status"] == "conflict" for level in MODEL_LEVELS):
        result["status"] = "conflict"
    elif any(result["conclusions"][level]["status"] in ("candidate", "observed") for level in MODEL_LEVELS):
        result["status"] = "candidate"
    elif result["budget"]["exhausted"]:
        result["status"] = "budget_exhausted"
    for level in ("modulation", "waveform_family", "protocol", "service", "emitter"):
        conclusion = result["conclusions"][level]
        if conclusion["label"] is not None:
            result["label"] = conclusion["label"]
            break
    stage("fusion", ["models", "hypotheses"], result["status"], result["label"], "Aussageebenen getrennt; nur konsistente Modellkandidaten, kein Frequenzvotum oder unbelegter universeller Decoder.", started)
    return finish()

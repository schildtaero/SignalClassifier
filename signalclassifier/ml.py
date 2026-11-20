"""Small, reproducible modulation experiments and portable, inspectable models.

The included synthetic experiment is a software check, not a measured RF result.
Frequency metadata never enters the AM/FM model. Neural nets are optional.
"""
from __future__ import annotations

import hashlib
import functools
import importlib.util
import json
import math
import os
import platform
import time
import zipfile
from pathlib import Path

import numpy as np
from scipy import signal, __version__ as scipy_version

from . import __version__
from .topologies import list_topologies, save_topology, validate_topology

CLASSES = ["AM", "FM"]
FEATURE_NAMES = ["amplitude_cv", "amplitude_kurtosis", "phase_std", "phase_dispersion",
                 "spectral_flatness", "spectral_entropy", "occupied_bandwidth_fraction",
                 "spectral_kurtosis", "fourth_cumulant", "envelope_correlation"]
_SCHEMA = 1


def _json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _iq(value):
    x = np.asarray(value, dtype=np.complex64)
    if x.ndim != 1 or len(x) < 64 or len(x) > 4_194_304 or not np.isfinite(x).all():
        raise ValueError("IQ-Eingabe benötigt 64 bis 4.194.304 endliche komplexe Samples.")
    return x


def extract_features(iq, sample_rate=256_000) -> dict:
    x = _iq(iq)
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("Abtastrate muss positiv und endlich sein.")
    n = min(len(x), 65_536)
    x = x[:n].astype(np.complex128)
    rms = float(np.sqrt(np.mean(np.abs(x) ** 2)))
    if rms < 1e-15:
        return dict(zip(FEATURE_NAMES, [0., 0., 0., 0., 1., 1., 1., 0., 0., 0.])) | {"rms_dbfs": -300., "sample_rate": float(sample_rate)}
    x /= rms
    a = np.abs(x)
    am = a - np.mean(a)
    av = float(np.var(a))
    d = x[1:] * np.conj(x[:-1])
    phase = np.angle(d)
    circular_center = np.angle(np.mean(np.exp(1j * phase)))
    centered_phase = np.angle(np.exp(1j * (phase - circular_center)))
    p = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n)))) ** 2 + 1e-20
    q = p / p.sum()
    # Occupancy: shortest contiguous span containing the middle 99% spectral mass.
    cdf = np.cumsum(q)
    width = (np.searchsorted(cdf, .995) - np.searchsorted(cdf, .005) + 1) / n
    feat = {
        "amplitude_cv": float(np.std(a) / (np.mean(a) + 1e-12)),
        "amplitude_kurtosis": float(np.mean(am ** 4) / (av ** 2 + 1e-12)),
        "phase_std": float(np.std(centered_phase)),
        "phase_dispersion": float(1 - abs(np.mean(np.exp(1j * phase)))),
        "spectral_flatness": float(np.exp(np.mean(np.log(p))) / np.mean(p)),
        "spectral_entropy": float(-np.sum(q * np.log(q)) / np.log(n)),
        "occupied_bandwidth_fraction": float(width),
        "spectral_kurtosis": float(np.mean((p / np.mean(p) - 1) ** 4) / (np.var(p / np.mean(p)) ** 2 + 1e-12)),
        "fourth_cumulant": float(abs(np.mean(abs(x) ** 4) - abs(np.mean(x ** 2)) ** 2 - 2)),
        "envelope_correlation": float(np.mean(am[1:] * am[:-1]) / (av + 1e-12)),
        "rms_dbfs": float(20 * np.log10(max(rms, 1e-15))),
        "sample_rate": float(sample_rate),
    }
    return feat


def _feature_vector(iq, sample_rate):
    return _vector_from_features(extract_features(iq, sample_rate))


def _vector_from_features(f):
    v = np.array([f[k] for k in FEATURE_NAMES], dtype=np.float32)
    v[1] = np.log1p(max(0, v[1]))
    v[2] = np.log10(max(1e-5, v[2]))
    v[7] = np.log1p(max(0, v[7]))
    return v


def _standard_iq(iq, size=1024):
    x = _iq(iq)
    # Remove bulk frequency offset. No RF center frequency or band labels enter here.
    d = np.mean(x[1:] * np.conj(x[:-1]))
    x = x * np.exp(-1j * np.angle(d) * np.arange(len(x)))
    rms = np.sqrt(np.mean(abs(x) ** 2))
    x /= max(float(rms), 1e-12)
    if len(x) != size:
        x = signal.resample(x, size)
    return np.stack((x.real, x.imag)).astype(np.float32)


def _spectrogram(iq):
    x = _standard_iq(iq, 1024)
    x = x[0] + 1j * x[1]
    frames = np.lib.stride_tricks.sliding_window_view(x, 64)[::32][:31]
    p = np.abs(np.fft.fftshift(np.fft.fft(frames * np.hanning(64), axis=-1), axes=-1)) ** 2
    p = np.log1p(p.reshape(len(frames), 32, 2).mean(axis=-1))
    p = np.pad(p, ((0, 32 - len(p)), (0, 0)), mode="edge")
    return p[None].astype(np.float32)


def _spectral_sequence(iq):
    # RF adaptation of Schildt thesis Ch6.1.1: ten spectral features per Hamming frame.
    a = _standard_iq(iq, 2048)
    x = (a[0] + 1j * a[1]).reshape(16, 128)
    p = np.abs(np.fft.fftshift(np.fft.fft(x * np.hamming(128), axis=-1), axes=-1)) ** 2 + 1e-12
    f = np.abs(np.fft.fftshift(np.fft.fftfreq(128)))
    p = p / p.sum(axis=-1, keepdims=True)
    centroid = np.sum(p * f, axis=-1)
    delta = f[None] - centroid[:, None]
    spread = np.sqrt(np.sum(p * delta ** 2, axis=-1) + 1e-12)
    crest = np.max(p, axis=-1) * 128
    # Decrease/slope are defined on the ordered nonnegative frequency distribution.
    folded = p[:, 64:] + p[:, :64][:, ::-1]
    bins = np.arange(64)
    decrease = np.sum((folded[:, 1:] - folded[:, :1]) / bins[None, 1:], axis=-1) / (folded[:, 1:].sum(axis=-1) + 1e-12)
    entropy = -np.sum(p * np.log(p), axis=-1) / np.log(128)
    flatness = np.exp(np.mean(np.log(p), axis=-1)) * 128
    kurtosis = np.sum(p * delta ** 4, axis=-1) / spread ** 4
    rolloff = np.array([bins[min(63, np.searchsorted(np.cumsum(row), .85))] / 128 for row in folded])
    skewness = np.sum(p * delta ** 3, axis=-1) / spread ** 3
    fb = bins - bins.mean()
    slope = np.sum(folded * fb, axis=-1) / np.sum(fb ** 2)
    return np.stack([centroid, crest, decrease, entropy, flatness, kurtosis, rolloff, skewness, slope, spread], axis=-1).astype(np.float32)


def _representation(iq, sample_rate, kind):
    if kind == "features":
        return _feature_vector(iq, sample_rate)
    if kind == "spectrogram":
        return _spectrogram(iq)
    if kind == "feature_sequence":
        return _spectral_sequence(iq)
    return _standard_iq(iq)


def _softmax(logits):
    z = np.asarray(logits, dtype=np.float64)
    e = np.exp(z - np.max(z, axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def _torch_model(topology, num_classes=2):
    try:
        import torch
        from torch import nn
        from torch.nn import functional as F
    except ImportError as exc:
        raise ValueError("Neuronales Training benötigt PyTorch. ./setup.sh --neural-cpu oder ./setup.sh --neural ausführen.") from exc
    family, p = topology["family"], topology["parameters"]
    width, depth, kernel = p["width"], p["depth"], p.get("kernel", 7)

    class CausalConv(nn.Module):
        def __init__(self, a, b, k, dilation=1):
            super().__init__()
            self.pad = (k - 1) * dilation
            self.conv = nn.Conv1d(a, b, k, dilation=dilation)
        def forward(self, x):
            return self.conv(F.pad(x, (self.pad, 0)))

    class Residual(nn.Module):
        def __init__(self, a, b):
            super().__init__()
            self.main = nn.Sequential(nn.Conv1d(a, b, kernel, padding=kernel // 2), nn.BatchNorm1d(b), nn.ReLU(), nn.Conv1d(b, b, kernel, padding=kernel // 2))
            self.skip = nn.Conv1d(a, b, 1) if a != b else nn.Identity()
        def forward(self, x):
            return F.relu(self.main(x) + self.skip(x))

    class Recurrent(nn.Module):
        def __init__(self):
            super().__init__()
            cell = nn.LSTM if family == "fb_lstm" else nn.GRU
            self.sequence = cell(10, width, depth, batch_first=True, dropout=.1 if depth > 1 else 0)
            self.head = nn.Sequential(nn.Linear(width, width), nn.ReLU(), nn.Linear(width, num_classes))
        def forward(self, x):
            x, _ = self.sequence(x)
            return self.head(x[:, -1])

    class PatchTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            self.projection = nn.Linear(64, width)
            self.position = nn.Parameter(torch.zeros(1, 32, width))
            layer = nn.TransformerEncoderLayer(width, p.get("heads", 4), width * 2, dropout=.1, batch_first=True, activation="gelu")
            self.encoder = nn.TransformerEncoder(layer, depth, enable_nested_tensor=False)
            self.head = nn.Linear(width, num_classes)
        def forward(self, x):
            x = x.transpose(1, 2).reshape(len(x), 32, 64)
            return self.head(self.encoder(self.projection(x) + self.position).mean(dim=1))

    if family == "feature_mlp":
        layers, incoming = [], 10
        for _ in range(depth):
            layers += [nn.Linear(incoming, width), nn.GELU()]
            incoming = width
        return nn.Sequential(*layers, nn.Linear(width, num_classes))
    if family in ("fb_lstm", "gru"):
        return Recurrent()
    if family == "transformer":
        return PatchTransformer()
    if family == "spectrogram_cnn":
        layers, incoming = [], 1
        for i in range(depth):
            outgoing = width * 2 ** min(i, 2)
            layers += [nn.Conv2d(incoming, outgoing, 3, padding=1), nn.BatchNorm2d(outgoing), nn.ReLU()]
            if i < 3 or i == 4:
                layers += [nn.MaxPool2d(2)]
            incoming = outgoing
        return nn.Sequential(*layers, nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(.2), nn.Linear(incoming, width), nn.ReLU(), nn.Linear(width, num_classes))
    layers, incoming = [], 2
    for i in range(depth):
        outgoing = width * 2 ** min(i, 2)
        if family == "resnet":
            layers += [Residual(incoming, outgoing)]
        elif family == "five_cnn":
            layers += [CausalConv(incoming, outgoing, kernel), nn.BatchNorm1d(outgoing), nn.ReLU()]
        elif family == "tcn":
            layers += [CausalConv(incoming, outgoing, kernel, dilation=2 ** i), nn.BatchNorm1d(outgoing), nn.GELU()]
        else:
            layers += [nn.Conv1d(incoming, outgoing, kernel, padding=kernel // 2), nn.GELU()]
        layers += [nn.AvgPool1d(2)]
        incoming = outgoing
    return nn.Sequential(*layers, nn.Dropout(.5 if family == "five_cnn" else .1), nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(incoming, num_classes))


def _validate_config(config):
    c = dict(config)
    def scalar(key, default, lo, hi, integer=False):
        v = c.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not lo <= v <= hi or (integer and int(v) != v):
            raise ValueError(f"{key}: Wert zwischen {lo} und {hi} erforderlich.")
        c[key] = int(v) if integer else float(v)
    scalar("seed", 42, 0, 2 ** 31 - 1, True)
    scalar("samples_per_class", 64, 12, 1000, True)
    scalar("segment_length", 4096, 256, 65_536, True)
    scalar("epochs", 8, 1, 100, True)
    scalar("batch_size", 32, 4, 256, True)
    scalar("learning_rate", .001, .00001, .1)
    scalar("sample_rate", 256_000, 8000, 10_000_000)
    for key, default, lo, hi, limit in [("snr_db", [0, 10, 20], -30, 80, 12), ("jitter", [0], 0, 2, 8),
                                       ("segment_lengths", [c["segment_length"]], 256, 65_536, 4),
                                       ("learning_rates", [c["learning_rate"]], .00001, .1, 4),
                                       ("batch_sizes", [c["batch_size"]], 4, 256, 4)]:
        values = c.get(key, default)
        if not isinstance(values, list) or not 1 <= len(values) <= limit or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not np.isfinite(v) or not lo <= v <= hi for v in values):
            raise ValueError(f"{key}: 1–{limit} numerische Werte zwischen {lo} und {hi} erforderlich.")
        if key in ("segment_lengths", "batch_sizes") and any(int(v) != v for v in values):
            raise ValueError(f"{key} muss ganze Zahlen enthalten.")
        c[key] = sorted(set(values))
    if len(c["segment_lengths"]) * len(c["learning_rates"]) * len(c["batch_sizes"]) > 12:
        raise ValueError("Maximal zwölf Kombinationen aus Segmentlänge, Lernrate und Batchgröße pro Experiment.")
    c.setdefault("source", "recordings" if c.get("recordings") else "synthetic")
    if c["source"] not in ("synthetic", "recordings") or c.get("device", "auto") not in ("auto", "cpu", "cuda"):
        raise ValueError("Quelle oder Rechengerät ist ungültig.")
    if c["source"] == "recordings" and not c.get("recordings"):
        raise ValueError("Für dieses Experiment fehlen bestätigte AM-/FM-Aufnahmen.")
    c["device"] = c.get("device", "auto")
    c["classes"] = CLASSES if c["source"] == "synthetic" else sorted(set(str(x.get("label", "")) for x in c["recordings"] if x.get("verified") is not False and x.get("label")))
    if not 2 <= len(c["classes"]) <= 64 or any(len(x) > 120 for x in c["classes"]):
        raise ValueError("Experimente benötigen 2–64 bestätigte Klassen mit maximal 120 Zeichen je Klassenname.")
    c["include_clean"] = bool(c.get("include_clean", True))
    effects = c.get("augmentation", {})
    limits = {"frequency_offset_hz": 1_000_000, "sample_clock_ppm": 1000, "iq_gain_db": 12,
              "iq_phase_deg": 45, "dc_offset": 1, "clip_level": 10, "echo_amplitude": .95, "echo_delay_samples": 128}
    if not isinstance(effects, dict) or any(key not in limits or isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 <= value <= limits[key] for key, value in effects.items()):
        raise ValueError("Ungültige Kanalaugmentation; siehe dokumentierte Wertebereiche.")
    c["augmentation"] = effects
    return c


def generate_signal(modulation, length=4096, sample_rate=256_000, seed=42):
    """Diverse baseband AM/FM fixtures. Each seed is an independent source group."""
    r = np.random.default_rng(seed)
    n = int(length)
    t = np.arange(n) / float(sample_rate)
    message = np.zeros(n)
    for _ in range(int(r.integers(2, 8))):
        freq = r.uniform(.001, .025) * sample_rate
        message += r.uniform(.2, 1) * np.sin(2 * np.pi * freq * t + r.uniform(-np.pi, np.pi))
    # Smooth random component prevents overfitting to a single test tone.
    noise = signal.lfilter([1], [1, -.95], r.normal(size=n))
    message += .15 * noise
    message /= max(float(np.max(np.abs(message))), 1e-10)
    if modulation == "AM":
        x = 1 + r.uniform(.25, .95) * message
    elif modulation == "FM":
        deviation = r.uniform(.015, .18) * sample_rate
        x = np.exp(2j * np.pi * deviation / sample_rate * np.cumsum(message))
    else:
        raise ValueError("Synthetischer Generator unterstützt AM oder FM.")
    cfo = r.uniform(-.15, .15)
    x = x * np.exp(1j * (2 * np.pi * cfo * np.arange(n) + r.uniform(-np.pi, np.pi)))
    return (x / max(float(np.sqrt(np.mean(np.abs(x) ** 2))), 1e-12)).astype(np.complex64)


def augment(iq, snr_db, jitter=0, seed=42, sample_rate=256_000, effects=None):
    x = np.asarray(iq, dtype=np.complex64).copy()
    r = np.random.default_rng(seed)
    effects = effects or {}
    ppm = float(effects.get("sample_clock_ppm", 0))
    if jitter or ppm:
        indices = np.arange(len(x))
        warped = np.clip(indices * (1 + r.uniform(-ppm, ppm) * 1e-6) + r.normal(0, jitter, len(x)), 0, len(x) - 1)
        x = np.interp(warped, indices, x.real) + 1j * np.interp(warped, indices, x.imag)
    cfo = float(effects.get("frequency_offset_hz", 0))
    if cfo:
        x = x * np.exp(2j * np.pi * r.uniform(-cfo, cfo) / sample_rate * np.arange(len(x)))
    gain = 10 ** (r.uniform(-effects.get("iq_gain_db", 0), effects.get("iq_gain_db", 0)) / 20)
    angle = np.deg2rad(r.uniform(-effects.get("iq_phase_deg", 0), effects.get("iq_phase_deg", 0)))
    x = x.real + 1j * gain * (x.imag * np.cos(angle) + x.real * np.sin(angle))
    dc = float(effects.get("dc_offset", 0))
    if dc:
        x += dc * np.exp(1j * r.uniform(-np.pi, np.pi))
    echo = float(effects.get("echo_amplitude", 0))
    if echo:
        delay = max(1, int(effects.get("echo_delay_samples", 8)))
        if delay < len(x):
            x[delay:] += echo * np.exp(1j * r.uniform(-np.pi, np.pi)) * x[:-delay].copy()
    level = float(effects.get("clip_level", 0))
    if level:
        x = np.clip(x.real, -level, level) + 1j * np.clip(x.imag, -level, level)
    power = float(np.mean(np.abs(x) ** 2))
    sigma = np.sqrt(power / (2 * 10 ** (float(snr_db) / 10)))
    return (x + sigma * (r.normal(size=len(x)) + 1j * r.normal(size=len(x)))).astype(np.complex64)


def _synthetic_groups(config, length):
    n = config["samples_per_class"]
    groups = {"train": [], "validation": [], "test": []}
    for label in CLASSES:
        for i in range(n):
            split = "train" if i < int(.6 * n) else "validation" if i < int(.8 * n) else "test"
            seed = config["seed"] + 100_000 * CLASSES.index(label) + i * 127
            groups[split].append({"iq": generate_signal(label, length, config["sample_rate"], seed), "label": CLASSES.index(label),
                                  "sample_rate": config["sample_rate"], "group_id": f"synthetic-{label}-{seed}"})
    return groups


def _recording_groups(config, length):
    from .sigmf import read_recording
    items = config["recordings"]
    assignments, groups = {}, {"train": [], "validation": [], "test": []}
    r = np.random.default_rng(config["seed"])
    if len(items) > 128:
        raise ValueError("Maximal 128 Aufnahmen pro Experiment; größere Datensätze in getrennten Experimenten prüfen.")
    classes = config["classes"]
    eligible = [x for x in items if x.get("label") in classes and x.get("verified") is not False]
    membership = {}
    for entry in eligible:
        gid = str(entry.get("group_id") or entry.get("id") or entry.get("metadata_path") or entry.get("path"))
        membership.setdefault(gid, set()).add(entry["label"])
    for label in classes:
        ids = [gid for gid, labels in membership.items() if label in labels]
        if len(ids) < 3:
            raise ValueError(f"{label}: mindestens drei unabhängige bestätigte Aufnahmen für Training, Validierung und Test erforderlich.")
    # One assignment per physical source group, across labels and every derived clip.
    ids = sorted(membership)
    n = len(ids)
    targets = [(n - v - t, v, t) for v in range(1, min(n - 1, max(5, n // 3 + 1)))
               for t in range(1, min(n - v, max(5, n // 3 + 1))) if n - v - t >= 1]
    targets.sort(key=lambda z: sum((z[i] / n - [.6, .2, .2][i]) ** 2 for i in range(3)))
    for sizes in targets[:30]:
        for _ in range(150):
            order = list(r.permutation(ids))
            parts = [order[:sizes[0]], order[sizes[0]:sizes[0] + sizes[1]], order[sizes[0] + sizes[1]:]]
            if all(set().union(*(membership[gid] for gid in part)) == set(classes) for part in parts):
                assignments = {gid: split for split, part in zip(groups, parts) for gid in part}
                break
        if assignments:
            break
    if not assignments:
        raise ValueError("Aufnahmegruppen lassen sich nicht mit allen Klassen auf Training, Validierung und Test aufteilen. Weitere unabhängige Aufnahmen ergänzen.")
    retained_samples = 0
    for entry in items:
        label = entry.get("label")
        if label not in classes or entry.get("verified") is False:
            continue
        path = entry.get("metadata_path") or entry.get("path")
        if not path:
            raise ValueError("Aufnahmepfad fehlt.")
        group_id = str(entry.get("group_id") or entry.get("id") or path)
        split = assignments[group_id]
        # Bound memory and segments. Read only the beginning of long captures.
        count = min(4_194_304, length * min(64, config["samples_per_class"]))
        # Budget before loading, independent of the number of files and segment size.
        if retained_samples + count > 16_777_216:
            raise ValueError("Aufnahmedatensatz überschreitet das 128-MiB-IQ-Budget. Weniger Aufnahmen/Segmente wählen.")
        x, metadata = read_recording(path, count=count)
        retained_samples += len(x)
        rate = float(metadata.get("global", {}).get("core:sample_rate", metadata.get("sample_rate", config["sample_rate"])))
        for start in range(0, len(x) - length + 1, length):
            groups[split].append({"iq": x[start:start + length].copy(), "label": classes.index(label), "sample_rate": rate, "group_id": group_id})
    for split, entries in groups.items():
        for label in range(len(classes)):
            if not any(x["label"] == label for x in entries):
                raise ValueError(f"{split}: kein vollständiges Segment für {classes[label]}; längere Aufnahmen oder kürzere Segmente wählen.")
        if len(entries) > 4096:
            raise ValueError("Zu viele Aufnahmesegmente; pro Experiment maximal 4096 je Split.")
    return groups


def _dataset(entries, topology, config, rng, snr=None, jitter=None):
    x, y = [], []
    for entry in entries:
        sn = float(rng.choice(config["snr_db"])) if snr is None else snr
        ji = float(rng.choice(config["jitter"])) if jitter is None else jitter
        # For real recordings this is injected SNR relative to recorded total power, not absolute RF SNR.
        data = entry["iq"] if snr == "clean" else augment(entry["iq"], sn, ji, int(rng.integers(0, 2 ** 31)), entry["sample_rate"], config.get("augmentation"))
        x.append(_representation(data, entry["sample_rate"], topology["input"]))
        y.append(entry["label"])
    return np.stack(x).astype(np.float32), np.array(y, dtype=np.int64)


def _fit_lda(x, y):
    nclasses = int(np.max(y)) + 1
    means = np.stack([x[y == i].mean(axis=0) for i in range(nclasses)])
    centered = x - means[y]
    cov = centered.T @ centered / max(1, len(x) - nclasses)
    cov = .9 * cov + .1 * np.eye(x.shape[1]) * max(float(np.trace(cov) / x.shape[1]), .01)
    precision = np.linalg.pinv(cov)
    weights = means @ precision
    bias = -.5 * np.sum(means * weights, axis=1) + np.log(np.bincount(y, minlength=nclasses) / len(y))
    return {"weights": weights.astype(np.float32), "bias": bias.astype(np.float32)}


def _metrics(y, probabilities, classes=None):
    classes = classes or CLASSES
    nclasses = len(classes)
    pred = np.argmax(probabilities, axis=1)
    matrix = np.zeros((nclasses, nclasses), dtype=int)
    np.add.at(matrix, (y, pred), 1)
    recalls = [matrix[i, i] / max(1, matrix[i].sum()) for i in range(nclasses)]
    f1 = [2 * matrix[i, i] / max(1, matrix[i].sum() + matrix[:, i].sum()) for i in range(nclasses)]
    confidence = probabilities.max(axis=1)
    correct = pred == y
    ece = 0.
    for lo in np.arange(0, 1, .1):
        mask = (confidence >= lo) & (confidence < lo + .100000001)
        if mask.any():
            ece += mask.mean() * abs(float(correct[mask].mean() - confidence[mask].mean()))
    accuracy = float(correct.mean())
    n, z = len(y), 1.96
    denom = 1 + z ** 2 / n
    center = (accuracy + z ** 2 / (2 * n)) / denom
    half = z * math.sqrt(accuracy * (1 - accuracy) / n + z ** 2 / (4 * n ** 2)) / denom
    return {"accuracy": accuracy, "balanced_accuracy": float(np.mean(recalls)), "macro_f1": float(np.mean(f1)),
            "brier_score": float(np.mean(np.sum((probabilities - np.eye(nclasses)[y]) ** 2, axis=1))),
            "ece": float(ece), "accuracy_ci95": [max(0., center - half), min(1., center + half)],
            "count": len(y), "confusion_matrix": matrix.tolist(), "recall": dict(zip(classes, recalls))}


def hardware_profile():
    info = {"cpu": platform.processor() or platform.machine(), "logical_cpus": os.cpu_count(),
            "platform": platform.platform(), "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy_version, "signalclassifier": __version__,
            "precision": "float32 (features accumulated in float64)", "torch_available": importlib.util.find_spec("torch") is not None,
            "gpu": None, "cuda_available": False}
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        info["cpu"] = next(line.split(":", 1)[1].strip() for line in cpuinfo.splitlines() if line.startswith("model name"))
    except (OSError, StopIteration):
        pass
    try:
        import psutil
        info.update(ram_bytes=int(psutil.virtual_memory().total), available_ram_bytes=int(psutil.virtual_memory().available),
                    process_rss_bytes=int(psutil.Process().memory_info().rss))
    except ImportError:
        pass
    try:
        import torch
        info.update(torch_version=torch.__version__, cuda_available=torch.cuda.is_available(), torch_threads=torch.get_num_threads())
        if torch.cuda.is_available():
            dev = torch.cuda.get_device_properties(0)
            info.update(gpu=dev.name, gpu_memory_bytes=dev.total_memory)
    except ImportError:
        pass
    return info


def benchmark_hardware():
    fixture = generate_signal("AM", 4096, seed=17)
    for _ in range(3):
        extract_features(fixture)
    durations = []
    for _ in range(12):
        begin = time.perf_counter()
        extract_features(fixture)
        durations.append((time.perf_counter() - begin) * 1000)
    return {"hardware": hardware_profile(), "reference": "signalclassifier-features-v1/AM4096-fs256000-seed17",
            "reference_latency_ms": float(np.median(durations)), "p95_ms": float(np.percentile(durations, 95)),
            "warmup": 3, "repetitions": 12, "batch_size": 1,
            "interpretation": "Verhältnis zu derselben CPU-Referenzlast; keine exakte Prognose für andere CPUs oder GPUs."}


def run_experiment(config: dict, output_dir, progress=None) -> dict:
    c = _validate_config(config)
    classes = c["classes"]
    if c.get("topology"):
        topology = validate_topology(c["topology"])
    else:
        topology = validate_topology({"id": c.get("topology_id", "feature-lda")})
    topology["output_classes"] = list(classes)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    def emit(fraction, stage, message):
        if progress:
            progress({"progress": float(fraction), "stage": stage, "message": message})
    emit(.01, "prepare", "Unabhängige Aufnahmegruppen und Vorverarbeitung vorbereiten")
    np.random.seed(c["seed"])
    neural = topology["family"] != "feature_lda"
    device, torch = "cpu", None
    if neural:
        try:
            import torch
        except ImportError as exc:
            raise ValueError("Für diese Topologie fehlt PyTorch; ./setup.sh --neural-cpu oder --neural verwenden.") from exc
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        torch.manual_seed(c["seed"])
        if c["device"] == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA wurde gewählt, ist aber in diesem Python-/WSL-System nicht verfügbar.")
        device = "cuda" if c["device"] != "cpu" and torch.cuda.is_available() else "cpu"
        if device == "cuda":
            torch.cuda.manual_seed_all(c["seed"])
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.deterministic = True
            torch.cuda.reset_peak_memory_stats()
    elif c["device"] == "cuda":
        raise ValueError("Feature-LDA ist ein CPU-Verfahren. Für CUDA ein neuronales Netz wählen.")
    combos = [(int(n), float(lr), int(bs)) for n in c["segment_lengths"] for lr in c["learning_rates"] for bs in c["batch_sizes"]]
    if not neural:
        combos = [(int(n), c["learning_rate"], c["batch_size"]) for n in c["segment_lengths"]]
    benchmark = benchmark_hardware()
    rss_samples = []
    def sample_rss():
        try:
            import psutil
            rss_samples.append(int(psutil.Process().memory_info().rss))
        except ImportError:
            pass
    sample_rss()
    all_trials, evaluations, candidates = [], [], []
    wall_start = time.perf_counter()
    for ci, (length, lr, batch_size) in enumerate(combos):
        rng = np.random.default_rng(c["seed"])
        groups = _synthetic_groups(c, length) if c["source"] == "synthetic" else _recording_groups(c, length)
        segment_fingerprints = sorted(f"{entry['group_id']}|{entry['label']}|{entry['sample_rate']}|{hashlib.sha256(entry['iq'].tobytes()).hexdigest()}"
                                      for entries in groups.values() for entry in entries)
        dataset_sha256 = hashlib.sha256("\n".join(segment_fingerprints).encode()).hexdigest()
        preprocessing_start = time.perf_counter()
        xtrain, ytrain = _dataset(groups["train"], topology, c, rng)
        xval, yval = _dataset(groups["validation"], topology, c, rng)
        if c["include_clean"]:
            clean_train, clean_y = _dataset(groups["train"], topology, c, rng, snr="clean", jitter=0)
            clean_val, clean_vy = _dataset(groups["validation"], topology, c, rng, snr="clean", jitter=0)
            xtrain, ytrain = np.concatenate([xtrain, clean_train]), np.concatenate([ytrain, clean_y])
            xval, yval = np.concatenate([xval, clean_val]), np.concatenate([yval, clean_vy])
        # Normalization fitted strictly on training representations.
        axis = (0, 1) if topology["input"] == "feature_sequence" else (0,)
        if topology["input"] in ("features", "feature_sequence"):
            mean, scale = xtrain.mean(axis=axis), np.maximum(xtrain.std(axis=axis), 1e-5)
        else:
            mean, scale = np.array(0., dtype=np.float32), np.array(1., dtype=np.float32)
        xtrain, xval = (xtrain - mean) / scale, (xval - mean) / scale
        preprocessing_seconds = time.perf_counter() - preprocessing_start
        training_start = time.perf_counter()
        history = []
        if not neural:
            weights = _fit_lda(xtrain, ytrain)
            def predict(x):
                return _softmax(x @ weights["weights"].T + weights["bias"])
        else:
            model = _torch_model(topology, len(classes)).to(device)
            optimizer = torch.optim.Adam(model.parameters(), lr=lr)
            tx, ty = torch.from_numpy(xtrain).to(device), torch.from_numpy(ytrain).to(device)
            best_loss, best_state = float("inf"), None
            for epoch in range(c["epochs"]):
                model.train()
                order = torch.randperm(len(tx), device=device)
                losses = []
                for offset in range(0, len(tx), batch_size):
                    batch = order[offset:offset + batch_size]
                    optimizer.zero_grad(set_to_none=True)
                    loss = torch.nn.functional.cross_entropy(model(tx[batch]), ty[batch])
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
                    optimizer.step()
                    losses.append(float(loss.detach().cpu()))
                model.eval()
                with torch.inference_mode():
                    vl = float(torch.nn.functional.cross_entropy(model(torch.from_numpy(xval).to(device)), torch.from_numpy(yval).to(device)).cpu())
                history.append({"epoch": epoch + 1, "training_loss": float(np.mean(losses)), "validation_loss": vl})
                sample_rss()
                if vl < best_loss:
                    best_loss, best_state = vl, {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
                emit(.06 + .68 * (ci + (epoch + 1) / c["epochs"]) / len(combos), "train", f"Kombination {ci + 1}/{len(combos)}, Epoche {epoch + 1}/{c['epochs']}")
            model.load_state_dict(best_state)
            model.eval()
            if device == "cuda":
                torch.cuda.synchronize()
            def predict(x):
                with torch.inference_mode():
                    batches = [torch.softmax(model(torch.from_numpy(x[start:start + batch_size].astype(np.float32)).to(device)), dim=-1).cpu().numpy() for start in range(0, len(x), batch_size)]
                return np.concatenate(batches)
        training_seconds = time.perf_counter() - training_start
        sample_rss()
        val_probabilities = predict(xval)
        validation = _metrics(yval, val_probabilities, classes)
        modeldir = out / f"candidate-{ci + 1}"
        modeldir.mkdir(exist_ok=True)
        if neural:
            torch.save({key: value.detach().cpu() for key, value in model.state_dict().items()}, modeldir / "weights.pt")
            np.savez(modeldir / "normalization.npz", mean=mean, scale=scale)
        else:
            np.savez(modeldir / "weights.npz", **weights, mean=mean, scale=scale)
        manifest = {"schema_version": _SCHEMA, "classes": classes, "family": topology["family"], "topology_id": topology["id"],
                    "topology": topology, "input": topology["input"], "segment_length": length, "sample_rate": c["sample_rate"],
                    "sample_rates": sorted(set(float(x["sample_rate"]) for split in groups.values() for x in split)),
                    "source": c["source"], "weights_file": "weights.pt" if neural else "weights.npz", "device_trained": device,
                    "dataset_sha256": dataset_sha256,
                    "feature_names": FEATURE_NAMES, "confidence_threshold": .6, "frequency_metadata_input": False, "include_clean_training_examples": c["include_clean"],
                    "preprocessing": {"version": 1, "segment_length": length, "normalization": "training-only zscore for features", "iq_samples": 1024,
                                      "feature_sequence": {"resampled_samples": 2048, "frames": 16, "frame_samples": 128, "window": "Hamming"},
                                      "spectrogram": "32x32 log1p power, RF linear frequency", "timing_jitter_units": "RMS samples"},
                    "provenance": {"seed": c["seed"], "synthetic": c["source"] == "synthetic", "software": "SignalClassifier", "software_version": __version__, "schema_version": _SCHEMA},
                    "split_groups": {key: sorted(set(x["group_id"] for x in entries)) for key, entries in groups.items()}}
        _json(modeldir / "model.json", manifest)
        _json(modeldir / "topology.json", topology)
        model_bytes = sum(p.stat().st_size for p in modeldir.iterdir() if p.is_file())
        # Hyperparameter/model selection uses validation only; never best test score.
        candidate = {"candidate": ci + 1, "model_path": str(modeldir), "segment_length": length, "learning_rate": lr, "batch_size": batch_size,
                     "validation": validation, "training_seconds": training_seconds, "preprocessing_seconds": preprocessing_seconds,
                     "model_bytes": model_bytes, "history": history, "parameter_count": int(sum(p.numel() for p in model.parameters())) if neural else 11 * len(classes),
                     "training_signal_seconds": float(sum(len(x["iq"]) / x["sample_rate"] for x in groups["train"]) * (2 if c["include_clean"] else 1))}
        candidates.append(candidate)
        candidate_evaluations = []
        for snr in c["snr_db"]:
            for jitter in c["jitter"]:
                emit(.75 + .18 * ci / len(combos), "evaluate", f"Unabhängiger Test: SNR {snr} dB, Jitter {jitter} Samples")
                xt, yt = _dataset(groups["test"], topology, c, rng, snr, jitter)
                prob = predict((xt - mean) / scale)
                m = _metrics(yt, prob, classes)
                trial = {"candidate": ci + 1, "segment_length": length, "learning_rate": lr, "batch_size": batch_size,
                         "snr_db": snr, "jitter": jitter, **m}
                all_trials.append(trial)
                candidate_evaluations.append((yt, prob))
        # End-to-end single-segment inference timings, including representation conversion.
        durations, duration_ratios = [], []
        entries = groups["test"]
        for entry in entries[:3]:
            if topology["input"] != "features":
                extract_features(entry["iq"], entry["sample_rate"])
            predict((np.stack([_representation(entry["iq"], entry["sample_rate"], topology["input"])]) - mean) / scale)
        for i in range(min(30, max(10, len(entries)))):
            entry = entries[i % len(entries)]
            if neural and device == "cuda":
                torch.cuda.synchronize()
            begin = time.perf_counter()
            if topology["input"] != "features":
                extract_features(entry["iq"], entry["sample_rate"])
            predict((np.stack([_representation(entry["iq"], entry["sample_rate"], topology["input"])]) - mean) / scale)
            if neural and device == "cuda":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - begin
            durations.append(elapsed * 1000)
            duration_ratios.append(elapsed / (len(entry["iq"]) / entry["sample_rate"]))
        candidate.update(latency_ms_p50=float(np.median(durations)), latency_ms_p95=float(np.percentile(durations, 95)),
                         real_time_factor=float(np.median(duration_ratios)), timed_sample_rates=sorted(set(x["sample_rate"] for x in entries)))
        for trial in all_trials:
            if trial["candidate"] == ci + 1:
                trial.update(latency_ms_p50=candidate["latency_ms_p50"])
        evaluations.append(candidate_evaluations)
    best_index = max(range(len(candidates)), key=lambda i: (candidates[i]["validation"]["macro_f1"], -candidates[i]["model_bytes"]))
    best = candidates[best_index]
    y = np.concatenate([x[0] for x in evaluations[best_index]])
    probabilities = np.concatenate([x[1] for x in evaluations[best_index]])
    summary = _metrics(y, probabilities, classes)
    # CI above counts augmented observations; record-group correlation limits interpretation.
    summary.update({key: best[key] for key in ("training_seconds", "preprocessing_seconds", "latency_ms_p50", "latency_ms_p95", "model_bytes", "parameter_count")})
    summary.update(real_time_factor=best["real_time_factor"], timed_sample_rates=best["timed_sample_rates"],
                   reference_latency_ms=benchmark["reference_latency_ms"], normalized_latency=best["latency_ms_p50"] / max(1e-9, benchmark["reference_latency_ms"]),
                   false_alarm_rate=None, unknown_detection_accuracy=None,
                   training_duration_ratio=best["training_seconds"] / max(1e-9, c["epochs"] * best["training_signal_seconds"]) if neural else None,
                   wall_seconds=time.perf_counter() - wall_start)
    zero_jitter = [x for x in all_trials if x["candidate"] == best_index + 1 and x["jitter"] == min(c["jitter"])]
    passing = [x["snr_db"] for x in zero_jitter if x["balanced_accuracy"] >= .9]
    summary["sensitivity_snr90_db"] = min(passing) if passing else None
    sample_rss()
    if rss_samples:
        summary.update(process_rss_before_bytes=rss_samples[0], process_rss_after_bytes=rss_samples[-1],
                       sampled_process_rss_max_bytes=max(rss_samples),
                       memory_scope="Gemeinsamer App-Prozess, vor/nach Training und pro Epoche abgetastet; kein isolierter Trainings-Peak.")
    hardware = hardware_profile() | {"device": device, "batch_size": best["batch_size"], "inference_batch_size": 1,
                                    "warmup": 3, "timing_repetitions": min(30, max(10, len(entries)))}
    if neural and device == "cuda":
        summary["peak_gpu_bytes"] = int(torch.cuda.max_memory_allocated())
    safe_config = {key: value for key, value in c.items() if key != "recordings"}
    safe_config["recording_groups"] = [str(x.get("group_id") or x.get("id") or "") for x in c.get("recordings", [])]
    report = {"schema_version": 1, "source": c["source"], "synthetic": c["source"] == "synthetic", "config": safe_config,
              "topology": topology, "model_path": best["model_path"], "metrics": summary, "hardware": hardware, "benchmark": benchmark,
              "trials": all_trials, "candidates": candidates, "selected_candidate": best_index + 1, "selection": "Höchstes Validierungs-Macro-F1; bei Gleichstand kleineres Modell",
              "confusion_matrix": summary["confusion_matrix"], "classes": classes, "history": best["history"],
              "evaluation_policy": "closed-set argmax without runtime abstention; runtime rejection/coverage require separate open-set validation",
              "split_groups": load_model_metadata(best["model_path"])["split_groups"],
              "dataset_sha256": load_model_metadata(best["model_path"])["dataset_sha256"],
              "limitations": ["Synthetische Ergebnisse sind kein Nachweis mit realen Empfängern." if c["source"] == "synthetic" else "SNR bezeichnet zusätzlich injiziertes Rauschen relativ zur Gesamtleistung der Aufnahme; der ursprüngliche RF-SNR ist unbekannt.",
                              "Konfidenzen sind Modellwerte; ECE und Brier bewerten ihre Kalibrierung, ersetzen sie aber nicht.",
                              "Synthetischer Generator umfasst AM/FM; reale bestätigte Aufnahmen erlauben weitere Klassen. Unbekannte Signale benötigen einen eigenen unabhängigen Negativdatensatz; Fehlalarmrate bleibt deshalb offen.",
                              "Augmentierungen derselben Testaufnahme sind korreliert; das Wilson-Intervall ist nur eine deskriptive Orientierung.",
                              "Referenz-normalisierte Laufzeit ist eine Vergleichskennzahl, keine exakte Hardwareübertragung.",
                              "Optionale Kanalaugmentationen werden zufällig innerhalb konfigurierter Grenzen angewendet; separate Robustheitskurven gibt es in diesem Build für SNR und Jitter."]}
    _json(out / "report.json", report)
    _json(Path(best["model_path"]) / "report.json", report)
    emit(1., "complete", "Experiment ausgewertet; Modell und Konfiguration gespeichert")
    return report


def load_model_metadata(path) -> dict:
    p = Path(path)
    if p.is_dir():
        p /= "model.json"
    if not p.is_file() or p.stat().st_size > 1_048_576:
        raise ValueError("Modellmanifest fehlt oder ist zu groß.")
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Modellmanifest ist kein gültiges JSON.") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Modellmanifest muss ein JSON-Objekt sein.")
    classes = manifest.get("classes")
    if manifest.get("schema_version") != _SCHEMA or not isinstance(classes, list) or not 2 <= len(classes) <= 64 or any(not isinstance(x, str) or not 1 <= len(x) <= 120 for x in classes) or len(set(classes)) != len(classes):
        raise ValueError("Nicht unterstütztes Modellschema oder Klassenliste.")
    length = manifest.get("segment_length")
    rate = manifest.get("sample_rate")
    threshold = manifest.get("confidence_threshold", .6)
    if type(length) is not int or not 256 <= length <= 65536 or not isinstance(rate, (float, int)) or not np.isfinite(rate) or not 1 <= rate <= 100_000_000 or not isinstance(threshold, (float, int)) or not .5 <= threshold <= 1:
        raise ValueError("Ungültige Segmentlänge, Abtastrate oder Ablehnungsgrenze im Modellmanifest.")
    topology = validate_topology(manifest.get("topology", {}))
    if manifest.get("family") != topology["family"] or manifest.get("input") != topology["input"]:
        raise ValueError("Modellfamilie und Eingabedarstellung passen nicht zur Topologie.")
    expected = "weights.npz" if topology["family"] == "feature_lda" else "weights.pt"
    if manifest.get("weights_file") != expected:
        raise ValueError("Ungültiger Gewichtsdateiname.")
    if not (p.parent / expected).is_file() or (p.parent / expected).stat().st_size > 134_217_728:
        raise ValueError("Gewichtsdatei fehlt oder überschreitet 128 MiB.")
    return manifest


def _bounded_npz(path, shapes):
    """Check NPY headers before NumPy can allocate an attacker-declared giant array."""
    try:
        with zipfile.ZipFile(path) as archive:
            if set(archive.namelist()) != {name + ".npy" for name in shapes}:
                raise ValueError("Unerwartete Arrays im NPZ-Modellarchiv.")
            for name, expected in shapes.items():
                info = archive.getinfo(name + ".npy")
                with archive.open(info) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        shape, _, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=16384)
                    elif version == (2, 0):
                        shape, _, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=16384)
                    else:
                        raise ValueError("Nicht unterstützte NPY-Headerversion.")
                    if shape != expected or dtype.kind != "f" or dtype.itemsize not in (4, 8) or math.prod(shape) * dtype.itemsize > info.file_size - stream.tell():
                        raise ValueError("Ungültige Arraydimension oder Typ im Modellarchiv.")
        with np.load(path, allow_pickle=False) as arrays:
            return {name: np.array(arrays[name]) for name in shapes}
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError("Ungültiges oder beschädigtes NPZ-Modellarchiv.") from exc


def _load_inference(path):
    root = Path(path)
    root = root if root.is_dir() else root.parent
    manifest = load_model_metadata(root)
    topology = manifest["topology"]
    nclasses = len(manifest["classes"])
    weight_path = root / manifest["weights_file"]
    try:
        with zipfile.ZipFile(weight_path) as archive:
            if len(archive.infolist()) > 1024 or sum(x.file_size for x in archive.infolist()) > 134_217_728:
                raise ValueError("Gewichtsarchiv überschreitet das entpackte Speicherbudget.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Gewichte müssen im unterstützten NPZ-/PyTorch-ZIP-Format vorliegen.") from exc
    if manifest["family"] == "feature_lda":
        values = _bounded_npz(root / "weights.npz", {"weights": (nclasses, 10), "bias": (nclasses,), "mean": (10,), "scale": (10,)})
        if values["weights"].shape != (nclasses, 10) or values["bias"].shape != (nclasses,) or values["mean"].shape != (10,) or values["scale"].shape != (10,):
            raise ValueError("Ungültige LDA-Gewichtsdimensionen.")
        if not all(np.isfinite(v).all() for v in values.values()) or (values["scale"] <= 0).any():
            raise ValueError("Ungültige numerische Modellgewichte.")
        def predict(x):
            return _softmax(((x - values["mean"]) / values["scale"]) @ values["weights"].T + values["bias"])
        def explain(x, chosen, second):
            normalized = (x[0] - values["mean"]) / values["scale"]
            contributions = normalized * (values["weights"][chosen] - values["weights"][second])
            items = [{"feature": name, "contribution": float(value), "normalized_value": float(normalized[i])}
                     for i, (name, value) in enumerate(zip(FEATURE_NAMES, contributions))]
            items.sort(key=lambda item: abs(item["contribution"]), reverse=True)
            return {"method": "LDA logit-margin decomposition", "compared_classes": [manifest["classes"][chosen], manifest["classes"][second]],
                    "bias": float(values["bias"][chosen] - values["bias"][second]), "contributions": items,
                    "note": "Additive Beiträge zur Logit-Differenz zwischen den beiden höchstbewerteten Klassen; keine physikalische Kausalität."}
        predict.explain = explain
    else:
        try:
            import torch
        except ImportError as exc:
            raise ValueError("Dieses Modell benötigt PyTorch; ./setup.sh --neural-cpu oder --neural verwenden.") from exc
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        model = _torch_model(topology, nclasses)
        # weights_only rejects executable pickle payloads; only tensor state_dict is supported.
        state = torch.load(root / "weights.pt", map_location="cpu", weights_only=True)
        expected_state = model.state_dict()
        if not isinstance(state, dict) or set(state) != set(expected_state) or any(not torch.is_tensor(v) or v.shape != expected_state[k].shape or v.layout != torch.strided for k, v in state.items()):
            raise ValueError("Neuronale Gewichtsdimensionen passen nicht zur Topologie.")
        if any(not torch.isfinite(v).all() for v in state.values()):
            raise ValueError("Ungültiger neuronaler Gewichtsdatensatz.")
        model.load_state_dict(state, strict=True)
        model.eval()
        normalization_path = root / "normalization.npz"
        if not normalization_path.is_file() or normalization_path.stat().st_size > 65536:
            raise ValueError("Eingabenormalisierung fehlt oder ist zu groß.")
        with zipfile.ZipFile(normalization_path) as archive:
            if len(archive.infolist()) > 4 or sum(x.file_size for x in archive.infolist()) > 65536:
                raise ValueError("Normalisierungsarchiv überschreitet das Speicherbudget.")
        expected_shape = (10,) if topology["input"] in ("features", "feature_sequence") else ()
        normalization = _bounded_npz(root / "normalization.npz", {"mean": expected_shape, "scale": expected_shape})
        mean, scale = normalization["mean"], normalization["scale"]
        if mean.shape != expected_shape or scale.shape != expected_shape:
            raise ValueError("Falsche Dimension der Eingabenormalisierung.")
        if not np.isfinite(mean).all() or not np.isfinite(scale).all() or np.any(scale <= 0):
            raise ValueError("Ungültige Eingabenormalisierung.")
        def predict(x):
            with torch.inference_mode():
                return torch.softmax(model(torch.from_numpy(((x - mean) / scale).astype(np.float32))), dim=-1).numpy()
        def explain(x, chosen, second):
            v = torch.tensor(((x - mean) / scale).astype(np.float32), requires_grad=True)
            scores = model(v)
            gradient, = torch.autograd.grad(scores[0, chosen] - scores[0, second], v)
            attribution = (gradient * v).detach().numpy()[0]
            if topology["input"] == "features":
                items = [{"feature": name, "contribution": float(attribution[i])} for i, name in enumerate(FEATURE_NAMES)]
            elif topology["input"] == "feature_sequence":
                items = [{"window": i, "magnitude": float(np.sum(abs(row)))} for i, row in enumerate(attribution)]
            elif topology["input"] == "spectrogram":
                items = [{"frequency_bin": i, "magnitude": float(np.sum(abs(attribution[..., i])))} for i in range(attribution.shape[-1])]
            else:
                items = [{"sample_start": start, "sample_end": start + 128, "magnitude": float(np.sum(abs(attribution[:, start:start + 128])))} for start in range(0, 1024, 128)]
            return {"method": "local gradient×input of logit margin", "compared_classes": [manifest["classes"][chosen], manifest["classes"][second]],
                    "input_domain": topology["input"], "contributions": items,
                    "note": "Lokale Sensitivität des geladenen Netzes nach Vorverarbeitung; keine kausale Erklärung und keine kalibrierte Sicherheit."}
        predict.explain = explain
    return manifest, predict


def validate_model(path):
    manifest, predict = _load_inference(path)
    fixture = generate_signal("AM", int(manifest.get("segment_length", 4096)))
    p = predict(np.stack([_representation(fixture, manifest.get("sample_rate", 256_000), manifest["input"])]))
    if p.shape != (1, len(manifest["classes"])) or not np.isfinite(p).all():
        raise ValueError("Modell liefert keine gültigen Klassenscores.")
    return manifest


@functools.lru_cache(maxsize=4)
def _cached_inference(path, signature):
    return _load_inference(path)


def classify(iq, sample_rate, center_frequency_hz=None, use_priors=False, model_path=None, explain=False) -> dict:
    begin = time.perf_counter()
    x = _iq(iq)
    features = extract_features(x, sample_rate)
    steps = [{"stage": "Signalprüfung", "decision": "IQ-Segment verfügbar", "reason": f"{len(x)} Samples bei {sample_rate:g} S/s; Pegel relativ zu digitalem Vollmaß."}]
    explanation = None
    classes = CLASSES
    rejected, rejection_reason = None, None
    if features["rms_dbfs"] < -140:
        rejected = "kein Signal"
        rejection_reason = "Digitaler Pegel unter Mindestgrenze. Aufwendige Klassifikatoren werden übersprungen."
    elif features["spectral_flatness"] > .50 and features["phase_dispersion"] > .65 and features["envelope_correlation"] < .1:
        domain = load_model_metadata(model_path)["classes"] if model_path else CLASSES
        if set(domain) == set(CLASSES):
            rejected = "unbekannt"
            rejection_reason = "Rauschartige Eingabe außerhalb des AM-/FM-Prüfbereichs. Aufwendige Klassifikatoren werden übersprungen; breitbandige digitale Klassen benötigen ein eigenes Modell."
    if rejected is not None:
        skipped = ["saved_model_inference" if model_path else "AM/FM_heuristic"]
        steps.append({"stage": "Günstige Merkmalskaskade", "decision": rejected, "reason": rejection_reason})
        return {"label": rejected, "confidence": 0., "confidence_kind": "feature_rejection_gate", "method": "Feature-Ablehnung vor Modellinferenz",
                "probabilities": {}, "features": features, "steps": steps, "explanation": None, "skipped_stages": skipped,
                "elapsed_ms": float((time.perf_counter() - begin) * 1000), "use_priors": bool(use_priors),
                "limitations": "Ablehnung ist eine günstige Domänenprüfung, keine validierte universelle Rauscherkennung."}
    if model_path:
        directory = Path(model_path)
        directory = directory if directory.is_dir() else directory.parent
        metadata = load_model_metadata(directory)
        required = metadata["segment_length"]
        if len(x) < required:
            steps.append({"stage": "Segmentprüfung", "decision": "unbekannt", "reason": f"{len(x)} von {required} benötigten Samples; keine Modellinferenz mit verkürztem Zeitkontext."})
            return {"label": "unbekannt", "confidence": 0., "confidence_kind": "insufficient_segment", "method": metadata["topology_id"],
                    "probabilities": {}, "features": features, "steps": steps, "explanation": None, "skipped_stages": ["saved_model_inference"],
                    "elapsed_ms": float((time.perf_counter() - begin) * 1000), "use_priors": bool(use_priors),
                    "limitations": "Mehr zusammenhängende IQ-Samples entsprechend der gespeicherten Segmentlänge erforderlich."}
        signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in directory.iterdir() if p.name in ("model.json", "weights.pt", "weights.npz", "normalization.npz"))
        manifest, predict = _cached_inference(str(directory.resolve()), tuple(sorted(signature)))
        classes = manifest["classes"]
        length = int(manifest.get("segment_length", len(x)))
        segment = x[:length]
        representation = np.stack([_vector_from_features(features) if manifest["input"] == "features" and len(segment) == len(x)
                                   else _representation(segment, sample_rate, manifest["input"])])
        probabilities = predict(representation)[0]
        threshold = float(manifest.get("confidence_threshold", .6))
        method = manifest["topology_id"]
        steps.append({"stage": "Gespeichertes Modell", "decision": method, "reason": "Geladene Topologie, trainierte Gewichte und gespeicherte Trainingsnormalisierung; keine RF-Frequenz als Eingabe."})
        trained_rates = manifest.get("sample_rates", [manifest.get("sample_rate")])
        if sample_rate not in trained_rates:
            steps.append({"stage": "Gültigkeitsbereich", "decision": "Abtastrate abweichend", "reason": "Die Trainingsraten enthalten diese Abtastrate nicht; Generalisierung muss separat geprüft werden."})
        if explain or manifest["family"] == "feature_lda":
            order = np.argsort(probabilities)
            explanation = predict.explain(representation, int(order[-1]), int(order[-2]))
            if manifest["family"] == "feature_lda":
                contributions = explanation["contributions"][:3]
                reason = "; ".join(f"{item['feature']}: {item['contribution']:+.2f}" for item in contributions)
            else:
                reason = "Lokale Gradientenbeiträge im Eingaberaum wurden berechnet; sie zeigen Sensitivität, keine Kausalität."
            steps.append({"stage": "Modelleinfluss", "decision": explanation["method"], "reason": reason})
    else:
        # Explainable starting point; scores are heuristic, never presented as calibrated likelihood.
        phase = features["phase_std"]
        cv = features["amplitude_cv"]
        logit = 3.0 * np.log10(max(phase, 1e-5) / .05) - 5.0 * (cv - .1)
        probabilities = _softmax([-logit, logit])
        threshold = .65
        method = "Feature-Heuristik (untrainiert)"
        steps.append({"stage": "Hüllkurve und Phase", "decision": "AM-/FM-Merkmalsvergleich", "reason": f"Hüllkurven-CV {cv:.3f}; um Frequenzversatz bereinigte Phasenstreuung {phase:.3f} rad. AM ändert primär Amplitude, FM primär Phase."})
    maximum = float(np.max(probabilities))
    label = classes[int(np.argmax(probabilities))]
    if features["rms_dbfs"] < -140:
        label, maximum = "kein Signal", 0.
        reason = "Digitaler Pegel unter der Mindestgrenze; keine verlässliche Modulationsaussage."
    elif (not model_path or classes == CLASSES) and features["spectral_flatness"] > .50 and features["phase_dispersion"] > .65 and features["envelope_correlation"] < .1:
        label = "unbekannt"
        reason = "Hohe spektrale Flachheit: rauschartig oder außerhalb der geprüften AM-/FM-Domäne."
    elif maximum < threshold:
        label = "unbekannt"
        reason = f"Modellscore {maximum:.3f} unterschreitet Ablehnungsgrenze {threshold:.2f}."
    else:
        reason = "Klassenscore überschreitet die Ablehnungsgrenze; offene Klassen sind weiterhin möglich."
    steps.append({"stage": "Entscheidung mit Ablehnung", "decision": label, "reason": reason})
    if use_priors and center_frequency_hz is not None:
        hint = "Kein vordefinierter Rundfunk-/Flugfunkhinweis"
        if 87.5e6 <= center_frequency_hz <= 108e6:
            hint = "UKW-Rundfunkbereich: WFM plausibel"
        elif 118e6 <= center_frequency_hz <= 136.975e6:
            hint = "VHF-Flugfunkbereich: konventionelles AM plausibel"
        steps.append({"stage": "Optionales Vorwissen", "decision": hint, "reason": "Unbestätigter Bandplanhinweis; verändert weder RF-Merkmale noch Modellscore und ist kein Ground-Truth-Label."})
    return {"label": label, "confidence": maximum, "confidence_kind": "uncalibrated_model_score" if model_path else "heuristic_score",
            "method": method, "probabilities": dict(zip(classes, map(float, probabilities))), "features": features, "steps": steps, "explanation": explanation,
            "elapsed_ms": float((time.perf_counter() - begin) * 1000), "use_priors": bool(use_priors),
            "limitations": "Heuristische Ablehnung ersetzt keinen validierten Open-Set-Klassifikator; Scores gelten nur innerhalb der geprüften Trainingsdomäne."}

"""Validated, portable topology proposals; cost estimates are qualitative, not benchmarks."""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

_CANDIDATES: list[dict] = []


def _add(identifier, name, family, input_kind, width, depth, rationale, limitations,
         complexity="low", origin="Eigenvorschlag", **parameters):
    _CANDIDATES.append({
        "id": identifier, "name": name, "family": family, "input": input_kind,
        "description": rationale, "parameters": {"width": width, "depth": depth, **parameters},
        "layers": _layers(family, width, depth, parameters), "complexity": complexity,
        "recommended_for": ["laptop", "workstation"] if complexity == "low" else ["workstation", "laptop-inference"],
        "rationale": rationale, "limitations": limitations, "origin": origin,
        "evaluation": "Vorschlag; keine empirische Rangfolge. Messwerte entstehen erst durch Experimente.",
    })


def _layers(family, width, depth, p):
    if family == "feature_lda":
        return ["10 normierte Signalmerkmale", "Shrinkage-LDA", "C Klassen aus dem Datensatz + Ablehnung"]
    if family == "feature_mlp":
        return ["10 normierte Signalmerkmale"] + [f"Linear {width} + GELU"] * depth + ["Linear C (Datensatzklassen)"]
    if family in ("fb_lstm", "gru"):
        return ["16 Hamming-Fenster × 10 Spektralmerkmale", f"{depth} × {'LSTM' if family == 'fb_lstm' else 'GRU'} {width} (Dropout 0,1 zwischen Stufen)", "Letzter Zeitschritt", f"Linear {width} + ReLU", "Linear C (Datensatzklassen)"]
    if family == "spectrogram_cnn":
        return ["Log-Leistungsspektrogramm 32 × 32"] + [f"Conv2D {width * 2**min(i,2)} + BN + ReLU" + (" + MaxPool2D 2" if i < 3 or i == 4 else "") for i in range(depth)] + ["Global Average Pool", "Dropout 0,2", f"Linear {width} + ReLU", "Linear C (Datensatzklassen)"]
    if family == "transformer":
        return ["32 IQ-Patches", f"Linear {width}", f"{depth} × Transformer ({p.get('heads', 4)} Heads)", "Mean Pool", "Linear C (Datensatzklassen)"]
    label = {"iq_cnn": "Conv1D", "five_cnn": "Conv1D", "resnet": "Residual Conv1D", "tcn": "Dilated Conv1D"}.get(family, family)
    details = "causal + BN + ReLU + AveragePool 2" if family == "five_cnn" else "causal/dilated + BN + GELU + AveragePool 2" if family == "tcn" else " + AveragePool 2"
    return ["IQ: 2 × 1024"] + [f"{label} {width * 2**min(i,2)} {details}" for i in range(depth)] + ["Dropout 0,5" if family == "five_cnn" else "Dropout 0,1", "Global Average Pool", "Linear C (Datensatzklassen)"]


_add("feature-lda", "Feature-LDA · Referenz", "feature_lda", "features", 10, 1,
     "Schnelle, überprüfbare Referenz mit Hüllkurven-, Phasen- und Spektralmerkmalen.",
     "Lineare Klassengrenze; Störungen und unbekannte Modulationen brauchen unabhängige Prüfung.")
for width, depth in [(16, 1), (32, 2), (64, 2), (128, 3)]:
    _add(f"feature-mlp-{width}", f"Feature-MLP · {width}", "feature_mlp", "features", width, depth,
         "Kleine nichtlineare Merkmalskombination; guter Ausgangspunkt auf CPU.", "Die handdefinierten Merkmale begrenzen die Informationsmenge.")
for width, depth, kernel in [(16, 2, 7), (32, 3, 7), (48, 3, 11), (64, 4, 7), (96, 4, 15)]:
    _add(f"iq-cnn-{width}", f"IQ-CNN · {width}", "iq_cnn", "iq", width, depth,
         "Lernt lokale IQ-Muster ohne feste Spektraldarstellung.", "Empfindlich gegen Hardware-/SNR-Domainwechsel; Eingabe wird auf 1024 Punkte resampelt.",
         "low" if width < 64 else "medium", kernel=kernel)
for width, depth in [(8, 3), (16, 5), (32, 5), (48, 5)]:
    identifier = "spectrogram-cnn-small" if width == 8 else f"spectrogram-cnn-{width}"
    _add(identifier, f"2D-CNN · Spektrogramm {width}", "spectrogram_cnn", "spectrogram", width, depth,
         "Zeit-/Frequenzstruktur eignet sich für schmalbandige und burstförmige Muster.",
         "STFT-Aufwand und Verlust der absoluten IQ-Phase; keine exakte Reproduktion des Promotionsnetzes.",
         "low" if width <= 16 else "medium", "Dissertations-inspirierte RF-Adaption: 2D-CNN")
for width in [16, 32, 48, 64]:
    _add(f"five-cnn-{width}", f"5-CNN · {width}", "five_cnn", "iq", width, 5,
         "Fünf Faltungsstufen kombinieren kurze und längere lokale Signalstrukturen.",
         "Audioarchitektur wird für zweikanalige IQ-Daten angepasst; keine behauptete Originalreproduktion.",
         "medium", "Dissertations-inspirierte RF-Adaption: 5-CNN", kernel=7)
for width, depth in [(16, 2), (32, 2), (64, 2), (96, 2)]:
    _add(f"fb-lstm-{width}", f"FB-LSTM · {width}", "fb_lstm", "feature_sequence", width, depth,
         "Zehn Spektralmerkmale je Hamming-Fenster plus zwei LSTM-Stufen; RF-Adaption des Audioansatzes aus Kap. 6.1.1.",
         "RF-IQ statt Audio; zwei Klassen statt RUL-Regression. Dimensionsgrößen sind eigene Vorschläge, keine Originalreproduktion.",
         "low" if width <= 32 else "medium", "Dissertations-inspirierte RF-Adaption: FB-LSTM")
for width, depth in [(24, 1), (48, 1), (96, 2)]:
    _add(f"feature-gru-{width}", f"Feature-GRU · {width}", "gru", "feature_sequence", width, depth,
         "Geringerer rekurrenter Aufwand als LSTM bei zeitlichen Fenstermerkmalen.", "Kann lange Abhängigkeiten übersehen.", "low" if width < 96 else "medium")
for width, depth in [(16, 3), (32, 4), (64, 5)]:
    _add(f"iq-resnet-{width}", f"IQ-ResNet · {width}", "resnet", "iq", width, depth,
         "Residualpfade erleichtern tiefere IQ-Faltungsmodelle.", "Mehr Parameter und Datenbedarf als kleine CNNs.", "medium", kernel=7)
for width, depth in [(16, 3), (32, 4), (64, 5)]:
    _add(f"iq-tcn-{width}", f"IQ-TCN · {width}", "tcn", "iq", width, depth,
         "Dilatierte Faltungen verbreitern das zeitliche Sichtfeld ohne Rekurrenz.", "Kurze resampelte Fenster begrenzen die maximale Symbolhistorie.", "medium", kernel=5)
for width, depth in [(32, 1), (64, 2), (96, 3)]:
    _add(f"iq-transformer-{width}", f"IQ-Patch-Transformer · {width}", "transformer", "iq", width, depth,
         "Globale Beziehungen zwischen IQ-Patches als Vergleich zu lokalen CNNs.", "Hoher Datenbedarf; für AM/FM wahrscheinlich unnötig komplex.", "high", heads=4)


def list_topologies() -> list[dict]:
    torch_available = importlib.util.find_spec("torch") is not None
    result = copy.deepcopy(_CANDIDATES)
    for item in result:
        item["status"] = "available" if item["family"] == "feature_lda" or torch_available else "requires_torch"
        item["trainable"] = item["status"] == "available"
    return result


def validate_topology(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Topologie muss ein JSON-Objekt sein.")
    base_id = config.get("base_id") or config.get("id") or config.get("topology_id")
    base = next((copy.deepcopy(x) for x in _CANDIDATES if x["id"] == base_id), None)
    if base is None and config.get("family"):
        base = next((copy.deepcopy(x) for x in _CANDIDATES if x["family"] == config["family"]), None)
    if base is None:
        raise ValueError("Unbekannte Topologie oder Modellfamilie.")
    identifier = config.get("id", base["id"])
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", identifier):
        raise ValueError("Topologie-ID darf nur Buchstaben, Ziffern, Bindestriche und Unterstriche enthalten.")
    parameters = {**base["parameters"], **config.get("parameters", {})}
    for key in parameters:
        if key not in ("width", "depth", "kernel", "heads"):
            raise ValueError(f"Unbekannter Topologieparameter: {key}")
        if type(parameters[key]) is not int:
            raise ValueError("Topologieparameter müssen ganze Zahlen sein.")
    if not 4 <= parameters["width"] <= 256 or not 1 <= parameters["depth"] <= 6:
        raise ValueError("Breite 4–256 und Tiefe 1–6 erforderlich.")
    if "kernel" in parameters and (not 3 <= parameters["kernel"] <= 31 or parameters["kernel"] % 2 == 0):
        raise ValueError("Kernelgröße muss ungerade zwischen 3 und 31 liegen.")
    if base["family"] == "five_cnn" and parameters["depth"] != 5:
        raise ValueError("5-CNN hat genau fünf Faltungsstufen.")
    if base["family"] == "transformer":
        heads = parameters.get("heads", 4)
        if heads not in (1, 2, 4, 8) or parameters["width"] % heads:
            raise ValueError("Transformer-Breite muss durch 1, 2, 4 oder 8 Heads teilbar sein.")
    base.update(id=identifier, name=str(config.get("name", base["name"]))[:120], parameters=parameters)
    base["layers"] = _layers(base["family"], parameters["width"], parameters["depth"], parameters)
    if identifier not in {x["id"] for x in _CANDIDATES}:
        base["origin"] = f"Benutzeradaption von {base_id or base['family']}"
    return base


def save_topology(config: dict, models_dir) -> dict:
    topology = validate_topology(config)
    target = Path(models_dir) / "topologies"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{topology['id']}.json"
    path.write_text(json.dumps(topology, ensure_ascii=False, indent=2), encoding="utf-8")
    return topology

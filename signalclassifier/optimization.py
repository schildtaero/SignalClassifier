"""Bounded, seeded random-search plans without an optimizer dependency.

This module only validates and proposes configurations. Training, validation,
cooperative deadlines and final-test isolation belong to the caller.
"""
from __future__ import annotations

import itertools
import math

import numpy as np


def _number(value, name, minimum, maximum, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)
            or (integer and type(value) is not int)):
        qualifier = "ganze Zahl" if integer else "Zahl"
        raise ValueError(f"{name}: {qualifier} zwischen {minimum} und {maximum} erforderlich.")
    return int(value) if integer else float(value)


def _choices(value, name, minimum, maximum):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError(f"{name}: 1–8 ganzzahlige Auswahlwerte erforderlich.")
    return sorted({_number(item, name, minimum, maximum, integer=True) for item in value})


def normalize_optimization(config: dict) -> dict:
    """Return validated public settings while leaving the input unchanged."""
    if not isinstance(config, dict):
        raise ValueError("Experimentkonfiguration muss ein JSON-Objekt sein.")
    supplied = config.get("optimization", {})
    if not isinstance(supplied, dict):
        raise ValueError("optimization muss ein JSON-Objekt sein.")
    allowed = {"enabled", "method", "max_trials", "timeout_seconds", "objective", "search_space"}
    unknown = set(supplied) - allowed
    if unknown:
        raise ValueError(f"Unbekannte Optimierungsfelder: {', '.join(sorted(map(str, unknown)))}")
    enabled = supplied.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("optimization.enabled muss true oder false sein.")
    if enabled and config.get("evaluation_mode", "independent") == "training_only":
        raise ValueError("Automatische Optimierung benötigt unabhängige Validierung; „Nur trainieren“ wird dafür nicht unterstützt.")
    method = supplied.get("method", "random")
    if method != "random":
        raise ValueError("Als automatische Suchmethode wird nur random (Random Search) unterstützt.")
    objective = supplied.get("objective", "score")
    if objective not in ("score", "macro_f1", "balanced_accuracy"):
        raise ValueError("Optimierungsziel muss score, macro_f1 oder balanced_accuracy sein.")
    trials = _number(supplied.get("max_trials", 12), "optimization.max_trials", 1, 50, integer=True)
    timeout = _number(supplied.get("timeout_seconds", 300), "optimization.timeout_seconds", 0, 86400)
    search = supplied.get("search_space", {})
    if not isinstance(search, dict):
        raise ValueError("optimization.search_space muss ein JSON-Objekt sein.")
    search_allowed = {"segment_lengths", "learning_rate_min", "learning_rate_max", "batch_sizes", "epochs"}
    unknown_search = set(search) - search_allowed
    if unknown_search:
        raise ValueError(f"Unbekannte Suchraumfelder: {', '.join(sorted(map(str, unknown_search)))}")
    base_length = _number(config.get("segment_length", 4096), "segment_length", 256, 65536, integer=True)
    default_lengths = sorted({max(256, base_length // 2), base_length, min(65536, base_length * 2)})
    lengths = _choices(search.get("segment_lengths", default_lengths), "optimization.search_space.segment_lengths", 256, 65536)
    batches = _choices(search.get("batch_sizes", [16, 32, 64]), "optimization.search_space.batch_sizes", 4, 256)
    epochs = _choices(search.get("epochs", [5, 10, 20]), "optimization.search_space.epochs", 1, 100)
    lr_min = _number(search.get("learning_rate_min", 1e-5), "optimization.search_space.learning_rate_min", 1e-5, .1)
    lr_max = _number(search.get("learning_rate_max", 1e-2), "optimization.search_space.learning_rate_max", 1e-5, .1)
    if lr_min > lr_max:
        raise ValueError("Die kleinste Lernrate darf die größte Lernrate nicht überschreiten.")
    return {"enabled": enabled, "method": method, "max_trials": trials, "timeout_seconds": timeout,
            "objective": objective, "search_space": {"segment_lengths": lengths,
                "learning_rate_min": lr_min, "learning_rate_max": lr_max,
                "batch_sizes": batches, "epochs": epochs}}


def sample_configurations(settings: dict, seed: int, family: str = "neural") -> list[dict]:
    """Propose unique discrete/log-uniform configurations in a reproducible order.

    Feature-LDA has no learning-rate, batch-size or epoch hyperparameters. Its
    effective search space consists solely of the available segment lengths.
    Finite spaces are exhausted without duplicate training runs.
    """
    normalized = normalize_optimization({"optimization": settings})
    _number(seed, "seed", 0, 2**31 - 1, integer=True)
    if not isinstance(family, str) or not family:
        raise ValueError("Eine Modellfamilie ist für die Suchplanung erforderlich.")
    if not normalized["enabled"]:
        return []
    rng = np.random.default_rng(seed)
    space = normalized["search_space"]
    limit = normalized["max_trials"]
    low, high = space["learning_rate_min"], space["learning_rate_max"]
    def candidate(length, learning_rate, batch_size, epochs):
        return {"segment_length": int(length), "learning_rate": float(learning_rate),
                "batch_size": int(batch_size), "epochs": int(epochs)}
    if family == "feature_lda":
        lengths = rng.permutation(space["segment_lengths"])[:limit]
        return [candidate(length, low, space["batch_sizes"][0], space["epochs"][0]) for length in lengths]
    if low == high:
        combinations = list(itertools.product(space["segment_lengths"], space["batch_sizes"], space["epochs"]))
        order = rng.permutation(len(combinations))[:limit]
        return [candidate(combinations[index][0], low, combinations[index][1], combinations[index][2]) for index in order]
    results, seen = [], set()
    # Narrow floating-point intervals may contain very few representable rates.
    # A bounded attempt count prevents an accidental infinite uniqueness loop.
    for _ in range(max(1000, limit * 100)):
        rate = min(high, max(low, float(math.exp(rng.uniform(math.log(low), math.log(high))))))
        proposal = candidate(rng.choice(space["segment_lengths"]), rate,
                             rng.choice(space["batch_sizes"]), rng.choice(space["epochs"]))
        key = tuple(proposal.values())
        if key not in seen:
            seen.add(key)
            results.append(proposal)
            if len(results) == limit:
                break
    return results

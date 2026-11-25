"""Explicit, hardware-free preparation of unvalidated recording training.

This preset represents every selected file with bounded IQ windows. It never
turns correlated generator fixtures into independent validation evidence.
"""
from __future__ import annotations

import math

from .sigmf import recording_info

IQ_BUDGET_SAMPLES = 16_777_216


def requested_settings(payload):
    if payload.get("source") != "recordings":
        raise ValueError("Für den Trainingsplan zuerst bestätigte Aufnahmen auswählen.")

    def choice(plural, singular, default, low, high, integer=False, minimum=False):
        values = payload.get(plural, [payload.get(singular, default)])
        if not isinstance(values, list) or not values:
            raise ValueError(f"{plural}: mindestens einen Wert angeben.")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or not low <= value <= high
               or (integer and int(value) != value) for value in values):
            raise ValueError(f"{plural}: gültige Werte zwischen {low} und {high} angeben.")
        value = min(values) if minimum else values[0]
        return int(value) if integer else float(value)

    length = choice("segment_lengths", "segment_length", 4096, 256, 65536, True, True)
    rate = choice("learning_rates", "learning_rate", .001, .00001, .1)
    batch = choice("batch_sizes", "batch_size", 32, 4, 256, True)
    return {"evaluation_mode": "training_only", "optimization": {"enabled": False},
            "respect_generation_splits": False, "use_all_recordings": True,
            "segment_length": length, "segment_lengths": [length],
            "learning_rate": rate, "learning_rates": [rate],
            "batch_size": batch, "batch_sizes": [batch]}


def fit_settings(config, settings):
    """Inspect file metadata only; retain all IDs and never pad or delete IQ."""
    entries = config["recordings"]
    if not entries:
        raise ValueError("Für den Trainingsplan fehlen bestätigte Aufnahmen.")
    available = []
    for entry in entries:
        count = recording_info(entry.get("metadata_path") or entry["path"])["sample_count"]
        if count < 256:
            raise ValueError(f"{entry['label']}, Aufnahme {entry.get('id', '?')}: {count} Samples; mindestens 256 nötig. "
                             "Diese Aufnahme ausdrücklich abwählen oder eine längere Aufnahme verwenden. Alle ausgewählten Dateien bleiben sonst erhalten.")
        available.append(count)
    maximum = min(min(available), IQ_BUDGET_SAMPLES // len(entries),
                  64_000_000 // config.get("samples_per_class", 64), 65536)
    previous = settings["segment_length"]
    length = previous if previous <= maximum else 1 << (maximum.bit_length() - 1)
    if length < 256:
        raise ValueError("Nicht für jede Aufnahme ist ein Segment innerhalb des IQ-Budgets möglich. Weniger Aufnahmen auswählen.")
    return {**settings, "segment_length": length, "segment_lengths": [length]}, {
        "selected_recordings": len(entries), "segment_length": length,
        "previous_segment_length": previous,
    }

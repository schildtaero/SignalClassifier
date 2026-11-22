"""Self-contained experiment evidence for later human/ChatGPT PDF reports.

This module only reads its supplied data and writes one atomic ZIP. Paths inside
reports are provenance strings: IQ data, model weights and arbitrary local files
are never opened or added to the archive.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import numbers
import os
import re
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _jsonable(value, path, warnings, ancestors=None):
    ancestors = set() if ancestors is None else ancestors
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            warnings.append({"json_pointer": path or "/", "original_value": str(number), "replacement": None})
            return None
        return number
    if isinstance(value, Path):
        return str(value)
    # NumPy values can occur in developer/partial reports; no NumPy dependency is
    # needed merely to export an otherwise ordinary Python/JSON experiment.
    if type(value).__module__.split(".")[0] == "numpy":
        return _jsonable(value.tolist() if hasattr(value, "tolist") else value.item(), path, warnings, ancestors)
    if isinstance(value, (dict, list, tuple)):
        if id(value) in ancestors:
            raise ValueError(f"Zyklische Berichtsdaten bei {path or '/'}")
        nested = ancestors | {id(value)}
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Berichtsobjekte benötigen eindeutige JSON-Textschlüssel")
            return {key: _jsonable(item, f"{path}/{key.replace('~', '~0').replace('/', '~1')}", warnings, nested)
                    for key, item in value.items()}
        return [_jsonable(item, f"{path}/{index}", warnings, nested) for index, item in enumerate(value)]
    raise ValueError(f"Nicht unterstützter Berichtswert bei {path or '/'}: {type(value).__name__}")


def _json(value, *, pretty=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))


def _object(value):
    return value if isinstance(value, dict) else {}


def _array(value):
    return value if isinstance(value, list) else []


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return _json(value) if isinstance(value, (dict, list)) else str(value)


def _csv(rows, columns):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key)) for key in columns})
    return output.getvalue().encode("utf-8")


def _escape(value):
    return html.escape(str(value), quote=True)


def _md(value):
    text = html.escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_[\]|!])", r"\\\1", text)


def _literal(value):
    text = str(value)
    length = max([2] + [len(match.group()) for match in re.finditer(r"`+", text)]) + 1
    fence = "`" * length
    return f"{fence}text\n{text}\n{fence}"


def _fmt(value, digits=3, percent=False):
    number = _number(value)
    if number is None:
        return "—"
    return f"{number * 100:.1f} %" if percent else f"{number:.{digits}f}"


def _views(experiment):
    """Prefer the comparison's full per-model reports over its selected mirror."""
    result = []

    def add(report, prefix, partial=False):
        report = _object(report)
        optimization_only = report.get("enabled") is True and isinstance(report.get("trials"), list) and "optimization" not in report
        optimization_pointer = None
        if optimization_only:
            optimization_pointer = prefix
            classes = next((_object(trial.get("report")).get("classes") for trial in report["trials"]
                            if isinstance(trial, dict) and _object(trial.get("report")).get("classes")), [])
            report = {"optimization": report, "topology_id": report.get("topology_id"), "classes": classes,
                      "config": _object(experiment.get("config"))}
        comparison = [entry for entry in _array(report.get("comparison")) if isinstance(entry, dict)]
        entries = comparison or ([report] if report else [])
        for index, entry in enumerate(entries):
            child = _object(entry.get("report")) or entry
            topology = _object(child.get("topology"))
            topology_id = entry.get("topology_id") or topology.get("id") or child.get("topology_id")
            entry_pointer = f"{prefix}/comparison/{index}" if comparison else prefix
            report_pointer = entry_pointer + "/report" if _object(entry.get("report")) else entry_pointer
            result.append({"scope": entry_pointer,
                           "report_pointer": report_pointer,
                           "optimization_pointer": optimization_pointer or report_pointer + "/optimization",
                           "name": entry.get("name") or topology.get("name") or topology_id or "Unbenanntes Modell",
                           "topology_id": topology_id, "entry": entry, "report": child, "partial": partial})

    add(experiment.get("report"), "/report")
    partials = experiment.get("partial_reports")
    if isinstance(partials, dict):
        partials = [{"key": key, "report": value} for key, value in partials.items()]
    pending = list(enumerate(_array(partials)))
    # A completed per-topology report already contains its optimizer evidence.
    # Keep duplicate log snapshots in raw JSON, not as extra apparent models.
    pending.sort(key=lambda pair: bool(_object(_object(pair[1]).get("report")).get("enabled") is True))
    for index, item in pending:
        raw = _object(item).get("report") or item
        raw = _object(raw)
        if "error" in raw and not any(key in raw for key in ("optimization", "candidates", "metrics", "trials", "comparison")):
            continue
        identifier = raw.get("topology_id") or _object(raw.get("topology")).get("id")
        if identifier and any(view["topology_id"] == identifier for view in result):
            continue
        add(raw, f"/partial_reports/{index}" + ("/report" if _object(_object(item).get("report")) else ""), True)
    # The function also accepts a bare ML report for CLI/developer use.
    if not result and any(key in experiment for key in ("metrics", "candidates", "comparison", "optimization")):
        add(experiment, "")
    return result


def _quality(view, experiment):
    report, entry = view["report"], view["entry"]
    configs = [_object(experiment.get("config")), _object(report.get("config"))]
    allowed = (not any(c.get("evaluation_mode") == "training_only" for c in configs)
               and not any(obj.get("evaluation_available") is False or obj.get("validation_status") == "unvalidated"
                           for obj in (experiment, entry, report)))
    validation = _object(entry.get("validation")) or _object(report.get("validation"))
    evidence = (any(obj.get("evaluation_available") is True for obj in (entry, report))
                or any(_number(validation.get(key)) is not None for key in ("accuracy", "balanced_accuracy", "macro_f1"))
                or any(any(_number(_object(trial.get("validation")).get(key)) is not None for key in ("accuracy", "balanced_accuracy", "macro_f1"))
                       for trial in _array(_object(report.get("optimization")).get("trials")) if isinstance(trial, dict)))
    return allowed and evidence


def _comparison_rows(views, experiment):
    rows = []
    for view in views:
        report, entry = view["report"], view["entry"]
        quality = _quality(view, experiment)
        metrics = _object(entry.get("metrics")) or _object(report.get("metrics"))
        validation = _object(entry.get("validation")) or _object(report.get("validation"))
        hardware = _object(report.get("hardware"))
        score = _object(entry.get("score")) or _object(report.get("score"))
        rows.append({"scope": view["scope"], "name": view["name"], "topology_id": view["topology_id"],
                     "model_id": entry.get("model_id"), "partial": view["partial"],
                     "status": experiment.get("status"), "source": report.get("source") or _object(experiment.get("config")).get("source"),
                     "synthetic": report.get("synthetic"), "evaluation_available": quality,
                     "evaluation_scope": report.get("evaluation_scope"),
                     "validation_accuracy": validation.get("accuracy") if quality else None,
                     "validation_balanced_accuracy": validation.get("balanced_accuracy") if quality else None,
                     "validation_macro_f1": validation.get("macro_f1") if quality else None,
                     "test_accuracy": metrics.get("accuracy") if quality and report.get("evaluation_scope") != "validation_only" else None,
                     "test_balanced_accuracy": metrics.get("balanced_accuracy") if quality and report.get("evaluation_scope") != "validation_only" else None,
                     "test_macro_f1": metrics.get("macro_f1") if quality and report.get("evaluation_scope") != "validation_only" else None,
                     "score_total": score.get("total") if quality and score.get("quality_score_available") is not False else None,
                     "score_rank": entry.get("score_rank") if quality else None,
                     "accuracy_rank": entry.get("accuracy_rank") if quality else None,
                     "eligible_for_live": score.get("eligible_for_live") if quality else None,
                     **{key: metrics.get(key) for key in ("training_seconds", "preprocessing_seconds", "latency_ms_p50", "latency_ms_p95",
                                                        "real_time_factor", "real_time_factor_p95", "model_bytes", "parameter_count")},
                     "training_device": hardware.get("training_device") or hardware.get("device"),
                     "inference_device": hardware.get("inference_device"),
                     "hardware_json": hardware, "config_json": report.get("config"), "score_json": score,
                     "optimization_json": report.get("optimization"), "raw_json": entry})
    return rows


def _tables(views, experiment):
    candidates, trials, epochs, grid, matrices = [], [], [], [], []

    def add_grid(view, entries, split, pointer, candidate=None):
        if not _quality(view, experiment):
            return
        for index, item in enumerate(_array(entries)):
            if not isinstance(item, dict):
                continue
            grid.append({"scope": view["scope"], "topology_id": view["topology_id"], "name": view["name"],
                         "split": split, "json_pointer": f"{pointer}/{index}", "candidate": item.get("candidate", candidate),
                         **{key: item.get(key) for key in ("segment_length", "snr_db", "jitter", "accuracy", "balanced_accuracy", "macro_f1", "count")},
                         "raw_json": item})
            add_matrix(view, item.get("confusion_matrix"), split, f"{pointer}/{index}/confusion_matrix", item.get("candidate", candidate),
                       snr=item.get("snr_db"), jitter=item.get("jitter"))

    def add_matrix(view, matrix, split, pointer, candidate=None, snr=None, jitter=None):
        if not _quality(view, experiment):
            return
        classes = _array(view["report"].get("classes"))
        for actual, values in enumerate(_array(matrix)):
            for predicted, count in enumerate(_array(values)):
                matrices.append({"scope": view["scope"], "topology_id": view["topology_id"], "name": view["name"],
                                 "split": split, "candidate": candidate, "snr_db": snr, "jitter": jitter,
                                 "json_pointer": pointer, "actual_index": actual, "predicted_index": predicted,
                                 "actual_class": classes[actual] if actual < len(classes) else str(actual),
                                 "predicted_class": classes[predicted] if predicted < len(classes) else str(predicted), "count": count})

    for view in views:
        report, scope = view["report"], view["report_pointer"]
        base = {"scope": view["scope"], "topology_id": view["topology_id"], "name": view["name"]}
        optimization = _object(report.get("optimization"))
        optimizer_trials = [x for x in _array(optimization.get("trials")) if isinstance(x, dict)]
        for index, trial in enumerate(optimizer_trials):
            config = _object(trial.get("config"))
            trial_pointer = f"{view['optimization_pointer']}/trials/{index}"
            trials.append({**base, "json_pointer": trial_pointer,
                           "trial": trial.get("trial", index + 1), "status": trial.get("status"),
                           "objective": optimization.get("objective"), "objective_value": trial.get("objective_value") if _quality(view, experiment) else None,
                           **{key: config.get(key) for key in ("segment_length", "learning_rate", "batch_size", "epochs")},
                           "elapsed_seconds": trial.get("elapsed_seconds"), "error": trial.get("error"),
                           "validation_json": trial.get("validation"), "score_json": trial.get("score"),
                           "metrics_json": trial.get("metrics"), "config_json": config, "raw_json": trial})
        items = [(x, f"{scope}/candidates/{index}", x.get("candidate", index + 1))
                 for index, x in enumerate(_array(report.get("candidates"))) if isinstance(x, dict)]
        # Partial optimizer logs may exist before the aggregate candidate list.
        if not items:
            for trial_index, trial in enumerate(optimizer_trials):
                child = _object(trial.get("report"))
                for candidate_index, candidate in enumerate(_array(child.get("candidates"))):
                    if isinstance(candidate, dict):
                        items.append((candidate, f"{view['optimization_pointer']}/trials/{trial_index}/report/candidates/{candidate_index}",
                                      trial.get("trial", candidate.get("candidate"))))
        history_candidates = set()
        for candidate, pointer, identifier in items:
            candidates.append({**base, "candidate": identifier, "json_pointer": pointer,
                               **{key: candidate.get(key) for key in ("segment_length", "learning_rate", "batch_size", "training_seconds", "model_bytes")},
                               "validation_json": candidate.get("validation"), "score_json": candidate.get("score"),
                               "optimization_json": candidate.get("optimization"), "raw_json": candidate})
            matching_trial = next((trial for trial in optimizer_trials if trial.get("trial") == identifier), {})
            history = _array(matching_trial.get("epoch_history")) or _array(candidate.get("history"))
            history_pointer = (f"{view['optimization_pointer']}/trials/{optimizer_trials.index(matching_trial)}/epoch_history"
                               if matching_trial.get("epoch_history") else pointer + "/history")
            for epoch in history:
                if isinstance(epoch, dict):
                    epochs.append({**base, "candidate": identifier, "epoch": epoch.get("epoch"),
                                   "epoch_count": epoch.get("epoch_count"), "trial_status": matching_trial.get("status"),
                                   "training_loss": epoch.get("training_loss"),
                                   "validation_loss": epoch.get("validation_loss") if _quality(view, experiment) else None,
                                   "json_pointer": history_pointer, "raw_json": epoch})
                    history_candidates.add(_json(identifier))
            add_grid(view, candidate.get("validation_trials"), "validation", pointer + "/validation_trials", identifier)
            add_matrix(view, _object(candidate.get("validation")).get("confusion_matrix"), "validation", pointer + "/validation/confusion_matrix", identifier)
        for trial_index, trial in enumerate(optimizer_trials):
            identifier = trial.get("trial", trial_index + 1)
            if _json(identifier) in history_candidates:
                continue
            child = _object(trial.get("report"))
            history = _array(trial.get("epoch_history")) or _array(child.get("history"))
            for epoch in history:
                if isinstance(epoch, dict):
                    epochs.append({**base, "candidate": identifier, "epoch": epoch.get("epoch"),
                                   "epoch_count": epoch.get("epoch_count"), "trial_status": trial.get("status"),
                                   "training_loss": epoch.get("training_loss"),
                                   "validation_loss": epoch.get("validation_loss") if _quality(view, experiment) else None,
                                   "json_pointer": f"{view['optimization_pointer']}/trials/{trial_index}", "raw_json": epoch})
        if not items and not optimizer_trials:
            for epoch in _array(report.get("history")):
                if isinstance(epoch, dict):
                    epochs.append({**base, "candidate": report.get("selected_candidate"), "epoch": epoch.get("epoch"),
                                   "epoch_count": epoch.get("epoch_count"), "trial_status": None,
                                   "training_loss": epoch.get("training_loss"),
                                   "validation_loss": epoch.get("validation_loss") if _quality(view, experiment) else None,
                                   "json_pointer": scope + "/history", "raw_json": epoch})
        if report.get("validation_trials") not in [candidate.get("validation_trials") for candidate, _, _ in items]:
            add_grid(view, report.get("validation_trials"), "validation", scope + "/validation_trials", report.get("selected_candidate"))
        if report.get("evaluation_scope") != "validation_only":
            add_grid(view, report.get("trials"), "test", scope + "/trials")
            add_matrix(view, report.get("confusion_matrix"), "test", scope + "/confusion_matrix", report.get("selected_candidate"))
        if not items:
            add_matrix(view, _object(report.get("validation")).get("confusion_matrix"), "validation", scope + "/validation/confusion_matrix", report.get("selected_candidate"))
    return {"candidates": candidates, "optimization_trials": trials, "epoch_history": epochs,
            "snr_jitter": grid, "confusion_matrices": matrices}


def _text(x, y, value, size=14, fill="#23323d", anchor="start"):
    return (f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}">'
            f'{_escape(value)}</text>')


def _svg(title, width, height, elements, description=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img"><title>{_escape(title)}</title>'
            f'<desc>{_escape(description)}</desc><rect width="100%" height="100%" fill="#ffffff"/>'
            '<g font-family="Arial,Helvetica,sans-serif">' + _text(24, 34, title, 20)
            + "".join(elements) + '</g></svg>\n').encode("utf-8")


def _short(value, length=35):
    text = str(value)
    return text if len(text) <= length else text[:length - 1] + "…"


def _comparison_plot(rows):
    valid = lambda value: _number(value) is not None and 0 <= value <= 1
    if not any(valid(row.get(key)) for row in rows for key in ("validation_balanced_accuracy", "test_balanced_accuracy")):
        return None
    width, left, extent = 1060, 300, 620
    height = 120 + 58 * len(rows)
    elements = [_text(24, 58, "Balanced Accuracy · Reihenfolge des gespeicherten Berichts, keine neue Auswahl", 14)]
    for tick in range(6):
        x = left + extent * tick / 5
        elements += [f'<path d="M{x:.3f},86 V{height - 35}" stroke="#e1e7eb"/>',
                     _text(x, 80, f"{tick * 20} %", 12, anchor="middle")]
    for index, row in enumerate(rows):
        y = 109 + 58 * index
        elements.append(_text(24, y + 10, _short(row["name"]), 14))
        found = False
        for offset, key, color in ((0, "validation_balanced_accuracy", "#157f8b"), (20, "test_balanced_accuracy", "#b97620")):
            value = row.get(key)
            if valid(value):
                found = True
                elements.append(f'<rect x="{left}" y="{y + offset}" width="{extent * value:.3f}" height="14" fill="{color}"/>')
                elements.append(_text(left + extent * value + 7, y + offset + 12, f"{value * 100:.1f} %", 12))
        if not found:
            elements.append(_text(left, y + 10, "keine unabhängigen Qualitätswerte", 14, "#637583"))
    elements += [_text(300, height - 10, "Blau: Validierung · Ocker: abschließender Test", 13)]
    return _svg("Modellvergleich: Validierung und Test getrennt", width, height, elements)


def _timing_plot(rows):
    values = [(row, _number(row.get("latency_ms_p95"))) for row in rows]
    values = [(row, value) for row, value in values if value is not None and value >= 0]
    if not values:
        return None
    maximum = max(1e-12, max(value for _, value in values))
    height = 100 + len(values) * 38
    elements = [_text(24, 58, "Gemeldete p95-Segmentlatenz; Hardware und Messumfang siehe CSV/JSON. Keine Hardwareumrechnung.", 13)]
    for index, (row, value) in enumerate(values):
        y = 83 + index * 38
        elements += [_text(24, y + 13, _short(row["name"])),
                     f'<rect x="300" y="{y}" width="{600 * value / maximum:.3f}" height="19" fill="#405b99"/>',
                     _text(306 + 600 * value / maximum, y + 14, f"{value:.4g} ms", 13)]
    return _svg("Modellvergleich: gemessene Segmentlatenz", 1060, height, elements)


def _snr_plot(view, rows):
    selected = view["report"].get("selected_candidate")
    hpo = _object(view["report"].get("optimization")).get("enabled") is True
    if hpo and selected is None:
        return None  # No chosen candidate yet; do not merge different trials.
    curves = {}
    for row in rows:
        if row["scope"] != view["scope"]:
            continue
        if selected is not None and row.get("candidate") not in (None, selected) and not (hpo and row["split"] == "test"):
            continue
        snr, jitter, value = _number(row.get("snr_db")), _number(row.get("jitter")), _number(row.get("balanced_accuracy"))
        if snr is None or jitter is None or value is None or not 0 <= value <= 1:
            continue
        curves.setdefault((row["split"], jitter), []).append((snr, value))
    if not curves:
        return None
    xmin = min(x for points in curves.values() for x, _ in points)
    xmax = max(x for points in curves.values() for x, _ in points)
    if xmin == xmax:
        xmin, xmax = xmin - 1, xmax + 1
    left, top, w, h = 85, 90, 770, 290
    legend_rows = (len(curves) + 1) // 2
    height = 455 + legend_rows * 22
    elements = [_text(24, 58, _short(view["name"], 95), 15)]
    for index in range(6):
        x = left + w * index / 5
        y = top + h * index / 5
        elements += [f'<path d="M{x:.3f},{top} V{top + h}" stroke="#e1e7eb"/>',
                     f'<path d="M{left},{y:.3f} H{left + w}" stroke="#e1e7eb"/>',
                     _text(x, top + h + 22, f"{xmin + (xmax - xmin) * index / 5:.3g}", 12, anchor="middle"),
                     _text(left - 10, y + 4, f"{100 - index * 20} %", 12, anchor="end")]
    elements += [_text(left + w / 2, top + h + 45, "SNR-Konfiguration / dB", anchor="middle"),
                 _text(24, height - 10, "Linien verbinden gemessene Rasterpunkte; Jitter in Sampleeinheiten. Keine Zwischenmessungen.", 12)]
    colors = ["#087e8b", "#b66a14", "#6648a0", "#9d3555", "#3d783d", "#2768a0", "#884a22", "#4a6a60"]
    for index, ((split, jitter), points) in enumerate(curves.items()):
        color = colors[index % len(colors)]
        dash = ' stroke-dasharray="6 4"' if split == "test" else ""
        coordinates = [(left + w * (x - xmin) / (xmax - xmin), top + h * (1 - y)) for x, y in sorted(points)]
        elements.append(f'<polyline points="{" ".join(f"{x:.3f},{y:.3f}" for x, y in coordinates)}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
        elements += [f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3.5" fill="{color}"/>' for x, y in coordinates]
        lx, ly = 85 + index % 2 * 410, 440 + index // 2 * 22
        elements += [f'<path d="M{lx},{ly - 4} h25" stroke="{color}" stroke-width="2"{dash}/>',
                     _text(lx + 32, ly, f"{'Test' if split == 'test' else 'Validierung'} · Jitter {jitter:g}", 13)]
    return _svg("SNR-/Jitter-Robustheit des ausgewählten Kandidaten", 940, height, elements)


def _confusion_plot(view, split):
    report = view["report"]
    if split == "test" and report.get("evaluation_scope") == "validation_only":
        return None
    matrix = report.get("confusion_matrix") if split == "test" else _object(report.get("validation")).get("confusion_matrix")
    if not isinstance(matrix, list) or not matrix or len(matrix) > 64:
        return None
    size = len(matrix)
    if any(not isinstance(row, list) or len(row) != size or any(_number(value) is None or value < 0 for value in row) for row in matrix):
        return None
    classes = _array(report.get("classes"))
    if len(classes) != size:
        classes = [str(index) for index in range(size)]
    cell = 62 if size <= 8 else 34
    left, top = 215, 130
    width, height = left + size * cell + 35, top + size * cell + 50
    maximum = max(1, max(max(row) for row in matrix))
    elements = [_text(24, 58, _short(view["name"], 80), 14),
                _text(left, 86, "Spalten: vorhergesagte Klasse · Zeilen: tatsächliche Klasse", 13)]
    for index, label in enumerate(classes):
        elements += [_text(left + index * cell + cell / 2, top - 12, _short(label, 9 if size > 8 else 13), 12, anchor="middle"),
                     _text(left - 10, top + index * cell + cell / 2 + 4, _short(label, 24), 13, anchor="end")]
    for row, values in enumerate(matrix):
        for column, count in enumerate(values):
            intensity = count / maximum
            color = f"#{round(242 - 200 * intensity):02x}{round(246 - 118 * intensity):02x}{round(250 - 110 * intensity):02x}"
            x, y = left + column * cell, top + row * cell
            elements += [f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{color}" stroke="#ffffff">'
                         f'<title>{_escape(classes[row])} → {_escape(classes[column])}: {count:g}</title></rect>',
                         _text(x + cell / 2, y + cell / 2 + 5, f"{count:g}", 14, "#ffffff" if intensity > .65 else "#23323d", "middle")]
    return _svg(f"Verwechslungsmatrix: {'Test' if split == 'test' else 'Validierung'} · Rohzählungen", width, height, elements)


COMPARISON_COLUMNS = ["scope", "name", "topology_id", "model_id", "partial", "status", "source", "synthetic",
    "evaluation_available", "evaluation_scope", "validation_accuracy", "validation_balanced_accuracy", "validation_macro_f1",
    "test_accuracy", "test_balanced_accuracy", "test_macro_f1", "score_total", "score_rank", "accuracy_rank", "eligible_for_live",
    "training_seconds", "preprocessing_seconds", "latency_ms_p50", "latency_ms_p95", "real_time_factor", "real_time_factor_p95",
    "model_bytes", "parameter_count", "training_device", "inference_device", "config_json", "hardware_json", "score_json",
    "optimization_json", "raw_json"]
TABLE_COLUMNS = {
    "candidates": ["scope", "topology_id", "name", "candidate", "json_pointer", "segment_length", "learning_rate", "batch_size",
                   "training_seconds", "model_bytes", "validation_json", "score_json", "optimization_json", "raw_json"],
    "optimization_trials": ["scope", "topology_id", "name", "trial", "status", "json_pointer", "objective", "objective_value",
                            "segment_length", "learning_rate", "batch_size", "epochs", "elapsed_seconds", "error",
                            "validation_json", "score_json", "metrics_json", "config_json", "raw_json"],
    "epoch_history": ["scope", "topology_id", "name", "candidate", "trial_status", "epoch", "epoch_count", "training_loss", "validation_loss", "json_pointer", "raw_json"],
    "snr_jitter": ["scope", "topology_id", "name", "split", "candidate", "segment_length", "snr_db", "jitter",
                   "accuracy", "balanced_accuracy", "macro_f1", "count", "json_pointer", "raw_json"],
    "confusion_matrices": ["scope", "topology_id", "name", "split", "candidate", "snr_db", "jitter", "json_pointer",
                           "actual_index", "predicted_index", "actual_class", "predicted_class", "count"],
}


def _generic_csv(items):
    rows = [{**item, "raw_json": item} if isinstance(item, dict) else {"value": item, "raw_json": item} for item in items]
    columns = sorted({key for row in rows for key in row if key != "raw_json"}) + ["raw_json"]
    return _csv(rows, columns)


def _resource_csv(events):
    rows = []
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "resources":
            continue
        data = _object(event.get("data"))
        cpu, gpu = _object(data.get("cpu")), _object(data.get("gpu"))
        for device in _array(gpu.get("devices")) or [{}]:
            device = _object(device)
            rows.append({"timestamp_utc": data.get("sampled_at", event.get("timestamp_utc")),
                "elapsed_seconds": event.get("elapsed_seconds"), "cpu_system_percent": cpu.get("system_percent"),
                "cpu_app_percent": cpu.get("process_percent"), "cpu_app_system_percent": cpu.get("app_system_percent"),
                "cpu_app_scope": cpu.get("process_percent_scope"), "process_count": cpu.get("process_count"),
                "gpu_index": device.get("index"), "gpu_name": device.get("name"),
                "gpu_utilization_percent": device.get("utilization_percent"), "vram_used_bytes": device.get("memory_used_bytes"),
                "vram_total_bytes": device.get("memory_total_bytes"), "gpu_provider": gpu.get("provider"),
                "gpu_scope": gpu.get("scope"), "gpu_reason": gpu.get("reason"), "raw_json": event})
    return _csv(rows, ["timestamp_utc", "elapsed_seconds", "cpu_system_percent", "cpu_app_percent", "cpu_app_system_percent",
        "cpu_app_scope", "process_count", "gpu_index", "gpu_name", "gpu_utilization_percent", "vram_used_bytes", "vram_total_bytes",
        "gpu_provider", "gpu_scope", "gpu_reason", "raw_json"])


def _phase_csv(views):
    rows = []
    def add(view, holder, scope, trial=None):
        phases = _object(holder.get("phase_timings")) or _object(_object(holder.get("metrics")).get("phase_timings"))
        if not phases:
            phases = _object(_object(holder.get("report")).get("phase_timings"))
        for name, value in phases.items():
            if name.endswith("_seconds") and isinstance(value, (int, float)) and not isinstance(value, bool):
                rows.append({"topology_id": view["topology_id"], "model_name": view["name"], "scope": scope,
                             "trial": trial, "phase": name, "seconds": value,
                             "timing_scope": phases.get("scope", holder.get("phase_timing_scope") or _object(holder.get("report")).get("phase_timing_scope")), "raw_json": phases})
    for view in views:
        report = view["report"]
        add(view, report, "selected_model")
        for trial in _array(_object(report.get("optimization")).get("trials")):
            if isinstance(trial, dict):
                add(view, trial, "optimization_trial", trial.get("trial"))
    return _csv(rows, ["topology_id", "model_name", "scope", "trial", "phase", "seconds", "timing_scope", "raw_json"])


def _markdown(experiment, views, comparison, tables, inventory, events, charts, warnings):
    config = _object(experiment.get("config"))
    report = _object(experiment.get("report")) or (views[0]["report"] if views else {})
    name = experiment.get("name") or "SignalClassifier-Experiment"
    status = experiment.get("status", "nicht angegeben")
    synthetic = config.get("source") == "synthetic" or report.get("synthetic") is True or report.get("source") == "synthetic"
    training_only = config.get("evaluation_mode") == "training_only" or report.get("evaluation_available") is False
    lines = [f"# {_md(name)}", "", f"Experiment-ID: `{_md(experiment.get('id', 'unbekannt'))}` · Status: **{_md(status)}**", "",
             f"Erstellt: {_md(experiment.get('created_at', 'unbekannt'))} · Beendet: {_md(experiment.get('finished_at', 'unbekannt'))}", "",
             "## Aussagegrenzen", ""]
    if synthetic:
        lines.append("- **Synthetischer Softwareversuch:** Die Ergebnisse belegen keine reale HF-Leistung oder Übertragbarkeit auf andere Empfänger.")
    if training_only:
        lines.append("- **Nur trainiert / unvalidiert:** Genauigkeit, F1, Qualitätsscore und Qualitätsrangfolge sind unbekannt. Trainingsverlust ist keine Generalisierungsprüfung.")
    if config.get("allow_same_session") is True and config.get("source") == "recordings":
        lines.append("- **Gleiche Sitzung ausdrücklich zugelassen:** Training, Validierung und Test trennen ursprüngliche Aufnahmen einschließlich ihrer bekannten Ableitungen. Gemeinsame Sender-, Empfänger- und Kanalbedingungen können die Genauigkeit begünstigen; die Übertragbarkeit auf andere Aufnahmesitzungen ist nicht nachgewiesen. Die gewählte Gruppierung und Zustimmung stehen in Konfiguration und Modellberichten.")
    if status not in ("completed", "nicht angegeben"):
        lines.append("- **Unvollständiger Lauf:** Vorhandene Teilergebnisse und Fehler sind dokumentiert; daraus entsteht kein fingierter Gesamtgewinner.")
    if not any(row["evaluation_available"] for row in comparison):
        lines.append("- Keine nachgewiesene unabhängige Qualitätsauswertung in den exportierten Modellberichten.")
    lines += ["- Modellwahl, Optimierung und Qualitätsranking verwenden Validierung. Der abschließende Test bleibt getrennt und erzeugt hier keine neue Auswahl.",
              "- Laufzeiten gelten für den dokumentierten Rechner und Messpfad. CPU-Inferenz, CUDA-Training, Warmup und erstmaliges Modellladen unterscheiden sich.",
              "- Bei realen Aufnahmen beschreibt das SNR-Raster zusätzlich injiziertes Rauschen relativ zur aufgenommenen Gesamtleistung; es ist kein kalibriertes RF-SNR in dBm.",
              "- `—` beziehungsweise leere CSV-Felder bedeuten unbekannt oder nicht anwendbar, nicht null Leistung.", ""]
    if warnings:
        lines += [f"- {len(warnings)} nicht endliche Zahlen wurden zu JSON-null; Originalmarker und vollständige Pfade stehen in `serialization.json`.", ""]
    legacy_warning = experiment.get("export_snapshot_legacy_warning") or _object(experiment.get("export_snapshot")).get("legacy_warning")
    if legacy_warning:
        lines += ["**Historischer Lauf ohne vollständigen Queue-Snapshot:**", "", _literal(legacy_warning), "",
                  "Aktuelle Labels oder aktuelle Hardwaredaten werden dadurch nicht zu historischen Messwerten erklärt.", ""]
    notes = config.get("experiment_notes", experiment.get("experiment_notes"))
    if notes:
        lines += ["## Versuchsziel und Messaufbau", "", _literal(notes), ""]
    lines += ["## Konfiguration und Datenherkunft", "", "| Parameter | Gespeicherter Wert |", "| --- | --- |"]
    for key in ("source", "evaluation_mode", "seed", "topology_ids", "topology_id", "segment_lengths", "segment_length",
                "learning_rates", "learning_rate", "batch_sizes", "batch_size", "epochs", "snr_db", "jitter", "augmentation", "optimization", "execution"):
        if key in config:
            lines.append(f"| `{key}` | {_md(_cell(config[key]))} |")
    lines += ["", f"Datensatzinventar: **{len(inventory)} Einträge**. Vollständige Labels, Gruppen, SigMF-Metadaten und vorliegende Dateifingerprints: `dataset_inventory.json` und `.csv`.",
              "Die Inventur behauptet keine vollständige IQ-Prüfsumme, wenn lediglich Metadaten-SHA oder Stat-Fingerprints vorliegen.", "",
              "Software-/Abhängigkeitsversionen, Quellprüfsummen, Hardware und Einstellungen bleiben vollständig in `provenance.json` und `report.json` erhalten.", ""]
    if report.get("execution") or report.get("resource_telemetry"):
        lines += ["## Parallelisierung und gemessene Ressourcen", "",
                  "Ausführungsplan, tatsächlich verwendete Worker und Geräte stehen vollständig in `report.json`. Phasenzeiten: `phase_timings.csv`; zeitlich abgetastete CPU-/GPU-Werte: `resource_usage.csv`.",
                  "Parallel summierte Job-/Trainingsarbeit ist keine Experimentwandzeit. CPU-Benchmarks pausieren andere Experimentworker; externe Hostlast ist nicht als ausgeschlossen verifiziert. GPU-Auslastung betrifft das gesamte Gerät und kann Arbeit anderer Programme enthalten. Fehlende Messwerte bleiben unbekannt.", ""]
    if inventory:
        omitted = sum(bool(_object(item).get("metadata_omitted_reason")) for item in inventory)
        if omitted:
            lines += [f"Bei **{omitted} Inventareinträgen** fehlen vollständige SigMF-Metadaten ausdrücklich wegen des gespeicherten Exportbudgets; Gründe stehen in `metadata_omitted_reason`. RF-, Label- und Gruppenfelder bleiben erhalten.", ""]
        counts = Counter(str(item.get("label") or _object(item.get("annotation")).get("label") or "unbekannt")
                         for item in inventory if isinstance(item, dict))
        lines += ["| Inventarlabel | Dateien/Einträge |", "| --- | ---: |"]
        lines += [f"| {_md(label)} | {count} |" for label, count in sorted(counts.items())]
        lines.append("")
    lines += ["## Modellvergleich", "", "Testwerte sind ausdrücklich separate Endmessungen; keine Sortierung anhand des Tests.", "",
              "| Modell | Validierung BA | Test BA | Score | p95 / ms | p95-RTF | Modell / Byte |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    if not comparison:
        lines.append("| Keine abgeschlossenen Modellberichte | — | — | — | — | — | — |")
    for row in comparison:
        lines.append(f"| {_md(row['name'])}{' (Teilbericht)' if row['partial'] else ''} | {_fmt(row['validation_balanced_accuracy'], percent=True)} | "
                     f"{_fmt(row['test_balanced_accuracy'], percent=True)} | {_fmt(row['score_total'], 2)} | {_fmt(row['latency_ms_p95'], 4)} | "
                     f"{_fmt(row['real_time_factor_p95'], 4)} | {_fmt(row['model_bytes'], 0)} |")
    lines += ["", "Hardwarekontext, Scorekomponenten/-gründe und alle weiteren Kennzahlen stehen in `model_comparison.csv`; die vollständigen Originalwerte bleiben im JSON.",
              "Scorewerte werden exportiert, nicht neu aus Testdaten berechnet. Im unvalidierten Modus werden vorhandene widersprüchliche Qualitätsfelder nur im Roh-JSON bewahrt.", "",
              "## Optimierung, Training und Robustheit", "",
              f"Exportiert: **{len(tables['candidates'])} Kandidaten**, **{len(tables['optimization_trials'])} Optimierungsversuche**, "
              f"**{len(tables['epoch_history'])} Epocheneinträge**, **{len(tables['snr_jitter'])} SNR-/Jitter-Messpunkte**.", "",
              "Alle Optimierungsversuche einschließlich Fehlern und Timeouts stehen in `optimization_trials.csv`; Suchraum, Budget, Objective, Seed und Auswahl bleiben in den vollständigen Optimierungsobjekten erhalten.",
              "Die Tabellen `epoch_history.csv`, `snr_jitter.csv` und `confusion_matrices.csv` trennen Modell, Kandidat und Validierungs-/Testsplit. Linien in Abbildungen verbinden vorhandene Rasterpunkte; sie sind keine zusätzlichen Messungen.", ""]
    if tables["optimization_trials"]:
        lines += ["| Modell | Trial | Status | Objective (Validierung) | Sekunden |", "| --- | ---: | --- | ---: | ---: |"]
        for row in tables["optimization_trials"][:100]:
            lines.append(f"| {_md(row['name'])} | {_md(row['trial'])} | {_md(row.get('status') or 'unbekannt')} | {_fmt(row.get('objective_value'), 5)} | {_fmt(row.get('elapsed_seconds'))} |")
        if len(tables["optimization_trials"]) > 100:
            lines += ["", "Die Markdown-Tabelle zeigt die ersten 100 Versuche; sämtliche Versuche stehen ungekürzt im CSV/JSON."]
        lines.append("")
    if charts:
        lines += ["## Lokal verfügbare Diagramme", ""]
        for path, title in charts:
            lines += [f"![{title}]({path})", ""]
    lines += ["## Ablauf, Fehler und Abbruch", "", f"**{len(events)} Ereigniseinträge**, vollständig in `events.json` und `events.csv`.", ""]
    if experiment.get("error"):
        lines += ["Gespeicherter Lauf-Fehler:", "", _literal(experiment["error"]), ""]
    partials = experiment.get("partial_reports")
    for item in (partials.values() if isinstance(partials, dict) else _array(partials)):
        if isinstance(item, dict) and item.get("error"):
            lines += [f"Nicht lesbarer Teilbericht: {_md(item.get('path', 'unbekannt'))}", "", _literal(item["error"]), ""]
    for event in events[-20:]:
        item = _object(event)
        message = item.get("message") or item.get("error") or item.get("stage") or _json(event)
        lines.append(f"- {_md(item.get('timestamp') or item.get('time') or item.get('created_at') or '')} {_md(str(message)[:600])}" + (" … [vollständig im Ereignisexport]" if len(str(message)) > 600 else ""))
    lines += ["", "## Weiterverwendung", "", "Für einen PDF-Bericht das vollständige ZIP an ChatGPT übergeben und `README.txt` beachten. "
              "Dieses Paket enthält keine IQ-Dateien oder Modellgewichte; gespeicherte Dateipfade sind Herkunftsangaben.", "",
              "`manifest.json` verzeichnet Dateigrößen und SHA-256 aller Nutzdateien; das Manifest hasht sich nicht selbst.", ""]
    return "\n".join(lines).encode("utf-8")


README = """SignalClassifier – Experimentpaket für einen späteren PDF-Bericht

Dieses ZIP ist eigenständig lesbar. report.md ist der Einstieg, report.json der
vollständige gespeicherte Experimentzustand einschließlich Konfiguration,
Vergleichen, Kandidaten, Optimierung, Teilberichten und Provenienz. JSON wird nicht
auf ausgewählte Felder reduziert. events.json und dataset_inventory.json enthalten
die übergebenen Ablauf- bzw. Inventardaten. CSVs verwenden UTF-8, Dezimalpunkt und
standardkonforme Anführungszeichen. raw_json-Spalten bewahren Zusatzfelder.

Die SVG-Dateien unter plots/ lassen sich ohne Internet in einem Browser öffnen
und in einen PDF-Bericht übernehmen. Es gibt keine externen Schriften/CDNs.
Nicht vorhandene Qualitätsmessungen erzeugen keine erfundenen Diagramme.

Vorschlag für die Anfrage an ChatGPT:
„Erstelle aus diesem Paket einen deutschsprachigen wissenschaftlichen PDF-Bericht.
Lies zuerst report.md, dann report.json, provenance.json, Inventar, Ereignisse und
alle relevanten CSVs. Berücksichtige experiment_notes als Versuchsziel/Messaufbau.
Dokumentiere Datenherkunft, unabhängige Gruppen/Splits, Klassen, Signalkette,
Topologien, Suchraum, Budgets, sämtliche HPO-Versuche einschließlich Timeouts und
Fehlern, Trainingsverläufe, Hardware, Scoreformeln und Messbedingungen. Verwende
vorhandene SVGs oder erstelle klar beschriftete Abbildungen aus den CSVs. Trenne
Validierung und abschließenden Test. Begründe Modellwahl ausschließlich anhand
der gespeicherten Validierungspolitik. Kennzeichne synthetische Daten, unvalidiertes
Training und unvollständige Läufe deutlich. Erfinde keine fehlenden Messungen und
keine HF-, Hardware- oder Generalisierungserfolge. Nenne offene Abnahmefragen.“

Interpretation:
- Qualitätsranking und HPO verwenden Validierung, nicht den abschließenden Test.
- Nur trainierte Modelle haben keinen nachgewiesenen Qualitätsscore.
- Ein fehlgeschlagener/abgebrochener Lauf ist kein abgeschlossener Vergleich.
- Synthetische Erfolge belegen keine reale HF-Leistung.
- SNR auf realen Aufnahmen ist zusätzlich injiziertes Rauschen; der ursprüngliche
  absolute RF-SNR und eine kalibrierte dBm-Empfindlichkeit können unbekannt bleiben.
- Jitter verwendet Sampleeinheiten. Segmentdauer = Samples / tatsächliche Rate.
- Latenz/RTF gelten für den dokumentierten CPU-/GPU-/Warmup-/Batch-/Gate-Pfad.
  Eine Referenznormierung ist keine universelle Hardwareumrechnung.
- Herkunftsgruppen, Labels und mögliche Aufnahmequalitätsfehler berücksichtigen.
- Stat-/Metadatenfingerprints sind keine behauptete vollständige IQ-Prüfsumme.
- configuration_sha256 und inventory_sha256 können kanonische JSON-Payloads
  bezeichnen, nicht die neu formatierten ZIP-Dateibytes. snapshot_hash_format in
  provenance.json beschreibt dies; das ZIP-Manifest hasht die tatsächlichen Bytes.
- Die im Bericht genannten IQ-/Modellpfade sind keine enthaltenen Dateien. Das
  Paket lädt diese Dateien nicht nach und enthält keine Modellgewichte.

Nicht endliche Zahlen sind in gültigem JSON als null gespeichert. serialization.json
enthält ihren Originalmarker und vollständigen JSON-Pointer, damit diese Werte
nicht still verschwinden. Unbekannte Kennzahlen sind keine Nullen.
manifest.json hasht sämtliche Nutzdateien (SHA-256), ausgenommen sich selbst.
Die Prüfsummen prüfen Dateikonsistenz, nicht die Authentizität wissenschaftlicher
Aussagen. Dieses Paket erzeugt selbst noch keine PDF-Datei.
"""


def build_report_bundle(experiment: dict, destination: Path, events: list[dict] | None = None,
                        dataset_inventory: list[dict] | None = None) -> Path:
    """Atomically export supplied evidence, with no IQ/model file access.

    Nonfinite numbers become null plus lossless path/marker diagnostics. All other
    JSON fields are retained, including unknown future optimizer/provenance keys.
    """
    if not isinstance(experiment, dict):
        raise ValueError("Experimentzustand muss ein JSON-Objekt sein")
    if events is not None and not isinstance(events, list):
        raise ValueError("Ereignisse müssen als Liste übergeben werden")
    if dataset_inventory is not None and not isinstance(dataset_inventory, list):
        raise ValueError("Datensatzinventar muss als Liste übergeben werden")
    warnings = []
    state = _jsonable(experiment, "", warnings)
    event_data = _jsonable(events if events is not None else _array(experiment.get("events")), "/events", warnings)
    inventory_source = "argument" if dataset_inventory is not None else "experiment.config.recordings"
    inventory = _jsonable(dataset_inventory if dataset_inventory is not None else _array(_object(experiment.get("config")).get("recordings")),
                          "/dataset_inventory", warnings)
    views = _views(state)
    comparison = _comparison_rows(views, state)
    tables = _tables(views, state)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    entries = []
    charts = []
    handle, temporary_name = tempfile.mkstemp(prefix=".report-bundle-", suffix=".zip.tmp", dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            def put(path, content):
                archive.writestr(path, content)
                entries.append({"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})

            def chart(path, content, title):
                if content:
                    put(path, content)
                    charts.append((path, title))

            put("report.json", (_json(state, pretty=True) + "\n").encode("utf-8"))
            put("provenance.json", (_json(state.get("provenance", {}), pretty=True) + "\n").encode("utf-8"))
            put("dataset_inventory.json", (_json(inventory, pretty=True) + "\n").encode("utf-8"))
            put("dataset_inventory.csv", _generic_csv(inventory))
            put("events.json", (_json(event_data, pretty=True) + "\n").encode("utf-8"))
            put("events.jsonl", "".join(_json(item) + "\n" for item in event_data).encode("utf-8"))
            put("events.csv", _generic_csv(event_data))
            put("resource_usage.csv", _resource_csv(event_data))
            put("phase_timings.csv", _phase_csv(views))
            put("model_comparison.csv", _csv(comparison, COMPARISON_COLUMNS))
            for name, rows in tables.items():
                put(name + ".csv", _csv(rows, TABLE_COLUMNS[name]))
            chart("plots/model_comparison.svg", _comparison_plot(comparison), "Modellqualität: Validierung und Test getrennt")
            chart("plots/model_timing.svg", _timing_plot(comparison), "Gemessene Segmentlatenz")
            for index, view in enumerate(views, 1):
                if not _quality(view, state):
                    continue
                chart(f"plots/snr_jitter_{index:03d}.svg", _snr_plot(view, tables["snr_jitter"]), f"SNR/Jitter – Modell {index}")
                for split, label in (("validation", "Validierung"), ("test", "Test")):
                    chart(f"plots/confusion_{index:03d}_{split}.svg", _confusion_plot(view, split), f"Verwechslungsmatrix – Modell {index}, {label}")
            put("report.md", _markdown(state, views, comparison, tables, inventory, event_data, charts, warnings))
            put("README.txt", README.encode("utf-8"))
            put("serialization.json", (_json({"nonfinite_replacements": warnings,
                                               "policy": "Nonfinite numbers become null; all other JSON fields are retained."}, pretty=True) + "\n").encode("utf-8"))
            manifest = {"schema_version": 1, "format": "signalclassifier-report-bundle", "created_at": created_at,
                        "experiment_id": state.get("id"), "experiment_status": state.get("status"),
                        "dataset_inventory_source": inventory_source, "nonfinite_replacement_count": len(warnings),
                        "files": entries, "manifest_excluded_from_own_hash": True,
                        "contains_iq_or_weights": False, "archive_members": [entry["path"] for entry in entries] + ["manifest.json"]}
            archive.writestr("manifest.json", _json(manifest, pretty=True).encode("utf-8"))
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)

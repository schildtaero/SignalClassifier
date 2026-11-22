"""One bounded display-only producer shared by all spectrum consumers.

Only CaptureManager.display_snapshot is called. The hub never accesses IQ,
models, inference APIs or databases. A slow consumer observes the latest packet
and has no private frame queue.
"""
from __future__ import annotations

import json
import math
import numbers
import threading
import time
import uuid

MAX_PAYLOAD_BYTES = 256 * 1024
_ERROR_BACKOFF_SECONDS = .25


def _finite_copy(value, *, depth=0, nodes=None, ancestors=None):
    """Copy supported display primitives without retaining mutable references."""
    nodes = [32768] if nodes is None else nodes
    ancestors = set() if ancestors is None else ancestors
    nodes[0] -= 1
    if nodes[0] < 0 or depth > 16:
        raise ValueError("Display snapshot exceeds its structural budget")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > 32768:
            raise ValueError("Display text exceeds its size budget")
        return value
    if isinstance(value, numbers.Integral):
        number = int(value)
        if abs(number) > 10**30:
            raise ValueError("Display integer exceeds its size budget")
        return number
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (dict, list, tuple)):
        if id(value) in ancestors:
            raise ValueError("Cyclic display snapshot")
        nested = ancestors | {id(value)}
        if isinstance(value, dict):
            if len(value) > 512 or any(not isinstance(key, str) or len(key) > 128 for key in value):
                raise ValueError("Display mapping exceeds its key budget")
            return {key: _finite_copy(item, depth=depth + 1, nodes=nodes, ancestors=nested)
                    for key, item in value.items()}
        if len(value) > 4096:
            raise ValueError("Display array exceeds its size budget")
        return [_finite_copy(item, depth=depth + 1, nodes=nodes, ancestors=nested) for item in value]
    raise ValueError("Unsupported display snapshot value")


def _encode_packet(frame, sequence):
    packet = {"type": "spectrum", "sequence": sequence, "frame": _finite_copy(frame)}
    encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Display packet exceeds 256 KiB")
    return encoded


class SpectrumHub:
    def __init__(self, capture, max_clients=4, bins=1024, max_fps=30):
        if not callable(getattr(capture, "display_snapshot", None)):
            raise ValueError("Capture must provide display_snapshot")
        if type(max_clients) is not int or not 1 <= max_clients <= 16:
            raise ValueError("max_clients must be between 1 and 16")
        if type(bins) is not int or bins != 1024:
            raise ValueError("The shared display stream uses exactly 1024 bins")
        if isinstance(max_fps, bool) or not isinstance(max_fps, (int, float)) or not math.isfinite(max_fps) or not 1 <= max_fps <= 30:
            raise ValueError("max_fps must be between 1 and 30")
        self.capture = capture
        self.max_clients, self.bins, self.max_fps = max_clients, bins, float(max_fps)
        self._condition = threading.Condition(threading.RLock())
        self._clients = set()
        self._closed = False
        self._thread = None
        self._latest_sequence = -1
        self._latest_packet = None
        self._refresh_required = True
        self._subscription_epoch = 0
        self._published_frames = 0
        self._last_serialization_ms = None
        self._last_error = None

    @property
    def closed(self):
        with self._condition:
            return self._closed

    def subscribe(self):
        with self._condition:
            if self._closed:
                raise ValueError("Spectrum stream is closed")
            if len(self._clients) >= self.max_clients:
                raise ValueError("Spectrum stream client limit reached")
            if not self._clients:
                # A receiver may have restarted/retuned while the producer was
                # idle. Validate its current revision before exposing old JSON.
                self._subscription_epoch += 1
                self._refresh_required = True
            token = uuid.uuid4().hex
            self._clients.add(token)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="spectrum-display", daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return token

    def unsubscribe(self, token):
        with self._condition:
            self._clients.discard(token)
            self._condition.notify_all()

    def wait_frame(self, token, after_sequence, timeout=1):
        """Return the shared (capture revision, JSON) or None; never enqueue."""
        if type(after_sequence) is not int or after_sequence < -1:
            raise ValueError("after_sequence must be an integer >= -1")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("timeout must be between 0 and 30 seconds")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._closed and isinstance(token, str) and token in self._clients:
                if not self._refresh_required and self._latest_packet is not None and self._latest_sequence > after_sequence:
                    return self._latest_sequence, self._latest_packet
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def stats(self):
        with self._condition:
            return {"clients": len(self._clients), "published_frames": self._published_frames,
                    "last_serialization_ms": self._last_serialization_ms, "last_error": self._last_error,
                    "latest_sequence": self._latest_sequence,
                    "running": bool(self._thread and self._thread.is_alive())}

    def _run(self):
        next_poll = 0.
        while True:
            with self._condition:
                while True:
                    if self._closed:
                        return
                    if not self._clients:
                        self._condition.wait()
                        continue
                    remaining = next_poll - time.monotonic()
                    if remaining > 0:
                        self._condition.wait(remaining)
                        continue
                    revision = self._latest_sequence
                    refresh = self._refresh_required
                    epoch = self._subscription_epoch
                    break
            delay = 1 / self.max_fps
            try:
                frame = self.capture.display_snapshot(max_bins=self.bins, since_frame_id=None if refresh or revision < 0 else revision)
                if frame is not None:
                    if not isinstance(frame, dict) or type(frame.get("frame_id")) is not int or frame["frame_id"] < 0:
                        raise ValueError("Display snapshot lacks a nonnegative integer frame_id")
                    frame_id = frame["frame_id"]
                    with self._condition:
                        if self._closed:
                            return
                        current = bool(self._clients) and epoch == self._subscription_epoch
                        if current and refresh and frame_id == revision and self._latest_packet is not None:
                            # The capture freshly confirmed the same immutable
                            # revision. Reuse its already encoded packet.
                            self._refresh_required = False
                            self._last_error = None
                            self._condition.notify_all()
                    if frame_id > revision:
                        with self._condition:
                            # An already-running snapshot call may finish after
                            # disconnect. Do not serialize/publish without clients.
                            if self._closed:
                                return
                            active = bool(self._clients) and epoch == self._subscription_epoch
                        if active:
                            started = time.perf_counter()
                            encoded = _encode_packet(frame, frame_id)
                            elapsed = (time.perf_counter() - started) * 1000
                            with self._condition:
                                if self._closed:
                                    return
                                if self._clients and epoch == self._subscription_epoch:
                                    self._latest_sequence, self._latest_packet = frame_id, encoded
                                    self._refresh_required = False
                                    self._published_frames += 1
                                    self._last_serialization_ms = elapsed
                                    self._last_error = None
                                    self._condition.notify_all()
            except Exception as exc:
                delay = max(delay, _ERROR_BACKOFF_SECONDS)
                with self._condition:
                    self._last_error = f"{type(exc).__name__}: display snapshot unavailable or outside stream limits"
                    self._condition.notify_all()
            next_poll = time.monotonic() + delay

    def close(self):
        with self._condition:
            self._closed = True
            self._clients.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.)

"""Parse, filter, classify, record, and snapshot thread-safe IMU session state.

The acquisition worker supplies raw samples. Display signals pass through a
time-adjusted low-pass filter and deadzones before status and move detection;
recordings retain the original samples. Avatar progress represents counted moves.
"""

from collections import deque
import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import math
from pathlib import Path
import threading
import time


# Acceleration uses m/s², orientation uses degrees, and gyro uses degrees/second.
# Detection thresholds can also be changed in the sidebar.
GRAVITY_MS2 = 9.80665
SAMPLE_HZ = 100
HISTORY_SECONDS = 30
CHART_SECONDS = 10
HISTORY_MAX_SAMPLES = 3200
# Low-pass coefficients at 100 Hz; smaller values respond more slowly.
# MotionFilter adjusts them for the actual interval between samples.
ORIENTATION_SMOOTHING_ALPHA = 0.14
GYRO_SMOOTHING_ALPHA = 0.14
MOVEMENT_SMOOTHING_ALPHA = 0.10
ROLL_DEADZONE_DEG = 3.0
PITCH_DEADZONE_DEG = 3.0
GYRO_DEADZONE_DPS = 5.0
MOVEMENT_DEADZONE = 0.12  # m/s², not Joules
STATE_ENTER_MS = 200
STATE_EXIT_MS = 400
MOVE_GYRO_THRESHOLD = 20.0
MOVE_ENERGY_THRESHOLD = 0.6
MOVE_START_CONFIRM_MS = 200
MOVE_END_CONFIRM_MS = 400
MAX_SAMPLE_GAP_SECONDS = 1.0
STALE_SECONDS = 2.0
VISUAL_STEP_PERCENT = 4
YAW_LIMIT_DEG = 40.0
YAW_DECAY_SECONDS = 0.8
CSV_COLUMNS = (
    "timestamp",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "roll",
    "pitch",
    "accelerationMagnitude",
    "gyroMagnitude",
    "movementEnergy",
    "state",
    "sampleHz",
)
RECORDING_COLUMNS = ("received_at_utc", "source", *CSV_COLUMNS)
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


@dataclass(frozen=True)
class DetectionConfig:
    """Immutable thresholds shared by status classification and move detection."""

    energy_threshold: float = MOVE_ENERGY_THRESHOLD  # |accelerationMagnitude - g|, m/s²
    gyro_threshold: float = MOVE_GYRO_THRESHOLD  # magnitude, °/s
    start_seconds: float = MOVE_START_CONFIRM_MS / 1000
    end_seconds: float = MOVE_END_CONFIRM_MS / 1000
    rest_ratio: float = 0.65  # hysteresis: BOTH channels must fall below this ratio
    rotation_threshold: float = 30.0  # |gz| for the directional status / avatar
    lean_threshold: float = 12.0


@dataclass(frozen=True)
class SensorSample:
    """One firmware CSV row, retaining its wire field names and physical units.

    Acceleration fields and movementEnergy use m/s²; gyro fields use degrees/s;
    roll/pitch use degrees; sampleHz is the firmware's reported sampling rate.
    """

    timestamp: int  # ESP32 millis(), NOT a Unix timestamp
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    roll: float
    pitch: float
    accelerationMagnitude: float
    gyroMagnitude: float
    movementEnergy: float
    state: str
    sampleHz: float


class MotionFilter:
    """One filter per session, before deadzones, status and move detection."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset filter memory to a level sensor resting under gravity."""
        # Start at rest so the first noisy sample cannot become an instant pose.
        self.values = dict.fromkeys(
            (
                "roll",
                "pitch",
                "gx",
                "gy",
                "gz",
                "gyroMagnitude",
                "ax",
                "ay",
                "az",
                "accelerationMagnitude",
                "movementEnergy",
            ),
            0.0,
        )
        self.values["az"] = self.values["accelerationMagnitude"] = GRAVITY_MS2

    def update(self, sample: SensorSample, dt: float) -> SensorSample:
        """Smooth each channel using its elapsed time in seconds."""
        for field, previous in self.values.items():
            if field in ("roll", "pitch"):
                base_alpha = ORIENTATION_SMOOTHING_ALPHA
            elif field in ("gx", "gy", "gz", "gyroMagnitude"):
                base_alpha = GYRO_SMOOTHING_ALPHA
            else:
                base_alpha = MOVEMENT_SMOOTHING_ALPHA
            # Preserve the nominal 100 Hz response when sample intervals vary.
            alpha = 1 - (1 - base_alpha) ** (dt * SAMPLE_HZ)
            difference = getattr(sample, field) - previous
            if field == "roll":
                # Follow the short arc across +180/-180 instead of crossing zero.
                difference = (difference + 180) % 360 - 180
            value = previous + alpha * difference
            self.values[field] = (value + 180) % 360 - 180 if field == "roll" else value
        return replace(sample, **self.values)


def apply_deadzones(sample: SensorSample) -> SensorSample:
    """Leave filter memory intact; only suppress the filtered output."""
    limits = {
        "roll": ROLL_DEADZONE_DEG,
        "pitch": PITCH_DEADZONE_DEG,
        "gx": GYRO_DEADZONE_DPS,
        "gy": GYRO_DEADZONE_DPS,
        "gz": GYRO_DEADZONE_DPS,
        "gyroMagnitude": GYRO_DEADZONE_DPS,
        "movementEnergy": MOVEMENT_DEADZONE,
    }
    return replace(
        sample,
        **{
            field: 0.0 if abs(getattr(sample, field)) < limit else getattr(sample, field)
            for field, limit in limits.items()
        },
    )


class MotionStatus:
    """A new label must persist continuously; returning to STILL takes longer."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Discard any pending label transition and return to the rest label."""
        self.status = "STILL"
        self.candidate = self.since = None

    def update(self, sample: SensorSample, sample_time: float, config: DetectionConfig):
        """Debounce the highest-priority matching label on the sample timeline."""
        # Rotation and movement take precedence over an otherwise static lean.
        if abs(sample.gz) > config.rotation_threshold:
            desired = "ROTATING LEFT" if sample.gz > 0 else "ROTATING RIGHT"
        elif sample.gyroMagnitude > config.rotation_threshold:
            desired = "ROTATING"
        elif (
            sample.movementEnergy > config.energy_threshold
            or sample.gyroMagnitude > config.gyro_threshold
        ):
            desired = "MOVING"
        elif abs(sample.roll) > config.lean_threshold:
            desired = "RIGHT LEAN" if sample.roll > 0 else "LEFT LEAN"
        elif abs(sample.pitch) > config.lean_threshold:
            desired = "FORWARD LEAN" if sample.pitch > 0 else "BACKWARD LEAN"
        else:
            desired = "STILL"
        if desired == self.status:
            self.candidate = self.since = None
            return
        if desired != self.candidate:
            self.candidate, self.since = desired, sample_time
        required_ms = STATE_EXIT_MS if desired == "STILL" else STATE_ENTER_MS
        if (sample_time - self.since) * 1000 + 1e-6 >= required_ms:
            self.status = desired
            self.candidate = self.since = None


class CSVParser:
    """Accept the firmware's fixed-order CSV even if its boot header was missed."""

    def __init__(self):
        self.columns = CSV_COLUMNS

    def parse(self, line: str) -> SensorSample | None:
        """Return a valid finite sample, or ignore headers and malformed rows."""
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            return None
        try:
            values = next(csv.reader([line], strict=True))
            normalized = tuple(
                "accelerationMagnitude" if value.strip() == "accMagnitude" else value.strip()
                for value in values
            )
            if "timestamp" in normalized:
                # Reject an invalid header instead of reusing a previous header silently.
                self.columns = ()
                if (
                    len(normalized) == len(CSV_COLUMNS)
                    and set(normalized) == set(CSV_COLUMNS)
                ):
                    self.columns = normalized
                return None
            if not self.columns or len(values) != len(self.columns):
                return None
            row = dict(zip(self.columns, values))
            timestamp = int(row["timestamp"])
            if not 0 <= timestamp <= 0xFFFFFFFF:
                return None
            numbers = {
                key: float(row[key])
                for key in CSV_COLUMNS
                if key not in ("timestamp", "state")
            }
            if not all(math.isfinite(value) for value in numbers.values()):
                return None
            if any(
                numbers[key] < 0
                for key in (
                    "accelerationMagnitude",
                    "gyroMagnitude",
                    "movementEnergy",
                    "sampleHz",
                )
            ):
                return None
            state = row["state"].strip().upper()
            if state not in ("STILL", "MOVING", "ROTATING"):
                return None
            return SensorSample(timestamp=timestamp, state=state, **numbers)
        except (ValueError, KeyError, csv.Error, OverflowError):
            return None


class MoveDetector:
    """REST -> MOVING -> REST with minimum durations and hysteresis."""

    def __init__(self, config: DetectionConfig):
        self.config = config
        self.reset()

    def reset(self):
        """Discard an unfinished move without emitting a completed cycle."""
        # Drop an incomplete cycle on disconnect, clock reset, or a long data gap.
        self.phase = "REST"
        self.candidate = None
        self.quiet_since = None

    @staticmethod
    def _peaks(move, sample):
        """Accumulate raw peaks, retaining the sign of the largest absolute lean."""
        move["peak_acceleration"] = max(move["peak_acceleration"], sample.accelerationMagnitude)
        move["peak_gyro"] = max(move["peak_gyro"], sample.gyroMagnitude)
        move["max_roll"] = max(move["max_roll"], sample.roll, key=abs)
        move["max_pitch"] = max(move["max_pitch"], sample.pitch, key=abs)

    def update(
        self,
        sample: SensorSample,
        sample_time: float,
        *,
        raw_sample: SensorSample | None = None,
    ):
        """Return a move only after confirmed motion followed by confirmed rest."""
        c = self.config
        # Gate using processed signals, but retain source peaks in the move table.
        peaks = raw_sample if raw_sample is not None else sample
        active = (
            sample.movementEnergy > c.energy_threshold
            or sample.gyroMagnitude > c.gyro_threshold
        )
        # Either channel can start motion; both must settle below lower thresholds
        # to end it. This hysteresis prevents chatter near the start thresholds.
        quiet = (
            sample.movementEnergy < c.energy_threshold * c.rest_ratio
            and sample.gyroMagnitude < c.gyro_threshold * c.rest_ratio
        )
        if self.phase == "REST":
            if not active:
                self.candidate = None
                return None
            if self.candidate is None:
                self.candidate = {
                    "start_time": sample_time,
                    "start_timestamp_ms": sample.timestamp,
                    "peak_acceleration": peaks.accelerationMagnitude,
                    "peak_gyro": peaks.gyroMagnitude,
                    "max_roll": peaks.roll,
                    "max_pitch": peaks.pitch,
                }
            self._peaks(self.candidate, peaks)
            if sample_time - self.candidate["start_time"] + 1e-9 >= c.start_seconds:
                self.phase = "MOVING"
            return None

        self._peaks(self.candidate, peaks)
        if not quiet:
            self.quiet_since = None
            return None
        if self.quiet_since is None:
            self.quiet_since = (sample_time, sample.timestamp)
        if sample_time - self.quiet_since[0] + 1e-9 < c.end_seconds:
            return None
        move = dict(self.candidate)
        # End at the beginning of confirmed rest, excluding its confirmation wait.
        move["end_time"], move["end_timestamp_ms"] = self.quiet_since
        move["duration"] = move["end_time"] - move["start_time"]
        self.reset()
        return move


class SensorState:
    """Own one session's processing pipeline, bounded history, and CSV recording.

    The reentrant lock protects ingestion, snapshots, and control changes. It also
    allows lifecycle methods to call one another while already holding the lock.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.config = DetectionConfig()
        self.detector = MoveDetector(self.config)
        self.motion_filter = MotionFilter()
        self.motion_status = MotionStatus()
        self.history = deque(maxlen=HISTORY_MAX_SAMPLES)
        self.arrivals = deque(maxlen=HISTORY_MAX_SAMPLES)
        self.moves = deque(maxlen=5)
        self.latest = None
        self.filtered = self.display = None
        self.last_received = None
        self.last_timestamp = None
        self.sample_time = 0.0
        self.total_samples = 0
        self.generation = 0
        self.move_count = 0
        self.display_roll = self.display_pitch = self.display_gz = self.yaw_angle = 0.0
        self.visual_progress = 0.0
        self.recording_file = None
        self.recording_writer = None
        self.recording_path = None
        self.recording_started = None
        self.recording_duration = 0.0
        self.recorded_samples = 0
        self.recording_error = ""
        self.last_flush = 0.0

    def configure(self, config: DetectionConfig):
        """Apply thresholds and discard candidates measured under the old values."""
        with self.lock:
            if config != self.config:
                self.config = config
                self.detector.config = config
                self.detector.reset()
                self.motion_status.reset()

    def reset_detection(self):
        """Clear pending moves and status transitions while retaining history."""
        with self.lock:
            self.detector.reset()
            self.motion_status.reset()

    def clear(self):
        """Begin a new source session and invalidate existing browser cursors."""
        with self.lock:
            self.stop_recording()
            self.history.clear()
            self.arrivals.clear()
            self.moves.clear()
            self.latest = self.last_received = self.last_timestamp = None
            self.filtered = self.display = None
            self.motion_filter.reset()
            self.motion_status.reset()
            self.sample_time = 0.0
            self.total_samples = self.move_count = 0
            self.generation += 1
            self.display_roll = self.display_pitch = self.display_gz = self.yaw_angle = 0.0
            self.visual_progress = 0.0
            self.detector.reset()

    def ingest(self, sample: SensorSample, source: str, now: float | None = None):
        """Process one raw sample and optionally record it under the state lock.

        Device timestamps drive filtering and detection; the local monotonic clock
        measures freshness, arrival rate, recording duration, and delivery gaps.
        """
        now = time.perf_counter() if now is None else now
        with self.lock:
            dt = 1 / SAMPLE_HZ
            discontinuous = False
            if self.last_timestamp is not None:
                delta_ms = sample.timestamp - self.last_timestamp
                if (
                    delta_ms < 0
                    and self.last_timestamp > 0xF0000000
                    and sample.timestamp < 0x0FFFFFFF
                ):
                    delta_ms += 2**32  # millis() rollover after ~49.7 days
                if delta_ms == 0:
                    # Duplicate timestamps must not advance filters or counters.
                    return
                dt = delta_ms / 1000
                if (
                    dt <= 0
                    or dt > MAX_SAMPLE_GAP_SECONDS
                    or now - self.last_received > MAX_SAMPLE_GAP_SECONDS
                ):
                    # A reset or gap invalidates motion continuity. Resume with a
                    # fresh filter instead of integrating across missing samples.
                    discontinuous = True
                    self.detector.reset()
                    self.motion_filter.reset()
                    self.motion_status.reset()
                    self.yaw_angle = 0.0
                    dt = max(1 / SAMPLE_HZ, now - self.last_received)
            self.sample_time += dt
            self.last_timestamp, self.last_received = sample.timestamp, now
            self.latest = sample
            self.total_samples += 1
            self.arrivals.append(now)
            while self.arrivals and self.arrivals[0] < now - 2:
                self.arrivals.popleft()
            filter_dt = 1 / SAMPLE_HZ if discontinuous else dt
            self.filtered = self.motion_filter.update(sample, filter_dt)
            self.display = apply_deadzones(self.filtered)
            self.motion_status.update(self.display, self.sample_time, self.config)
            row = asdict(self.display)
            row["state"] = self.motion_status.status
            row["time_s"] = self.sample_time
            row["seq"] = self.total_samples
            self.history.append(row)
            while self.history and self.history[0]["time_s"] < self.sample_time - HISTORY_SECONDS:
                self.history.popleft()

            # Bound and decay the visual pose; yaw is a short-lived illustration,
            # not an absolute heading or an estimate of the climber's position.
            self.display_roll = max(-45, min(45, self.display.roll))
            self.display_pitch = max(-45, min(45, self.display.pitch))
            self.display_gz = self.display.gz
            visual_dt = min(dt, 0.1)
            self.yaw_angle *= math.exp(-visual_dt / YAW_DECAY_SECONDS)
            if self.display_gz != 0:
                self.yaw_angle += self.display_gz * visual_dt * 0.65
            self.yaw_angle = max(-YAW_LIMIT_DEG, min(YAW_LIMIT_DEG, self.yaw_angle))

            move = self.detector.update(self.display, self.sample_time, raw_sample=sample)
            if move:
                self.move_count += 1
                self.moves.appendleft({"move": self.move_count, **move})
            # The browser softly approaches this target independently of sensor rate.
            self.visual_progress = min(100, self.move_count * VISUAL_STEP_PERCENT)
            if self.recording_file:
                try:
                    # Record source values before filtering, with UTC arrival time.
                    self.recording_writer.writerow(
                        {
                            "received_at_utc": datetime.now(timezone.utc).isoformat(
                                timespec="milliseconds"
                            ),
                            "source": source,
                            **asdict(sample),
                        }
                    )
                    self.recorded_samples += 1
                    if now - self.last_flush >= 1:
                        # Keep disk writes bounded without flushing every sample.
                        self.recording_file.flush()
                        self.last_flush = now
                except (OSError, ValueError) as exc:
                    self.recording_error = f"Recording beendet: {exc}"
                    self.stop_recording()

    def start_recording(self, directory: Path = RECORDINGS_DIR):
        """Create a unique UTF-8 CSV and publish its handle after header success."""
        with self.lock:
            if self.recording_file:
                return
            self.recording_error = ""
            try:
                directory.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")
                path = directory / f"belaybuddy_session_{stamp}.csv"
                handle = path.open("x", newline="", encoding="utf-8")
                writer = csv.DictWriter(handle, fieldnames=RECORDING_COLUMNS)
                try:
                    writer.writeheader()
                    handle.flush()
                except OSError:
                    handle.close()
                    raise
            except OSError as exc:
                self.recording_error = f"Recording konnte nicht gestartet werden: {exc}"
                return
            self.recording_file, self.recording_writer = handle, writer
            self.recording_path = path
            self.recorded_samples = 0
            self.recording_started = time.perf_counter()
            self.recording_duration = 0.0

    def stop_recording(self):
        """Close and retain the latest recording's path, duration, and counters."""
        with self.lock:
            if self.recording_file:
                self.recording_duration = time.perf_counter() - self.recording_started
                try:
                    self.recording_file.close()
                except OSError as exc:
                    self.recording_error = (
                        f"Recording konnte nicht vollständig gespeichert werden: {exc}"
                    )
                self.recording_file = self.recording_writer = None

    def snapshot(self, now: float | None = None, *, after_sequence: int = 0):
        """Copy UI state and bounded history rows newer than a sequence cursor."""
        now = time.perf_counter() if now is None else now
        with self.lock:
            fresh = self.latest is not None and now - self.last_received < STALE_SECONDS
            arrivals = [t for t in self.arrivals if now - t < 2]
            # Windows monotonic clocks can give several USB samples the same tick.
            arrival_span = arrivals[-1] - arrivals[0] if len(arrivals) > 1 else 0.0
            rate = (len(arrivals) - 1) / arrival_span if arrival_span > 0 and fresh else 0.0
            intensity = 0.0
            if self.display:
                intensity = min(
                    1.0,
                    max(
                        self.display.movementEnergy / 5,
                        self.display.gyroMagnitude / 150,
                    ),
                )
            return {
                "latest": self.latest,
                "fresh": fresh,
                "rate": rate,
                "filtered": asdict(self.filtered) if self.filtered else None,
                "display": asdict(self.display) if self.display else None,
                "status": self.motion_status.status if fresh else "NO DATA",
                "phase": self.detector.phase,
                "total_samples": self.total_samples,
                "move_count": self.move_count,
                "moves": list(self.moves),
                "history": [row for row in self.history if row["seq"] > after_sequence],
                "generation": self.generation,
                "roll": self.display_roll,
                "pitch": self.display_pitch,
                "gz": self.display_gz,
                "yaw": self.yaw_angle,
                "progress": self.visual_progress,
                "intensity": intensity,
                "recording": self.recording_file is not None,
                "recording_path": self.recording_path,
                "recording_seconds": (
                    now - self.recording_started
                    if self.recording_file
                    else self.recording_duration
                ),
                "recorded_samples": self.recorded_samples,
                "recording_error": self.recording_error,
            }

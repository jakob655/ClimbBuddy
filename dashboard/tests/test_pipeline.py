"""Behavioral tests: malformed data, real timings, finite buffers and I/O failures."""

import csv
from dataclasses import asdict, replace
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Resolve dashboard modules independently of the test runner's working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial

from sensor_state import (
    CSV_COLUMNS,
    CSVParser,
    DetectionConfig,
    GRAVITY_MS2,
    HISTORY_MAX_SAMPLES,
    MotionFilter,
    MotionStatus,
    MoveDetector,
    SensorSample,
    SensorState,
    apply_deadzones,
)
from serial_reader import SensorReader
from visualization import render_wall


def sample(timestamp=0, energy=0.0, gz=0.0, roll=0.0, pitch=0.0):
    """Build a firmware-shaped sample with explicit motion on the tested axes."""
    return SensorSample(
        timestamp,
        0.0,
        0.0,
        GRAVITY_MS2 + energy,
        0.0,
        0.0,
        gz,
        roll,
        pitch,
        GRAVITY_MS2 + energy,
        abs(gz),
        energy,
        "MOVING" if energy or gz else "STILL",
        100.0,
    )


def csv_line(value):
    """Serialize in the firmware's canonical field order, without a header."""
    return ",".join(str(asdict(value)[key]) for key in CSV_COLUMNS)


def wait_until(predicate, timeout=3):
    """Wait briefly for background reader work using a monotonic deadline."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class ParserTests(unittest.TestCase):
    """Validate firmware compatibility and recovery from malformed serial input."""

    def test_firmware_contract_and_attach_without_header(self):
        value = CSVParser().parse(
            "12340,0.030,-0.120,9.780,0.40,-0.20,0.10,-0.70,-0.18,9.781,0.46,0.026,STILL,100.0"
        )
        self.assertEqual(value.timestamp, 12340)
        self.assertAlmostEqual(value.accelerationMagnitude, 9.781)

    def test_reordered_header_and_alias(self):
        parser = CSVParser()
        columns = list(reversed(CSV_COLUMNS))
        parser.parse(
            ",".join("accMagnitude" if c == "accelerationMagnitude" else c for c in columns)
        )
        original = sample(20, energy=1.2, gz=-70)
        parsed = parser.parse(",".join(str(asdict(original)[c]) for c in columns))
        self.assertEqual(parsed, original)

    def test_invalid_lines_and_nonfinite_values_are_ignored(self):
        parser = CSVParser()
        for line in (
            "",
            "MPU6050 gefunden",
            "# FEHLER: I2C",
            "--- IMU ---",
            "1,2,3",
            csv_line(sample()) + ",extra",
        ):
            with self.subTest(line=line):
                self.assertIsNone(parser.parse(line))
        for value in (float("nan"), float("inf"), -float("inf")):
            self.assertIsNone(parser.parse(csv_line(replace(sample(), ax=value))))
        self.assertIsNone(parser.parse(csv_line(sample(-1))))
        self.assertIsNone(parser.parse(csv_line(sample(2**32))))
        self.assertIsNone(parser.parse(csv_line(replace(sample(), gyroMagnitude=-1))))
        self.assertIsNone(parser.parse(csv_line(replace(sample(), state="garbage"))))
        self.assertEqual(parser.parse(csv_line(sample(10))), sample(10))

    def test_invalid_header_does_not_corrupt_column_mapping(self):
        parser = CSVParser()
        parser.parse("timestamp,ax")
        self.assertIsNone(parser.parse(csv_line(sample())))
        parser.parse(",".join(CSV_COLUMNS))
        self.assertEqual(parser.parse(csv_line(sample())), sample())


class DetectionTests(unittest.TestCase):
    """Drive movement cycles with a deterministic 100 Hz device and host clock."""

    def setUp(self):
        self.detector = MoveDetector(DetectionConfig())
        self.t = 0
        self.completed = []

    def feed(self, count, **values):
        """Advance both clocks by 10 ms per sample and collect completed cycles."""
        for _ in range(count):
            value = sample(self.t, **values)
            move = self.detector.update(value, self.t / 1000)
            if move:
                self.completed.append(move)
            self.t += 10

    def test_complete_cycle_records_peaks_once(self):
        self.feed(50)
        self.feed(60, energy=2, gz=-80, roll=-30, pitch=20)
        self.feed(60)
        self.assertEqual(len(self.completed), 1)
        move = self.completed[0]
        self.assertAlmostEqual(move["duration"], 0.6)
        self.assertAlmostEqual(move["peak_acceleration"], GRAVITY_MS2 + 2)
        self.assertEqual(move["peak_gyro"], 80)
        self.assertEqual((move["max_roll"], move["max_pitch"]), (-30, 20))

    def test_short_spikes_do_not_count(self):
        for _ in range(10):
            self.feed(14, energy=2)
            self.feed(50)
        self.assertEqual(self.completed, [])

    def test_gyro_only_counts_and_short_rest_does_not_finish(self):
        self.feed(40, gz=60)
        self.feed(39)
        self.assertEqual(self.completed, [])
        self.feed(40, gz=60)
        self.feed(50)
        self.assertEqual(len(self.completed), 1)

    def test_hysteresis_requires_both_channels_quiet(self):
        self.feed(30, energy=1)
        self.feed(100, energy=0.5)  # Below start, above the acceleration rest threshold.
        self.feed(100, gz=15)  # Above the gyroscope rest threshold.
        self.assertEqual(self.completed, [])
        self.feed(50)
        self.assertEqual(len(self.completed), 1)

    def test_held_movement_is_one_cycle(self):
        self.feed(1000, energy=2)
        self.assertEqual(self.completed, [])
        self.feed(100)
        self.assertEqual(len(self.completed), 1)


class MotionFilterTests(unittest.TestCase):
    """Check smoothing, angle wrapping, and independence from sampling frequency."""

    def test_all_motion_channels_are_smoothed_before_deadzones(self):
        motion_filter = MotionFilter()
        raw = replace(sample(energy=2, gz=60, roll=6, pitch=-6), gx=30, gy=-40)
        filtered = motion_filter.update(raw, 0.01)
        display = apply_deadzones(filtered)
        for field in (
            "roll",
            "pitch",
            "gx",
            "gy",
            "gz",
            "gyroMagnitude",
            "movementEnergy",
        ):
            with self.subTest(field=field):
                self.assertGreater(abs(getattr(filtered, field)), 0)
                self.assertLess(abs(getattr(filtered, field)), abs(getattr(raw, field)))
        self.assertEqual((display.roll, display.pitch, display.gx), (0, 0, 0))
        # The deadzone must not erase accumulated low-pass memory.
        for _ in range(100):
            filtered = motion_filter.update(raw, 0.01)
        self.assertAlmostEqual(filtered.roll, 6, places=4)
        self.assertGreater(apply_deadzones(filtered).roll, 5.9)
        self.assertEqual(raw.roll, 6)

    def test_filter_response_depends_on_elapsed_time_not_sample_rate(self):
        results = []
        # Each schedule covers the same second using a different sampling interval.
        for count, dt in ((100, 0.01), (20, 0.05), (10, 0.1)):
            motion_filter = MotionFilter()
            for _ in range(count):
                filtered = motion_filter.update(
                    sample(energy=2, gz=60, roll=30, pitch=-20), dt
                )
            results.append(filtered)
        for field in ("roll", "pitch", "gz", "gyroMagnitude", "movementEnergy"):
            self.assertAlmostEqual(getattr(results[0], field), getattr(results[1], field))
            self.assertAlmostEqual(getattr(results[0], field), getattr(results[2], field))

    def test_roll_wrap_uses_the_short_arc(self):
        motion_filter = MotionFilter()
        for _ in range(100):
            motion_filter.update(sample(roll=179), 0.01)
        for _ in range(25):
            filtered = motion_filter.update(sample(roll=-179), 0.01)
            self.assertGreater(abs(filtered.roll), 178)


class MotionStatusTests(unittest.TestCase):
    """Verify that interrupted status candidates restart their confirmation timers."""

    def test_each_direction_and_activity_requires_continuous_confirmation(self):
        cases = [
            (sample(roll=-20), "LEFT LEAN"),
            (sample(roll=20), "RIGHT LEAN"),
            (sample(pitch=20), "FORWARD LEAN"),
            (sample(pitch=-20), "BACKWARD LEAN"),
            (sample(gz=60), "ROTATING LEFT"),
            (sample(gz=-60), "ROTATING RIGHT"),
            (replace(sample(), gx=60, gyroMagnitude=60), "ROTATING"),
            (sample(energy=2), "MOVING"),
        ]
        for active, expected in cases:
            with self.subTest(status=expected):
                status, config = MotionStatus(), DetectionConfig()
                status.update(active, 0, config)
                status.update(active, 0.199, config)
                self.assertEqual(status.status, "STILL")
                status.update(sample(), 0.2, config)  # Interrupt the pending candidate.
                status.update(active, 0.21, config)
                status.update(active, 0.409, config)
                self.assertEqual(status.status, "STILL")
                status.update(active, 0.41, config)
                self.assertEqual(status.status, expected)

    def test_still_requires_longer_uninterrupted_quiet(self):
        status, config, active = MotionStatus(), DetectionConfig(), sample(energy=2)
        status.update(active, 0, config)
        status.update(active, 0.2, config)
        status.update(sample(), 0.3, config)
        status.update(sample(), 0.699, config)
        self.assertEqual(status.status, "MOVING")
        status.update(active, 0.7, config)
        status.update(sample(), 0.71, config)
        status.update(sample(), 1.109, config)
        self.assertEqual(status.status, "MOVING")
        status.update(sample(), 1.11, config)
        self.assertEqual(status.status, "STILL")


class StateTests(unittest.TestCase):
    """Cover state aggregation, timing discontinuities, bounded history, and recording."""

    def test_small_sensor_noise_cannot_move_avatar_or_change_status_or_count_moves(self):
        state = SensorState()
        for i in range(1000):
            direction = 1 if i % 2 else -1
            state.ingest(
                sample(
                    i * 10,
                    energy=0.08,
                    gz=4 * direction,
                    roll=2 * direction,
                    pitch=-2 * direction,
                ),
                "test",
                now=i / 100,
            )
            snap = state.snapshot(now=i / 100)
            self.assertEqual(snap["status"], "STILL")
            self.assertEqual(
                (snap["roll"], snap["pitch"], snap["gz"], snap["yaw"], snap["intensity"]),
                (0, 0, 0, 0, 0),
            )
        self.assertEqual(snap["move_count"], 0)
        self.assertEqual(snap["phase"], "REST")
        self.assertEqual(snap["display"]["movementEnergy"], 0)
        self.assertAlmostEqual(snap["filtered"]["movementEnergy"], 0.08)
        self.assertEqual(snap["latest"].movementEnergy, 0.08)
        self.assertEqual(snap["history"][-1]["gz"], 0)

    def test_isolated_spikes_do_not_create_status_or_move(self):
        state = SensorState()
        for i in range(500):
            spike = i % 100 == 50
            state.ingest(
                sample(i * 10, energy=5 if spike else 0, gz=200 if spike else 0),
                "test",
                now=i / 100,
            )
            self.assertEqual(state.snapshot(now=i / 100)["status"], "STILL")
        self.assertEqual(state.snapshot(now=5)["move_count"], 0)

    def test_reset_and_gap_discard_filter_and_confirmation_memory(self):
        state = SensorState()
        for i in range(100):
            state.ingest(sample(i * 10, energy=2, gz=60, roll=30), "test", now=i / 100)
        self.assertEqual(state.snapshot(now=1)["status"], "ROTATING LEFT")
        state.ingest(sample(4000), "test", now=4)
        snap = state.snapshot(now=4)
        self.assertEqual(
            (snap["status"], snap["phase"], snap["roll"], snap["gz"], snap["yaw"]),
            ("STILL", "REST", 0, 0, 0),
        )
        self.assertEqual(snap["filtered"]["movementEnergy"], 0)
        state.clear()
        self.assertIsNone(state.snapshot(now=4)["filtered"])
        state.ingest(sample(0, roll=2), "test", now=5)
        self.assertEqual(state.snapshot(now=5)["roll"], 0)

    def test_buffers_and_recent_moves_are_bounded(self):
        state = SensorState()
        for i in range(7000):
            state.ingest(
                sample(i * 10, energy=2 if i % 100 < 40 else 0), "test", now=i / 100
            )
        snap = state.snapshot(now=70)
        self.assertLessEqual(len(snap["history"]), HISTORY_MAX_SAMPLES)
        self.assertLessEqual(
            snap["history"][-1]["time_s"] - snap["history"][0]["time_s"], 30.00001
        )
        self.assertEqual(len(snap["moves"]), 5)
        self.assertEqual(snap["move_count"], 70)
        self.assertLessEqual(snap["progress"], 100)

    def test_clock_reset_and_data_gap_do_not_complete_a_move(self):
        for gap_kind in ("reset", "device_gap", "host_gap"):
            with self.subTest(gap_kind=gap_kind):
                state = SensorState()
                for i in range(50):
                    state.ingest(sample(1000 + i * 10, energy=2), "test", now=i / 100)
                for i in range(60):
                    # Separate device-clock resets/gaps from host reception gaps.
                    timestamp = (
                        i * 10
                        if gap_kind == "reset"
                        else 4000 + i * 10
                        if gap_kind == "device_gap"
                        else 1500 + i * 10
                    )
                    now = (3 if gap_kind == "host_gap" else 0.5) + i / 100
                    state.ingest(sample(timestamp), "test", now=now)
                self.assertEqual(state.snapshot(now=4)["move_count"], 0)

    def test_millis_rollover_preserves_a_valid_cycle(self):
        state = SensorState()
        for i in range(120):
            state.ingest(
                sample((2**32 - 300 + i * 10) % 2**32, energy=2 if i < 60 else 0),
                "test",
                now=i / 100,
            )
        self.assertEqual(state.snapshot(now=1.2)["move_count"], 1)

    def test_smoothing_bounds_and_yaw_decay(self):
        state = SensorState()
        state.ingest(sample(0, gz=150, roll=90, pitch=-90), "test", now=0)
        snap = state.snapshot(now=0)
        self.assertTrue(0 < snap["roll"] < 45)
        for i in range(1, 200):
            state.ingest(sample(i * 10, gz=150), "test", now=i / 100)
        self.assertLessEqual(abs(state.snapshot(now=2)["yaw"]), 40)
        for i in range(200, 700):
            state.ingest(sample(i * 10), "test", now=i / 100)
        self.assertLess(abs(state.snapshot(now=7)["yaw"]), 1)
        self.assertEqual(state.snapshot(now=10)["status"], "NO DATA")
        self.assertEqual(state.snapshot(now=10)["rate"], 0)

    def test_recording_streams_to_csv_and_survives_reset(self):
        state = SensorState()
        with tempfile.TemporaryDirectory() as directory:
            state.start_recording(Path(directory))
            # Record raw samples even when the display suppresses their small motion.
            for i in range(20):
                state.ingest(sample(i * 10, energy=0.08, gz=4, roll=2), "simulation")
            path = state.snapshot()["recording_path"]
            state.stop_recording()
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 20)
            self.assertEqual(rows[0]["source"], "simulation")
            self.assertTrue(rows[0]["received_at_utc"].endswith("+00:00"))
            self.assertEqual(rows[-1]["timestamp"], "190")
            self.assertEqual(float(rows[-1]["roll"]), 2)
            self.assertEqual(float(rows[-1]["gz"]), 4)
            self.assertEqual(float(rows[-1]["movementEnergy"]), 0.08)
            self.assertEqual(state.snapshot()["display"]["gz"], 0)
            state.clear()
            self.assertTrue(path.exists())

    def test_recording_errors_are_reported(self):
        state = SensorState()
        with tempfile.TemporaryDirectory() as directory:
            bad_directory = Path(directory) / "file.txt"
            bad_directory.write_text("existing file")
            state.start_recording(bad_directory)
            self.assertFalse(state.snapshot()["recording"])
            self.assertIn("nicht gestartet", state.snapshot()["recording_error"])


class FakeSerial:
    """Yield supplied USB chunks, then emulate unplugging the serial connection."""

    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False
        self.in_waiting = 1

    def read(self, size):
        chunk = next(self.chunks, None)
        if chunk is None:
            raise serial.SerialException("USB unplugged")
        return chunk

    def close(self):
        self.closed = True


class ReaderTests(unittest.TestCase):
    """Exercise serial framing, diagnostics, simulation, and reader cleanup."""

    def test_fragmented_lines_noise_and_unplug(self):
        state, reader = SensorState(), None
        reader = SensorReader(state)
        first, second = csv_line(sample(10)).encode(), csv_line(sample(20, energy=1)).encode()
        # Include partial frames, an empty read, oversize garbage, and invalid UTF-8.
        fake = FakeSerial(
            [
                b"boot\n",
                first[:15],
                b"",
                first[15:] + b"\n",
                b"x" * 3000,
                b"\n",
                b"\xff\n",
                second + b"\r\n",
            ]
        )
        reader.serial_port, reader.connected = fake, True
        reader._run(False)
        self.assertEqual(state.snapshot()["total_samples"], 2)
        self.assertTrue(fake.closed)
        self.assertFalse(reader.status()["connected"])
        self.assertIn("unterbrochen", reader.status()["error"])

    def test_open_failure_does_not_crash(self):
        reader = SensorReader(SensorState())
        with patch(
            "serial_reader.serial.Serial", side_effect=serial.SerialException("port busy")
        ):
            reader.start_serial("COM999")
        self.assertFalse(reader.status()["connected"])
        self.assertIn("COM999", reader.status()["error"])

    def test_serial_diagnosis_explains_old_firmware_and_sensor_errors(self):
        for raw, expected_format, expected_error in (
            (b"--- IMU ---\nax: 0.03 m/s2\n", "text", ""),
            (b"Active mode: PRESSURE_TEST\nRaw: 20 | Voltage: 0.01 V\n", "pressure", ""),
            (b"# FEHLER: MPU6050/I2C-Lesefehler\n", "", "MPU6050/I2C"),
        ):
            with self.subTest(raw=raw):
                reader = SensorReader(SensorState())
                reader.serial_port = FakeSerial([raw])
                reader._run(False)
                status = reader.status()
                self.assertEqual(status["stream_format"], expected_format)
                self.assertIn(expected_error, status["firmware_error"])
                self.assertEqual(status["received_bytes"], len(raw))
                self.assertGreater(status["received_lines"], 0)
                self.assertTrue(status["recent_lines"])

    def test_simulate_button_uses_detector_and_closes_on_stop(self):
        state = SensorState()
        reader = SensorReader(state)
        try:
            reader.start_simulation()
            self.assertTrue(wait_until(lambda: state.snapshot()["total_samples"] > 5))
            reader.set_simulation(-30, 15, 0, 0)
            reader.simulate_move()
            self.assertTrue(wait_until(lambda: state.snapshot()["move_count"] == 1))
            snap = state.snapshot()
            self.assertLess(snap["roll"], -20)
            self.assertGreater(snap["pitch"], 10)
            self.assertEqual(len(snap["moves"]), 1)
        finally:
            reader.stop()
        self.assertFalse(reader.thread.is_alive())

    def test_idle_client_releases_connection(self):
        state = SensorState()
        reader = SensorReader(state)
        # Age the last client heartbeat without waiting for the real idle timeout.
        reader.last_client_seen = time.perf_counter() - 31
        reader.connected = True
        fake = FakeSerial([])
        reader.serial_port = fake
        reader._run(False)
        self.assertFalse(reader.status()["connected"])
        self.assertTrue(fake.closed)


class VisualizationTests(unittest.TestCase):
    """Keep SVG element identities stable for browser-controlled animation."""

    def test_wall_exposes_persistent_avatar_parts_for_browser_animation(self):
        import xml.etree.ElementTree as ET

        state = SensorState()
        root = ET.fromstring(render_wall(state.snapshot()))
        for name in (
            "avatar-position",
            "avatar-scale",
            "avatar-lean",
            "avatar-twist",
            "wall-progress",
        ):
            self.assertEqual(len(root.findall(f".//*[@id='{name}']")), 1)
        self.assertFalse(root.findall(".//animateTransform"))


if __name__ == "__main__":
    unittest.main()

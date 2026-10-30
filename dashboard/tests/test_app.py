"""HTTP and SSE integration tests using a loopback server and simulated input."""

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

# Import the dashboard modules when discovery runs from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Dashboard, DashboardServer


class LiveSerial:
    """Emit paced firmware CSV rows so HTTP tests exercise the real serial reader."""

    def __init__(self):
        self.closed = False
        self.in_waiting = 1
        self.tick = 0

    def open(self):
        self.closed = False

    def close(self):
        self.closed = True

    def read(self, size):
        # Match a 100 Hz device clock without producing an unbounded busy loop.
        time.sleep(0.01)
        self.tick += 10
        return f"{self.tick},0,0,9.807,0,0,45,-25,5,9.807,45,0,ROTATING,100\n".encode()


class AppTests(unittest.TestCase):
    """Exercise browser endpoints against a live server with isolated recordings."""

    def setUp(self):
        # Port zero lets the OS choose a free local port for each test.
        self.directory = tempfile.TemporaryDirectory()
        self.dashboard = Dashboard(recordings_dir=Path(self.directory.name))
        self.server = DashboardServer(("127.0.0.1", 0), self.dashboard)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        # Stop request handling before deleting files owned by this fixture.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def get(self, path):
        """Read an endpoint using the same HTTP path as the browser."""
        with urlopen(self.base + path, timeout=3) as response:
            return response.read()

    def post(self, action, **values):
        """Send a same-origin action and decode its JSON response."""
        request = Request(
            self.base + "/api/action",
            json.dumps({"action": action, **values}).encode(),
            {"Content-Type": "application/json", "Origin": self.base},
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def snapshot(self):
        """Fetch the current public state through the HTTP API."""
        return json.loads(self.get("/api/state"))

    def wait_for(self, predicate):
        """Poll state until asynchronous reader work satisfies the predicate."""
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline:
            snapshot = self.snapshot()
            if predicate(snapshot):
                return snapshot
            time.sleep(0.01)
        self.fail("Expected sensor state did not arrive")

    def next_event(self, stream, predicate=lambda value: True):
        """Consume SSE data frames until a matching state arrives."""
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline:
            line = stream.readline().decode()
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if predicate(payload):
                    return payload
        self.fail("Expected streaming event did not arrive")

    def test_page_and_browser_reload_preserve_reader(self):
        self.wait_for(lambda state: state["total_samples"] > 3)
        before = self.snapshot()
        for _ in range(3):
            page = self.get("/").decode()
            self.assertIn("avatar-position", page)
            self.assertIn("/static/app.js", page)
        after = self.snapshot()
        self.assertEqual(before["generation"], after["generation"])
        self.assertGreaterEqual(after["total_samples"], before["total_samples"])
        self.assertIn("new EventSource", self.get("/static/app.js").decode())
        self.assertTrue(json.loads(self.get("/health"))["ok"])

    def test_continuous_stream_controls_and_reset(self):
        with urlopen(self.base + "/events", timeout=3) as stream:
            first = self.next_event(stream, lambda data: data["total_samples"] > 3)
            self.post("simulation", values={"roll": 30, "pitch": -15, "gz": 0, "energy": 0})
            moved = self.next_event(stream, lambda data: data["roll"] > 20)
            self.assertGreater(moved["total_samples"], first["total_samples"])
            self.assertLessEqual(len(moved["history"]), 10)
            sequences = [row["seq"] for row in moved["history"]]
            following = self.next_event(stream, lambda data: bool(data["history"]))
            # Each SSE payload carries a fresh history delta rather than a replay.
            self.assertGreater(following["history"][0]["seq"], max(sequences))
            self.post("reset")
            reset = self.next_event(
                stream, lambda data: data["generation"] != first["generation"]
            )
            self.assertEqual(reset["move_count"], 0)
            self.assertEqual(reset["connection"]["simulation"]["roll"], 0)
            self.assertEqual(reset["connection"]["source"], "Simulation")

    def test_move_recording_and_download(self):
        # Drive the normal detector through a complete simulated movement cycle.
        self.post("reset")
        self.post("record_start")
        self.post("simulate_move")
        snap = self.wait_for(lambda state: state["move_count"] == 1)
        self.assertEqual(len(snap["moves"]), 1)
        self.post("record_stop")
        rows = list(csv.DictReader(io.StringIO(self.get("/api/recording").decode())))
        self.assertGreater(len(rows), 100)
        self.assertEqual(rows[0]["source"], "simulation")
        self.assertIn("received_at_utc", rows[0])
        self.assertFalse(self.snapshot()["recording"])

    def test_source_changes_do_not_break_stream(self):
        with urlopen(self.base + "/events", timeout=3) as stream:
            self.next_event(stream)
            for _ in range(4):
                self.post("source", source="serial")
                snap = self.next_event(
                    stream,
                    lambda data: data["connection"]["source"] == "Serial / ESP32",
                )
                self.assertFalse(snap["connection"]["connected"])
                self.post("source", source="simulation")
                snap = self.next_event(
                    stream,
                    lambda data: data["connection"]["source"] == "Simulation"
                    and data["total_samples"] > 0,
                )
                self.assertTrue(snap["connection"]["connected"])

    def test_opening_browser_resumes_an_idle_simulation(self):
        self.dashboard.reader.stop()
        self.assertFalse(self.dashboard.reader.connected)
        with urlopen(self.base + "/events", timeout=3) as stream:
            snap = self.next_event(stream, lambda data: data["fresh"])
            self.assertTrue(snap["connection"]["connected"])
            self.assertGreater(snap["total_samples"], 0)

    def test_serial_csv_reaches_browser_stream(self):
        fake = LiveSerial()
        port = SimpleNamespace(device="COM5", description="ESP32 test")
        with (
            patch("app.available_ports", return_value=([port], "")),
            patch("serial_reader.serial.Serial", return_value=fake),
        ):
            self.post("connect", port="COM5")
            with urlopen(self.base + "/events", timeout=3) as stream:
                snap = self.next_event(stream, lambda data: data["fresh"])
                self.assertEqual(snap["connection"]["source"], "Serial / ESP32")
                self.assertEqual(snap["latest"]["roll"], -25)
                self.assertEqual(snap["latest"]["gz"], 45)
            self.post("source", source="simulation")
        self.assertTrue(fake.closed)

    def test_filter_debug_and_display_signals_reach_the_browser(self):
        self.post("reset")
        self.post("simulation", values={"roll": 2, "pitch": -2, "gz": 4, "energy": 0.1})
        with urlopen(self.base + "/events", timeout=3) as stream:
            snap = self.next_event(
                stream, lambda data: data["latest"] and data["latest"]["gz"] == 4
            )
            # Debug data retains the low-pass response while display deadzones hide noise.
            self.assertGreater(snap["filtered"]["gz"], 0)
            self.assertLess(snap["filtered"]["gz"], 4)
            self.assertEqual(snap["display"]["gz"], 0)
            self.assertEqual(snap["display"]["movementEnergy"], 0)
            self.assertEqual(snap["status"], "STILL")
            self.assertEqual(snap["move_count"], 0)
        page = self.get("/").decode()
        self.assertIn('id="filter-debug"', page)
        self.assertIn('id="debug-filtered-movementEnergy"', page)

    def test_bad_inputs_and_cross_origin_are_rejected(self):
        with patch("app.available_ports", return_value=([], "")):
            for action, values in [
                (
                    "simulation",
                    {
                        "values": {
                            "roll": float("nan"),
                            "pitch": 0,
                            "gz": 0,
                            "energy": 0,
                        }
                    },
                ),
                ("config", {"values": {"start_seconds": -1}}),
                ("source", {"source": "unknown"}),
                ("connect", {"port": "COM999"}),
            ]:
                with self.subTest(action=action), self.assertRaises(HTTPError) as error:
                    self.post(action, **values)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        # Even a valid command must be rejected when sent from another origin.
        request = Request(
            self.base + "/api/action",
            b'{"action":"reset"}',
            {"Content-Type": "application/json", "Origin": "https://example.com"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        self.assertTrue(self.snapshot()["connection"]["connected"])


if __name__ == "__main__":
    unittest.main()

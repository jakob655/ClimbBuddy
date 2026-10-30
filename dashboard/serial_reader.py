"""Acquire serial or simulated samples independently of browser rendering.

One worker feeds the shared state pipeline. Browser heartbeats keep the source
alive; an idle session releases its serial port and closes any active recording.
"""

import math
from collections import deque
import threading
import time

import serial
from serial.tools import list_ports

from sensor_state import CSVParser, GRAVITY_MS2, SAMPLE_HZ, SensorSample, SensorState


SERIAL_BAUD = 115200
SERIAL_TIMEOUT = 0.1
MAX_LINE_BYTES = 2048
CLIENT_TIMEOUT_SECONDS = 30  # Release the COM port after the browser disappears.


def available_ports():
    """List serial devices in a stable order, returning scan errors as UI text."""
    try:
        return sorted(list_ports.comports(), key=lambda port: port.device), ""
    except (OSError, serial.SerialException) as exc:
        return [], f"Serial-Ports konnten nicht gelesen werden: {exc}"


class SensorReader:
    """Own one source and worker; HTTP handlers only change controls or read status."""

    def __init__(self, state: SensorState):
        self.state = state
        # Protect reader metadata independently of SensorState's processing lock.
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.serial_port = None
        self.source = "Serial / ESP32"
        self.port = ""
        self.connected = False
        self.error = ""
        self.ignored_lines = 0
        self.received_bytes = 0
        self.received_lines = 0
        self.recent_lines = deque(maxlen=8)
        self.stream_format = ""
        self.firmware_error = ""
        self.last_client_seen = time.perf_counter()
        self.simulation = {"roll": 0.0, "pitch": 0.0, "gz": 0.0, "energy": 0.0}
        self.pulse_started = None

    def heartbeat(self):
        """Refresh the monotonic timestamp of the last active browser stream."""
        with self.lock:
            self.last_client_seen = time.perf_counter()

    def status(self):
        """Copy diagnostics so request threads do not expose mutable containers."""
        with self.lock:
            return {
                "connected": self.connected,
                "source": self.source,
                "port": self.port,
                "error": self.error,
                "ignored_lines": self.ignored_lines,
                "simulating_move": self.pulse_started is not None,
                "received_bytes": self.received_bytes,
                "received_lines": self.received_lines,
                "recent_lines": list(self.recent_lines),
                "stream_format": self.stream_format,
                "firmware_error": self.firmware_error,
                "simulation": dict(self.simulation),
            }

    def stop(self):
        """Request worker shutdown and finish recording before switching sources."""
        self.stop_event.set()
        # Do not hold the reader lock while joining: the worker needs it to exit.
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self.serial_port:
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
        with self.lock:
            self.connected = False
            self.pulse_started = None
        self.state.reset_detection()
        self.state.stop_recording()

    def _prepare(self, source):
        """Stop the previous worker and clear session state before a new source."""
        self.stop()
        if self.thread and self.thread.is_alive():
            # Never start a second worker while the previous one still owns I/O.
            with self.lock:
                self.error = "Reader beendet sich noch. Bitte gleich erneut verbinden."
            return False
        self.state.clear()
        self.stop_event = threading.Event()
        with self.lock:
            self.source, self.error, self.port = source, "", ""
            self.ignored_lines = 0
            self.received_bytes = self.received_lines = 0
            self.recent_lines.clear()
            self.stream_format = self.firmware_error = ""
            self.last_client_seen = time.perf_counter()
            self.serial_port = None
        return True

    def start_serial(self, port: str):
        """Open the requested device without toggling ESP32 reset control lines."""
        if not self._prepare("Serial / ESP32"):
            return
        connection = None
        try:
            # Set control lines BEFORE opening, as in platformio.ini.
            connection = serial.Serial(
                port=None, baudrate=SERIAL_BAUD, timeout=SERIAL_TIMEOUT
            )
            connection.dtr = False
            connection.rts = False
            connection.port = port
            connection.open()
        except (OSError, serial.SerialException, ValueError) as exc:
            if connection:
                connection.close()
            with self.lock:
                self.error = (
                    f"{port} konnte nicht geöffnet werden: {exc}. "
                    "Serial Monitor schließen und Port prüfen."
                )
            return
        with self.lock:
            self.serial_port, self.port, self.connected = connection, port, True
        self.thread = threading.Thread(
            target=self._run, args=(False,), daemon=True, name="belaybuddy-serial"
        )
        self.thread.start()

    def select_serial(self):
        """Select hardware mode without opening a port until the user connects."""
        self._prepare("Serial / ESP32")

    def start_simulation(self):
        """Start a fresh synthetic session using the same ingestion pipeline."""
        if not self._prepare("Simulation"):
            return
        with self.lock:
            self.connected = True
        self.thread = threading.Thread(
            target=self._run, args=(True,), daemon=True, name="belaybuddy-simulation"
        )
        self.thread.start()

    def set_simulation(self, roll, pitch, gz, energy):
        """Set degrees, degrees/second, and acceleration deviation in m/s²."""
        with self.lock:
            self.simulation = {"roll": roll, "pitch": pitch, "gz": gz, "energy": energy}

    def simulate_move(self):
        """Queue one rest-motion-rest pulse without directly incrementing moves."""
        with self.lock:
            if self.connected and self.source == "Simulation" and self.pulse_started is None:
                self.state.reset_detection()
                self.pulse_started = time.perf_counter()

    def _simulation_sample(self, now, started):
        """Build firmware-shaped data from controls and an optional motion pulse."""
        with self.lock:
            values = dict(self.simulation)
            pulse_started = self.pulse_started
        roll, pitch, gz, energy = (values[key] for key in ("roll", "pitch", "gz", "energy"))
        with self.state.lock:
            config = self.state.config
        if pulse_started is not None:
            # Leave enough rest and motion time for the configured confirmations.
            elapsed = now - pulse_started
            rest = config.end_seconds + 0.15
            motion = max(0.6, config.start_seconds + 0.2)
            gz, energy = 0.0, 0.0
            if rest <= elapsed < rest + motion:
                energy = max(2.0, config.energy_threshold * 1.5)
                gz = max(60.0, config.gyro_threshold * 1.5)
            if elapsed >= 2 * rest + motion:
                with self.lock:
                    self.pulse_started = None
        # Gravity rotates with attitude; energy changes acceleration magnitude
        # independently. It is an acceleration deviation, not physical energy.
        roll_r, pitch_r = math.radians(roll), math.radians(pitch)
        magnitude = GRAVITY_MS2 + energy
        ax = -math.sin(pitch_r) * magnitude
        ay = math.sin(roll_r) * math.cos(pitch_r) * magnitude
        az = math.cos(roll_r) * math.cos(pitch_r) * magnitude
        state = (
            "ROTATING"
            if abs(gz) > 30
            else "MOVING" if energy > 0.35 or abs(gz) > 3 else "STILL"
        )
        return SensorSample(
            timestamp=int((now - started) * 1000) % 2**32,
            ax=ax,
            ay=ay,
            az=az,
            gx=0.0,
            gy=0.0,
            gz=gz,
            roll=roll,
            pitch=pitch,
            accelerationMagnitude=magnitude,
            gyroMagnitude=abs(gz),
            movementEnergy=energy,
            state=state,
            sampleHz=float(SAMPLE_HZ),
        )

    def _run(self, simulation):
        """Acquire samples until stopped, idle, or disconnected, then release I/O."""
        parser = CSVParser()
        # Serial reads may contain partial rows or several rows. Keep only one
        # bounded pending line and discard oversized input until its next newline.
        pending = bytearray()
        dropping_line = False
        started = next_sample = time.perf_counter()
        try:
            while not self.stop_event.is_set():
                now = time.perf_counter()
                with self.lock:
                    expired = now - self.last_client_seen > CLIENT_TIMEOUT_SECONDS
                if expired:
                    with self.lock:
                        self.error = (
                            "Verbindung nach 30 s ohne Dashboard-Updates beendet. "
                            "Bitte neu verbinden."
                        )
                    break
                if simulation:
                    if now < next_sample:
                        # Python 3.11+ sleep uses a high-resolution Windows timer;
                        # Event.wait can round 10 ms up to a 15.6 ms system tick.
                        time.sleep(min(next_sample - now, 1 / SAMPLE_HZ))
                        continue
                    self.state.ingest(self._simulation_sample(now, started), "simulation", now)
                    # No invented catch-up samples after a pause / slow machine.
                    next_sample = max(next_sample + 1 / SAMPLE_HZ, now + 0.001)
                    continue

                chunk = self.serial_port.read(
                    min(max(self.serial_port.in_waiting, 1), 4096)
                )
                with self.lock:
                    self.received_bytes += len(chunk)
                for byte in chunk:
                    if byte == 10:
                        with self.lock:
                            self.received_lines += 1
                        if not dropping_line:
                            line = pending.decode("utf-8", errors="replace").strip()
                            # Keep short diagnostics even for firmware output that
                            # is not valid CSV; only strict UTF-8 reaches parsing.
                            with self.lock:
                                if line:
                                    self.recent_lines.append(line[:300])
                                if "FEHLER:" in line:
                                    self.firmware_error = line
                                if line == "--- IMU ---" or line.startswith("ax:"):
                                    self.stream_format = "text"
                                elif (
                                    line.startswith("Raw:")
                                    or "Active mode: PRESSURE_TEST" in line
                                ):
                                    self.stream_format = "pressure"
                            try:
                                sample = parser.parse(pending.decode("utf-8", errors="strict"))
                            except UnicodeDecodeError:
                                sample = None
                            if sample:
                                with self.lock:
                                    self.stream_format, self.firmware_error = "csv", ""
                                self.state.ingest(sample, "serial")
                            else:
                                with self.lock:
                                    self.ignored_lines += 1
                        pending.clear()
                        dropping_line = False
                    elif not dropping_line:
                        pending.append(byte)
                        if len(pending) > MAX_LINE_BYTES:
                            pending.clear()
                            dropping_line = True
                            with self.lock:
                                self.ignored_lines += 1
        except (serial.SerialException, OSError, ValueError) as exc:
            with self.lock:
                self.error = (
                    f"Serial-Verbindung unterbrochen: {exc}. "
                    "USB-Verbindung prüfen und erneut verbinden."
                )
        finally:
            if self.serial_port:
                try:
                    self.serial_port.close()
                except (OSError, serial.SerialException):
                    pass
            self.state.stop_recording()
            self.state.reset_detection()
            with self.lock:
                self.connected = False
                self.pulse_started = None

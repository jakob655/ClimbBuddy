"""Local live dashboard: Python + pyserial, HTTP/SSE and browser animation."""

import argparse
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
import webbrowser

from sensor_state import DetectionConfig, RECORDINGS_DIR, SensorState
from serial_reader import SensorReader, available_ports
from visualization import render_wall

STATIC = Path(__file__).resolve().parent / 'static'
STREAM_HZ = 30


class Dashboard:
    """One shared sensor per server. Reloads/tabs don't create extra serial readers."""

    def __init__(self, *, simulate=True, recordings_dir=RECORDINGS_DIR):
        self.lock = threading.RLock()
        self.state = SensorState()
        self.reader = SensorReader(self.state)
        self.recordings_dir = recordings_dir
        self.stream_slots = threading.BoundedSemaphore(4)
        if simulate:
            self.reader.start_simulation()

    def close(self):
        self.reader.stop()

    def begin_stream(self):
        with self.lock:
            # A browser opened after the idle timeout should work immediately.
            if self.reader.source == 'Simulation' and not self.reader.connected:
                self.reader.start_simulation()
            self.reader.heartbeat()

    def ports(self):
        ports, error = available_ports()
        return {'ports': [{'device': p.device, 'description': p.description} for p in ports], 'error': error}

    def payload(self, generation=None, sequence=0):
        with self.lock:
            cursor = sequence if generation == self.state.generation else 0
            snap = self.state.snapshot(after_sequence=cursor)
            snap['latest'] = asdict(snap['latest']) if snap['latest'] else None
            path = snap['recording_path']
            snap['recording_name'] = path.name if path else None
            snap['recording_path'] = str(path) if path else None
            snap['connection'] = self.reader.status()
            snap['config'] = asdict(self.state.config)
            snap['server_time'] = time.time()
            return snap

    @staticmethod
    def number(value, low, high):
        number = float(value)
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'Wert muss zwischen {low} und {high} liegen.')
        return number

    def action(self, data):
        with self.lock:
            action = data.get('action')
            if action == 'source':
                if data.get('source') == 'simulation':
                    if self.reader.source != 'Simulation' or not self.reader.connected:
                        self.reader.start_simulation()
                elif data.get('source') == 'serial':
                    if self.reader.source != 'Serial / ESP32':
                        self.reader.select_serial()
                else:
                    raise ValueError('Unbekannte Datenquelle.')
            elif action == 'connect':
                ports = self.ports()['ports']
                port = data.get('port')
                if port not in [item['device'] for item in ports]:
                    raise ValueError('Port nicht gefunden. USB-Kabel prüfen und Ports aktualisieren.')
                self.reader.start_serial(port)
                if not self.reader.connected:
                    raise ValueError(self.reader.error)
            elif action == 'disconnect':
                self.reader.stop()
            elif action == 'simulation':
                if self.reader.source != 'Simulation':
                    raise ValueError('Zuerst Simulation auswählen.')
                values = data.get('values', {})
                limits = {'roll': (-45, 45), 'pitch': (-45, 45), 'gz': (-150, 150), 'energy': (0, 5)}
                if not isinstance(values, dict) or set(values) != set(limits):
                    raise ValueError('Simulation benötigt Roll, Pitch, Gyro Z und Energy.')
                self.reader.set_simulation(**{key: self.number(values[key], *bounds) for key, bounds in limits.items()})
            elif action in ('reset', 'simulate_move'):
                if self.reader.source != 'Simulation':
                    raise ValueError('Zuerst Simulation auswählen.')
                if action == 'reset':
                    self.reader.set_simulation(0, 0, 0, 0)
                    self.reader.start_simulation()
                else:
                    if not self.reader.connected:
                        self.reader.start_simulation()
                    self.reader.simulate_move()
            elif action == 'config':
                limits = {'energy_threshold': (.1, 5), 'gyro_threshold': (1, 150),
                          'start_seconds': (.05, 1), 'end_seconds': (.1, 2),
                          'rotation_threshold': (1, 150), 'lean_threshold': (1, 45)}
                values = data.get('values', {})
                if not isinstance(values, dict) or not values or not set(values) <= set(limits):
                    raise ValueError('Ungültige Schwellenwerte.')
                changes = {key: self.number(value, *limits[key]) for key, value in values.items()}
                self.state.configure(replace(self.state.config, **changes))
            elif action == 'record_start':
                if not self.reader.connected:
                    raise ValueError('Zuerst eine Datenquelle verbinden.')
                self.state.start_recording(self.recordings_dir)
                if self.state.recording_error:
                    raise ValueError(self.state.recording_error)
            elif action == 'record_stop':
                self.state.stop_recording()
            else:
                raise ValueError('Unbekannte Aktion.')


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, dashboard):
        self.dashboard = dashboard
        self.stopping = threading.Event()
        super().__init__(address, DashboardHandler)

    def server_close(self):
        self.stopping.set()
        self.dashboard.close()
        super().server_close()


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # A browser may close/reload while a response is in flight.

    def log_message(self, *args):
        pass  # Avoid flooding the terminal with streaming updates.

    def local_request(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        return self.headers.get('Host') in allowed

    def send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), 'application/json; charset=utf-8', status)

    def do_GET(self):
        if not self.local_request():
            self.json_response({'error': 'Nur lokale Zugriffe erlaubt.'}, 403)
            return
        path = urlparse(self.path).path
        if path == '/':
            page = (STATIC / 'index.html').read_text(encoding='utf-8')
            page = page.replace('{{WALL}}', render_wall(self.server.dashboard.state.snapshot()))
            self.send_bytes(page.encode(), 'text/html; charset=utf-8')
        elif path in ('/static/app.js', '/static/style.css'):
            kind = 'text/javascript' if path.endswith('.js') else 'text/css'
            self.send_bytes((STATIC / Path(path).name).read_bytes(), kind + '; charset=utf-8')
        elif path == '/api/ports':
            self.json_response(self.server.dashboard.ports())
        elif path == '/api/state':
            self.json_response(self.server.dashboard.payload())
        elif path == '/events':
            self.stream()
        elif path == '/api/recording':
            self.download_recording()
        elif path == '/health':
            self.json_response({'ok': True, 'stream_hz': STREAM_HZ})
        else:
            self.json_response({'error': 'Nicht gefunden.'}, 404)

    def do_POST(self):
        origin = self.headers.get('Origin')
        valid_origin = origin is None or origin == 'http://' + self.headers.get('Host', '')
        if not self.local_request() or not valid_origin:
            self.json_response({'error': 'Lokale Herkunft erforderlich.'}, 403)
            return
        if self.path != '/api/action':
            self.json_response({'error': 'Nicht gefunden.'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096 or self.headers.get_content_type() != 'application/json':
                raise ValueError('JSON-Anfrage mit maximal 4096 Bytes erforderlich.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('JSON-Objekt erforderlich.')
            self.server.dashboard.action(data)
            self.json_response({'ok': True})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.json_response({'error': str(exc)}, 400)
        except OSError as exc:
            self.json_response({'error': f'Lokaler Ein-/Ausgabefehler: {exc}'}, 500)

    def stream(self):
        controller = self.server.dashboard
        if not controller.stream_slots.acquire(blocking=False):
            self.json_response({'error': 'Maximal vier Dashboard-Tabs gleichzeitig.'}, 503)
            return
        generation, sequence = None, 0
        try:
            controller.begin_stream()
            self.connection.settimeout(2)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache, no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.wfile.write(b'retry: 700\n\n')
            deadline = time.perf_counter()
            while not self.server.stopping.is_set():
                # The connection is alive even when browser JS is in a background tab.
                controller.reader.heartbeat()
                payload = controller.payload(generation, sequence)
                generation, sequence = payload['generation'], payload['total_samples']
                frame = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                self.wfile.write(('data: ' + frame + '\n\n').encode())
                self.wfile.flush()
                deadline = max(deadline + 1 / STREAM_HZ, time.perf_counter())
                # High-resolution Windows timer; Event.wait rounds short waits up.
                time.sleep(max(0, deadline - time.perf_counter()))
        except (OSError, ConnectionError):
            pass  # Expected when a tab closes or the process is stopped.
        finally:
            controller.stream_slots.release()
            self.close_connection = True

    def download_recording(self):
        with self.server.dashboard.state.lock:
            state = self.server.dashboard.state
            path = state.recording_path
            if state.recording_file or not path or not path.is_file():
                self.json_response({'error': 'Zuerst eine Aufnahme stoppen.'}, 404)
                return
            handle = path.open('rb')
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv; charset=utf-8')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Content-Disposition', f'attachment; filename="{path.name}"')
            self.end_headers()
            while chunk := handle.read(64 * 1024):
                self.wfile.write(chunk)
        except OSError:
            pass
        finally:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description='BelayBuddy Live-Dashboard')
    parser.add_argument('--port', type=int, default=8501)
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args()
    controller = Dashboard()
    try:
        server = DashboardServer(('127.0.0.1', args.port), controller)
    except OSError as exc:
        controller.close()
        parser.exit(1, f'Port {args.port} nicht verfügbar: {exc}\nBitte Dashboard starten.cmd verwenden oder --port ändern.\n')
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'BelayBuddy Live · {url}\nFenster offen lassen. Beenden mit Strg+C.', flush=True)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

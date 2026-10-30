"""Start the local dashboard with the project's Python environment."""

from pathlib import Path
import socket
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
DEPENDENCY_CHECK = """
from importlib.metadata import version
import serial
assert tuple(map(int, version('pyserial').split('.')[:2])) >= (3, 5), 'pyserial >= 3.5 erforderlich'
print('Python:', __import__('sys').executable)
for name in ('pyserial',):
    print(name + ':', version(name))
"""


def free_port():
    # A leftover server must not send the user back to an old dashboard instance.
    for port in range(8501, 8521):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError("Keine freie Portnummer zwischen 8501 und 8520. Alte Dashboard-Terminals schließen.")


def main():
    if sys.version_info < (3, 10):
        print("Python 3.10 oder neuer erforderlich.")
        return 1
    try:
        if not PYTHON.exists():
            print("Erstelle die Python-Umgebung im Projekt ...", flush=True)
            subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
        checked = subprocess.run([str(PYTHON), "-c", DEPENDENCY_CHECK], cwd=ROOT, capture_output=True, text=True)
        if checked.returncode:
            print("Installiere die benötigten Dashboard-Pakete ...", flush=True)
            subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(ROOT / "dashboard/requirements.txt")], cwd=ROOT, check=True)
            subprocess.run([str(PYTHON), "-c", DEPENDENCY_CHECK], cwd=ROOT, check=True)
        else:
            print(checked.stdout, end="", flush=True)
        if "--check" in sys.argv:
            print("Startprüfung erfolgreich. Es wurde kein Server gestartet.")
            return 0
        port = free_port()
        print(f"\nDashboard: http://127.0.0.1:{port}\nDieses Fenster offen lassen. Beenden mit Strg+C.\n", flush=True)
        return subprocess.run([
            str(PYTHON), "-u", str(ROOT / "dashboard/app.py"),
            "--port", str(port), "--open-browser",
        ], cwd=ROOT).returncode
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Start fehlgeschlagen: {exc}")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

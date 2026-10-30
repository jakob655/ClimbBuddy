# BelayBuddy dashboard

The dashboard is a local Python application that displays USB serial data from
the ESP32-C3 / MPU6050 firmware. A built-in simulation works without hardware.
The browser draws a persistent SVG avatar and Canvas charts; Python handles
acquisition, filtering, movement detection, and CSV recording.

Use **Python 3.10 or later**. The only required Python package is `pyserial`; the
server and sensor pipeline use the standard library. Some interface labels are
German; this guide uses their visible names. See the
[main README](../README.md) for wiring and firmware installation.

## Start and stop

On Windows, double-click **`Dashboard starten.cmd`** in the project root, or run
this command from that directory:

```powershell
python dashboard/launch.py
```

The launcher uses the project's `.venv`, creates it if needed, checks `pyserial`,
and installs requirements if the dependency check fails. Initial setup may need
internet access. It selects a free port between **8501 and 8520** and opens the
browser at the printed address. **Simulation starts automatically.** Keep the
terminal open and press **Ctrl+C** to stop the server.

For manual installation and a fixed port:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r dashboard/requirements.txt
.\.venv\Scripts\python.exe dashboard/app.py --port 8501 --open-browser
```

On macOS or Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.
The server listens on `127.0.0.1`. Choose another `--port` if needed, or let the
launcher select one. Close old terminals and tabs if several instances are running.

## Try the simulation

1. Keep **Data Source** set to **Simulation**. Samples and charts should update
   even when all sliders are at zero.
2. Adjust **Roll** to lean and move the avatar sideways. **Pitch** slightly
   changes its scale. **Gyro Z** adds a bounded twist; return the slider to zero
   to let the avatar turn back toward center.
3. Adjust **Movement Energy** to change acceleration magnitude relative to gravity.
4. Click **Reset**, then **Simulate Move**. After roughly two seconds, expect
   **Detected Moves = 1** and **Demo climbing progress = 4%**.
5. Click **Start Recording**, generate motion, then **Stop Recording** and
   **CSV herunterladen**.

`Simulate Move` sends a rest-motion-rest sequence through the same detector as
serial input. Holding a motion slider high does not complete a move; the detector
also requires rest. **Reset** returns sliders to zero and starts a new simulation
session.

## Connect an ESP32

1. Wire the MPU6050 as described in the [main README](../README.md).
2. Confirm these settings in `src/main.cpp`:

   ```cpp
   const TestMode ACTIVE_TEST = IMU_TEST;
   constexpr bool IMU_CSV_OUTPUT = true;
   ```

3. Build and **upload** the firmware. Building alone does not transfer it.
4. Close the PlatformIO Serial Monitor. Leave the sensor flat and still, press
   **RESET**, and allow about eight seconds for startup and calibration.
5. Select **Data Source > Serial / ESP32**, click **Ports aktualisieren**, choose
   the correct port, and click **Connect**.
6. Check that the sample count increases, then tilt or rotate the sensor.
7. Click **Disconnect** before another upload or opening the Serial Monitor.

One application can own the port at a time. Firmware and reader use **115200 baud**.
The reader keeps DTR and RTS low when opening the port, matching `platformio.ini`.
Port names may change after reset or upload; refresh the list if needed.

Expand **Verbindungsdiagnose · letzte Serial-Zeilen** to inspect received bytes,
line counts, and recent device output. IMU text blocks mean CSV output must be
enabled; pressure-test lines mean `IMU_TEST` must be selected. An open port alone
does not imply valid samples. After two seconds without valid data, the dashboard
shows **NO DATA** and keeps the last pose and history visible.

## Read the display

| Display | Meaning |
| --- | --- |
| **Sample Rate · received** | Samples actually received by Python per second |
| **Quelle ... Hz** | Firmware-reported `sampleHz`, or the simulation target rate |
| **Current State** | Status after filtering, deadzones, and confirmation |
| **Detected Moves** | Completed motion-rest cycles in the session |
| **Acceleration** | Smoothed magnitude, including gravity |
| **Demo climbing progress** | Move count times 4%, capped at 100% |
| **Recent Moves** | The last five moves and their peak readings |

Acquisition targets **100 samples/s** in a background Python thread. The server
sends server-sent events (SSE) at a target **30 packets/s**, including the latest
pose and newly received history. The avatar uses `requestAnimationFrame`; charts
target 30 redraws/s and numbers update at 10 Hz. Actual rendering depends on the
browser, display, and computer. Background tabs may draw less often while Python
acquisition continues through an open event stream.

Python history is bounded to 30 seconds / 3200 samples; browser history to 10
seconds / 3200 samples. Charts can skip points for rendering efficiency while
detection and recording process the acquired samples.

## Movement detection

A completed **REST -> MOVING -> REST** cycle counts as one move. Defaults are:

- Start when **Movement Energy > 0.6 m/s^2** or **gyroscope magnitude > 20 deg/s**
  persists for at least **200 ms**.
- Finish when both channels remain below **65% of their start thresholds** for
  at least **400 ms**.

Open **Move detection · thresholds**, adjust the values, and click **Übernehmen**
to apply them. Duration fields use seconds.
Changing settings discards an unfinished move. Brief spikes and continuously held
motion do not complete a cycle. Deadzones still limit sensitivity if thresholds
are lowered below them.

Move start/end times are relative to the session. Duration excludes the final
rest confirmation period. Peak acceleration includes gravity. Maximum roll/pitch
retain the sign of the greatest absolute excursion. Peaks use raw received
samples, while movement decisions use filtered values after deadzones. Device
clock resets or sample gaps over one second discard open moves; normal `millis()`
rollover is handled.

## Filtering and tuning

```text
Received sample (RAW) -> low-pass filter (FILTERED) -> deadzone (DISPLAY)
                                                        |
                                             display, status, move decisions
```

With serial input, RAW already includes firmware filtering. The dashboard adds
smoothing for orientation, gyroscope data, movement energy, and acceleration.
Filter response accounts for elapsed sample time; alpha constants are referenced
to 100 Hz. Deadzones suppress small outputs without erasing filter memory.

Defaults are grouped at the top of `sensor_state.py`:

| Constant | Default | Effect |
| --- | --- | --- |
| `ORIENTATION_SMOOTHING_ALPHA` | 0.14 | Roll/pitch smoothing |
| `GYRO_SMOOTHING_ALPHA` | 0.14 | Gyroscope axis and magnitude smoothing |
| `MOVEMENT_SMOOTHING_ALPHA` | 0.10 | Movement energy and acceleration smoothing |
| `ROLL_DEADZONE_DEG` / `PITCH_DEADZONE_DEG` | 3 degrees | Ignore small orientation changes |
| `GYRO_DEADZONE_DPS` | 5 deg/s | Ignore small gyroscope readings |
| `MOVEMENT_DEADZONE` | 0.12 m/s^2 | Ignore small movement energy |
| `STATE_ENTER_MS` / `STATE_EXIT_MS` | 200 / 400 ms | Confirm active status / return to `STILL` |
| `MOVE_GYRO_THRESHOLD` | 20 deg/s | Initial gyroscope move threshold |
| `MOVE_ENERGY_THRESHOLD` | 0.6 m/s^2 | Initial alternative move threshold |
| `MOVE_START_CONFIRM_MS` / `MOVE_END_CONFIRM_MS` | 200 / 400 ms | Initial start / rest durations |

The `MOVE_*` constants initialize the movement controls. Directional states, such
as leaning or rotating left/right, have their own confirmation period. The
firmware status is displayed separately as source information.

In `static/app.js`, `AVATAR_POSITION_SMOOTHING = 0.10` and
`AVATAR_ROTATION_SMOOTHING = 0.08` control visual interpolation. Their reference
rate is 60 frames/s, adjusted to actual frame intervals.

- Too restless: reduce smoothing alphas or avatar factors, increase deadzones or
  confirmation times, and increase move thresholds if noise causes false moves.
- Too slow: increase smoothing alphas or avatar factors, or shorten confirmation
  times. Lower deadzones or thresholds if intended movements are suppressed.

Change one value at a time. Restart the server and reload the browser after
editing code constants. Expand **Filter-Debug · RAW vs. FILTERED** to compare
received, smoothed, and displayed signals. For example, simulation values of
Roll 2 degrees, Gyro Z 4 deg/s, and Energy 0.1 m/s^2 should remain below display
deadzones: status `STILL`, no moves. Larger movements should respond smoothly.

## Recording and sessions

**Start Recording** writes RAW samples directly to a timestamped CSV under
`dashboard/recordings/`. Filenames use local time and microseconds. Each row
contains `received_at_utc`, `source`, and these firmware fields:

```text
timestamp,ax,ay,az,gx,gy,gz,roll,pitch,accelerationMagnitude,gyroMagnitude,movementEnergy,state,sampleHz
```

`timestamp` is the device's millisecond counter, not Unix time. Acceleration uses
m/s^2 and includes gravity; gyro data uses deg/s; roll/pitch use degrees.
`movementEnergy = abs(accelerationMagnitude - 9.80665)` is a motion indicator in
m/s^2, not physical energy. The parser accepts a missed header, reordered valid
headers, and the alias `accMagnitude`. Startup text and malformed samples are
ignored.

Stop the recording before using **CSV herunterladen**. Downloads stream in small
blocks, so long recordings are not loaded entirely into memory. Reset, source
changes, disconnects, and reader shutdown stop recording. Existing files remain.

Each server owns one reader and one shared session. Up to **four live tabs** can
view and control it. Reloading reconnects the stream without resetting moves or
recording. The browser retries a lost event stream automatically.

After about **30 seconds without an active browser event stream**, the reader
releases the serial port and stops recording. Reopening the dashboard resumes an
idle simulation as a new session. Reconnect serial input using **Connect**.
Stopping the server with **Ctrl+C** also closes the reader and recording.

## Troubleshooting and checks

| Problem | Action |
| --- | --- |
| Launcher fails | Read the terminal message; check Python 3.10+ and package installation |
| Address in use | Use the launcher or choose a different `--port` for `app.py` |
| No ports | Check the USB data cable and board connection, then refresh |
| Port cannot open | Close the Serial Monitor or another program using it |
| Bytes arrive but no samples | Check serial diagnostics; upload IMU CSV firmware |
| `NO DATA` | Check firmware errors, USB, calibration, and selected port; reconnect |
| Move never finishes | Return gyro/energy to rest long enough to confirm it |
| CSV unavailable | Stop the recording first |

From the project root, verify installation without starting a server and run tests:

```powershell
.\.venv\Scripts\python.exe dashboard/launch.py --check
.\.venv\Scripts\python.exe -m unittest discover -s dashboard/tests -v
```

Tests cover malformed data, filtering, movement timing, clock rollover, bounded
history, recordings, serial failures, and HTTP/SSE integration with simulated input.

## Interpretation limits

**Avatar position is a visualization of IMU movement, not measured absolute position.**

Roll affects lateral position and lean; pitch slightly changes scale; Gyro Z
produces a bounded twist that returns toward center when rotation stops. There is no double integration,
measured climbing height, or stable absolute yaw. Shaking or rotating can count as
a prototype move. The FSR test is separate and uses the Serial Monitor.

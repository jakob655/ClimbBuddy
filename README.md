# BelayBuddy

BelayBuddy is a sensor prototype for exploring climbing-related movement. It
combines Arduino firmware for an **ESP32-C3 SuperMini Plus** with a local
**Python and browser dashboard**. The firmware reads either an MPU6050 motion
sensor or a force-sensitive resistor (FSR). The dashboard visualizes motion,
detects simple movement cycles, and records samples as CSV files.

You can try the dashboard without hardware: simulation starts automatically.
The current firmware configuration uses `IMU_TEST` with CSV output. Device
messages and parts of the interface are in German; this guide uses the visible
control names where needed.

## Features and interpretation

- MPU6050 acquisition at a target 100 Hz, gyroscope bias calibration, axis
  filtering, roll/pitch estimates, and basic motion classification.
- USB serial input at 115200 baud, plus a simulation that uses the same pipeline.
- An animated climbing avatar, live charts, recent moves, and CSV recording.
- A separate FSR test with a relative load percentage at about 50 Hz.

The avatar visualizes movement; it does not measure absolute position or height.
Demo climbing progress is move count times 4%, capped at 100%. Roll and pitch
are estimated from acceleration, so fast motion can distort them. The MPU6050
has no magnetometer; the displayed twist is a bounded visual effect, not an
absolute yaw measurement. Move detection is a prototype heuristic, and shaking
or rotating the sensor can also trigger it.

## Requirements

For the dashboard, install **Python 3.10 or later** and use a browser supporting
SVG, Canvas, and server-sent events. Python 3.11 or later is preferable for
simulation timing on Windows. The only required Python package is `pyserial`;
the launcher can install it from
[dashboard/requirements.txt](dashboard/requirements.txt).

For real sensor data, you also need:

- VS Code with the PlatformIO IDE extension, or the PlatformIO CLI.
- An ESP32-C3 SuperMini Plus and a USB **data** cable.
- An MPU6050 / GY-521 module, or an FSR and a 10 kOhm resistor for the pressure test.

[platformio.ini](platformio.ini) pins `espressif32 @ 6.12.0`, the Arduino framework,
and `Adafruit MPU6050 @ 2.2.6`. The `esp32-c3-devkitm-1` profile is used as a
compatible 4 MB ESP32-C3 profile. Check GPIO labels on your particular board.

## Quick start: no hardware required

Run commands from the project root, which contains `platformio.ini`.

On Windows, double-click **`Dashboard starten.cmd`** in File Explorer, or run:

```powershell
python dashboard/launch.py
```

The launcher creates a project-local `.venv` if necessary, checks dependencies,
selects a free local port between **8501 and 8520**, and opens the browser. Initial
setup may need internet access for package installation. Use the address printed
in the terminal. Keep the terminal open and stop the server with **Ctrl+C**.

Simulation begins automatically:

1. Change **Roll** and **Pitch** to tilt and move the avatar.
2. Change **Gyro Z** for rotation and **Movement Energy** for stronger movement.
3. Click **Reset**, then **Simulate Move**. After about two seconds, expect one
   detected move and 4% demo progress.
4. Click **Start Recording**, generate motion, then **Stop Recording** and
   **CSV herunterladen** to download the samples.

Recordings also remain under `dashboard/recordings/`. For controls, filtering,
session behavior, and troubleshooting, see the
[dashboard guide](dashboard/README.md).

### Manual dashboard installation

To manage installation and the port yourself:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r dashboard/requirements.txt
.\.venv\Scripts\python.exe dashboard/app.py --port 8501 --open-browser
```

On macOS or Linux, use `.venv/bin/python` instead of
`.\.venv\Scripts\python.exe`. The server listens on `127.0.0.1`. Manual startup
uses port 8501 unless you set `--port`; omit `--open-browser` if you prefer to
open the printed address yourself.

## Wire the MPU6050

Disconnect USB before changing wiring. Use **3.3 V** for this setup.

| ESP32-C3 SuperMini Plus | MPU6050 / GY-521 |
| --- | --- |
| 3V3 | VCC |
| GND | GND |
| GPIO4 | SDA |
| GPIO5 | SCL |
| GND | AD0 |

Leave **INT**, **XDA**, and **XCL** unconnected. AD0 connected to GND selects
I2C address `0x68`. GPIO4/GPIO5 are GPIO numbers, not header positions. An FSR
may remain connected; `IMU_TEST` does not initialize or read it.

Use short wires. SDA and SCL need pull-ups to **3V3**; many GY-521 modules already
include them. If yours does not, add approximately 4.7 kOhm on each line. The
configured I2C clock is 400 kHz.

## Build and upload firmware

Open [src/main.cpp](src/main.cpp). For dashboard use, keep:

```cpp
const TestMode ACTIVE_TEST = IMU_TEST;
constexpr bool IMU_CSV_OUTPUT = true;
```

1. Open the project folder in VS Code.
2. Under **PlatformIO > Project Tasks > esp32c3_supermini > General**, run
   **Build** and wait for **SUCCESS**. No connected board is needed to build.
3. Connect the board using a USB data cable.
4. Close the Serial Monitor and disconnect the dashboard from the serial port.
5. Run **Upload** and wait for **SUCCESS**. Building alone does not upload firmware.

Equivalent commands in a PlatformIO terminal:

```powershell
pio run -e esp32c3_supermini
pio run -e esp32c3_supermini --target upload
pio device list
```

The first build may download the toolchain, platform, and libraries. If multiple
ports are available, identify the board with `pio device list` or Windows Device
Manager. Set `upload_port` and `monitor_port` in `platformio.ini` if necessary.

If upload stalls at `Connecting...`, hold **BOOT**, press and release **RESET**,
then release **BOOT**. Select the newly visible port and retry. Press **RESET**
after upload if needed. The port may change between bootloader and normal mode.

## Calibrate and view real data

1. Place the MPU6050 flat with **+Z pointing up** and keep it completely still.
2. Press **RESET** and allow roughly **8 seconds** for startup and calibration.
3. In the dashboard, choose **Data Source > Serial / ESP32**, click
   **Ports aktualisieren**, select the board's port, and click **Connect**.
4. Check that the sample count increases, then tilt and rotate the sensor.
5. To finish a move, stop moving and hold still for about a second.
6. Click **Disconnect** before uploading firmware or opening the Serial Monitor.

Startup waits up to three seconds for USB serial, gives three seconds to prepare,
then averages 200 gyroscope samples over about two seconds. Obvious motion or
excessive variation causes a calibration error. Slow, steady rotation can escape
this check, so always calibrate at rest. Offsets remain in RAM and are recalculated
on every reset. Calibration does not zero roll/pitch or calibrate acceleration.

With a flat, stationary sensor after calibration, expect approximately:

| Signal | Expected value |
| --- | --- |
| `ax`, `ay` | 0 m/s^2 |
| `az`, `accelerationMagnitude` | +9.81 m/s^2 |
| `gx`, `gy`, `gz`, `gyroMagnitude` | 0 deg/s |
| `roll`, `pitch` | 0 degrees |
| Firmware `state` | Usually `STILL` |
| `sampleHz` | About 100 after the first measurement window |

Acceleration includes gravity. Small noise and bias are normal. With +Z pointing
down, `az` is near -9.81 m/s^2 and roll can be near +/-180 degrees.

### Serial output and settings

To inspect output directly, close the dashboard's serial connection and run:

```powershell
pio device monitor --baud 115200
```

Press **RESET** with the monitor open to see startup messages. The firmware keeps
running without a monitor. CSV columns are:

```text
timestamp,ax,ay,az,gx,gy,gz,roll,pitch,accelerationMagnitude,gyroMagnitude,movementEnergy,state,sampleHz
```

`timestamp` is the device's `millis()` counter in milliseconds, not Unix time.
Acceleration uses m/s^2, gyroscope data uses deg/s, and roll/pitch use degrees.
`movementEnergy = abs(accelerationMagnitude - 9.80665)` is a motion indicator in
m/s^2, not energy in joules. Firmware states are `STILL`, `MOVING`, and `ROTATING`;
the dashboard derives its own filtered status and move count.

Set `IMU_CSV_OUTPUT = false`, rebuild, and upload to get readable text blocks at
about 10 Hz. The dashboard requires CSV. Settings are grouped at the top of
`src/main.cpp`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `IMU_SAMPLE_INTERVAL_US` | 10000 us | Target 100 samples/s |
| `PRINT_INTERVAL_MS` | 100 ms | Text output interval |
| `LOW_PASS_ALPHA` | 0.20 | Axis smoothing; smaller responds more slowly |
| `STILL_ACCEL_THRESHOLD` | 0.35 m/s^2 | Allowed magnitude deviation from gravity for `STILL` |
| `STILL_GYRO_THRESHOLD` | 3 deg/s | Gyroscope magnitude limit for `STILL` |
| `ROTATION_THRESHOLD_DPS` | 30 deg/s | Gyroscope threshold for `ROTATING` |

The MPU6050 uses ranges of +/-4 g and +/-500 deg/s, a 21 Hz internal low-pass,
and a sample-rate divisor of 9. An additional exponential low-pass smooths all
six axes. The first reading initializes that filter directly.

## Use the separate FSR pressure test

Change the mode in `src/main.cpp`, then rebuild and upload:

```cpp
const TestMode ACTIVE_TEST = PRESSURE_TEST;
```

Only the FSR is initialized in this mode; the MPU6050 may be absent. Pressure
output is intended for the Serial Monitor and is not consumed by the dashboard.

With USB disconnected, wire the FSR and 10 kOhm resistor as a voltage divider:

```text
ESP32 3V3
    |
   FSR
    |
    +---------- GPIO0 (ADC)
    |
  10 kOhm
    |
ESP32 GND
```

An ordinary two-terminal resistive FSR has no polarity. Use 3.3 V for the divider.
Leave the FSR unloaded and press **RESET**. Startup averages about two seconds of
readings to set the baseline. Open the Serial Monitor at 115200 baud. Example:

```text
Raw: 900 | Voltage: 0.60 V | Pressure: 25 %
```

Values illustrate the format. `Raw` is the current unfiltered 12-bit ADC reading
(0-4095). `Voltage` and `Pressure` average the last eight samples at about 50 Hz.
Applying pressure normally increases the readings; releasing should return the
percentage near zero.

```text
Pressure = clamp((average_raw - baseline) / (MAX_PRESSURE_RAW - baseline) * 100, 0, 100)
```

`MAX_PRESSURE_RAW` defaults to **3500** and is an absolute ADC reference. Adjust
it to the stable reading under a chosen reference load. The percentage is relative
load, not force in newtons. A baseline at or above the reference produces an error
and leaves pressure at zero.

Voltage uses a separate calibrated `analogReadMilliVolts()` measurement, not
`Raw / 4095 * 3.3`. With 11 dB attenuation, the ESP32-C3's specified ADC range
is approximately 0-2.5 V. The divider can exceed that range under strong pressure.
Near saturation, stronger loads cannot be distinguished and voltage is unreliable.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| No serial port | USB data cable, USB connection, board driver, and Device Manager |
| Busy port during upload or connect | Close the Serial Monitor or disconnect the dashboard |
| `MPU6050 NICHT gefunden` | 3V3, common GND, GPIO4/SDA, GPIO5/SCL, AD0/GND, and module type |
| `I2C-Lesefehler` | Power, short wires, reliable contacts, and pull-ups |
| `Kalibrierung zu unruhig` | Keep the sensor still and press **RESET** |
| Open port but no dashboard samples | Upload `IMU_TEST` with CSV enabled; inspect serial diagnostics |
| `NO DATA` | No valid samples for over two seconds; check connection and firmware |
| Gyroscope far from zero at rest | Reset and recalibrate without moving the sensor |
| FSR raw value stays at 0 or near 4095 | Check the measurement node and resistor to GND |

An IMU error stops processing and repeats its message every two seconds. Fix the
cause and press **RESET**. I2C acknowledgment and finite-value checks cannot verify
every internal bus transaction. For persistent bus errors, check wiring and try
reducing `I2C_CLOCK_HZ` from 400000 to 100000.

## Project structure and checks

```text
src/main.cpp                  ESP32 firmware: IMU and FSR modes
platformio.ini                Board, serial, toolchain, and library settings
Dashboard starten.cmd         Windows dashboard entry point
dashboard/
    app.py                    HTTP server, actions, and live event stream
    launch.py                 Environment setup and available-port selection
    serial_reader.py          Background serial acquisition and simulation
    sensor_state.py           CSV, filtering, detection, history, recording
    visualization.py          Persistent SVG wall and avatar
    static/                   HTML, CSS, browser animation, and Canvas charts
    tests/                    Pipeline and HTTP/stream integration tests
```

After installing dashboard dependencies, run these hardware-independent checks:

```powershell
.\.venv\Scripts\python.exe dashboard/launch.py --check
.\.venv\Scripts\python.exe -m unittest discover -s dashboard/tests -v
pio run -e esp32c3_supermini
```

`--check` verifies the environment without starting a server. Tests cover parsing,
filtering, timing, recording, serial failures, and HTTP/event streaming. Firmware
upload and physical sensor behavior still need to be checked on the board.

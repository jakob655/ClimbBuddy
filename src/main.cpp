#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <math.h>

// ---------------- Alle Einstellungen an einer Stelle ----------------
enum TestMode {
    PRESSURE_TEST,
    IMU_TEST
};

// Nur diese Zeile aendern, dann neu kompilieren und hochladen:
const TestMode ACTIVE_TEST = IMU_TEST;

// Drucksensor: 3V3 -- FSR -- Messpunkt/GPIO0 -- 10 kOhm -- GND.
const int PRESSURE_PIN = 0; // ESP32-C3: GPIO0 = ADC1, Kanal 0
constexpr uint32_t SAMPLE_INTERVAL_MS = 20; // 50 Messungen/s
constexpr size_t NUM_SAMPLES = 8; // gleitender Mittelwert der letzten Messungen
constexpr int MAX_PRESSURE_RAW = 3500; // absoluter ADC-Wert fuer relative 100 %
constexpr uint32_t PRESSURE_CALIBRATION_MS = 2000;
constexpr int ADC_MAX_RAW = 4095; // 12 Bit

static_assert(NUM_SAMPLES > 0, "Mindestens ein Sample erforderlich");
static_assert(SAMPLE_INTERVAL_MS > 0 && SAMPLE_INTERVAL_MS <= PRESSURE_CALIBRATION_MS,
              "Drucksensor-Messintervall pruefen");
static_assert(MAX_PRESSURE_RAW > 0 && MAX_PRESSURE_RAW <= ADC_MAX_RAW,
              "MAX_PRESSURE_RAW muss zwischen 1 und 4095 liegen");

// MPU6050: Diese Einstellungen werden nur im IMU_TEST verwendet.
constexpr int SDA_PIN = 4; // GPIO-Nummer, mit Beschriftung am Board abgleichen!
constexpr int SCL_PIN = 5;
constexpr uint8_t MPU6050_ADDRESS = 0x68; // AD0 = GND; bei AD0 = 3V3: 0x69
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint32_t I2C_CLOCK_HZ = 400000;
constexpr uint16_t I2C_TIMEOUT_MS = 20;

constexpr uint32_t IMU_SAMPLE_INTERVAL_US = 10000; // 100 Hz
constexpr uint32_t PRINT_INTERVAL_MS = 100;   // lesbare Ausgabe: 10 Hz
// true: CSV fuer dashboard/app.py mit jeder IMU-Messung; false: bisherige Textbloecke.
// PRESSURE_TEST bleibt davon unabhaengig.
constexpr bool IMU_CSV_OUTPUT = true;
constexpr uint32_t SERIAL_WAIT_MS = 3000;
constexpr uint32_t CALIBRATION_PREPARE_MS = 3000;
constexpr uint32_t CALIBRATION_DURATION_MS = 2000;

constexpr float GRAVITY_MS2 = 9.80665f;
constexpr float RAD_TO_DEG_FACTOR = 57.2957795f;
constexpr float LOW_PASS_ALPHA = 0.20f; // 0 < alpha <= 1; kleiner = ruhiger
constexpr float STILL_ACCEL_THRESHOLD = 0.35f; // |accMagnitude - g| in m/s^2
constexpr float STILL_GYRO_THRESHOLD = 3.0f;   // Gyro-Betrag in Grad/s
constexpr float ROTATION_THRESHOLD_DPS = 30.0f; // Gyro-Gesamtbetrag in Grad/s
constexpr float CALIBRATION_MAX_GYRO_DPS = 10.0f;
constexpr float CALIBRATION_MAX_ACCEL_DEVIATION = 1.0f; // m/s^2
constexpr float CALIBRATION_MAX_GYRO_STDDEV_DPS = 1.5f;

// Roll/Pitch beziehen sich auf die aufgedruckten Sensorachsen.
// Fuer die Versuche: +X nach vorne, +Y nach links, +Z nach oben.

static_assert(LOW_PASS_ALPHA > 0 && LOW_PASS_ALPHA <= 1, "Alpha: 0 < alpha <= 1");
static_assert(STILL_ACCEL_THRESHOLD > 0 && STILL_GYRO_THRESHOLD > 0,
              "Stillstandsschwellen muessen positiv sein");
static_assert(ROTATION_THRESHOLD_DPS > STILL_GYRO_THRESHOLD,
              "Rotationsschwelle muss ueber der Stillstandsschwelle liegen");
static_assert(IMU_SAMPLE_INTERVAL_US > 0 && PRINT_INTERVAL_MS > 0,
              "IMU-Mess- und Ausgabeintervall muessen positiv sein");

struct SensorData {
    float ax = 0, ay = 0, az = 0; // m/s^2, einschliesslich Schwerkraft
    float gx = 0, gy = 0, gz = 0; // Grad/s, nach Gyro-Offsetkorrektur
};

struct DerivedData {
    float accelerationMagnitude = 0;
    float gyroMagnitude = 0;
    float roll = 0, pitch = 0; // Grad
};

enum class MovementState {
    STILL, MOVING, ROTATING
};

Adafruit_MPU6050 mpu;
SensorData gyroOffsets;
SensorData filteredData;
bool filterInitialized = false;
MovementState currentState = MovementState::MOVING;
uint32_t lastSampleUs = 0;
uint32_t lastPrintMs = 0;
uint32_t rateWindowMs = 0;
uint32_t samplesInWindow = 0;
float actualSampleHz = 0;

float pressureBaseline = 0;
int pressureSamples[NUM_SAMPLES] = {};
uint32_t pressureMillivoltSamples[NUM_SAMPLES] = {};
size_t pressureSampleIndex = 0;
size_t pressureSampleCount = 0;
uint32_t lastPressureSampleMs = 0;

// ---------------- Drucksensor ----------------
int readPressureRaw() {
    return analogRead(PRESSURE_PIN);
}

void calibratePressureBaseline() {
    Serial.println("Baseline: Drucksensor fuer etwa 2 Sekunden UNBELASTET lassen.");
    constexpr uint32_t sampleCount = PRESSURE_CALIBRATION_MS / SAMPLE_INTERVAL_MS;
    uint64_t total = 0;
    for (uint32_t i = 0; i < sampleCount; ++i) {
        total += readPressureRaw();
        delay(SAMPLE_INTERVAL_MS);
    }
    pressureBaseline = static_cast<float>(total) / sampleCount;
    Serial.printf("Baseline: %.1f | MAX_PRESSURE_RAW: %d\n",
                  pressureBaseline, MAX_PRESSURE_RAW);
    if (pressureBaseline >= MAX_PRESSURE_RAW) {
        Serial.println("FEHLER: Baseline >= MAX_PRESSURE_RAW. Sensor entlasten und RESET");
        Serial.println("druecken oder MAX_PRESSURE_RAW passend einstellen. Pressure bleibt 0 %.");
    }
}

void initPressureSensor() {
    // Hier KEIN Wire.begin() und KEIN mpu.begin(): MPU darf fehlen.
    pinMode(PRESSURE_PIN, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(PRESSURE_PIN, ADC_11db);
    pressureSampleIndex = pressureSampleCount = 0;
    Serial.printf("Drucksensor bereit: GPIO%d, ca. %lu Hz, Mittelwert ueber %u Samples.\n",
                  PRESSURE_PIN, static_cast<unsigned long>(1000 / SAMPLE_INTERVAL_MS),
                  static_cast<unsigned int>(NUM_SAMPLES));
    Serial.println("Pressure ist ein relativer Belastungswert, KEINE Kraft in Newton.");
    calibratePressureBaseline();
    lastPressureSampleMs = millis();
}

float calculatePressurePercent(float smoothedRaw) {
    // 0 % = unbelastete Baseline; 100 % = konfigurierbarer absoluter Rohwert.
    const float range = MAX_PRESSURE_RAW - pressureBaseline;
    if (range <= 0) {
        return 0; // ungueltige Kalibrierung: keine Division durch null
    }
    const float percent = (smoothedRaw - pressureBaseline) * 100.0f / range;
    return fminf(100.0f, fmaxf(0.0f, percent));
}

void printPressureData(int raw, float voltage, float pressurePercent) {
    Serial.printf("Raw: %d | Voltage: %.2f V | Pressure: %.0f %%\n",
                  raw, voltage, pressurePercent);
}

void updatePressureSensor() {
    const uint32_t nowMs = millis();
    const uint32_t elapsedMs = static_cast<uint32_t>(nowMs - lastPressureSampleMs);
    if (elapsedMs < SAMPLE_INTERVAL_MS) {
        return;
    }
    lastPressureSampleMs += SAMPLE_INTERVAL_MS;
    if (elapsedMs >= 2 * SAMPLE_INTERVAL_MS) {
        lastPressureSampleMs = nowMs; // keine verspaeteten Messungen nachholen
    }

    const int raw = readPressureRaw();
    // Die ESP32-ADC-Kennlinie entspricht NICHT einfach 0..4095 = 0..3,3 V.
    // Diese API liefert eine separate, kalibrierte Spannungsmessung in mV.
    // Beim C3 mit 11 dB ist der spezifizierte Bereich etwa 0..2,5 V.
    // Bei Saettigung kann die wirkliche Teilerspannung hoeher sein.
    pressureSamples[pressureSampleIndex] = raw;
    pressureMillivoltSamples[pressureSampleIndex] = analogReadMilliVolts(PRESSURE_PIN);
    pressureSampleIndex = (pressureSampleIndex + 1) % NUM_SAMPLES;
    if (pressureSampleCount < NUM_SAMPLES) {
        ++pressureSampleCount;
    }

    float rawSum = 0;
    float millivoltSum = 0;
    for (size_t i = 0; i < pressureSampleCount; ++i) {
        rawSum += pressureSamples[i];
        millivoltSum += pressureMillivoltSamples[i];
    }
    const float smoothedRaw = rawSum / pressureSampleCount;
    const float voltage = millivoltSum / pressureSampleCount / 1000.0f;
    if (Serial) {
        // Raw ist ungefiltert; Voltage und Pressure sind geglaettet.
        printPressureData(raw, voltage, calculatePressurePercent(smoothedRaw));
    }
}

// ---------------- MPU6050 ----------------

float magnitude(float x, float y, float z) {
    return sqrtf(x * x + y * y + z * z);
}

[[noreturn]] void stopWithError(const char* message) {
    // Bei einem Fehler werden KEINE alten Werte weiter klassifiziert.
    while (true) {
        Serial.print("# FEHLER: ");
        Serial.println(message);
        Serial.println("# Ursache beheben, Sensor still ablegen und RESET drücken.");
        delay(2000);
    }
}

bool sensorResponds() {
    Wire.beginTransmission(MPU6050_ADDRESS);
    return Wire.endTransmission() == 0;
}

bool initIMU() {
    Serial.println("Initialisiere I2C...");
    if (!Wire.begin(SDA_PIN, SCL_PIN)) {
        stopWithError("I2C-Initialisierung fehlgeschlagen. SDA/SCL pruefen.");
    }
    if (!Wire.setClock(I2C_CLOCK_HZ)) {
        stopWithError("I2C-Takt konnte nicht eingestellt werden. SDA/SCL pruefen.");
    }
    Wire.setTimeOut(I2C_TIMEOUT_MS);

    Serial.println("Suche MPU6050...");
    if (!mpu.begin(MPU6050_ADDRESS, &Wire)) {
        stopWithError("MPU6050 NICHT gefunden.\n"
                      "Prüfe:\n- 3.3V\n- GND\n- SDA GPIO4\n- SCL GPIO5\n"
                      "- AD0 an GND\n- Verkabelung");
    }

    mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ); // eingebauter Low-Pass
    // Bei aktiviertem DLPF: 1000 Hz / (1 + 9) = 100 Hz Sensor-Ausgaberate.
    mpu.setSampleRateDivisor(9);
    delay(100); // Filter nach Konfiguration einschwingen lassen
    Serial.println("MPU6050 gefunden");
    Serial.printf("I2C-Adresse: 0x%02X\n", MPU6050_ADDRESS);
    Serial.printf("SDA: GPIO%d | SCL: GPIO%d\n", SDA_PIN, SCL_PIN);
    return true;
}

bool readIMU(SensorData& data) {
    // getEvent() der Library meldet nicht jeden Busfehler zuverlaessig.
    // Zusaetzliches ACK pruefen, damit z.B. ein abgezogener Sensor auffaellt.
    // Dies ist keine vollstaendige Pruefung jeder einzelnen I2C-Transaktion.
    if (!sensorResponds()) {
        return false;
    }
    sensors_event_t acceleration{}, gyro{}, temperature{};
    if (!mpu.getEvent(&acceleration, &gyro, &temperature)) {
        return false;
    }

    data.ax = acceleration.acceleration.x;
    data.ay = acceleration.acceleration.y;
    data.az = acceleration.acceleration.z;
    // Adafruit liefert rad/s. Fuer Schwellen und Ausgabe in Grad/s umrechnen.
    data.gx = gyro.gyro.x * RAD_TO_DEG_FACTOR - gyroOffsets.gx;
    data.gy = gyro.gyro.y * RAD_TO_DEG_FACTOR - gyroOffsets.gy;
    data.gz = gyro.gyro.z * RAD_TO_DEG_FACTOR - gyroOffsets.gz;
    return isfinite(data.ax) && isfinite(data.ay) && isfinite(data.az) &&
           isfinite(data.gx) && isfinite(data.gy) && isfinite(data.gz);
}

void calibrateIMU() {
    Serial.println("Sensor jetzt still liegen lassen.");
    Serial.println("Kalibrierung startet in 3 Sekunden.");
    delay(CALIBRATION_PREPARE_MS);
    Serial.println("Kalibrierung START: für etwa 2 Sekunden NICHT bewegen.");

    gyroOffsets = SensorData{};
    float sumX = 0, sumY = 0, sumZ = 0;
    float sumSquares = 0;
    bool movementDetected = false;
    constexpr uint32_t sampleCount = CALIBRATION_DURATION_MS * 1000UL / IMU_SAMPLE_INTERVAL_US;
    static_assert(sampleCount > 0, "Kalibrierungsdauer zu kurz");
    uint32_t previousUs = micros();
    for (uint32_t i = 0; i < sampleCount; ++i) {
        while (static_cast<uint32_t>(micros() - previousUs) < IMU_SAMPLE_INTERVAL_US) {
            delay(1);
        }
        previousUs += IMU_SAMPLE_INTERVAL_US;
        SensorData sample;
        if (!readIMU(sample)) {
            stopWithError("I2C-Lesefehler waehrend der Kalibrierung.");
        }
        sumX += sample.gx;
        sumY += sample.gy;
        sumZ += sample.gz;
        sumSquares += sample.gx * sample.gx + sample.gy * sample.gy + sample.gz * sample.gz;
        if (magnitude(sample.gx, sample.gy, sample.gz) > CALIBRATION_MAX_GYRO_DPS ||
            fabsf(magnitude(sample.ax, sample.ay, sample.az) - GRAVITY_MS2) >
                CALIBRATION_MAX_ACCEL_DEVIATION) {
            movementDetected = true;
        }
    }

    gyroOffsets.gx = sumX / sampleCount;
    gyroOffsets.gy = sumY / sampleCount;
    gyroOffsets.gz = sumZ / sampleCount;
    const float variance = sumSquares / sampleCount -
        (gyroOffsets.gx * gyroOffsets.gx + gyroOffsets.gy * gyroOffsets.gy +
         gyroOffsets.gz * gyroOffsets.gz);
    const float stddev = sqrtf(fmaxf(variance, 0.0f));
    if (movementDetected || stddev > CALIBRATION_MAX_GYRO_STDDEV_DPS) {
        stopWithError("Kalibrierung zu unruhig. Sensor still ablegen und RESET druecken.");
    }
    Serial.println("Kalibrierung abgeschlossen.");
    Serial.printf("Messungen: %lu\n",
                  static_cast<unsigned long>(sampleCount));
    Serial.printf("Gyro Offset X: %.3f °/s\nGyro Offset Y: %.3f °/s\nGyro Offset Z: %.3f °/s\n",
                  gyroOffsets.gx, gyroOffsets.gy, gyroOffsets.gz);
    filterInitialized = false;
    // Langsame, gleichmaessige Bewegung kann diese einfache Pruefung uebersehen.
    // Die Kalibrierung deshalb immer wirklich im Stillstand durchfuehren.
}

void filterIMU(const SensorData& raw) {
    if (!filterInitialized) {
        filteredData = raw; // nicht mit Null starten: az enthaelt etwa 1 g
        filterInitialized = true;
        return;
    }
    // Exponentieller Low-Pass: alt + alpha * (neu - alt).
    filteredData.ax += LOW_PASS_ALPHA * (raw.ax - filteredData.ax);
    filteredData.ay += LOW_PASS_ALPHA * (raw.ay - filteredData.ay);
    filteredData.az += LOW_PASS_ALPHA * (raw.az - filteredData.az);
    filteredData.gx += LOW_PASS_ALPHA * (raw.gx - filteredData.gx);
    filteredData.gy += LOW_PASS_ALPHA * (raw.gy - filteredData.gy);
    filteredData.gz += LOW_PASS_ALPHA * (raw.gz - filteredData.gz);
}

DerivedData calculateIMUValues(const SensorData& data) {
    DerivedData result;
    result.accelerationMagnitude = magnitude(data.ax, data.ay, data.az);
    result.gyroMagnitude = magnitude(data.gx, data.gy, data.gz);
    // Neigung nur aus Schwerkraft: bei schnellen Bewegungen ungenauer.
    result.roll = atan2f(data.ay, data.az) * RAD_TO_DEG_FACTOR;
    result.pitch = atan2f(-data.ax, sqrtf(data.ay * data.ay + data.az * data.az)) *
                   RAD_TO_DEG_FACTOR;
    return result;
}

MovementState classifyIMUState(const DerivedData& derived) {
    // Drehung um jede Sensorachse zaehlt; keine Richtungs- oder Zugerkennung.
    if (derived.gyroMagnitude > ROTATION_THRESHOLD_DPS) {
        return MovementState::ROTATING;
    }
    if (fabsf(derived.accelerationMagnitude - GRAVITY_MS2) < STILL_ACCEL_THRESHOLD &&
        derived.gyroMagnitude < STILL_GYRO_THRESHOLD) {
        return MovementState::STILL;
    }
    return MovementState::MOVING;
}

const char* stateName(MovementState state) {
    switch (state) {
        case MovementState::STILL: return "STILL";
        case MovementState::MOVING: return "MOVING";
        case MovementState::ROTATING: return "ROTATING";
    }
    return "UNKNOWN";
}

void printIMUData(const SensorData& data, const DerivedData& derived) {
    Serial.println("\n--- IMU ---");
    Serial.printf("ax: %.3f m/s²\nay: %.3f m/s²\naz: %.3f m/s²\n\n",
                  data.ax, data.ay, data.az);
    Serial.printf("gx: %.3f °/s\ngy: %.3f °/s\ngz: %.3f °/s\n\n",
                  data.gx, data.gy, data.gz);
    Serial.printf("Roll: %.2f°\nPitch: %.2f°\n\n", derived.roll, derived.pitch);
    Serial.printf("Acc magnitude: %.3f m/s²\nGyro magnitude: %.3f °/s\n",
                  derived.accelerationMagnitude, derived.gyroMagnitude);
    Serial.printf("State: %s | sampleHz: %.1f\n", stateName(currentState), actualSampleHz);
}

void printIMUCSV(uint32_t timestamp, const SensorData& data, const DerivedData& derived) {
    // movementEnergy ist nur |Beschleunigungsbetrag - g| in m/s^2,
    // keine physikalische Energie und keine Positions-/Hoehenberechnung.
    const float movementEnergy = fabsf(derived.accelerationMagnitude - GRAVITY_MS2);
    Serial.printf("%lu,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f,%.2f,%.2f,%.3f,%.2f,%.3f,%s,%.1f\n",
                  static_cast<unsigned long>(timestamp), data.ax, data.ay, data.az,
                  data.gx, data.gy, data.gz, derived.roll, derived.pitch,
                  derived.accelerationMagnitude, derived.gyroMagnitude, movementEnergy,
                  stateName(currentState), actualSampleHz);
}

void updateIMU() {
    const uint32_t nowUs = micros();
    const uint32_t elapsedUs = static_cast<uint32_t>(nowUs - lastSampleUs);
    if (elapsedUs < IMU_SAMPLE_INTERVAL_US) {
        return;
    }
    // Zeitraster halten; bei Verzoegerung keine alten Messungen nachholen.
    lastSampleUs += IMU_SAMPLE_INTERVAL_US;
    if (elapsedUs >= 2 * IMU_SAMPLE_INTERVAL_US) {
        lastSampleUs = nowUs;
    }

    SensorData raw;
    if (!readIMU(raw)) {
        stopWithError("MPU6050/I2C-Lesefehler. Sensor antwortet nicht oder Daten ungueltig.");
    }
    filterIMU(raw);
    const DerivedData derived = calculateIMUValues(filteredData);
    const uint32_t nowMs = millis();
    currentState = classifyIMUState(derived);

    ++samplesInWindow;
    const uint32_t rateElapsedMs = static_cast<uint32_t>(nowMs - rateWindowMs);
    if (rateElapsedMs >= 1000) {
        actualSampleHz = samplesInWindow * 1000.0f / rateElapsedMs;
        samplesInWindow = 0;
        rateWindowMs = nowMs;
    }
    // Serial-Ausgabe nur bei verbundenem Monitor; kein Warten auf Verbindung.
    if (Serial && IMU_CSV_OUTPUT) {
        printIMUCSV(nowMs, filteredData, derived);
    } else if (Serial && static_cast<uint32_t>(nowMs - lastPrintMs) >= PRINT_INTERVAL_MS) {
        lastPrintMs = nowMs;
        printIMUData(filteredData, derived);
    }
}

// ---------------- Gemeinsamer Programmeinstieg ----------------
void setup() {
    Serial.begin(SERIAL_BAUD);
    const uint32_t startMs = millis();
    while (!Serial && static_cast<uint32_t>(millis() - startMs) < SERIAL_WAIT_MS) {
        delay(10); // ohne angeschlossenen Monitor trotzdem weiterlaufen
    }
    switch (ACTIVE_TEST) {
        case PRESSURE_TEST:
            Serial.println("=== Climbing Sensor Prototype ===");
            Serial.println("Active mode: PRESSURE_TEST");
            initPressureSensor();
            break;
        case IMU_TEST:
            Serial.println("=== BelayBuddy IMU Test ===");
            if (!initIMU()) {
                return;
            }
            calibrateIMU();
            if (IMU_CSV_OUTPUT) {
                Serial.println("timestamp,ax,ay,az,gx,gy,gz,roll,pitch,accelerationMagnitude,gyroMagnitude,movementEnergy,state,sampleHz");
            }
            lastSampleUs = micros();
            lastPrintMs = rateWindowMs = millis();
            break;
    }
}

void loop() {
    switch (ACTIVE_TEST) {
        case PRESSURE_TEST:
            updatePressureSensor();
            break;
        case IMU_TEST:
            updateIMU();
            break;
    }
    delay(1); // CPU zwischen Messungen kurz freigeben
}

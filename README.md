# BelayBuddy: erster MPU6050-Funktionstest

PlatformIO-Projekt für **ESP32-C3 SuperMini Plus + MPU6050/GY-521 + FSR**,
C++ mit Arduino. **Aktiv ist jetzt `IMU_TEST`.** Der FSR wird in diesem Modus
weder initialisiert noch gelesen. Sein bestehender Code bleibt vollständig erhalten.

Der Test prüft I2C und plausible Beschleunigungs- und Gyroskopdaten.
Er liest etwa **100-mal pro Sekunde** und gibt standardmäßig CSV für das Dashboard
aus. Der weiterhin verfügbare Textmodus zeigt etwa **10 Messblöcke pro Sekunde**.
Roll und Pitch werden einfach aus der gefilterten Beschleunigung berechnet.
Es gibt nur die Plausibilitätszustände `STILL`, `MOVING` und `ROTATING`.
Kletteranalyse, Positionsberechnung, doppelte Integration, Machine Learning und
Bluetooth sind nicht Bestandteil dieses Tests.

## Live-Dashboard (Python / Browser)

Das separate [Dashboard](dashboard/README.md) bietet USB-CSV, Simulation ohne
Hardware, eine schematische Kletterwand, Live-Charts, Move-Erkennung und CSV-Recording.
Die Oberfläche empfängt kontinuierlich Sensordaten und animiert die Figur direkt
im Browser. Streamlit wird dafür nicht mehr verwendet.
Installation und beide Testabläufe stehen in **[dashboard/README.md](dashboard/README.md)**.
Unter Windows im Windows-Explorer **`Dashboard starten.cmd` doppelklicken**;
der Starter verwendet automatisch die passende Python-Umgebung und öffnet den Browser.

`IMU_CSV_OUTPUT = true` in `src/main.cpp` aktiviert jetzt CSV mit jeder IMU-Messung
(Ziel etwa 100 Hz). Für die unten beschriebenen lesbaren Messblöcke
`IMU_CSV_OUTPUT = false` setzen und erneut hochladen; diese Textausgabe bleibt bei
etwa 10 Hz. `PRESSURE_TEST` und dessen Ausgabe bleiben unverändert.
Die folgenden ursprünglichen IMU-Beispiele zeigen den **Textmodus**.

## 1. Welche Dateien angepasst wurden

- `src/main.cpp`: `ACTIVE_TEST = IMU_TEST`, Startmeldungen, I2C, Gyro-Kalibrierung,
  Low-Pass-Filter, Messwerte und einfache Zustände. Die vorhandenen
  Drucksensor-Funktionen, Einstellungen und Messvariablen sind unverändert.
- `README.md`: diese Anleitung und die weiterhin enthaltene Drucksensor-Anleitung.
- `platformio.ini` bleibt unverändert: Das Projekt verwendet bereits
  `adafruit/Adafruit MPU6050 @ 2.2.6`, `monitor_speed = 115200` und USB-CDC.
  PlatformIO kümmert sich um die Library-Abhängigkeiten. Das vorhandene kompatible
  Boardprofil `esp32-c3-devkitm-1` und die Plattformversion 6.12.0 bleiben erhalten.

## 2. MPU6050 anschließen

USB vor dem Verdrahten abziehen. **Für diesen Aufbau ausschließlich 3,3 V verwenden.**

| ESP32-C3 SuperMini Plus | MPU6050 / GY-521 |
| --- | --- |
| **3V3** | **VCC** |
| **GND** | **GND** |
| **GPIO4** | **SDA** |
| **GPIO5** | **SCL** |
| **GND** | **AD0** |

**INT, XDA und XCL bleiben unverbunden.** AD0 an GND legt die I2C-Adresse auf
`0x68` fest. Der FSR darf angeschlossen bleiben und wird bei `IMU_TEST` ignoriert.

GPIO4/GPIO5 sind die GPIO-Nummern auf dem Board, nicht die vierte/fünfte
Position der Stiftleiste. Die Beschriftung am konkreten Plus-Board abgleichen.
Kurze Leitungen und ein USB-Datenkabel verwenden.
I2C benötigt Pull-ups an SDA und SCL; diese sind auf vielen GY-521-Modulen
bereits vorhanden. Falls sie fehlen, je etwa 4,7 kOhm nach **3V3** ergänzen.
Auch die I2C-Leitungen dürfen hier nur 3,3-V-Pegel haben.

Die [Adafruit-Anschlussanleitung](https://learn.adafruit.com/mpu6050-6-dof-accelerometer-and-gyro/arduino)
erklärt die Verbindungen. Für den hier beschriebenen ESP32-Aufbau gilt die
3,3-V-Verdrahtung aus der Tabelle.

## 3. GPIO4 als SDA und GPIO5 als SCL im Code prüfen

`src/main.cpp` öffnen und oben diese Zeilen suchen:

```cpp
const TestMode ACTIVE_TEST = IMU_TEST;

constexpr int SDA_PIN = 4;
constexpr int SCL_PIN = 5;
constexpr uint8_t MPU6050_ADDRESS = 0x68;
```

Mit **Strg+F** nach `Wire.begin` suchen. In `initIMU()` steht:

```cpp
Wire.begin(SDA_PIN, SCL_PIN)
```

Der erste Parameter ist SDA, der zweite SCL. Damit verwendet dieser Test
ausdrücklich GPIO4 und GPIO5. Danach setzt `Wire.setClock(I2C_CLOCK_HZ)`
den I2C-Takt auf 400 kHz. Auch die Startausgabe bestätigt
`SDA: GPIO4 | SCL: GPIO5`. Die
[Espressif-I2C-Dokumentation](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/i2c.html)
beschreibt die Pinparameter und den Bustakt.

## 4. PlatformIO Build ausführen

1. In VS Code den Projektordner öffnen, in dem `platformio.ini` liegt.
2. Links auf das PlatformIO-Symbol klicken.
3. Unter **Project Tasks > esp32c3_supermini > General > Build** starten.
4. Im Terminal auf **SUCCESS** warten. Für Build muss kein Board angeschlossen sein.

Alternativ über **Strg+Umschalt+P > PlatformIO: New Terminal**:

```powershell
pio run -e esp32c3_supermini
```

Beim ersten Build können Downloads der Plattform und Libraries nötig sein.

## 5. Upload ausführen

1. Die Verdrahtung prüfen und das Board über ein USB-Datenkabel anschließen.
2. Einen geöffneten Serial Monitor schließen, damit der COM-Port frei ist.
3. **Project Tasks > esp32c3_supermini > General > Upload** starten.
4. Auf **SUCCESS** warten.

Alternativ im PlatformIO-Terminal:

```powershell
pio run -e esp32c3_supermini --target upload
```

Bei mehreren COM-Ports mit `pio device list` oder im Windows-Gerätemanager
den richtigen Port ermitteln. Falls nötig in `platformio.ini` die vorhandenen
Zeilen für `upload_port` und `monitor_port` aktivieren und `COM5` ersetzen.

Bleibt der Upload bei `Connecting...` hängen: BOOT gedrückt halten,
RESET kurz drücken und loslassen, dann BOOT loslassen. Den nun sichtbaren
Port wählen und Upload erneut starten. Anschließend RESET drücken.
Der COM-Port kann sich zwischen Bootloader und laufender Firmware ändern.

## 6. Serial Monitor mit 115200 Baud und Kalibrierung

**Project Tasks > esp32c3_supermini > General > Monitor** öffnen.
In `platformio.ini` steht bereits `monitor_speed = 115200`.

Alternativ:

```powershell
pio device monitor --baud 115200
```

Den Sensor flach auf den Tisch legen und bei geöffnetem Monitor **RESET**
drücken. So siehst du auch die Startmeldungen. Das Programm wartet beim Start
höchstens drei Sekunden auf die USB-Serial-Verbindung und läuft auch ohne
Monitor weiter.

Erwartete Startausgabe:

```text
=== BelayBuddy IMU Test ===
Initialisiere I2C...
Suche MPU6050...
MPU6050 gefunden
I2C-Adresse: 0x68
SDA: GPIO4 | SCL: GPIO5
Sensor jetzt still liegen lassen.
Kalibrierung startet in 3 Sekunden.
Kalibrierung START: für etwa 2 Sekunden NICHT bewegen.
Kalibrierung abgeschlossen.
Messungen: 200
Gyro Offset X: ...
Gyro Offset Y: ...
Gyro Offset Z: ...
```

Nach den drei Sekunden Vorbereitungszeit werden etwa zwei Sekunden lang
200 Gyro-Messungen gemittelt. **Währenddessen muss der Sensor vollständig
still liegen.** Grobe Bewegung oder stark schwankende Gyro-Werte erzeugen eine
Fehlermeldung; dann still ablegen und RESET drücken. Eine langsame gleichmäßige
Drehung kann diese einfache Prüfung übersehen.

Die Offsets sind in Grad/s und werden von jeder weiteren Gyro-Messung abgezogen.
Sie bleiben nur im RAM; jeder Neustart kalibriert erneut. Roll/Pitch werden
dabei nicht auf null gesetzt, und der Beschleunigungssensor wird nicht kalibriert.

## 7. Erwartete Werte bei still und flach liegendem Sensor

Das Modul so hinlegen, dass seine **+Z-Achse nach oben** zeigt.
Die Achsenmarkierungen auf dem Modul beachten.

| Wert | Erwartung |
| --- | --- |
| ax, ay | ungefähr 0 m/s² |
| az | ungefähr **+9,81 m/s²** |
| Acc magnitude | ungefähr **9,81 m/s²** |
| gx, gy, gz | nach der Kalibrierung ungefähr **0 °/s** |
| Gyro magnitude | nahe **0 °/s** |
| Roll, Pitch | ungefähr **0°** |
| State | üblicherweise **STILL**, abhängig von Bias und Schwellen |
| sampleHz | nach der ersten Messsekunde ungefähr **100** |

Kleines Rauschen und Sensorabweichungen sind normal. Die Beschleunigung enthält
die Schwerkraft: az soll in dieser Lage etwa 9,81 sein. Zeigt +Z nach unten,
ist az ungefähr -9,81 und Roll kann nahe ±180° liegen.

Beispiel für einen Messblock:

```text
--- IMU ---
ax: 0.030 m/s²
ay: -0.120 m/s²
az: 9.780 m/s²

gx: 0.400 °/s
gy: -0.200 °/s
gz: 0.100 °/s

Roll: -0.70°
Pitch: -0.18°

Acc magnitude: 9.781 m/s²
Gyro magnitude: 0.458 °/s
State: STILL | sampleHz: 100.0
```

Die Zahlen illustrieren das Format und sind keine garantierte Sensorgenauigkeit.
`sampleHz` zeigt bis zum ersten Zeitfenster noch 0,0 an.
Alle ausgegebenen Achsen sind gefiltert, die Gyro-Werte zusätzlich offsetkorrigiert.
Adafruit liefert m/s² und rad/s; der Code rechnet rad/s mit ungefähr 57,2958
in Grad/s um, entsprechend den Einheiten im
[Adafruit-Beispiel](https://learn.adafruit.com/mpu6050-6-dof-accelerometer-and-gyro/arduino).

## 8. Kippen, drehen und kurz bewegen

Für diese Richtungsbeschreibung das Modul mit **+X nach vorne, +Y nach links
und +Z nach oben** ausrichten. Die Lage des ESP32-USB-Anschlusses spielt dabei
keine Rolle. Bei anderer Montage ändern sich die Zuordnung und Vorzeichen.

| Versuch | Was sich ändern sollte |
| --- | --- |
| Nach vorne kippen: +X-Ende nach unten | ax wird negativ, Pitch positiv; az nimmt ab |
| Nach hinten kippen: +X-Ende nach oben | ax wird positiv, Pitch negativ; az nimmt ab |
| Nach links kippen: +Y-Ende nach unten | ay wird negativ, Roll negativ |
| Nach rechts kippen: +Y-Ende nach oben | ay wird positiv, Roll positiv |
| Flach um die Z-Achse drehen | vor allem gz schlägt während der Drehung aus; Roll/Pitch bleiben ungefähr gleich |
| Um X oder Y drehen | vor allem gx bzw. gy schlägt während der Drehung aus |
| Kurz verschieben oder leicht schütteln | ax/ay/az ändern sich vorübergehend; oft ändert sich auch Acc magnitude |

Nach dem Kippen wieder ruhig halten: Der Beschleunigungsbetrag bleibt
ungefähr bei 9,81 m/s², die Gyro-Werte gehen zurück nahe null.
Auch ein ruhig geneigter Sensor kann **STILL** anzeigen.
Bei einer Drehung über `ROTATION_THRESHOLD_DPS` (anfangs 30 °/s als
Gyro-Gesamtbetrag) erscheint **ROTATING**, bei anderen erkannten Bewegungen
**MOVING**. Nach dem Anhalten sollte die Anzeige wieder **STILL** werden.

Gyro-Werte zeigen die Drehgeschwindigkeit, keinen bleibenden Drehwinkel.
Roll/Pitch stammen ausschließlich aus der Beschleunigung. Deshalb können
sie beim Schütteln vorübergehend falsche Neigungen anzeigen.
Der Low-Pass glättet kurze Impulse und verzögert die Anzeige etwas;
sehr kurze Ereignisse können zwischen zwei 10-Hz-Ausgaben liegen.

`STILL` bedeutet nur, dass die gewählten Schwellen keine Bewegung erkennen.
Gleichförmige geradlinige Bewegung und jede beliebige kleine Bewegung lassen
sich damit nicht sicher erkennen. Der Test dient zur Prüfung der Hardware.

## 9. Fehlermeldungen und Verkabelung prüfen

Bei fehlender IMU erscheint wiederholt:

```text
# FEHLER: MPU6050 NICHT gefunden.
Prüfe:
- 3.3V
- GND
- SDA GPIO4
- SCL GPIO5
- AD0 an GND
- Verkabelung
```

| Meldung / Problem | Was du prüfen solltest |
| --- | --- |
| `MPU6050 NICHT gefunden.` | 3V3 an VCC, gemeinsame Masse, SDA/SCL nicht vertauscht, AD0 an GND; Modul muss tatsächlich ein MPU6050 sein |
| `I2C-Initialisierung fehlgeschlagen.` | GPIO4/GPIO5 und die Pin-Konstanten prüfen |
| `I2C-Takt konnte nicht eingestellt werden.` | I2C-Konfiguration und SDA/SCL prüfen |
| `I2C-Lesefehler` bei Kalibrierung oder Messung | Versorgung, lose Kontakte, kurze Kabel und Pull-ups prüfen |
| `Kalibrierung zu unruhig.` | Sensor vollständig still ablegen und RESET drücken |
| Keine Ausgabe | USB-Datenkabel, richtigen COM-Port, 115200 Baud und RESET prüfen |
| Gyro in Ruhe deutlich ungleich null | Sensor still ablegen und mit RESET neu kalibrieren |
| Dauerhaft MOVING in Ruhe | Ruhewerte und Kalibrierung prüfen; ggf. Stillstandsschwellen vorsichtig anpassen |

Bei Fehlern stoppt die Auswertung und wiederholt die Meldung alle zwei Sekunden.
Es werden keine alten Messwerte weiter ausgegeben. Das Programm bleibt mit
Pausen in dieser Fehlerschleife; nach Beheben der Ursache RESET drücken.
Vor Änderungen an der Verdrahtung USB abziehen.

`readIMU()` prüft zusätzlich, ob die IMU auf ihre I2C-Adresse antwortet,
und ob die Messwerte endlich sind. Die verwendete Adafruit-Library 2.2.6
meldet über `getEvent()` nicht jeden internen Busfehler. Die zusätzliche
ACK-Prüfung erkennt etwa einen abgezogenen Sensor, prüft aber nicht jede
einzelne interne Übertragung. Deshalb auch auf plausible Werte und eine
stabile Leserate achten. Bei Busproblemen zunächst Kabel und Pull-ups prüfen;
bei Bedarf `I2C_CLOCK_HZ` testweise von 400000 auf 100000 reduzieren.

## Einstellungen und Ablauf

Alle Einstellungen stehen oben in `src/main.cpp`.

| Konstante | Startwert | Bedeutung |
| --- | --- | --- |
| `IMU_SAMPLE_INTERVAL_US` | 10000 µs | etwa 100 Messungen/s |
| `PRINT_INTERVAL_MS` | 100 ms | etwa 10 Ausgaben/s |
| `LOW_PASS_ALPHA` | 0,20 | kleiner = ruhiger, aber verzögert |
| `STILL_ACCEL_THRESHOLD` | 0,35 m/s² | erlaubte Abweichung des Beschleunigungsbetrags von 9,80665 |
| `STILL_GYRO_THRESHOLD` | 3 °/s | Gyro-Gesamtbetrag für STILL muss darunter liegen |
| `ROTATION_THRESHOLD_DPS` | 30 °/s | Gyro-Gesamtbetrag für ROTATING muss darüber liegen |
| `CALIBRATION_MAX_GYRO_DPS` | 10 °/s | Obergrenze des unkalibrierten Gyro-Gesamtbetrags während der Kalibrierung |
| `CALIBRATION_MAX_ACCEL_DEVIATION` | 1 m/s² | maximal erlaubte Abweichung von 1 g während der Kalibrierung |
| `CALIBRATION_MAX_GYRO_STDDEV_DPS` | 1,5 °/s | maximal erlaubte kombinierte Gyro-Streuung während der Kalibrierung |

Die MPU6050-Messbereiche bleiben bei **±4 g** und **±500 °/s**.
Der eingebaute 21-Hz-Tiefpass und ein Abtastteiler von 9 stellen die
Sensor-Ausgaberate auf 100 Hz ein. Zusätzlich glättet `filterIMU()` alle
sechs Achsen:

```text
filtered = alpha * newValue + (1 - alpha) * filtered
```

Der erste Messwert initialisiert den Filter direkt, damit er nicht bei null
startet. `calculateIMUValues()` berechnet die Beträge als Wurzel der Summe der
Achsenquadrate, Roll als `atan2(ay, az)` und Pitch als
`atan2(-ax, sqrt(ay² + az²))`, jeweils in Grad.

Die vorhandene Aufteilung bleibt einfach:

```text
setup(): nur den gewählten Sensor initialisieren
  IMU_TEST -> initIMU() -> calibrateIMU()

loop():
  PRESSURE_TEST -> updatePressureSensor()
  IMU_TEST      -> updateIMU()
                   -> readIMU()
                   -> filterIMU()
                   -> calculateIMUValues()
                   -> classifyIMUState()
                   -> printIMUCSV() pro Messung, wenn IMU_CSV_OUTPUT = true
                      oder printIMUData() etwa alle 100 ms im Textmodus
```

## Zum unveränderten Drucksensor-Test zurückkehren

In `src/main.cpp` nur die Moduszeile ändern:

```cpp
const TestMode ACTIVE_TEST = PRESSURE_TEST;
```

Danach neu bauen und hochladen. Im `PRESSURE_TEST` wird ausschließlich
der FSR initialisiert und gelesen; der MPU6050 darf fehlen.
Die folgenden Abschnitte beschreiben den erhaltenen Drucksensor-Test.

### Drucksensor anschliessen

USB vor dem Verdrahten abziehen. Den FSR und den 10-kOhm-Widerstand so verbinden:

```text
ESP32 3V3
    |
   FSR
    |
    +---------- ESP32 GPIO0 (ADC)
    |
  10 kOhm
    |
ESP32 GND
```

Ein FSR-Anschluss geht an **3V3**, der andere an den Messpunkt. Vom selben
Messpunkt geht ein Kabel an **GPIO0** und der **10-kOhm-Widerstand an GND**.
Ein einfacher resistiver Zweipol-FSR hat keine Polung. Fuer den Teiler nur
3,3 V verwenden. Beim Druecken sinkt normalerweise sein Widerstand; dadurch
steigen Spannung und Rohwert am Messpunkt.

GPIO0 ist beim **ESP32-C3** ADC1-Kanal 0 und fuer diesen Test geeignet,
siehe [Espressif ADC-Dokumentation](https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32c3/api-reference/peripherals/adc.html).
GPIO-Nummern mit der Beschriftung deines konkreten Plus-Boards abgleichen.

### Drucksensor starten und Funktion pruefen

Nach **Build** und **Upload** den **Monitor** bei **115200 Baud** oeffnen.
FSR unbelastet lassen und **RESET** druecken, um die Startmeldungen zu sehen:

```text
=== Climbing Sensor Prototype ===
Active mode: PRESSURE_TEST
Drucksensor bereit: GPIO0, ca. 50 Hz, Mittelwert ueber 8 Samples.
Pressure ist ein relativer Belastungswert, KEINE Kraft in Newton.
Baseline: Drucksensor fuer etwa 2 Sekunden UNBELASTET lassen.
Baseline: 20.0 | MAX_PRESSURE_RAW: 3500
```

Die Baseline wird bei jedem Neustart etwa **2 Sekunden** lang aus 100
Messungen gemittelt und bleibt nur im RAM. Waehrenddessen nicht druecken.
Anschliessend erscheinen etwa **50 Zeilen pro Sekunde**, beispielsweise:

```text
Raw: 20 | Voltage: 0.01 V | Pressure: 0 %
Raw: 900 | Voltage: 0.60 V | Pressure: 25 %
Raw: 2500 | Voltage: 1.65 V | Pressure: 71 %
```

Die Zahlen illustrieren das Ausgabeformat; tatsaechliche Spannungen haengen
von der ADC-Kalibrierung ab. `Raw` ist der aktuelle ungefilterte 12-Bit-Wert
(0..4095). `Voltage` und `Pressure` sind ueber die letzten `NUM_SAMPLES`
Messungen geglaettet, anfangs nur ueber bereits vorhandene Samples.

1. **Ohne Druck:** niedriger Rohwert, Prozentwert nahe 0; etwas Rauschen ist normal.
2. **Langsam druecken:** Rohwert, Spannung und Prozentwert steigen.
3. **Loslassen:** Werte fallen nahe Baseline; die Glaettung braucht einen kurzen Moment.
4. **Staerker druecken:** Prozentwert steigt bis maximal 100 %.

| Konstante in `src/main.cpp` | Standard | Bedeutung |
| --- | --- | --- |
| `PRESSURE_PIN` | 0 | GPIO0, ADC1 |
| `SAMPLE_INTERVAL_MS` | 20 | 50 Messungen/s |
| `NUM_SAMPLES` | 8 | gleitender Mittelwert, Fenster etwa 160 ms |
| `MAX_PRESSURE_RAW` | 3500 | absoluter ADC-Wert fuer relative 100 % |

```text
Pressure = clamp((gemittelter Rohwert - Baseline)
                 / (MAX_PRESSURE_RAW - Baseline) * 100, 0, 100)
```

**Der Prozentwert ist ein relativer Belastungswert, keine Kraft in Newton.**
`MAX_PRESSURE_RAW` ist ein absoluter Rohwert, kein Abstand zur Baseline.
Unter einer gewuenschten Referenzbelastung den stabilen Rohwert beobachten
und diesen einstellen. Bei Baseline >= MAX_PRESSURE_RAW erscheint ein Fehler;
Pressure bleibt 0 %. Entlasten und RESET druecken oder die Grenze anpassen.

Die Spannung wird ueber eine separate kalibrierte Messung mit
`analogReadMilliVolts()` ermittelt und durch 1000 geteilt. **Raw / 4095 * 3,3 V
waere beim ESP32-C3 ungenau.** Mit 11-dB-Daempfung reicht sein spezifizierter
ADC-Messbereich nur bis etwa **2,5 V**, siehe
[Espressif ADC-API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/adc.html).
Bei starkem Druck kann der 3,3-V-Teiler darueber liegen: Rohwerte nahe 4095
bedeuten Saettigung, weitere Belastungen sind dann nicht unterscheidbar und
die Spannungsanzeige ist unzuverlaessig. Ein spaeter kleinerer Teilerwiderstand
kann den nutzbaren Belastungsbereich erweitern.

Bleibt `Raw` immer 0: Messpunkt, Versorgung und FSR-Verbindungen pruefen.
Bleibt er ohne Druck nahe 4095: 10-kOhm-Verbindung nach GND und Verdrahtung pruefen.
Bleibt der Monitor leer: Datenkabel, COM-Port und RESET pruefen.

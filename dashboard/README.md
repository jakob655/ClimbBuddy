# BelayBuddy – Live Climbing Sensor

Lokales Python-/Browser-Dashboard für ESP32-C3 und MPU6050. Die Oberfläche nutzt
HTML, SVG und Canvas. Python benötigt nur **pyserial**; der HTTP-Server und die
Datenverarbeitung verwenden die Python-Standardbibliothek.

## Einfach starten

1. Alte Dashboard-Terminals mit **Strg+C** beenden und alte Browser-Tabs schließen.
2. Im Windows-Explorer im Projektordner **Dashboard starten.cmd doppelklicken**.
3. Das Terminal offen lassen. Der Browser öffnet sich automatisch.
4. **Simulation läuft direkt beim Start.** Oben muss der Samples-Zähler steigen.

Der Starter verwendet `.venv`, installiert bei Bedarf pyserial und sucht einen
freien lokalen Port zwischen 8501 und 8520. Maßgeblich ist die Adresse im neu
geöffneten Browser/Terminal. Es gibt keine Streamlit-Installation, Fragment-IDs,
Seiten-Reruns oder automatischen Seiten-Reloads mehr.

## Simulation testen

1. **Roll** zwischen −30° und +30° bewegen: Wert und Figur reagieren links/rechts.
2. **Pitch** bewegen: leichte Größenänderung der Figur.
3. **Gyro Z** auf +90 °/s: Oberkörper dreht links; −90: rechts. Bei 0 kehrt der
   rein visuelle Drehwinkel langsam zur Mitte zurück.
4. **Reset**, danach **Simulate Move** drücken und etwa 2 Sekunden warten:
   **Detected Moves = 1**, **Demo climbing progress = 4 %**.
5. **Start Recording**, einige Sekunden bewegen, **Stop Recording**, dann
   **CSV herunterladen**. Die Datei liegt auch unter `dashboard/recordings/`.

Bei allen Reglern auf 0 bleibt der Avatar ruhig. Samples-Zähler und Charts
laufen trotzdem weiter. Ein dauerhaft hoher Gyro-/Energy-Wert beendet keinen
Move: erst ausreichend lange Ruhe bestätigt den Zyklus.

## ESP32 / MPU6050 testen

1. In `src/main.cpp` prüfen:

   ```cpp
   const TestMode ACTIVE_TEST = IMU_TEST;
   constexpr bool IMU_CSV_OUTPUT = true;
   ```

2. ESP32 per USB-Datenkabel anschließen und in PlatformIO **Upload** starten.
   Auf **SUCCESS** warten. **Build allein überträgt keine Firmware.**
3. Serial Monitor schließen. Sensor flach/still ablegen, **RESET** drücken,
   etwa **8 Sekunden** für Start und Kalibrierung warten.
4. Dashboard öffnen. **Data Source → Serial / ESP32**, **Ports aktualisieren**,
   passenden COM-Port auswählen und **Connect** drücken.
5. Auf steigenden Samples-Zähler achten. Sensor kippen und drehen: Avatar und
   Kurven reagieren. Für einen Move bewegen und danach mindestens 1 s stilllegen.
6. Vor einem weiteren Upload **Disconnect** drücken.

Die [bestehende Verdrahtungsanleitung](../README.md) gilt weiterhin:
3V3 → VCC, GND → GND/AD0, GPIO4 → SDA, GPIO5 → SCL. Nach RESET/Upload kann sich
COM ändern. Fehlt ein Port, USB-Datenkabel und Windows-Gerätemanager prüfen.

**Verbindungsdiagnose · letzte Serial-Zeilen** zeigt Empfangsbytes, Zeilenzähler
und Originalausgabe. IMU-Textblöcke erfordern CSV-Firmware, `PRESSURE_TEST`
erfordert `IMU_TEST`. Ein offener COM-Port allein bedeutet noch keinen Datenempfang.
Firmware- und USB-Fehler werden sichtbar angezeigt. Nach mehr als 2 s ohne
Samples steht NO DATA; die letzte Pose und Historie bleiben stehen.

## Wie schnell ist die Darstellung?

- **Erfassung:** Ziel etwa 100 Samples/s, in einem eigenen Python-Thread.
- **Datenübertragung:** kontinuierlicher SSE-Stream mit Ziel 30 Paketen/s.
  Jedes Paket enthält alle neuen Samples seit dem vorherigen Paket sowie die
  aktuellste Pose. Es gibt keine anwachsende Warteschlange alter Bildschirmbilder.
- **Avatar:** `requestAnimationFrame`, üblicherweise etwa 60 Bilder/s, abhängig
  von Bildschirm, Browser und Rechner. Die Figur interpoliert zwischen neuen
  Posen. Ihre SVG-Elemente werden einmal erstellt und anschließend direkt bewegt.
- **Charts:** Canvas, Ziel 30 Zeichnungen/s, letzte 10 s. Nur die Darstellung
  wird bei Bedarf ausgedünnt; Move-Erkennung und Recording verarbeiten die Samples.
- **Live-Zahlen:** 10 Aktualisierungen/s, unabhängig von der Avatar-Animation.

Unter den Metriken stehen gemessene Avatar-fps und empfangene Datenpakete/s.
`Sample Rate · received` ist die tatsächlich in Python empfangene Sensor-Rate.
`Quelle ... Hz` ist das vom ESP32 gemeldete `sampleHz` (Simulation: Zielrate).
Browser können Hintergrund-Tabs beim Zeichnen drosseln. Die Python-Erfassung
läuft über die offene Datenverbindung unabhängig davon weiter.

## Verbindungen, Reload und Recording

Ein Server besitzt genau einen Leser und eine Datenquelle. Mehrere Tabs zeigen
und bedienen denselben Sensor. Ein Browser-Reload baut nur den Datenstream neu
auf und setzt weder Moves noch Recording zurück. Bis zu vier Live-Tabs sind
möglich. Bei Verbindungsabbruch versucht der Browser automatisch neu zu verbinden.

Nach etwa 30 s ohne offene Browser-Datenverbindung gibt der Reader den COM-Port
frei und beendet Recording. Beim erneuten Öffnen startet eine ruhende Simulation
automatisch als neue Session; Serial erneut mit **Connect** verbinden. Beenden
mit Strg+C schließt Reader und Recording ebenfalls.

Recording schreibt fortlaufend auf die Festplatte; der Download wird in kleinen
Blöcken übertragen. Auch lange Aufzeichnungen werden nicht komplett im RAM gehalten.
Quellenwechsel, Reset, Disconnect und Serial-Fehler stoppen die Aufnahme. Alte
CSV-Dateien bleiben erhalten. Dateinamen verwenden lokale Zeit plus Mikrosekunden.
Die Datei enthält `received_at_utc`, `source` und die Firmware-Spalten.

## CSV und Move-Erkennung

Die bestehende Firmware liefert bei `IMU_CSV_OUTPUT = true`:

```text
timestamp,ax,ay,az,gx,gy,gz,roll,pitch,accelerationMagnitude,gyroMagnitude,movementEnergy,state,sampleHz
```

Timestamp ist `millis()` in ms, Beschleunigung in m/s² einschließlich Schwerkraft,
Gyro in °/s, Roll/Pitch in Grad. `movementEnergy = abs(accelerationMagnitude -
9.80665)` ist ein Bewegungsindikator in m/s², keine Energie in Joule. Ein
verpasster Header ist zulässig; `accMagnitude` wird als Alias akzeptiert.
Startmeldungen und fehlerhafte/unvollständige Zeilen werden übersprungen.

REST → MOVING → REST zählt einen Move. Standard: Energy > 0,6 m/s² **oder**
Gyro-Betrag > 20 °/s für mindestens 200 ms; Ende bei beiden Kanälen unter 65 %
der Startschwelle für mindestens 400 ms. Unter **Move detection · thresholds**
ändern und **Übernehmen** drücken. Änderungen verwerfen einen offenen Move.
Die Bedingungen verwenden geglättete Werte nach der Deadzone. Ein dauerhaft
kleiner Ausschlag oder ein einzelner kurzer Impuls zählt damit keinen Move.

Defaults und Glättung stehen zentral in `sensor_state.py`. Die Tabelle enthält
die letzten fünf Moves: Start/Ende relativ zur Session, Dauer ohne abschließende
Ruhe-Bestätigungszeit, Peak acc inklusive g, Peak gyro und maximale Roll-/Pitch-
Auslenkung mit Vorzeichen. Datenlücken über 1 s oder Zeit-Rücksprünge verwerfen
offene Moves; `millis()`-Überlauf wird berücksichtigt. Python-Historie maximal
30 s / 3200 Samples, Browser-Historie maximal 10 s / 3200 Samples.

## Ruhige Darstellung und Filter einstellen

Der Datenpfad ist **RAW → Low-Pass → Deadzone → Anzeige / Status / Move-Erkennung**.
RAW bezeichnet die im Dashboard empfangenen Werte; bei Serial hat die Firmware
bereits ihren eigenen Low-Pass angewendet. Dessen Sensorberechnung bleibt erhalten.
Der zusätzliche Dashboard-Filter glättet Roll/Pitch, alle Gyro-Achsen,
Gyro-Gesamtbetrag und Movement Energy. Auch Beschleunigungsachsen und -betrag
werden für Kurven und Anzeige geglättet. Die Filterzeit berücksichtigt die
tatsächlichen Sample-Abstände; die Alphas beziehen sich auf 100 Hz.

Die Deadzone setzt kleine **gefilterte** Werte exakt auf null, ohne den internen
Filterwert zu löschen. Statuswechsel wie LEFT/RIGHT LEAN, ROTATING oder MOVING
benötigen 200 ms durchgehende Bestätigung. Für die Rückkehr zu STILL gelten
400 ms. Die ungefilterte Firmware-Statusmeldung ist nur noch unter „Quelle“
sichtbar und kann die beruhigte Hauptanzeige nicht überschreiben.

Die Einstellungen stehen oben in `sensor_state.py`:

| Konstante | Startwert | Wirkung |
| --- | --- | --- |
| `ORIENTATION_SMOOTHING_ALPHA` | 0,14 | Low-Pass für Roll und Pitch |
| `GYRO_SMOOTHING_ALPHA` | 0,14 | Low-Pass für gx, gy, gz und Gyro-Gesamtbetrag |
| `MOVEMENT_SMOOTHING_ALPHA` | 0,10 | Low-Pass für Movement Energy und Beschleunigung |
| `ROLL_DEADZONE_DEG` | 3 ° | kleine Roll-Auslenkungen ignorieren |
| `PITCH_DEADZONE_DEG` | 3 ° | kleine Pitch-Auslenkungen ignorieren |
| `GYRO_DEADZONE_DPS` | 5 °/s | kleine Gyro-Werte ignorieren |
| `MOVEMENT_DEADZONE` | 0,12 m/s² | kleine Energy-Werte ignorieren |
| `STATE_ENTER_MS` | 200 ms | Mindestdauer für einen neuen aktiven Status |
| `STATE_EXIT_MS` | 400 ms | Mindestdauer vor STILL |
| `MOVE_GYRO_THRESHOLD` | 20 °/s | Startschwelle für Gyro-Gesamtbetrag |
| `MOVE_ENERGY_THRESHOLD` | 0,6 m/s² | alternative Startschwelle für Movement Energy |
| `MOVE_START_CONFIRM_MS` | 200 ms | Bewegung so lange durchgehend bestätigen |
| `MOVE_END_CONFIRM_MS` | 400 ms | Ruhe so lange durchgehend bestätigen |

Die vier `MOVE_*`-Konstanten liefern die Startwerte der vorhandenen
**Move detection · thresholds**-Einstellungen. Dort lassen sich Empfindlichkeit
und Bestätigungszeiten ohne Neustart ändern; Dauerfelder verwenden Sekunden.
Für das Move-Ende müssen beide Kanäle unter 65 % ihrer Startschwelle liegen.
Die Deadzones bleiben dabei die Untergrenze der Empfindlichkeit.

In `static/app.js` steuern `AVATAR_POSITION_SMOOTHING = 0.10` und
`AVATAR_ROTATION_SMOOTHING = 0.08` das zusätzliche weiche Nachziehen.
Position und Demo-Höhe sowie Körperneigung und Körperdrehung nähern sich ihren
Zielwerten schrittweise an. Diese Faktoren beziehen sich auf 60 Bilder/s und
werden an die tatsächlichen Frame-Abstände angepasst.

- **Noch zu nervös:** passende Alphas und Avatar-Faktoren **verkleinern**;
  Deadzones, Status-Bestätigungszeiten und bei falschen Moves die Move-Schwellen
  bzw. Start-Bestätigungszeit **erhöhen**.
- **Zu träge:** passende Alphas und Avatar-Faktoren **erhöhen**;
  Status-/Move-Bestätigungszeiten **verkürzen**. Wenn kleine echte Bewegungen
  fehlen, Deadzones bzw. Move-Schwellen **verkleinern**.

Nach Änderungen an Code-Konstanten den Dashboard-Server neu starten und den
Browser neu laden. Am besten jeweils nur eine Einstellung ändern.

Zum Vergleichen unter den Moves **Filter-Debug · RAW vs. FILTERED** aufklappen:

- **RAW:** empfangener Originalwert.
- **FILTERED:** zusätzlicher Dashboard-Low-Pass, noch vor der Deadzone.
- **DISPLAY:** nach Deadzone; Grundlage für Zahlen, Kurven und Erkennung.

Die Tabelle zeigt Roll/Pitch, gx/gy/gz, Gyro-Gesamtbetrag und Movement Energy
zehnmal pro Sekunde. Winkel und Gyro erscheinen mit einer Nachkommastelle,
Movement Energy mit zwei. Beispiel: Simulation auf Roll 2°, Gyro Z 4 °/s und
Energy 0,1 m/s² stellen: RAW/FILTERED zeigen die kleinen Ausschläge, DISPLAY
bleibt null; Status STILL, keine Moves. Anschließend Roll auf 25° oder
**Simulate Move** testen: eine echte Bewegung erscheint mit weichem Nachziehen.

CSV-Aufnahmen speichern weiterhin RAW, ebenso die Peak-Werte der Move-Tabelle.
Reset, Quellenwechsel und Datenlücken verwerfen Filter- und Bestätigungszustände;
ein normaler Browser-Reload lässt die laufende Session bestehen.

## Grenzen

**Avatar position is a visualization of IMU movement, not measured absolute position.**

Roll verschiebt/neigt die Figur, Pitch ändert leicht die Größe. Gyro Z erzeugt
einen begrenzten, zur Mitte zurückkehrenden Drehwinkel. Der MPU6050 hat keinen
Magnetometer und liefert keinen stabilen absoluten Yaw-Winkel. Es gibt keine
Beschleunigungs-Doppelintegration, metrische Position oder gemessene Höhe.
**Demo climbing progress** ist Move Count × 4 %, maximal 100 %. Auch Schütteln
oder Drehen kann einen MVP-Move auslösen. Das ist keine validierte Zugerkennung.

`PRESSURE_TEST`, die vorhandene IMU-Berechnung und PlatformIO-Konfiguration wurden
für den Wechsel der Oberfläche nicht verändert. Der FSR bleibt separat nutzbar.

## Dateien und manuelle Installation

```text
dashboard/
    app.py              HTTP-Server, Aktionen und Live-Datenstream
    launch.py           Start mit Projektumgebung und freiem Port
    serial_reader.py    Serial / Simulation, Hintergrundthread, Diagnose
    sensor_state.py     CSV, Puffer, Glättung, Move Detection, Recording
    visualization.py    einmaliges SVG-Grundgerüst
    static/             index.html, style.css, app.js (Animation und Canvas)
    tests/              Datenpfad- und HTTP/SSE-Integrationstests
```

Python 3.10 oder neuer (getestet: 3.12). Alternativ zum Windows-Starter:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r dashboard/requirements.txt
.\.venv\Scripts\python.exe dashboard/app.py --port 8501 --open-browser
```

Prüfen, ohne einen Server zu starten:

```powershell
.\.venv\Scripts\python.exe dashboard/launch.py --check
```

Automatische Tests ohne Hardware:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s dashboard/tests -v
```

Referenzen: [SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events),
[requestAnimationFrame](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame).

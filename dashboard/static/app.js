"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const setText = (id, value) => { if ($(id).textContent !== String(value)) $(id).textContent = value; };
  const ranges = {roll: [-45, 45], pitch: [-45, 45], gz: [-150, 150], energy: [0, 5]};
  const units = {roll: "°", pitch: "°", gz: " °/s", energy: " m/s²"};
  // Per-frame factors at 60 fps. Smaller = softer/slower; adjusted for actual dt.
  const AVATAR_POSITION_SMOOTHING = 0.10;
  const AVATAR_ROTATION_SMOOTHING = 0.08;
  const debugFields = ["roll", "pitch", "gx", "gy", "gz", "gyroMagnitude", "movementEnergy"];
  let state = null, history = [], generation = null, sequence = 0, lastPacket = 0;
  let requestError = "", transport = false, configSignature = "", moveSignature = "";
  let dirty = false, sending = false, sliderTimer = null;
  let frames = 0, packets = 0, fps = 0, packetRate = 0, rateTime = performance.now();
  const pose = {x: 320, y: 550, lean: 0, rotation: 0, scale: 1, progress: 0};

  let commandQueue = Promise.resolve();
  function action(name, extra = {}) {
    const send = async () => {
    try {
      const response = await fetch("/api/action", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action: name, ...extra})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Aktion fehlgeschlagen.");
      requestError = "";
      return true;
    } catch (error) {
      requestError = error.message;
      return false;
    }
    };
    commandQueue = commandQueue.then(send, send);
    return commandQueue;
  }

  function simulationValues() {
    return Object.fromEntries(Object.keys(ranges).map(key => [key, Number($("sim-" + key).value)]));
  }
  function sliderLabels() {
    for (const key of Object.keys(ranges)) setText("out-" + key, $("sim-" + key).value + units[key]);
  }
  async function flushSliders() {
    sliderTimer = null;
    if (sending || !dirty) return;
    dirty = false; sending = true;
    await action("simulation", {values: simulationValues()});
    sending = false;
    if (dirty) sliderTimer = setTimeout(flushSliders, 25);
  }
  for (const key of Object.keys(ranges)) {
    $("sim-" + key).addEventListener("input", () => {
      sliderLabels(); dirty = true;
      if (!sliderTimer && !sending) sliderTimer = setTimeout(flushSliders, 20);
    });
  }
  $("source").addEventListener("change", async () => {
    const source = $("source").value;
    await action("source", {source});
    if (source === "serial") await refreshPorts();
  });
  async function refreshPorts() {
    try {
      const response = await fetch("/api/ports");
      const result = await response.json();
      const selected = $("port").value;
      $("port").replaceChildren(...result.ports.map(port => new Option(port.device + " · " + port.description, port.device)));
      if (result.ports.some(port => port.device === selected)) $("port").value = selected;
      $("port").disabled = !result.ports.length;
      setText("port-hint", result.error || (result.ports.length ? "ESP32-Port auswählen." : "Kein Serial-Port gefunden. USB-Datenkabel prüfen."));
    } catch (error) { requestError = "Portliste nicht erreichbar: " + error.message; }
  }
  $("refresh-ports").onclick = refreshPorts;
  $("connect").onclick = () => action("connect", {port: $("port").value});
  $("disconnect").onclick = () => action("disconnect");
  $("simulate-move").onclick = () => action("simulate_move");
  $("reset").onclick = async () => {
    dirty = false;
    if (sliderTimer) clearTimeout(sliderTimer);
    sliderTimer = null;
    for (const key of Object.keys(ranges)) $("sim-" + key).value = 0;
    sliderLabels();
    await action("reset");
  };
  $("record-start").onclick = () => action("record_start");
  $("record-stop").onclick = () => action("record_stop");
  $("thresholds").onsubmit = async event => {
    event.preventDefault();
    const values = Object.fromEntries([...new FormData(event.target)].map(([key, value]) => [key, Number(value)]));
    await action("config", {values});
  };

  const events = new EventSource("/events");
  events.onopen = () => { transport = true; };
  events.onerror = () => { transport = false; };
  events.onmessage = event => {
    try {
      const next = JSON.parse(event.data);
      if (next.generation !== generation) {
        generation = next.generation; history = []; sequence = 0;
      }
      for (const sample of next.history) {
        if (sample.seq > sequence) { history.push(sample); sequence = sample.seq; }
      }
      if (history.length) {
        const cutoff = history[history.length - 1].time_s - 10;
        let first = 0;
        while (first < history.length && history[first].time_s < cutoff) first++;
        if (first) history.splice(0, first);
        if (history.length > 3200) history.splice(0, history.length - 3200);
      }
      state = next; transport = true; packets++; lastPacket = performance.now();
    } catch (error) { requestError = "Empfangsfehler: " + error.message; }
  };

  function updateControls(connection) {
    const simulation = connection.source === "Simulation";
    const source = simulation ? "simulation" : "serial";
    if ($("source").value !== source) $("source").value = source;
    $("simulation-controls").hidden = !simulation;
    $("serial-controls").hidden = simulation;
    $("connect").disabled = connection.connected || !$("port").options.length;
    $("disconnect").disabled = !connection.connected;
    $("simulate-move").disabled = connection.simulating_move;
    if (simulation && !dirty && !sending) {
      for (const key of Object.keys(ranges)) {
        const input = $("sim-" + key);
        if (document.activeElement !== input) input.value = connection.simulation[key];
      }
      sliderLabels();
    }
    $("record-start").disabled = state.recording || !connection.connected;
    $("record-stop").disabled = !state.recording;
    $("download").hidden = !state.recording_name || state.recording;
    setText("record-info", (state.recording ? "● Recording · " : "") + state.recording_seconds.toFixed(1) + " s · " + state.recorded_samples.toLocaleString() + " Samples");
    setText("record-path", state.recording_path || "");
    const signature = JSON.stringify(state.config);
    if (signature !== configSignature && !$("thresholds").contains(document.activeElement)) {
      for (const input of $("thresholds").querySelectorAll("input")) input.value = state.config[input.name];
      configSignature = signature;
    }
  }

  function updateMetrics(now) {
    const live = transport && now - lastPacket < 1500;
    setText("transport", live ? "● Live" : "Verbindung wird erneuert …");
    $("transport").classList.toggle("live", live);
    if (!state) return;
    const c = state.connection, raw = state.latest, sample = state.display, fresh = state.fresh && live;
    setText("connection", (c.connected ? (c.source === "Simulation" ? "● Simulation" : "● Port geöffnet: " + c.port + " · 115200 baud") : "○ Datenquelle getrennt") + " · Receiving: " + (fresh ? state.rate.toFixed(1) : "0.0") + " samples/s · Samples: " + state.total_samples.toLocaleString());
    setText("sample-rate", (fresh ? state.rate.toFixed(1) : "0.0") + " Hz");
    setText("state", fresh ? state.status : "NO DATA");
    setText("current-state", fresh ? state.status : "NO DATA");
    setText("move-count", state.move_count);
    setText("acceleration", fresh ? sample.accelerationMagnitude.toFixed(2) + " m/s²" : "—");
    for (const [id, key, unit] of [["roll", "roll", "°"], ["pitch", "pitch", "°"], ["rotation", "gyroMagnitude", " °/s"], ["gyro-z", "gz", " °/s"]]) {
      setText(id, fresh ? sample[key].toFixed(1) + unit : "—");
    }
    setText("movement-energy", fresh ? sample.movementEnergy.toFixed(2) + " m/s²" : "—");
    if ($("filter-debug").open) {
      for (const field of debugFields) {
        const decimals = field === "movementEnergy" ? 2 : 1;
        for (const [name, values] of [["raw", raw], ["filtered", state.filtered], ["display", sample]]) {
          setText("debug-" + name + "-" + field, fresh ? values[field].toFixed(decimals) : "—");
        }
      }
    }
    $("intensity").value = fresh ? state.intensity : 0;
    setText("intensity-text", fresh ? Math.round(state.intensity * 100) + " %" : "—");
    $("progress").value = Math.min(100, state.move_count * 4);
    setText("progress-text", Math.min(100, state.move_count * 4) + " %");
    setText("firmware", raw ? "Quelle: " + raw.state + " · " + raw.sampleHz.toFixed(1) + " Hz · Detector: " + state.phase : "");
    setText("performance", fps + " fps Avatar · " + packetRate + " Datenpakete/s");
    setText("diagnosis", "Serial: " + c.received_bytes + " Bytes · " + c.received_lines + " Zeilen · " + c.ignored_lines + " ignoriert");
    setText("serial-lines", c.recent_lines.join("\n") || "Noch keine Serial-Zeile empfangen.");
    let message = requestError || c.error || state.recording_error || c.firmware_error;
    if (!message && c.connected && !fresh && c.source !== "Simulation") {
      message = c.stream_format === "text" ? "IMU-Textausgabe empfangen: IMU_CSV_OUTPUT = true setzen und Firmware hochladen."
        : c.stream_format === "pressure" ? "PRESSURE_TEST empfangen: ACTIVE_TEST = IMU_TEST setzen und Firmware hochladen."
        : "Warte auf IMU-CSV-Daten. Kalibrierung abwarten; COM-Port und Verbindungsdiagnose prüfen.";
    }
    $("message").hidden = !message;
    setText("message", message || "");
    updateControls(c);
    const movesKey = generation + ":" + state.move_count;
    if (movesKey !== moveSignature) {
      moveSignature = movesKey;
      const fields = ["move", "start_time", "end_time", "duration", "peak_acceleration", "peak_gyro", "max_roll", "max_pitch"];
      $("move-rows").replaceChildren(...state.moves.map(move => {
        const row = document.createElement("tr");
        for (const field of fields) {
          const cell = document.createElement("td");
          const decimals = ["peak_gyro", "max_roll", "max_pitch"].includes(field) ? 1 : 2;
          cell.textContent = field === "move" ? move[field] : move[field].toFixed(decimals); row.append(cell);
        }
        return row;
      }));
      $("moves").hidden = !state.moves.length;
      $("no-moves").hidden = !!state.moves.length;
    }
  }

  function drawChart(id, fields, derived = false) {
    const canvas = $(id), box = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
    const width = Math.round(box.width), height = Math.round(box.height);
    if (!width || !height) return;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
    const left = 43, right = derived ? 40 : 12, top = 12, bottom = height - 25;
    const w = width - left - right, h = bottom - top;
    let low = fields[0] === "ax" ? -1 : -10, high = fields[0] === "ax" ? 11 : 10, energyMax = 1;
    for (const row of history) for (const field of fields) {
      if (field === "movementEnergy") energyMax = Math.max(energyMax, row[field]);
      else { low = Math.min(low, row[field]); high = Math.max(high, row[field]); }
    }
    const padding = (high - low) * .08; low -= padding; high += padding;
    ctx.font = "10px system-ui"; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = top + h * i / 4;
      ctx.strokeStyle = "#29404f"; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + w, y); ctx.stroke();
      ctx.fillStyle = "#91aab9"; ctx.textAlign = "right";
      ctx.fillText((high - (high - low) * i / 4).toFixed(1), left - 6, y + 3);
      if (derived) { ctx.textAlign = "left"; ctx.fillText((energyMax * (1 - i / 4)).toFixed(1), left + w + 6, y + 3); }
    }
    const end = history.length ? Math.max(10, history[history.length - 1].time_s) : 10, start = end - 10;
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) ctx.fillText((start + i * 2.5).toFixed(1) + " s", left + w * i / 4, height - 7);
    if (!history.length) { ctx.fillText("Waiting for samples …", left + w / 2, top + h / 2); return; }
    const stride = Math.max(1, Math.ceil(history.length / 600));
    ctx.save(); ctx.beginPath(); ctx.rect(left, top, w, h); ctx.clip();
    fields.forEach((field, index) => {
      ctx.strokeStyle = ["#58dac3", "#efae69", "#8ca6ff"][index]; ctx.lineWidth = 1.6; ctx.beginPath();
      let begun = false;
      for (let i = 0; i < history.length; i += stride) {
        const row = history[i], x = left + (row.time_s - start) / 10 * w;
        const value = field === "movementEnergy" ? row[field] / energyMax : (row[field] - low) / (high - low);
        const y = bottom - value * h;
        if (!begun) { ctx.moveTo(x, y); begun = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });
    ctx.restore();
  }

  let previousFrame = performance.now(), lastMetrics = 0, lastCharts = 0;
  function animate(now) {
    frames++;
    const dt = Math.min(.05, Math.max(0, (now - previousFrame) / 1000)); previousFrame = now;
    if (now - rateTime >= 1000) {
      fps = Math.round(frames * 1000 / (now - rateTime)); packetRate = Math.round(packets * 1000 / (now - rateTime));
      frames = packets = 0; rateTime = now;
    }
    if (state) {
      const positionAlpha = 1 - (1 - AVATAR_POSITION_SMOOTHING) ** (dt * 60);
      const rotationAlpha = 1 - (1 - AVATAR_ROTATION_SMOOTHING) ** (dt * 60);
      const targets = {
        x: 320 + state.roll * 2.3, y: 550 - state.progress * 3.9,
        lean: state.roll * .4, rotation: -state.yaw * .8,
        scale: 1 + state.pitch / 450, progress: state.progress
      };
      for (const key of Object.keys(pose)) {
        const alpha = ["lean", "rotation"].includes(key) ? rotationAlpha : positionAlpha;
        pose[key] += alpha * (targets[key] - pose[key]);
      }
      $("avatar-position").setAttribute("transform", `translate(${pose.x.toFixed(2)} ${pose.y.toFixed(2)})`);
      $("avatar-scale").setAttribute("transform", `scale(${pose.scale.toFixed(4)})`);
      $("avatar-lean").setAttribute("transform", `rotate(${pose.lean.toFixed(2)})`);
      $("avatar-twist").setAttribute("transform", `rotate(${pose.rotation.toFixed(2)} 0 -35)`);
      setText("wall-progress", pose.progress.toFixed(0) + "% DEMO");
    }
    if (now - lastMetrics >= 100) { updateMetrics(now); lastMetrics = now; }
    if (now - lastCharts >= 1000 / 30) {
      drawChart("accel-chart", ["ax", "ay", "az"]); drawChart("gyro-chart", ["gx", "gy", "gz"]);
      drawChart("derived-chart", ["roll", "pitch", "movementEnergy"], true); lastCharts = now;
    }
    requestAnimationFrame(animate);
  }
  sliderLabels(); refreshPorts(); requestAnimationFrame(animate);
})();

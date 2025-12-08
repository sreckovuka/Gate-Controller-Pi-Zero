#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import threading, time, json, os
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, jsonify
from gpiozero import DigitalOutputDevice, DigitalInputDevice
import BlynkLib

# ---------------- CONFIG ----------------
VERSION = "GateController v4.0"
BLYNK_AUTH_TOKEN = 'qxxxxxxx1'
RELAY_PIN = 21
REED_PIN = 20
SCHEDULE_FILE = "schedule.json"
LOG_FILE = "gate_log.txt"
RELAY_DURATION = 1.0  # seconds

# ---------------- HARDWARE ----------------
relay = DigitalOutputDevice(RELAY_PIN, active_high=False, initial_value=True)  # HIGH=OFF
reed = DigitalInputDevice(REED_PIN, pull_up=True)  # 0=closed, 1=open
relay_lock = threading.Lock()

# ---------------- BLYNK ----------------
blynk = BlynkLib.Blynk(BLYNK_AUTH_TOKEN,heartbeat=7)

# ---------------- SCHEDULE ----------------
schedule = {"enabled": True, "open_time": "07:00", "close_time": "19:00"}
if os.path.exists(SCHEDULE_FILE):
    with open(SCHEDULE_FILE, "r") as f:
        schedule.update(json.load(f))

# ---------------- INITIALIZE last_triggered ----------------
now = datetime.now()
today = now.date()
try:
    open_dt = datetime.combine(today, datetime.strptime(schedule["open_time"], "%H:%M").time())
    close_dt = datetime.combine(today, datetime.strptime(schedule["close_time"], "%H:%M").time())
except Exception:
    open_dt = close_dt = None

last_triggered = {
    "open": open_dt if now >= open_dt else None,
    "close": close_dt if now >= close_dt else None
}

# ---------------- SAFE BLYNK WRITE ----------------
def safe_blynk_write(pin, value):
    try:
        blynk.virtual_write(pin, value)
    except (BrokenPipeError, ConnectionResetError, AttributeError) as e:
        print(f"[BLYNK WRITE ERROR] Pin {pin} Value {value}: {e}")
        try:
            blynk.disconnect()
            blynk.connect()
        except Exception:
            pass

# ---------------- LOG ----------------
def log(msg, push=True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} - {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    if push:
        safe_blynk_write(2, 1 if not relay.value else 0)

def get_last_logs(n=10):
    if not os.path.exists(LOG_FILE):
        return ""
    with open(LOG_FILE) as f:
        lines = f.readlines()
    return "".join(lines[-n:])

# ---------------- GATE CONTROL ----------------
def is_gate_open():
    return not reed.value  # 0=closed, 1=open

_last_reed_state = is_gate_open()

def push_reed_status():
    global _last_reed_state
    current = is_gate_open()
    if current != _last_reed_state:
        safe_blynk_write(3, 1 if current else 0)
        safe_blynk_write(22, 0 if current else 1)
        _last_reed_state = current

def push_relay_status():
    safe_blynk_write(2, 1 if not relay.value else 0)

def push_status():
    push_relay_status()
    push_reed_status()

def pulse_relay(duration=RELAY_DURATION):
    with relay_lock:
        log(">>> RELAY ON (pulse start)")
        relay.off()  # LOW=ON
        safe_blynk_write(2, 1)
        time.sleep(duration)  # stays ON for full duration
        relay.on()   # HIGH=OFF
        log("<<< RELAY OFF (pulse end)")
        safe_blynk_write(2, 0)
    time.sleep(0.2)

def open_gate(trigger="manual"):
    if not is_gate_open():
        log(f"Gate OPEN triggered by {trigger}")
        pulse_relay()
    else:
        log(f"Gate OPEN skipped (already open) [{trigger}]")
    push_relay_status()

def close_gate(trigger="manual"):
    if is_gate_open():
        log(f"Gate CLOSE triggered by {trigger}")
        pulse_relay()
    else:
        log(f"Gate CLOSE skipped (already closed) [{trigger}]")
    push_relay_status()

# ---------------- REED CHANGE HANDLER + NOTIFICATIONS ----------------
def reed_changed():
    global _last_reed_state
    current = is_gate_open()
    if current == _last_reed_state:
        return

    safe_blynk_write(3, 1 if current else 0)
    safe_blynk_write(22, 0 if current else 1)

    try:
        if current:
            blynk.log_event("gate_opened", "Gate has opened")
        else:
            blynk.log_event("gate_closed", "Gate has closed")
    except Exception as e:
        print(f"[BLYNK EVENT ERROR] {e}")

    _last_reed_state = current
    log(f"Reed state changed: {'OPEN' if current else 'CLOSED'}", push=False)

reed.when_activated = reed_changed
reed.when_deactivated = reed_changed

# ---------------- BLYNK HANDLERS ----------------
@blynk.on("V1")
def handle_blynk_control(value):
    val = int(value[0])
    if val == 1:
        open_gate(trigger="Blynk")
    else:
        close_gate(trigger="Blynk")

@blynk.on("connected")
def blynk_connected():
    log("Raspberry Pi Connected to Blynk", push=False)

# ---------------- FLASK ----------------
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Pi Gate Controller</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { font-family: Arial; padding: 15px; background-color: #f2f2f2; }
            h1 { margin-bottom: 20px; }
            .status { font-weight: bold; font-size: 1.2em; }
            pre { background-color: #fff; padding: 10px; max-height: 300px; overflow-y: scroll; }
        </style>
    </head>
    <body class="container">
        <h1>Pi Gate Controller</h1>
        <p>Version: <b>{{version}}</b></p>
        <p>Current Time: <span id="now_time">{{now_time}}</span></p>
        <p>Gate Status: <span id="gate_status" class="status">{{ "OPEN" if gate else "CLOSED" }}</span></p>
        <p>Relay Status: <span id="relay_status" class="status">{{ "ON" if relay else "OFF" }}</span></p>

        <div class="d-flex gap-2 my-2">
            <form action="/open" method="post"><button id="open_btn" type="submit" class="btn btn-success btn-lg rounded-pill flex-fill">OPEN</button></form>
            <form action="/close" method="post"><button id="close_btn" type="submit" class="btn btn-danger btn-lg rounded-pill flex-fill">CLOSE</button></form>
        </div>

        <h2>Schedule</h2>
        <form action="/schedule" method="post">
            <div class="form-check">
                <input class="form-check-input" type="checkbox" name="enabled" id="enabled" {% if schedule.enabled %}checked{% endif %}>
                <label class="form-check-label" for="enabled">Enable</label>
            </div>
            <div class="mb-2">
                Open Time: <input type="time" name="open_time" value="{{schedule.open_time}}" class="form-control">
            </div>
            <div class="mb-2">
                Close Time: <input type="time" name="close_time" value="{{schedule.close_time}}" class="form-control">
            </div>
            <button type="submit" class="btn btn-primary btn-lg">Update Schedule</button>
        </form>

        <h2 class="mt-4">Log (Last 10 lines)</h2>
        <pre id="log">{{log_content}}</pre>

        <script>
        function fetchStatus() {
            fetch('/status')
            .then(resp => resp.json())
            .then(data => {
                document.getElementById('gate_status').innerHTML = data.gate ? "OPEN" : "CLOSED";
                document.getElementById('gate_status').style.color = data.gate ? "green" : "red";

                document.getElementById('relay_status').innerHTML = data.relay ? "ON" : "OFF";
                document.getElementById('relay_status').style.color = data.relay ? "green" : "gray";

                document.getElementById('log').innerText = data.log;
                document.getElementById('now_time').innerHTML = data.current_time;

                document.getElementById('open_btn').className = data.gate ? "btn btn-secondary btn-lg rounded-pill flex-fill" : "btn btn-success btn-lg rounded-pill flex-fill";
                document.getElementById('close_btn').className = data.gate ? "btn btn-danger btn-lg rounded-pill flex-fill" : "btn btn-secondary btn-lg rounded-pill flex-fill";
            });
        }
        setInterval(fetchStatus, 2000);
        </script>
    </body>
    </html>
    """, version=VERSION, now_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
       gate=is_gate_open(), relay=not relay.value,
       schedule=schedule, log_content=get_last_logs(10))

@app.route("/status")
def status():
    return jsonify({
        "gate": is_gate_open(),
        "relay": not relay.value,
        "log": get_last_logs(10),
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/open", methods=["POST"])
def web_open():
    open_gate(trigger="Web")
    return redirect("/")

@app.route("/close", methods=["POST"])
def web_close():
    close_gate(trigger="Web")
    return redirect("/")

@app.route("/schedule", methods=["POST"])
def web_schedule():
    schedule["enabled"] = "enabled" in request.form
    schedule["open_time"] = request.form.get("open_time", schedule["open_time"])
    schedule["close_time"] = request.form.get("close_time", schedule["close_time"])
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(schedule, f)
    log("Schedule updated via Web")
    return redirect("/")

# ---------------- WORKERS ----------------
def schedule_worker():
    global last_triggered
    while True:
        if schedule["enabled"]:
            now = datetime.now()
            today = now.date()
            try:
                open_dt = datetime.combine(today, datetime.strptime(schedule["open_time"], "%H:%M").time())
                close_dt = datetime.combine(today, datetime.strptime(schedule["close_time"], "%H:%M").time())
            except Exception as e:
                log(f"Schedule parse error: {e}")
                time.sleep(5)
                continue
            if now >= open_dt and last_triggered["open"] != open_dt:
                open_gate(trigger="Schedule")
                last_triggered["open"] = open_dt
            if now >= close_dt and last_triggered["close"] != close_dt:
                close_gate(trigger="Schedule")
                last_triggered["close"] = close_dt
        time.sleep(2)

def blynk_loop():
    while True:
        try:
            blynk.run()
        except Exception as e:
            print(f"Blynk loop error: {e}")
            try:
                blynk.disconnect()
                blynk.connect()
            except Exception:
                pass
            time.sleep(5)

# ---------------- START THREADS ----------------
threading.Thread(target=blynk_loop, daemon=True).start()
threading.Thread(target=schedule_worker, daemon=True).start()

# ---------------- RUN FLASK ----------------
if __name__ == "__main__":
    log(f"Starting Pi Gate Controller (Flask + Blynk) - {VERSION}")
    try:
        app.run(host="0.0.0.0", port=5000, threaded=True)
    finally:
        relay.on()

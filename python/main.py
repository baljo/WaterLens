# Runs the UNO Q water analyzer with deterministic anomaly decisions and a concise local-LLM explanation layer. 2026-09-06 20:33 Europe/Helsinki, Thomas Vikström.

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import csv
from datetime import datetime
import html
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from zoneinfo import ZoneInfo
from interpret_water import interpret_water_result

# Set the process timezone before Arduino App/Bridge is imported so
# framework log timestamps also use Finnish local time.
os.environ["TZ"] = "Europe/Helsinki"
if hasattr(time, "tzset"):
    time.tzset()

from arduino.app_utils import App, Bridge


# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
LABEL_FILE = BASE_DIR / "labels.txt"
OUTPUT_DIR = BASE_DIR / "samples"
INFERENCE_SCRIPT = BASE_DIR / "infer_water.py"
EI_PYTHON = Path("/app/.cache/.venv/bin/python")


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------

DASHBOARD_PORT = 8000
dashboard_server = None

# UNO Q ADC and approximate sensor conversion settings.
# These affect dashboard presentation only; CSV files and EI inference stay raw.
ADC_MAX = 16383.0
ADC_REFERENCE_V = 3.3
TDS_ASSUMED_TEMPERATURE_C = 25.0
ORP_OFFSET_MV = 0.0
LOCAL_TIMEZONE = ZoneInfo("Europe/Helsinki")

# Browser-assisted time synchronization.
# If setting the Linux clock is not permitted inside the App Lab container,
# browser_time_offset_seconds is used to keep application timestamps correct.
browser_time_offset_seconds = 0.0
browser_time_synced = False
browser_time_source = ""
browser_timezone_name = ""
browser_time_sync_lock = threading.Lock()


# ------------------------------------------------------------
# Shared sample state
# ------------------------------------------------------------

sample_lock = threading.Lock()
current_rows = []
current_label = None
current_mode = None

last_test_prediction = ""
last_test_confidence_percent = 0
last_test_time = ""
last_test_raw_averages = {}

last_test_anomaly_score = None
last_test_anomaly_threshold = None
last_test_is_anomaly = None
last_test_anomaly_status = ""

last_llm_status = "idle"
last_llm_interpretation = ""
last_llm_error = ""
llm_generation_id = 0
llm_call_lock = threading.Lock()


# ------------------------------------------------------------
# Labels
# ------------------------------------------------------------

def load_labels():
    labels = []

    if not LABEL_FILE.exists():
        raise FileNotFoundError(
            f"Label file not found: {LABEL_FILE}"
        )

    with LABEL_FILE.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, 1):
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            try:
                number_text, label = line.split(",", 1)
                number = int(number_text.strip())
                label = label.strip()
            except ValueError:
                raise ValueError(
                    f"Invalid labels.txt line {line_number}: "
                    f"{raw_line.rstrip()}"
                )

            if not label:
                raise ValueError(
                    f"Empty label on line {line_number}"
                )

            if "." in label:
                raise ValueError(
                    f"Label '{label}' contains a dot. "
                    "Dots are not allowed in labels."
                )

            expected_number = len(labels) + 1

            if number != expected_number:
                raise ValueError(
                    "labels.txt numbering must be consecutive "
                    f"starting at 1. Expected {expected_number}, "
                    f"found {number}."
                )

            labels.append(label)

    if not labels:
        raise ValueError("labels.txt contains no labels")

    return labels


LABELS = load_labels()

# Human-facing names only. Internal EI labels, filenames, and labels.txt stay unchanged.
DISPLAY_LABELS = {
    "Tap_water": "Tap water",
    "Sea_water": "Seawater",
    "Narrow_ditch": "Ditch water A",
    "Wide_ditch": "Ditch water B",
    "Wide_ditch_diluted": "Diluted ditch water B",
}


# ------------------------------------------------------------
# Dashboard helpers
# ------------------------------------------------------------

def friendly_label(label):
    return DISPLAY_LABELS.get(
        str(label),
        str(label).replace("_", " "),
    )


def adc_to_voltage(raw):
    return float(raw) * ADC_REFERENCE_V / ADC_MAX


def ph_from_raw(raw):
    """Approximate pH using Seeed's nominal uncalibrated linear equation."""
    voltage = adc_to_voltage(raw)
    return -19.18518519 * voltage + 41.02740741


def tds_from_raw(raw):
    """Approximate TDS in ppm, assuming 25 C because no water temperature is measured."""
    voltage = adc_to_voltage(raw)

    compensation = 1.0 + 0.02 * (TDS_ASSUMED_TEMPERATURE_C - 25.0)
    compensated_voltage = voltage / compensation

    tds_ppm = (
        133.42 * compensated_voltage ** 3
        - 255.86 * compensated_voltage ** 2
        + 857.39 * compensated_voltage
    ) * 0.5

    return max(0.0, tds_ppm)


def orp_from_raw(raw):
    """Approximate ORP in mV using the Grove ORP Sensor Kit Pro equation."""
    voltage = adc_to_voltage(raw)
    return (
        400.0 * ADC_REFERENCE_V
        - voltage * 1000.0
        - ORP_OFFSET_MV
    )


def turbidity_ntu_from_raw(raw):
    """Estimate turbidity in NTU using the published 3.3 V quadratic approximation."""
    voltage = adc_to_voltage(raw)

    ntu = (
        -2572.2 * voltage ** 2
        + 8700.5 * voltage
        - 4352.9
    )

    return max(0.0, ntu)


def app_now():
    """Return current time using browser sync when the system clock is stale."""
    with browser_time_sync_lock:
        offset = browser_time_offset_seconds

    return datetime.fromtimestamp(
        time.time() + offset,
        LOCAL_TIMEZONE
    )


def sync_time_from_browser(epoch_ms, timezone_name=""):
    """Synchronize time from a browser, with app-level fallback."""
    global browser_time_offset_seconds
    global browser_time_synced
    global browser_time_source
    global browser_timezone_name

    epoch_seconds = float(epoch_ms) / 1000.0

    # First try to set the Linux realtime clock. This may fail in a container
    # without CAP_SYS_TIME, which is expected and harmless.
    system_clock_set = False

    try:
        if hasattr(time, "clock_settime") and hasattr(time, "CLOCK_REALTIME"):
            time.clock_settime(
                time.CLOCK_REALTIME,
                epoch_seconds
            )
            system_clock_set = True
    except (PermissionError, OSError):
        system_clock_set = False

    with browser_time_sync_lock:
        if system_clock_set:
            browser_time_offset_seconds = 0.0
            browser_time_source = "browser → Linux clock"
        else:
            browser_time_offset_seconds = (
                epoch_seconds - time.time()
            )
            browser_time_source = "browser → application clock"

        browser_time_synced = True
        browser_timezone_name = str(timezone_name or "")

    return system_clock_set


def latest_dashboard_snapshot():
    with sample_lock:
        return {
            "prediction": last_test_prediction,
            "confidence": last_test_confidence_percent,
            "time": last_test_time,
            "raw": dict(last_test_raw_averages),
            "anomaly_score": last_test_anomaly_score,
            "anomaly_threshold": last_test_anomaly_threshold,
            "is_anomaly": last_test_is_anomaly,
            "anomaly_status": last_test_anomaly_status,
            "llm_status": last_llm_status,
            "llm_interpretation": last_llm_interpretation,
            "llm_error": last_llm_error,
            "time_synced": browser_time_synced,
            "time_source": browser_time_source,
            "browser_timezone": browser_timezone_name,
        }


def dashboard_html():
    result = latest_dashboard_snapshot()

    prediction = result["prediction"]
    confidence = result["confidence"]
    test_time = result["time"]
    raw = result["raw"]
    anomaly_score = result["anomaly_score"]
    anomaly_threshold = result["anomaly_threshold"]
    is_anomaly = result["is_anomaly"]
    llm_status = result["llm_status"]
    llm_interpretation = result["llm_interpretation"]
    llm_error = result["llm_error"]
    time_synced = result["time_synced"]
    time_source = result["time_source"]
    browser_timezone = result["browser_timezone"]

    if time_synced:
        time_status = "Time synchronized from this browser"
        if browser_timezone:
            time_status += f" ({html.escape(browser_timezone)})"
    else:
        time_status = "Waiting for browser time synchronization"

    if raw:
        ph_value = ph_from_raw(raw.get("ph_raw", 0))
        orp_mv = orp_from_raw(raw.get("orp_raw", 0))
        tds_ppm = tds_from_raw(raw.get("tds_raw", 0))
        turbidity_ntu = turbidity_ntu_from_raw(
            raw.get("turbidity_raw", 0)
        )
    else:
        ph_value = 0.0
        orp_mv = 0.0
        tds_ppm = 0.0
        turbidity_ntu = 0.0

    if prediction:
        if llm_status == "generating":
            llm_block = """
                <div class="llm-card">
                    <div class="eyebrow">LOCAL AI INTERPRETATION</div>
                    <h3>Generating interpretation…</h3>
                    <p>
                        The Edge AI result and measurements above are already available.
                        The local language model is preparing a short explanation in the background.
                    </p>
                </div>
            """
        elif llm_status == "ready" and llm_interpretation:
            llm_block = f"""
                <div class="llm-card">
                    <div class="eyebrow">LOCAL AI INTERPRETATION</div>
                    <h3>Interpretation</h3>
                    <p class="llm-text">{html.escape(llm_interpretation)}</p>
                    <p class="note">
                        Generated locally on the UNO Q from the measured values,
                        Edge Impulse result, and application-supplied interpretation rules.
                    </p>
                </div>
            """
        elif llm_status == "error":
            error_text = html.escape(llm_error or "Unknown LLM error")
            llm_block = f"""
                <div class="llm-card">
                    <div class="eyebrow">LOCAL AI INTERPRETATION</div>
                    <h3>AI interpretation unavailable</h3>
                    <p>
                        The water test itself completed normally. The optional local
                        language-model explanation could not be generated.
                    </p>
                    <details>
                        <summary>LLM error</summary>
                        <div class="llm-error">{error_text}</div>
                    </details>
                </div>
            """
        else:
            llm_block = ""

        if anomaly_score is not None and anomaly_threshold is not None:
            if is_anomaly:
                anomaly_css_class = "reference-card reference-warning"
                anomaly_heading = "Outside normal tap-water range"
                anomaly_symbol = "⚠"
                anomaly_explanation = (
                    "This sample differs from the tap-water measurements "
                    "used to train the reference model."
                )
            else:
                anomaly_css_class = "reference-card reference-ok"
                anomaly_heading = "Within normal tap-water range"
                anomaly_symbol = "✓"
                anomaly_explanation = (
                    "The sensor pattern is consistent with the tap-water "
                    "reference measurements used to train the model."
                )

            anomaly_block = f"""
                <div class="{anomaly_css_class}">
                    <div class="eyebrow">TAP-WATER REFERENCE</div>
                    <h2 class="reference-heading">
                        <span class="status-symbol">{anomaly_symbol}</span>
                        {html.escape(anomaly_heading)}
                    </h2>

                    <div class="anomaly-metrics">
                        <div>
                            <span>Anomaly score</span>
                            <strong>{anomaly_score:.3f}</strong>
                        </div>
                        <div>
                            <span>Reference threshold</span>
                            <strong>{anomaly_threshold:.3f}</strong>
                        </div>
                    </div>

                    <p>{html.escape(anomaly_explanation)}</p>

                    <p class="note">
                        This is an AI comparison against your recorded reference
                        samples. It is not a drinking-water safety test or
                        certification.
                    </p>
                </div>
            """
        else:
            anomaly_block = """
                <div class="reference-card">
                    <div class="eyebrow">TAP-WATER REFERENCE</div>
                    <h2>Reference result unavailable</h2>
                    <p>
                        The sample-group classification completed, but no anomaly
                        score was returned by the anomaly model.
                    </p>
                </div>
            """

        result_block = f"""
            <div class="result-card">
                <div class="eyebrow">LATEST TEST RESULT</div>
                <h2>
                    Closest known sample group:
                    {html.escape(friendly_label(prediction))}
                </h2>
                <div class="confidence">
                    Classifier confidence: {confidence}%
                </div>
                <p>
                    Of the sample groups currently included in the model,
                    this measurement is most similar to
                    <strong>{html.escape(friendly_label(prediction))}</strong>.
                </p>
                <p class="time">{html.escape(test_time)}</p>
            </div>

            {anomaly_block}

            <div class="measurements">
                <h3>Water measurements</h3>
                <div class="grid">
                    <div>
                        <span>pH</span>
                        <strong>{ph_value:.1f}</strong>
                        <small>approx.</small>
                    </div>
                    <div>
                        <span>ORP</span>
                        <strong>{orp_mv:+.0f} mV</strong>
                        <small>approx.</small>
                    </div>
                    <div>
                        <span>Turbidity</span>
                        <strong>{turbidity_ntu:.0f} NTU</strong>
                        <small>uncalibrated estimate</small>
                    </div>
                    <div>
                        <span>TDS</span>
                        <strong>{tds_ppm:.0f} ppm</strong>
                        <small>approx., assuming {TDS_ASSUMED_TEMPERATURE_C:.0f} °C</small>
                    </div>
                </div>

                <p class="note">
                    pH and TDS are approximate until calibrated and
                    temperature-compensated. ORP currently has no stored
                    calibration offset. Turbidity is an indicative,
                    uncalibrated estimate only.
                </p>

                <details>
                    <summary>Raw sensor averages</summary>
                    <div class="raw-grid">
                        <span>pH: {raw.get("ph_raw", "-")}</span>
                        <span>ORP: {raw.get("orp_raw", "-")}</span>
                        <span>Turbidity: {raw.get("turbidity_raw", "-")}</span>
                        <span>TDS: {raw.get("tds_raw", "-")}</span>
                    </div>
                </details>
            </div>

            {llm_block}
        """
    else:
        result_block = """
            <div class="result-card">
                <div class="eyebrow">UNO Q WATER ANALYZER</div>
                <h2>Dashboard is running</h2>
                <p>
                    No water test has been completed since the app started.
                    Run <strong>TEST WATER</strong> on the UNO Q and refresh this page.
                </p>
            </div>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="5">
    <title>UNO Q Water Analyzer</title>
    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f4f7f9;
            color: #16202a;
        }}

        main {{
            max-width: 720px;
            margin: 0 auto;
            padding: 24px 16px 40px;
        }}

        header {{
            margin-bottom: 18px;
        }}

        header h1 {{
            margin: 0;
            font-size: 1.65rem;
        }}

        header p {{
            margin: 6px 0 0;
            color: #5b6874;
        }}

        .result-card,
        .reference-card,
        .measurements,
        .llm-card {{
            background: white;
            border-radius: 18px;
            padding: 22px;
            margin-bottom: 16px;
            box-shadow: 0 4px 18px rgba(0,0,0,0.07);
        }}

        .reference-ok {{
            background: #f1f8f3;
            border: 1px solid #c9e2cf;
        }}

        .reference-warning {{
            background: #fff7ec;
            border: 1px solid #efd5ae;
        }}

        .eyebrow {{
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            color: #61707d;
        }}

        h2 {{
            margin: 8px 0 4px;
            font-size: 2rem;
        }}

        .reference-heading {{
            font-size: 1.55rem;
            line-height: 1.2;
        }}

        .status-symbol {{
            margin-right: 4px;
        }}

        h3 {{
            margin-top: 0;
        }}

        .confidence {{
            font-size: 1.15rem;
            font-weight: 700;
            margin-bottom: 18px;
        }}

        .anomaly-metrics {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin: 18px 0;
        }}

        .anomaly-metrics div {{
            background: rgba(255,255,255,0.72);
            border-radius: 12px;
            padding: 14px;
        }}

        .anomaly-metrics span {{
            display: block;
            font-size: 0.8rem;
            color: #60707c;
            margin-bottom: 4px;
        }}

        .anomaly-metrics strong {{
            display: block;
            font-size: 1.25rem;
        }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }}

        .grid div {{
            background: #f4f7f9;
            border-radius: 12px;
            padding: 14px;
        }}

        .grid span {{
            display: block;
            font-size: 0.8rem;
            color: #60707c;
            margin-bottom: 4px;
        }}

        .grid strong {{
            display: block;
            font-size: 1.25rem;
        }}

        .grid small {{
            display: block;
            margin-top: 4px;
            color: #78848d;
            font-size: 0.72rem;
            line-height: 1.3;
        }}

        details {{
            margin-top: 18px;
            color: #66737e;
            font-size: 0.82rem;
        }}

        summary {{
            cursor: pointer;
            font-weight: 600;
        }}

        .raw-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 6px 14px;
            margin-top: 10px;
            font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        }}

        .llm-text {{
            line-height: 1.55;
            white-space: pre-wrap;
        }}

        .llm-error {{
            margin-top: 10px;
            font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
            overflow-wrap: anywhere;
        }}

        .time,
        .note {{
            color: #66737e;
            font-size: 0.88rem;
        }}

        .sync-status {{
            font-size: 0.8rem;
            color: #66737e;
        }}

        footer {{
            color: #6b7781;
            font-size: 0.8rem;
            line-height: 1.45;
        }}

        @media (max-width: 460px) {{
            .grid,
            .anomaly-metrics {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <main>
        <header>
            <h1>UNO Q Water Analyzer</h1>
            <p>Local Edge AI water-sample report</p>
            <p class="sync-status" id="timeSyncStatus">{time_status}</p>
        </header>

        {result_block}

        <footer>
            Screening and comparison tool only. The classification and
            tap-water-reference result do not establish whether water is safe to drink.
        </footer>
    </main>

    <script>
        async function syncBrowserTime() {{
            const status = document.getElementById("timeSyncStatus");

            try {{
                const payload = {{
                    epoch_ms: Date.now(),
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || ""
                }};

                const response = await fetch("/api/time-sync", {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json"
                    }},
                    body: JSON.stringify(payload)
                }});

                if (!response.ok) {{
                    throw new Error("Time sync failed");
                }}

                const result = await response.json();

                if (status) {{
                    status.textContent = result.message;
                }}
            }}
            catch (error) {{
                if (status) {{
                    status.textContent = "Browser time synchronization unavailable";
                }}
            }}
        }}

        const serverTimeAlreadySynced = {str(time_synced).lower()};

        if (!serverTimeAlreadySynced) {{
            syncBrowserTime();
        }}
    </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/time-sync":
            self.send_error(404)
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", "0")
            )

            payload = json.loads(
                self.rfile.read(content_length).decode("utf-8")
            )

            epoch_ms = float(payload["epoch_ms"])
            timezone_name = str(payload.get("timezone", ""))

            system_clock_set = sync_time_from_browser(
                epoch_ms,
                timezone_name
            )

            if system_clock_set:
                message = "Time synchronized from browser to Linux clock"
            else:
                message = "Time synchronized from browser for app timestamps"

            response_data = json.dumps({
                "ok": True,
                "system_clock_set": system_clock_set,
                "message": message,
                "server_time": app_now().isoformat(),
            }).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.send_header(
                "Content-Length",
                str(len(response_data))
            )
            self.send_header(
                "Cache-Control",
                "no-store"
            )
            self.end_headers()
            self.wfile.write(response_data)

            print(
                f"Browser time sync: {app_now().strftime('%Y-%m-%d %H:%M:%S')} "
                f"({browser_time_source})"
            )

        except Exception as error:
            response_data = json.dumps({
                "ok": False,
                "error": str(error),
            }).encode("utf-8")

            self.send_response(400)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.send_header(
                "Content-Length",
                str(len(response_data))
            )
            self.end_headers()
            self.wfile.write(response_data)

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return

        page = dashboard_html().encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, format, *args):
        return


def start_dashboard():
    global dashboard_server

    dashboard_server = ThreadingHTTPServer(
        ("0.0.0.0", DASHBOARD_PORT),
        DashboardHandler,
    )

    thread = threading.Thread(
        target=dashboard_server.serve_forever,
        daemon=True,
    )
    thread.start()

    hostname = socket.gethostname()

    print()
    print("Local dashboard started")
    print(f"  Port: {DASHBOARD_PORT}")
    print(f"  Hostname: {hostname}")
    print(f"  QR URL: http://uno-q.local:{DASHBOARD_PORT}")
    print()


# ------------------------------------------------------------
# CSV helpers
# ------------------------------------------------------------

CSV_HEADER = [
    "timestamp",
    "ph_raw",
    "orp_raw",
    "turbidity_raw",
    "tds_raw",
]


def write_sample_csv(path, rows):
    with path.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as file:
        writer = csv.writer(file)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def next_sample_path(label):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pattern = re.compile(
        rf"^{re.escape(label)}\.(\d+)\.csv$"
    )
    highest_number = 0

    for path in OUTPUT_DIR.glob(f"{label}.*.csv"):
        match = pattern.match(path.name)

        if match:
            highest_number = max(
                highest_number,
                int(match.group(1))
            )

    return OUTPUT_DIR / (
        f"{label}.{highest_number + 1:03d}.csv"
    )


def calculate_raw_averages(rows):
    if not rows:
        return {}

    count = len(rows)

    return {
        "ph_raw": round(sum(row[1] for row in rows) / count),
        "orp_raw": round(sum(row[2] for row in rows) / count),
        "turbidity_raw": round(sum(row[3] for row in rows) / count),
        "tds_raw": round(sum(row[4] for row in rows) / count),
    }


# ------------------------------------------------------------
# Edge Impulse inference
# ------------------------------------------------------------

def run_inference(sample_path):
    """Run one CSV sample through classifier and anomaly EIM models."""

    if not EI_PYTHON.exists():
        print(
            f"INFERENCE ERROR: EI Python not found: {EI_PYTHON}"
        )
        return None

    if not INFERENCE_SCRIPT.exists():
        print(
            f"INFERENCE ERROR: Helper not found: {INFERENCE_SCRIPT}"
        )
        return None

    try:
        completed = subprocess.run(
            [
                str(EI_PYTHON),
                str(INFERENCE_SCRIPT),
                str(sample_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("INFERENCE ERROR: Timed out.")
        return None
    except Exception as error:
        print(f"INFERENCE ERROR: {error}")
        return None

    if completed.returncode != 0:
        print("INFERENCE ERROR:")

        if completed.stderr:
            print(completed.stderr.strip())

        if completed.stdout:
            print(completed.stdout.strip())

        return None

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(
            "INFERENCE ERROR: Invalid JSON returned by inference helper."
        )
        print(completed.stdout)
        return None

    classification = result.get("classification", {})
    prediction = str(result.get("prediction", ""))
    confidence = float(result.get("confidence", 0.0))

    anomaly_score = result.get("anomaly_score")
    anomaly_threshold = result.get("anomaly_threshold")
    is_anomaly = result.get("is_anomaly")

    print()
    print("======================================")
    print("EDGE IMPULSE INFERENCE")
    print()

    print("WATER-TYPE CLASSIFICATION")

    for label, score in classification.items():
        print(f"{label}: {score:.6f}")

    print()
    print(f"Prediction: {prediction}")
    print(f"Confidence: {confidence * 100:.2f}%")

    print()
    print("TAP-WATER REFERENCE")

    if anomaly_score is not None and anomaly_threshold is not None:
        anomaly_score = float(anomaly_score)
        anomaly_threshold = float(anomaly_threshold)

        derived_is_anomaly = anomaly_score > anomaly_threshold

        if derived_is_anomaly:
            reference_text = "DIFFERS FROM TAP-WATER REFERENCE"
        else:
            reference_text = "WITHIN TAP-WATER REFERENCE RANGE"

        print(f"Anomaly score: {anomaly_score:.3f}")
        print(f"Threshold: {anomaly_threshold:.3f}")
        print(f"Status: {reference_text}")
    else:
        print("Status: anomaly result unavailable")

    print("======================================")
    print()

    return result


# ------------------------------------------------------------
# Local LLM interpretation
# ------------------------------------------------------------

def generate_llm_interpretation(
    generation_id,
    prediction,
    confidence_percent,
    raw_averages,
    anomaly_score,
    anomaly_threshold,
    is_anomaly,
):
    """Generate an optional explanation from deterministic application conclusions."""

    global last_llm_status
    global last_llm_interpretation
    global last_llm_error

    try:
        ph_value = ph_from_raw(raw_averages.get("ph_raw", 0))
        tds_ppm = tds_from_raw(raw_averages.get("tds_raw", 0))
        orp_mv = orp_from_raw(raw_averages.get("orp_raw", 0))
        turbidity_ntu = turbidity_ntu_from_raw(
            raw_averages.get("turbidity_raw", 0)
        )

        classifier_indicates_tap = prediction == "Tap_water"
        reference_indicates_tap = not is_anomaly

        relationship = (
            "consistent"
            if classifier_indicates_tap == reference_indicates_tap
            else "conflicting"
        )
        reference_status = "outside" if is_anomaly else "within"

        print()
        print("Starting local LLM interpretation in background...")

        with llm_call_lock:
            interpretation = interpret_water_result(
                relationship=relationship,
                reference_status=reference_status,
                ph=ph_value,
                tds=tds_ppm,
                orp=orp_mv,
                turbidity=turbidity_ntu,
            )

        with sample_lock:
            if generation_id != llm_generation_id:
                print("Discarding stale LLM interpretation from an older test.")
                return

            last_llm_interpretation = interpretation
            last_llm_status = "ready"
            last_llm_error = ""

        print("Local LLM interpretation ready.")

    except Exception as error:
        with sample_lock:
            if generation_id != llm_generation_id:
                return

            last_llm_interpretation = ""
            last_llm_status = "error"
            last_llm_error = str(error)

        print(f"LLM INTERPRETATION ERROR: {error}")

def start_llm_interpretation(
    prediction,
    confidence_percent,
    raw_averages,
    anomaly_score,
    anomaly_threshold,
    is_anomaly,
):
    """Start one background LLM job for the latest completed water test."""

    global last_llm_status
    global last_llm_interpretation
    global last_llm_error

    with sample_lock:
        generation_id = llm_generation_id
        last_llm_status = "generating"
        last_llm_interpretation = ""
        last_llm_error = ""

    thread = threading.Thread(
        target=generate_llm_interpretation,
        args=(
            generation_id,
            prediction,
            confidence_percent,
            dict(raw_averages),
            anomaly_score,
            anomaly_threshold,
            is_anomaly,
        ),
        daemon=True,
    )
    thread.start()


# ------------------------------------------------------------
# Shared Bridge sample helpers
# ------------------------------------------------------------

def reset_current_sample():
    global current_rows
    global current_label
    global current_mode

    current_rows = []
    current_label = None
    current_mode = None


def take_current_sample(expected_mode):
    """Atomically remove and return the current sample rows."""

    with sample_lock:
        if current_mode != expected_mode:
            print(
                f"ERROR: Expected {expected_mode} sample, "
                f"found {current_mode}."
            )
            return None, None

        if not current_rows:
            print("ERROR: No sample rows received.")
            return None, None

        label = current_label
        rows = list(current_rows)
        reset_current_sample()

    return label, rows


# ------------------------------------------------------------
# Bridge functions callable by the MCU
# ------------------------------------------------------------

def linux_started():
    return True


def get_label_count():
    return len(LABELS)


def get_label(index):
    index = int(index)

    if index < 0 or index >= len(LABELS):
        return ""

    return LABELS[index]


def begin_sample(label):
    """Start a labelled dataset-collection transfer."""

    global current_rows
    global current_label
    global current_mode

    label = str(label)

    if label not in LABELS:
        print(f"ERROR: MCU requested unknown label: {label}")
        return False

    with sample_lock:
        current_mode = "collect"
        current_label = label
        current_rows = []

    print()
    print(f"Receiving collection sample: {label}")
    return True


def begin_test_sample():
    """Start an unlabelled test transfer used only for inference."""

    global current_rows
    global current_label
    global current_mode
    global last_test_prediction
    global last_test_confidence_percent
    global last_test_time
    global last_test_raw_averages
    global last_test_anomaly_score
    global last_test_anomaly_threshold
    global last_test_is_anomaly
    global last_test_anomaly_status
    global last_llm_status
    global last_llm_interpretation
    global last_llm_error
    global llm_generation_id

    with sample_lock:
        current_mode = "test"
        current_label = None
        current_rows = []

        last_test_prediction = ""
        last_test_confidence_percent = 0
        last_test_time = ""
        last_test_raw_averages = {}

        last_test_anomaly_score = None
        last_test_anomaly_threshold = None
        last_test_is_anomaly = None
        last_test_anomaly_status = ""

        last_llm_status = "idle"
        last_llm_interpretation = ""
        last_llm_error = ""
        llm_generation_id += 1

    print()
    print("Receiving temporary test sample")
    return True


def add_sample_row(
    time_ms,
    ph_raw,
    orp_raw,
    turbidity_raw,
    tds_raw
):
    row = [
        int(time_ms),
        int(ph_raw),
        int(orp_raw),
        int(turbidity_raw),
        int(tds_raw),
    ]

    with sample_lock:
        if current_mode is None:
            return False

        current_rows.append(row)

    return True


def finish_sample():
    """Save one collection sample without running inference."""

    label, rows_to_save = take_current_sample("collect")

    if rows_to_save is None:
        return ""

    output_path = next_sample_path(label)
    write_sample_csv(output_path, rows_to_save)

    print()
    print("======================================")
    print(f"SAVED: {output_path}")
    print(f"Rows: {len(rows_to_save)}")
    print("Mode: COLLECT DATA (inference not run)")
    print("======================================")
    print()

    return str(output_path)


def finish_test_sample():
    """Infer one test sample using a temporary CSV, then delete it."""

    global last_test_prediction
    global last_test_confidence_percent
    global last_test_time
    global last_test_raw_averages
    global last_test_anomaly_score
    global last_test_anomaly_threshold
    global last_test_is_anomaly
    global last_test_anomaly_status

    _, rows_to_test = take_current_sample("test")

    if rows_to_test is None:
        return False

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            suffix=".csv",
            prefix="uno_q_water_test_",
            delete=False,
        ) as file:
            writer = csv.writer(file)
            writer.writerow(CSV_HEADER)
            writer.writerows(rows_to_test)
            temporary_path = Path(file.name)

        result = run_inference(temporary_path)

        if result is None:
            return False

        prediction = str(result.get("prediction", ""))
        confidence = float(result.get("confidence", 0.0))

        anomaly_score = result.get("anomaly_score")
        anomaly_threshold = result.get("anomaly_threshold")
        is_anomaly = result.get("is_anomaly")

        if not prediction:
            print("INFERENCE ERROR: Prediction is empty.")
            return False

        if anomaly_score is None:
            print("INFERENCE ERROR: Anomaly score is missing.")
            return False

        if anomaly_threshold is None:
            print("INFERENCE ERROR: Anomaly threshold is missing.")
            return False

        anomaly_score = float(anomaly_score)
        anomaly_threshold = float(anomaly_threshold)

        # Derive the authoritative status locally from the numeric rule.
        # The helper-provided flag is checked only for consistency.
        reported_is_anomaly = is_anomaly
        is_anomaly = anomaly_score > anomaly_threshold

        if reported_is_anomaly is not None:
            if isinstance(reported_is_anomaly, bool):
                reported_boolean = reported_is_anomaly
            elif isinstance(reported_is_anomaly, (int, float)):
                reported_boolean = reported_is_anomaly != 0
            else:
                reported_boolean = (
                    str(reported_is_anomaly).strip().lower()
                    in ("true", "1", "yes")
                )

            if reported_boolean != is_anomaly:
                print(
                    "WARNING: Anomaly status returned by inference helper "
                    "does not match score/threshold comparison."
                )
                print(
                    f"  Score: {anomaly_score:.3f}, "
                    f"threshold: {anomaly_threshold:.3f}"
                )
                print(
                    f"  Helper status: {reported_boolean}, "
                    f"derived status: {is_anomaly}"
                )

        anomaly_status = (
            "Differs from tap-water reference"
            if is_anomaly
            else "Within tap-water reference range"
        )

        confidence_percent = max(
            0,
            min(100, round(confidence * 100))
        )
        raw_averages = calculate_raw_averages(rows_to_test)

        with sample_lock:
            last_test_prediction = prediction
            last_test_confidence_percent = confidence_percent
            last_test_time = app_now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            last_test_raw_averages = raw_averages

            last_test_anomaly_score = anomaly_score
            last_test_anomaly_threshold = anomaly_threshold
            last_test_is_anomaly = is_anomaly
            last_test_anomaly_status = anomaly_status

        start_llm_interpretation(
            prediction=prediction,
            confidence_percent=confidence_percent,
            raw_averages=raw_averages,
            anomaly_score=anomaly_score,
            anomaly_threshold=anomaly_threshold,
            is_anomaly=is_anomaly,
        )

        return True
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def get_test_prediction():
    with sample_lock:
        return last_test_prediction


def get_test_confidence():
    with sample_lock:
        return last_test_confidence_percent


def get_test_anomaly_score():
    with sample_lock:
        if last_test_anomaly_score is None:
            return -1.0

        return float(last_test_anomaly_score)


def get_test_anomaly_threshold():
    with sample_lock:
        if last_test_anomaly_threshold is None:
            return -1.0

        return float(last_test_anomaly_threshold)


def get_test_is_anomaly():
    with sample_lock:
        if last_test_is_anomaly is None:
            return -1

        return 1 if last_test_is_anomaly else 0


def discard_sample():
    with sample_lock:
        reset_current_sample()

    print("Sample discarded.")
    return True


# ------------------------------------------------------------
# Register Bridge functions
# ------------------------------------------------------------

Bridge.provide("linux_started", linux_started)
Bridge.provide("get_label_count", get_label_count)
Bridge.provide("get_label", get_label)
Bridge.provide("begin_sample", begin_sample)
Bridge.provide("begin_test_sample", begin_test_sample)
Bridge.provide("add_sample_row", add_sample_row)
Bridge.provide("finish_sample", finish_sample)
Bridge.provide("finish_test_sample", finish_test_sample)
Bridge.provide("get_test_prediction", get_test_prediction)
Bridge.provide("get_test_confidence", get_test_confidence)
Bridge.provide("get_test_anomaly_score", get_test_anomaly_score)
Bridge.provide("get_test_anomaly_threshold", get_test_anomaly_threshold)
Bridge.provide("get_test_is_anomaly", get_test_is_anomaly)
Bridge.provide("discard_sample", discard_sample)


# ------------------------------------------------------------
# App loop and startup information
# ------------------------------------------------------------

def loop():
    time.sleep(0.1)


print()
print("UNO Q water sample logger and tester")
print("Labels:")

for index, label in enumerate(LABELS, start=1):
    print(f"  {index} = {label}")

print(f"Sample directory: {OUTPUT_DIR}")
print(f"Inference helper: {INFERENCE_SCRIPT}")
print(f"Python interpreter: {EI_PYTHON}")
print("Waiting for MCU...")
print()

start_dashboard()
App.run(user_loop=loop)

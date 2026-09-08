# Provides explicit WaterLens hotspot control and restores the exact Wi-Fi profile that was active before AP mode. 2026-09-08 21:54 Europe/Helsinki, Thomas Vikström.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess

HOST = "0.0.0.0"
PORT = 5055
WIFI_DEVICE = "wlan0"
HOTSPOT_PROFILE = "Hotspot"
HOTSPOT_SSID = "WaterLens"
HOTSPOT_IP = "10.42.0.1"

# Remember the exact client profile that was active when offline mode was started.
# This avoids relying on NetworkManager to guess which saved network to reconnect.
previous_wifi_profile = ""


def run_nmcli(*args, timeout=30):
    completed = subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "nmcli failed").strip()
        raise RuntimeError(message)
    return completed.stdout.strip()


def active_wifi_profile():
    output = run_nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
    for line in output.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) == 3 and parts[1] == "802-11-wireless" and parts[2] == WIFI_DEVICE:
            return parts[0]
    return ""


def saved_client_profiles():
    """Return saved Wi-Fi profiles other than the WaterLens hotspot."""
    output = run_nmcli("-t", "-f", "NAME,TYPE", "connection", "show")
    profiles = []

    for line in output.splitlines():
        parts = line.rsplit(":", 1)
        if len(parts) != 2:
            continue

        name, connection_type = parts
        if connection_type == "802-11-wireless" and name != HOTSPOT_PROFILE:
            profiles.append(name)

    return profiles


def wifi_ipv4():
    output = run_nmcli("-g", "IP4.ADDRESS", "device", "show", WIFI_DEVICE)
    first = output.splitlines()[0].strip() if output else ""
    return first.split("/", 1)[0] if first else ""


def status_payload():
    profile = active_wifi_profile()
    return {
        "ok": True,
        "profile": profile,
        "hotspot": profile == HOTSPOT_PROFILE,
        "ssid": HOTSPOT_SSID if profile == HOTSPOT_PROFILE else "",
        "ip": wifi_ipv4(),
        "previous_profile": previous_wifi_profile,
    }


def start_hotspot():
    global previous_wifi_profile

    current = active_wifi_profile()

    if current and current != HOTSPOT_PROFILE:
        previous_wifi_profile = current

    run_nmcli("connection", "up", HOTSPOT_PROFILE)
    return status_payload()


def return_to_wifi():
    global previous_wifi_profile

    # Prefer the exact Wi-Fi profile that was active before hotspot mode.
    candidates = []

    if previous_wifi_profile and previous_wifi_profile != HOTSPOT_PROFILE:
        candidates.append(previous_wifi_profile)

    # Recovery fallback for cases where the network-control brick was restarted
    # while hotspot mode was active and therefore lost the in-memory profile.
    for profile in saved_client_profiles():
        if profile not in candidates:
            candidates.append(profile)

    last_error = "No saved normal Wi-Fi profile is available"

    for profile in candidates:
        try:
            # Bringing the client profile up directly is the same method that was
            # verified manually on the UNO Q; NetworkManager replaces the AP on wlan0.
            run_nmcli("connection", "up", profile, timeout=40)
            active = active_wifi_profile()

            if active == profile:
                previous_wifi_profile = profile
                return status_payload()

            last_error = f"Profile '{profile}' did not become active"
        except Exception as error:
            last_error = str(error)

    raise RuntimeError(last_error)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/status":
            self.send_json(404, {"ok": False, "error": "Not found"})
            return
        try:
            self.send_json(200, status_payload())
        except Exception as error:
            self.send_json(500, {"ok": False, "error": str(error)})

    def do_POST(self):
        try:
            if self.path == "/hotspot":
                self.send_json(200, start_hotspot())
            elif self.path == "/normal":
                self.send_json(200, return_to_wifi())
            else:
                self.send_json(404, {"ok": False, "error": "Not found"})
        except Exception as error:
            self.send_json(500, {"ok": False, "error": str(error)})

    def log_message(self, format, *args):
        return


print(f"WaterLens network control listening on {HOST}:{PORT}")
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

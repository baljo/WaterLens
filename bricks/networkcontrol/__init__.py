# Exposes simple Python helpers for explicit WaterLens hotspot control from the main App Lab process. 2026-09-08 21:19 Europe/Helsinki, Thomas Vikström.
import requests

BASE_URL = "http://networkcontrol:5055"


def status():
    return requests.get(f"{BASE_URL}/status", timeout=10).json()


def start_hotspot():
    return requests.post(f"{BASE_URL}/hotspot", timeout=25).json()


def return_to_wifi():
    return requests.post(f"{BASE_URL}/normal", timeout=25).json()

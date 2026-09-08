# Runs one UNO Q water sample through classification and tap-water anomaly EIM models. Thomas Vikström, 2026-09-04 13:43 Europe/Helsinki.
from pathlib import Path
import csv
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


BASE_DIR = Path(__file__).resolve().parent

CLASSIFIER_MODEL = BASE_DIR / "model.eim"
ANOMALY_MODEL = BASE_DIR / "anomaly.eim"

ANOMALY_THRESHOLD = 20.0


class EIMRunner:
    def __init__(self, model_path, timeout=30):
        self.model_path = Path(model_path)
        self.timeout = timeout
        self.temp_dir = None
        self.process = None
        self.client = None
        self.message_id = 0

    def start(self):
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model not found: {self.model_path}"
            )

        if not os.access(self.model_path, os.X_OK):
            self.model_path.chmod(
                self.model_path.stat().st_mode | 0o111
            )

        self.temp_dir = tempfile.mkdtemp(
            prefix="ei-eim-"
        )

        socket_path = str(
            Path(self.temp_dir) / "runner.sock"
        )

        self.process = subprocess.Popen(
            [
                str(self.model_path),
                socket_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + self.timeout

        while not Path(socket_path).exists():
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"EIM exited with code "
                    f"{self.process.returncode}"
                )

            if time.time() > deadline:
                raise TimeoutError(
                    "Timed out waiting for EIM socket."
                )

            time.sleep(0.05)

        self.client = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )

        self.client.settimeout(
            self.timeout
        )

        self.client.connect(
            socket_path
        )

        return self.send_message(
            {"hello": 1}
        )

    def send_message(self, message):
        self.message_id += 1

        message = dict(
            message
        )

        message["id"] = self.message_id

        payload = json.dumps(
            message
        ).encode(
            "utf-8"
        )

        self.client.sendall(
            payload
        )

        data = b""

        while True:
            chunk = self.client.recv(
                4096
            )

            if not chunk:
                raise RuntimeError(
                    "EIM connection closed unexpectedly."
                )

            if chunk[-1] == 0:
                data += chunk[:-1]
                break

            data += chunk

        text = data.decode(
            "utf-8"
        )

        start = text.find(
            "{"
        )

        end = text.rfind(
            "}"
        )

        if start < 0 or end < start:
            raise RuntimeError(
                "Invalid response from EIM."
            )

        response = json.loads(
            text[start:end + 1]
        )

        if response.get("id") != self.message_id:
            raise RuntimeError(
                "Incorrect response ID from EIM."
            )

        if not response.get(
            "success",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "Unknown EIM error"
                )
            )

        response.pop(
            "id",
            None
        )

        response.pop(
            "success",
            None
        )

        return response

    def classify(self, features):
        return self.send_message({
            "classify": features
        })

    def stop(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

            self.client = None

        if self.process is not None:
            if self.process.poll() is None:
                try:
                    self.process.send_signal(
                        signal.SIGINT
                    )

                    self.process.wait(
                        timeout=2
                    )

                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass

            self.process = None

        if self.temp_dir is not None:
            shutil.rmtree(
                self.temp_dir,
                ignore_errors=True
            )

            self.temp_dir = None


def load_features(csv_path):
    features = []

    with csv_path.open(
        "r",
        newline="",
        encoding="utf-8"
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:
            features.extend([
                float(row["ph_raw"]),
                float(row["orp_raw"]),
                float(row["turbidity_raw"]),
                float(row["tds_raw"]),
            ])

    if len(features) != 400:
        raise ValueError(
            "Expected 400 values "
            "(100 rows x 4 channels), "
            f"got {len(features)}"
        )

    return features


def run_model(model_path, features):
    runner = EIMRunner(
        model_path
    )

    try:
        model_info = runner.start()

        result = runner.classify(
            features
        )

        return model_info, result

    finally:
        runner.stop()


def get_anomaly_score(result):
    result_data = result.get(
        "result",
        {}
    )

    anomaly = result_data.get(
        "anomaly"
    )

    if anomaly is None:
        raise RuntimeError(
            "Anomaly model returned no anomaly score. "
            f"Result was: {result_data}"
        )

    if isinstance(anomaly, (int, float)):
        return float(
            anomaly
        )

    if isinstance(anomaly, dict):
        for key in (
            "score",
            "value",
            "anomaly_score",
        ):
            if key in anomaly:
                return float(
                    anomaly[key]
                )

    raise RuntimeError(
        "Could not interpret anomaly result: "
        f"{anomaly}"
    )


def main():
    if len(sys.argv) != 2:
        raise ValueError(
            "Expected one CSV filename."
        )

    csv_path = Path(
        sys.argv[1]
    )

    features = load_features(
        csv_path
    )

    classifier_info, classifier_result = run_model(
        CLASSIFIER_MODEL,
        features
    )

    classification = classifier_result[
        "result"
    ][
        "classification"
    ]

    prediction = max(
        classification,
        key=classification.get
    )

    confidence = classification[
        prediction
    ]

    anomaly_info, anomaly_result = run_model(
        ANOMALY_MODEL,
        features
    )

    anomaly_score = get_anomaly_score(
        anomaly_result
    )

    is_anomaly = (
        anomaly_score >= ANOMALY_THRESHOLD
    )

    anomaly_status = (
        "anomaly"
        if is_anomaly
        else "no anomaly"
    )

    output = {
        "model": classifier_info[
            "project"
        ][
            "name"
        ],
        "classification": classification,
        "prediction": prediction,
        "confidence": confidence,
        "timing": classifier_result.get(
            "timing",
            {}
        ),

        "anomaly_model": anomaly_info[
            "project"
        ][
            "name"
        ],
        "anomaly_score": anomaly_score,
        "anomaly_threshold": ANOMALY_THRESHOLD,
        "anomaly_status": anomaly_status,
        "is_anomaly": is_anomaly,
        "anomaly_timing": anomaly_result.get(
            "timing",
            {}
        ),
    }

    print(
        json.dumps(
            output
        )
    )


if __name__ == "__main__":
    main()
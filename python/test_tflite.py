# Tests the TFLite water model against one saved CSV using LiteRT. Thomas Vikstrom, 2026-09-01 17:26 Europe/Helsinki.
import csv
import sys
import numpy as np
from ai_edge_litert.interpreter import Interpreter

MODEL = "water_quality.tflite"
LABELS = ["Sea_water", "Tap_water"]


def load_csv(path):
    values = []

    with open(path, newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            values.extend([
                float(row["ph_raw"]),
                float(row["orp_raw"]),
                float(row["turbidity_raw"]),
                float(row["tds_raw"]),
            ])

    if len(values) != 400:
        raise ValueError(f"Expected 400 values, got {len(values)}")

    return np.array(values, dtype=np.float32)


def main():
    if len(sys.argv) != 2:
        print("Usage: python test_water_tflite.py sample.csv")
        sys.exit(1)

    features = load_csv(sys.argv[1])

    interpreter = Interpreter(model_path=MODEL)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    print("Input shape:", input_details[0]["shape"])
    print("Input dtype:", input_details[0]["dtype"])
    print("Output shape:", output_details[0]["shape"])
    print("Output dtype:", output_details[0]["dtype"])

    input_data = features.reshape(input_details[0]["shape"])

    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()

    output = interpreter.get_tensor(output_details[0]["index"]).flatten()

    print("\nClassification:")
    for label, score in zip(LABELS, output):
        print(f"  {label}: {score:.12f}")

    best_index = int(np.argmax(output))

    print(f"\nPrediction: {LABELS[best_index]}")
    print(f"Confidence: {output[best_index] * 100:.2f}%")


if __name__ == "__main__":
    main()
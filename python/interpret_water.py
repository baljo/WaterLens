# Generates validated local-LLM synthesis, exposes explicit offline Wi-Fi controls, and enables WaterLens dashboard branding. 2026-09-08 21:40 Europe/Helsinki, Thomas Vikström.

import re

from arduino.app_bricks.llm import LargeLanguageModel
from arduino.app_utils import Bridge
from dashboard_branding import install_dashboard_branding
from networkcontrol import return_to_wifi, start_hotspot, status as network_status


install_dashboard_branding()


SYSTEM_PROMPT = """
You provide a concise interpretation of already-established UNO Q water-test results.

The application has already performed classification, anomaly detection,
numeric comparisons, and logical decisions. Treat supplied conclusions as facts.
Do not redo arithmetic or contradict them.

Rules:
- Use only supplied information.
- Do not repeat the displayed sample-group name, classifier confidence,
  anomaly score, or anomaly threshold.
- The classifier selects the closest learned sample group.
- The tap-water reference model separately checks whether the combined sensor
  pattern falls within the variation learned from recorded tap-water samples.
- Do not describe the reference result as proof of the water's origin.
- Do not say that agreement between the two models independently verifies or
  proves a conclusion; both use the same underlying sensor measurement.
- Do not infer contaminants, causes, source, location, or safety.
- Do not interpret individual pH, TDS, ORP, or turbidity values as high, low,
  normal, abnormal, good, bad, elevated, or reduced.
- pH, TDS and ORP are approximate with the current calibration state.
- Turbidity is an uncalibrated estimate.
- Never claim or imply that the water is safe or unsafe to drink.
- Never say that being inside or outside the tap-water reference determines
  drinking-water safety.
- Write exactly three concise sentences.
"""


llm = LargeLanguageModel(
    system_prompt=SYSTEM_PROMPT,
    temperature=0.1,
    max_tokens=150,
    timeout=180,
)


def clean_generated_text(text):
    """Remove repeated sentences and normalize whitespace."""

    text = " ".join(str(text).split())
    sentences = re.split(r"(?<=[.!?])\s+", text)

    seen = set()
    cleaned = []

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        normalized = " ".join(sentence.lower().split())

        if normalized in seen:
            continue

        seen.add(normalized)
        cleaned.append(sentence)

    return " ".join(cleaned)


def interpretation_is_valid(text):
    """Reject unsupported safety/origin claims or incorrect attribution of model conclusions."""

    normalized = " ".join(str(text).lower().split())

    forbidden_phrases = [
        "safe drinking water",
        "unsafe drinking water",
        "safe to drink",
        "unsafe to drink",
        "not safe",
        "is safe",
        "is unsafe",
        "confirms the water is",
        "proves the water is",
        "proves that the water is",
        "confirms that the water is",
        "classifier and the reference model agree that",
        "classifier and reference model agree that",
        "classifier confirms",
        "classifier proves",
        "reference model confirms the water",
        "reference model proves the water",
    ]

    if any(phrase in normalized for phrase in forbidden_phrases):
        return False

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\\s+", str(text).strip())
        if sentence.strip()
    ]

    return 2 <= len(sentences) <= 4 and len(str(text)) <= 850


def deterministic_fallback(relationship, reference_status):
    """Return a concise scientifically conservative interpretation without using the LLM."""

    reference_indicates_tap = reference_status == "within"

    if relationship == "consistent":
        classifier_indicates_tap = reference_indicates_tap
    else:
        classifier_indicates_tap = not reference_indicates_tap

    if classifier_indicates_tap:
        classifier_phrase = "the closest learned sample group is tap water"
    else:
        classifier_phrase = "the closest learned sample group is not tap water"

    if reference_status == "outside":
        reference_phrase = (
            "the combined sensor pattern falls outside the variation learned "
            "from the recorded tap-water samples"
        )
    else:
        reference_phrase = (
            "the combined sensor pattern falls within the variation learned "
            "from the recorded tap-water samples"
        )

    if relationship == "consistent":
        sentence_1 = (
            f"The two Edge AI results are consistent: {classifier_phrase}, and "
            f"{reference_phrase}."
        )
    else:
        sentence_1 = (
            f"The two Edge AI results point in different directions: {classifier_phrase}, "
            f"while {reference_phrase}."
        )

    sentence_2 = (
        "Together, these results describe similarity to the learned sample groups and "
        "tap-water reference, but they do not establish the sample's actual origin or "
        "identify what caused the observed sensor pattern."
    )

    sentence_3 = (
        "The individual sensor values are still approximate or uncalibrated, and this "
        "sensor set cannot establish drinking-water safety."
    )

    return " ".join([sentence_1, sentence_2, sentence_3])


def interpret_water_result(
    relationship,
    reference_status,
    ph=None,
    tds=None,
    orp=None,
    turbidity=None,
):
    """Explain deterministic classifier/reference conclusions and measurement limits."""

    if relationship not in ("consistent", "conflicting"):
        raise ValueError(
            f"Unexpected classifier/anomaly relationship: {relationship}"
        )

    if reference_status not in ("within", "outside"):
        raise ValueError(
            f"Unexpected tap-water reference status: {reference_status}"
        )

    if relationship == "consistent":
        relationship_fact = (
            "The closest-sample-group classification and the tap-water "
            "reference check point in the same general direction."
        )
    else:
        relationship_fact = (
            "The closest-sample-group classification and the tap-water "
            "reference check point in different directions."
        )

    if reference_status == "within":
        reference_fact = (
            "The application has established that the combined sensor pattern "
            "falls within the learned tap-water reference range."
        )
    else:
        reference_fact = (
            "The application has established that the combined sensor pattern "
            "falls outside the learned tap-water reference range."
        )

    measurement_facts = []

    if ph is not None:
        measurement_facts.append(f"Approximate pH: {ph:.1f}")

    if tds is not None:
        measurement_facts.append(f"Approximate TDS: {tds:.0f} ppm")

    if orp is not None:
        measurement_facts.append(f"Approximate ORP: {orp:+.0f} mV")

    if turbidity is not None:
        measurement_facts.append(
            f"Uncalibrated turbidity estimate: {turbidity:.0f} NTU"
        )

    measurements_text = "\n".join(
        "- " + item for item in measurement_facts
    )

    reference_indicates_tap = reference_status == "within"

    if relationship == "consistent":
        classifier_indicates_tap = reference_indicates_tap
    else:
        classifier_indicates_tap = not reference_indicates_tap

    classifier_fact = (
        "The closest learned sample group is tap water."
        if classifier_indicates_tap
        else "The closest learned sample group is not tap water."
    )

    prompt = f"""
Established application conclusions:
- {classifier_fact}
- {reference_fact}
- The classifier/reference relationship is {relationship}.

How to synthesize them:
- If the relationship is consistent, sentence 1 should say:
  "The two Edge AI results are consistent:" and then explain, without naming
  the displayed sample group, whether the closest learned group is tap water
  or not and whether the combined pattern is within or outside the learned
  tap-water variation.
- If the relationship is conflicting, sentence 1 should clearly explain the
  two different directions.
- Do not imply that agreement constitutes independent verification.

Available sensor readouts:
{measurements_text if measurements_text else "- No sensor measurements supplied."}

Measurement limitations:
- pH, TDS and ORP are approximate with the current calibration state.
- Turbidity is an uncalibrated estimate.
- The sensor set cannot identify the specific cause of the observed pattern.
- The result does not establish the sample's actual origin.
- The sensor set cannot establish drinking-water safety.

Write exactly three concise sentences.

Sentence 1:
Synthesize the classifier and tap-water reference results.

Sentence 2:
Explain that the models describe similarity to learned examples/reference
patterns but cannot establish actual origin or the cause of the pattern.

Sentence 3:
Combine the calibration limitation and drinking-water-safety limitation.

Do not list or repeat the numeric sensor values.
Do not use headings or bullet points.
"""

    result = clean_generated_text(llm.chat(prompt))

    if not interpretation_is_valid(result):
        print(
            "WARNING: Local LLM interpretation failed scientific/safety validation; "
            "using deterministic fallback."
        )
        return deterministic_fallback(
            relationship=relationship,
            reference_status=reference_status,
        )

    return result


# ------------------------------------------------------------
# Explicit network controls exposed to the MCU through Bridge
# ------------------------------------------------------------


def start_offline_connection():
    """Switch wlan0 to the already-tested WaterLens hotspot profile."""
    try:
        result = start_hotspot()
        return bool(result.get("ok") and result.get("hotspot"))
    except Exception as error:
        print(f"OFFLINE CONNECTION ERROR: {error}")
        return False


def stop_offline_connection():
    """Leave hotspot mode and let NetworkManager reconnect a saved Wi-Fi profile."""
    try:
        result = return_to_wifi()
        return bool(result.get("ok") and not result.get("hotspot"))
    except Exception as error:
        print(f"NORMAL WI-FI RESTORE ERROR: {error}")
        return False


def offline_connection_active():
    """Report whether wlan0 currently uses the WaterLens hotspot profile."""
    try:
        return bool(network_status().get("hotspot"))
    except Exception as error:
        print(f"NETWORK STATUS ERROR: {error}")
        return False


Bridge.provide("start_offline_connection", start_offline_connection)
Bridge.provide("stop_offline_connection", stop_offline_connection)
Bridge.provide("offline_connection_active", offline_connection_active)


if __name__ == "__main__":
    result = interpret_water_result(
        relationship="consistent",
        reference_status="outside",
        ph=5.9,
        tds=0,
        orp=248,
        turbidity=2949,
    )

    print()
    print("======================================")
    print("LOCAL LLM INTERPRETATION")
    print("======================================")
    print(result)
    print("======================================")

# WaterLens
Edge AI water analysis with Arduino UNO Q

WaterLens is an experimental water-analysis prototype built with the Arduino UNO Q, four Grove water sensors, Edge Impulse machine learning, and a local web dashboard.

The system records a multichannel sensor pattern from a water sample and evaluates it in two complementary ways:

1. a multiclass model compares the sample with previously learned sample groups;
2. a separate anomaly model compares it with a learned tap-water reference.

WaterLens explores Edge AI, sensor fusion, time-series classification, anomaly detection, and local AI interpretation using inexpensive sensors.

---

# Hardware

## Main controller

* Arduino UNO Q, 4 GB
* Grove Base Shield V2.0

## Sensors and connections

| Sensor                 | Purpose                        | Connection |
| ---------------------- | ------------------------------ | ---------- |
| Grove pH Sensor Kit    | pH-related voltage measurement | A0         |
| Grove TDS Sensor       | Total dissolved solids proxy   | A1         |
| Grove Turbidity Sensor | Optical turbidity measurement  | A2         |
| Grove ORP Pro          | Oxidation-reduction potential  | A3         |
| Grove Dual Button      | Local user interface           | D2 Grove port |
| Grove 0.96" OLED       | Instructions and status        | I²C        |

Measurements are performed in a 400 mL laboratory beaker.

A custom holder is used to keep the turbidity sensor at a repeatable position in the sample.

---

# Sampling procedure

Each complete sample contains:

* 100 time steps per channel
* 100 ms sampling interval
* 10 Hz sampling frequency
* approximately 10 seconds of data per channel
* four sensor channels
* two sequential 10-second measurement phases

The CSV format is:

```text
time_ms,ph_raw,orp_raw,turbidity_raw,tds_raw
```

Example:

```text
0,8948,5318,9489,1629
100,8952,5326,9499,1611
200,8958,5312,9498,1624
...
```

---

## Two-stage measurement

A complete measurement is collected in two phases.

### Phase 1

The following probes are placed in the water:

* pH
* ORP
* turbidity

The white button starts the 10-second measurement.

### Phase 2

The TDS probe is then inserted and its 10-second measurement is started with the blue button.

The application combines the corresponding measurements into one four-channel CSV sample.

The four values in each stored row should therefore not be interpreted as physically simultaneous measurements: the TDS sequence is recorded in the second measurement phase after the pH, ORP, and turbidity sequence.

This two-stage method was chosen because the TDS measurement can electrically interfere with the other sensors when all probes are used together in the same small water sample.

---

# Operating modes

The application provides two main modes.

## Collect data

Used to create datasets for Edge Impulse.

The OLED guides the user through:

1. sample-group selection
2. first sensor measurement
3. TDS measurement
4. save or discard

Saved samples use filenames such as:

```text
Tap_water.001.csv
Sea_water.004.csv
Wide_ditch.003.csv
```

---

## Test

Test mode collects a temporary sample without adding it to the training dataset.

The sample is evaluated locally using the deployed Edge Impulse models, and the result is shown on the web dashboard.

---

# Current sample groups

The current development dataset uses five working labels:

```text
Tap_water
Sea_water
Narrow_ditch
Wide_ditch
Wide_ditch_diluted
```

These are identifiers for the specific sample groups used during development.

They are **not general scientific categories of water**.

For example, `Narrow_ditch` identifies samples collected from one particular source used in this project. It does not imply that the model has learned a general class representing water from narrow ditches.

The same applies to the other location-based working labels.

---

# Edge Impulse models

The project uses **two separate Edge Impulse projects** because the classification and anomaly-detection tasks answer different questions and require different training data.

Both models are exported as Linux `.eim` models and run locally on the UNO Q.

---

## 1. Multiclass classification project

The classification project contains measurements from the currently known sample groups.

Its purpose is to answer:

> Which of the known sample groups does this new measurement most closely resemble?

The input consists of four sensor channels:

* pH raw ADC
* ORP raw ADC
* turbidity raw ADC
* TDS raw ADC

at 10 Hz over a 10-second window.

The deployed model is:

```text
model.eim
```

The classifier returns one score for each learned sample group.

The application reports the class with the largest score when its confidence exceeds the selected acceptance threshold.

If no class exceeds the threshold, the result can instead be shown as uncertain.

The confidence threshold is an application-level decision criterion and is still being evaluated. Changing the threshold changes whether a result is accepted; it does not change or improve the trained neural network.

---

## 2. Tap-water anomaly project

Anomaly detection is implemented in a **separate Edge Impulse project**.

Its purpose is different:

> How closely does this new measurement resemble the tap-water measurements used as the normal reference?

### Training data

The anomaly model is trained using **tap-water samples only**.

These define the reference distribution that the anomaly detector learns as normal.

### Testing data

The test data contains both:

* tap-water samples
* samples from other water groups

This makes it possible to examine whether new tap-water measurements remain within the learned reference distribution while other sample types produce larger anomaly scores.

The deployed anomaly model is:

```text
anomaly.eim
```

The anomaly score is compared with a selected threshold.

A lower score indicates greater similarity to the learned tap-water reference, while a higher score indicates a larger deviation from that reference.

The numerical anomaly score has meaning only relative to this trained model and its selected threshold. It is not a universal water-quality scale.

---

# Why both models are useful

The two Edge Impulse projects provide complementary information.

The classifier answers:

> Which known sample group is closest?

The anomaly detector answers:

> How similar is this measurement to the learned tap-water reference?

For example, a new sample might still receive the highest classification score for `Tap_water` because it is closer to tap water than to any other known class.

The anomaly detector can independently indicate that the same sample lies outside the range represented by the tap-water reference data.

This is useful because a multiclass neural network will normally still produce class scores even for measurements that differ from everything it has seen during training.

---

# Local inference

Both exported models run directly on the Linux side of the UNO Q.

The simplified processing chain is:

```text
Water sensors
     │
     ▼
UNO Q MCU
     │
     │ raw sensor sequences
     ▼
Python application
     │
     ├── Multiclass classification → model.eim
     │
     ├── Tap-water anomaly test    → anomaly.eim
     │
     ├── Sensor-value conversion
     │
     └── Local LLM interpretation
     │
     ▼
Local web dashboard
```

No cloud connection is required for normal inference once the models have been deployed to the UNO Q.

---

# Local dashboard

The UNO Q hosts a local web dashboard showing the most recent measurement.

The dashboard currently includes:

* closest known sample group
* classifier confidence
* tap-water anomaly result
* anomaly score
* anomaly threshold
* approximate physical sensor values
* raw sensor averages
* timestamp
* local AI interpretation

The dashboard can be opened from another device on the same local network.

QR-code access is also supported.

---

# Local AI interpretation

A local large language model is used as an explanation layer.

The LLM does **not** perform the sensor classification or anomaly detection.

Instead, it receives structured results already produced by the measurement and inference pipeline and generates a short human-readable interpretation.

Its instructions constrain it to:

* use only supplied facts
* treat classification labels as opaque names
* avoid inventing contaminants or causes
* avoid inventing thresholds
* avoid unsupported interpretation of sensor values
* avoid safety conclusions
* avoid unnecessary repetition

The deterministic sensor and Edge Impulse results remain the primary outputs.

---

# Sensor-value conversion

The machine-learning models use the raw sensor sequences.

The dashboard additionally converts some measurements into approximate physical units for easier interpretation.

These converted values currently have different levels of calibration confidence.

## pH

The pH conversion is approximate until the sensor has been calibrated against appropriate reference solutions.

## TDS

TDS is estimated from the measured sensor voltage.

The result depends on sensor calibration and temperature.

Measured water-temperature compensation is not currently available.

## ORP

ORP is displayed as an approximate millivolt value.

The current value is shown without a stored calibration offset, so it should be treated as indicative rather than as a calibrated reference measurement.

## Turbidity

The current conversion is an uncalibrated estimate based on an approximate sensor response relationship.

The displayed NTU value should therefore be treated as indicative rather than as a calibrated laboratory measurement.

---

# Raw data and physical units

An important distinction in the project is that the Edge Impulse models can learn useful patterns directly from sensor ADC values even when the conversion into physical units is still imperfect.

For example:

```text
raw ADC pattern → suitable for machine-learning comparison
```

does not automatically imply:

```text
converted pH / ppm / mV / NTU → laboratory-grade measurement
```

The raw measurements are therefore retained and displayed alongside the converted values.

---

# Experimental status

The prototype can currently compare measurements with several controlled sample groups and can independently compare them with the learned tap-water reference.

However, the present results should be interpreted in the context of the dataset used to create them.

---

## Dataset size and independence

The dataset remains relatively small.

Many samples consist of repeated measurements from the same physical water source or container.

Such measurements provide useful information about short-term sensor variation, but they are not statistically equivalent to measurements from independent water sources.

A model can therefore obtain very good results on a held-out Edge Impulse test set while still requiring broader real-world validation.

---

## Training, test, and independent validation

The Edge Impulse training and test sets are useful for model development, but truly independent validation should also include newly collected samples that were not involved in model development.

Useful future validation data includes measurements collected:

* on different days
* from fresh physical samples
* from different containers
* at different temperatures
* from additional locations

This is particularly important when assessing whether the classifier generalises beyond the exact samples represented in the dataset.

---

## Temperature

Sensor responses can vary with water temperature.

Tap-water measurements collected under different temperature conditions have already shown that temperature and sampling conditions can influence the measured sensor pattern.

Future datasets should therefore include controlled temperature variation.

Adding a water-temperature sensor would also allow explicit temperature compensation where appropriate.

---

## Classification confidence

High classifier confidence means that the trained neural network strongly prefers one known class over the alternatives represented in its training data.

It does not prove the physical origin of the sample.

Likewise, a lower confidence result can reflect overlap between learned sample patterns rather than a malfunction of the model.

The confidence threshold should ultimately be chosen using independent validation data rather than adjusted to make individual measurements pass.

---

## Anomaly threshold

The tap-water anomaly threshold is likewise specific to the anomaly model and dataset.

It should be selected using held-out and independent tap-water measurements together with non-tap-water samples.

The objective is to understand the trade-off between:

* accepting genuine tap-water variation
* rejecting measurements that differ from the learned tap-water reference

---

# What the system does not measure

The four sensors measure broad electrical and optical properties of the water.

They do not directly identify specific contaminants such as:

* bacteria
* heavy metals
* pesticides
* PFAS
* hydrocarbons

The classifier recognises patterns present in its training data; it does not perform chemical identification.

---

# Main software components

The Python application is located under:

```text
/app/python/
```

Important components include:

```text
main.py
infer_water.py
interpret_water.py
model.eim
anomaly.eim
labels.txt
samples/
```

## `main.py`

Main application logic, including:

* user interface
* sampling workflow
* local data storage
* dashboard
* result presentation
* integration of inference and interpretation

## `infer_water.py`

Handles local Edge Impulse inference and processing of the deployed models.

## `interpret_water.py`

Generates the constrained local-LLM interpretation from the measured facts and inference results.

## `model.eim`

Multiclass water-sample classification model.

## `anomaly.eim`

Tap-water anomaly-detection model.

## `labels.txt`

Contains the working sample labels used by the data-collection interface.

## `samples/`

Contains locally collected CSV samples.

---

# Current project status

Currently operational:

* four-sensor acquisition
* two-stage sampling procedure
* sample labelling
* local CSV storage
* multiclass Edge Impulse classification
* separate tap-water anomaly detection
* local `.eim` inference on UNO Q
* confidence-based uncertain classification
* local dashboard
* approximate sensor-value conversion
* raw-value display
* local LLM interpretation
* local timestamp handling
* mobile browser access
* QR-code dashboard access

---

# Possible next steps

* collect more independent samples
* collect samples over multiple days
* increase temperature variation in the dataset
* perform proper pH calibration
* improve TDS calibration
* improve turbidity calibration
* verify ORP calibration
* add measured water temperature
* evaluate classifier confidence thresholds using independent data
* further validate the anomaly threshold
* investigate model behaviour for previously unseen water samples
* reduce redundancy in the local LLM interpretation
* move configurable thresholds and calibration constants into a configuration file
* add field-network or hotspot mode
* produce final photographs, diagrams, video, and project write-up

---

# Scope and limitations

This project is an experimental Edge AI and sensor-fusion prototype.

Its purpose is to investigate whether patterns from several inexpensive water sensors can be learned and compared locally using embedded machine learning.

The system is not a certified analytical instrument. Its classifications, anomaly scores, and approximate sensor conversions must not be used to establish whether water is safe to drink.

Proper assessment of drinking-water safety requires validated measurements and appropriate laboratory analysis.

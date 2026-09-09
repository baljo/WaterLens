# WaterLens

**Edge AI water analysis with Arduino UNO Q**

WaterLens is an experimental water-analysis prototype built with the **Arduino UNO Q**, four Grove water sensors, **Edge Impulse** machine learning, a local web dashboard, and a local AI interpretation layer.

The system records a multichannel sensor pattern from a water sample and evaluates it in two complementary ways:

1. a multiclass model compares the measurement with previously learned sample groups;
2. a separate anomaly model compares summary features from the measurement with a learned tap-water reference.

All normal inference runs locally on the UNO Q after the models have been deployed. WaterLens also provides an explicit **Offline Connection** mode that creates a direct local Wi-Fi connection for accessing the dashboard without relying on an existing Wi-Fi network or Internet connection.

> **Important:** WaterLens is an experimental Edge AI and sensor-fusion prototype. It is not a certified analytical instrument and must not be used to determine whether water is safe to drink.

---

# Hardware

## Main controller

- Arduino UNO Q, 4 GB
- Grove Base Shield V2.0

## Sensors and user interface

| Device | Purpose | Connection |
| --- | --- | --- |
| Grove pH Sensor Kit | pH-related voltage measurement | A0 |
| Grove TDS Sensor | Total dissolved solids proxy | A1 |
| Grove Turbidity Sensor | Optical turbidity measurement | A2 |
| Grove ORP Pro | Oxidation-reduction potential | A3 |
| Grove Dual Button | Local user interface | Grove digital port D2; the two button signals use D2 and D3 |
| Grove 0.96" OLED | Instructions, status, results and QR codes | I²C |

Measurements are performed using laboratory beakers containing the same water sample. A custom 3D-printed holder keeps the turbidity sensor at a repeatable position in the sample.

---

# Sampling procedure

Each complete sample contains:

- 100 time steps
- 100 ms sampling interval
- 10 Hz sampling frequency
- approximately 10 seconds of data per phase
- four sensor channels

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

## Two-stage measurement

A complete WaterLens measurement is collected in two phases.

### Phase 1

The following probes are placed in the first beaker:

- pH
- ORP
- turbidity

The **white button** starts the 10-second measurement.

### Phase 2

The TDS probe is placed in a second beaker containing the same water sample. The **blue button** starts the second 10-second measurement.

The application then combines the two sequences row by row into one four-channel CSV sample.

The four values in a stored row are therefore **not physically simultaneous measurements**. The TDS value was recorded during the second phase, immediately after the pH, ORP and turbidity sequence.

This two-stage procedure provides a repeatable workflow while reducing unwanted interaction between probes.

---

# Operating modes

The OLED menu currently provides three explicit operating modes.

## 1. Collect Data

Used to create labelled datasets for Edge Impulse.

The OLED guides the user through:

1. sample-group selection
2. pH + ORP + turbidity measurement
3. TDS measurement
4. save or discard

Saved samples use filenames such as:

```text
Tap_water.001.csv
Sea_water.004.csv
Wide_ditch.003.csv
```

After saving a sample, additional measurements can be collected without restarting the application. The user can also change the active label or return to the main menu.

## 2. Test Water

Test Water collects a temporary sample without adding it to the training dataset.

The sample is evaluated locally using both deployed Edge Impulse models. The OLED shows the closest learned sample group and classifier confidence. The full result is available on the local web dashboard, including the tap-water reference result and anomaly score.

Pressing the blue button after a test displays a dashboard QR code. The QR destination automatically follows the current network mode:

- normal local network: `http://uno-q.local:8000`
- WaterLens offline Wi-Fi: `http://10.42.0.1:8000`

## 3. Offline Connection

Offline Connection is deliberately selected by the user from the OLED menu. WaterLens does **not** automatically switch into hotspot mode merely because normal Wi-Fi is temporarily unavailable or slow.

When Offline Connection is started, the UNO Q activates the WaterLens Wi-Fi network. A phone or other device can join that network and open the dashboard directly.

Current prototype settings:

```text
SSID: WaterLens
Password: waterlens123
Dashboard: http://10.42.0.1:8000
```

When Offline Connection is stopped, the network-control helper first attempts to restore the exact Wi-Fi profile that was active before hotspot mode was started. A saved-profile fallback is also available if the helper has restarted while hotspot mode was active.

### One-time NetworkManager setup

The current implementation expects a **NetworkManager connection profile named `Hotspot`** on Wi-Fi interface `wlan0`. The application activates and deactivates that existing profile; it deliberately does not create or change hotspot configuration automatically at runtime.

Before creating anything, check whether a profile with that name already exists:

```bash
nmcli connection show Hotspot
```

If `Hotspot` already exists, do **not** create another profile with the same name. Verify or modify the existing profile instead.

On a fresh UNO Q where the profile does not yet exist, create it without activating it:

```bash
nmcli connection add type wifi ifname wlan0 con-name Hotspot autoconnect no ssid WaterLens
nmcli connection modify Hotspot 802-11-wireless.mode ap
nmcli connection modify Hotspot ipv4.method shared ipv4.addresses 10.42.0.1/24
nmcli connection modify Hotspot 802-11-wireless-security.key-mgmt wpa-psk
nmcli connection modify Hotspot 802-11-wireless-security.psk waterlens123
```

If the shell account does not have permission to modify NetworkManager connections, run the same commands with the privileges required by that UNO Q installation, for example by prefixing them with `sudo` where appropriate.

The profile can be inspected with:

```bash
nmcli -f connection.id,connection.autoconnect,802-11-wireless.ssid,802-11-wireless.mode,ipv4.method,ipv4.addresses connection show Hotspot
```

The relevant values should be:

```text
connection.id:          Hotspot
connection.autoconnect: no
802-11-wireless.ssid:   WaterLens
802-11-wireless.mode:   ap
ipv4.method:            shared
ipv4.addresses:         10.42.0.1/24
```

There is normally no need to run `nmcli connection up Hotspot` manually. Selecting **Offline Connection** from the WaterLens OLED menu performs that activation, while stopping Offline Connection returns to the Wi-Fi profile that was active beforehand.

Keeping `connection.autoconnect` disabled is intentional: the UNO Q should not enter hotspot mode automatically during boot or merely because normal Wi-Fi association is slow.

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

These are identifiers for the specific sample groups used during development. They are **not general scientific categories of water**.

For example, `Narrow_ditch` identifies measurements collected from one particular source used in this project. It does not mean that the model has learned a general class representing water from narrow ditches.

The dashboard converts these working identifiers into more readable names where appropriate.

---

# Edge Impulse models

WaterLens uses **two separate Edge Impulse projects** because classification and anomaly detection answer different questions and require different training data.

Both models are exported as Linux `.eim` models and run locally on the UNO Q.

## 1. Multiclass classification project

The classification project contains measurements from the currently known sample groups.

Its purpose is to answer:

> Which of the known sample groups does this new measurement most closely resemble?

The classifier uses the complete four-channel raw sequence:

- pH raw ADC
- ORP raw ADC
- turbidity raw ADC
- TDS raw ADC

at 10 Hz over the 100-row sample window.

The deployed model is:

```text
model.eim
```

The model returns one score for each learned sample group. The application currently reports the class with the largest score and displays its confidence.

Classifier confidence expresses preference among the learned classes. It does not prove the physical origin of a new sample, and a high score does not mean that the measurement necessarily belongs to a familiar real-world category outside the development dataset.

## 2. Tap-water anomaly project

Anomaly detection is implemented in a **separate Edge Impulse project**.

Its purpose is different:

> How closely does this measurement resemble the tap-water measurements used as the reference?

The anomaly project is trained using **tap-water samples only**. In the current impulse, summary features derived from the four sensor channels are used by the anomaly model rather than the complete 400-value time sequence.

The deployed model is:

```text
anomaly.eim
```

The anomaly score is compared with the application threshold. In the current prototype this threshold is:

```text
20.0
```

A lower score indicates greater similarity to the learned tap-water reference, while a higher score indicates a larger deviation from it.

The numerical anomaly score and threshold are specific to this trained model and dataset. They are not universal water-quality scales.

---

# Why both models are useful

The two Edge Impulse projects provide complementary information.

The classifier answers:

> Which known sample group is closest?

The anomaly detector answers:

> How similar is this measurement to the learned tap-water reference?

A new sample can, for example, receive the highest classification score for `Tap_water` because it is closer to that class than to the other learned classes, while the separate anomaly model can still indicate that the same measurement falls outside the variation represented by the tap-water reference data.

The two results should not be treated as independent confirmation because they are derived from the same underlying sensor measurement.

---

# Local inference architecture

Both exported Edge Impulse models run directly on the Linux side of the UNO Q.

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
     └── Local interpretation layer
     │
     ▼
Local web dashboard
```

No cloud connection is required for normal inference once the models have been deployed to the UNO Q.

---

# Local dashboard

The UNO Q hosts a local web dashboard branded **WaterLens with UNO Q**.

The dashboard shows the most recent test and currently includes:

- closest known sample group
- classifier confidence
- tap-water reference result
- anomaly score
- anomaly threshold
- approximate physical sensor values
- raw sensor averages
- local timestamp
- local AI interpretation

The dashboard can be opened from another device either through the normal local network or through the explicit WaterLens Offline Connection mode.

---

# Local AI interpretation

A local language model is used only as an explanation layer. It does **not** perform the sensor classification or anomaly detection.

The deterministic application first establishes the classifier and tap-water-reference results. The language model then receives constrained facts and is asked to produce a short human-readable synthesis.

The interpretation rules are designed to prevent the model from:

- inventing contaminants or causes
- claiming the sample's actual origin
- interpreting approximate sensor values as automatically good or bad
- treating agreement between the two Edge AI outputs as independent verification
- making drinking-water safety conclusions

Generated text is validated before display. If the generated interpretation does not satisfy the application's constraints, a deterministic fallback explanation can be used instead.

The deterministic measurement and Edge Impulse outputs remain the primary results.

---

# Sensor-value conversion

The machine-learning models use the raw sensor data. The dashboard additionally converts some values into approximate physical units for easier interpretation.

These conversions currently have different levels of calibration confidence.

## pH

The displayed pH is approximate until the sensor is calibrated against appropriate reference solutions.

## TDS

TDS is estimated from sensor voltage. The result depends on calibration and temperature. Water temperature is not currently measured, so the dashboard calculation uses an assumed temperature.

## ORP

ORP is displayed as an approximate millivolt value. Its accuracy depends on calibration and any required sensor offset.

## Turbidity

The current conversion uses an approximate sensor response relationship. The displayed NTU value is therefore an indicative, uncalibrated estimate rather than a laboratory-grade turbidity measurement.

---

# Raw data and physical units

An important distinction in WaterLens is that a machine-learning model can learn useful patterns from repeatable raw ADC measurements even when the conversion into physical units is still imperfect.

```text
raw ADC pattern → useful for machine-learning comparison
```

does not automatically imply:

```text
converted pH / ppm / mV / NTU → laboratory-grade measurement
```

The raw sensor values are therefore retained and can also be inspected on the dashboard.

---

# Experimental status and validation

WaterLens can currently distinguish the controlled sample groups represented in the development dataset and can independently compare measurements with the learned tap-water reference.

These results must still be interpreted in the context of the dataset used to create the models.

## Dataset size and independence

The dataset remains relatively small. Many samples consist of repeated measurements from the same physical water source or container.

Such measurements provide useful information about short-term sensor variation but are not statistically equivalent to measurements from many independent water sources.

A model can therefore obtain very good results on a held-out Edge Impulse test set while still requiring broader real-world validation.

## Training, test and independent validation

The Edge Impulse training and test sets are useful during model development, but stronger validation should include newly collected measurements that were not involved in model development.

Useful future validation data includes measurements collected:

- on different days
- from fresh physical samples
- from different containers
- at different temperatures
- from additional locations

## Temperature

Tap-water measurements collected at different temperatures have already shown that temperature and sampling conditions can change the observed sensor pattern.

Future datasets should therefore include controlled temperature variation. Adding a water-temperature sensor would also allow explicit temperature compensation where appropriate.

## Classification confidence

Classifier confidence means that the trained neural network prefers one learned class over the alternatives represented in its training data. It does not prove the physical origin of the sample.

During development, borderline confidence results were treated as a reason to collect more representative data rather than simply lowering an acceptance threshold to obtain the expected label.

## Anomaly threshold

The current tap-water anomaly threshold is specific to the present anomaly model and dataset. It should be further evaluated using held-out and genuinely independent tap-water measurements together with non-reference samples.

The relevant trade-off is between:

- accepting genuine tap-water variation
- identifying measurements that differ from the learned tap-water reference

---

# What WaterLens does not measure

The four sensors measure broad electrical and optical properties of the water.

They do not directly identify specific contaminants such as:

- bacteria
- heavy metals
- pesticides
- PFAS
- hydrocarbons

The classifier recognises patterns represented in its training data; it does not perform chemical identification.

---

# Main software components

The main Python application is located under:

```text
/app/python/
```

Important components include:

```text
python/main.py
python/infer_water.py
python/interpret_water.py
python/dashboard_branding.py
python/waterlens_brand.py
python/model.eim
python/anomaly.eim
python/labels.txt
python/samples/
sketch/sketch.ino
bricks/networkcontrol/
```

## `python/main.py`

Main Linux-side application logic, including sample storage, dashboard generation, timestamp handling and integration of inference and interpretation.

## `python/infer_water.py`

Runs the deployed Edge Impulse classifier and anomaly `.eim` models and returns deterministic inference results.

## `python/interpret_water.py`

Provides the constrained local interpretation layer and exposes the explicit network-control functions to the MCU through the Arduino Bridge.

## `python/dashboard_branding.py` and `python/waterlens_brand.py`

Apply the WaterLens branding and embedded logo to the local dashboard without requiring external web assets.

## `sketch/sketch.ino`

Runs the MCU-side OLED and button interface, sampling workflows, test workflow, QR-code display and the explicit Offline Connection menu.

## `bricks/networkcontrol/`

Custom App Lab brick that communicates with NetworkManager through the host D-Bus socket. It activates the existing WaterLens hotspot profile and restores normal Wi-Fi when Offline Connection is stopped.

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

- four-sensor acquisition
- two-stage sampling procedure
- sample labelling
- local CSV storage
- multiclass Edge Impulse classification
- separate tap-water anomaly detection
- local `.eim` inference on UNO Q
- local dashboard
- approximate sensor-value conversion
- raw-value display
- constrained local AI interpretation with deterministic fallback
- local timestamp handling
- mobile browser access
- QR-code dashboard access
- explicit menu-controlled Offline Connection mode
- direct WaterLens Wi-Fi dashboard access
- restoration of the previous normal Wi-Fi profile after offline mode
- WaterLens dashboard branding

---

# Possible next steps

- collect more independent samples
- collect samples over multiple days
- increase controlled temperature variation in the dataset
- perform proper pH calibration
- improve TDS calibration
- improve turbidity calibration
- verify ORP calibration
- add measured water temperature
- further validate the anomaly threshold
- investigate model behaviour for previously unseen water samples
- move configurable thresholds and calibration constants into a configuration file
- make hotspot-profile provisioning and credentials configurable rather than preconfigured
- add timestamped measurement history with optional location metadata
- complete broader real-world validation

---

# Scope and limitations

WaterLens is an experimental Edge AI and sensor-fusion prototype intended to explore whether patterns from several inexpensive water sensors can be learned, compared and interpreted locally using embedded machine learning.

The system is **not a certified analytical instrument**. Its classifications, anomaly scores and approximate sensor conversions must not be used to establish whether water is safe to drink.

Proper assessment of drinking-water safety requires validated measurements and appropriate laboratory analysis.

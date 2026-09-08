// Adds once-per-second live A0-A3 ADC diagnostics plus repeated pH/ORP reads without changing the normal water sampling workflow. Thomas Vikström, 2026-09-05 16:29 Europe/Helsinki.

#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <Arduino_RouterBridge.h>


// ------------------------------------------------------------
// Grove Base Shield connections
// ------------------------------------------------------------

constexpr uint8_t PH_PIN = A0;
constexpr uint8_t TDS_PIN = A1;
constexpr uint8_t TURBIDITY_PIN = A2;

// Grove ORP Sensor Kit Pro:
// A3 = SIG
// A4 = CAL
constexpr uint8_t ORP_PIN = A3;
constexpr uint8_t ORP_CAL_PIN = A4;


// ------------------------------------------------------------
// Buttons
// ------------------------------------------------------------

constexpr uint8_t WHITE_BUTTON_PIN = 2;
constexpr uint8_t BLUE_BUTTON_PIN = 3;
constexpr uint16_t LONG_PRESS_MS = 1000;


// ------------------------------------------------------------
// ADC / sampling
// ------------------------------------------------------------

constexpr uint8_t ADC_BITS = 14;
constexpr uint16_t SAMPLE_INTERVAL_MS = 100;
constexpr uint16_t SAMPLE_ROWS = 100;
constexpr uint8_t ADC_AVERAGES = 4;


// ------------------------------------------------------------
// Labels
// ------------------------------------------------------------

constexpr uint8_t MAX_LABELS = 12;
constexpr uint8_t MAX_LABEL_LENGTH = 22;

char labels[MAX_LABELS][MAX_LABEL_LENGTH + 1];
uint8_t labelCount = 0;
uint8_t selectedLabel = 0;


// ------------------------------------------------------------
// OLED
// ------------------------------------------------------------

// Keep the same constructor that already works with the SSD1315 module.
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(
    U8G2_R0,
    U8X8_PIN_NONE
);


// ------------------------------------------------------------
// QR code
// ------------------------------------------------------------

// QR payload: http://192.168.178.34:8000
const uint32_t QR_ROWS[25] = {
    0x1FD3C7F,
    0x104CE41,
    0x1745B5D,
    0x1741F5D,
    0x174AC5D,
    0x1045441,
    0x1FD557F,
    0x0016A00,
    0x1B4E841,
    0x18896FE,
    0x0556409,
    0x100F12F,
    0x10FBEB1,
    0x1A90DB2,
    0x1FE3B7F,
    0x15BA6D5,
    0x11587FE,
    0x001F916,
    0x1FC235D,
    0x1049715,
    0x1751DF3,
    0x175442D,
    0x1745567,
    0x10544FB,
    0x1FD8D59
};

// ------------------------------------------------------------
// Sample storage
// ------------------------------------------------------------

struct SampleRow
{
    uint32_t timeMs;
    uint16_t phRaw;
    uint16_t orpRaw;
    uint16_t turbidityRaw;
    uint16_t tdsRaw;
};

SampleRow rows[SAMPLE_ROWS];


// ------------------------------------------------------------
// Workflow choices
// ------------------------------------------------------------

enum class MainMode : uint8_t
{
    Collect,
    Test
};

enum class BlueAction : uint8_t
{
    None,
    ShortPress,
    LongPress
};

enum class CollectAction : uint8_t
{
    NewSample,
    ChangeLabel,
    MainMenu
};

MainMode selectedMode = MainMode::Collect;


// ------------------------------------------------------------
// Button helpers
// ------------------------------------------------------------

bool pressed(uint8_t pin)
{
    if (digitalRead(pin) != LOW)
    {
        return false;
    }

    delay(25);

    if (digitalRead(pin) != LOW)
    {
        return false;
    }

    while (digitalRead(pin) == LOW)
    {
        delay(5);
    }

    delay(25);
    return true;
}


BlueAction readBlueAction()
{
    if (digitalRead(BLUE_BUTTON_PIN) != LOW)
    {
        return BlueAction::None;
    }

    delay(25);

    if (digitalRead(BLUE_BUTTON_PIN) != LOW)
    {
        return BlueAction::None;
    }

    uint32_t pressStarted = millis();

    while (digitalRead(BLUE_BUTTON_PIN) == LOW)
    {
        delay(5);
    }

    uint32_t pressDuration = millis() - pressStarted;
    delay(25);

    if (pressDuration >= LONG_PRESS_MS)
    {
        return BlueAction::LongPress;
    }

    return BlueAction::ShortPress;
}


// ------------------------------------------------------------
// ADC helper
// ------------------------------------------------------------

uint16_t readAveraged(uint8_t pin)
{
    uint32_t sum = 0;

    analogRead(pin);
    delayMicroseconds(300);

    for (uint8_t i = 0; i < ADC_AVERAGES; ++i)
    {
        sum += analogRead(pin);
        delay(2);
    }

    return uint16_t(sum / ADC_AVERAGES);
}


// ------------------------------------------------------------
// Temporary live ADC diagnostic
// ------------------------------------------------------------

void printLiveAnalogDebug()
{
    static uint32_t lastPrintMs = 0;
    uint32_t now = millis();

    if (now - lastPrintMs < 1000)
    {
        return;
    }

    lastPrintMs = now;

    uint16_t a0 = readAveraged(PH_PIN);
    uint16_t a1 = readAveraged(TDS_PIN);
    uint16_t a2 = readAveraged(TURBIDITY_PIN);
    uint16_t a3 = readAveraged(ORP_PIN);

    uint16_t ph2 = readAveraged(PH_PIN);
    uint16_t orp2 = readAveraged(ORP_PIN);

    Monitor.print("LIVE ADC  A0=");
    Monitor.print(a0);
    Monitor.print("  A1=");
    Monitor.print(a1);
    Monitor.print("  A2=");
    Monitor.print(a2);
    Monitor.print("  A3=");
    Monitor.print(a3);
    Monitor.print("  pH2=");
    Monitor.print(ph2);
    Monitor.print("  ORP2=");
    Monitor.println(orp2);
}


// ------------------------------------------------------------
// OLED helpers
// ------------------------------------------------------------

void centerText(const char* text, uint8_t y)
{
    int16_t x = (128 - oled.getStrWidth(text)) / 2;

    if (x < 0)
    {
        x = 0;
    }

    oled.drawStr(x, y, text);
}


void drawMessage(
    const char* top,
    const char* middle,
    const char* bottom = ""
)
{
    oled.clearBuffer();

    oled.setFont(u8g2_font_6x10_tf);
    centerText(top, 11);

    oled.setFont(u8g2_font_helvB12_tf);
    centerText(middle, 38);

    oled.setFont(u8g2_font_6x10_tf);
    centerText(bottom, 62);

    oled.sendBuffer();
}


void drawCountdown(const char* title, int secondsLeft)
{
    oled.clearBuffer();

    oled.setFont(u8g2_font_6x10_tf);
    centerText(title, 11);

    char numberText[8];
    snprintf(numberText, sizeof(numberText), "%d", secondsLeft);

    oled.setFont(u8g2_font_logisoso24_tf);
    centerText(numberText, 45);

    oled.sendBuffer();
}


void drawModeSelection()
{
    oled.clearBuffer();

    oled.setFont(u8g2_font_6x10_tf);
    centerText("SELECT MODE", 11);

    oled.setFont(u8g2_font_helvB12_tf);

    if (selectedMode == MainMode::Collect)
    {
        centerText("COLLECT DATA", 39);
    }
    else
    {
        centerText("TEST WATER", 39);
    }

    oled.setFont(u8g2_font_6x10_tf);
    centerText("BLUE NEXT  WHITE OK", 62);

    oled.sendBuffer();
}


void drawSelection()
{
    oled.clearBuffer();

    oled.setFont(u8g2_font_6x10_tf);
    centerText("BLUE NEXT  HOLD MENU", 9);

    char numberText[8];
    snprintf(
        numberText,
        sizeof(numberText),
        "%u",
        selectedLabel + 1
    );

    oled.setFont(u8g2_font_logisoso24_tf);
    centerText(numberText, 39);

    oled.setFont(u8g2_font_6x10_tf);

    if (labelCount > 0)
    {
        centerText(labels[selectedLabel], 52);
    }

    centerText("WHITE = OK", 63);
    oled.sendBuffer();
}


void drawCollectReady()
{
    oled.clearBuffer();

    oled.setFont(u8g2_font_6x10_tf);
    centerText("COLLECT DATA", 10);
    centerText(labels[selectedLabel], 28);
    centerText("WHITE = NEW", 46);
    centerText("BLUE=LABEL HOLD=MENU", 62);

    oled.sendBuffer();
}


void drawTestResult(
    const String& prediction,
    int confidencePercent
)
{
    oled.clearBuffer();

    oled.setFont(u8g2_font_6x10_tf);
    centerText("TEST RESULT", 10);

    oled.setFont(u8g2_font_helvB12_tf);

    if (oled.getStrWidth(prediction.c_str()) > 124)
    {
        oled.setFont(u8g2_font_helvB10_tf);
    }

    if (oled.getStrWidth(prediction.c_str()) > 124)
    {
        oled.setFont(u8g2_font_6x10_tf);
    }

    centerText(prediction.c_str(), 31);

    char confidenceText[10];
    snprintf(
        confidenceText,
        sizeof(confidenceText),
        "%d%%",
        confidencePercent
    );

    oled.setFont(u8g2_font_helvB12_tf);
    centerText(confidenceText, 49);

    oled.setFont(u8g2_font_5x8_tf);
    centerText("BLUE REPORT  WHITE MENU", 63);

    oled.sendBuffer();
}


void drawReportQr()
{
    constexpr int modules = 25;
    constexpr int scale = 2;
    constexpr int border = 2;

    constexpr int totalModules = modules + border * 2;
    constexpr int qrSize = totalModules * scale;  // 58 x 58 pixels

    const int x0 = (128 - qrSize) / 2;
    const int y0 = (64 - qrSize) / 2;

    oled.clearBuffer();

    // Lit OLED pixels form the QR background.
    oled.setDrawColor(1);
    oled.drawBox(x0, y0, qrSize, qrSize);

    // Clear pixels for the black QR modules.
    oled.setDrawColor(0);

    for (int row = 0; row < modules; ++row)
    {
        for (int col = 0; col < modules; ++col)
        {
            bool black =
                QR_ROWS[row] & (1UL << (modules - 1 - col));

            if (black)
            {
                int x = x0 + (col + border) * scale;
                int y = y0 + (row + border) * scale;

                oled.drawBox(x, y, scale, scale);
            }
        }
    }

    oled.setDrawColor(1);
    oled.sendBuffer();
}


// ------------------------------------------------------------
// Emergency/default labels
// ------------------------------------------------------------

void loadDefaultLabels()
{
    strcpy(labels[0], "Tap_water");
    strcpy(labels[1], "Sea_water");
    strcpy(labels[2], "Narrow_ditch");
    strcpy(labels[3], "Wide_ditch");
    strcpy(labels[4], "Wide_ditch_diluted");

    labelCount = 5;
}


// ------------------------------------------------------------
// Linux/Bridge startup and labels
// ------------------------------------------------------------

bool waitForLinux()
{
    drawMessage(
        "WATER SAMPLE",
        "STARTING",
        "Linux Bridge"
    );

    uint32_t deadline = millis() + 10000;

    while (millis() < deadline)
    {
        bool started = false;

        Bridge.call("linux_started").result(started);

        if (started)
        {
            return true;
        }

        delay(250);
    }

    return false;
}


bool loadLabelsFromLinux()
{
    int count = 0;
    Bridge.call("get_label_count").result(count);

    if (count <= 0 || count > MAX_LABELS)
    {
        return false;
    }

    for (int i = 0; i < count; ++i)
    {
        String label;
        Bridge.call("get_label", i).result(label);

        if (label.length() == 0)
        {
            return false;
        }

        label.toCharArray(
            labels[i],
            MAX_LABEL_LENGTH + 1
        );
    }

    labelCount = uint8_t(count);
    selectedLabel = 0;
    return true;
}


// ------------------------------------------------------------
// Menu and water selection
// ------------------------------------------------------------

MainMode selectMainMode()
{
    drawModeSelection();

    while (true)
    {
        printLiveAnalogDebug();

        BlueAction blueAction = readBlueAction();

        if (blueAction != BlueAction::None)
        {
            if (selectedMode == MainMode::Collect)
            {
                selectedMode = MainMode::Test;
            }
            else
            {
                selectedMode = MainMode::Collect;
            }

            drawModeSelection();
        }

        if (pressed(WHITE_BUTTON_PIN))
        {
            return selectedMode;
        }

        delay(10);
    }
}


bool selectWater()
{
    drawSelection();

    while (true)
    {
        BlueAction blueAction = readBlueAction();

        if (blueAction == BlueAction::LongPress)
        {
            return false;
        }

        if (blueAction == BlueAction::ShortPress)
        {
            selectedLabel++;

            if (selectedLabel >= labelCount)
            {
                selectedLabel = 0;
            }

            drawSelection();
        }

        if (pressed(WHITE_BUTTON_PIN))
        {
            Monitor.print("Selected: ");
            Monitor.print(selectedLabel + 1);
            Monitor.print(" = ");
            Monitor.println(labels[selectedLabel]);
            return true;
        }

        delay(10);
    }
}


CollectAction waitForCollectAction()
{
    drawCollectReady();

    while (true)
    {
        if (pressed(WHITE_BUTTON_PIN))
        {
            return CollectAction::NewSample;
        }

        BlueAction blueAction = readBlueAction();

        if (blueAction == BlueAction::LongPress)
        {
            return CollectAction::MainMenu;
        }

        if (blueAction == BlueAction::ShortPress)
        {
            return CollectAction::ChangeLabel;
        }

        delay(10);
    }
}


// ------------------------------------------------------------
// Countdown and acquisition phases
// ------------------------------------------------------------

void countdown(const char* title, int fromSeconds)
{
    for (int seconds = fromSeconds; seconds >= 0; --seconds)
    {
        drawCountdown(title, seconds);

        if (seconds > 0)
        {
            delay(1000);
        }
    }
}


void acquirePhase1()
{
    drawMessage(
        "PHASE 1",
        "INSERT 3",
        "WHITE = START"
    );

    while (!pressed(WHITE_BUTTON_PIN))
    {
        delay(10);
    }

    countdown("SETTLING", 3);
    uint32_t start = millis();

    for (uint16_t i = 0; i < SAMPLE_ROWS; ++i)
    {
        uint32_t target =
            start + uint32_t(i) * SAMPLE_INTERVAL_MS;

        while ((int32_t)(millis() - target) < 0)
        {
            delay(1);
        }

        rows[i].timeMs =
            uint32_t(i) * SAMPLE_INTERVAL_MS;
        rows[i].phRaw = readAveraged(PH_PIN);
        rows[i].orpRaw = readAveraged(ORP_PIN);
        rows[i].turbidityRaw = readAveraged(TURBIDITY_PIN);
        rows[i].tdsRaw = 0;

        int secondsLeft =
            10 - int((i * SAMPLE_INTERVAL_MS) / 1000);

        if (secondsLeft < 0)
        {
            secondsLeft = 0;
        }

        drawCountdown("PHASE 1", secondsLeft);
    }
}


void acquirePhase2()
{
    drawMessage(
        "PHASE 2",
        "INSERT TDS",
        "BLUE = START"
    );

    while (!pressed(BLUE_BUTTON_PIN))
    {
        delay(10);
    }

    countdown("SETTLING", 3);
    uint32_t start = millis();

    for (uint16_t i = 0; i < SAMPLE_ROWS; ++i)
    {
        uint32_t target =
            start + uint32_t(i) * SAMPLE_INTERVAL_MS;

        while ((int32_t)(millis() - target) < 0)
        {
            delay(1);
        }

        rows[i].tdsRaw = readAveraged(TDS_PIN);

        int secondsLeft =
            10 - int((i * SAMPLE_INTERVAL_MS) / 1000);

        if (secondsLeft < 0)
        {
            secondsLeft = 0;
        }

        drawCountdown("PHASE 2", secondsLeft);
    }
}


// ------------------------------------------------------------
// Serial Monitor debug output
// ------------------------------------------------------------

void printSampleToMonitor()
{
    Monitor.println("BEGIN_SAMPLE");
    Monitor.println(
        "time_ms,ph_raw,orp_raw,turbidity_raw,tds_raw"
    );

    for (uint16_t i = 0; i < SAMPLE_ROWS; ++i)
    {
        Monitor.print(rows[i].timeMs);
        Monitor.print(',');
        Monitor.print(rows[i].phRaw);
        Monitor.print(',');
        Monitor.print(rows[i].orpRaw);
        Monitor.print(',');
        Monitor.print(rows[i].turbidityRaw);
        Monitor.print(',');
        Monitor.println(rows[i].tdsRaw);
    }

    Monitor.println("END_SAMPLE");
}


// ------------------------------------------------------------
// Linux transfer helpers
// ------------------------------------------------------------

void discardLinuxTransfer()
{
    bool discarded = false;
    Bridge.call("discard_sample").result(discarded);
}


bool transferRowsToLinux()
{
    for (uint16_t i = 0; i < SAMPLE_ROWS; ++i)
    {
        bool ok = false;

        Bridge.call(
            "add_sample_row",
            rows[i].timeMs,
            rows[i].phRaw,
            rows[i].orpRaw,
            rows[i].turbidityRaw,
            rows[i].tdsRaw
        ).result(ok);

        if (!ok)
        {
            Monitor.print("ERROR transferring row ");
            Monitor.println(i);
            discardLinuxTransfer();
            return false;
        }
    }

    return true;
}


bool saveSampleToLinux()
{
    drawMessage(
        "SAVING",
        labels[selectedLabel],
        "please wait"
    );

    bool ok = false;

    Bridge.call(
        "begin_sample",
        labels[selectedLabel]
    ).result(ok);

    if (!ok)
    {
        Monitor.println("ERROR: begin_sample failed");
        return false;
    }

    if (!transferRowsToLinux())
    {
        return false;
    }

    String savedPath;
    Bridge.call("finish_sample").result(savedPath);

    if (savedPath.length() == 0)
    {
        Monitor.println("ERROR: Linux did not save sample");
        return false;
    }

    Monitor.print("SAVED: ");
    Monitor.println(savedPath);
    return true;
}


bool testSampleWithLinux(
    String& prediction,
    int& confidencePercent
)
{
    drawMessage(
        "TEST WATER",
        "ANALYSING",
        "please wait"
    );

    bool ok = false;
    Bridge.call("begin_test_sample").result(ok);

    if (!ok)
    {
        Monitor.println("ERROR: begin_test_sample failed");
        return false;
    }

    if (!transferRowsToLinux())
    {
        return false;
    }

    ok = false;
    Bridge.call("finish_test_sample").result(ok);

    if (!ok)
    {
        Monitor.println("ERROR: test inference failed");
        return false;
    }

    Bridge.call(
        "get_test_prediction"
    ).result(prediction);

    Bridge.call(
        "get_test_confidence"
    ).result(confidencePercent);

    if (prediction.length() == 0)
    {
        Monitor.println("ERROR: empty test prediction");
        return false;
    }

    Monitor.print("Prediction: ");
    Monitor.println(prediction);
    Monitor.print("Confidence: ");
    Monitor.print(confidencePercent);
    Monitor.println('%');
    return true;
}


// ------------------------------------------------------------
// Collection save/discard and workflows
// ------------------------------------------------------------

void saveOrDiscard()
{
    drawMessage(
        "DONE",
        "WHITE = SAVE",
        "BLUE = DISCARD"
    );

    while (true)
    {
        if (pressed(WHITE_BUTTON_PIN))
        {
            bool saved = saveSampleToLinux();

            if (saved)
            {
                drawMessage(
                    "SAVED",
                    labels[selectedLabel],
                    ""
                );
            }
            else
            {
                drawMessage(
                    "SAVE ERROR",
                    "NOT SAVED",
                    ""
                );
            }

            return;
        }

        if (pressed(BLUE_BUTTON_PIN))
        {
            discardLinuxTransfer();
            Monitor.println("Sample discarded");

            drawMessage(
                "DISCARDED",
                labels[selectedLabel],
                ""
            );

            return;
        }

        delay(10);
    }
}


void collectWorkflow()
{
    bool chooseLabel = true;

    while (true)
    {
        if (chooseLabel)
        {
            if (!selectWater())
            {
                return;
            }
        }

        acquirePhase1();
        acquirePhase2();
        printSampleToMonitor();
        saveOrDiscard();

        delay(1200);

        CollectAction action = waitForCollectAction();

        if (action == CollectAction::MainMenu)
        {
            return;
        }

        chooseLabel =
            action == CollectAction::ChangeLabel;
    }
}


void testWorkflow()
{
    acquirePhase1();
    acquirePhase2();
    printSampleToMonitor();

    String prediction;
    int confidencePercent = 0;

    bool inferred = testSampleWithLinux(
        prediction,
        confidencePercent
    );

    if (!inferred)
    {
        drawMessage(
            "TEST ERROR",
            "NO RESULT",
            "WHITE = MENU"
        );

        while (!pressed(WHITE_BUTTON_PIN))
        {
            delay(10);
        }

        return;
    }

    drawTestResult(
        prediction,
        confidencePercent
    );

    while (true)
    {
        if (pressed(WHITE_BUTTON_PIN))
        {
            return;
        }

        if (pressed(BLUE_BUTTON_PIN))
        {
            drawReportQr();

            while (!pressed(WHITE_BUTTON_PIN))
            {
                delay(10);
            }

            drawTestResult(
                prediction,
                confidencePercent
            );
        }

        delay(10);
    }
}


// ------------------------------------------------------------
// Setup
// ------------------------------------------------------------

void setup()
{
    Bridge.begin();
    Monitor.begin(115200);

    analogReadResolution(ADC_BITS);

    pinMode(PH_PIN, INPUT);
    pinMode(TDS_PIN, INPUT);
    pinMode(TURBIDITY_PIN, INPUT);
    pinMode(ORP_PIN, INPUT);

    pinMode(ORP_CAL_PIN, OUTPUT);
    digitalWrite(ORP_CAL_PIN, LOW);

    pinMode(WHITE_BUTTON_PIN, INPUT_PULLUP);
    pinMode(BLUE_BUTTON_PIN, INPUT_PULLUP);

    Wire.begin();
    oled.begin();

    loadDefaultLabels();

    bool linuxReady = waitForLinux();

    if (linuxReady)
    {
        bool labelsLoaded = loadLabelsFromLinux();

        if (labelsLoaded)
        {
            Monitor.println("Labels loaded from Linux");
        }
        else
        {
            Monitor.println(
                "Could not load Linux labels; using MCU defaults"
            );
        }
    }
    else
    {
        Monitor.println(
            "Linux Bridge unavailable; using MCU defaults"
        );
    }
}


// ------------------------------------------------------------
// Main workflow
// ------------------------------------------------------------

void loop()
{
    MainMode mode = selectMainMode();

    if (mode == MainMode::Collect)
    {
        collectWorkflow();
    }
    else
    {
        testWorkflow();
    }
}

const int switchPin = 2;
const int startStopPin = 3;

volatile int pulseCount = 0;
volatile unsigned long lastPulseTime = 0;

int lastStartStopState = HIGH;
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;

void setup() {
  pinMode(switchPin, INPUT_PULLUP);
  pinMode(startStopPin, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(switchPin), countPulse, FALLING);

  Serial.begin(9600);

  lastStartStopState = digitalRead(startStopPin);
}

void loop() {
  unsigned long now = millis();

  // Check START / STOP switch on pin 3
  int currentStartStopState = digitalRead(startStopPin);

  if (currentStartStopState != lastStartStopState && (now - lastDebounceTime > debounceDelay)) {
    lastDebounceTime = now;

    if (currentStartStopState == LOW) {
      Serial.println("START");
    } else {
      Serial.println("STOP");
    }

    lastStartStopState = currentStartStopState;
  }

  // Check pulse digit from pin 2
  if (pulseCount > 0 && (now - lastPulseTime > 300)) {
    int digit = (pulseCount == 10) ? 0 : pulseCount;

    Serial.println(digit);

    pulseCount = 0;
  }
}

void countPulse() {
  unsigned long now = millis();

  if (now - lastPulseTime > 40) {
    pulseCount++;
    lastPulseTime = now;
  }
}
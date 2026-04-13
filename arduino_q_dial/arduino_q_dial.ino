const int switchPin = 2;                                                                                                                
                                                                                                                                          
  volatile int pulseCount = 0;                                                                                                            
  volatile unsigned long lastPulseTime = 0;

  void setup() {
    pinMode(switchPin, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(switchPin), countPulse, FALLING);
    Serial.begin(9600);                       
  }                                       

  void loop() {                                                                                                                           
    unsigned long now = millis();
                                                                                                                                          
    if (pulseCount > 0 && (now - lastPulseTime > 300)) {
      int digit = (pulseCount == 10) ? 0 : pulseCount;

      // Serial.print("Digit detected: ");
      Serial.println(digit);
                                              
      pulseCount = 0;                     
    }
  }                                                                                               
   
  void countPulse() {                                                                                                                     
    unsigned long now = millis();
    if (now - lastPulseTime > 40) {  // increased from 10
      pulseCount++;                           
      lastPulseTime = now;                
    }
  }
# AI Story Phone

A rotary telephone that lets children dial a sequence of numbers — each digit is equivalent to a story element/word. They hear a short AI-generated story through the handset.

## What It Does

- Children dial digits (1-4) on a vintage rotary telephone
- Each digit selects character and emotion for the AI story
- Audio is captured via microphone, sent to Deepgram for speech-to-text
- Transcribed text is sent to Claude for story generation
- Story is spoken back through Cartesia TTS

## Who It's For

**Children**: Makes AI fun and engaging while exposing them to retro tech.

**Parents**: AI in the 3D world — no screens, better for eyesight and posture.

## How to Run

1. **Install dependencies:**
   ```bash
   pip install anthropic deepgram pyaudio requests pyserial
   ```

2. **Configure environment** (create `.env`):
   ```
   DEEPGRAM_API_KEY=your_key
   ANTHROPIC_API_KEY=your_key
   CARTESIA_API_KEY=your_key
   ```

3. **Connect Arduino** running dial-reader code to `/dev/cu.usbmodem1101`

4. **Run:**
   ```bash
   python cartesiaagent-dial.py
   ```

5. **Dial inputs:**
   - `1` = Character A
   - `2` = Character B
   - `3` = Neutral emotion
   - `4` = Angry emotion

## File Overview

| File | Description |
|------|------------|
| `cartesiaagent-dial.py` | Main app with Arduino dial input |
| `cartesiaagent.py` | Original with keyboard input |
| `elevenlabs.py` | Alternative TTS (not used) |

## Hardware

- Vintage rotary telephone
- Arduino (reads dial pulses, sends via Serial)
- Computer with microphone/audio output

## Digit Mapping

| Digit | Character | Emotion |
|-------|----------|---------|
| 1 | A | — |
| 2 | B | — |
| 3 | — | neutral |
| 4 | — | angry |
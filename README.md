# AI Telephone Voice Agent

An interactive voice agent that simulates a phone call with an AI-powered character. Users can select different character voices and emotional tones, then have a natural conversation with the AI through microphone input and audio output.

## What It Does

- **Voice Selection**: Users dial a character (1-4) and emotion (5-8) to customize their AI companion
- **Live Conversation**: Speak naturally into the microphone—Deepgram transcribes your speech in real-time
- **AI Response**: Claude generates contextual, in-character responses with the selected personality
- **Voice Output**: ElevenLabs TTS speaks Claude's response in the selected character voice
- **Special Actions**: Quick shortcuts for jokes, music recommendations, facts, and advice (options 9-12)
- **Serial/Keyboard Control**: Supports both serial input (from Arduino dial) and keyboard input (START/STOP commands)

## Who It's For

**Children**: An engaging, screen-free way to interact with AI through voice and retro technology.

**Developers**: A modular phone conversation framework with character personalities and emotional tones.

## How to Run

### 1. Install Dependencies

```bash
pip install anthropic deepgram pyaudio requests pyserial python-dotenv
```

### 2. Configure Environment

Create a `.env` file in the project directory:

```env
DEEPGRAM_API_KEY=your_deepgram_key
ANTHROPIC_API_KEY=your_anthropic_key
ELEVENLABS_API_KEY=your_elevenlabs_key
ANTHROPIC_MODEL=claude-opus-4-1-20250805
SERIAL_PORT=/dev/cu.usbmodem1101
SERIAL_BAUD=9600
```

**Optional**: If no `SERIAL_PORT` is set, the system will use keyboard input only.

### 3. Run the Application

```bash
python test_local.py
```

## Voice Selection

### Characters (Press 1-4)

| Digit | Character | Available Voices |
|-------|-----------|-----------------|
| 1 | Girl | Kelly, Lala, Ireen, Karen |
| 2 | Duck | Merry, Sam, Brad, Parth |
| 3 | Boy | Harry, Jayden, Alex, Ben |
| 4 | Cartoon Mouse | Mickey, Katelyn, Radhika, Fany |

### Emotions (Press 5-8)

| Digit | Emotion | Description |
|-------|---------|-------------|
| 5 | Neutral | Calm and straightforward |
| 6 | Happy | Upbeat and enthusiastic |
| 7 | Chill | Relaxed and laid-back |
| 8 | Angry | Irritated and sharp |

### Special Actions (Press 9-12)

| Digit | Action | Description |
|-------|--------|-------------|
| 9 | Joke Teller | Get a short, kid-friendly joke |
| 10 | Random Music | Receive a music recommendation |
| 11 | Random Facts | Learn a surprising fact |
| 12 | Random Advice | Get a piece of advice |

## Control Commands

- **START**: Begin the phone call (keyboard or serial)
- **STOP**: End the entire application (keyboard or serial, works anytime)
- After voice selection, all serial input except START/STOP is ignored until the call ends

## File Overview

| File | Description |
|------|-------------|
| `test_local.py` | Main application (current) |
| `chatlog.txt` | Log of all conversations |
| `.env` | Configuration (API keys, serial port) |

## Architecture

- **Deepgram**: Real-time speech-to-text with noise detection and voice activity detection
- **Claude**: Generates conversational responses with character personality and emotional tone
- **ElevenLabs**: Converts text responses to natural-sounding speech
- **PyAudio**: Handles microphone input and speaker output
- **Serial Input**: Optional Arduino dial input support

## Hardware (Optional)

- Vintage rotary telephone or dial encoder
- Arduino (reads dial pulses, sends via Serial)
- Computer with microphone and speaker/headphone output

## Key Features

- **Smart Silence Detection**: Automatically detects speech boundaries using RMS analysis
- **Filler Sentences**: AI fills pauses with natural acknowledgments like "Hmm..." or "That's interesting."
- **Conversation Logging**: All exchanges are logged to `chatlog.txt` with timestamps
- **Ringtone Support**: Plays `ringtone.mp3` when the call is connected
- **Multi-threaded**: Concurrent TTS, STT, and Claude API calls for smooth interaction

## Troubleshooting

- **Serial port not found**: Check the `SERIAL_PORT` setting or use keyboard input only
- **No audio input**: Verify microphone is connected and PyAudio has access
- **Silent response from Claude**: Check `ANTHROPIC_API_KEY` and model availability
- **TTS not working**: Ensure `ELEVENLABS_API_KEY` is correct and voice IDs are valid

## Future Enhancements

- Additional character personalities and voice actors
- Custom prompt templates for different conversation styles
- Web interface for remote users
- Recording and playback of conversations
- Multi-language support

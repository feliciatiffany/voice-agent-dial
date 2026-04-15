import os
import time
import threading
import queue
import pyaudio
import requests
import unicodedata
import serial
from datetime import datetime
from anthropic import Anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


DIGIT_INPUT_QUEUE = queue.Queue()
# SERIAL_PORT = "/dev/cu.usbmodem1101"
# SERIAL_BAUD = 9600


VOICE_MAP = {
    ("A", "neutral"): "NDTYOmYEjbDIVCKB35i3",
    ("B", "neutral"): "WIi4Wzyjc860r5dZ3gjK",
    ("A", "angry"): "FCdKzv68Ofr4VUDcZXIy",
    ("B", "angry"): "z2P4oCxSHhXan3ew4COv",
}


def clean_env_value(value):
    if not value:
        return None

    return (
        value.strip()
        .strip('"')
        .strip("'")
        .replace("“", "")
        .replace("”", "")
        .replace("‘", "")
        .replace("’", "")
    )


def sanitize_text(text):
    text = unicodedata.normalize("NFKD", text)

    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
        "…": "...",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = "".join(c for c in text if 32 <= ord(c) <= 126 or c in "\n\t")
    return " ".join(text.split())


def serial_reader_thread():
    """Read digits from Arduino serial port."""
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        print(f"📟 Connected to Arduino on {SERIAL_PORT}")

        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    print(f"📟 Received digit from Arduino: {line}")
                    DIGIT_INPUT_QUEUE.put(line)

            time.sleep(0.1)

    except serial.SerialException as e:
        print(f"❌ Serial error: {e}")
    except Exception as e:
        print(f"❌ Reader error: {e}")


def get_digit_input(prompt_text, valid_options, timeout=30):
    """Get digit input from Arduino serial or keyboard fallback."""
    print(prompt_text)
    print(f"   waiting for digit {valid_options}... or press Enter for keyboard")

    start_time = time.time()

    while True:
        try:
            digit = DIGIT_INPUT_QUEUE.get(timeout=1)
            if digit in valid_options:
                return digit
            else:
                print(f"   Invalid digit: {digit}. Valid: {valid_options}")
        except queue.Empty:
            pass

        if time.time() - start_time > timeout:
            start_time = time.time()

        import sys
        import select

        if select.select([sys.stdin], [], [], 0)[0]:
            keyboard_input = input("   > ").strip()

            if keyboard_input in valid_options:
                return keyboard_input
            elif keyboard_input == "":
                return keyboard_input
            elif keyboard_input:
                print(f"   Invalid: {keyboard_input}. Valid: {valid_options}")


def create_wav_data(audio_frames, sample_rate=16000, channels=1, sample_width=2):
    """Create WAV data from raw microphone frames."""
    import struct

    audio_data = b"".join(audio_frames)

    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width

    riff_header = b"RIFF"
    file_size = 36 + len(audio_data)
    riff_header += struct.pack("<I", file_size)
    riff_header += b"WAVE"

    fmt_header = b"fmt "
    fmt_header += struct.pack("<I", 16)
    fmt_header += struct.pack("<H", 1)
    fmt_header += struct.pack("<H", channels)
    fmt_header += struct.pack("<I", sample_rate)
    fmt_header += struct.pack("<I", byte_rate)
    fmt_header += struct.pack("<H", block_align)
    fmt_header += struct.pack("<H", sample_width * 8)

    data_header = b"data"
    data_header += struct.pack("<I", len(audio_data))

    return riff_header + fmt_header + data_header + audio_data


def play_elevenlabs_tts(text, voice_id=None):
    """Generate and play ElevenLabs TTS using REST API."""
    try:
        elevenlabs_api_key = clean_env_value(os.getenv("ELEVENLABS_API_KEY"))

        if not elevenlabs_api_key:
            print("⚠️ ELEVENLABS_API_KEY not set, skipping TTS")
            return

        if not voice_id:
            print("⚠️ No voice ID provided, skipping TTS")
            return

        elevenlabs_api_key = elevenlabs_api_key.encode("ascii", "ignore").decode("ascii")
        voice_id = "".join(c for c in voice_id if ord(c) < 128)
        text = sanitize_text(text)

        if not text or len(text) < 2:
            print("❌ No valid text to speak")
            return

        print("\n🎵 Generating speech with ElevenLabs...")
        print(f"   Text: {text[:80]}...")

        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            headers={
                "xi-api-key": elevenlabs_api_key,
                "Content-Type": "application/json",
                "Accept": "application/octet-stream",
            },
            params={
                "output_format": "pcm_24000",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
                detail = error_data.get("detail", {})
                code = detail.get("code", "unknown")
                message = detail.get("message", response.text)

                print(f"❌ ElevenLabs API Error ({code}): {message}")

                if response.status_code == 402:
                    print("   This voice requires a paid ElevenLabs plan.")
                    print("   Use a voice from your own account or upgrade.")
            except Exception:
                print(f"❌ ElevenLabs API Error {response.status_code}: {response.text}")

            return

        audio_data = response.content

        if not audio_data:
            print("❌ No audio generated")
            return

        print(f"🔊 Playing audio ({len(audio_data)} bytes)...")

        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True,
        )

        stream.write(audio_data)
        stream.stop_stream()
        stream.close()
        p.terminate()

        print("✓ Audio playback complete")

    except Exception as e:
        print(f"❌ TTS Error: {e}")
        import traceback
        traceback.print_exc()


def record_audio():
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000
    RECORD_SECONDS = 30

    print("🎤 Initializing microphone... Speak now! Ctrl+C to exit")

    p = pyaudio.PyAudio()

    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )

    print("🔴 Recording audio...")

    frames = []
    silence_threshold = 1000
    silence_count = 0
    max_silence_chunks = 30

    for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)

        audio_data = bytearray(data)
        max_value = max(audio_data) if audio_data else 0

        if max_value < silence_threshold:
            silence_count += 1
            if silence_count > max_silence_chunks:
                print("⏸️ Silence detected, stopping recording...")
                break
        else:
            silence_count = 0

    stream.stop_stream()
    stream.close()
    p.terminate()

    wav_data = create_wav_data(frames, sample_rate=RATE, channels=CHANNELS, sample_width=2)
    print(f"📊 Recorded {len(wav_data)} bytes of audio")

    return wav_data


def transcribe_with_deepgram(wav_data, deepgram_api_key):
    print("\n📤 Sending audio to Deepgram...")

    response = requests.post(
        "https://api.deepgram.com/v1/listen",
        headers={
            "Authorization": f"Token {deepgram_api_key}",
            "Content-Type": "audio/wav",
        },
        params={
            "model": "nova-2",
            "language": "en",
            "smart_format": "true",
        },
        data=wav_data,
    )

    if response.status_code != 200:
        print(f"Deepgram API Response: {response.text}")

    response.raise_for_status()
    result = response.json()

    try:
        transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError):
        print(f"Could not extract transcript from: {result}")
        transcript = ""

    return transcript


def main():
    try:
        serial_thread = threading.Thread(target=serial_reader_thread, daemon=True)
        serial_thread.start()

        deepgram_api_key = clean_env_value(os.getenv("DEEPGRAM_API_KEY"))
        anthropic_api_key = clean_env_value(os.getenv("ANTHROPIC_API_KEY"))
        elevenlabs_api_key = clean_env_value(os.getenv("ELEVENLABS_API_KEY"))

        if not deepgram_api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable is not set")

        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

        if not elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY environment variable is not set")

        print("✓ API Keys found\n")

        claude = Anthropic(api_key=anthropic_api_key)

        char_digit_map = {
            "1": "A",
            "2": "B",
        }

        emotion_digit_map = {
            "3": "neutral",
            "4": "angry",
        }

        character_digit = get_digit_input(
            "Select character: digit 1=A, 2=B",
            ["1", "2"],
            timeout=60,
        )
        character = char_digit_map.get(character_digit, "A")

        emotion_digit = get_digit_input(
            "Select emotion: digit 3=neutral, 4=angry",
            ["3", "4"],
            timeout=60,
        )
        emotion = emotion_digit_map.get(emotion_digit, "neutral")

        voice_id = VOICE_MAP.get((character, emotion))

        print(f"✓ Selected: Character {character}, Emotion {emotion}")
        print(f"   Voice ID: {voice_id}\n")

        conversation_count = 0

        while True:
            conversation_count += 1

            print(f"\n{'=' * 60}")
            print(f"🔄 Conversation #{conversation_count}")
            print(f"{'=' * 60}")

            try:
                wav_data = record_audio()

                transcript = transcribe_with_deepgram(wav_data, deepgram_api_key)

                if not transcript.strip():
                    print("❌ No speech detected or empty transcription")
                    continue

                print(f"\n💬 Transcription:\n>>> {transcript}\n")

                print("Do you want to send this to Claude? Press Enter to confirm, or type new text")
                user_input = input("> ").strip()

                if user_input:
                    transcript = user_input

                if not transcript.strip():
                    print("❌ Empty transcript, skipping")
                    continue

                print(f"✏️ Using: {transcript}")

                with open("chatlog.txt", "a") as chatlog:
                    chatlog.write(f"\n[{datetime.now().isoformat()}] Conversation #{conversation_count}\n")
                    chatlog.write(f"User: {transcript}\n")

                print("\n🧠 Sending to Claude...")

                message = claude.messages.create(
                    model="claude-opus-4-1-20250805",
                    max_tokens=1024,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript,
                        }
                    ],
                )

                response_text = message.content[0].text

                print(f"\n🤖 Claude Response:\n{response_text}\n")

                with open("chatlog.txt", "a") as chatlog:
                    chatlog.write(f"Claude: {response_text}\n")

                print("⏳ Preparing ElevenLabs text-to-speech...")
                play_elevenlabs_tts(response_text, voice_id=voice_id)

                print("\n✓ Ready for next input")

            except KeyboardInterrupt:
                print("\n\n⏹️ Interrupted by user")
                break
            except Exception as e:
                print(f"❌ Error in conversation: {e}")
                import traceback
                traceback.print_exc()
                print("Continuing to next conversation...\n")
                continue

        print("\n✓ Exited. Check chatlog.txt for conversation history.")

    except Exception as e:
        print(f"❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
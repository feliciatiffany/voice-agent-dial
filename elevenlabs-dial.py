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
from deepgram import DeepgramClient
from deepgram.core.events import EventType
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


def listen_with_deepgram_until_speech_ends(deepgram_api_key):
    print("🎤 Listening with Deepgram live... Speak now!")

    deepgram = DeepgramClient(api_key=deepgram_api_key)

    transcript_parts = []
    stop_event = threading.Event()

    speech_started = False
    last_speech_time = None

    try:
        with deepgram.listen.v1.connect(
            model="nova-3",
            language="en-US",
            smart_format=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms="3000",
            vad_events=True,
            endpointing=300,
        ) as connection:

            def on_message(message):
                nonlocal speech_started, last_speech_time

                msg_type = getattr(message, "type", "Unknown")

                if msg_type == "SpeechStarted":
                    print("🗣️ Speech detected")
                    speech_started = True
                    last_speech_time = time.time()
                    return

                if msg_type == "UtteranceEnd":
                    print("⏸️ Deepgram utterance ended")
                    stop_event.set()
                    return

                if hasattr(message, "channel") and hasattr(message.channel, "alternatives"):
                    sentence = message.channel.alternatives[0].transcript

                    if not sentence:
                        return

                    speech_started = True
                    last_speech_time = time.time()

                    if getattr(message, "is_final", False):
                        transcript_parts.append(sentence)
                        print(f"📝 Final: {sentence}")

            connection.on(EventType.OPEN, lambda _: print("🔌 Deepgram connection opened"))
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, lambda _: print("🔒 Deepgram connection closed"))
            connection.on(EventType.ERROR, lambda error: print(f"❌ Deepgram error: {error}"))

            def listening_thread():
                try:
                    connection.start_listening()
                except Exception as e:
                    print(f"❌ Error in listening thread: {e}")
                    stop_event.set()

            listen_thread = threading.Thread(target=listening_thread, daemon=True)
            listen_thread.start()

            CHUNK = 1024
            RATE = 16000
            FORMAT = pyaudio.paInt16
            CHANNELS = 1

            NO_INITIAL_SPEECH_SECONDS = 3
            NO_SPEECH_AFTER_START_SECONDS = 3
            MAX_RECORD_SECONDS = 30

            p = pyaudio.PyAudio()
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )

            start_time = time.time()

            try:
                while not stop_event.is_set():
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    connection.send_media(data)

                    now = time.time()

                    if not speech_started and now - start_time >= NO_INITIAL_SPEECH_SECONDS:
                        print("⏸️ No speech detected for 3 seconds, stopping recording...")
                        stop_event.set()
                        break

                    if speech_started and last_speech_time:
                        if now - last_speech_time >= NO_SPEECH_AFTER_START_SECONDS:
                            print("⏸️ 3 seconds without speech, stopping recording...")
                            stop_event.set()
                            break

                    if now - start_time >= MAX_RECORD_SECONDS:
                        print("⏱️ Max recording time reached")
                        stop_event.set()
                        break

            finally:
                stop_event.set()
                stream.stop_stream()
                stream.close()
                p.terminate()
                connection.finish()          
                listen_thread.join(timeout=5.0)

    except Exception as e:
        print(f"❌ Could not open Deepgram socket: {e}")

    return " ".join(transcript_parts).strip()


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

        greeting = "Hi, my name is Claude. How can I help you today?"
        print(f"🤖 Claude Greeting:\n{greeting}\n")
        play_elevenlabs_tts(greeting, voice_id=voice_id)

        conversation_count = 0

        while True:
            conversation_count += 1

            print(f"\n{'=' * 60}")
            print(f"🔄 Conversation #{conversation_count}")
            print(f"{'=' * 60}")

            try:
                transcript = listen_with_deepgram_until_speech_ends(deepgram_api_key)

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
                    max_tokens=150,
                    system=(
                        "You are Claude, a spoken voice assistant. "
                        "Always answer in 50 words or fewer. "
                        "Be clear, conversational, and concise."
                    ),
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
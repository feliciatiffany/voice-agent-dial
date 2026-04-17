import os
import time
import math
import struct
import threading
import collections
import unicodedata
from datetime import datetime

import pyaudio
import requests
from anthropic import Anthropic
from deepgram import DeepgramClient
from deepgram.core.events import EventType

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


VOICE_MAP = {
    ("A", "neutral"): "NDTYOmYEjbDIVCKB35i3",
    ("B", "neutral"): "WIi4Wzyjc860r5dZ3gjK",
    ("A", "angry"): "FCdKzv68Ofr4VUDcZXIy",
    ("B", "angry"): "z2P4oCxSHhXan3ew4COv",
}

CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-1-20250805")

# Debug
DEBUG_AUDIO = False
DEBUG_EVERY_N_FRAMES = 8

# Audio settings
RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 1024

# Timing
NO_INITIAL_SPEECH_SECONDS = 8.0
NO_SPEECH_AFTER_START_SECONDS = 3.0
MAX_RECORD_SECONDS = 20.0
POST_TTS_PAUSE_SECONDS = 0.7
CALIBRATION_SECONDS = 0.8

# Volume thresholds
MIN_START_MULTIPLIER = 2.8
HOLD_MULTIPLIER = 1.8
ABSOLUTE_MIN_START_RMS = 450.0
ABSOLUTE_MIN_HOLD_RMS = 250.0
SPEECH_FRAMES_TO_START = 3
SPEECH_FRAMES_TO_HOLD = 2


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


def rms_level_pcm16(data: bytes) -> float:
    if not data:
        return 0.0
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack("<" + "h" * count, data)
    square_sum = 0
    for s in samples:
        square_sum += s * s
    return math.sqrt(square_sum / count)


def list_audio_devices():
    p = pyaudio.PyAudio()
    print("\n=== AUDIO DEVICES ===")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        print(
            f"[{i}] {info['name']} | "
            f"inputs={info['maxInputChannels']} outputs={info['maxOutputChannels']}"
        )
    p.terminate()


def get_digit_choice(prompt_text, valid_options, default_value=None):
    while True:
        suffix = f" (default {default_value})" if default_value else ""
        user_input = input(f"{prompt_text} {valid_options}{suffix}: ").strip()

        if user_input == "" and default_value is not None:
            return default_value

        if user_input in valid_options:
            return user_input

        print(f"Invalid choice. Please enter one of: {', '.join(valid_options)}")


def play_elevenlabs_tts(text, voice_id=None):
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
            params={"output_format": "pcm_24000"},
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
            timeout=60,
        )

        if response.status_code != 200:
            try:
                error_data = response.json()
                detail = error_data.get("detail", {})
                code = detail.get("code", "unknown")
                message = detail.get("message", response.text)
                print(f"❌ ElevenLabs API Error ({code}): {message}")
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
        time.sleep(POST_TTS_PAUSE_SECONDS)

    except Exception as e:
        print(f"❌ TTS Error: {e}")


def listen_with_deepgram_until_silence(deepgram_api_key):
    print("🎤 Listening with Deepgram live... Speak now!")

    deepgram = DeepgramClient(api_key=deepgram_api_key)

    transcript_parts = []
    stop_event = threading.Event()
    opened_event = threading.Event()

    speech_started = False
    last_confirmed_speech_time = None
    frame_count = 0
    consecutive_start_frames = 0
    consecutive_hold_frames = 0

    ambient_rms = None
    start_threshold = None
    hold_threshold = None
    speech_rms_history = collections.deque(maxlen=30)

    try:
        # Keep the minimal Deepgram websocket config from the working version.
        with deepgram.listen.v1.connect(
            model="nova-3",
            encoding="linear16",
            sample_rate=16000,
        ) as connection:

            def on_open(_):
                print("🔌 Deepgram opened")
                opened_event.set()

            def on_message(message):
                msg_type = getattr(message, "type", "")
                if msg_type != "Results":
                    return

                if not hasattr(message, "channel"):
                    return

                alts = getattr(message.channel, "alternatives", [])
                if not alts:
                    return

                text = (alts[0].transcript or "").strip()
                if not text:
                    return

                if getattr(message, "is_final", False):
                    transcript_parts.append(text)
                    print(f"✅ Final: {text}")
                elif DEBUG_AUDIO:
                    print(f"📝 Interim: {text}")

            def on_error(error):
                print(f"❌ Deepgram error: {error}")
                stop_event.set()

            connection.on(EventType.OPEN, on_open)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, on_error)
            connection.on(EventType.CLOSE, lambda _: print("🔒 Deepgram closed"))

            listener = threading.Thread(target=connection.start_listening, daemon=True)
            listener.start()

            if not opened_event.wait(timeout=5):
                raise RuntimeError("Deepgram websocket did not open")

            p = pyaudio.PyAudio()
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                input_device_index=None,
                frames_per_buffer=CHUNK,
            )

            start_time = time.time()

            try:
                calibration_values = []
                calibration_end = time.time() + CALIBRATION_SECONDS

                print("🎚️ Calibrating room noise... stay quiet")
                while time.time() < calibration_end:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    connection.send_media(data)
                    calibration_values.append(rms_level_pcm16(data))

                ambient_rms = sum(calibration_values) / max(len(calibration_values), 1)
                start_threshold = max(ABSOLUTE_MIN_START_RMS, ambient_rms * MIN_START_MULTIPLIER)
                hold_threshold = max(ABSOLUTE_MIN_HOLD_RMS, ambient_rms * HOLD_MULTIPLIER)

                print(
                    f"🎚️ ambient_rms={ambient_rms:.1f} | "
                    f"start_threshold={start_threshold:.1f} | "
                    f"hold_threshold={hold_threshold:.1f}"
                )

                while not stop_event.is_set():
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    connection.send_media(data)

                    now = time.time()
                    frame_count += 1
                    rms = rms_level_pcm16(data)

                    is_start_candidate = rms >= start_threshold
                    is_hold_candidate = rms >= hold_threshold

                    if not speech_started:
                        if is_start_candidate:
                            consecutive_start_frames += 1
                        else:
                            consecutive_start_frames = 0

                        if consecutive_start_frames >= SPEECH_FRAMES_TO_START:
                            speech_started = True
                            last_confirmed_speech_time = now
                            speech_rms_history.append(rms)
                            print(f"🗣️ speech_started=True at {now:.2f} (rms={rms:.1f})")
                    else:
                        if is_hold_candidate:
                            consecutive_hold_frames += 1
                            speech_rms_history.append(rms)
                        else:
                            consecutive_hold_frames = 0

                        if consecutive_hold_frames >= SPEECH_FRAMES_TO_HOLD:
                            old_time = last_confirmed_speech_time
                            last_confirmed_speech_time = now

                            avg_speech_rms = sum(speech_rms_history) / len(speech_rms_history)
                            hold_threshold = max(
                                ABSOLUTE_MIN_HOLD_RMS,
                                ambient_rms * HOLD_MULTIPLIER,
                                avg_speech_rms * 0.35,
                            )

                            old_time_str = f"{old_time:.2f}" if old_time is not None else "None"
                            print(
                                f"🔄 refresh last_confirmed_speech_time: "
                                f"{old_time_str} -> {last_confirmed_speech_time:.2f} | "
                                f"rms={rms:.1f} | hold_threshold={hold_threshold:.1f}"
                            )

                    if DEBUG_AUDIO and frame_count % DEBUG_EVERY_N_FRAMES == 0:
                        silence_for_dbg = None
                        if speech_started and last_confirmed_speech_time is not None:
                            silence_for_dbg = now - last_confirmed_speech_time
                        print(
                            f"[DEBUG] frame={frame_count} rms={rms:.1f} ambient={ambient_rms:.1f} "
                            f"start_th={start_threshold:.1f} hold_th={hold_threshold:.1f} "
                            f"start_candidate={is_start_candidate} hold_candidate={is_hold_candidate} "
                            f"speech_started={speech_started} start_frames={consecutive_start_frames} "
                            f"hold_frames={consecutive_hold_frames} silence_for={silence_for_dbg}"
                        )

                    if not speech_started and now - start_time >= NO_INITIAL_SPEECH_SECONDS:
                        print("⏸️ No loud speech detected in initial window")
                        break

                    if speech_started and last_confirmed_speech_time is not None:
                        silence_for = now - last_confirmed_speech_time
                        if DEBUG_AUDIO:
                            print(f"[CHECK] silence_for={silence_for:.2f}")
                        if silence_for >= NO_SPEECH_AFTER_START_SECONDS:
                            print("⏸️ No strong speech for 3 seconds, stopping")
                            break

                    if now - start_time >= MAX_RECORD_SECONDS:
                        print("⏱️ Max recording time reached")
                        break

            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()

                try:
                    connection.send_finalize()
                except Exception:
                    pass

                time.sleep(1)

                try:
                    connection.send_close_stream()
                except Exception:
                    pass

                listener.join(timeout=3)

    except Exception as e:
        print(f"❌ Could not open Deepgram socket: {e}")
        return None

    transcript = " ".join(transcript_parts).strip()
    print(f"📄 Final combined transcript: {transcript if transcript else '[empty]'}")
    return transcript


def ask_claude(claude, transcript):
    message = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=120,
        system=(
            "You are Claude, a spoken voice assistant. "
            "Always answer in 50 words or fewer. "
            "Be clear, natural, and conversational."
        ),
        messages=[
            {
                "role": "user",
                "content": transcript,
            }
        ],
    )
    return message.content[0].text.strip()


def append_chatlog(turn_number, user_text=None, claude_text=None):
    with open("chatlog.txt", "a", encoding="utf-8") as chatlog:
        if user_text is not None:
            chatlog.write(f"\n[{datetime.now().isoformat()}] Conversation #{turn_number}\n")
            chatlog.write(f"User: {user_text}\n")
        if claude_text is not None:
            chatlog.write(f"Claude: {claude_text}\n")


def main():
    deepgram_api_key = clean_env_value(os.getenv("DEEPGRAM_API_KEY"))
    anthropic_api_key = clean_env_value(os.getenv("ANTHROPIC_API_KEY"))
    elevenlabs_api_key = clean_env_value(os.getenv("ELEVENLABS_API_KEY"))

    if not deepgram_api_key:
        raise ValueError("DEEPGRAM_API_KEY is not set")
    if not anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    if not elevenlabs_api_key:
        raise ValueError("ELEVENLABS_API_KEY is not set")

    print("✓ API Keys found\n")

    claude = Anthropic(api_key=anthropic_api_key)

    char_digit_map = {"1": "A", "2": "B"}
    emotion_digit_map = {"3": "neutral", "4": "angry"}

    character_digit = get_digit_choice(
        "Select character: digit 1=A, 2=B",
        ["1", "2"],
        default_value="1",
    )
    character = char_digit_map.get(character_digit, "A")

    emotion_digit = get_digit_choice(
        "Select emotion: digit 3=neutral, 4=angry",
        ["3", "4"],
        default_value="3",
    )
    emotion = emotion_digit_map.get(emotion_digit, "neutral")

    voice_id = VOICE_MAP.get((character, emotion))

    print(f"✓ Selected: Character {character}, Emotion {emotion}")
    print(f"   Voice ID: {voice_id}\n")

    list_audio_devices()

    greeting = "Hi, my name is Claude. How can I help you today?"
    print(f"🤖 Claude Greeting:\n{greeting}\n")
    play_elevenlabs_tts(greeting, voice_id=voice_id)

    conversation_count = 0
    consecutive_connection_failures = 0

    while True:
        conversation_count += 1

        print(f"\n{'=' * 60}")
        print(f"🔄 Conversation #{conversation_count}")
        print(f"{'=' * 60}")

        try:
            transcript = listen_with_deepgram_until_silence(deepgram_api_key)

            if transcript is None:
                consecutive_connection_failures += 1
                print(f"⚠️ Deepgram connection failed ({consecutive_connection_failures}/3)")
                if consecutive_connection_failures >= 3:
                    print("⛔ Too many websocket failures. Exiting.")
                    break
                time.sleep(1)
                continue

            consecutive_connection_failures = 0

            if not transcript.strip():
                print("⚠️ No speech detected or empty transcription")
                continue

            print(f"\n💬 Transcription:\n>>> {transcript}\n")
            append_chatlog(conversation_count, user_text=transcript)

            print("🧠 Sending to Claude...")
            response_text = ask_claude(claude, transcript)

            print(f"\n🤖 Claude Response:\n{response_text}\n")
            append_chatlog(conversation_count, claude_text=response_text)

            play_elevenlabs_tts(response_text, voice_id=voice_id)
            print("✓ Ready for next input")

        except KeyboardInterrupt:
            print("\n\n⏹️ Interrupted by user")
            break
        except Exception as e:
            print(f"❌ Error in conversation: {e}")
            print("Continuing to next conversation...\n")
            continue

    print("\n✓ Exited. Check chatlog.txt for conversation history.")


if __name__ == "__main__":
    main()

import os
import time
import math
import struct
import threading
import collections
import unicodedata

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


VOICE_ID = "NDTYOmYEjbDIVCKB35i3"
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-1-20250805")

# Debug
DEBUG_AUDIO = True
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


def play_elevenlabs_tts(text, voice_id=VOICE_ID):
    try:
        elevenlabs_api_key = clean_env_value(os.getenv("ELEVENLABS_API_KEY"))
        if not elevenlabs_api_key:
            print("⚠️ ELEVENLABS_API_KEY not set, skipping TTS")
            return

        text = sanitize_text(text)
        if not text:
            return

        print(f"\n🎵 Speaking: {text}")

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
            print(f"❌ ElevenLabs error {response.status_code}: {response.text}")
            return

        audio_data = response.content
        if not audio_data:
            print("❌ No audio returned from ElevenLabs")
            return

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

        time.sleep(POST_TTS_PAUSE_SECONDS)

    except Exception as e:
        print(f"❌ TTS error: {e}")


def listen_with_deepgram_until_silence(deepgram_api_key):
    print("\n🎤 Listening... speak now")

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
                else:
                    if DEBUG_AUDIO:
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
                # Step 1: calibrate room noise
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

                # Step 2: detect strong speech by volume
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

                        # print(
                        #     f"[DEBUG] frame={frame_count} "
                        #     f"rms={rms:.1f} "
                        #     f"ambient={ambient_rms:.1f} "
                        #     f"start_th={start_threshold:.1f} "
                        #     f"hold_th={hold_threshold:.1f} "
                        #     f"start_candidate={is_start_candidate} "
                        #     f"hold_candidate={is_hold_candidate} "
                        #     f"speech_started={speech_started} "
                        #     f"start_frames={consecutive_start_frames} "
                        #     f"hold_frames={consecutive_hold_frames} "
                        #     f"last_speech={last_confirmed_speech_time} "
                        #     f"silence_for={silence_for_dbg}"
                        # )

                    if not speech_started and now - start_time >= NO_INITIAL_SPEECH_SECONDS:
                        print("⏸️ No loud speech detected in initial window")
                        break

                    if speech_started and last_confirmed_speech_time is not None:
                        silence_for = now - last_confirmed_speech_time
                        print(f"[CHECK] silence_for={silence_for:.2f}")

                        if silence_for >= NO_SPEECH_AFTER_START_SECONDS:
                            print("⏸️ No strong speech for 3 seconds, stopping")
                            break

                    if now - start_time >= MAX_RECORD_SECONDS:
                        print("⏱️ Max record time reached")
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

    print("✓ API keys found")

    claude = Anthropic(api_key=anthropic_api_key)

    greeting = "Hi, my name is Claude. How can I help you today?"
    print(f"\n🤖 Claude: {greeting}")
    play_elevenlabs_tts(greeting)

    turn = 0
    consecutive_connection_failures = 0

    while True:
        try:
            turn += 1
            print(f"\n{'=' * 50}")
            print(f"Turn {turn}")
            print(f"{'=' * 50}")

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

            if not transcript:
                print("⚠️ No speech captured. Listening again...")
                continue

            print(f"\n💬 You said: {transcript}")

            response_text = ask_claude(claude, transcript)

            print(f"\n🤖 Claude: {response_text}")
            play_elevenlabs_tts(response_text)

        except KeyboardInterrupt:
            print("\n⏹️ Stopped by user")
            break
        except Exception as e:
            print(f"❌ Loop error: {e}")


if __name__ == "__main__":
    main()
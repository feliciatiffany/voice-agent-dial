import os
import time
import threading
import pyaudio

from deepgram import DeepgramClient
from deepgram.core.events import EventType

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


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


def test_macbook_mic_deepgram():
    deepgram_api_key = clean_env_value(os.getenv("DEEPGRAM_API_KEY"))
    if not deepgram_api_key:
        raise ValueError("DEEPGRAM_API_KEY is not set")

    client = DeepgramClient(api_key=deepgram_api_key)

    transcript_parts = []
    open_event = threading.Event()

    print("🎤 Using MacBook microphone only")
    print("Speak for 5 seconds...")

    try:
        with client.listen.v1.connect(
            model="nova-3",
            language="en-US",
            smart_format=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
            endpointing=300,
            vad_events=True,
            utterance_end_ms="1000",
        ) as connection:

            def on_open(_):
                print("🔌 Deepgram connection opened")
                open_event.set()

            def on_message(message):
                msg_type = getattr(message, "type", "Unknown")
                print(f"📡 Event type: {msg_type}")

                if msg_type != "Results":
                    return

                if not hasattr(message, "channel") or not hasattr(message.channel, "alternatives"):
                    return

                sentence = (message.channel.alternatives[0].transcript or "").strip()
                if not sentence:
                    return

                is_final = getattr(message, "is_final", False)
                if is_final:
                    print(f"✅ Final: {sentence}")
                    transcript_parts.append(sentence)
                else:
                    print(f"📝 Interim: {sentence}")

            def on_error(error):
                print(f"❌ Deepgram error: {error}")

            connection.on(EventType.OPEN, on_open)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, on_error)
            connection.on(EventType.CLOSE, lambda _: print("🔒 Deepgram connection closed"))

            listener = threading.Thread(target=connection.start_listening, daemon=True)
            listener.start()

            if not open_event.wait(timeout=5):
                raise RuntimeError("WebSocket did not open")

            CHUNK = 1024
            RATE = 16000
            CHANNELS = 1
            FORMAT = pyaudio.paInt16
            RECORD_SECONDS = 5

            p = pyaudio.PyAudio()

            # input_device_index=None => macOS default input (usually MacBook mic or system-selected mic)
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                input_device_index=None,
                frames_per_buffer=CHUNK,
            )

            print("🔴 Recording from default Mac input now...")

            for _ in range(int(RATE / CHUNK * RECORD_SECONDS)):
                data = stream.read(CHUNK, exception_on_overflow=False)
                connection.send_media(data)

            stream.stop_stream()
            stream.close()
            p.terminate()

            print("⏹️ Recording finished")
            connection.send_finalize()
            time.sleep(2)
            connection.send_close_stream()
            listener.join(timeout=3)

    except Exception as e:
        print(f"❌ Deepgram socket error: {e}")

    final_transcript = " ".join(transcript_parts).strip()

    print("\n" + "=" * 50)
    print("FINAL TRANSCRIPT:")
    print(final_transcript if final_transcript else "[empty]")
    print("=" * 50)


if __name__ == "__main__":
    list_audio_devices()   # optional: helps confirm your MacBook mic index
    test_macbook_mic_deepgram()
import os
import time
import threading
import pyaudio

from deepgram import DeepgramClient
from deepgram.core.events import EventType
from anthropic import Anthropic

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


def test_deepgram_live_then_claude():
    deepgram_api_key = clean_env_value(os.getenv("DEEPGRAM_API_KEY"))
    anthropic_api_key = clean_env_value(os.getenv("ANTHROPIC_API_KEY"))

    if not deepgram_api_key:
        raise ValueError("DEEPGRAM_API_KEY is not set")

    if not anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    deepgram = DeepgramClient(api_key=deepgram_api_key)
    claude = Anthropic(api_key=anthropic_api_key)

    transcript_parts = []

    print("🎤 Deepgram live test")
    print("Speak for 5 seconds...")

    try:
        with deepgram.listen.v1.connect(
            model="nova-3",
            language="en-US",
            smart_format=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
        ) as connection:

            def on_message(message):
                msg_type = getattr(message, "type", "Unknown")
                print(f"📡 Event type: {msg_type}")

                if hasattr(message, "channel") and hasattr(message.channel, "alternatives"):
                    sentence = message.channel.alternatives[0].transcript

                    if not sentence:
                        return

                    is_final = getattr(message, "is_final", False)

                    if is_final:
                        print(f"✅ Final transcript: {sentence}")
                        transcript_parts.append(sentence)
                    else:
                        print(f"📝 Interim transcript: {sentence}")

            connection.on(EventType.OPEN, lambda _: print("🔌 Deepgram connection opened"))
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, lambda _: print("🔒 Deepgram connection closed"))
            connection.on(EventType.ERROR, lambda error: print(f"❌ Deepgram error: {error}"))

            def listening_thread():
                try:
                    connection.start_listening()
                except Exception as e:
                    print(f"❌ Listening thread error: {e}")

            listen_thread = threading.Thread(target=listening_thread, daemon=True)
            listen_thread.start()

            CHUNK = 1024
            RATE = 16000
            CHANNELS = 1
            FORMAT = pyaudio.paInt16
            RECORD_SECONDS = 5

            p = pyaudio.PyAudio()

            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )

            print("🔴 Recording and streaming to Deepgram now...")

            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = stream.read(CHUNK, exception_on_overflow=False)
                connection.send_media(data)

            print("⏹️ Finished 5 seconds of recording")

            stream.stop_stream()
            stream.close()
            p.terminate()

            # Give Deepgram a moment to return final transcript
            time.sleep(2)

            try:
                connection.finish()
            except Exception:
                pass

            listen_thread.join(timeout=3)

    except Exception as e:
        print(f"❌ Deepgram socket error: {e}")

    final_transcript = " ".join(transcript_parts).strip()

    print("\n" + "=" * 50)
    print("FINAL TRANSCRIPT:")
    print(final_transcript if final_transcript else "[empty]")
    print("=" * 50)

    if not final_transcript:
        print("❌ Deepgram did not return transcript text.")
        return

    print("\n🧠 Sending transcript to Claude...")

    message = claude.messages.create(
        model="claude-opus-4-1-20250805",
        max_tokens=150,
        system=(
            "You are Claude, a spoken voice assistant. "
            "Answer in 50 words or fewer."
        ),
        messages=[
            {
                "role": "user",
                "content": final_transcript,
            }
        ],
    )

    response_text = message.content[0].text

    print("\n🤖 Claude Response:")
    print(response_text)


if __name__ == "__main__":
    test_deepgram_live_then_claude()
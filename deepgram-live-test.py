import os, time, math, struct, threading, unicodedata
from datetime import datetime
from collections import deque

import pyaudio, requests
from anthropic import Anthropic
from deepgram import DeepgramClient
from deepgram.core.events import EventType

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------- Config ----------
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-1-20250805")

VOICE_MAP = {
    "1": {"name": "Girl", "voices": {
        "1": ("neutral", "7YaUDeaStRuoYg3FKsmU"),
        "2": ("happy",   "d3MFdIuCfbAIwiu7jC4a"),
        "3": ("sad",     "t4U671CQHG58R11znrVj"),
        "4": ("angry",   "dIeHOwebB4fO6l6gNfUK"),
    }},
    "2": {"name": "Child", "voices": {
        "1": ("neutral", "hO2yZ8lxM3axUxL8OeKX"),
        "2": ("happy",   "vGQNBgLaiM3EdZtxIiuY"),
        "3": ("sad",     "o80picuztV1xYiPeIrpa"),
        "4": ("angry",   "9vP6R7VVxNwGIGLnpl17"),
    }},
    "3": {"name": "Boy", "voices": {
        "1": ("neutral", "fvVBPXuE7f1iX3dZLKFy"),
        "2": ("happy",   "15CVCzDByBinCIoCblXo"),
        "3": ("sad",     "6xPz2opT0y5qtoRh1U1Y"),
        "4": ("angry",   "raMcNf2S8wCmuaBcyI6E"),
    }},
    "4": {"name": "Cartoon Mouse", "voices": {
        "1": ("neutral", "XJ2fW4ybq7HouelYYGcL"),
        "2": ("happy",   "ocZQ262SsZb9RIxcQBOj"),
        "3": ("sad",     "mdzEgLpu0FjTwYs5oot0"),
        "4": ("angry",   "87n4zM8Wuy87vFILuKvE"),
    }},
}

RATE, CHUNK = 16000, 1024
NO_INITIAL_SPEECH_SECONDS = 8
NO_SPEECH_AFTER_START_SECONDS = 3
MAX_RECORD_SECONDS = 20
CALIBRATION_SECONDS = 0.8
POST_TTS_PAUSE_SECONDS = 0.7

MIN_START_MULTIPLIER = 2.8
HOLD_MULTIPLIER = 1.8
ABS_MIN_START_RMS = 450
ABS_MIN_HOLD_RMS = 250
FRAMES_TO_START = 3
FRAMES_TO_HOLD = 2

# ---------- Small helpers ----------
def env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value.strip().strip('"').strip("'").replace("“", "").replace("”", "")


def clean_text(text):
    text = unicodedata.normalize("NFKD", text)
    for a, b in {"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "..."}.items():
        text = text.replace(a, b)
    return " ".join("".join(c for c in text if 32 <= ord(c) <= 126 or c in "\n\t").split())


def rms(data):
    if len(data) < 2:
        return 0.0
    samples = struct.unpack("<" + "h" * (len(data) // 2), data)
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def choose(prompt, options, default="1"):
    while True:
        val = input(f"{prompt} {options} (default {default}): ").strip() or default
        if val in options:
            return val
        print(f"Invalid. Choose one of: {', '.join(options)}")


def choose_voice():
    print("\nCharacters: 1=Girl, 2=Child, 3=Boy, 4=Cartoon Mouse")
    c_key = choose("Select character", VOICE_MAP.keys())
    char = VOICE_MAP[c_key]

    print("Emotions: 1=neutral, 2=happy, 3=sad, 4=angry")
    e_key = choose("Select emotion", char["voices"].keys())
    emotion, voice_id = char["voices"][e_key]

    print(f"✓ Selected: {char['name']} / {emotion}\n")
    return voice_id

# ---------- Output ----------
def speak(text, voice_id):
    key = env("ELEVENLABS_API_KEY")
    text = clean_text(text)
    if not text:
        return

    print(f"\n🎵 Speaking: {text}")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "application/octet-stream"},
        params={"output_format": "pcm_24000"},
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=60,
    )
    if r.status_code != 200:
        print(f"❌ ElevenLabs error {r.status_code}: {r.text}")
        return

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
    stream.write(r.content)
    stream.close()
    p.terminate()
    time.sleep(POST_TTS_PAUSE_SECONDS)

# ---------- Input / STT ----------
def listen(deepgram_key):
    print("\n🎤 Listening... speak now")
    dg = DeepgramClient(api_key=deepgram_key)
    parts, opened, stop = [], threading.Event(), threading.Event()

    def on_msg(msg):
        if getattr(msg, "type", "") != "Results" or not hasattr(msg, "channel"):
            return
        alts = getattr(msg.channel, "alternatives", [])
        text = (alts[0].transcript or "").strip() if alts else ""
        if text and getattr(msg, "is_final", False):
            print(f"✅ Final: {text}")
            parts.append(text)

    try:
        with dg.listen.v1.connect(model="nova-3", encoding="linear16", sample_rate=RATE) as conn:
            conn.on(EventType.OPEN, lambda _: (print("🔌 Deepgram opened"), opened.set()))
            conn.on(EventType.MESSAGE, on_msg)
            conn.on(EventType.ERROR, lambda e: (print(f"❌ Deepgram error: {e}"), stop.set()))
            conn.on(EventType.CLOSE, lambda _: print("🔒 Deepgram closed"))

            threading.Thread(target=conn.start_listening, daemon=True).start()
            if not opened.wait(5):
                raise RuntimeError("Deepgram websocket did not open")

            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                            input_device_index=None, frames_per_buffer=CHUNK)

            try:
                ambient = calibrate(stream, conn)
                start_th = max(ABS_MIN_START_RMS, ambient * MIN_START_MULTIPLIER)
                hold_th = max(ABS_MIN_HOLD_RMS, ambient * HOLD_MULTIPLIER)
                print(f"🎚️ ambient={ambient:.1f} | start_th={start_th:.1f} | hold_th={hold_th:.1f}")

                started = False
                start_frames = hold_frames = 0
                last_voice = None
                speech_levels = deque(maxlen=30)
                t0 = time.time()

                while not stop.is_set():
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    conn.send_media(data)
                    now, level = time.time(), rms(data)

                    if not started:
                        start_frames = start_frames + 1 if level >= start_th else 0
                        if start_frames >= FRAMES_TO_START:
                            started, last_voice = True, now
                            speech_levels.append(level)
                            print(f"🗣️ Speech started (rms={level:.1f})")
                    else:
                        hold_frames = hold_frames + 1 if level >= hold_th else 0
                        if hold_frames >= FRAMES_TO_HOLD:
                            last_voice = now
                            speech_levels.append(level)
                            avg = sum(speech_levels) / len(speech_levels)
                            hold_th = max(ABS_MIN_HOLD_RMS, ambient * HOLD_MULTIPLIER, avg * 0.35)

                        if now - last_voice >= NO_SPEECH_AFTER_START_SECONDS:
                            print("⏸️ No strong speech for 3 seconds")
                            break

                    if not started and now - t0 >= NO_INITIAL_SPEECH_SECONDS:
                        print("⏸️ No loud speech detected")
                        break
                    if now - t0 >= MAX_RECORD_SECONDS:
                        print("⏱️ Max recording time reached")
                        break
            finally:
                stream.close()
                p.terminate()
                try:
                    conn.send_finalize()
                    time.sleep(1)
                    conn.send_close_stream()
                except Exception:
                    pass
    except Exception as e:
        print(f"❌ Deepgram socket error: {e}")
        return None

    text = " ".join(parts).strip()
    print(f"📄 Transcript: {text or '[empty]'}")
    return text


def calibrate(stream, conn):
    print("🎚️ Calibrating room noise... stay quiet")
    vals, end = [], time.time() + CALIBRATION_SECONDS
    while time.time() < end:
        data = stream.read(CHUNK, exception_on_overflow=False)
        conn.send_media(data)
        vals.append(rms(data))
    return sum(vals) / max(len(vals), 1)

# ---------- Claude ----------
def ask_claude(client, text):
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=120,
        system="You are Claude, a spoken voice assistant. Always answer in 50 words or fewer. Be clear and conversational.",
        messages=[{"role": "user", "content": text}],
    )
    return msg.content[0].text.strip()


def log(turn, user=None, claude=None):
    with open("chatlog.txt", "a", encoding="utf-8") as f:
        if user:
            f.write(f"\n[{datetime.now().isoformat()}] Conversation #{turn}\nUser: {user}\n")
        if claude:
            f.write(f"Claude: {claude}\n")

# ---------- Main ----------
def main():
    deepgram_key = env("DEEPGRAM_API_KEY")
    anthropic_key = env("ANTHROPIC_API_KEY")
    env("ELEVENLABS_API_KEY")
    claude = Anthropic(api_key=anthropic_key)

    voice_id = choose_voice()
    greeting = "Hi, my name is Claude. How can I help you today?"
    print(f"🤖 Claude: {greeting}")
    speak(greeting, voice_id)

    turn = 1
    while True:
        try:
            print(f"\n{'=' * 56}\n🔄 Conversation #{turn}\n{'=' * 56}")
            text = listen(deepgram_key)
            if text is None:
                print("⚠️ STT failed. Trying again...")
                continue
            if not text:
                print("⚠️ Empty transcript. Listening again...")
                continue

            print(f"\n💬 You: {text}")
            log(turn, user=text)
            reply = ask_claude(claude, text)
            print(f"\n🤖 Claude: {reply}")
            log(turn, claude=reply)
            speak(reply, voice_id)
            turn += 1

        except KeyboardInterrupt:
            print("\n⏹️ Stopped")
            break
        except Exception as e:
            print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()

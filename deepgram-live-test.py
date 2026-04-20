import os, sys, time, math, struct, threading, unicodedata, queue, select
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
WOMAN_NEUTRAL_VOICE_ID = "7YaUDeaStRuoYg3FKsmU"  # Girl / neutral

VOICE_MAP = {
    "1": {"name": "Girl", "voices": {
        "5": ("neutral", "7YaUDeaStRuoYg3FKsmU"),
        "6": ("happy",   "d3MFdIuCfbAIwiu7jC4a"),
        "7": ("sad",     "t4U671CQHG58R11znrVj"),
        "8": ("angry",   "dIeHOwebB4fO6l6gNfUK"),
    }},
    "2": {"name": "Child", "voices": {
        "5": ("neutral", "hO2yZ8lxM3axUxL8OeKX"),
        "6": ("happy",   "vGQNBgLaiM3EdZtxIiuY"),
        "7": ("sad",     "o80picuztV1xYiPeIrpa"),
        "8": ("angry",   "9vP6R7VVxNwGIGLnpl17"),
    }},
    "3": {"name": "Boy", "voices": {
        "5": ("neutral", "fvVBPXuE7f1iX3dZLKFy"),
        "6": ("happy",   "15CVCzDByBinCIoCblXo"),
        "7": ("sad",     "6xPz2opT0y5qtoRh1U1Y"),
        "8": ("angry",   "raMcNf2S8wCmuaBcyI6E"),
    }},
    "4": {"name": "Cartoon Mouse", "voices": {
        "5": ("neutral", "XJ2fW4ybq7HouelYYGcL"),
        "6": ("happy",   "ocZQ262SsZb9RIxcQBOj"),
        "7": ("sad",     "mdzEgLpu0FjTwYs5oot0"),
        "8": ("angry",   "87n4zM8Wuy87vFILuKvE"),
    }},
}

RATE, CHUNK = 16000, 1024
NO_INITIAL_SPEECH_SECONDS = 8
NO_SPEECH_AFTER_START_SECONDS = 3
MAX_RECORD_SECONDS = 20
CALIBRATION_SECONDS = 0.8
POST_TTS_PAUSE_SECONDS = 0.7
SELECTION_STEP_PAUSE_SECONDS = 2.5  # Pause between character and emotion prompts so user can read/hear
PICKUP_DELAY_SECONDS = 2.0  # After START, wait this long so the kid can lift the phone to their ear

MIN_START_MULTIPLIER = 2.8
HOLD_MULTIPLIER = 1.8
ABS_MIN_START_RMS = 450
ABS_MIN_HOLD_RMS = 250
FRAMES_TO_START = 3
FRAMES_TO_HOLD = 2

# Optional serial config. Example:
# export 
# SERIAL_PORT="/dev/cu.usbmodem1101"
# export 
# SERIAL_BAUD="9600"
# SERIAL_PORT = os.getenv("SERIAL_PORT")
# SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/cu.usbmodem101")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))

CMD_QUEUE = queue.Queue()
STOP_EVENT = threading.Event()
START_EVENT = threading.Event()

# ---------- Helpers ----------
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


def handle_command(cmd):
    if cmd is None:
        return None
    cmd = cmd.strip()
    upper = cmd.upper()
    if upper == "STOP":
        print("\n🛑 STOP received. Exiting...")
        STOP_EVENT.set()
    elif upper == "START":
        print("\n▶️ START received")
        START_EVENT.set()
    return cmd


def poll_keyboard(timeout=0.0):
    """Read a full line from terminal if available. Mac/Linux terminal only."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return handle_command(sys.stdin.readline())
    except Exception:
        return None
    return None


def next_command(timeout=0.1):
    """Read command from serial queue first, then keyboard."""
    try:
        return handle_command(CMD_QUEUE.get_nowait())
    except queue.Empty:
        return poll_keyboard(timeout)


def serial_reader():
    if not SERIAL_PORT:
        print("ℹ️ SERIAL_PORT not set. Using keyboard only for START/STOP and choices.")
        return
    try:
        import serial
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        print(f"📟 Connected to serial: {SERIAL_PORT} @ {SERIAL_BAUD}")
        while not STOP_EVENT.is_set():
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line:
                print(f"📟 Serial: {line}")
                CMD_QUEUE.put(line)
                handle_command(line)
            time.sleep(0.02)
        ser.close()
    except Exception as e:
        print(f"⚠️ Serial unavailable: {e}")


def wait_for_start():
    print("\nWaiting for START. Type START + Enter or send START from serial.")
    print("Type STOP anytime to quit.")
    while not STOP_EVENT.is_set():
        if START_EVENT.is_set():
            return True
        next_command(0.1)
    return False


def choose(prompt, options, default="1", feedback_voice_id=None, step_name=""):
    options = list(options)
    print(f"{prompt} {options} (default {default}; type STOP to quit):")
    while not STOP_EVENT.is_set():
        cmd = next_command(0.1)
        if cmd is None:
            continue
        if cmd == "":
            return default
        if cmd in options:
            return cmd
        if cmd.upper() in ("START", "STOP"):
            continue
        # Invalid input — tell the user (both in terminal and, if we have a voice, out loud)
        valid_list = ", ".join(options)
        first, last = options[0], options[-1]
        print(f"⚠️ Invalid input '{cmd}'. Please {step_name or 'choose'} one of: {valid_list}")
        if feedback_voice_id:
            if step_name:
                msg = (
                    f"Oops! That's not a {step_name} choice. "
                    f"Please pick a number from {first} to {last} for the {step_name}."
                )
            else:
                msg = f"Oops! Please pick a number from {first} to {last}."
            speak(msg, feedback_voice_id)
        print(f"{prompt} {options} (default {default}; type STOP to quit):")
    return None


def choose_voice():
    print("\nStep 1 of 2: Choose your CHARACTER (numbers 1 to 4).")
    print("Characters: 1=Girl, 2=Child, 3=Boy, 4=Cartoon Mouse")
    c_key = choose(
        "Select character",
        VOICE_MAP.keys(),
        default="1",
        feedback_voice_id=WOMAN_NEUTRAL_VOICE_ID,
        step_name="character",
    )
    if c_key is None:
        return None, None, None
    char = VOICE_MAP[c_key]
    print(f"✓ Character set to: {char['name']}")

    # Longer pause so the user can register the character selection before the next prompt
    time.sleep(SELECTION_STEP_PAUSE_SECONDS)

    speak(f"Great, you picked {char['name']}. Now choose your emotion.", WOMAN_NEUTRAL_VOICE_ID)
    print("\nStep 2 of 2: Now choose your EMOTION (numbers 5 to 8).")
    print("Emotions: 5=neutral, 6=happy, 7=sad, 8=angry")
    e_key = choose(
        "Select emotion",
        char["voices"].keys(),
        default="5",
        feedback_voice_id=WOMAN_NEUTRAL_VOICE_ID,
        step_name="emotion",
    )
    if e_key is None:
        return None, None, None

    emotion, voice_id = char["voices"][e_key]
    print(f"✓ Selected: {char['name']} / {emotion}\n")
    return voice_id, char["name"], emotion

# ---------- Output ----------
def speak(text, voice_id):
    if STOP_EVENT.is_set():
        return
    key = env("ELEVENLABS_API_KEY")
    text = clean_text(text)
    if not text:
        return

    print(f"\n🎵 Speaking: {text}")
    try:
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
    except Exception as e:
        print(f"❌ TTS error: {e}")

# ---------- Input / STT ----------
def calibrate(stream, conn):
    print("🎚️ Calibrating room noise... stay quiet")
    vals, end = [], time.time() + CALIBRATION_SECONDS
    while time.time() < end and not STOP_EVENT.is_set():
        cmd = next_command(0.0)
        if STOP_EVENT.is_set():
            break
        data = stream.read(CHUNK, exception_on_overflow=False)
        conn.send_media(data)
        vals.append(rms(data))
    return sum(vals) / max(len(vals), 1)


def listen(deepgram_key):
    if STOP_EVENT.is_set():
        return None
    print("\n🎤 Listening... speak now. Type STOP + Enter or send STOP from serial to quit.")
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

                while not stop.is_set() and not STOP_EVENT.is_set():
                    cmd = next_command(0.0)
                    if STOP_EVENT.is_set():
                        break

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
                try:
                    stream.stop_stream()
                except Exception:
                    pass
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

    threading.Thread(target=serial_reader, daemon=True).start()
    claude = Anthropic(api_key=anthropic_key)

    if not wait_for_start():
        print("✓ Exited before start")
        return

    # Give the kid time to lift the phone to their ear before the greeting begins
    print(f"📞 Waiting {PICKUP_DELAY_SECONDS:.1f}s for phone pickup...")
    pickup_end = time.time() + PICKUP_DELAY_SECONDS
    while time.time() < pickup_end and not STOP_EVENT.is_set():
        next_command(0.1)
    if STOP_EVENT.is_set():
        print("✓ Exited during pickup delay")
        return

    speak(
        "Hi, I'm Claude. First, please choose your character. "
        "After that, I'll ask you to choose an emotion.",
        WOMAN_NEUTRAL_VOICE_ID,
    )
    voice_id, character, emotion = choose_voice()
    if STOP_EVENT.is_set() or not voice_id:
        print("✓ Exited during selection")
        return

    # Character introduces themselves in their own voice, by name — kid-friendly
    intro_name = character
    # "a Cartoon Mouse" reads better than "Cartoon Mouse" in the intro sentence
    article_name = f"a {intro_name}" if intro_name == "Cartoon Mouse" else intro_name
    speak(f"Hi, I'm {article_name}! How can I help you today?", voice_id)

    turn = 1
    while not STOP_EVENT.is_set():
        try:
            print(f"\n{'=' * 56}\n🔄 Conversation #{turn}\n{'=' * 56}")
            text = listen(deepgram_key)

            if STOP_EVENT.is_set():
                break
            if text is None:
                print("⚠️ STT failed. Trying again...")
                continue
            if not text:
                print("⚠️ Empty transcript. Listening again...")
                continue

            print(f"\n💬 You: {text}")
            log(turn, user=text)

            reply = ask_claude(claude, text)
            if STOP_EVENT.is_set():
                break

            print(f"\n🤖 Claude: {reply}")
            log(turn, claude=reply)
            speak(reply, voice_id)
            turn += 1

        except KeyboardInterrupt:
            STOP_EVENT.set()
        except Exception as e:
            print(f"❌ Error: {e}")

    print("\n🛑 Program stopped. Terminal is still open; Python script exited.")


if __name__ == "__main__":
    main()

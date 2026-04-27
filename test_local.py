import os, sys, time, math, struct, threading, unicodedata, queue, select, re
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

SPECIAL_ACTIONS = {
    "9": {
        "intro": "Hi, you just picked a random joke teller.",
        "prompt": "Tell one short, playful, kid-friendly joke. Keep it under 30 words.",
    },
    "10": {
        "intro": "Hi, I'm going to pick random music for you.",
        "prompt": "Recommend one fun song or music style for a kid to listen to. Keep it under 30 words.",
    },
    "11": {
        "intro": "Hi, I'm going to give you random facts.",
        "prompt": "Share one surprising, kid-friendly random fact. Keep it under 30 words.",
    },
    "12": {
        "intro": "Hi, you have picked a random advice generator.",
        "prompt": "Give one short, warm, kid-friendly piece of advice. Keep it under 30 words.",
    },
}

RATE, CHUNK = 16000, 1024
NO_INITIAL_SPEECH_SECONDS = 8
NO_SPEECH_AFTER_START_SECONDS = 2
MAX_RECORD_SECONDS = 20
CALIBRATION_SECONDS = 0.8
POST_TTS_PAUSE_SECONDS = 0.7
PICKUP_DELAY_SECONDS = 2.0

MIN_START_MULTIPLIER = 2.8
HOLD_MULTIPLIER = 1.8
ABS_MIN_START_RMS = 450
ABS_MIN_HOLD_RMS = 250
FRAMES_TO_START = 3
FRAMES_TO_HOLD = 2

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/cu.usbmodem1101")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))

# Optional mic selection:
# - MIC_DEVICE_INDEX: integer PyAudio device index
# - MIC_DEVICE_NAME: substring match against device names (case-insensitive)
MIC_DEVICE_INDEX = os.getenv("MIC_DEVICE_INDEX")
MIC_DEVICE_NAME = os.getenv("MIC_DEVICE_NAME")
MIC_CHANNELS = int(os.getenv("MIC_CHANNELS", "1"))
MIC_TEST_SECONDS = float(os.getenv("MIC_TEST_SECONDS", "5"))
MIC_TEST_MODE = os.getenv("MIC_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

CMD_QUEUE = queue.Queue()
STOP_EVENT = threading.Event()
START_EVENT = threading.Event()

SPECIAL_RESULT_PREFIX = "__SPECIAL__:"
SPECIAL_LABELS = {"9": "joke", "10": "music", "11": "facts", "12": "advice"}


def special_result(key):
    return f"{SPECIAL_RESULT_PREFIX}{key}"


def is_special_result(value):
    return isinstance(value, str) and value.startswith(SPECIAL_RESULT_PREFIX)


def get_special_key(value):
    return value.replace(SPECIAL_RESULT_PREFIX, "", 1)

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


def to_mono_16le(data, channels):
    """Downmix interleaved int16 LE audio to mono bytes."""
    if channels == 1:
        return data
    if channels != 2:
        raise ValueError(f"Unsupported MIC_CHANNELS={channels}; use 1 or 2")
    if len(data) < 4:
        return b""
    samples = struct.unpack("<" + "h" * (len(data) // 2), data)
    # samples = [L0, R0, L1, R1, ...]
    mono = []
    for i in range(0, len(samples) - 1, 2):
        mono.append(int((samples[i] + samples[i + 1]) / 2))
    return struct.pack("<" + "h" * len(mono), *mono)

def list_input_devices(p):
    try:
        n = p.get_device_count()
    except Exception:
        return []
    devices = []
    for i in range(n):
        try:
            info = p.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                devices.append((i, info))
        except Exception:
            continue
    return devices


def pick_input_device_index(p):
    """Return a PyAudio input_device_index or None for default."""
    if MIC_DEVICE_INDEX:
        try:
            return int(MIC_DEVICE_INDEX)
        except ValueError:
            print(f"⚠️ MIC_DEVICE_INDEX is not an int: {MIC_DEVICE_INDEX!r}")

    if MIC_DEVICE_NAME:
        want = MIC_DEVICE_NAME.strip().lower()
        for idx, info in list_input_devices(p):
            name = str(info.get("name", "")).lower()
            if want and want in name:
                return idx

    return None


def print_audio_inputs(p):
    devices = list_input_devices(p)
    if not devices:
        print("⚠️ No input devices found via PyAudio.")
        return
    print("\n🎙️ Available input devices:")
    for idx, info in devices:
        name = info.get("name", "")
        ch = int(info.get("maxInputChannels", 0))
        sr = info.get("defaultSampleRate", "")
        print(f"  - index={idx} | channels={ch} | defaultSR={sr} | {name}")
    if MIC_DEVICE_INDEX or MIC_DEVICE_NAME:
        print(f"🎙️ Mic selection env: MIC_DEVICE_INDEX={MIC_DEVICE_INDEX!r}, MIC_DEVICE_NAME={MIC_DEVICE_NAME!r}")
    print(f"🎙️ MIC_CHANNELS={MIC_CHANNELS}")


def mic_test():
    """Simple local mic test (no Deepgram): prints RMS for a few seconds."""
    p = pyaudio.PyAudio()
    try:
        print_audio_inputs(p)
        input_device_index = pick_input_device_index(p)
        if input_device_index is not None:
            try:
                picked = p.get_device_info_by_index(input_device_index)
                print(f"🎙️ Mic test using index={input_device_index}: {picked.get('name','')}")
            except Exception:
                print(f"🎙️ Mic test using index={input_device_index}")
        else:
            print("🎙️ Mic test using default input device")

        stream = p.open(
            format=pyaudio.paInt16,
            channels=MIC_CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=input_device_index,
            frames_per_buffer=CHUNK,
        )
        try:
            print(f"🧪 Mic test: speak now for {MIC_TEST_SECONDS:.1f}s")
            end = time.time() + MIC_TEST_SECONDS
            peak = 0.0
            while time.time() < end:
                data = stream.read(CHUNK, exception_on_overflow=False)
                mono = to_mono_16le(data, MIC_CHANNELS)
                level = rms(mono)
                peak = max(peak, level)
                print(f"🎚️ rms={level:.1f} (peak={peak:.1f})")
                time.sleep(0.02)
        finally:
            try:
                stream.stop_stream()
            except Exception:
                pass
            stream.close()
    finally:
        p.terminate()


def handle_command(cmd):
    if cmd is None:
        return None
    cmd = cmd.strip()
    if not cmd:
        return None
    upper = cmd.upper()
    if upper == "STOP":
        print("\n🛑 STOP received. Exiting...")
        STOP_EVENT.set()
    elif upper == "START":
        print("\n▶️ START received")
        START_EVENT.set()

    # If Arduino prints verbose lines (e.g. "Button: 7"), accept the first integer token.
    if cmd not in VOICE_MAP and cmd not in SPECIAL_ACTIONS:
        m = re.search(r"\b\d+\b", cmd)
        if m:
            cmd = m.group(0)
    return cmd


def poll_keyboard(timeout=0.0):
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return handle_command(sys.stdin.readline())
    except Exception:
        return None
    return None


def next_command(timeout=0.1):
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
                # Support either single command per line ("5") or multiple tokens ("3 5").
                if line.strip().upper() in ("START", "STOP"):
                    CMD_QUEUE.put(line)
                else:
                    nums = re.findall(r"\b\d+\b", line)
                    if nums:
                        for n in nums:
                            CMD_QUEUE.put(n)
                    else:
                        CMD_QUEUE.put(line)
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


def print_mix_options():
    print("\nDial your AI voice:")
    print("Characters: 1=Girl, 2=Child, 3=Boy, 4=Cartoon Mouse")
    print("Emotions:   5=neutral, 6=happy, 7=sad, 8=angry")
    print("Extras:     9=joke teller, 10=random music, 11=random fact, 12=random advice")
    print("Type STOP anytime to quit.")

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

# ---------- Claude ----------
VOICE_SYSTEM_PROMPT = """\
You are a curious, casual AI friend on a phone call — NOT a helper, assistant, or expert.
Never try to fix problems, offer advice, give information, or explain things unless the person \
explicitly begs you for it.
Your whole vibe is genuine curiosity about the person you're talking to.
Ask one short follow-up question at a time — about their life, feelings, opinions, weird thoughts, \
anything interesting.
Never open with "How can I help you?" or any version of it.
Instead, kick off with a surprising or random question to get them talking.
Keep every reply under 40 words. Be warm, playful, and a little unpredictable.\
"""


def ask_claude(client, text, max_tokens=120, character=None, emotion=None):
    system = VOICE_SYSTEM_PROMPT
    if character and emotion:
        system += f"\n\nYou are playing the character: {character}. Your emotional tone is: {emotion}. Stay in character."
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    return msg.content[0].text.strip()


def run_special_action(claude, key, voice_id=None, ask_followup=False):
    """Run a one-off special mode.

    key: 9=joke, 10=music, 11=facts, 12=advice
    voice_id: use selected character voice during the call, or woman neutral before selection
    ask_followup: after the special action, ask the user how it was and return to listening
    """
    action = SPECIAL_ACTIONS[key]
    active_voice = voice_id or WOMAN_NEUTRAL_VOICE_ID

    speak(action["intro"], active_voice)
    if STOP_EVENT.is_set():
        return

    try:
        reply = ask_claude(claude, action["prompt"], max_tokens=80)
    except Exception as e:
        print(f"❌ Claude special action error: {e}")
        return

    print(f"\n✨ {reply}")
    speak(reply, active_voice)

    if ask_followup and not STOP_EVENT.is_set():
        label = SPECIAL_LABELS.get(key, "answer")
        speak(f"Hi, how was my {label}? is there anything else you want to talk about?", active_voice)

def choose_voice_mix_match(claude):
    """Let the user pick character (1-4) and emotion (5-8) in any order.
    If the second required choice is completed, announce that they can meet the AI bestfriend.
    """
    speak("Hi, I'm your AI bestfriend, mix a character and emotion you want to talk to.", WOMAN_NEUTRAL_VOICE_ID)
    print_mix_options()

    character_key = None
    emotion_key = None

    emotion_names = {"5": "neutral", "6": "happy", "7": "sad", "8": "angry"}

    while not STOP_EVENT.is_set():
        cmd = next_command(0.1)
        if cmd is None or cmd == "":
            continue

        upper = cmd.upper()
        if upper in ("START", "STOP"):
            continue

        # One-off special modes
        if cmd in SPECIAL_ACTIONS:
            run_special_action(claude, cmd, WOMAN_NEUTRAL_VOICE_ID, ask_followup=False)
            if not STOP_EVENT.is_set():
                print_mix_options()
            continue

        # Character picked: 1-4
        if cmd in VOICE_MAP:
            if character_key is not None:
                if emotion_key is None:
                    speak("Oh, you already pick a character, now choose emotion.", WOMAN_NEUTRAL_VOICE_ID)
                else:
                    speak("Oh, you already picked your character and emotion, now meet your AI bestfriend.", WOMAN_NEUTRAL_VOICE_ID)
                continue

            character_key = cmd
            character_name = VOICE_MAP[character_key]["name"]

            if emotion_key is None:
                speak(f"You have picked {character_name} for character, now choose emotion.", WOMAN_NEUTRAL_VOICE_ID)
                continue

            speak(f"You have picked {character_name} for character, now meet your AI bestfriend.", WOMAN_NEUTRAL_VOICE_ID)
            break

        # Emotion picked: 5-8
        if cmd in emotion_names:
            if emotion_key is not None:
                if character_key is None:
                    speak("Oh, you already pick an emotion, now choose character.", WOMAN_NEUTRAL_VOICE_ID)
                else:
                    speak("Oh, you already picked your emotion and character, now meet your AI bestfriend.", WOMAN_NEUTRAL_VOICE_ID)
                continue

            emotion_key = cmd
            emotion_name = emotion_names[emotion_key]

            if character_key is None:
                speak(f"You have picked {emotion_name} for emotion, now choose character.", WOMAN_NEUTRAL_VOICE_ID)
                continue

            speak(f"You have picked {emotion_name} for emotion, now meet your AI bestfriend.", WOMAN_NEUTRAL_VOICE_ID)
            break

        speak("Please choose a character from 1 to 4, an emotion from 5 to 8, or an extra option from 9 to 12.", WOMAN_NEUTRAL_VOICE_ID)
        print_mix_options()

    if STOP_EVENT.is_set():
        return None, None, None

    char = VOICE_MAP[character_key]
    emotion, voice_id = char["voices"][emotion_key]
    print(f"✓ Selected: {char['name']} / {emotion}\n")
    return voice_id, char["name"], emotion

# ---------- Input / STT ----------
def calibrate(stream, conn):
    print("🎚️ Calibrating room noise... stay quiet")
    vals, end = [], time.time() + CALIBRATION_SECONDS
    while time.time() < end and not STOP_EVENT.is_set():
        cmd = next_command(0.0)
        if STOP_EVENT.is_set():
            break
        if cmd in SPECIAL_ACTIONS:
            print(f"✨ Special option {cmd} received during calibration")
            return None, cmd

        data = stream.read(CHUNK, exception_on_overflow=False)
        mono = to_mono_16le(data, MIC_CHANNELS)
        if mono:
            conn.send_media(mono)
            vals.append(rms(mono))

    ambient = sum(vals) / max(len(vals), 1)
    return ambient, None

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
            print_audio_inputs(p)
            input_device_index = pick_input_device_index(p)
            if input_device_index is not None:
                try:
                    picked = p.get_device_info_by_index(input_device_index)
                    print(f"🎙️ Using input device index={input_device_index}: {picked.get('name','')}")
                except Exception:
                    print(f"🎙️ Using input device index={input_device_index}")
            else:
                print("🎙️ Using default input device")

            stream = p.open(
                format=pyaudio.paInt16,
                channels=MIC_CHANNELS,
                rate=RATE,
                input=True,
                input_device_index=input_device_index,
                frames_per_buffer=CHUNK,
            )

            try:
                ambient, special_key = calibrate(stream, conn)
                if special_key:
                    return special_result(special_key)

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
                    if cmd in SPECIAL_ACTIONS:
                        print(f"✨ Special option {cmd} received during call")
                        return special_result(cmd)

                    data = stream.read(CHUNK, exception_on_overflow=False)
                    mono = to_mono_16le(data, MIC_CHANNELS)
                    conn.send_media(mono)
                    now, level = time.time(), rms(mono)

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


def log(turn, user=None, claude=None):
    with open("chatlog.txt", "a", encoding="utf-8") as f:
        if user:
            f.write(f"\n[{datetime.now().isoformat()}] Conversation #{turn}\nUser: {user}\n")
        if claude:
            f.write(f"Claude: {claude}\n")

# ---------- Main ----------
def main():
    print(f"🐍 Python: {sys.executable}")
    try:
        print(f"📄 Script: {os.path.abspath(__file__)}")
    except Exception:
        pass
    try:
        print(f"📁 CWD: {os.getcwd()}")
    except Exception:
        pass

    deepgram_key = env("DEEPGRAM_API_KEY")
    anthropic_key = env("ANTHROPIC_API_KEY")
    env("ELEVENLABS_API_KEY")

    # Print mic devices once at startup so we can verify which environment/file is running.
    try:
        p = pyaudio.PyAudio()
        print_audio_inputs(p)
        p.terminate()
    except Exception as e:
        print(f"⚠️ Audio device enumeration failed: {e}")

    if MIC_TEST_MODE:
        mic_test()
        return

    threading.Thread(target=serial_reader, daemon=True).start()
    claude = Anthropic(api_key=anthropic_key)

    if not wait_for_start():
        print("✓ Exited before start")
        return

    print(f"📞 Waiting {PICKUP_DELAY_SECONDS:.1f}s for phone pickup...")
    pickup_end = time.time() + PICKUP_DELAY_SECONDS
    while time.time() < pickup_end and not STOP_EVENT.is_set():
        next_command(0.1)
    if STOP_EVENT.is_set():
        print("✓ Exited during pickup delay")
        return

    voice_id, character, emotion = choose_voice_mix_match(claude)
    if STOP_EVENT.is_set() or not voice_id:
        print("✓ Exited during voice selection")
        return

    opening = ask_claude(claude, "Start the conversation with a surprising or random question to the person you just met. Do not greet, do not introduce yourself.", max_tokens=60, character=character, emotion=emotion)
    speak(opening, voice_id)

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
            if is_special_result(text):
                special_key = get_special_key(text)
                run_special_action(claude, special_key, voice_id, ask_followup=True)
                turn += 1
                continue
            if not text:
                print("⚠️ Empty transcript. Listening again...")
                continue

            print(f"\n💬 You: {text}")
            log(turn, user=text)

            reply = ask_claude(claude, text, character=character, emotion=emotion)
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

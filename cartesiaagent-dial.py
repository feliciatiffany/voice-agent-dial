# Deepgram Flux STT → Python → Claude LLM workflow

import os
import json
import time
import threading
import queue
import pyaudio
import requests
import unicodedata
import serial
from datetime import datetime
from deepgram import DeepgramClient
from anthropic import Anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DIGIT_INPUT_QUEUE = queue.Queue()
SERIAL_PORT = "/dev/cu.usbmodem1101"
SERIAL_BAUD = 9600

def serial_reader_thread():
    """Read digits from Arduino serial port."""
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        print(f"📟 Connected to Arduino on {SERIAL_PORT}")
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
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
    print(f"   (waiting for digit {valid_options}... or press Enter for keyboard)")
    
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
    """Create WAV file data from audio frames."""
    import struct
    
    # Combine frames
    audio_data = b''.join(audio_frames)
    
    # WAV header
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    
    # RIFF header
    riff_header = b'RIFF'
    file_size = 36 + len(audio_data)
    riff_header += struct.pack('<I', file_size)
    riff_header += b'WAVE'
    
    # fmt subchunk
    fmt_header = b'fmt '
    fmt_header += struct.pack('<I', 16)  # Subchunk1Size
    fmt_header += struct.pack('<H', 1)   # AudioFormat (1 = PCM)
    fmt_header += struct.pack('<H', channels)
    fmt_header += struct.pack('<I', sample_rate)
    fmt_header += struct.pack('<I', byte_rate)
    fmt_header += struct.pack('<H', block_align)
    fmt_header += struct.pack('<H', sample_width * 8)  # BitsPerSample
    
    # data subchunk
    data_header = b'data'
    data_header += struct.pack('<I', len(audio_data))
    
    return riff_header + fmt_header + data_header + audio_data

def get_cartesia_voices():
    """Fetch available voices from Cartesia API."""
    try:
        cartesia_api_key = os.getenv("CARTESIA_API_KEY")
        if not cartesia_api_key:
            return None
        
        cartesia_api_key = ''.join(c for c in cartesia_api_key if ord(c) < 128)
        today = datetime.now().strftime("%Y-%m-%d")
        
        headers = {
            "Authorization": f"Bearer {cartesia_api_key}".encode('ascii', 'ignore').decode('ascii'),
            "Cartesia-Version": today,
        }
        
        response = requests.get(
            "https://api.cartesia.ai/voices",
            headers=headers,
        )
        
        if response.status_code == 200:
            data = response.json()
            voices = data.get("voices", [])
            if voices:
                # Return first available voice ID
                return voices[0].get("id")
    except Exception as e:
        print(f"⚠️  Could not fetch voices: {str(e)}")
    
    return None

def play_cartesia_tts(text, voice_id=None):
    """Generate and play audio using Cartesia TTS via REST API."""
    try:
        cartesia_api_key = os.getenv("CARTESIA_API_KEY")
        if not cartesia_api_key:
            print("⚠️  CARTESIA_API_KEY not set, skipping TTS")
            return
        
        # Get voice ID if not provided
        if not voice_id:
            voice_id = get_cartesia_voices()
            if not voice_id:
                print("⚠️  Could not fetch voice IDs from Cartesia, skipping TTS")
                return
        
        # Sanitize API key - remove any non-ASCII characters
        cartesia_api_key = ''.join(c for c in cartesia_api_key if ord(c) < 128)
        
        # Aggressive sanitization for text - encode and decode with error handling
        # First normalize
        text = unicodedata.normalize('NFKD', text)
        # Encode to UTF-8 then back to get pure ASCII-safe text
        text = text.encode('utf-8', 'replace').decode('utf-8', 'replace')
        # Replace smart quotes and dashes
        replacements = {
            '"': '"', '"': '"', '"': '"',
            ''': "'", ''': "'",
            '–': '-', '—': '-', '…': '...',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        # Final pass: keep only ASCII printable characters
        text = ''.join(c for c in text if 32 <= ord(c) <= 126 or c in '\n\t')
        text = ' '.join(text.split())  # Clean up whitespace
        
        if not text or len(text) < 2:
            print("❌ No valid text to speak")
            return
        
        print(f"\n🎵 Generating speech with Cartesia...")
        print(f"   Text: {text[:80]}...")
        
        # Use Cartesia REST API directly
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Sanitize headers to ASCII
        headers = {
            "Authorization": f"Bearer {cartesia_api_key}".encode('ascii', 'ignore').decode('ascii'),
            "Cartesia-Version": today,
            "Content-Type": "application/json",
        }
        
        response = requests.post(
            "https://api.cartesia.ai/tts/bytes",
            headers=headers,
            json={
                "model_id": "sonic-english",
                "transcript": text,
                "voice": {"mode": "id", "id": voice_id},
                "output_format": {"container": "wav", "encoding": "pcm_f32le", "sample_rate": 24000},
            },
        )
        
        # Handle errors
        if response.status_code != 200:
            try:
                error_data = response.json()
                error_code = error_data.get("error_code", "unknown")
                message = error_data.get("message", response.text)
                request_id = error_data.get("request_id", "unknown")
                print(f"❌ Cartesia API Error ({error_code}): {message}")
                print(f"   Request ID: {request_id}")
            except:
                print(f"❌ Cartesia API Error {response.status_code}: {response.text}")
            return
        
        audio_data = response.content
        
        if not audio_data:
            print("❌ No audio generated")
            return
        
        print(f"🔊 Playing audio ({len(audio_data)} bytes)...")
        
        # Parse WAV header and convert to 16-bit PCM if needed
        import struct
        
        # Check if it's a WAV file (RIFF header)
        if audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE':
            # Parse WAV header
            audio_offset = 44  # Standard WAV header size
            # Try to find 'data' chunk
            pos = 12
            while pos < len(audio_data) - 8:
                chunk_id = audio_data[pos:pos+4]
                chunk_size = struct.unpack('<I', audio_data[pos+4:pos+8])[0]
                if chunk_id == b'data':
                    audio_offset = pos + 8
                    break
                pos += 8 + chunk_size
            
            audio_samples = audio_data[audio_offset:]
            
            # Convert float32 to int16 if needed
            if len(audio_samples) >= 4:
                # Check if it looks like float32 (values typically between -1 and 1)
                # Convert from float32 to int16
                import array
                float_array = array.array('f', audio_samples)
                int_array = array.array('h', [int(x * 32767) for x in float_array])
                audio_samples = int_array.tobytes()
            
            audio_data = audio_samples
        
        # Play audio using PyAudio
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
        print(f"❌ TTS Error: {str(e)}")
        import traceback
        traceback.print_exc()

def main():
    try:
        serial_thread = threading.Thread(target=serial_reader_thread, daemon=True)
        serial_thread.start()
        
        # Initialize API clients
        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        if not deepgram_api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable is not set")
        
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        
        print("✓ API Keys found\n")
        
        # Initialize clients
        deepgram = DeepgramClient(api_key=deepgram_api_key)
        claude = Anthropic(api_key=anthropic_api_key)
        
        # Voice configuration
        voice_map = {
            ("A", "neutral"): "e07c00bc-4134-4eae-9ea4-1a55fb45746b",
            ("B", "neutral"): "5ee9feff-1265-424a-9d7f-8e4d431a12c7",
            ("A", "angry"): "0b32066b-2bcc-44b9-89ab-0223a09d1606",
            ("B", "angry"): "fd098a10-ba9e-445e-b144-be2a9f3dac02",
        }
        
        char_digit_map = {"1": "A", "2": "B"}
        emotion_digit_map = {"3": "neutral", "4": "angry"}
        
        character_digit = get_digit_input("Select character: digit 1=A, 2=B", ["1", "2"], timeout=60)
        character = char_digit_map.get(character_digit, "A")
        
        emotion_digit = get_digit_input("Select emotion: digit 3=neutral, 4=angry", ["3", "4"], timeout=60)
        emotion = emotion_digit_map.get(emotion_digit, "neutral")
        
        voice_id = voice_map.get((character, emotion))
        print(f"✓ Selected: Character {character}, Emotion {emotion}\n")
        
        # Audio parameters
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        RECORD_SECONDS = 30
        
        # Continuous loop
        conversation_count = 0
        while True:
            conversation_count += 1
            print(f"\n{'='*60}")
            print(f"🔄 Conversation #{conversation_count}")
            print(f"{'='*60}")
            
            try:
                # Capture audio from microphone
                print("🎤 Initializing microphone... Speak now! (Ctrl+C to exit)")
                p = pyaudio.PyAudio()
                
                stream = p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK
                )
                
                print("🔴 Recording audio...")
                frames = []
                silence_threshold = 1000
                silence_count = 0
                max_silence_chunks = 30
                
                for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)
                    
                    # Simple silence detection
                    audio_data = bytearray(data)
                    max_value = max(audio_data) if audio_data else 0
                    
                    if max_value < silence_threshold:
                        silence_count += 1
                        if silence_count > max_silence_chunks:
                            print("⏸️  Silence detected, stopping recording...")
                            break
                    else:
                        silence_count = 0
                
                stream.stop_stream()
                stream.close()
                p.terminate()
                
                # Combine audio frames into WAV format
                wav_data = create_wav_data(frames, sample_rate=RATE, channels=CHANNELS, sample_width=2)
                print(f"📊 Recorded {len(wav_data)} bytes of audio (with WAV header)")
                
                # Send to Deepgram STT (Flux model)
                print("\n📤 Sending audio to Deepgram Flux STT...")
                
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
                    data=wav_data
                )
                
                if response.status_code != 200:
                    print(f"API Response: {response.text}")
                response.raise_for_status()
                result = response.json()
                
                # Extract transcription
                try:
                    transcript = result["results"]["channels"][0]["alternatives"][0]["transcript"]
                except (KeyError, IndexError):
                    print(f"Could not extract transcript from: {result}")
                    transcript = ""
                
                if not transcript.strip():
                    print("❌ No speech detected or empty transcription")
                    continue
                
                print(f"\n💬 Transcription:\n>>> {transcript}\n")
                
                # Let user filter/confirm the text
                print("Do you want to send this to Claude? (Press Enter to confirm, or type new text)")
                user_input = input("> ").strip()
                
                if user_input:
                    transcript = user_input
                
                if not transcript.strip():
                    print("❌ Empty transcript, skipping")
                    continue
                
                print(f"✏️  Using: {transcript}")
                
                # Log to chatlog
                with open("chatlog.txt", 'a') as chatlog:
                    chatlog.write(f"\n[{datetime.now().isoformat()}] Conversation #{conversation_count}\n")
                    chatlog.write(f"User: {transcript}\n")
                
                # Send to Claude API
                print("\n🧠 Sending to Claude...")
                message = claude.messages.create(
                    model="claude-opus-4-1-20250805",
                    max_tokens=1024,
                    messages=[
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ]
                )
                
                response_text = message.content[0].text
                print(f"\n🤖 Claude Response:\n{response_text}\n")
                
                # Log response
                with open("chatlog.txt", 'a') as chatlog:
                    chatlog.write(f"Claude: {response_text}\n")
                
                # Generate and play Cartesia TTS
                print("⏳ Preparing text-to-speech...")
                play_cartesia_tts(response_text, voice_id=voice_id)
                
                print("\n✓ Ready for next input")
                
            except KeyboardInterrupt:
                print("\n\n⏹️  Interrupted by user")
                break
            except Exception as e:
                print(f"❌ Error in conversation: {str(e)}")
                import traceback
                traceback.print_exc()
                print("Continuing to next conversation...\n")
                continue
        
        print("\n✓ Exited. Check chatlog.txt for conversation history.")
        
    except Exception as e:
        print(f"❌ Fatal Error: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
#     byte_rate = sample_rate * channels * (bits_per_sample // 8)
#     block_align = channels * (bits_per_sample // 8)
#     header = bytearray(44)
#     header[0:4] = b'RIFF'
#     header[4:8] = b'\x00\x00\x00\x00'
#     header[8:12] = b'WAVE'
#     header[12:16] = b'fmt '
#     header[16:20] = b'\x10\x00\x00\x00'
#     header[20:22] = b'\x01\x00'
#     header[22:24] = channels.to_bytes(2, 'little')
#     header[24:28] = sample_rate.to_bytes(4, 'little')
#     header[28:32] = byte_rate.to_bytes(4, 'little')
#     header[32:34] = block_align.to_bytes(2, 'little')
#     header[34:36] = bits_per_sample.to_bytes(2, 'little')
#     header[36:40] = b'data'
#     header[40:44] = b'\x00\x00\x00\x00'
#     return header


# def main():
#     try:
#         api_key = os.getenv("DEEPGRAM_API_KEY")
#         if not api_key:
#             raise ValueError("DEEPGRAM_API_KEY not set")
#         print("API Key found")

#         client = DeepgramClient(api_key=api_key)

#         with client.agent.v1.connect() as connection:
#             print("🔌 WebSocket connection established")

#             # --- Configure with Claude as the LLM ---
#             settings = AgentV1Settings(
#                 audio=AgentV1SettingsAudio(
#                     input=AgentV1SettingsAudioInput(
#                         encoding="linear16",
#                         sample_rate=24000,
#                     ),
#                     output=AgentV1SettingsAudioOutput(
#                         encoding="linear16",
#                         sample_rate=24000,
#                         container="wav",
#                     ),
#                 ),
#                 agent=AgentV1SettingsAgent(
#                     language="en",
#                     listen=AgentV1SettingsAgentListen(
#                         provider=AgentV1SettingsAgentListenProvider_V1(
#                             type="deepgram",
#                             model="nova-3",  # Deepgram STT
#                         )
#                     ),
#                     think=ThinkSettingsV1(
#                         provider=ThinkSettingsV1Provider_Anthropic(
#                             type=CLAUDE_PROVIDER_TYPE,
#                             model="claude-haiku-4-5",  # ← Claude is the brain!
#                         ),
#                         prompt=(
#                             "You are a helpful voice assistant powered by Claude. "
#                             "Keep responses concise and conversational — you are speaking out loud, not writing."
#                         ),
#                     ),
#                     speak=SpeakSettingsV1(
#                         provider=SpeakSettingsV1Provider_Deepgram(
#                             type="deepgram",
#                             model="aura-2-asteria-en",  # Deepgram TTS voice
#                         )
#                     ),
#                     greeting="Hello! I'm Claude, your voice assistant. How can I help you today?",
#                 ),
#             )

#             # --- Event handlers ---
#             audio_buffer = bytearray()
#             file_counter = [0]
#             processing_complete = [False]

#             def on_open(event):
#                 print("📡 Connection opened")

#             def on_message(message):
#                 if isinstance(message, bytes):
#                     audio_buffer.extend(message)
#                     return

#                 msg_type = getattr(message, "type", "Unknown")

#                 if msg_type == "Welcome":
#                     print(" Welcome received — connection ready")
#                 elif msg_type == "SettingsApplied":
#                     print(" Settings applied — agent configured with Claude")
#                 elif msg_type == "ConversationText":
#                     role = getattr(message, 'role', 'unknown')
#                     content = getattr(message, 'content', str(message))
#                     print(f" [{role.upper()}]: {content}")
#                     with open("chatlog.txt", 'a') as f:
#                         f.write(f"{json.dumps(message.__dict__)}\n")
#                 elif msg_type == "UserStartedSpeaking":
#                     print("🎙️  User is speaking...")
#                 elif msg_type == "AgentThinking":
#                     print("🧠 Claude is thinking...")
#                 elif msg_type == "AgentStartedSpeaking":
#                     audio_buffer.clear()
#                     print("🔊 Agent speaking...")
#                 elif msg_type == "AgentAudioDone":
#                     if len(audio_buffer) > 0:
#                         fname = f"output-{file_counter[0]}.wav"
#                         with open(fname, 'wb') as f:
#                             f.write(create_wav_header())
#                             f.write(audio_buffer)
#                         print(f" Saved: {fname}")
#                         audio_buffer.clear()
#                         file_counter[0] += 1
#                     processing_complete[0] = True
#                 elif msg_type not in ("Unknown",):
#                     print(f" Event: {msg_type}")

#             def on_error(error):
#                 print(f" Error: {error}")

#             def on_close(event):
#                 print("🔒 Connection closed")

#             connection.on(EventType.OPEN, on_open)
#             connection.on(EventType.MESSAGE, on_message)
#             connection.on(EventType.ERROR, on_error)
#             connection.on(EventType.CLOSE, on_close)

#             # Send config
#             connection.send_settings(settings)
#             print("📤 Settings sent")

#             # Start listener thread
#             listener_thread = threading.Thread(target=connection.start_listening, daemon=True)
#             listener_thread.start()
#             time.sleep(1)

#             # Stream a sample audio file (spacewalk.wav — someone talking)
#             print("📥 Streaming audio to agent...")
#             response = requests.get("https://dpgr.am/spacewalk.wav", stream=True)
#             header = response.raw.read(44)

#             if header[0:4] != b'RIFF' or header[8:12] != b'WAVE':
#                 print(" Invalid WAV file")
#                 return

#             for chunk in response.iter_content(chunk_size=8192):
#                 if chunk:
#                     connection.send_media(chunk)
#                     time.sleep(0.05)

#             print(" Audio sent — waiting for Claude's response...")

#             # Wait up to 30s for response
#             start = time.time()
#             while not processing_complete[0] and (time.time() - start) < 30:
#                 time.sleep(1)
#                 print(f"  ⏳ {int(time.time() - start)}s...")

#             if processing_complete[0]:
#                 print("\n Done! Check output-0.wav to hear Claude's spoken response.")
#                 print("Conversation saved to chatlog.txt")
#             else:
#                 print(" Timed out waiting for response")

#     except Exception as e:
#         print(f" Error: {e}")
#         raise


# if __name__ == "__main__":
#     main()
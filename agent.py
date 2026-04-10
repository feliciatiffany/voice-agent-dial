# Deepgram Flux STT → Python → Claude LLM workflow

import os
import json
import time
import pyaudio
import requests
from datetime import datetime
from deepgram import DeepgramClient
from anthropic import Anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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

def main():
    try:
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
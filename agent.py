# Deepgram Voice Agent with Claude as the LLM brain

# For more Python SDK migration guides, visit:
# https://github.com/deepgram/deepgram-python-sdk/tree/main/docs

# Import dependencies and set up the main function
import requests
import wave
import io
import time
import os
import json
import threading
from datetime import datetime

from deepgram import DeepgramClient
from deepgram.agent.v1.types import (
    AgentV1Settings,
    AgentV1SettingsAgent,
    AgentV1SettingsAudio,
    AgentV1SettingsAudioInput,
    AgentV1SettingsAudioOutput,
    AgentV1SettingsAgentListen,
    AgentV1SettingsAgentListenProvider_V1,
    AgentV1SettingsAgentSpeakEndpoint,
)
from deepgram.types.think_settings_v1 import ThinkSettingsV1

# For more Python SDK migration guides, visit:
# https://github.com/deepgram/deepgram-python-sdk/tree/main/docs

def main():
  try:
      # Initialize the Voice Agent
      api_key = os.getenv("DEEPGRAM_API_KEY")
      if not api_key:
          raise ValueError("DEEPGRAM_API_KEY environment variable is not set")
      print("API Key found")

      # Initialize Deepgram client
      client = DeepgramClient(api_key=api_key)

      # Use connection as a context manager
      with client.agent.v1.connect() as connection:
          print("Created WebSocket connection...")

          # The code in the following steps will go here

          # Configure the Agent
          settings = AgentV1Settings(
              type="Settings",
              audio=AgentV1SettingsAudio(
                  input=AgentV1SettingsAudioInput(
                      encoding="linear16",
                      sample_rate=24000,
                  ),
                  output=AgentV1SettingsAudioOutput(
                      encoding="linear16",
                      sample_rate=24000,
                      container="wav",
                  ),
              ),
              agent=AgentV1SettingsAgent(
                  language="en",
                  listen=AgentV1SettingsAgentListen(
                      provider=AgentV1SettingsAgentListenProvider_V1(
                          type="deepgram",
                          model="nova-3",
                      )
                  ),
                  think=ThinkSettingsV1(
                      provider={"type": "open_ai", "model": "gpt-4o-mini"},
                      prompt="You are a friendly AI assistant.",
                  ),
                    speak=AgentV1SettingsAgentSpeakEndpoint(
                        provider={"type": "deepgram", "model": "aura-2-thalia-en"},
                    ),
                  greeting="Hello! How can I help you today?",
              ),
          )

          # Send settings to configure the agent
          print("Sending settings configuration...")
          connection.send_settings(settings)
          print("Settings sent successfully")

          # Setup Event Handlers
          audio_buffer = bytearray()
          file_counter = [0]
          processing_complete = [False]

          def on_open(event):
              print("Connection opened")

          def on_message(message):
              # Handle binary audio data
              if isinstance(message, bytes):
                  audio_buffer.extend(message)
                  print(f"Received audio data: {len(message)} bytes")
                  return

              # Handle different message types
              msg_type = getattr(message, "type", "Unknown")
              print(f"Received {msg_type} event")

              # Handle specific event types
              if msg_type == "Welcome":
                  print(f"Welcome: {message}")
                  with open("chatlog.txt", 'a') as chatlog:
                      chatlog.write(f"Welcome: {message}\n")

              elif msg_type == "SettingsApplied":
                  print(f"Settings applied: {message}")
                  with open("chatlog.txt", 'a') as chatlog:
                      chatlog.write(f"Settings applied: {message}\n")

              elif msg_type == "ConversationText":
                  print(f"Conversation: {message}")
                  with open("chatlog.txt", 'a') as chatlog:
                      chatlog.write(f"{json.dumps(message.__dict__)}\n")

              elif msg_type == "UserStartedSpeaking":
                  print(f"User started speaking")
                  with open("chatlog.txt", 'a') as chatlog:
                      chatlog.write(f"User started speaking\n")

              elif msg_type == "AgentThinking":
                  print(f"Agent thinking")
                  with open("chatlog.txt", 'a') as chatlog:
                      chatlog.write(f"Agent thinking\n")

              elif msg_type == "AgentStartedSpeaking":
                  audio_buffer.clear()  # Reset buffer for new response
                  print(f"Agent started speaking")
                  with open("chatlog.txt", 'a') as chatlog:
                      chatlog.write(f"Agent started speaking\n")

              elif msg_type == "AgentAudioDone":
                  print(f"Agent audio done")
                  if len(audio_buffer) > 0:
                      with open(f"output-{file_counter[0]}.wav", 'wb') as f:
                          f.write(create_wav_header())
                          f.write(audio_buffer)
                      print(f"Created output-{file_counter[0]}.wav")
                  audio_buffer.clear()
                  file_counter[0] += 1
                  processing_complete[0] = True

          def on_error(error):
              print(f"Error: {error}")
              with open("chatlog.txt", 'a') as chatlog:
                  chatlog.write(f"Error: {error}\n")

          def on_close(event):
              print(f"Connection closed")
              with open("chatlog.txt", 'a') as chatlog:
                  chatlog.write(f"Connection closed\n")

          # Register event handlers
          connection.on("open", on_open)
          connection.on("message", on_message)
          connection.on("error", on_error)
          connection.on("close", on_close)
          print("Event handlers registered")

          # Start listening for events in a background thread
          print("Starting event listener...")
          listener_thread = threading.Thread(target=connection.start_listening, daemon=True)
          listener_thread.start()

          # Wait a moment for connection to establish
          time.sleep(1)

          # Stream audio
          print("Downloading and sending audio...")
          response = requests.get("https://dpgr.am/spacewalk.wav", stream=True)
          # Skip WAV header
          header = response.raw.read(44)

          # Verify WAV header
          if header[0:4] != b'RIFF' or header[8:12] != b'WAVE':
              print("Invalid WAV header")
              return

          chunk_size = 8192
          total_bytes_sent = 0
          chunk_count = 0
          for chunk in response.iter_content(chunk_size=chunk_size):
              if chunk:
                  print(f"Sending chunk {chunk_count}: {len(chunk)} bytes")
                  connection.send_media(chunk)
                  total_bytes_sent += len(chunk)
                  chunk_count += 1
                  time.sleep(0.1)  # Small delay between chunks

          print(f"Total audio data sent: {total_bytes_sent} bytes in {chunk_count} chunks")
          print("Waiting for agent response...")

          # Wait for processing
          print("Waiting for processing to complete...")
          start_time = time.time()
          timeout = 30  # 30 second timeout

          while not processing_complete[0] and (time.time() - start_time) < timeout:
              time.sleep(1)
              print(f"Still waiting for agent response... ({int(time.time() - start_time)}s elapsed)")

          if not processing_complete[0]:
              print("Processing timed out after 30 seconds")
          else:
              print("Processing complete. Check output-*.wav and chatlog.txt for results.")

          print("Finished")

  except Exception as e:
      print(f"Error: {str(e)}")

# WAV Header Functions
def create_wav_header(sample_rate=24000, bits_per_sample=16, channels=1):
  """Create a WAV header with the specified parameters"""
  byte_rate = sample_rate * channels * (bits_per_sample // 8)
  block_align = channels * (bits_per_sample // 8)

  header = bytearray(44)
  # RIFF header
  header[0:4] = b'RIFF'
  header[4:8] = b'\x00\x00\x00\x00'  # File size (to be updated later)
  header[8:12] = b'WAVE'
  # fmt chunk
  header[12:16] = b'fmt '
  header[16:20] = b'\x10\x00\x00\x00'  # Subchunk1Size (16 for PCM)
  header[20:22] = b'\x01\x00'  # AudioFormat (1 for PCM)
  header[22:24] = channels.to_bytes(2, 'little')  # NumChannels
  header[24:28] = sample_rate.to_bytes(4, 'little')  # SampleRate
  header[28:32] = byte_rate.to_bytes(4, 'little')  # ByteRate
  header[32:34] = block_align.to_bytes(2, 'little')  # BlockAlign
  header[34:36] = bits_per_sample.to_bytes(2, 'little')  # BitsPerSample
  # data chunk
  header[36:40] = b'data'
  header[40:44] = b'\x00\x00\x00\x00'  # Subchunk2Size (to be updated later)

  return header

if __name__ == "__main__":
  main()

# # Claude-specific provider (uses Anthropic's API under the hood via Deepgram's managed integration)
# # ThinkSettingsV1Provider for Anthropic/Claude
# try:
#     from deepgram.types.think_settings_v1provider import ThinkSettingsV1Provider_Anthropic
#     CLAUDE_PROVIDER_TYPE = "anthropic"
# except ImportError:
#     # Fallback: use custom endpoint to proxy Claude via OpenAI-compatible wrapper
#     from deepgram.types.think_settings_v1provider import ThinkSettingsV1Provider_OpenAi as ThinkSettingsV1Provider_Anthropic
#     CLAUDE_PROVIDER_TYPE = "open_ai"
#     print("⚠️  Anthropic provider not found in SDK — falling back to open_ai-compatible mode")


# def create_wav_header(sample_rate=24000, bits_per_sample=16, channels=1):
#     """Create a WAV header."""
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
import os
import sys
import unicodedata

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Fix import path for venv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.venv', 'lib', 'python3.14', 'site-packages'))

try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    # Try without sys.path modification
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        print("⚠️  elevenlabs package not installed. Run: .venv/bin/pip install elevenlabs")
        exit(1)

VOICE_MAP = {
    ("A", "neutral"): "NDTYOmYEjbDIVCKB35i3",
    ("B", "neutral"): "WIi4Wzyjc860r5dZ3gjK",
    ("A", "angry"): "FCdKzv68Ofr4VUDcZXIy",
    ("B", "angry"): "z2P4oCxSHhXan3ew4COv",
}


def sanitize_text(text):
    """Clean text for API compatibility."""
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('utf-8', 'replace').decode('utf-8', 'replace')
    replacements = {
        '"': '"', '"': '"', '"': '"',
        ''': "'", ''': "'",
        '–': '-', '—': '-', '…': '...',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = ''.join(c for c in text if 32 <= ord(c) <= 126 or c in '\n\t')
    return ' '.join(text.split())


def play_elevenlabs_tts(text, voice_id=None):
    """Generate and play audio using ElevenLabs TTS SDK."""
    try:
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        if not elevenlabs_api_key:
            print("⚠️  ELEVENLABS_API_KEY not set, skipping TTS")
            return

        if not voice_id:
            print("⚠️  No voice ID provided, skipping TTS")
            return

        text = sanitize_text(text)
        
        # Ensure voice_id is ASCII
        voice_id = ''.join(c for c in voice_id if ord(c) < 128)
        
        if not text or len(text) < 2:
            print("❌ No valid text to speak")
            return

        print(f"\n🎵 Generating speech with ElevenLabs...")
        print(f"   Text: {text[:80]}...")

        client = ElevenLabs(api_key=elevenlabs_api_key)

        audio = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id="eleven_v3",
            output_format="mp3_44100_128",
        )

        audio_data = b"".join(audio)

        if not audio_data:
            print("❌ No audio generated")
            return

        print(f"🔊 Audio generated ({len(audio_data)} bytes)")

        with open("elevenlabs_output.mp3", "wb") as f:
            f.write(audio_data)
        print("✓ Saved to elevenlabs_output.mp3")

    except Exception as e:
        print(f"❌ TTS Error: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
    if not elevenlabs_api_key:
        print("⚠️  ELEVENLABS_API_KEY not set")
        return

    print("ElevenLabs TTS Test")
    print("=" * 40)

    print("Select character: A or B")
    character = input("> ").strip().upper()
    while character not in ("A", "B"):
        print("Please enter A or B")
        character = input("> ").strip().upper()

    print("Select emotion: neutral or angry")
    emotion = input("> ").strip().lower()
    while emotion not in ("neutral", "angry"):
        print("Please enter neutral or angry")
        emotion = input("> ").strip().lower()

    voice_id = VOICE_MAP.get((character, emotion))
    print(f"✓ Selected: Character {character}, Emotion {emotion}")
    print(f"   Voice ID: {voice_id}\n")

    test_text = input("Enter text to speak: ")
    play_elevenlabs_tts(test_text, voice_id=voice_id)


if __name__ == "__main__":
    main()
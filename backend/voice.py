# Audio pipeline
# VAD via Silero + STT via faster-whisper + TTS via piper

import torch
import torchaudio
from faster_whisper import WhisperModel
from silero_vad import VAD
from piper_tts import PiperTTS


class VoicePipeline:

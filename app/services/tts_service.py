
import subprocess
from pathlib import Path


class TTSService:
    def __init__(self, voice="en-us", speed=160, profile_id=None):
        self.profile_id = profile_id
        self.voice = voice
        self.speed = speed

    def synthesize(self, text, output_path):
        output_path = Path(output_path)
        output_path.parent.mkdir(
            parents=True, exist_ok=True
        )

        if not text.strip():
            raise ValueError(
                "Narration cannot be empty"
            )

        if self.profile_id:
            from app.services.speech_profiles import synthesize
            return synthesize(self.profile_id,text,output_path)

        command = [
            "espeak-ng",
            "-v", self.voice,
            "-s", str(self.speed),
            "-w", str(output_path),
            text
        ]

        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True
        )

        if not output_path.exists():
            raise RuntimeError(
                "TTS failed to generate audio"
            )

        return output_path

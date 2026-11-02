
import subprocess
from pathlib import Path


class TTSService:
    def __init__(self, voice="en-us", speed=160):
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

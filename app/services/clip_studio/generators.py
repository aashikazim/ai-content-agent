"""Video generator interface and explicit CPU mock implementation."""
from abc import ABC, abstractmethod
from pathlib import Path

from app.services.cloud_video_renderer import run, validate_clip


class VideoGenerator(ABC):
    @abstractmethod
    def generate(self, clip, settings, seed, directory, *, references=None):
        """Consume verified source records and return media without overwriting versions."""


class MockGenerator(VideoGenerator):
    def generate(self, clip, settings, seed, directory, *, references=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        width, height = {'16:9': (512, 288), '9:16': (288, 512), '1:1': (384, 384)}[settings.aspect_ratio]
        output = directory/'clip.mp4'
        # Only fixed text and validated integers enter filters. Instructions are data.
        filters = f'hue=h={seed % 360},drawbox=x=0:y=0:w=iw:h=40:color=black@0.7:t=fill,drawtext=text=MOCK CLIP:fontsize=22:fontcolor=white:x=12:y=10'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
             f'testsrc2=size={width}x{height}:rate=24', '-f', 'lavfi', '-i',
             f'sine=frequency={220+seed%440}:sample_rate=48000', '-t', str(clip.duration_seconds),
             '-vf', filters, '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-ac', '2', '-movflags', '+faststart', str(output)])
        thumbnail = directory/'thumbnail.jpg'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(output), '-frames:v', '1', str(thumbnail)])
        output.chmod(0o600)
        thumbnail.chmod(0o600)
        return output, thumbnail, validate_clip(output, clip.duration_seconds, require_audio=True,
                                               expected_dimensions=(width, height), expected_fps=24)

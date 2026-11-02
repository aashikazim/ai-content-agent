
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class VideoRenderer:
    def __init__(
        self,
        width=1080,
        height=1920,
        fps=30
    ):
        self.width = width
        self.height = height
        self.fps = fps

    def run_command(self, command):
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True
        )

    def audio_duration(self, audio_path):
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries",
                "format=duration",
                "-of", "json",
                str(audio_path)
            ],
            check=True,
            capture_output=True,
            text=True
        )

        return float(
            json.loads(result.stdout)["format"]["duration"]
        )

    def prepare_image(
        self, image_path, caption, output_path
    ):
        image = Image.open(image_path).convert("RGB")

        target_ratio = self.width / self.height
        ratio = image.width / image.height

        if ratio > target_ratio:
            crop_width = int(image.height * target_ratio)
            left = (image.width - crop_width) // 2
            image = image.crop((
                left, 0,
                left + crop_width, image.height
            ))
        else:
            crop_height = int(image.width / target_ratio)
            top = (image.height - crop_height) // 2
            image = image.crop((
                0, top,
                image.width, top + crop_height
            ))

        image = image.resize(
            (self.width, self.height),
            Image.Resampling.LANCZOS
        )

        if caption:
            draw = ImageDraw.Draw(image)

            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/"
                "DejaVuSans-Bold.ttf",
                48
            )

            import textwrap

            lines = textwrap.wrap(caption, width=30)
            text = "\n".join(lines[:4])

            bbox = draw.multiline_textbbox(
                (0, 0), text,
                font=font, spacing=12,
                align="center"
            )

            text_height = bbox[3] - bbox[1]
            y = int(self.height * 0.75)

            draw.rectangle(
                (
                    30, y - 30,
                    self.width - 30,
                    y + text_height + 40
                ),
                fill=(0, 0, 0)
            )

            draw.multiline_text(
                (self.width // 2, y),
                text,
                font=font,
                fill="white",
                anchor="ma",
                spacing=12,
                align="center"
            )

        output_path = Path(output_path)
        image.save(output_path)
        return output_path

 
    def render_scene(
        self,
        image_path,
        audio_path,
        duration,
        output_path
    ):
        narration_duration = self.audio_duration(audio_path)

        # Calculate the speed needed to fit narration
        speed_factor = narration_duration / duration

        # Allow up to 25% faster narration
        max_speed = 1.25

        if speed_factor > max_speed:
            raise ValueError(
                f"Narration is {narration_duration:.1f}s "
                f"but scene allows {duration}s. "
                f"Required speed: {speed_factor:.2f}x "
                f"(maximum allowed: {max_speed}x). "
                "Regenerate a shorter script."
            )

        # Speed up narration only when necessary
        if speed_factor > 1.0:
            audio_filter = (
                f"atempo={speed_factor:.6f},apad"
            )
            print(
                f"  Adjusting narration speed: "
                f"{speed_factor:.2f}x"
            )
        else:
            audio_filter = "apad"

        frames = round(duration * self.fps)

        command = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-framerate", str(self.fps),
            "-i", str(image_path),
            "-i", str(audio_path),
            "-vf",
            (
                f"scale={self.width}:{self.height},"
                "format=yuv420p,"
                "fade=t=in:st=0:d=0.4"
            ),
            "-af", audio_filter,
            "-frames:v", str(frames),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", "48000",
            "-ac", "2",
            "-b:a", "128k",
            "-t", str(duration),
            "-movflags", "+faststart",
            str(output_path)
        ]

        self.run_command(command)

        return Path(output_path)

    def concatenate(self, scene_paths, output_path):
        output_path = Path(output_path)
        list_file = output_path.parent / "concat.txt"

        with list_file.open("w", encoding="utf-8") as f:
            for path in scene_paths:
                # Scene files use fixed generated names.
                f.write(f"file '{Path(path).name}'\n")

        self.run_command([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path)
        ])

        return output_path

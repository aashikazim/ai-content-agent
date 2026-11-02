
import json
from pathlib import Path

from app.models.content_plan import ContentPlan
from app.services.image_service import ImageService
from app.services.tts_service import TTSService
from app.services.video_renderer import VideoRenderer


class VideoGenerationAgent:
    def __init__(
        self,
        image_service=None,
        tts_service=None,
        renderer=None
    ):
        self.image_service = (
            image_service or ImageService()
        )
        self.tts_service = (
            tts_service or TTSService()
        )
        self.renderer = (
            renderer or VideoRenderer()
        )

    def generate(self, plan, output_dir):
        if not isinstance(plan, ContentPlan):
            plan = ContentPlan.model_validate(plan)

        output_dir = Path(output_dir)
        output_dir.mkdir(
            parents=True, exist_ok=True
        )

        scene_paths = []
        manifest = {
            "title": plan.title,
            "topic": plan.topic,
            "status": "rendering",
            "scenes": []
        }

        for scene in plan.scenes:
            index = scene.scene_number

            image_path = (
                output_dir / f"scene_{index}.png"
            )
            prepared_path = (
                output_dir / f"scene_{index}_caption.png"
            )
            audio_path = (
                output_dir / f"scene_{index}.wav"
            )
            video_path = (
                output_dir / f"scene_{index}.mp4"
            )

            print(f"Generating scene {index}...")

            self.image_service.generate(
                scene.visual_prompt,
                image_path,
                seed=42 + index
            )

            self.tts_service.synthesize(
                scene.narration,
                audio_path
            )

            self.renderer.prepare_image(
                image_path,
                scene.on_screen_text,
                prepared_path
            )

            self.renderer.render_scene(
                prepared_path,
                audio_path,
                scene.duration_seconds,
                video_path
            )

            scene_paths.append(video_path)

            manifest["scenes"].append({
                "scene_number": index,
                "duration": scene.duration_seconds,
                "image": str(image_path),
                "audio": str(audio_path),
                "video": str(video_path)
            })

        final_path = output_dir / "final_video.mp4"

        self.renderer.concatenate(
            scene_paths, final_path
        )

        manifest["status"] = "rendered"
        manifest["output"] = str(final_path)
        manifest["review_status"] = "draft"

        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8"
        )

        return final_path

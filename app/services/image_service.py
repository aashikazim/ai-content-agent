
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class ImageService:
    def __init__(
        self, mode="diffusion", model_id=None,
        width=512, height=768, visual_style="cinematic"
    ):
        if mode not in {"text", "diffusion"}:
            raise ValueError("Unknown image mode")
        self.mode = mode
        self.model_id = model_id
        self.width = width
        self.height = height
        self.visual_style = visual_style
        self.pipe = None

    def load_model(self):
        if self.mode != "diffusion":
            return

        if self.pipe is not None:
            return

        import torch
        from diffusers import StableDiffusionPipeline

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is required for diffusion mode. "
                "Use --image-mode text for a fallback."
            )

        model_id = self.model_id or os.getenv(
            "IMAGE_MODEL_ID",
            "stable-diffusion-v1-5/"
            "stable-diffusion-v1-5"
        )

        self.pipe = StableDiffusionPipeline.from_pretrained(
            model_id,
            torch_dtype=torch.float16,
            safety_checker=None,
            requires_safety_checker=False
        )

        self.pipe = self.pipe.to("cuda")
        self.pipe.enable_attention_slicing()

    def generate(
        self, prompt, output_path, seed=42
    ):
        output_path = Path(output_path)
        output_path.parent.mkdir(
            parents=True, exist_ok=True
        )

        if self.mode == "text":
            return self.generate_text_card(
                prompt, output_path
            )

        import torch

        self.load_model()

        generator = torch.Generator(
            device="cuda"
        ).manual_seed(seed)

        image = self.pipe(
            prompt=(
                f"Original {self.visual_style.replace('_', ' ')} illustration, "
                + ("landscape composition, " if self.width > self.height else
                   "square composition, " if self.width == self.height else "portrait composition, ")
                +
                "high detail, no text, "
                + prompt
            ),
            negative_prompt=(
                "watermark, logo, text, "
                "blurry, distorted"
            ),
            width=self.width,
            height=self.height,
            num_inference_steps=int(
                os.getenv("IMAGE_STEPS", "20")
            ),
            generator=generator
        ).images[0]

        image.save(output_path)
        return output_path

    def generate_text_card(
        self, prompt, output_path
    ):
        image = Image.new(
            "RGB", (self.width, self.height), (20, 30, 48)
        )

        draw = ImageDraw.Draw(image)

        font_path = (
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf"
        )

        font = ImageFont.truetype(
            font_path, 26
        )

        import textwrap

        lines = textwrap.wrap(prompt, width=max(15, (self.width - 70) // 16))
        text = "\n".join(lines[:12])

        draw.multiline_text(
            (35, min(200, self.height // 4)),
            text,
            fill="white",
            font=font,
            spacing=12
        )

        image.save(output_path)
        return output_path

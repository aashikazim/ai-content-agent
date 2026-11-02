
from unittest.mock import patch
from app.services.video_renderer import VideoRenderer


def test_narration_auto_speed_adjustment(tmp_path):
    renderer = VideoRenderer(
        width=360,
        height=640,
        fps=15
    )

    output = tmp_path / "scene.mp4"

    with patch.object(
        renderer,
        "audio_duration",
        return_value=6.3
    ), patch.object(
        renderer,
        "run_command"
    ) as mock_run:

        renderer.render_scene(
            image_path="image.png",
            audio_path="audio.wav",
            duration=6,
            output_path=output
        )

        command = mock_run.call_args.args[0]

        audio_filter = command[
            command.index("-af") + 1
        ]

        assert "atempo=1.050000" in audio_filter


def test_excessively_long_narration(tmp_path):
    import pytest

    renderer = VideoRenderer()

    with patch.object(
        renderer,
        "audio_duration",
        return_value=10.0
    ):
        with pytest.raises(ValueError):
            renderer.render_scene(
                image_path="image.png",
                audio_path="audio.wav",
                duration=6,
                output_path=tmp_path / "scene.mp4"
            )

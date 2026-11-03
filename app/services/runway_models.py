"""Explicit Runway API model contracts, verified 2026-11-03.

No model routers or account/price discovery during catalogue reads. Prices are
standard credits (USD .01 each), not promotional rates or invoice guarantees.
https://docs.dev.runwayml.com/openapi.json
https://docs.dev.runwayml.com/guides/pricing/
"""
from decimal import Decimal, ROUND_CEILING

SOURCE = 'https://docs.dev.runwayml.com/guides/pricing/'


def model(label, low, high, silent, audio=None, *, inputs=('text', 'image'), native='720p', references=False, **extra):
    return dict(label=label, clip_duration_seconds={'min': low, 'max': high},
                input_modes=list(inputs), credits_per_second=silent,
                audio_credits_per_second=audio, audio=audio is not None,
                native_resolution=native, character_references=references, **extra)


MODELS = {
    'gen4_turbo': model('Gen-4 Turbo', 2, 10, 5, inputs=('image',)),
    'gen4.5': model('Gen-4.5', 2, 10, 12),
    'kling2.5_turbo_pro': model('Kling 2.5 Turbo Pro', 5, 10, 12, native='1080p', durations=[5, 10]),
    'kling3.0_standard': model('Kling 3.0 Standard', 3, 15, 9, 13),
    'kling3.0_pro': model('Kling 3.0 Pro', 3, 15, 12, 17, native='1080p'),
    'kling3.0_4k': model('Kling 3.0 4K', 3, 15, 43, 43, native='4k'),
    'klingO3_standard': model('Kling O3 Standard', 3, 15, 13, 13, references=True),
    'klingO3_pro': model('Kling O3 Pro', 3, 15, 17, 17, native='1080p', references=True),
    'klingO3_4k': model('Kling O3 4K', 3, 15, 43, 43, native='4k', references=True),
    'kling_4': model('Kling 4', 3, 30, '15.7', '15.7', native='selected', references=True, always_audio=True),
}


def capabilities(config):
    try:
        return MODELS[config.video_model]
    except KeyError:
        raise ValueError('Unknown Runway model; select a model from the catalogue') from None


def validate(config, duration=None):
    info = capabilities(config)
    if config.video_input_mode not in info['input_modes']:
        raise ValueError(f"{info['label']} requires image-to-video with a selected start frame")
    if config.video_input_mode == 'text' and (config.start_image_reference_id or config.shot_image_reference_ids):
        raise ValueError('Select image-to-video or remove unused start frames')
    if config.video_model == 'gen4.5' and config.video_input_mode == 'text' and config.aspect_ratio == '1:1':
        raise ValueError('Gen-4.5 text-to-video supports portrait or landscape; square needs image-to-video')
    if config.narration == 'model_audio' and not info['audio']:
        raise ValueError(f"{info['label']} has no generated audio; choose local TTS or an audio-capable Kling model")
    if config.character_reference_ids and not (info['character_references'] and
            (config.video_input_mode == 'image' or config.video_model == 'kling_4')):
        raise ValueError('This Runway mode does not accept independent character images; use a composed start frame or Kling O3 image-to-video / Kling 4')
    if duration is not None:
        limits = info['clip_duration_seconds']
        if type(duration) is not int or not limits['min'] <= duration <= limits['max'] or (info.get('durations') and duration not in info['durations']):
            raise ValueError(f"Shot duration is unsupported by {info['label']}; revise the script using its model limits")


def estimate(config, duration):
    validate(config, duration)
    info = capabilities(config)
    rate = info['audio_credits_per_second'] if config.narration == 'model_audio' else info['credits_per_second']
    if config.video_model == 'kling_4' and config.resolution == '1080p':
        rate = '23.5'
    credits = (Decimal(str(rate)) * duration).to_integral_value(rounding=ROUND_CEILING)
    return credits * Decimal('.01')


def ratio(config):
    key, aspect = config.video_model, config.aspect_ratio
    if key == 'kling_4' and config.video_input_mode == 'image':
        return 'auto_' + config.resolution
    if key == 'kling_4':
        values = ('1920:1080', '1080:1920', '1440:1440') if config.resolution == '1080p' else ('1280:720', '720:1280', '940:940')
    elif MODELS[key]['native_resolution'] == '4k':
        values = ('3840:2160', '2160:3840', '2880:2880')
    elif MODELS[key]['native_resolution'] == '1080p':
        values = ('1920:1080', '1080:1920', '1440:1440')
    else:
        values = ('1280:720', '720:1280', '960:960')
    return values[{'16:9': 0, '9:16': 1, '1:1': 2}[aspect]]


def catalog_models():
    return [dict(info, id=key, implemented=True, status='account_access_unverified',
                 account_access='unverified', verified_on='2026-11-03', source_url=SOURCE,
                 aspect_ratios=['9:16', '16:9', '1:1'], resolutions=['720p', '1080p'],
                 fps=[24, 30], normalizes_fps=True, operations=['generate'],
                 resolution_note='Local FFmpeg normalizes output; 720p models are upscaled for 1080p. 4K models incur 4K cost even for smaller final output.')
            for key, info in MODELS.items()]

"""Pure chapter layout/merge helpers; never submit model requests here."""
from math import ceil

from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan


def chapter_configs(config):
    if config.video_provider != 'local_cinematic' or config.duration_seconds <= 240:
        return [config]
    count = ceil(config.duration_seconds / 180)
    seconds, extra = divmod(config.duration_seconds, count)
    shots, more = divmod(config.max_shots, count)
    return [GenerationConfig.model_validate(config.model_copy(update={
        'duration_seconds': seconds + (index < extra), 'max_shots': shots + (index < more),
        'shot_image_reference_ids': {}, 'scene_image_reference_ids': {}, 'shot_driving_video_ids': {},
        'scene_character_reference_ids': {},
    })) for index in range(count)]


def merge_chapters(parts, config):
    data = parts[0].model_dump(mode='json')
    data['content_plan']['scenes'] = []
    data['outline'], data['dialogue'], data['shots'] = [], [], []
    scene_offset = shot_offset = 0
    cast = {actor.character_id:actor.model_dump() for actor in parts[0].characters}
    for index, part in enumerate(parts):
        if {actor.character_id:actor.model_dump() for actor in part.characters} != cast:
            raise ValueError('A chapter changed the shared cast bible')
        content = part.model_dump(mode='json')
        for scene in content['content_plan']['scenes']:
            scene['scene_number'] += scene_offset
            data['content_plan']['scenes'].append(scene)
        for shot in content['shots']:
            shot['shot_number'] += shot_offset
            shot['scene_number'] += scene_offset
            data['shots'].append(shot)
        for line in content['dialogue']:
            line['scene_number'] += scene_offset
            data['dialogue'].append(line)
        data['outline'] += [f'Chapter {index + 1}: {item}' for item in content['outline']]
        scene_offset += len(content['content_plan']['scenes'])
        shot_offset += len(content['shots'])
    data['content_plan']['source_video_ids'] = []
    data['continuity_notes'] = parts[-1].continuity_notes
    return ProductionPlan.model_validate(data).validate_configuration(config)

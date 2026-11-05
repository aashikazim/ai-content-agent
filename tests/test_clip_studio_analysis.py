"""YouTube regressions: no live network, GPU models, billing or real credentials."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.workers import clip_studio_analyze as worker
from app.services.clip_studio import media


def test_download_selects_separate_audio_video_and_explicit_node(tmp_path,monkeypatch):
    monkeypatch.setattr(worker.shutil,'which',lambda name:'/usr/bin/node' if name=='node' else None)
    options=worker.download_options(tmp_path,100)
    assert options['format'].startswith('bv[height<=1080]')
    assert '+ba[' in options['format'] and '/b[' in options['format']
    assert '[filesize<=82]' in options['format'] and '[filesize<=?13]' in options['format']
    assert options['js_runtimes']=={'node':{}}
    assert options['extractor_retries']==options['retries']==options['fragment_retries']==0
    assert options['match_filter']({'duration':None}) is None
    assert options['match_filter']({'duration':3601})
    hook=options['progress_hooks'][0]
    hook({'filename':'video','downloaded_bytes':60})
    with pytest.raises(worker.AnalysisError,match='source_too_large'):
        hook({'filename':'audio','downloaded_bytes':41})


def fake_downloader(monkeypatch, *, result=None, error=None):
    downloader=Mock()
    downloader.__enter__=Mock(return_value=downloader);downloader.__exit__=Mock(return_value=False)
    downloader.extract_info.return_value=result
    if error:downloader.extract_info.side_effect=error
    downloader.prepare_filename.return_value='/nonexistent/pre-merge.mp4'
    monkeypatch.setitem(sys.modules,'yt_dlp',SimpleNamespace(YoutubeDL=Mock(return_value=downloader)))
    return downloader


def test_download_uses_actual_merged_file_not_premerge_name(tmp_path,monkeypatch):
    (tmp_path/'source.mkv').write_bytes(b'fixture-merged-container')
    (tmp_path/'source.f399.mp4').write_bytes(b'video-only')
    fake_downloader(monkeypatch,result={'filepath':str(tmp_path/'source.f399.mp4')})
    path=worker.download_source({'metadata_json':'{"url":"https://www.youtube.com/watch?v=abcdefghijk"}'},tmp_path,1024)
    assert path==tmp_path/'source.mkv'


@pytest.mark.parametrize('message,code',[
    ('Requested format is not available token=private-fixture','youtube_format_unavailable'),
    ('Sign in to confirm you are not a bot','youtube_login_required'),
    ('HTTP Error 403 Forbidden','youtube_access_denied'),
    ('Connection timed out','network_failure'),
])
def test_download_errors_are_classified_without_leaking_provider_details(tmp_path,monkeypatch,message,code):
    fake_downloader(monkeypatch,error=RuntimeError(message))
    with pytest.raises(worker.AnalysisError) as error:
        worker.download_source({'metadata_json':'{"url":"https://www.youtube.com/watch?v=abcdefghijk"}'},tmp_path,1024)
    assert error.value.code==code
    assert 'private-fixture' not in str(error.value)


def test_worker_saves_only_sanitized_diagnostic_code(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'main',Mock(side_effect=worker.AnalysisError('youtube_format_unavailable')))
    assert worker.execute(tmp_path/'analysis-request.json')==1
    assert json.loads((tmp_path/'analysis-error.json').read_text())=={'code':'youtube_format_unavailable'}


def test_analysis_failure_is_actionable_and_does_not_expose_raw_errors(tmp_path,monkeypatch):
    monkeypatch.setattr(media,'analysis_python',lambda:Path(sys.executable))
    directory=tmp_path/'analysis'
    def failure(*args,**kwargs):
        (directory/'analysis-error.json').write_text(json.dumps({'code':'youtube_format_unavailable','raw':'private-token'}))
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(media.subprocess,'run',failure)
    with pytest.raises(media.StudioError,match='YouTube') as error:
        media.analyze({'kind':'youtube'},directory,'base',1024)
    assert 'Node 22' in str(error.value) and 'private-token' not in str(error.value)
    assert not (directory/'analysis-request.json').exists()

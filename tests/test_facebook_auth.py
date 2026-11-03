"""Token setup uses mock Graph calls and temporary env files, never real secrets."""
import json
import os
import time
from unittest.mock import Mock

import pytest
import requests

from app.services.facebook_auth_service import (
    FacebookAuthError, FacebookAuthService, REQUIRED_SCOPES, save_page_token,
)


def reply(body, status=200):
    response = Mock(status_code=status)
    response.json.return_value = body
    return response


def metadata(kind, expiry=None, **changes):
    return dict(type=kind, is_valid=True, app_id='1234',
        expires_at=expiry if expiry is not None else int(time.time()) + 3600,
        data_access_expires_at=int(time.time()) + 90 * 86400,
        scopes=sorted(REQUIRED_SCOPES), **changes)


def setup_responses():
    return [reply({'data':metadata('USER')}),
        reply({'access_token':'long-lived-user-private'}),
        reply({'data':metadata('USER', int(time.time()) + 60 * 86400)}),
        reply({'id':'9876','access_token':'page-token-private'}),
        reply({'data':metadata('PAGE', 0)}), reply({'id':'9876'})]


def service(responses):
    session = Mock()
    session.get.side_effect = responses
    return FacebookAuthService(page_id='9876', app_id='1234', app_secret='app-secret-private', session=session)


def test_exchange_derives_and_verifies_page_token_without_upload_or_secret_output():
    instance = service(setup_responses())
    token, result = instance.provision('short-user-private')
    assert token == 'page-token-private'
    assert result['type'] == 'PAGE' and result['expires_at'] == 0
    assert result['acts_as_configured_page'] is True
    assert not result['missing_permissions']
    assert not any(value in json.dumps(result) for value in ['page-token-private','long-lived-user-private','app-secret-private'])
    assert instance.session.get.call_count == 6
    instance.session.post.assert_not_called()
    exchange = instance.session.get.call_args_list[1]
    assert exchange.args == ('https://graph.facebook.com/v26.0/oauth/access_token',)
    assert exchange.kwargs['params'] == {'grant_type':'fb_exchange_token','client_id':'1234',
        'client_secret':'app-secret-private','fb_exchange_token':'short-user-private'}
    assert exchange.kwargs['headers'] == {}
    assert instance.session.trust_env is False
    assert all(call.kwargs['allow_redirects'] is False for call in instance.session.get.call_args_list)


def test_check_reports_wrong_token_type_and_expiry_without_user_ids_or_credentials():
    instance = service([reply({'data':metadata('USER')}), reply({'id':'different-user'})])
    result = instance.check('short-user-private')
    assert result['type'] == 'USER' and not result['acts_as_configured_page']
    assert result['expires_at'] > time.time()
    assert 'different-user' not in json.dumps(result) and 'short-user-private' not in json.dumps(result)


@pytest.mark.parametrize('change,message', [
    ({'type':'PAGE'}, 'USER token'), ({'app_id':'other-app'}, 'configured FACEBOOK_APP_ID'),
    ({'scopes':['pages_read_engagement']}, 'pages_manage_posts'),
    ({'is_valid':False}, 'invalid'), ({'expires_at':1}, 'expired'),
    ({'data_access_expires_at':1}, 'data access expired'),
])
def test_bad_input_stops_before_exchange(change, message):
    data = metadata('USER'); data.update(change)
    instance = service([reply({'data':data})])
    with pytest.raises(FacebookAuthError, match=message): instance.provision('short-user-private')
    assert instance.session.get.call_count == 1


@pytest.mark.parametrize('index,body,message', [
    (1, {}, 'no exchanged token'),
    (2, {'data':metadata('USER')}, 'at least seven days'),
    (3, {'id':'different-page','access_token':'private'}, 'configured Page'),
    (4, {'data':metadata('PAGE')}, 'still short-lived'),
    (5, {'id':'different-page'}, 'different Page'),
])
def test_unverified_or_short_lived_outputs_cannot_be_saved(index, body, message):
    responses = setup_responses(); responses[index] = reply(body)
    instance = service(responses)
    with pytest.raises(FacebookAuthError, match=message): instance.provision('short-user-private')
    instance.session.post.assert_not_called()


def test_missing_app_credentials_stop_before_network():
    instance = FacebookAuthService(page_id='9876', app_id='', app_secret='', session=Mock())
    with pytest.raises(FacebookAuthError, match='FACEBOOK_APP_ID'):
        instance.provision('short-user-private')
    instance.session.get.assert_not_called()


@pytest.mark.parametrize('failure', ['timeout', 401, 403, 500])
def test_auth_requests_do_not_retry_or_reflect_raw_credentials(failure):
    response = reply({'error':{'code':190, 'error_subcode':463, 'message':'app-secret-private short-user-private'}}, status=failure if isinstance(failure,int) else 200)
    instance = service([response])
    if failure == 'timeout': instance.session.get.side_effect = requests.Timeout('app-secret-private')
    with pytest.raises(FacebookAuthError) as error: instance.inspect('short-user-private')
    assert 'app-secret-private' not in str(error.value) and 'short-user-private' not in str(error.value)
    assert instance.session.get.call_count == 1


def test_invalid_app_secret_is_actionable_without_echoing_provider_values():
    responses = setup_responses()
    responses[1] = reply({'error':{'code':1, 'message':'Error validating client secret. app-secret-private'}},400)
    instance = service(responses)
    with pytest.raises(FacebookAuthError, match='FACEBOOK_APP_SECRET') as error:
        instance.provision('short-user-private')
    assert 'token exchange' in str(error.value)
    assert 'app-secret-private' not in str(error.value)
    assert instance.session.get.call_count == 2


@pytest.mark.parametrize('original', [
    'OTHER_SECRET=unchanged\nFACEBOOK_PAGE_ACCESS_TOKEN=old\n# retain this comment\n',
    'OTHER_SECRET=unchanged\nexport FACEBOOK_PAGE_ACCESS_TOKEN="old"\n',
    "FACEBOOK_PAGE_ACCESS_TOKEN='old\nmultiline'\nOTHER_SECRET=unchanged\n",
    'OTHER_SECRET=unchanged\n',
])
def test_private_atomic_save_preserves_other_settings(tmp_path, original):
    path = tmp_path / '.env'; path.write_text(original)
    save_page_token(path,'page-token-private')
    result = path.read_text()
    assert 'OTHER_SECRET=unchanged\n' in result
    assert 'FACEBOOK_PAGE_ACCESS_TOKEN=page-token-private' in result
    assert 'old' not in result and 'multiline' not in result
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.facebook-token-*'))
    if '# retain' in original: assert '# retain this comment\n' in result


def test_duplicate_keys_and_failed_replace_leave_original_file_untouched(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    original = 'FACEBOOK_PAGE_ACCESS_TOKEN=old\nFACEBOOK_PAGE_ACCESS_TOKEN=other\n'
    path.write_text(original)
    with pytest.raises(FacebookAuthError, match='duplicate'): save_page_token(path,'new-token')
    assert path.read_text() == original
    original = 'FACEBOOK_PAGE_ACCESS_TOKEN=old\n'; path.write_text(original)
    def fail(*args): raise OSError('failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError): save_page_token(path,'new-token')
    assert path.read_text() == original
    assert not list(tmp_path.glob('.facebook-token-*'))

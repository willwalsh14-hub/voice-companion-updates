"""Explicit permission gates for optional online speech services."""

def narration_allowed(preferences,settings):
    return preferences.get('ai_consent',settings.get('consent',False)) is True

def cloud_recognition_allowed(mode,preferences,account_setup=False):
    return preferences.get('cloud_consent',False) is True and mode not in ('sleep','exit','web','settings') and not account_setup

PRIVATE_QUERY_KEYS=frozenset(('access_token','refresh_token','id_token','token','auth','authorization','code','state','password','passwd','pass','secret','client_secret','api_key','apikey','key','session','sessionid','sid','oauth_token','oauth_verifier','samlrequest','samlresponse','ticket','signature','sig','x_amz_signature','x_amz_credential','x_amz_security_token'))

def saved_address(url):
    """Keep sign-in credentials out of saved page addresses."""
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    if not isinstance(url,str):return None
    try:
        parts=urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.port is not None and not 1<=parts.port<=65535:return None
        def private(key):return key.casefold().replace('-','_') in PRIVATE_QUERY_KEYS
        query=parse_qsl(parts.query,keep_blank_values=True)
        private_query=any(private(key) for key,_ in query)
        private_fragment=any(private(key) for key,_ in parse_qsl(parts.fragment.split('?')[-1],keep_blank_values=True))
        if not parts.username and not parts.password and not private_query and not private_fragment:return url
        return urlunsplit((parts.scheme,parts.netloc.rsplit('@',1)[-1],parts.path,urlencode([(key,value) for key,value in query if not private(key)]),'' if private_fragment else parts.fragment))
    except (ValueError,TypeError):return None

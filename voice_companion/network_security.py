"""Fail closed before forwarding a credential-bearing service request."""
from urllib import request,error,parse

class NoServiceRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise error.URLError('The service redirected unexpectedly. No credentials were sent to the new address.')

def secure_service_open(req,timeout=30):
    url=req.full_url if isinstance(req,request.Request) else req
    address=parse.urlsplit(url)
    if address.scheme!='https' or not address.hostname or address.username or address.password or address.port not in (None,443):
        raise error.URLError('A secure service address is required.')
    return request.build_opener(NoServiceRedirect()).open(req,timeout=timeout)

import pytest
from local_ai_control.services.web_research import HttpResponse,SafeHttpFetcher
def transport(url,timeout,max_bytes): return HttpResponse(200,{"content-type":"text/plain"},b"ok",url)
@pytest.mark.parametrize("address",["127.0.0.1","10.0.0.1","192.168.1.1","169.254.169.254","::1","0.0.0.0"])
def test_ssrf_blocks_private_special_and_metadata_addresses(address):
    fetcher=SafeHttpFetcher(resolver=lambda host:[address],transport=transport)
    with pytest.raises(PermissionError): fetcher.fetch("https://example.test/")
def test_ssrf_blocks_credentials_non_http_and_unsafe_ports():
    fetcher=SafeHttpFetcher(resolver=lambda host:["93.184.216.34"],transport=transport)
    for url in ("file:///etc/passwd","https://u:p@example.test/","http://example.test:8000/"):
        with pytest.raises(PermissionError): fetcher.fetch(url)
def test_redirect_is_revalidated_and_cannot_land_on_private_ip():
    def redirected(url,timeout,max_bytes): return HttpResponse(302,{"location":"http://internal.test/"},b"",url)
    fetcher=SafeHttpFetcher(resolver=lambda host:["93.184.216.34"] if host=="public.test" else ["127.0.0.1"],transport=redirected)
    with pytest.raises(PermissionError): fetcher.fetch("https://public.test/")

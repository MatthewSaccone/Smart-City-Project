from urllib.parse import parse_qs
import httpx
import pytest
from shared.service_tokens import ServiceTokenClient, ServiceTokenError

def test_cache_and_refresh():
    clock=[0]; calls=[]
    def handler(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200,json={'access_token':str(len(calls)), 'expires_in':100,'token_type':'Bearer'})
    client=ServiceTokenClient('https://id.example/application/o/token/','api','machine','test-only',transport=httpx.MockTransport(handler),clock=lambda:clock[0])
    assert client.get_token()=='1'; clock[0]=79; assert client.get_token()=='1'
    clock[0]=80; assert client.headers()=={'Authorization':'Bearer 2'}
    assert calls[0]=={'grant_type':['client_credentials'],'client_id':['api'],'username':['machine'],'password':['test-only'],'scope':['smart-city']}

@pytest.mark.parametrize('response',[httpx.Response(401),httpx.Response(200,json={'access_token':'x','expires_in':0,'token_type':'Bearer'})])
def test_reject_bad_response(response):
    client=ServiceTokenClient('https://id.example/token','api','machine','test-only',transport=httpx.MockTransport(lambda _:response))
    with pytest.raises(ServiceTokenError): client.get_token()


def test_internal_development_preserves_canonical_host(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    def handler(request):
        assert request.headers["host"] == "localhost:8088"
        return httpx.Response(200,json={"access_token":"test", "expires_in":300, "token_type":"Bearer"})
    client=ServiceTokenClient("http://authentik-server:9000/application/o/token/", "api", "machine", "test-only", issuer="http://localhost:8088/application/o/smart-city/", transport=httpx.MockTransport(handler))
    assert client.get_token()=="test"
    with pytest.raises(ValueError):
        ServiceTokenClient("http://authentik-server:9000/application/o/token/", "api", "machine", "test-only")

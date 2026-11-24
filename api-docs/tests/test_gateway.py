import httpx
from fastapi.testclient import TestClient
from app import main

def test_proxy_and_docs(monkeypatch):
    actual=httpx.AsyncClient; calls=[]
    def upstream(request):
        calls.append(request)
        if request.url.path=='/openapi.json':
            return httpx.Response(200,json={'paths':{'/health':{'get':{}},'/business':{'get':{}}}})
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(main.httpx,'AsyncClient',lambda **kwargs:actual(transport=httpx.MockTransport(upstream),**kwargs))
    with TestClient(main.app) as client:
        response=client.get('/s7/business?x=1',headers={'Authorization':'Bearer example','X-User-ID':'fake','Cookie':'secret=1'})
        assert response.status_code==200
        assert calls[-1].headers['Authorization']=='Bearer example'
        assert 'x-user-id' not in calls[-1].headers and 'cookie' not in calls[-1].headers
        spec=client.get('/openapi.json').json()
        assert list(spec['components']['securitySchemes'])==['SmartCityOAuth']
        for i in range(1,8):
            assert spec['paths'][f'/s{i}/health']['get']['security']==[]
            assert spec['paths'][f'/s{i}/business']['get']['security']==[{'SmartCityOAuth':['smart-city']}]
        assert 'usePkceWithAuthorizationCodeGrant' in client.get('/docs').text

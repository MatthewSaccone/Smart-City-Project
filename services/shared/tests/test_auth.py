import time
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.auth import JWTSettings, TokenVerifier, protect_service
from shared.testing_identity import EphemeralIdentity

@pytest.fixture(scope='module')
def identity():
    with EphemeralIdentity() as authority:
        yield authority

@pytest.mark.parametrize('service', [f's{i}' for i in range(1,8)])
def test_policy(service, identity, monkeypatch):
    for k,v in identity.environment().items(): monkeypatch.setenv(k,v)
    app=FastAPI(dependencies=[protect_service(service)])
    app.state.token_verifier=TokenVerifier(JWTSettings.from_env())
    @app.get('/health')
    def health(): return {'ok':True}
    @app.get('/business')
    def read(): return {'ok':True}
    @app.post('/business')
    def write(): return {'ok':True}
    client=TestClient(app)
    def call(token=None, method='GET', path='/business'):
        return client.request(method,path,headers={'Authorization':'Bearer '+token} if token else {})
    assert call(path='/health').status_code==200
    assert call().status_code==401
    assert call('bad').status_code==401
    assert call(identity.token()).status_code==200
    assert call(identity.token(roles=[])).status_code==403
    assert call(identity.token(), 'POST').status_code==403
    assert call(identity.token(roles=['administrator']), 'POST').status_code==200
    assert call(identity.token(roles=['service'], permissions=[service+':read'])).status_code==200
    assert call(identity.token(roles=['service'], permissions=[service+':read']), 'POST').status_code==403
    assert call(identity.token(roles=['service'], permissions=[service+':write']), 'POST').status_code==200
    assert call(identity.token(roles=['service','administrator'], permissions=[service+':read'])).status_code==403
    for claims in [{'iss':'https://wrong.example'},{'aud':'wrong'},{'exp':int(time.time())-1},{'nbf':int(time.time())+300},{'scope':'openid'},{'sub':''}]:
        assert call(identity.token(**claims)).status_code==401
    tok=identity.token().split('.'); tok[2]=('A' if tok[2][0]!='A' else 'B')+tok[2][1:]
    assert call('.'.join(tok)).status_code==401
    assert call(jwt.encode({'sub':'x'},'a'*32,algorithm='HS256',headers={'kid':'ephemeral-key'})).status_code==401

def test_estimator_and_prediction(identity, monkeypatch):
    for k,v in identity.environment().items(): monkeypatch.setenv(k,v)
    for service,path in [('s4','/v1/parking/occupancy/estimate'),('s2','/v1/traffic/predictions')]:
        app=FastAPI(dependencies=[protect_service(service)])
        app.state.token_verifier=TokenVerifier(JWTSettings.from_env())
        app.add_api_route(path,lambda: {'ok':True},methods=['POST'])
        client=TestClient(app)
        def call(**claims): return client.post(path,headers={'Authorization':'Bearer '+identity.token(**claims)})
        if service=='s4':
            assert call(roles=['administrator'],permissions=['parking:estimate']).status_code==403
            assert call(roles=['service'],permissions=['s4:write']).status_code==403
            assert call(roles=['service'],permissions=['parking:estimate']).status_code==200
        else:
            assert call().status_code==200
            assert call(roles=['service'],permissions=['s2:read']).status_code==200

def test_missing_configuration_is_fail_closed():
    app=FastAPI(dependencies=[protect_service('s7')]); app.state.token_verifier=TokenVerifier(JWTSettings(None,None,None))
    app.add_api_route('/business',lambda: {},methods=['GET'])
    assert TestClient(app).get('/business',headers={'Authorization':'Bearer abc'}).status_code==503


def test_unknown_key_refresh_and_removed_key_rejection(monkeypatch):
    import time
    from types import SimpleNamespace

    import jwt.jwks_client as jwks_client
    from cryptography.hazmat.primitives.asymmetric import rsa
    from shared.auth import InvalidAccessToken

    with EphemeralIdentity() as authority:
        for k, v in authority.environment().items():
            monkeypatch.setenv(k, v)

        verifier = TokenVerifier(JWTSettings.from_env())

        cooldown = getattr(
            verifier._jwks_client,
            "cooldown_duration",
            0,
        )

        # Simulate elapsed time without modifying the system clock
        # or waiting for the real JWKS refresh cooldown.
        clock = time.monotonic()

        if cooldown > 0:
            monkeypatch.setattr(
                jwks_client,
                "time",
                SimpleNamespace(monotonic=lambda: clock),
            )

        # Verify the original signing key.
        old = authority.token()
        assert verifier.verify_token(old).subject

        before = authority.requests

        # Rotate the identity provider's signing key.
        authority.key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        authority.kid = "rotated-key"

        rotated = authority.token()

        if cooldown > 0:
            # Requests during the cooldown must not force a refresh.
            for _ in range(3):
                with pytest.raises(InvalidAccessToken):
                    verifier.verify_token(rotated)

            assert authority.requests == before

            # Advance the simulated clock beyond the cooldown.
            clock += cooldown + 1

        # The new key must be discovered and accepted.
        assert verifier.verify_token(rotated).subject
        assert authority.requests > before

        # The removed key must now be rejected.
        with pytest.raises(InvalidAccessToken):
            verifier.verify_token(old)

        # The newly rotated key must remain valid.
        assert verifier.verify_token(rotated).subject

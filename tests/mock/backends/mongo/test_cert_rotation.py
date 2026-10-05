"""Mock-tier tests for bookscraper.backends.mongo.cert_rotation's Atlas API calls.

httpx.post is mocked, so no network is touched; the filesystem side goes through
isolated_config so the real ~/.bookscrapper is never read or written.
"""

import json
import os
import pathlib
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest

from bookscraper.backends.mongo import cert_rotation
from bookscraper.backends.mongo.client import MongoConnectionError

MODULE = "bookscraper.backends.mongo.cert_rotation"

_X509_SETTINGS = {
    "auth_type": "x509",
    "x509": {
        "uri": {"source": "literal", "value": "mongodb+srv://x"},
        "cert": {"source": "literal", "value": "X509-cert-old.pem"},
    },
}


@pytest.fixture
def atlas_env(monkeypatch):
    """Fixture: set ATLAS_* env vars to dummy values for cert rotation tests."""
    monkeypatch.setenv("ATLAS_CLIENT_ID", "client-id")
    monkeypatch.setenv("ATLAS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ATLAS_PROJECT_ID", "proj123")
    monkeypatch.setenv("ATLAS_DB_USER", "svc-user")
    monkeypatch.delenv("ATLAS_CERT_MONTHS", raising=False)
    monkeypatch.delenv("ATLAS_CERT_FILE", raising=False)


def _response(*, json_body=None, content=b"", status=200, url="https://cloud.mongodb.com/x") -> httpx.Response:
    """Build a minimal httpx.Response for Atlas API mocking."""
    return httpx.Response(
        status,
        json=json_body,
        content=None if json_body is not None else content,
        request=httpx.Request("POST", url),
    )


# --------------------------------------------------------------------------- #
# get_access_token / issue_certificate
# --------------------------------------------------------------------------- #
def test_get_access_token_posts_client_credentials():
    """
    [Local] cert rotation: verifies that get_access_token POSTs client_credentials to the Atlas OAuth URL and returns the token.

    Scenario:
        Given httpx.post is patched to return {"access_token": "tok"} with status 200
        When cert_rotation.get_access_token("id", "secret") is called
        Then it POSTs to ATLAS_OAUTH_URL with auth=("id", "secret"), grant_type="client_credentials", and returns "tok".

    Boundaries:
        - HTTP call shape and return value only; no real Atlas network

    On failure, first check:
        - The POST URL, auth tuple, and grant_type data in get_access_token
    """
    with patch(f"{MODULE}.httpx.post", return_value=_response(json_body={"access_token": "tok"})) as post:
        assert cert_rotation.get_access_token("id", "secret") == "tok"

    assert post.call_args.args[0] == cert_rotation.ATLAS_OAUTH_URL
    assert post.call_args.kwargs["auth"] == ("id", "secret")
    assert post.call_args.kwargs["data"] == {"grant_type": "client_credentials"}


def test_get_access_token_propagates_http_errors():
    """
    [Local] cert rotation: verifies that get_access_token raises HTTPStatusError on a 401 response.

    Scenario:
        Given httpx.post is patched to return a 401 response
        When cert_rotation.get_access_token("id", "bad") is called
        Then httpx.HTTPStatusError is raised.

    Boundaries:
        - The raise_for_status() path; no real Atlas network

    On failure, first check:
        - The raise_for_status call in get_access_token
    """
    with (
        patch(f"{MODULE}.httpx.post", return_value=_response(status=401, json_body={})),
        pytest.raises(httpx.HTTPStatusError),
    ):
        cert_rotation.get_access_token("id", "bad")


def test_issue_certificate_sends_months_in_json_body_not_query():
    """
    [Local] cert rotation: verifies that issue_certificate sends monthsUntilExpiration in the JSON body, not query params.

    Scenario:
        Given httpx.post is patched to return b"PEM" content
        When cert_rotation.issue_certificate("tok", "proj123", "svc-user", 9) is called
        Then the POST URL ends with the correct user path, json={"monthsUntilExpiration": 9} is set, "params" is absent, and the Bearer token is in the Authorization header.

    Boundaries:
        - Request shape only; no real Atlas network

    On failure, first check:
        - The JSON body vs query-params placement and the Bearer token header in issue_certificate
    """
    with patch(f"{MODULE}.httpx.post", return_value=_response(content=b"PEM")) as post:
        cert = cert_rotation.issue_certificate("tok", "proj123", "svc-user", 9)

    assert cert == b"PEM"
    assert post.call_args.args[0].endswith("/groups/proj123/databaseUsers/svc-user/certs")
    assert post.call_args.kwargs["json"] == {"monthsUntilExpiration": 9}
    assert "params" not in post.call_args.kwargs
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


# --------------------------------------------------------------------------- #
# rotate_certificate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("missing", ["ATLAS_CLIENT_ID", "ATLAS_CLIENT_SECRET", "ATLAS_PROJECT_ID", "ATLAS_DB_USER"])
def test_rotate_certificate_requires_each_credential(atlas_env, monkeypatch, missing):
    """
    [Local] cert rotation: verifies that each missing ATLAS_* env var causes a RotationError before any HTTP request.

    Scenario:
        Given all four ATLAS_* env vars are set (atlas_env) and one of them is deleted
        When cert_rotation.rotate_certificate() is called
        Then RotationError matching the missing variable name is raised and httpx.post is never called.

    Boundaries:
        - 4 parametrized cases (one per ATLAS_* variable); no real Atlas network

    On failure, first check:
        - The per-credential presence check in rotate_certificate
    """
    monkeypatch.delenv(missing)

    with (
        patch(f"{MODULE}.httpx.post") as post,
        pytest.raises(cert_rotation.RotationError, match=missing),
    ):
        cert_rotation.rotate_certificate()

    post.assert_not_called()


def test_rotate_certificate_invalid_months_fails_before_any_request(atlas_env):
    """
    [Local] cert rotation: verifies that months=99 raises RotationError mentioning "1-24" before any HTTP request.

    Scenario:
        Given all ATLAS_* env vars are set and months=99 (out of range)
        When cert_rotation.rotate_certificate(months=99) is called
        Then RotationError matching "1-24" is raised and httpx.post is never called.

    Boundaries:
        - Months range validation only; no real Atlas network

    On failure, first check:
        - The months range guard in rotate_certificate
    """
    with (
        patch(f"{MODULE}.httpx.post") as post,
        pytest.raises(cert_rotation.RotationError, match="1-24"),
    ):
        cert_rotation.rotate_certificate(months=99)

    post.assert_not_called()


def test_rotate_certificate_happy_path_writes_cert_and_updates_settings(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies that a successful rotation writes the PEM, defaults to 6 months, and repoints settings.json.

    Scenario:
        Given an x509 settings.json, httpx.post returning a token then a PEM, and verify=False
        When cert_rotation.rotate_certificate(verify=False) is called
        Then the PEM is written to isolated_config, monthsUntilExpiration=6 is sent, settings.json cert.value is updated, and notes mention the new path.

    Boundaries:
        - Happy path with verify skipped; no real Atlas network or MongoDB

    On failure, first check:
        - The cert write, default months value, settings.json repoint, and notes in rotate_certificate
    """
    (isolated_config / "settings.json").write_text(
        json.dumps(
            {
                "auth_type": "x509",
                "x509": {
                    "uri": {"source": "literal", "value": "mongodb+srv://x"},
                    "cert": {"source": "literal", "value": "X509-cert-old.pem"},
                },
            }
        )
    )
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with patch(f"{MODULE}.httpx.post", side_effect=responses) as post:
        # verify=False isolates issuance + repoint mechanics from the ping (covered separately).
        path, notes, verified = cert_rotation.rotate_certificate(verify=False)

    assert verified is True
    assert path.parent == isolated_config
    assert path.read_bytes() == b"NEW PEM"
    assert post.call_args_list[1].kwargs["json"] == {"monthsUntilExpiration": 6}
    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"]["value"] == path.name
    assert any(path.name in n for n in notes)


def test_rotate_certificate_explicit_arguments_beat_env(atlas_env, monkeypatch, tmp_path, isolated_config):
    """
    [Local] cert rotation: verifies that explicit months/output arguments override ATLAS_CERT_MONTHS/ATLAS_CERT_FILE env vars.

    Scenario:
        Given ATLAS_CERT_MONTHS=12 and ATLAS_CERT_FILE pointing to from-env.pem, but months=3 and output=explicit.pem passed explicitly
        When cert_rotation.rotate_certificate(months=3, output=explicit, verify=False) is called
        Then the cert is written to explicit.pem with monthsUntilExpiration=3, and from-env.pem is never created.

    Boundaries:
        - Flag-beats-env precedence for both months and output path

    On failure, first check:
        - The months and output-path precedence logic in rotate_certificate
    """
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    monkeypatch.setenv("ATLAS_CERT_FILE", str(tmp_path / "from-env.pem"))
    explicit = tmp_path / "explicit.pem"
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"PEM")]

    with patch(f"{MODULE}.httpx.post", side_effect=responses) as post:
        path, _, _ = cert_rotation.rotate_certificate(months=3, output=explicit, verify=False)

    assert path == explicit
    assert explicit.read_bytes() == b"PEM"
    assert not (tmp_path / "from-env.pem").exists()
    assert post.call_args_list[1].kwargs["json"] == {"monthsUntilExpiration": 3}


def test_rotate_certificate_env_overrides_apply_when_no_arguments(
    atlas_env, monkeypatch, tmp_path, isolated_config
):
    """
    [Local] cert rotation: verifies that ATLAS_CERT_MONTHS and ATLAS_CERT_FILE env vars apply when no explicit arguments are passed.

    Scenario:
        Given ATLAS_CERT_MONTHS=12 and ATLAS_CERT_FILE pointing to from-env.pem, and no months/output arguments
        When cert_rotation.rotate_certificate(verify=False) is called
        Then the cert is written to from-env.pem with monthsUntilExpiration=12.

    Boundaries:
        - Env-override path for months and output path when no flags override them

    On failure, first check:
        - The env-var fallback for months and output path in rotate_certificate
    """
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    monkeypatch.setenv("ATLAS_CERT_FILE", str(tmp_path / "from-env.pem"))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"PEM")]

    with patch(f"{MODULE}.httpx.post", side_effect=responses) as post:
        path, _, _ = cert_rotation.rotate_certificate(verify=False)

    assert path == tmp_path / "from-env.pem"
    assert post.call_args_list[1].kwargs["json"] == {"monthsUntilExpiration": 12}


def test_rotate_certificate_http_status_error_becomes_rotation_error_without_leaking_body(
    atlas_env, isolated_config
):
    """
    [Local] cert rotation: verifies that an Atlas 403 becomes RotationError with the status code but no response body content.

    Scenario:
        Given httpx.post returns a 403 response whose body contains "client-secret was rejected"
        When cert_rotation.rotate_certificate() is called
        Then RotationError is raised containing "403" but not "client-secret", and no PEM is written.

    Boundaries:
        - HTTP error -> RotationError conversion and response-body redaction; no real Atlas network

    On failure, first check:
        - The HTTPStatusError -> RotationError conversion and body-stripping in rotate_certificate
    """
    denied = _response(status=403, json_body={"detail": "client-secret was rejected"})

    with (
        patch(f"{MODULE}.httpx.post", return_value=denied),
        pytest.raises(cert_rotation.RotationError) as excinfo,
    ):
        cert_rotation.rotate_certificate()

    assert "403" in str(excinfo.value)
    assert "client-secret" not in str(excinfo.value)
    assert list(pathlib.Path(isolated_config).glob("X509-cert-*.pem")) == []


def test_rotate_certificate_network_error_becomes_rotation_error(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies that an httpx.ConnectError during the Atlas API call becomes a RotationError("ConnectError").

    Scenario:
        Given httpx.post raises httpx.ConnectError("no route")
        When cert_rotation.rotate_certificate() is called
        Then RotationError matching "ConnectError" is raised.

    Boundaries:
        - Network-failure -> RotationError conversion; no real Atlas network

    On failure, first check:
        - The ConnectError -> RotationError conversion in rotate_certificate
    """
    with (
        patch(f"{MODULE}.httpx.post", side_effect=httpx.ConnectError("no route")),
        pytest.raises(cert_rotation.RotationError, match="ConnectError"),
    ):
        cert_rotation.rotate_certificate()


def test_rotate_certificate_does_not_touch_settings_when_atlas_fails(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies that an Atlas 500 error leaves settings.json untouched.

    Scenario:
        Given an existing x509 settings.json and httpx.post returning a 500 response
        When cert_rotation.rotate_certificate() is called and RotationError is raised
        Then settings.json content is identical to its pre-rotation state.

    Boundaries:
        - Atomicity guard: settings.json must never be partially modified on failure

    On failure, first check:
        - The settings.json write guard / rollback in rotate_certificate on Atlas errors
    """
    settings = {
        "auth_type": "x509",
        "x509": {
            "uri": {"source": "literal", "value": "mongodb+srv://x"},
            "cert": {"source": "literal", "value": "X509-cert-old.pem"},
        },
    }
    (isolated_config / "settings.json").write_text(json.dumps(settings))

    with (
        patch(f"{MODULE}.httpx.post", return_value=_response(status=500, json_body={})),
        pytest.raises(cert_rotation.RotationError),
    ):
        cert_rotation.rotate_certificate()

    assert json.loads((isolated_config / "settings.json").read_text()) == settings


# --------------------------------------------------------------------------- #
# verify_certificate
# --------------------------------------------------------------------------- #
def test_verify_retries_then_succeeds():
    """
    [Local] cert rotation: verifies verify retries then succeeds.

    Scenario:
        Atlas rejects the freshly issued cert twice while it propagates, then accepts it.

    Boundaries:
        - build_mongo_client/ping faked; injected sleep so no real wait; client per attempt.

    On failure, first check:
        - verification gives up before Atlas propagates, or never retries.
    """
    client = Mock()
    sleep = Mock()
    with (
        patch(f"{MODULE}.build_mongo_client", return_value=client) as build,
        patch(f"{MODULE}.ping", side_effect=[MongoConnectionError("e"), MongoConnectionError("e"), None]) as ping,
    ):
        cert_rotation.verify_certificate(
            pathlib.Path("/tmp/new.pem"), "mongodb+srv://x", attempts=5, delay_s=1.0, sleep=sleep
        )

    assert ping.call_count == 3
    assert build.call_count == 3
    assert sleep.call_count == 2  # one pause between each of the three attempts, none after success
    assert client.close.call_count == 3  # closed after every attempt


def test_verify_failure_message_has_code_but_no_host():
    """
    [Local] cert rotation: verifies verify failure message has code but no host.

    Scenario:
        every attempt fails; the raised error keeps the redacted code but no host/URI.

    Boundaries:
        - ping always raises a redacted MongoConnectionError; injected sleep.

    On failure, first check:
        - verification masks the error code, or a host/credential leaks into the message.
    """
    client = Mock()
    sleep = Mock()
    with (
        patch(f"{MODULE}.build_mongo_client", return_value=client),
        patch(f"{MODULE}.ping", side_effect=MongoConnectionError("OperationFailure 8000 AtlasError")),
        pytest.raises(MongoConnectionError) as excinfo,
    ):
        cert_rotation.verify_certificate(
            pathlib.Path("/tmp/new.pem"), "mongodb+srv://user:pw@cluster.mongodb.net/db", attempts=2, sleep=sleep
        )

    msg = str(excinfo.value)
    assert "8000" in msg
    assert "cluster.mongodb.net" not in msg and "pw" not in msg and "user" not in msg
    assert client.close.call_count == 2
    assert sleep.call_count == 1


def test_verify_certificate_runs_once_when_attempts_below_one():
    """
    [Local] cert rotation: verifies verify certificate runs once when attempts below one.

    Scenario:
        attempts <= 0 still runs exactly one ping (clamped to 1), never zero.

    Boundaries:
        - build_mongo_client/ping faked; ping succeeds so no sleep is needed.

    On failure, first check:
        - a mistyped --verify-attempts 0 silently skips verification.
    """
    client = Mock()
    with (
        patch(f"{MODULE}.build_mongo_client", return_value=client),
        patch(f"{MODULE}.ping") as ping,
    ):
        cert_rotation.verify_certificate(pathlib.Path("/tmp/new.pem"), "mongodb+srv://x", attempts=0)

    assert ping.call_count == 1
    assert client.close.call_count == 1


# --------------------------------------------------------------------------- #
# rotate_certificate verification integration
# --------------------------------------------------------------------------- #
def test_verify_success_then_repoints_settings(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies verify success then repoints settings.

    Scenario:
        the new cert verifies, so settings.json is repointed at it and verified is True.

    Boundaries:
        - Atlas HTTP + build_mongo_client/ping faked (ping succeeds).

    On failure, first check:
        - a verified cert is not committed, or verification is skipped.
    """
    (isolated_config / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", "old.pem")),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping"),
    ):
        path, notes, verified = cert_rotation.rotate_certificate()

    assert verified is True
    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"]["value"] == path.name
    assert any("Verified" in n for n in notes)


def test_verify_skipped_when_no_uri_configured(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies verify skipped when no uri configured.

    Scenario:
        no URI is configured, so verification is skipped (no ping) but repointing proceeds.

    Boundaries:
        - resolver returns (None, None); build_mongo_client asserted never called.

    On failure, first check:
        - verification pings with no URI, or a skip blocks the repoint.
    """
    (isolated_config / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=(None, None)),
        patch(f"{MODULE}.build_mongo_client") as build,
        patch(f"{MODULE}.ping") as ping,
    ):
        path, notes, verified = cert_rotation.rotate_certificate()

    assert verified is True
    build.assert_not_called()
    ping.assert_not_called()
    assert any("Skipping verification" in n for n in notes)
    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"]["value"] == path.name


def test_rotate_certificate_verify_failure_leaves_settings_untouched(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies rotate certificate verify failure leaves settings untouched.

    Scenario:
        verification fails for good; settings.json is NOT repointed, the PEM stays on disk, and verified is False (ROT-12).

    Boundaries:
        - ping always raises a redacted MongoConnectionError; verify_attempts=1 (no sleep).

    On failure, first check:
        - a broken identity is committed to settings.json.
    """
    (isolated_config / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", "old.pem")),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping", side_effect=MongoConnectionError("OperationFailure 8000 AtlasError")),
    ):
        path, notes, verified = cert_rotation.rotate_certificate(verify_attempts=1)

    assert verified is False
    assert json.loads((isolated_config / "settings.json").read_text()) == _X509_SETTINGS  # untouched
    assert path.read_bytes() == b"NEW PEM"  # new PEM left on disk
    assert any(n.startswith("WARNING") and "8000" in n for n in notes)


def test_rotate_certificate_no_verify_skips_ping_and_repoints(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies rotate certificate no verify skips ping and repoints.

    Scenario:
        verify=False repoints settings.json without ever building a client / pinging.

    Boundaries:
        - build_mongo_client asserted never called.

    On failure, first check:
        - the offline path still contacts Atlas.
    """
    (isolated_config / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.build_mongo_client") as build,
    ):
        path, notes, verified = cert_rotation.rotate_certificate(verify=False)

    assert verified is True
    build.assert_not_called()
    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"]["value"] == path.name
    assert any("Verification skipped" in n for n in notes)


def test_rotate_certificate_config_error_during_verify_becomes_rotation_error(atlas_env, isolated_config):
    """
    [Local] cert rotation: verifies rotate certificate config error during verify becomes rotation error.

    Scenario:
        the resolver cannot supply a URI (ConfigError), so rotation fails loudly without repointing rather than pinging or guessing.

    Boundaries:
        - resolve_mongo_connection raises ConfigError after the PEM is written.

    On failure, first check:
        - an unhandled ConfigError crashes, or settings.json is repointed blindly.
    """
    (isolated_config / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(
            f"{MODULE}.config.resolve_mongo_connection",
            side_effect=cert_rotation.config.ConfigError("settings.json: bad shape"),
        ),
        patch(f"{MODULE}.build_mongo_client") as build,
        pytest.raises(cert_rotation.RotationError, match="could not be resolved"),
    ):
        cert_rotation.rotate_certificate()

    build.assert_not_called()
    # PEM was written before the failure; settings.json is left pointing at the old cert.
    assert json.loads((isolated_config / "settings.json").read_text()) == _X509_SETTINGS


# --------------------------------------------------------------------------- #
# rotate_certificate pruning integration (subtask 07)
# --------------------------------------------------------------------------- #
@pytest.fixture
def prune_guard(isolated_config) -> Path:
    """isolated_config plus a hard guard: a prune-through-rotation test must never run against the
    real ~/.bookscrapper, whose X509-cert-*.pem files hold live private keys."""
    assert cert_rotation.config.CONFIG_DIR != pathlib.Path.home() / ".bookscrapper"
    return isolated_config


def test_verify_success_prunes_superseded_keeping_active(atlas_env, prune_guard):
    """
    [Local] cert rotation: verifies verify success prunes superseded keeping active.

    Scenario:
        after a verified rotation the active cert and the new cert are kept, older certs pruned.

    Boundaries:
        - keep=1; the resolver reports an older 'active' cert that must survive despite being beyond keep (protected), while a still-older stale cert is pruned (ROT-6/7).

    On failure, first check:
        - the active identity is deleted, or stale keys linger past keep.
    """
    (prune_guard / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    active = prune_guard / "X509-cert-old.pem"
    active.write_bytes(b"active")
    os.utime(active, (1000, 1000))
    stale = prune_guard / "X509-cert-stale.pem"
    stale.write_bytes(b"stale")
    os.utime(stale, (500, 500))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", str(active))),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping"),
    ):
        path, notes, verified = cert_rotation.rotate_certificate(keep=1, verify_attempts=1)

    assert verified is True
    assert path.exists()  # new cert kept (newest and protected)
    assert active.exists()  # protected active cert kept despite being older and beyond keep=1
    assert not stale.exists()  # superseded cert pruned
    assert any("Pruned superseded" in n for n in notes)


def test_prune_protects_tls_cert_file_env(atlas_env, prune_guard, monkeypatch):
    """
    [Local] cert rotation: verifies prune protects tls cert file env.

    Scenario:
        a cert named by TLS_CERT_FILE is protected from pruning however old (ROT-6).

    Boundaries:
        - keep=1 with a TLS_CERT_FILE cert older than the newest, so only the TLS_CERT_FILE clause of the protected set keeps it.

    On failure, first check:
        - an in-use cert referenced only via TLS_CERT_FILE is deleted.
    """
    (prune_guard / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    tls_cert = prune_guard / "X509-cert-tls.pem"
    tls_cert.write_bytes(b"tls")
    os.utime(tls_cert, (500, 500))
    monkeypatch.setenv("TLS_CERT_FILE", str(tls_cert))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", "old.pem")),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping"),
    ):
        _path, _notes, verified = cert_rotation.rotate_certificate(keep=1, verify_attempts=1)

    assert verified is True
    assert tls_cert.exists()  # protected via TLS_CERT_FILE despite being older than keep=1


def test_prune_skipped_when_active_cert_unresolvable(atlas_env, prune_guard):
    """
    [Local] cert rotation: verifies prune skipped when active cert unresolvable.

    Scenario:
        the URI resolves for verification but the post-repoint resolve raises ConfigError, so pruning is skipped entirely - never prune on uncertainty (ROT-8).

    Boundaries:
        - resolve_mongo_connection succeeds once (URI) then raises ConfigError (prune).

    On failure, first check:
        - a resolver hiccup deletes certs blindly, risking loss of the active identity.
    """
    (prune_guard / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    stale = prune_guard / "X509-cert-stale.pem"
    stale.write_bytes(b"stale")
    os.utime(stale, (500, 500))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(
            f"{MODULE}.config.resolve_mongo_connection",
            side_effect=[("mongodb+srv://x", "old.pem"), cert_rotation.config.ConfigError("bad shape")],
        ),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping"),
    ):
        _path, notes, verified = cert_rotation.rotate_certificate(keep=1, verify_attempts=1)

    assert verified is True
    assert stale.exists()  # nothing pruned on uncertainty
    assert any("Skipping cert pruning" in n and "uncertainty" in n for n in notes)


def test_prune_not_run_when_verification_fails(atlas_env, prune_guard):
    """
    [Local] cert rotation: verifies prune not run when verification fails.

    Scenario:
        verification fails, so pruning never runs (ROT-13) - no superseded cert is removed.

    Boundaries:
        - ping always raises; prune_old_certificates patched to detect any call.

    On failure, first check:
        - certs are pruned around an unverified/broken new identity.
    """
    (prune_guard / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", "old.pem")),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping", side_effect=MongoConnectionError("OperationFailure 8000 AtlasError")),
        patch(f"{MODULE}.prune_old_certificates") as mock_prune,
    ):
        _path, _notes, verified = cert_rotation.rotate_certificate(verify_attempts=1)

    assert verified is False
    mock_prune.assert_not_called()


def test_prune_not_run_when_rotation_fails(atlas_env, prune_guard):
    """
    [Local] cert rotation: verifies prune not run when rotation fails.

    Scenario:
        an Atlas API failure aborts rotation before any pruning is attempted.

    Boundaries:
        - httpx.post returns 500; prune_old_certificates patched to detect any call.

    On failure, first check:
        - certs are pruned even though no new cert was successfully issued.
    """
    with (
        patch(f"{MODULE}.httpx.post", return_value=_response(status=500, json_body={})),
        patch(f"{MODULE}.prune_old_certificates") as mock_prune,
        pytest.raises(cert_rotation.RotationError),
    ):
        cert_rotation.rotate_certificate()

    mock_prune.assert_not_called()


def test_no_prune_argument_keeps_all_certs(atlas_env, prune_guard):
    """
    [Local] cert rotation: verifies no prune argument keeps all certs.

    Scenario:
        prune=False leaves every superseded cert on disk after a verified rotation.

    Boundaries:
        - a stale cert well beyond keep=1 survives; no 'Pruned' note is emitted.

    On failure, first check:
        - --no-prune still deletes certs.
    """
    (prune_guard / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    stale = prune_guard / "X509-cert-stale.pem"
    stale.write_bytes(b"stale")
    os.utime(stale, (500, 500))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", "old.pem")),
        patch(f"{MODULE}.build_mongo_client"),
        patch(f"{MODULE}.ping"),
    ):
        _path, notes, verified = cert_rotation.rotate_certificate(keep=1, verify_attempts=1, prune=False)

    assert verified is True
    assert stale.exists()
    assert not any("Pruned" in n for n in notes)


def test_no_verify_does_not_prune(atlas_env, prune_guard):
    """
    [Local] cert rotation: verifies no verify does not prune.

    Scenario:
        --no-verify (verify=False) repoints but never prunes, keeping old certs against a possible lockout (the chosen conservative interaction of --no-verify with pruning).

    Boundaries:
        - a stale cert beyond keep=1 survives; a skip note is emitted.

    On failure, first check:
        - an unverified rotation deletes the previously working identity.
    """
    (prune_guard / "settings.json").write_text(json.dumps(_X509_SETTINGS))
    stale = prune_guard / "X509-cert-stale.pem"
    stale.write_bytes(b"stale")
    os.utime(stale, (500, 500))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"NEW PEM")]

    with (
        patch(f"{MODULE}.httpx.post", side_effect=responses),
        patch(f"{MODULE}.build_mongo_client") as build,
    ):
        _path, notes, verified = cert_rotation.rotate_certificate(keep=1, verify=False)

    assert verified is True
    build.assert_not_called()
    assert stale.exists()
    assert any("Skipping cert pruning" in n and "--no-verify" in n for n in notes)

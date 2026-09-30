"""Mock-tier tests for bookscraper.backends.mongo.cert_rotation's Atlas API calls.

httpx.post is mocked, so no network is touched; the filesystem side goes through
isolated_config so the real ~/.bookscrapper is never read or written.
"""

import json
import pathlib
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
    monkeypatch.setenv("ATLAS_CLIENT_ID", "client-id")
    monkeypatch.setenv("ATLAS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ATLAS_PROJECT_ID", "proj123")
    monkeypatch.setenv("ATLAS_DB_USER", "svc-user")
    monkeypatch.delenv("ATLAS_CERT_MONTHS", raising=False)
    monkeypatch.delenv("ATLAS_CERT_FILE", raising=False)


def _response(*, json_body=None, content=b"", status=200, url="https://cloud.mongodb.com/x") -> httpx.Response:
    return httpx.Response(
        status,
        json=json_body,
        content=None if json_body is not None else content,
        request=httpx.Request("POST", url),
    )


# --------------------------------------------------------------------------- #
# get_access_token / issue_certificate
# --------------------------------------------------------------------------- #
def test_get_access_token_posts_client_credentials() -> None:
    with patch(f"{MODULE}.httpx.post", return_value=_response(json_body={"access_token": "tok"})) as post:
        assert cert_rotation.get_access_token("id", "secret") == "tok"

    assert post.call_args.args[0] == cert_rotation.ATLAS_OAUTH_URL
    assert post.call_args.kwargs["auth"] == ("id", "secret")
    assert post.call_args.kwargs["data"] == {"grant_type": "client_credentials"}


def test_get_access_token_propagates_http_errors() -> None:
    with (
        patch(f"{MODULE}.httpx.post", return_value=_response(status=401, json_body={})),
        pytest.raises(httpx.HTTPStatusError),
    ):
        cert_rotation.get_access_token("id", "bad")


def test_issue_certificate_sends_months_in_json_body_not_query() -> None:
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
def test_rotate_certificate_requires_each_credential(atlas_env, monkeypatch, missing) -> None:
    monkeypatch.delenv(missing)

    with (
        patch(f"{MODULE}.httpx.post") as post,
        pytest.raises(cert_rotation.RotationError, match=missing),
    ):
        cert_rotation.rotate_certificate()

    post.assert_not_called()


def test_rotate_certificate_invalid_months_fails_before_any_request(atlas_env) -> None:
    with (
        patch(f"{MODULE}.httpx.post") as post,
        pytest.raises(cert_rotation.RotationError, match="1-24"),
    ):
        cert_rotation.rotate_certificate(months=99)

    post.assert_not_called()


def test_rotate_certificate_happy_path_writes_cert_and_updates_settings(atlas_env, isolated_config) -> None:
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


def test_rotate_certificate_explicit_arguments_beat_env(atlas_env, monkeypatch, tmp_path, isolated_config) -> None:
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
) -> None:
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    monkeypatch.setenv("ATLAS_CERT_FILE", str(tmp_path / "from-env.pem"))
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"PEM")]

    with patch(f"{MODULE}.httpx.post", side_effect=responses) as post:
        path, _, _ = cert_rotation.rotate_certificate(verify=False)

    assert path == tmp_path / "from-env.pem"
    assert post.call_args_list[1].kwargs["json"] == {"monthsUntilExpiration": 12}


def test_rotate_certificate_http_status_error_becomes_rotation_error_without_leaking_body(
    atlas_env, isolated_config
) -> None:
    denied = _response(status=403, json_body={"detail": "client-secret was rejected"})

    with (
        patch(f"{MODULE}.httpx.post", return_value=denied),
        pytest.raises(cert_rotation.RotationError) as excinfo,
    ):
        cert_rotation.rotate_certificate()

    assert "403" in str(excinfo.value)
    assert "client-secret" not in str(excinfo.value)
    assert list(pathlib.Path(isolated_config).glob("X509-cert-*.pem")) == []


def test_rotate_certificate_network_error_becomes_rotation_error(atlas_env, isolated_config) -> None:
    with (
        patch(f"{MODULE}.httpx.post", side_effect=httpx.ConnectError("no route")),
        pytest.raises(cert_rotation.RotationError, match="ConnectError"),
    ):
        cert_rotation.rotate_certificate()


def test_rotate_certificate_does_not_touch_settings_when_atlas_fails(atlas_env, isolated_config) -> None:
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
def test_verify_retries_then_succeeds() -> None:
    """
    Scenario: Atlas rejects the freshly issued cert twice while it propagates, then accepts it.
    Boundaries: build_mongo_client/ping faked; injected sleep so no real wait; client per attempt.
    On failure: verification gives up before Atlas propagates, or never retries.
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


def test_verify_failure_message_has_code_but_no_host() -> None:
    """
    Scenario: every attempt fails; the raised error keeps the redacted code but no host/URI.
    Boundaries: ping always raises a redacted MongoConnectionError; injected sleep.
    On failure: verification masks the error code, or a host/credential leaks into the message.
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


def test_verify_certificate_runs_once_when_attempts_below_one() -> None:
    """
    Scenario: attempts <= 0 still runs exactly one ping (clamped to 1), never zero.
    Boundaries: build_mongo_client/ping faked; ping succeeds so no sleep is needed.
    On failure: a mistyped --verify-attempts 0 silently skips verification.
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
def test_verify_success_then_repoints_settings(atlas_env, isolated_config) -> None:
    """
    Scenario: the new cert verifies, so settings.json is repointed at it and verified is True.
    Boundaries: Atlas HTTP + build_mongo_client/ping faked (ping succeeds).
    On failure: a verified cert is not committed, or verification is skipped.
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


def test_verify_skipped_when_no_uri_configured(atlas_env, isolated_config) -> None:
    """
    Scenario: no URI is configured, so verification is skipped (no ping) but repointing proceeds.
    Boundaries: resolver returns (None, None); build_mongo_client asserted never called.
    On failure: verification pings with no URI, or a skip blocks the repoint.
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


def test_rotate_certificate_verify_failure_leaves_settings_untouched(atlas_env, isolated_config) -> None:
    """
    Scenario: verification fails for good; settings.json is NOT repointed, the PEM stays on disk,
    and verified is False (ROT-12).
    Boundaries: ping always raises a redacted MongoConnectionError; verify_attempts=1 (no sleep).
    On failure: a broken identity is committed to settings.json.
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


def test_rotate_certificate_no_verify_skips_ping_and_repoints(atlas_env, isolated_config) -> None:
    """
    Scenario: verify=False repoints settings.json without ever building a client / pinging.
    Boundaries: build_mongo_client asserted never called.
    On failure: the offline path still contacts Atlas.
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


def test_rotate_certificate_config_error_during_verify_becomes_rotation_error(atlas_env, isolated_config) -> None:
    """
    Scenario: the resolver cannot supply a URI (ConfigError), so rotation fails loudly without
    repointing rather than pinging or guessing.
    Boundaries: resolve_mongo_connection raises ConfigError after the PEM is written.
    On failure: an unhandled ConfigError crashes, or settings.json is repointed blindly.
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

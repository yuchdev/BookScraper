"""Mock-tier tests for bookscraper.backends.mongo.cert_rotation's Atlas API calls.

httpx.post is mocked, so no network is touched; the filesystem side goes through
isolated_config so the real ~/.bookscrapper is never read or written.
"""

import json
import pathlib
from unittest.mock import patch

import httpx
import pytest

from bookscraper.backends.mongo import cert_rotation

MODULE = "bookscraper.backends.mongo.cert_rotation"


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
        path, notes = cert_rotation.rotate_certificate()

    assert path.parent == isolated_config
    assert path.read_bytes() == b"NEW PEM"
    assert post.call_args_list[1].kwargs["json"] == {"monthsUntilExpiration": 6}
    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"]["value"] == path.name
    assert path.name in notes[0]


def test_rotate_certificate_explicit_arguments_beat_env(atlas_env, monkeypatch, tmp_path, isolated_config) -> None:
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    monkeypatch.setenv("ATLAS_CERT_FILE", str(tmp_path / "from-env.pem"))
    explicit = tmp_path / "explicit.pem"
    responses = [_response(json_body={"access_token": "tok"}), _response(content=b"PEM")]

    with patch(f"{MODULE}.httpx.post", side_effect=responses) as post:
        path, _ = cert_rotation.rotate_certificate(months=3, output=explicit)

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
        path, _ = cert_rotation.rotate_certificate()

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

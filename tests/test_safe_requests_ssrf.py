"""Connection-bound SSRF tests for synchronous website and sitemap fetches."""

import datetime
import socket
import ssl
import threading

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.utils import public_http
from src.utils.security import safe_requests_get


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


def _answers(addresses, port):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, port or 80),
        )
        for address in addresses
    ]


def _response_socket(response_bytes, *, tls_context=None):
    client, server = socket.socketpair()
    observed = []

    def serve():
        try:
            if tls_context:
                connection = tls_context.wrap_socket(server, server_side=True)
            else:
                connection = server
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                observed.append(request)
                connection.sendall(response_bytes)
        except ssl.SSLError:
            # A deliberately mismatched certificate makes the client abort TLS.
            pass

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    return client, observed, worker


def test_public_http_fetch_returns_native_response_and_keeps_host(monkeypatch):
    body = b"image data"
    client, observed, worker = _response_socket(
        b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\n" + body
    )
    destination = []

    def connect(addr_info, timeout, source_address, socket_options):
        destination.append(addr_info[4])
        assert timeout == 4
        return client

    monkeypatch.setattr(public_http, "_connect_checked_sockaddr", connect)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answers(["93.184.216.34"], port),
    )
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8999")

    response = safe_requests_get("http://images.example/photo.png", timeout=4)
    worker.join(timeout=2)

    assert isinstance(response, requests.Response)
    assert response.status_code == 200
    response.raise_for_status()
    assert response.content == body
    assert response.url == "http://images.example/photo.png"
    assert destination == [("93.184.216.34", 80)]
    assert b"Host: images.example\r\n" in observed[0]


def test_public_to_private_dns_change_never_opens_socket(monkeypatch):
    dns_answers = iter([_answers(["93.184.216.34"], 80), _answers(["10.0.0.8"], 80)])
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: next(dns_answers),
    )
    calls = []
    monkeypatch.setattr(
        public_http,
        "_connect_checked_sockaddr",
        lambda *args: calls.append(args),
    )

    with pytest.raises(ValueError, match="not allowed"):
        safe_requests_get("http://images.example/photo.png")
    assert calls == []


@pytest.mark.parametrize(
    "addresses",
    [
        ["93.184.216.34", "10.0.0.8"],
        ["93.184.216.34", "::1"],
        ["2001:4860:4860::8888", "fe80::1"],
        ["169.254.169.254"],
        ["::ffff:127.0.0.1"],
    ],
)
def test_mixed_or_private_connection_answers_never_open_socket(monkeypatch, addresses):
    # Let the URL preflight pass, then exercise the connection boundary.
    dns_answers = iter([_answers(["93.184.216.34"], 80), _answers(addresses, 80)])
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: next(dns_answers),
    )
    calls = []
    monkeypatch.setattr(
        public_http,
        "_connect_checked_sockaddr",
        lambda *args: calls.append(args),
    )

    with pytest.raises(ValueError, match="not allowed"):
        safe_requests_get("http://images.example/photo.png")
    assert calls == []


def test_redirects_are_bounded_and_private_location_is_refused(monkeypatch):
    client, observed, worker = _response_socket(
        b"HTTP/1.1 302 Found\r\n"
        b"Location: http://169.254.169.254/secret\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    calls = []

    def connect(addr_info, *args):
        calls.append(addr_info[4])
        return client

    monkeypatch.setattr(public_http, "_connect_checked_sockaddr", connect)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answers(
            ["169.254.169.254" if host == "169.254.169.254" else "93.184.216.34"],
            port,
        ),
    )

    with pytest.raises(ValueError, match="not allowed"):
        safe_requests_get("http://images.example/start")
    worker.join(timeout=2)

    assert calls == [("93.184.216.34", 80)]
    assert b"Host: images.example\r\n" in observed[0]


def test_public_relative_redirect_fetches_final_response(monkeypatch):
    first_client, first_request, first_worker = _response_socket(
        b"HTTP/1.1 302 Found\r\nLocation: /final.png\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    second_client, second_request, second_worker = _response_socket(
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nimage"
    )
    clients = iter([first_client, second_client])
    connected = []

    def connect(addr_info, *args):
        connected.append(addr_info[4])
        return next(clients)

    monkeypatch.setattr(public_http, "_connect_checked_sockaddr", connect)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answers(["93.184.216.34"], port),
    )

    response = safe_requests_get("http://images.example/start", max_redirects=1)
    first_worker.join(timeout=2)
    second_worker.join(timeout=2)

    assert response.content == b"image"
    assert response.url == "http://images.example/final.png"
    assert connected == [("93.184.216.34", 80), ("93.184.216.34", 80)]
    assert b"GET /start HTTP/1.1\r\n" in first_request[0]
    assert b"GET /final.png HTTP/1.1\r\n" in second_request[0]


def test_cross_origin_redirect_drops_request_and_response_credentials(monkeypatch):
    first_client, first_request, first_worker = _response_socket(
        b"HTTP/1.1 302 Found\r\nLocation: http://other.example/final\r\n"
        b"Set-Cookie: session=from-first-origin; Path=/\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    second_client, second_request, second_worker = _response_socket(
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
    )
    clients = iter([first_client, second_client])
    monkeypatch.setattr(
        public_http, "_connect_checked_sockaddr", lambda *args: next(clients)
    )
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answers(["93.184.216.34"], port),
    )

    response = safe_requests_get(
        "http://images.example/start",
        headers={"Authorization": "Bearer original", "Cookie": "manual=secret"},
        cookies={"session": "caller-secret"},
    )
    first_worker.join(timeout=2)
    second_worker.join(timeout=2)

    assert response.content == b"OK"
    assert b"Authorization: Bearer original\r\n" in first_request[0]
    assert b"Cookie:" in first_request[0]
    assert b"Authorization:" not in second_request[0]
    assert b"Cookie:" not in second_request[0]


def test_requests_allow_redirects_false_returns_native_redirect(monkeypatch):
    client, _, worker = _response_socket(
        b"HTTP/1.1 302 Found\r\nLocation: /next\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    monkeypatch.setattr(public_http, "_connect_checked_sockaddr", lambda *args: client)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answers(["93.184.216.34"], port),
    )

    response = safe_requests_get("http://images.example/start", allow_redirects=False)
    worker.join(timeout=2)

    assert response.status_code == 302
    assert response.headers["Location"] == "/next"
    assert response.history == []


def test_checked_sockaddr_connects_without_second_dns_lookup(monkeypatch):
    class Socket:
        def __init__(self):
            self.destination = None
            self.timeout = None
            self.options = []

        def setsockopt(self, *option):
            self.options.append(option)

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, destination):
            self.destination = destination

        def close(self):
            raise AssertionError("successful socket must remain open")

    fake = Socket()
    with monkeypatch.context() as patcher:
        patcher.setattr(public_http.socket, "socket", lambda *args: fake)
        patcher.setattr(
            public_http.socket,
            "getaddrinfo",
            lambda *args, **kwargs: pytest.fail("unexpected second DNS lookup"),
        )
        result = public_http._connect_checked_sockaddr(
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2001:4860:4860::8888", 443, 0, 0),
            ),
            5,
            None,
            [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )

    assert result is fake
    assert fake.destination == ("2001:4860:4860::8888", 443, 0, 0)
    assert fake.timeout == 5
    assert fake.options == [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]


def test_sync_connection_tries_another_checked_public_answer(monkeypatch):
    addresses = _answers(["2001:4860:4860::8888", "93.184.216.34"], 443)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: addresses,
    )
    destinations = []
    result_socket = object()

    def connect(addr_info, *args):
        destinations.append(addr_info[4][0])
        if len(destinations) == 1:
            raise OSError("IPv6 route unavailable")
        return result_socket

    monkeypatch.setattr(public_http, "_connect_checked_sockaddr", connect)

    connection = public_http.PublicOnlyHTTPSConnection("images.example", 443)
    result = connection._new_conn()

    assert result is result_socket
    assert destinations == ["2001:4860:4860::8888", "93.184.216.34"]


def _tls_cert(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "images.example")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        )
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("images.example")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    return context, cert_path


@pytest.mark.parametrize(
    "host,valid", [("images.example", True), ("other.example", False)]
)
def test_https_checks_original_hostname(monkeypatch, tmp_path, host, valid):
    tls_context, cert_path = _tls_cert(tmp_path)
    client, observed, worker = _response_socket(
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nimage",
        tls_context=tls_context,
    )
    monkeypatch.setattr(public_http, "_connect_checked_sockaddr", lambda *args: client)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answers(["93.184.216.34"], port),
    )
    monkeypatch.setattr(requests.adapters, "DEFAULT_CA_BUNDLE_PATH", str(cert_path))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8999")

    if valid:
        response = safe_requests_get(f"https://{host}/photo.png")
        assert response.content == b"image"
        assert b"Host: images.example\r\n" in observed[0]
    else:
        with pytest.raises(requests.exceptions.SSLError):
            safe_requests_get(f"https://{host}/photo.png")
    worker.join(timeout=2)


def test_tls_verification_and_explicit_proxy_cannot_be_disabled():
    with pytest.raises(ValueError, match="TLS verification"):
        safe_requests_get("https://images.example/photo.png", verify=False)
    with pytest.raises(ValueError, match="Proxies"):
        safe_requests_get(
            "http://images.example/photo.png", proxies={"http": "http://proxy.example"}
        )

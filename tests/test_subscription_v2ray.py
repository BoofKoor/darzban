"""PR 1 / Track A — structured field propagation regression + new-fidelity.

Covers:
- Byte-for-byte invariance: leaving the new kwargs at their defaults
  (None) must produce identical output to the pre-change code paths.
- New fidelity: passing operator-defined pcs/vcn/xhttp_extra/
  downloadSettings emits them with v26.2.6 spellings in both the
  v2ray base64 share link query and the v2ray-json outbound TLS /
  xhttp settings.
- Parse layer: ``XRayConfig._resolve_inbounds`` now stores the four
  new fields on ``inbounds_by_tag`` (absent → key absent / None).
"""

import json
import urllib.parse as urlparse
import uuid

import pytest

from app.subscription.v2ray import V2rayJsonConfig, V2rayShareLink
from app.xray.config import XRayConfig


REMARK = "node1"
ADDR = "1.2.3.4"
PORT = 443
UID = str(uuid.UUID(int=1))
PWD = "trojan-secret"
SNI = "example.com"
PBK = "pbk-stub"
SID = "0123"


# --- byte-for-byte regression: defaults unchanged --------------------


def _vless_args(**overrides):
    base = dict(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="xhttp", path="/foo", host="example.com",
        type="", flow="xtls-rprx-vision",
        tls="reality", sni=SNI, fp="chrome", pbk=PBK, sid=SID, spx="",
    )
    base.update(overrides)
    return base


def test_vless_xhttp_reality_no_modern_fields_byte_for_byte():
    # Calling with no new kwargs and with new kwargs explicitly None must
    # produce identical output. This locks the "absent → key omitted"
    # contract for every new branch.
    no_kwargs = V2rayShareLink.vless(**_vless_args())
    explicit_none = V2rayShareLink.vless(
        **_vless_args(),
        pcs=None, vcn=None, xhttp_extra=None, downloadSettings=None,
    )
    assert no_kwargs == explicit_none

    # And the URL must NOT contain encryption / pcs / vcn / mldsa65 today.
    q = urlparse.urlparse(no_kwargs).query
    params = dict(urlparse.parse_qsl(q))
    assert "encryption" not in params
    assert "pcs" not in params
    assert "vcn" not in params
    assert "mldsa65" not in params

    # extra JSON must contain only the synthesized keys (no operator keys).
    extra = json.loads(params["extra"])
    assert set(extra.keys()) == {
        "scMaxEachPostBytes", "scMaxConcurrentPosts",
        "scMinPostsIntervalMs", "xPaddingBytes", "noGRPCHeader",
    }
    assert "downloadSettings" not in extra


def test_vmess_tls_no_modern_fields_byte_for_byte():
    no_kwargs = V2rayShareLink.vmess(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="ws", path="/", host="example.com", tls="tls",
        sni=SNI, fp="chrome",
    )
    explicit_none = V2rayShareLink.vmess(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="ws", path="/", host="example.com", tls="tls",
        sni=SNI, fp="chrome",
        pcs=None, vcn=None, xhttp_extra=None, downloadSettings=None,
    )
    assert no_kwargs == explicit_none


def test_trojan_tls_no_modern_fields_byte_for_byte():
    no_kwargs = V2rayShareLink.trojan(
        remark=REMARK, address=ADDR, port=PORT, password=PWD,
        net="ws", path="/", host="example.com", tls="tls", sni=SNI, fp="chrome",
    )
    explicit_none = V2rayShareLink.trojan(
        remark=REMARK, address=ADDR, port=PORT, password=PWD,
        net="ws", path="/", host="example.com", tls="tls", sni=SNI, fp="chrome",
        pcs=None, vcn=None, xhttp_extra=None, downloadSettings=None,
    )
    assert no_kwargs == explicit_none


# --- new-fidelity: pcs / vcn appear in TLS URL ----------------------


def test_vless_tls_emits_pcs_vcn():
    link = V2rayShareLink.vless(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="ws", path="/", host="example.com", tls="tls",
        sni=SNI, fp="chrome",
        pcs=["sha256-aaaa", "sha256-bbbb"],
        vcn="leaf.example.com",
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    assert params["pcs"] == "sha256-aaaa,sha256-bbbb"
    assert params["vcn"] == "leaf.example.com"


def test_trojan_tls_emits_pcs_vcn_string_form():
    link = V2rayShareLink.trojan(
        remark=REMARK, address=ADDR, port=PORT, password=PWD,
        net="ws", path="/", host="example.com", tls="tls", sni=SNI, fp="chrome",
        pcs="sha256-aaaa",
        vcn=["leaf-a", "leaf-b"],
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    assert params["pcs"] == "sha256-aaaa"
    assert params["vcn"] == "leaf-a,leaf-b"


def test_pcs_vcn_only_on_tls_not_reality():
    # REALITY links must NOT pick up cert-pinning params.
    link = V2rayShareLink.vless(
        **_vless_args(),
        pcs="sha256-aaaa", vcn="leaf-a",
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    assert "pcs" not in params
    assert "vcn" not in params


# --- new-fidelity: xhttp_extra + downloadSettings -------------------


def test_vless_xhttp_operator_extra_merges_and_wins():
    link = V2rayShareLink.vless(
        **_vless_args(),
        xhttp_extra={
            "xPaddingBytes": "200-2000",   # operator overrides default 100-1000
            "headers": {"X-Test": "yes"},  # new key
        },
        downloadSettings={"address": "2.2.2.2", "port": 8443},
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    extra = json.loads(params["extra"])
    assert extra["xPaddingBytes"] == "200-2000"
    assert extra["headers"] == {"X-Test": "yes"}
    assert extra["downloadSettings"] == {"address": "2.2.2.2", "port": 8443}
    # Synthesized defaults still present for keys the operator didn't override.
    assert extra["scMaxEachPostBytes"] == 1000000


def test_vmess_xhttp_operator_extra_merges_into_dict_payload():
    link = V2rayShareLink.vmess(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="xhttp", path="/foo", host="example.com",
        tls="tls", sni=SNI, fp="chrome",
        xhttp_extra={"xPaddingBytes": "200-2000"},
        downloadSettings={"address": "dl.example.com"},
    )
    # vmess link is base64-encoded JSON.
    import base64
    body = link.removeprefix("vmess://")
    payload = json.loads(base64.b64decode(body).decode())
    # In vmess the share link stores `extra` as a raw dict (not a string).
    assert payload["extra"]["xPaddingBytes"] == "200-2000"
    assert payload["extra"]["downloadSettings"] == {"address": "dl.example.com"}
    # Operator-only overrides; defaults retained.
    assert payload["extra"]["scMaxEachPostBytes"] == 1000000


# --- v2ray-json outbound ---------------------------------------------


def test_tls_config_emits_pcs_vcn_as_lists():
    out = V2rayJsonConfig.tls_config(
        sni=SNI, fp="chrome", ais=False,
        pcs="sha256-aaaa",
        vcn=["leaf-a"],
    )
    assert out["pinnedPeerCertSha256"] == ["sha256-aaaa"]
    assert out["verifyPeerCertByName"] == ["leaf-a"]


def test_tls_config_omits_pcs_vcn_when_absent():
    out = V2rayJsonConfig.tls_config(sni=SNI, fp="chrome", ais=False)
    assert "pinnedPeerCertSha256" not in out
    assert "verifyPeerCertByName" not in out


def test_splithttp_config_merges_extra_and_download_settings():
    cfg = V2rayJsonConfig().splithttp_config(
        path="/foo", host="example.com",
        xhttp_extra={"headers": {"X-Test": "yes"}},
        downloadSettings={"address": "2.2.2.2"},
    )
    assert cfg["extra"] == {"headers": {"X-Test": "yes"}}
    assert cfg["downloadSettings"] == {"address": "2.2.2.2"}


def test_splithttp_config_omits_extra_and_download_when_absent():
    cfg = V2rayJsonConfig().splithttp_config(path="/foo", host="example.com")
    assert "extra" not in cfg
    assert "downloadSettings" not in cfg


# --- parse layer -----------------------------------------------------


def _write_config(tmp_path, inbound: dict):
    full = {
        "log": {"loglevel": "warning"},
        "inbounds": [inbound],
        "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}],
    }
    p = tmp_path / "xray.json"
    p.write_text(json.dumps(full))
    return str(p)


def test_resolve_inbounds_parses_pcs_vcn(tmp_path):
    inbound = {
        "tag": "vless-tls",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": "ws",
            "security": "tls",
            "tlsSettings": {
                "pinnedPeerCertSha256": ["sha256-aaaa"],
                "verifyPeerCertByName": ["leaf-a"],
            },
            "wsSettings": {"path": "/"},
        },
    }
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=18081)
    parsed = cfg.inbounds_by_tag["vless-tls"]
    assert parsed["pcs"] == ["sha256-aaaa"]
    assert parsed["vcn"] == ["leaf-a"]


def test_resolve_inbounds_parses_xhttp_extra_and_download(tmp_path):
    inbound = {
        "tag": "vless-xhttp",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": "xhttp",
            "security": "tls",
            "tlsSettings": {},
            "xhttpSettings": {
                "path": "/foo",
                "host": "example.com",
                "extra": {"headers": {"X-Test": "yes"}},
                "downloadSettings": {"address": "dl.example.com"},
            },
        },
    }
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=18082)
    parsed = cfg.inbounds_by_tag["vless-xhttp"]
    assert parsed["xhttp_extra"] == {"headers": {"X-Test": "yes"}}
    assert parsed["downloadSettings"] == {"address": "dl.example.com"}


def test_resolve_inbounds_absent_fields_are_none(tmp_path):
    inbound = {
        "tag": "vless-tls-bare",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": "ws",
            "security": "tls",
            "tlsSettings": {},
            "wsSettings": {"path": "/"},
        },
    }
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=18083)
    parsed = cfg.inbounds_by_tag["vless-tls-bare"]
    assert parsed["pcs"] is None
    assert parsed["vcn"] is None


@pytest.mark.parametrize("net", ["xhttp", "splithttp"])
def test_resolve_inbounds_xhttp_and_splithttp_both_parsed(tmp_path, net):
    inbound = {
        "tag": f"vless-{net}",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": net,
            "security": "tls",
            "tlsSettings": {},
            f"{net}Settings": {
                "path": "/foo",
                "host": "example.com",
                "extra": {"k": "v"},
                "downloadSettings": {"address": "dl"},
            },
        },
    }
    port = 18090 + (1 if net == "splithttp" else 0)
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=port)
    parsed = cfg.inbounds_by_tag[f"vless-{net}"]
    assert parsed["xhttp_extra"] == {"k": "v"}
    assert parsed["downloadSettings"] == {"address": "dl"}

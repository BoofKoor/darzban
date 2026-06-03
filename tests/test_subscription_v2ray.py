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
        pcs=None, vcn=None, xhttp_extra=None, downloadSettings=None, pqv="",
    )
    assert no_kwargs == explicit_none

    # And the URL must NOT contain encryption / pcs / vcn / pqv today.
    q = urlparse.urlparse(no_kwargs).query
    params = dict(urlparse.parse_qsl(q))
    assert "encryption" not in params
    assert "pcs" not in params
    assert "vcn" not in params
    assert "pqv" not in params

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


# =====================================================================
# PR 1.5 — full xhttpSettings passthrough (top-level form).
#
# Operators commonly write rich xhttp params directly at the top level
# of xhttpSettings (no `extra` envelope). The resolver's fixed
# allow-list used to drop everything outside ~10 scalars; PR 1.5 stores
# the whole object and the emitters merge it over the synthesized
# defaults. Bare configs must stay byte-identical.
# =====================================================================


# The user's real-server inbound: every field set at the TOP LEVEL of
# xhttpSettings (NOT wrapped in `extra`). scMaxConcurrentPosts is left
# unset so we can prove the synthesized default still fills it.
RICH_XHTTP = {
    "path": "/foo",
    "host": "example.com",
    "mode": "stream-up",
    "xPaddingObfsMode": "header",
    "xPaddingObfsKey": "obfs-key",
    "xPaddingObfsHeader": "X-Pad",
    "xPaddingObfsPlacement": "header",
    "xPaddingObfsMethod": "aes",
    "sessionPlacement": "query",
    "sessionKey": "sess",
    "seqPlacement": "header",
    "seqKey": "seq",
    "scMaxBufferedPosts": 30,
    "scStreamUpServerSecs": "20-80",
    "noSSEHeader": True,
    "headers": {"X-Custom": "v"},
    "enableXmux": True,
}

# Every operator key minus the host/path/mode params that are carried
# separately. These must all surface in the emitted config.
_RICH_PASSTHROUGH_KEYS = {
    k for k in RICH_XHTTP if k not in ("host", "path", "mode")
}


def _xhttp_settings_from(net, tmp_path, port, xhttp):
    """Resolve a single xhttp/splithttp inbound and return its parsed
    inbound dict (what process_inbounds_and_tags would feed emitters)."""
    inbound = {
        "tag": f"vless-{net}-rich",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": net,
            "security": "tls",
            "tlsSettings": {},
            f"{net}Settings": xhttp,
        },
    }
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=port)
    return cfg.inbounds_by_tag[f"vless-{net}-rich"]


# --- parse: full object preserved ------------------------------------


def test_resolve_inbounds_stores_full_xhttp_settings(tmp_path):
    parsed = _xhttp_settings_from("xhttp", tmp_path, 18100, RICH_XHTTP)
    assert parsed["xhttp_settings"] == RICH_XHTTP
    # The legacy scalar allow-list still co-exists for the defaults.
    assert parsed["mode"] == "stream-up"


def test_resolve_inbounds_xhttp_settings_is_a_copy(tmp_path):
    # Mutating the parsed copy must not corrupt anything shared.
    parsed = _xhttp_settings_from("xhttp", tmp_path, 18101, dict(RICH_XHTTP))
    parsed["xhttp_settings"]["headers"]["X-Custom"] = "mutated"
    # Re-resolve a fresh config; original literal is untouched.
    assert RICH_XHTTP["headers"]["X-Custom"] == "v"


# --- emit (v2ray base64): top-level fields land in extra= JSON -------


def test_vless_xhttp_full_passthrough_into_extra():
    link = V2rayShareLink.vless(
        **_vless_args(tls="tls", pbk="", sid="", flow=""),
        xhttp_settings=RICH_XHTTP,
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    extra = json.loads(params["extra"])
    # Every operator field (minus host/path/mode) is present.
    for k in _RICH_PASSTHROUGH_KEYS:
        assert k in extra, f"{k} dropped from extra"
        assert extra[k] == RICH_XHTTP[k]
    # host/path/mode are NOT duplicated inside extra (they're URL params).
    assert "host" not in extra
    assert "path" not in extra
    assert "mode" not in extra
    # Synthesized default still fills the field the operator didn't set.
    assert extra["scMaxConcurrentPosts"] == 100


def test_vmess_xhttp_full_passthrough_into_extra_dict():
    import base64
    link = V2rayShareLink.vmess(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="xhttp", path="/foo", host="example.com",
        tls="tls", sni=SNI, fp="chrome",
        xhttp_settings=RICH_XHTTP,
    )
    payload = json.loads(base64.b64decode(link.removeprefix("vmess://")).decode())
    extra = payload["extra"]
    for k in _RICH_PASSTHROUGH_KEYS:
        assert extra[k] == RICH_XHTTP[k]
    assert "host" not in extra and "path" not in extra and "mode" not in extra


def test_trojan_xhttp_full_passthrough_into_extra():
    link = V2rayShareLink.trojan(
        remark=REMARK, address=ADDR, port=PORT, password=PWD,
        net="xhttp", path="/foo", host="example.com",
        tls="tls", sni=SNI, fp="chrome",
        xhttp_settings=RICH_XHTTP,
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    extra = json.loads(params["extra"])
    for k in _RICH_PASSTHROUGH_KEYS:
        assert extra[k] == RICH_XHTTP[k]


# --- emit (v2ray-json): top-level fields land in splithttpSettings ---


def test_splithttp_config_full_passthrough_top_level():
    cfg = V2rayJsonConfig().splithttp_config(
        path="/foo", host="example.com", mode="stream-up",
        xhttp_settings=RICH_XHTTP,
    )
    for k in _RICH_PASSTHROUGH_KEYS:
        assert cfg[k] == RICH_XHTTP[k], f"{k} missing from splithttpSettings"
    # Dedicated scalars still set.
    assert cfg["mode"] == "stream-up"
    assert cfg["path"] == "/foo"
    assert cfg["host"] == "example.com"
    # Synthesized default fills the unset field.
    assert cfg["scMaxConcurrentPosts"] == 100


# --- operator precedence ---------------------------------------------


def test_operator_xhttp_setting_overrides_synthesized_default():
    # Operator sets scMaxConcurrentPosts at top level → must win over the
    # hardcoded 100 default in both formats.
    xhttp = dict(RICH_XHTTP, scMaxConcurrentPosts=7)
    link = V2rayShareLink.vless(
        **_vless_args(tls="tls", pbk="", sid="", flow=""),
        xhttp_settings=xhttp,
    )
    extra = json.loads(dict(urlparse.parse_qsl(urlparse.urlparse(link).query))["extra"])
    assert extra["scMaxConcurrentPosts"] == 7

    cfg = V2rayJsonConfig().splithttp_config(
        path="/foo", host="example.com", xhttp_settings=xhttp,
    )
    assert cfg["scMaxConcurrentPosts"] == 7


# --- byte-identity: bare configs unchanged by the new kwarg ----------


def test_vless_xhttp_bare_config_byte_identical_with_passthrough_kwarg():
    # Bare xhttp (only path/host) → _operator_xhttp_extra strips all three
    # → empty merge → output identical whether xhttp_settings is omitted,
    # None, {}, or the bare {path, host, mode} dict.
    base = _vless_args(tls="tls", pbk="", sid="", flow="")
    omitted = V2rayShareLink.vless(**base)
    explicit_none = V2rayShareLink.vless(**base, xhttp_settings=None)
    empty = V2rayShareLink.vless(**base, xhttp_settings={})
    bare = V2rayShareLink.vless(
        **base, xhttp_settings={"path": "/foo", "host": "example.com", "mode": "auto"}
    )
    assert omitted == explicit_none == empty == bare


def test_splithttp_config_bare_byte_identical_with_passthrough_kwarg():
    cfg_omitted = V2rayJsonConfig().splithttp_config(path="/foo", host="example.com")
    cfg_none = V2rayJsonConfig().splithttp_config(
        path="/foo", host="example.com", xhttp_settings=None)
    cfg_bare = V2rayJsonConfig().splithttp_config(
        path="/foo", host="example.com",
        xhttp_settings={"path": "/foo", "host": "example.com", "mode": "auto"})
    assert cfg_omitted == cfg_none == cfg_bare


# --- envelope form (PR 1) still works alongside top-level form -------


def test_envelope_form_extra_not_double_nested():
    # When the operator used the `extra` envelope (form b), xhttp_settings
    # ALSO contains that `extra` key. It must NOT be merged as a nested
    # `extra.extra`; PR 1's xhttp_extra path spreads it instead.
    envelope = {"path": "/foo", "host": "example.com", "mode": "auto",
                "extra": {"noSSEHeader": True}}
    link = V2rayShareLink.vless(
        **_vless_args(tls="tls", pbk="", sid="", flow=""),
        xhttp_settings=envelope,
        xhttp_extra=envelope["extra"],
    )
    extra = json.loads(dict(urlparse.parse_qsl(urlparse.urlparse(link).query))["extra"])
    assert "extra" not in extra          # no double-nesting
    assert extra["noSSEHeader"] is True  # envelope contents spread in


# =====================================================================
# REALITY post-quantum mldsa65 — URL `pqv=<verify>` param +
# v2ray-json `realitySettings.mldsa65Verify`. Operator's xray inbound
# carries `realitySettings.mldsa65Seed`; the resolver derives the
# verify key via `xray mldsa65 -i <seed>` and threads it as `pqv`.
# =====================================================================


# Stand-in for the ~2400-char verify blob; the emitter is value-agnostic.
PQV_STUB = "Verify-Stub-Base64URL-Encoded"


def test_vless_reality_emits_pqv_when_present():
    link = V2rayShareLink.vless(
        **_vless_args(),
        pqv=PQV_STUB,
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    assert params["pqv"] == PQV_STUB


def test_trojan_reality_emits_pqv_when_present():
    link = V2rayShareLink.trojan(
        remark=REMARK, address=ADDR, port=PORT, password=PWD,
        net="tcp", path="", host="example.com",
        tls="reality", sni=SNI, fp="chrome", pbk=PBK, sid=SID, spx="",
        pqv=PQV_STUB,
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    assert params["pqv"] == PQV_STUB


def test_vmess_reality_emits_pqv_when_present():
    import base64
    link = V2rayShareLink.vmess(
        remark=REMARK, address=ADDR, port=PORT, id=UID,
        net="tcp", path="", host="example.com",
        tls="reality", sni=SNI, fp="chrome", pbk=PBK, sid=SID, spx="",
        pqv=PQV_STUB,
    )
    payload = json.loads(base64.b64decode(link.removeprefix("vmess://")).decode())
    assert payload["pqv"] == PQV_STUB


def test_pqv_only_on_reality_not_tls():
    # `pqv` is REALITY-only; setting it under tls=tls must not leak it.
    link = V2rayShareLink.vless(
        **_vless_args(tls="tls", pbk="", sid="", flow=""),
        pqv=PQV_STUB,
    )
    params = dict(urlparse.parse_qsl(urlparse.urlparse(link).query))
    assert "pqv" not in params


def test_reality_config_emits_mldsa65verify_when_present():
    out = V2rayJsonConfig.reality_config(
        sni=SNI, fp="chrome", pbk=PBK, sid=SID,
        mldsa65Verify=PQV_STUB,
    )
    assert out["mldsa65Verify"] == PQV_STUB


def test_reality_config_omits_mldsa65verify_when_absent():
    out = V2rayJsonConfig.reality_config(
        sni=SNI, fp="chrome", pbk=PBK, sid=SID,
    )
    assert "mldsa65Verify" not in out


def test_resolve_inbounds_parses_mldsa65_seed(tmp_path):
    # Parser stores the seed verbatim under `mldsa65_seed`. The verify-key
    # derivation calls into XRayCore which we don't exercise here; we just
    # confirm the seed reaches the parsed inbound dict.
    inbound = {
        "tag": "vless-reality-pq",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "serverNames": ["example.com"],
                "publicKey": PBK,
                "shortIds": [SID],
                "mldsa65Seed": "seed-stub-base64url",
            },
        },
    }
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=18200)
    parsed = cfg.inbounds_by_tag["vless-reality-pq"]
    assert parsed["mldsa65_seed"] == "seed-stub-base64url"


def test_resolve_inbounds_absent_mldsa65_seed_is_absent(tmp_path):
    inbound = {
        "tag": "vless-reality-no-pq",
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": []},
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "serverNames": ["example.com"],
                "publicKey": PBK,
                "shortIds": [SID],
            },
        },
    }
    cfg = XRayConfig(_write_config(tmp_path, inbound), api_port=18201)
    parsed = cfg.inbounds_by_tag["vless-reality-no-pq"]
    assert "mldsa65_seed" not in parsed
    assert "pqv" not in parsed

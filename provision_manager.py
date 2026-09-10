"""
provision_manager.py — single auto-provisioning entry point for
special bot users across all enabled protocols.

Naming convention:
    <Prefix>_ID<first2>_<last2>
where first2/last2 are the first/last 2 digits of the Telegram user ID.

Example: TG-ID 8288584609 + protocol vless → "Vless_ID82_09".

PROTOCOL_PREFIX:
    vless     → Vless
    hysteria2 → Hys
    mtproto   → Mtp
    tuic      → Tuic
    anytls    → Any
    xhttp     → Xh
    mieru     → Mieru

NaiveProxy is not part of provisioning — it is a single-credentials model;
per-user isolation would need a serious Caddyfile refactor and is not
solved by a single add_client.

VLESS-Reality supports two sources:
    1. 3x-ui API, if `/xui_setup` is enabled.
    2. Legacy host-Xray (`vless_config.json` + `/usr/local/etc/xray/config.json`),
       if 3x-ui is not configured but VLESS is enabled in `vless_manager`.
"""

from typing import Dict, List, Optional
import logging

import hysteria2_manager
import mtproto_manager
import tuic_manager
import anytls_manager
import xhttp_manager
import mieru_manager
import vless_manager

logger = logging.getLogger(__name__)

PROTOCOL_PREFIX: Dict[str, str] = {
    "vless": "Vless",
    "hysteria2": "Hys",
    "mtproto": "Mtp",
    "tuic": "Tuic",
    "anytls": "Any",
    "xhttp": "Xh",
    "mieru": "Mieru",
}

# Managers that already expose add_client/remove_client/get_client/list_clients.
# VLESS is handled separately: 3x-ui first, then legacy host-Xray.
_SIMPLE_MANAGERS = {
    "hysteria2": hysteria2_manager,
    "mtproto": mtproto_manager,
    "tuic": tuic_manager,
    "anytls": anytls_manager,
    "xhttp": xhttp_manager,
    "mieru": mieru_manager,
}


def _xui_enabled() -> bool:
    """True if VLESS should be provisioned via the 3x-ui REST API."""
    try:
        import xui_manager
        return bool(xui_manager.is_enabled())
    except Exception as exc:
        logger.warning("provision_manager: xui_manager.is_enabled() failed: %s", exc)
        return False


def _legacy_vless_enabled() -> bool:
    """True if VLESS is available via legacy `vless_manager`/host-Xray."""
    try:
        return bool(vless_manager.is_vless_enabled())
    except Exception as exc:
        logger.warning(
            "provision_manager: vless_manager.is_vless_enabled() failed: %s",
            exc,
        )
        return False


def make_client_name(protocol: str, telegram_id: int) -> str:
    """Canonical client name: <Prefix>_ID<first2>_<last2>."""
    prefix = PROTOCOL_PREFIX.get(protocol)
    if not prefix:
        raise ValueError(f"unknown protocol for naming: {protocol}")
    s = str(int(telegram_id))
    if len(s) < 4:
        s = s.zfill(4)
    return f"{prefix}_ID{s[:2]}_{s[-2:]}"


def legacy_client_name(protocol: str, telegram_id: int) -> str:
    """Old /user-flow name: ``Vless52...49``, ``Hysteria252...49``.

    The name really does contain three dots — that is how handlers built it historically.
    """
    legacy_prefix = {
        "vless": "Vless",
        "hysteria2": "Hysteria2",
        "mtproto": "Mtproto",
        "tuic": "Tuic",
        "anytls": "Anytls",
        "xhttp": "Xhttp",
        "mieru": "Mieru",
    }.get(protocol)
    if not legacy_prefix:
        raise ValueError(f"unknown protocol for legacy naming: {protocol}")
    s = str(int(telegram_id))
    suffix = s if len(s) <= 4 else f"{s[:2]}...{s[-2:]}"
    return f"{legacy_prefix}{suffix}"


def client_name_candidates(protocol: str, telegram_id: int) -> List[str]:
    """Canonical + legacy (+ full-id variants) for finding already issued clients."""
    names: List[str] = []
    try:
        names.append(make_client_name(protocol, telegram_id))
    except ValueError:
        pass
    try:
        legacy = legacy_client_name(protocol, telegram_id)
        if legacy not in names:
            names.append(legacy)
    except ValueError:
        pass
    # Sometimes the client was created with the full TG-ID as a suffix.
    s = str(int(telegram_id))
    full_prefix = {
        "vless": "Vless",
        "hysteria2": "Hysteria2",
        "mtproto": "Mtproto",
        "tuic": "Tuic",
        "anytls": "Anytls",
        "xhttp": "Xhttp",
        "mieru": "Mieru",
    }.get(protocol)
    if full_prefix:
        full = f"{full_prefix}{s}"
        if full not in names:
            names.append(full)
    return names


def list_enabled_protocols() -> List[str]:
    """Protocol keys whose is_enabled() is True.

    Order: vless, hysteria2, mtproto, tuic, anytls, xhttp, mieru.
    """
    enabled: List[str] = []
    # VLESS: 3x-ui first, otherwise legacy host-Xray.
    if _xui_enabled() or _legacy_vless_enabled():
        enabled.append("vless")
    # Simple managers
    for proto in ("hysteria2", "mtproto", "tuic", "anytls", "xhttp", "mieru"):
        mgr = _SIMPLE_MANAGERS.get(proto)
        if not mgr:
            continue
        try:
            if mgr.is_enabled():
                enabled.append(proto)
        except Exception as exc:
            logger.warning(
                "provision_manager: %s.is_enabled() failed: %s", proto, exc
            )
    return enabled


def _gen_uri(protocol: str, client_name: str, client_dict: Dict) -> str:
    """Generate a URI/link for the client if the manager has a method for it."""
    try:
        if protocol == "vless":
            ok, _msg, uri = vless_manager.generate_client_link(client_name)
            return uri if ok else ""
        if protocol == "mtproto":
            secret = client_dict.get("secret") or ""
            if not secret:
                return ""
            return mtproto_manager.generate_tg_link(secret)
        mgr = _SIMPLE_MANAGERS.get(protocol)
        if not mgr:
            return ""
        gen = getattr(mgr, "generate_client_uri", None)
        if not gen:
            return ""
        ok, _msg, uri = gen(client_name)
        return uri if ok else ""
    except Exception as exc:
        logger.warning(
            "provision_manager: gen_uri %s/%s failed: %s",
            protocol, client_name, exc,
        )
        return ""


def _apply_legacy_vless_runtime(message: str) -> tuple[bool, str]:
    """Write legacy Xray config to host and restart xray."""
    try:
        apply_ok, apply_msg = vless_manager.apply_xray_config()
    except Exception as exc:
        apply_ok, apply_msg = False, f"{type(exc).__name__}: {exc}"
    message = f"{message}; {apply_msg}"
    if not apply_ok:
        return False, message

    try:
        restart_ok, restart_msg = vless_manager.restart_xray()
    except Exception as exc:
        restart_ok, restart_msg = False, f"{type(exc).__name__}: {exc}"
    message = f"{message}; {restart_msg}"
    return bool(restart_ok), message


def _legacy_vless_provision(client_name: str) -> Dict:
    """Idempotently create/find a VLESS client in legacy host-Xray."""
    existing = None
    try:
        existing = vless_manager.get_client(client_name)
    except Exception as exc:
        logger.warning(
            "provision_manager: vless.get_client(%s) failed: %s",
            client_name,
            exc,
        )
    if existing:
        ok, msg = _apply_legacy_vless_runtime("exists (legacy Xray)")
        return {
            "ok": ok,
            "message": msg,
            "client_name": client_name,
            "uri": _gen_uri("vless", client_name, existing) if ok else "",
            "existed": True,
            "source": "legacy_xray",
        }

    try:
        ok, msg, client = vless_manager.add_client(client_name, None)
    except Exception as exc:
        return {
            "ok": False,
            "message": f"{type(exc).__name__}: {exc}",
            "client_name": client_name,
            "uri": "",
            "existed": False,
            "source": "legacy_xray",
        }

    if ok:
        ok, msg = _apply_legacy_vless_runtime(msg)

    return {
        "ok": ok,
        "message": msg,
        "client_name": client_name,
        "uri": _gen_uri("vless", client_name, client) if ok else "",
        "existed": False,
        "source": "legacy_xray",
    }


def _legacy_vless_profile(client_name: str) -> Dict:
    """Read-only lookup of a VLESS client in legacy host-Xray."""
    existing = None
    try:
        existing = vless_manager.get_client(client_name)
    except Exception as exc:
        logger.warning(
            "provision_manager: vless.get_client(%s) failed: %s",
            client_name,
            exc,
        )
    return {
        "exists": bool(existing),
        "client_name": client_name,
        "uri": _gen_uri("vless", client_name, existing or {}) if existing else "",
        "source": "legacy_xray",
    }


def _legacy_vless_clean(client_name: str) -> Dict:
    """Remove the canonical VLESS client from legacy host-Xray and apply the config."""
    try:
        existing = vless_manager.get_client(client_name)
    except Exception:
        existing = None
    if not existing:
        return {
            "ok": True,
            "message": "not present (legacy Xray)",
            "client_name": client_name,
            "removed": False,
            "source": "legacy_xray",
        }

    try:
        ok, msg = vless_manager.remove_client(client_name)
    except Exception as exc:
        ok, msg = False, f"{type(exc).__name__}: {exc}"

    if ok:
        ok, msg = _apply_legacy_vless_runtime(msg)

    return {
        "ok": ok,
        "message": msg,
        "client_name": client_name,
        "removed": ok,
        "source": "legacy_xray",
    }


def provision_user(
    telegram_id: int,
    enabled_protocols: Optional[List[str]] = None,
) -> Dict[str, Dict]:
    """Provision clients for one TG-ID across all enabled protocols.

    Idempotent: if a client with this canonical name already exists, do not
    duplicate it and return the existing one.

    Returns {protocol: {ok, message, client_name, uri, existed}}.
    """
    out: Dict[str, Dict] = {}
    protocols = list(enabled_protocols) if enabled_protocols is not None else list_enabled_protocols()
    for proto in protocols:
        client_name = make_client_name(proto, telegram_id)
        if proto == "vless":
            if _xui_enabled():
                try:
                    import xui_manager
                    ok, msg, uri = xui_manager.provision_named_client(
                        client_name, telegram_id
                    )
                except Exception as exc:
                    ok, msg, uri = False, f"{type(exc).__name__}: {exc}", ""
                out[proto] = {
                    "ok": ok,
                    "message": msg,
                    "client_name": client_name,
                    "uri": uri,
                    "existed": (msg == "exists"),
                    "source": "xui",
                }
            else:
                out[proto] = _legacy_vless_provision(client_name)
            continue
        mgr = _SIMPLE_MANAGERS[proto]
        # check first whether it already exists
        existing = None
        try:
            existing = mgr.get_client(client_name)
        except Exception as exc:
            logger.warning(
                "provision_manager: %s.get_client(%s) failed: %s",
                proto, client_name, exc,
            )
        if existing:
            if proto == "hysteria2":
                try:
                    apply_ok, apply_msg = hysteria2_manager.apply_config()
                except Exception as exc:
                    apply_ok, apply_msg = False, f"{type(exc).__name__}: {exc}"
                msg = f"exists; {apply_msg}"
                ok = bool(apply_ok)
            else:
                msg = "exists"
                ok = True
            out[proto] = {
                "ok": ok,
                "message": msg,
                "client_name": client_name,
                "uri": _gen_uri(proto, client_name, existing) if ok else "",
                "existed": True,
            }
            continue
        # create a new one
        try:
            ok, msg, client = mgr.add_client(name=client_name)
        except Exception as exc:
            ok, msg, client = False, f"{type(exc).__name__}: {exc}", {}
        if ok and proto == "hysteria2":
            try:
                apply_ok, apply_msg = hysteria2_manager.apply_config()
            except Exception as exc:
                apply_ok, apply_msg = False, f"{type(exc).__name__}: {exc}"
            msg = f"{msg}; {apply_msg}"
            ok = bool(ok and apply_ok)
        out[proto] = {
            "ok": ok,
            "message": msg,
            "client_name": client_name,
            "uri": _gen_uri(proto, client_name, client) if ok else "",
            "existed": False,
        }
    return out


def profiles_for_user(telegram_id: int) -> Dict[str, Dict]:
    """Read-only variant of provision_user — only show what already exists.

    Returns {protocol: {exists, client_name, uri}}.

    Looks up not only canonical ``Vless_ID52_49`` / ``Hys_ID52_49`` but also
    legacy names that older links used — otherwise `/profiles` reports
    "none" while the client is still alive.
    """
    out: Dict[str, Dict] = {}
    for proto in list_enabled_protocols():
        candidates = client_name_candidates(proto, telegram_id)
        preferred = candidates[0] if candidates else make_client_name(proto, telegram_id)
        if proto == "vless":
            if _xui_enabled():
                try:
                    import xui_manager
                    exists, _msg, uri, matched = xui_manager.find_named_client_uri_any(
                        candidates
                    )
                except Exception as exc:
                    logger.warning(
                        "provision_manager: xui find_named_client_uri_any failed: %s",
                        exc,
                    )
                    exists, uri, matched = False, "", ""
                out[proto] = {
                    "exists": bool(exists),
                    "client_name": matched or preferred,
                    "uri": uri or "",
                    "source": "xui",
                }
            else:
                # legacy host-Xray: first matching name among candidates
                found_profile = None
                for name in candidates:
                    found_profile = _legacy_vless_profile(name)
                    if found_profile.get("exists"):
                        break
                out[proto] = found_profile or _legacy_vless_profile(preferred)
            continue
        mgr = _SIMPLE_MANAGERS[proto]
        existing = None
        matched = preferred
        for name in candidates:
            try:
                existing = mgr.get_client(name)
            except Exception as exc:
                logger.warning(
                    "provision_manager: %s.get_client(%s) failed: %s",
                    proto, name, exc,
                )
                existing = None
            if existing:
                matched = name
                break
        if existing:
            out[proto] = {
                "exists": True,
                "client_name": matched,
                "uri": _gen_uri(proto, matched, existing),
            }
        else:
            out[proto] = {
                "exists": False,
                "client_name": preferred,
                "uri": "",
            }
    return out


def clean_user(telegram_id: int) -> Dict[str, Dict]:
    """Remove bot-managed clients with canonical names for this TG-ID.

    Safe: looks up the exact canonical name; manual clients with other
    names are left untouched.

    Returns {protocol: {ok, message, client_name, removed}}.
    """
    out: Dict[str, Dict] = {}
    for proto in list_enabled_protocols():
        client_name = make_client_name(proto, telegram_id)
        if proto == "vless":
            if _xui_enabled():
                try:
                    import xui_manager
                    # Check existence first so we know removed=True/False
                    exists, _msg, _uri = xui_manager.find_named_client_uri(client_name)
                    if not exists:
                        out[proto] = {
                            "ok": True,
                            "message": "not present",
                            "client_name": client_name,
                            "removed": False,
                            "source": "xui",
                        }
                        continue
                    ok, msg = xui_manager.remove_named_client(client_name)
                except Exception as exc:
                    ok, msg = False, f"{type(exc).__name__}: {exc}"
                out[proto] = {
                    "ok": ok,
                    "message": msg,
                    "client_name": client_name,
                    "removed": ok,
                    "source": "xui",
                }
            else:
                out[proto] = _legacy_vless_clean(client_name)
            continue
        mgr = _SIMPLE_MANAGERS[proto]
        existing = None
        try:
            existing = mgr.get_client(client_name)
        except Exception:
            existing = None
        if not existing:
            out[proto] = {
                "ok": True,
                "message": "not present",
                "client_name": client_name,
                "removed": False,
            }
            continue
        try:
            ok, msg = mgr.remove_client(client_name)
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        if ok and proto == "hysteria2":
            try:
                apply_ok, apply_msg = hysteria2_manager.apply_config()
            except Exception as exc:
                apply_ok, apply_msg = False, f"{type(exc).__name__}: {exc}"
            msg = f"{msg}; {apply_msg}"
            ok = bool(ok and apply_ok)
        out[proto] = {
            "ok": ok,
            "message": msg,
            "client_name": client_name,
            "removed": ok,
        }
    return out

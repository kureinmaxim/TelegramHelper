"""
provision_manager.py — единая точка авто-провизионинга клиентов
для особых пользователей бота во всех включённых протоколах.

Naming convention:
    <Prefix>_ID<first2>_<last2>
где first2/last2 — первые/последние 2 цифры Telegram user-ID.

Пример: TG-ID 8288584609 + протокол vless → "Vless_ID82_09".

PROTOCOL_PREFIX:
    vless     → Vless
    hysteria2 → Hys
    mtproto   → Mtp
    tuic      → Tuic
    anytls    → Any
    xhttp     → Xh
    mieru     → Mieru

NaiveProxy в провизионинг не входит — single-credentials модель,
per-user разделение требует серьёзного refactor Caddyfile и не
решается одним add_client.

VLESS-Reality поддерживает два источника:
    1. 3x-ui API, если `/xui_setup` включён.
    2. Legacy host-Xray (`vless_config.json` + `/usr/local/etc/xray/config.json`),
       если 3x-ui не настроена, но VLESS включён в `vless_manager`.
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

# Менеджеры с уже-готовым add_client/remove_client/get_client/list_clients API.
# VLESS обрабатывается отдельно: сначала 3x-ui, затем legacy host-Xray.
_SIMPLE_MANAGERS = {
    "hysteria2": hysteria2_manager,
    "mtproto": mtproto_manager,
    "tuic": tuic_manager,
    "anytls": anytls_manager,
    "xhttp": xhttp_manager,
    "mieru": mieru_manager,
}


def _xui_enabled() -> bool:
    """True, если VLESS должен провижениться через 3x-ui REST API."""
    try:
        import xui_manager
        return bool(xui_manager.is_enabled())
    except Exception as exc:
        logger.warning("provision_manager: xui_manager.is_enabled() failed: %s", exc)
        return False


def _legacy_vless_enabled() -> bool:
    """True, если VLESS доступен через legacy `vless_manager`/host-Xray."""
    try:
        return bool(vless_manager.is_vless_enabled())
    except Exception as exc:
        logger.warning(
            "provision_manager: vless_manager.is_vless_enabled() failed: %s",
            exc,
        )
        return False


def make_client_name(protocol: str, telegram_id: int) -> str:
    """Канонизированное имя клиента: <Prefix>_ID<first2>_<last2>."""
    prefix = PROTOCOL_PREFIX.get(protocol)
    if not prefix:
        raise ValueError(f"unknown protocol for naming: {protocol}")
    s = str(int(telegram_id))
    if len(s) < 4:
        s = s.zfill(4)
    return f"{prefix}_ID{s[:2]}_{s[-2:]}"


def legacy_client_name(protocol: str, telegram_id: int) -> str:
    """Старое имя /user-flow: ``Vless52...49``, ``Hysteria252...49``.

    В имени действительно три точки — так исторически строил handlers.
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
    """Канон + legacy (+ full-id варианты) для поиска уже выданных клиентов."""
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
    # Иногда клиент заводили с полным TG-ID в хвосте.
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
    """Список ключей протоколов, у которых is_enabled() == True.

    Порядок: vless, hysteria2, mtproto, tuic, anytls, xhttp, mieru.
    """
    enabled: List[str] = []
    # VLESS: сначала 3x-ui, иначе legacy host-Xray.
    if _xui_enabled() or _legacy_vless_enabled():
        enabled.append("vless")
    # Простые менеджеры
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
    """Сгенерировать URI/ссылку для клиента, если у менеджера есть метод."""
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
    """Идемпотентно создать/найти VLESS-клиента в legacy host-Xray."""
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
    """Read-only lookup VLESS-клиента в legacy host-Xray."""
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
    """Удалить canonical VLESS-клиента из legacy host-Xray и применить конфиг."""
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
    """Провизионит клиентов для одного TG-ID во всех включённых протоколах.

    Идемпотентно: если клиент с таким канон-именем уже есть, не дублирует
    и возвращает существующий.

    Возвращает {protocol: {ok, message, client_name, uri, existed}}.
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
        # сначала проверим, есть ли уже
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
        # создаём нового
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
    """Read-only вариант provision_user — только показать что есть.

    Возвращает {protocol: {exists, client_name, uri}}.

    Ищет не только канон ``Vless_ID52_49`` / ``Hys_ID52_49``, но и legacy
    имена, под которыми ссылки могли работать раньше — иначе `/profiles`
    врёт «нет», хотя клиент жив.
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
                # legacy host-Xray: первое найденное имя из кандидатов
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
    """Удалить bot-managed клиентов с канон-именами для этого TG-ID.

    Безопасно: ищет по точному канон-имени, ручные клиенты с другими
    именами не трогаются.

    Возвращает {protocol: {ok, message, client_name, removed}}.
    """
    out: Dict[str, Dict] = {}
    for proto in list_enabled_protocols():
        client_name = make_client_name(proto, telegram_id)
        if proto == "vless":
            if _xui_enabled():
                try:
                    import xui_manager
                    # Сначала проверим существование, чтобы знать removed=True/False
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

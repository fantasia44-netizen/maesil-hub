"""saas_config — DB-backed system settings with Fernet encryption.

Schema (migrations/001 + 005):
    saas_config(
        key            TEXT PRIMARY KEY,
        value_encrypted BYTEA,        -- Fernet ciphertext (bytes)
        value_plain    TEXT,          -- non-secret value
        category       TEXT,
        description    TEXT,
        updated_by     BIGINT REFERENCES app_users(id),
        updated_at     TIMESTAMPTZ
    )

Behavior:
    - get_config(key) returns the decrypted secret if present, else value_plain, else None.
    - set_config(key, value, encrypted=False) stores under value_plain or value_encrypted.
    - FERNET_KEY env required for encrypted storage. If missing, encrypted writes fall back
      to value_plain with a warning (so the app still runs in dev).
    - Reads cache for 60 s per key to reduce DB hits.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from db.client import get_admin_client

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_fernet_loaded = False
_cache: dict[str, tuple[float, Any]] = {}
_TTL = 60.0  # seconds


def _get_fernet() -> Fernet | None:
    """Return Fernet instance or None if FERNET_KEY missing."""
    global _fernet, _fernet_loaded
    if _fernet_loaded:
        return _fernet
    _fernet_loaded = True

    key = (os.environ.get('FERNET_KEY') or os.environ.get('ENCRYPTION_KEY') or '').strip()
    if not key:
        logger.warning('[saas_config] FERNET_KEY missing — secrets stored as plaintext')
        return None
    try:
        Fernet(key.encode())
        fkey = key.encode()
    except Exception:
        # derive from arbitrary string via SHA-256
        fkey = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    _fernet = Fernet(fkey)
    return _fernet


def _decrypt(blob) -> str | None:
    """Decrypt BYTEA value. blob may be bytes/memoryview/hex-string from Supabase."""
    if not blob:
        return None
    f = _get_fernet()
    if not f:
        return None
    try:
        if isinstance(blob, memoryview):
            blob = bytes(blob)
        if isinstance(blob, str):
            # Supabase REST returns BYTEA as '\x...' hex string
            if blob.startswith('\\x'):
                blob = bytes.fromhex(blob[2:])
            else:
                blob = blob.encode('utf-8')
        return f.decrypt(blob).decode('utf-8')
    except (InvalidToken, Exception) as e:
        logger.warning(f'[saas_config] decrypt failed: {e}')
        return None


def _encrypt(value: str) -> str | None:
    """Encrypt plain string and return base64 token (stored as TEXT-compatible)."""
    f = _get_fernet()
    if not f:
        return None
    return f.encrypt(value.encode('utf-8')).decode('utf-8')


def get_config(key: str, default: Any = None) -> Any:
    """Read a single config key (decrypts if needed). 60 s cache."""
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _TTL:
        return cached[1] if cached[1] is not None else default

    try:
        client = get_admin_client()
        res = client.table('saas_config').select('value_plain,value_encrypted') \
            .eq('key', key).limit(1).execute()
        rows = res.data or []
        if not rows:
            _cache[key] = (now, None)
            return default
        row = rows[0]
        val: Any = None
        if row.get('value_encrypted'):
            val = _decrypt(row['value_encrypted'])
        if val is None and row.get('value_plain'):
            val = row['value_plain']
        _cache[key] = (now, val)
        return val if val is not None else default
    except Exception as e:
        logger.error(f'[saas_config] get_config({key}) failed: {e}')
        return default


def set_config(key: str, value: str, *, encrypted: bool = False,
               category: str = 'general', description: str | None = None,
               updated_by: int | None = None) -> bool:
    """Insert/upsert a config row.

    encrypted=True → value_encrypted (Fernet); falls back to value_plain if FERNET_KEY missing.
    """
    payload: dict[str, Any] = {
        'key': key,
        'category': category,
    }
    if description is not None:
        payload['description'] = description
    if updated_by is not None:
        payload['updated_by'] = updated_by

    if encrypted:
        token = _encrypt(value)
        if token is None:
            # fallback: store plaintext when no key set (dev mode)
            payload['value_plain'] = value
            payload['value_encrypted'] = None
        else:
            payload['value_encrypted'] = token
            payload['value_plain'] = None
    else:
        payload['value_plain'] = value
        payload['value_encrypted'] = None

    try:
        client = get_admin_client()
        client.table('saas_config').upsert(payload, on_conflict='key').execute()
        _cache.pop(key, None)
        return True
    except Exception as e:
        logger.error(f'[saas_config] set_config({key}) failed: {e}')
        return False


def list_configs(category: str | None = None) -> list[dict]:
    """List all configs (raw rows; secrets are masked)."""
    try:
        client = get_admin_client()
        q = client.table('saas_config').select(
            'key,category,description,value_plain,value_encrypted,updated_at'
        )
        if category:
            q = q.eq('category', category)
        rows = q.order('category').order('key').execute().data or []
        # Mask: replace ciphertext with placeholder so admin UI knows it's set.
        for r in rows:
            if r.get('value_encrypted'):
                r['value_encrypted'] = '***SET***'
        return rows
    except Exception as e:
        logger.error(f'[saas_config] list_configs failed: {e}')
        return []


def delete_config(key: str) -> bool:
    try:
        client = get_admin_client()
        client.table('saas_config').delete().eq('key', key).execute()
        _cache.pop(key, None)
        return True
    except Exception as e:
        logger.error(f'[saas_config] delete_config({key}) failed: {e}')
        return False

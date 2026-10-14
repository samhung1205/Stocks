"""Invitation-only accounts, opaque server-side sessions and assisted recovery.

Only this service reads credential/session/token tables. API responses never
include hashes. Tokens are random, stored hashed, consumed atomically once and
passed in URL fragments so access logs/referrers do not contain credentials.
No SMTP or public registration. Owner id=1 is claimed only by the local CLI.
"""
import hashlib
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import text

from app import config, identity
from app.db import engine

_passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_dummy_hash = _passwords.hash(secrets.token_urlsafe(24))


class AccountError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _one(conn, sql, **params):
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _audit(conn, action, subject=None):
    actor = identity.current_actor()
    conn.execute(text('INSERT INTO account_audit(actor_id,subject_id,action,created_at) '
                      'VALUES(:a,:s,:v,:t)'),
                 {'a': actor.id if actor else None, 's': subject, 'v': action, 't': time.time()})


def _username(value):
    value = str(value or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}', value) or value == '__owner_pending__':
        raise AccountError(400, '帳號請使用 3–40 個英文字母、數字、底線、點或連字號')
    return value


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise AccountError(400, '密碼長度須為 12–128 個字元，可使用空白與中文')
    return _passwords.hash(value)


def _verify(stored, password):
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        return _passwords.verify(stored or _dummy_hash, password) and bool(stored)
    except (VerificationError, InvalidHashError):
        return False


def _admin():
    actor = identity.current_actor()
    if actor is None or actor.role != 'admin':
        raise AccountError(403, '需要管理員權限')
    # Do not trust a stale actor after an administrator disables an account.
    with engine.connect() as conn:
        row = _one(conn, 'SELECT active,role FROM users WHERE id=:i', i=actor.id)
    if not row or not row['active'] or row['role'] != 'admin':
        raise AccountError(403, '需要管理員權限')
    return actor


def _limit(bucket, maximum, seconds=900):
    now = time.time()
    key = _hash(bucket)
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        row = _one(conn, 'SELECT * FROM auth_attempts WHERE bucket=:b', b=key)
        if row and row['reset_at'] > now and row['count'] >= maximum:
            conn.rollback()
            raise AccountError(429, '嘗試次數過多，請稍後再試')
        count = row['count'] + 1 if row and row['reset_at'] > now else 1
        reset = row['reset_at'] if row and row['reset_at'] > now else now + seconds
        conn.execute(text('INSERT INTO auth_attempts(bucket,count,reset_at) VALUES(:b,:n,:t) '
                          'ON CONFLICT(bucket) DO UPDATE SET count=:n,reset_at=:t'),
                     {'b': key, 'n': count, 't': reset})
        conn.execute(text('DELETE FROM auth_attempts WHERE reset_at < :t'), {'t': now})
        conn.commit()


def bootstrap(username, password, display_name='管理員'):
    """Local operator only. The caller backs up an existing DB before migration."""
    return _claim_owner(_username(username), _password(password), display_name)


def bootstrap_from_basic():
    """Explicit operator migration of existing Basic credentials, once only.

    Preserve a legacy password's length so the owner can keep their login.
    New invites/password changes still use the normal 12-character minimum.
    Never exposed through HTTP; plaintext exists only in the process environment.
    """
    if not identity.is_system():
        raise PermissionError('Operator scope required')
    username = _username(config.AUTH_USERNAME)
    if not 1 <= len(config.AUTH_PASSWORD) <= 128:
        raise AccountError(400, '原 Basic 密碼未設定或超出可轉入長度')
    return _claim_owner(username, _passwords.hash(config.AUTH_PASSWORD), '管理員')


def _claim_owner(username, encoded, display_name):
    if not identity.is_system():
        raise PermissionError('Operator scope required')
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        row = _one(conn, 'SELECT * FROM users WHERE id=1')
        if not row or row['password_hash'] is not None or row['active']:
            raise AccountError(409, '管理員已初始化；請登入修改密碼或使用 recovery 指令')
        conn.execute(text('UPDATE users SET username=:u,display_name=:d,password_hash=:p,active=1 '
                          "WHERE id=1 AND password_hash IS NULL"),
                     {'u': username, 'd': display_name[:80], 'p': encoded})
        _audit(conn, 'owner_initialized', 1)
        conn.commit()


def login(username, password, remote):
    username = str(username or '').strip().lower()[:200]
    _limit('login-ip:' + remote, 30)
    _limit('login-user:' + username, 8)
    with engine.connect() as conn:
        row = _one(conn, 'SELECT * FROM users WHERE username=:u', u=username)
    valid = _verify(row['password_hash'] if row else None, password)
    if not valid or not row or not row['active']:
        raise AccountError(401, '帳號或密碼不正確')
    token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
    with engine.begin() as conn:
        active = _one(conn, 'SELECT active,password_hash FROM users WHERE id=:i', i=row['id'])
        if not active or not active['active'] or active['password_hash'] != row['password_hash']:
            raise AccountError(401, '帳號或密碼不正確')
        conn.execute(text('DELETE FROM auth_sessions WHERE expires_at<=:t'), {'t': now})
        conn.execute(text('INSERT INTO auth_sessions VALUES(:h,:u,:c,:t,:e,:t)'),
            {'h': _hash(token), 'u': row['id'], 'c': _hash(csrf), 't': now,
             'e': now + config.SESSION_HOURS * 3600})
        _audit(conn, 'login', row['id'])
    return token, csrf


def authenticate(token):
    if not token or len(token) > 200:
        return None
    now = time.time()
    with engine.begin() as conn:
        row = _one(conn, '''SELECT s.*,u.username,u.display_name,u.role,u.active
            FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=:h''', h=_hash(token))
        if not row or not row['active'] or row['expires_at'] <= now or row['last_seen'] <= now - config.SESSION_IDLE_MINUTES * 60:
            if row:
                conn.execute(text('DELETE FROM auth_sessions WHERE token_hash=:h'), {'h': row['token_hash']})
            return None
        if now - row['last_seen'] >= 60:
            conn.execute(text('UPDATE auth_sessions SET last_seen=:t WHERE token_hash=:h'),
                         {'t': now, 'h': row['token_hash']})
    return identity.Actor(row['user_id'], row['username'], row['role']), row['csrf_hash']


def me():
    actor = identity.current_actor()
    if not actor:
        raise AccountError(401, '請先登入')
    with engine.connect() as conn:
        return _one(conn, 'SELECT id,username,display_name,role FROM users WHERE id=:i', i=actor.id)


def logout(token):
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM auth_sessions WHERE token_hash=:h'), {'h': _hash(token or '')})
        _audit(conn, 'logout', identity.owner_id())


def change_password(current_password, new_password):
    uid = identity.owner_id()
    with engine.connect() as conn:
        row = _one(conn, 'SELECT password_hash FROM users WHERE id=:i', i=uid)
    if not row or not _verify(row['password_hash'], current_password):
        raise AccountError(400, '目前密碼不正確')
    encoded = _password(new_password)
    with engine.begin() as conn:
        changed = conn.execute(text('UPDATE users SET password_hash=:p WHERE id=:i AND password_hash=:old'),
                              {'p': encoded, 'i': uid, 'old': row['password_hash']}).rowcount
        if not changed:
            raise AccountError(409, '帳號狀態已變更，請重新登入')
        conn.execute(text('DELETE FROM auth_sessions WHERE user_id=:i'), {'i': uid})
        conn.execute(text('DELETE FROM account_tokens WHERE user_id=:i'), {'i': uid})
        _audit(conn, 'password_changed', uid)


def list_users():
    _admin()
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(
            'SELECT id,username,display_name,role,active,created_at,password_hash IS NOT NULL AS configured FROM users ORDER BY id')).mappings()]


def _issue(conn, uid, kind, actor_id):
    token = secrets.token_urlsafe(32)
    expiry = time.time() + (48 * 3600 if kind == 'invite' else 30 * 60)
    conn.execute(text('DELETE FROM account_tokens WHERE user_id=:i AND kind=:k'), {'i': uid, 'k': kind})
    conn.execute(text('INSERT INTO account_tokens(token_hash,kind,user_id,created_by,expires_at) '
                      'VALUES(:h,:k,:i,:a,:e)'),
                 {'h': _hash(token), 'k': kind, 'i': uid, 'a': actor_id, 'e': expiry})
    _audit(conn, kind + '_issued', uid)
    return {'user_id': uid, 'link': '/login.html#' + kind + '=' + token, 'expires_at': expiry}


def invite(username, display_name):
    actor = _admin()
    username = _username(username)
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        if _one(conn, 'SELECT id FROM users WHERE username=:u', u=username):
            raise AccountError(409, '此帳號已存在，請選擇其他帳號')
        uid = conn.execute(text('INSERT INTO users(username,display_name,role,active,created_at) '
            "VALUES(:u,:d,'member',0,datetime('now'))"),
            {'u': username, 'd': str(display_name or username).strip()[:80]}).lastrowid
        result = _issue(conn, uid, 'invite', actor.id)
        conn.commit()
    return result


def issue_token(uid, kind):
    actor = _admin()
    if kind not in ('invite', 'reset'):
        raise AccountError(400, '不支援的連結類型')
    with engine.begin() as conn:
        row = _one(conn, 'SELECT * FROM users WHERE id=:i', i=uid)
        if not row or (kind == 'invite' and row['password_hash'] is not None):
            raise AccountError(404, '找不到符合條件的帳號')
        if kind == 'reset' and (not row['active'] or row['password_hash'] is None):
            raise AccountError(409, '請先啟用帳號；未完成註冊的帳號請重發邀請')
        return _issue(conn, uid, kind, actor.id)


def revoke_tokens(uid):
    _admin()
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM account_tokens WHERE user_id=:i'), {'i': uid})
        _audit(conn, 'tokens_revoked', uid)


def consume_token(token, password, remote):
    _limit('token-ip:' + remote, 15)
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        raise AccountError(400, '連結已失效或已使用，請聯絡管理員重新產生')
    encoded = _password(password)
    now = time.time()
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        row = _one(conn, '''SELECT t.*,u.active,u.password_hash FROM account_tokens t
            JOIN users u ON u.id=t.user_id WHERE token_hash=:h''', h=_hash(token))
        if not row or row['used_at'] is not None or row['expires_at'] <= now:
            raise AccountError(400, '連結已失效或已使用，請聯絡管理員重新產生')
        if row['kind'] == 'reset' and not row['active']:
            raise AccountError(400, '帳號已停用，請聯絡管理員')
        conn.execute(text('UPDATE account_tokens SET used_at=:t WHERE token_hash=:h'), {'t': now, 'h': row['token_hash']})
        conn.execute(text('UPDATE users SET password_hash=:p,active=:a WHERE id=:i'),
                     {'p': encoded, 'a': 1 if row['kind'] == 'invite' else row['active'], 'i': row['user_id']})
        conn.execute(text('DELETE FROM auth_sessions WHERE user_id=:i'), {'i': row['user_id']})
        conn.execute(text('DELETE FROM account_tokens WHERE user_id=:i AND token_hash!=:h'),
                     {'i': row['user_id'], 'h': row['token_hash']})
        _audit(conn, row['kind'] + '_completed', row['user_id'])
        conn.commit()


def set_active(uid, active):
    actor = _admin()
    if uid == actor.id or uid == 1:
        raise AccountError(400, '不能停用主要管理員或自己')
    with engine.begin() as conn:
        row = _one(conn, 'SELECT id,password_hash FROM users WHERE id=:i', i=uid)
        if not row:
            raise AccountError(404, '找不到帳號')
        if active and not row['password_hash']:
            raise AccountError(409, '請先完成邀請設定密碼')
        conn.execute(text('UPDATE users SET active=:a WHERE id=:i'), {'a': int(active), 'i': uid})
        conn.execute(text('DELETE FROM auth_sessions WHERE user_id=:i'), {'i': uid})
        conn.execute(text('DELETE FROM account_tokens WHERE user_id=:i'), {'i': uid})
        _audit(conn, 'user_enabled' if active else 'user_disabled', uid)


def owner_recovery():
    """Explicit local CLI recovery; never exposed as an HTTP operation."""
    with engine.begin() as conn:
        owner = _one(conn, 'SELECT active,password_hash FROM users WHERE id=1')
        if not owner or not owner['active'] or not owner['password_hash']:
            raise AccountError(409, '請先使用 init 指令初始化管理員')
        return _issue(conn, 1, 'reset', 1)

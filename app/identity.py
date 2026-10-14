"""Trusted request/worker identity; never populated from a request payload.

Accounts mode fails closed without an actor. System scope is reserved for
startup migration/recovery and shared-data maintenance, never HTTP handlers.
Legacy basic mode remains a deliberately selected single-user compatibility mode.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app import config


@dataclass(frozen=True)
class Actor:
    id: int
    username: str
    role: str


_actor = ContextVar('stocks_actor', default=None)
_system = ContextVar('stocks_system', default=False)


def current_actor():
    return _actor.get()


def is_system():
    return _system.get()


def owner_id():
    actor = current_actor()
    if actor is not None:
        return actor.id
    if config.AUTH_MODE == 'basic' or is_system():
        return 1
    raise PermissionError('Authentication required')


@contextmanager
def actor_scope(actor):
    token = _actor.set(actor)
    system_token = _system.set(False)
    try:
        yield
    finally:
        _system.reset(system_token)
        _actor.reset(token)


@contextmanager
def system_scope():
    token = _system.set(True)
    try:
        yield
    finally:
        _system.reset(token)

"""Strict V5.0.1 freeze; the permissive V5 path is retired."""
from .release import seal_release


def freeze(output, release_policy=None):
    return seal_release(output, release_policy)

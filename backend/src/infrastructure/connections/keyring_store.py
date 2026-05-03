"""SecretStore implementation backed by the OS keychain via `keyring`.

The naming scheme is internal: callers pass a connection slug (``con-3``)
as the key, and we map it onto a service/username pair so the keychain
groups Atelier secrets cleanly. Keeping the mapping here means the
domain never sees the keychain naming convention.
"""

import keyring

KEYCHAIN_SERVICE = "atelier"


class KeyringSecretStore:
    """Thin shim over `keyring`. Tokens are stored under
    (KEYCHAIN_SERVICE, slug) so all Atelier entries live together."""

    def get(self, key: str) -> str | None:
        return keyring.get_password(KEYCHAIN_SERVICE, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(KEYCHAIN_SERVICE, key, value)

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            # Idempotent: deleting a missing entry is fine. The service
            # also calls delete on connections it never wrote a secret
            # for (404 path), so swallowing is correct.
            return


__all__ = ["KEYCHAIN_SERVICE", "KeyringSecretStore"]

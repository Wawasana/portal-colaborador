"""Password handling. Legacy hashes require an explicit administrative reset."""
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = PH.hash('nonexistent-account-dummy-secret')


def hash_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('La contraseña debe tener entre 12 y 256 caracteres.')
    return PH.hash(password)


def verify_password(password, stored):
    if not isinstance(password, str) or len(password) > 256:
        return False
    try:
        if not stored.startswith('$argon2id$'):
            # Equal-cost dummy check must NEVER authorize a legacy hash.
            try:
                PH.verify(DUMMY_HASH, password)
            except (VerificationError, InvalidHashError):
                pass
            return False
        return PH.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False

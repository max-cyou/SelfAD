import base64
import hashlib
import hmac
import secrets


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_LENGTH = 64


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_KEY_LENGTH,
    )

    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii")

    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${encoded_salt}${encoded_digest}"
    )


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        algorithm, n, r, p, encoded_salt, encoded_digest = encoded_password.split(
            "$", 5
        )
        if algorithm != "scrypt":
            return False

        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual_digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_digest),
        )
    except (TypeError, ValueError):
        return False

    return hmac.compare_digest(actual_digest, expected_digest)

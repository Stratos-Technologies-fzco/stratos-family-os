"""Order-independent content checksums."""

import hashlib


def compute_checksum(files: dict[str, bytes]) -> str:
    """sha256 over (path, content-hash) pairs, independent of file order."""
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(f"{rel}\x00{hashlib.sha256(files[rel]).hexdigest()}\n".encode())
    return digest.hexdigest()

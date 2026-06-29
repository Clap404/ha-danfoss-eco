"""XXTEA encrypt/decrypt for Danfoss eTRV BLE payloads.

Ported from libetrv (my_etrv2mqtt). The device reverses each 4-byte chunk
before/after XXTEA, so wire bytes must be byte-swapped per word.
"""
from __future__ import annotations

import xxtea


def reverse_chunks(data: bytes) -> bytes:
    """Reverse each 4-byte word in `data`. Length must be a multiple of 4."""
    out = bytearray(len(data))
    for i in range(0, len(data), 4):
        out[i : i + 4] = data[i : i + 4][::-1]
    return bytes(out)


def decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt a payload read from the device. `key` is the 16-byte secret."""
    return reverse_chunks(xxtea.decrypt(reverse_chunks(data), key, padding=False))


def encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt a payload to write to the device. `key` is the 16-byte secret."""
    return reverse_chunks(xxtea.encrypt(reverse_chunks(data), key, padding=False))

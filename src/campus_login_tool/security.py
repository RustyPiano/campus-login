"""RSA helpers for the captive portal login flow.

The portal's JavaScript uses a custom BigInt implementation, but the actual
algorithm is plain modular exponentiation over 16-bit words. Python's built-in
integers can perform the same work directly and much more efficiently.
"""


biRadixBits = 16
bitsPerDigit = biRadixBits
biRadix = 1 << biRadixBits
maxDigitVal = biRadix - 1


def _hex_words(value: str) -> list[int]:
    """Split a hex string into JS-compatible 16-bit words, least significant first."""
    words = []
    for end in range(len(value), 0, -4):
        start = max(end - 4, 0)
        words.append(int(value[start:end], 16))
    while len(words) > 1 and words[-1] == 0:
        words.pop()
    return words


def _chunk_size_from_modulus(modulus_hex: str) -> int:
    """Match the historical `2 * biHighIndex(modulus)` chunk sizing rule."""
    words = _hex_words(modulus_hex)
    high_index = len(words) - 1
    chunk_size = 2 * high_index
    if chunk_size <= 0:
        raise ValueError("RSA modulus is too short to determine a valid chunk size.")
    return chunk_size


def _encode_plaintext_block(chars: list[int]) -> int:
    """Pack plaintext into 16-bit little-endian words like the portal JS."""
    block = 0
    shift = 0
    for index in range(0, len(chars), 2):
        word = chars[index]
        if index + 1 < len(chars):
            word |= chars[index + 1] << 8
        block |= word << shift
        shift += biRadixBits
    return block


def _int_to_portal_hex(value: int) -> str:
    """Format integers the same way the JS BigInt `biToHex` helper does."""
    if value == 0:
        return "0000"

    groups = []
    while value:
        groups.append(f"{value & maxDigitVal:04x}")
        value >>= biRadixBits
    return "".join(reversed(groups))


def encryptPassword(
    password: str,
    publicKeyExponent: str,
    publicKeyModulus: str,
    macString: str = "111111111",
) -> str:
    """Encrypt the password exactly as the captive portal expects."""
    password_mac = f"{password}>{macString}"
    password_encode = password_mac[::-1]
    chunk_size = _chunk_size_from_modulus(publicKeyModulus)

    plaintext = [ord(char) for char in password_encode]
    while len(plaintext) % chunk_size != 0:
        plaintext.append(0)

    exponent = int(publicKeyExponent, 16)
    modulus = int(publicKeyModulus, 16)
    encrypted_blocks = []

    for offset in range(0, len(plaintext), chunk_size):
        block = _encode_plaintext_block(plaintext[offset : offset + chunk_size])
        encrypted = pow(block, exponent, modulus)
        encrypted_blocks.append(_int_to_portal_hex(encrypted))

    return " ".join(encrypted_blocks)

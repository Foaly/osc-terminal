"""
OSC Terminal – CLI Prototype v2
Sends each character as a mapped integer via OSC /letter,
with configurable delay between characters.
"""

import argparse
import re
import sys
import time

from pythonosc import udp_client

# ------------------------------------------------------------------
# Character-to-number mapping
# Edit these values to match your receiving end.
# ------------------------------------------------------------------
CHAR_MAP: dict[str, int] = {
    " ": 0,
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "I": 9,
    "J": 10,
    "K": 11,
    "L": 12,
    "M": 13,
    "N": 14,
    "O": 15,
    "P": 16,
    "Q": 17,
    "R": 18,
    "S": 19,
    "T": 20,
    "U": 21,
    "V": 22,
    "W": 23,
    "X": 24,
    "Y": 25,
    "Z": 26,
    "0": 27,
    "1": 28,
    "2": 29,
    "3": 30,
    "4": 31,
    "5": 32,
    "6": 33,
    "7": 34,
    "8": 35,
    "9": 36,
    ".": 37,
    ",": 38,
    "!": 39,
    "?": 40,
    "-": 41,
    ":": 42,
}

ALLOWED_CHARS = set(CHAR_MAP.keys())
OSC_PATH = "/letter"
DELAY_S = 0.7  # 700 ms between characters


def validate(msg: str) -> str | None:
    """Convert to uppercase, strip, and validate against CHAR_MAP keys."""
    msg = msg.upper().strip()
    if not msg:
        return None
    bad = set(c for c in msg if c not in ALLOWED_CHARS)
    if bad:
        return None
    return msg


def find_bad_chars(raw: str) -> set[str]:
    return set(c for c in raw.upper() if c not in ALLOWED_CHARS)


def send_message(client: udp_client.SimpleUDPClient, msg: str) -> None:
    """Send each character as its mapped integer, then a trailing space."""
    # append space so every message ends with a clear delimiter
    sequence = msg + " "
    total = len(sequence)

    for i, char in enumerate(sequence, 1):
        value = CHAR_MAP[char]
        client.send_message(OSC_PATH, value)
        print(f"    [{i}/{total}]  '{char}' -> {value}")
        if i < total:
            time.sleep(DELAY_S)


def build_client(ip: str, port: int) -> udp_client.SimpleUDPClient:
    return udp_client.SimpleUDPClient(ip, port)


def main():
    parser = argparse.ArgumentParser(description="OSC Terminal – send letters as numbers")
    parser.add_argument("--ip", default="127.0.0.1", help="OSC target IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8010, help="OSC target port (default: 8000)")
    parser.add_argument("--delay", type=float, default=DELAY_S,
                        help=f"Delay between characters in seconds (default: {DELAY_S})")
    args = parser.parse_args()

    delay = args.delay
    client = build_client(args.ip, args.port)

    print(f"OSC TERMINAL v2 – sending to {args.ip}:{args.port}")
    print(f"OSC path: {OSC_PATH}  |  delay: {int(delay * 1000)} ms")
    print(f"Allowed: A-Z  0-9  SPACE  . , ! ? - :")
    print(f"Type 'EXIT' to quit.\n")

    while True:
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBYE.")
            break

        if raw.strip().upper() == "EXIT":
            print("BYE.")
            break

        cleaned = validate(raw)
        if cleaned is None:
            bad = find_bad_chars(raw)
            print(f"  ERROR: invalid characters: {' '.join(sorted(bad))}")
            continue

        print(f"  SENDING >> {cleaned}")
        send_message(client, cleaned)
        print(f"  DONE.\n")


if __name__ == "__main__":
    main()
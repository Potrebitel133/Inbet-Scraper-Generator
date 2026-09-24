"""Standalone: generate a kiosk QR from a voucher code.

Usage: python qr_code_generator.py <voucher> [output.png]
"""

import sys

from bot import generate_qr

if __name__ == "__main__":
    voucher = sys.argv[1] if len(sys.argv) > 1 else "1527685772577091584"
    out = sys.argv[2] if len(sys.argv) > 2 else "voucher.png"
    path = generate_qr(voucher, out)
    print("saved", path)

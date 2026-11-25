"""
The efbet barcode: Code 128 (code set B) of the booking number, the same
barcode efbet prints ("ПЛАТИ В EFBET ПУНКТ КОД НА ЗАПАЗВАНЕТО").

efbet's own image for booking 3535002 (recorded on the client's PC,
25 Nov 2026, tests/fixtures/efbet/barcode_3535002.png) is 264 x 70 pixels:
Start B, the digits, the check symbol and Stop, 2 pixels per module, 10
modules of white on each side. render() draws the same image: a test
compares them pixel by pixel (efbet's last bar is one pixel wider, which
changes nothing for a scanner) and reads both back to 3535002.

No extra package is needed: the 107 Code 128 patterns are below.
"""

from PIL import Image, ImageDraw

# Widths (bar, space, bar, space, bar, space) of the Code 128 symbols 0..106;
# 106 = Stop (7 elements). Symbol value of a character in set B = ord(c) - 32.
PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]
START_B, STOP = 104, 106
MODULE = 2           # pixels per module, as efbet's image
HEIGHT = 70
QUIET = 10           # white modules on each side


def symbols(text):
    """Start B, the characters, the check symbol and Stop."""
    text = str(text)
    if not text or any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError(f"cannot make a barcode of {text!r}")
    values = [ord(c) - 32 for c in text]
    check = (START_B + sum(i * v for i, v in enumerate(values, start=1))) % 103
    return [START_B, *values, check, STOP]


def modules(text):
    """The barcode as a list of 1 (bar) / 0 (space), one per module, without the quiet zones."""
    bits = []
    for value in symbols(text):
        for i, width in enumerate(PATTERNS[value]):
            bits.extend([1 if i % 2 == 0 else 0] * int(width))
    return bits


def render(text, module=MODULE, height=HEIGHT, quiet=QUIET):
    """Black and white image of the barcode (module pixels per module, quiet modules each side)."""
    bits = [0] * quiet + modules(text) + [0] * quiet
    img = Image.new("1", (len(bits) * module, height), 1)
    draw = ImageDraw.Draw(img)
    for i, bit in enumerate(bits):
        if bit:
            draw.rectangle([i * module, 0, (i + 1) * module - 1, height - 1], fill=0)
    return img


def save(text, path, **options):
    render(text, **options).save(path)
    return path


def decode(img):
    """Read a Code 128 B barcode image back to its text (tests; also checks a saved image).

    Looks at the middle row, measures the narrowest bar as one module, and
    checks the check symbol. Raises ValueError for anything that is not a
    valid Code 128 B barcode.
    """
    gray = img.convert("L")
    w, h = gray.size
    row = [1 if gray.getpixel((x, h // 2)) < 128 else 0 for x in range(w)]
    runs, cur, n = [], row[0], 0
    for bit in row:
        if bit == cur:
            n += 1
        else:
            runs.append((cur, n))
            cur, n = bit, 1
    runs.append((cur, n))
    if runs and runs[0][0] == 0:
        runs = runs[1:]
    if runs and runs[-1][0] == 0:
        runs = runs[:-1]
    if not runs:
        raise ValueError("no barcode found")
    unit = min(n for _, n in runs)
    widths = [round(n / unit) for _, n in runs]
    values, i = [], 0
    while i < len(widths):
        size = 7 if len(widths) - i == 7 else 6
        key = "".join(map(str, widths[i:i + size]))
        if key not in PATTERNS:
            raise ValueError("not a Code 128 barcode")
        values.append(PATTERNS.index(key))
        i += size
    if len(values) < 4 or values[0] != START_B or values[-1] != STOP:
        raise ValueError("not a Code 128 B barcode")
    data, check = values[1:-2], values[-2]
    if (START_B + sum(i * v for i, v in enumerate(data, start=1))) % 103 != check:
        raise ValueError("barcode check symbol is wrong")
    return "".join(chr(v + 32) for v in data)

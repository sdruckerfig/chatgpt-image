#!/usr/bin/env python3
"""Generate and edit images with OpenAI's ChatGPT Images 2.5 models.

Uses gpt-image-2.5-sunburst by default (OpenAI's highest-quality image model)
and gpt-image-2.5-flare with --fast (quicker, same price), through the OpenAI
Images API. Standard library only; runs on Python 3.9 and newer.

The API key is read from $OPENAI_API_KEY, else ~/.config/openai/api_key, else
the macOS Keychain item "openai-api-key". It is sent only to
https://api.openai.com and is never printed. --dry-run needs no key.

Run "imagegen.py -h" for usage.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import http.client
import json
import math
import os
import random
import re
import shlex
import socket
import stat
import struct
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import uuid

API_ROOT = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-2.5-sunburst"
FAST_MODEL = "gpt-image-2.5-flare"
# The 2.5 aliases with their dated snapshots, and the previous generation.
MODEL_25 = re.compile(r"gpt-image-2\.5-(?:sunburst|flare)(?:-\d{4}-\d{2}-\d{2})?")
MODEL_2 = re.compile(r"gpt-image-2(?:-\d{4}-\d{2}-\d{2})?")

QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")
BACKGROUNDS = ("auto", "opaque", "transparent")
FORMATS = ("png", "webp", "jpeg", "jpg")
MODERATIONS = ("auto", "low")
MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
EXTENSION = {"png": "png", "jpeg": "jpg", "webp": "webp"}
QUOTA_CODES = ("insufficient_quota", "billing_hard_limit_reached", "billing_not_active")

MAX_PROMPT_CHARS = 32_000
MAX_N = 10
MAX_INPUT_IMAGES = 16
MAX_INPUT_BYTES = 50 * 1024 * 1024
MASK_SPEC_BYTES = 4 * 1024 * 1024
MAX_DATA_URL_CHARS = 20_971_520

SIZE_STEP = 16
MAX_EDGE = 3840
MAX_RATIO = 3
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
EXPERIMENTAL_ABOVE = 2560 * 1440

# Standard-tier list prices in USD per 1M tokens (OpenAI pricing page, October 2026).
PRICE_TEXT_IN = 5.00
PRICE_IMAGE_IN = 8.00
PRICE_IMAGE_OUT = 30.00
# Output patches along the long edge for each quality (from OpenAI's token calculator).
PATCHES = {
    "2.5": {"low": 16, "medium": 24, "high": 48, "xhigh": 64, "max": 96},
    "2": {"low": 16, "medium": 48, "high": 96},
}

KEY_FILE = "~/.config/openai/api_key"
KEYCHAIN_SERVICE = "openai-api-key"
RETRIES = 3
DEFAULT_TIMEOUT = 540.0

NO_KEY_HELP = """\
no OpenAI API key found. Checked $OPENAI_API_KEY, ~/.config/openai/api_key and the
macOS Keychain item "openai-api-key". Create a key at https://platform.openai.com/api-keys
and store it yourself in a terminal (never paste it into a chat), with one of:
  Keychain (recommended; prompts for the key, so it stays out of shell history):
    security add-generic-password -a "$USER" -s openai-api-key -w
  File (run this, paste the key when it waits; nothing is shown; then press Enter):
    mkdir -p ~/.config/openai && (umask 077 && read -rs k && printf '%s\\n' "$k" > ~/.config/openai/api_key) && chmod 600 ~/.config/openai/api_key
  Environment variable: add  export OPENAI_API_KEY=...  to ~/.zshrc or ~/.bashrc, then open a new terminal."""


class UsageError(Exception):
    """Invalid arguments or inputs, or no API key: exit status 2."""


class ApiError(Exception):
    """The API returned an error or could not be reached: exit status 1."""


# --------------------------------------------------------------------------- output

_SECRETS = []  # the key in use, so that any echo of it can be scrubbed
_KEY_LIKE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_*\-]{6,}")


def redact(text):
    for secret in _SECRETS:
        text = text.replace(secret, "[redacted]")
    return _KEY_LIKE.sub("sk-[redacted]", text)


def note(message):
    print("imagegen: " + redact(message), file=sys.stderr, flush=True)


def emit(obj):
    """Print one JSON object on stdout."""
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    try:
        text.encode(sys.stdout.encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        text = json.dumps(obj, indent=2)
    print(text, flush=True)


# --------------------------------------------------------------------------- API key

def _key_from_env():
    return os.environ.get("OPENAI_API_KEY", "").strip() or None


def _key_from_file():
    path = os.path.expanduser(KEY_FILE)
    try:
        mode = os.stat(path).st_mode
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        note(f"cannot read {KEY_FILE} ({exc.__class__.__name__}); skipping it")
        return None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        note(f"{KEY_FILE} is readable by other users; run: chmod 600 {KEY_FILE}")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:  # tolerate OPENAI_API_KEY=... and export OPENAI_API_KEY=...
            line = line.split("=", 1)[1].strip().strip("'\"")
        return line or None
    note(f"{KEY_FILE} holds no key; skipping it")
    return None


def _key_from_keychain():
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/security"):
        return None
    try:
        found = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if found.returncode != 0:
        return None
    return found.stdout.strip() or None


def load_api_key():
    sources = (
        ("$OPENAI_API_KEY", _key_from_env),
        (KEY_FILE, _key_from_file),
        (f'the Keychain item "{KEYCHAIN_SERVICE}"', _key_from_keychain),
    )
    for label, read in sources:
        key = read()
        if not key:
            continue
        if not re.fullmatch(r"[\x21-\x7e]+", key):
            raise UsageError(f"the API key in {label} contains spaces or unusual characters; store it again")
        _SECRETS.append(key)
        return key
    raise UsageError(NO_KEY_HELP)


# --------------------------------------------------------------------------- images on disk

def sniff_image(data):
    """Return (format, width, height, has_alpha) for PNG, JPEG or WebP bytes; format is "" otherwise."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 26 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "png", width, height, data[25] in (4, 6) or _png_has_trns(data)
    if data[:3] == b"\xff\xd8\xff":
        width, height = _jpeg_size(data)
        return "jpeg", width, height, False
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        width, height, alpha = _webp_info(data)
        return "webp", width, height, alpha
    return "", None, None, False


def _png_has_trns(data):
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk = data[pos + 4:pos + 8]
        if chunk == b"tRNS":
            return True
        if chunk in (b"IDAT", b"IEND"):
            return False
        pos += 12 + length
    return False


def _jpeg_size(data):
    pos, end = 2, len(data)
    while pos + 4 <= end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:  # markers without a length
            pos += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image, or start of scan before any frame header
            break
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and pos + 9 <= end:
            height, width = struct.unpack(">HH", data[pos + 5:pos + 9])
            return width, height
        pos += 2 + struct.unpack(">H", data[pos + 2:pos + 4])[0]
    return None, None


def _webp_info(data):
    chunk = data[12:16]
    try:
        if chunk == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF, False
        if chunk == b"VP8L":
            b0, b1, b2, b3 = data[21:25]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return width, height, bool(b3 & 0x10)
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return width, height, bool(data[20] & 0x10)
    except (struct.error, ValueError, IndexError):
        pass
    return None, None, False


def load_image(arg, option):
    path = os.path.abspath(os.path.expanduser(arg))
    if not os.path.isfile(path):
        raise UsageError(f"{option} file not found: {arg}")
    size = os.path.getsize(path)
    if size >= MAX_INPUT_BYTES:
        raise UsageError(f"{option} {arg} is {size / 1048576:.1f} MB; the API accepts files under 50 MB")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise UsageError(f"cannot read {option} {arg}: {exc.strerror or exc}") from None
    fmt, width, height, alpha = sniff_image(data)
    if not fmt:
        raise UsageError(f"{option} {arg} is not a PNG, JPEG or WebP image; convert it first, "
                         f"e.g. sips -s format png {shlex.quote(arg)} --out converted.png")
    return {"arg": arg, "path": path, "data": data, "format": fmt,
            "width": width, "height": height, "alpha": alpha}


def pixels_of(image):
    return f"{image['width']}x{image['height']}" if image["width"] else None


def check_mask(mask, first):
    if mask["format"] != "png":
        raise UsageError(f"--mask {mask['arg']} must be a PNG with an alpha channel, not {mask['format'].upper()}")
    if not mask["alpha"]:
        raise UsageError(
            f"--mask {mask['arg']} has no alpha channel; fully transparent pixels mark the area to edit. "
            "Convert a black-and-white mask (black = edit, white = keep) with Pillow "
            "(python3 -m pip install --user pillow): from PIL import Image; "
            "m = Image.open('bw.png').convert('L'); r = m.convert('RGBA'); r.putalpha(m); r.save('mask.png')")
    if mask["width"] and first["width"] and (mask["width"], mask["height"]) != (first["width"], first["height"]):
        raise UsageError(f"--mask is {pixels_of(mask)} but the first --image is {pixels_of(first)}; "
                         "they must be the same size")
    if len(mask["data"]) >= MASK_SPEC_BYTES:
        note(f"--mask is {len(mask['data']) / 1048576:.1f} MB; the API reference asks for masks under 4 MB")


def upload_name(image):
    stem = os.path.splitext(os.path.basename(image["path"]))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "image"
    return f"{stem}.{EXTENSION[image['format']]}"


def data_url(image):
    url = f"data:{MIME[image['format']]};base64," + base64.b64encode(image["data"]).decode("ascii")
    if len(url) > MAX_DATA_URL_CHARS:
        raise UsageError(
            f"{image['arg']} is too large to inline as a data URL ({len(url):,} characters; the limit is "
            "20,971,520, about 15 MB of image). Drop --moderation so it is uploaded as a file "
            "(up to 50 MB), or shrink it first: sips -Z 2048 IN --out OUT")
    return url


# --------------------------------------------------------------------------- validation

_SIZE = re.compile(r"(\d+)\s*[x×]\s*(\d+)")


def size_problems(width, height):
    if width <= 0 or height <= 0:
        return ["both edges must be positive"]
    problems = []
    if width % SIZE_STEP or height % SIZE_STEP:
        problems.append("both edges must be multiples of 16")
    if max(width, height) > MAX_EDGE:
        problems.append("each edge must be at most 3840")
    if max(width, height) > MAX_RATIO * min(width, height):
        problems.append("the long edge can be at most 3 times the short edge")
    pixels = width * height
    if pixels < MIN_PIXELS:
        problems.append(f"{pixels:,} pixels is below the minimum of 655,360 (about 810x810)")
    elif pixels > MAX_PIXELS:
        problems.append(f"{pixels:,} pixels is above the maximum of 8,294,400 (3840x2160)")
    return problems


def nearest_valid_size(width, height):
    """The valid size closest in shape and scale to WIDTHxHEIGHT. One that covers it wins when one
    exists, so the result can be cropped or shrunk to the target exactly."""
    width, height = max(width, 1), max(height, 1)
    aspect = min(max(width / height, 1 / MAX_RATIO), MAX_RATIO)
    pixels = min(max(width * height, MIN_PIXELS), MAX_PIXELS)
    target_w, target_h = math.sqrt(pixels * aspect), math.sqrt(pixels / aspect)
    shrink = min(1.0, MAX_EDGE / max(target_w, target_h))
    target_w, target_h = target_w * shrink, target_h * shrink
    best = None
    for w in range(SIZE_STEP, MAX_EDGE + 1, SIZE_STEP):
        for h in range(SIZE_STEP, MAX_EDGE + 1, SIZE_STEP):
            if not MIN_PIXELS <= w * h <= MAX_PIXELS or max(w, h) > MAX_RATIO * min(w, h):
                continue
            score = (0 if w >= width and h >= height else 1,
                     max(abs(math.log(w / target_w)), abs(math.log(h / target_h))))
            if best is None or score < best[0]:
                best = (score, w, h)
    return best[1], best[2]


def check_size(value):
    if value is None:
        return None
    text = value.strip().lower()
    if text == "auto":
        return "auto"
    match = _SIZE.fullmatch(text)
    if not match:
        raise UsageError(f'--size must be "auto" or WIDTHxHEIGHT such as 1536x1024, not "{value}"')
    width, height = int(match.group(1)), int(match.group(2))
    problems = size_problems(width, height)
    if problems:
        w, h = nearest_valid_size(width, height)
        message = f"--size {width}x{height} is not allowed: {'; '.join(problems)}. Nearest valid size: {w}x{h}"
        if width and height and w >= width and h >= height:
            message += "; generate that, then " + fit_commands(w, h, width, height)
        raise UsageError(message)
    if width * height > EXPERIMENTAL_ABOVE:
        note(f"{width}x{height} is above 2560x1440 pixels; OpenAI calls output this large experimental")
    return f"{width}x{height}"


def fit_commands(w, h, width, height):
    """The sips commands that turn a WxH image into the WIDTHxHEIGHT target it covers."""
    crop = f"sips -c {height} {width} IN.png --out OUT.png"
    if w * h <= 1.1 * width * height:  # a small overshoot: trim it without resampling
        return "crop with: " + crop
    scale = min(w / width, h / height)
    crop_w, crop_h = min(w, round(width * scale)), min(h, round(height * scale))
    shrink = f"sips -z {height} {width}"
    if (crop_w, crop_h) == (w, h):  # same shape, larger
        return f"shrink with: {shrink} IN.png --out OUT.png"
    if (crop_w, crop_h) == (width, height):  # e.g. a banner wider than 3:1
        return "crop with: " + crop
    return f"crop and shrink with: sips -c {crop_h} {crop_w} IN.png --out OUT.png && {shrink} OUT.png"


def check_timeout(timeout):
    if not 10 <= timeout <= 7200:
        raise UsageError("--timeout must be between 10 and 7200 seconds")


def resolve_model(args):
    model = FAST_MODEL if args.fast else (args.model or DEFAULT_MODEL).strip()
    if MODEL_25.fullmatch(model) or MODEL_2.fullmatch(model):
        return model
    raise UsageError(
        f'unsupported --model "{model}". Use {DEFAULT_MODEL} (default), {FAST_MODEL} (--fast), a dated '
        f"snapshot such as {DEFAULT_MODEL}-2026-09-08, or the previous-generation gpt-image-2. "
        "(chatgpt-image-latest is the previous ChatGPT model; dall-e-2 and dall-e-3 were retired on 2026-05-12.)")


def read_prompt(args):
    if args.prompt_file is not None:
        if args.prompt_file == "-":
            text = sys.stdin.read()
        else:
            try:
                with open(os.path.expanduser(args.prompt_file), encoding="utf-8") as handle:
                    text = handle.read()
            except FileNotFoundError:
                raise UsageError(f"--prompt-file not found: {args.prompt_file}") from None
            except UnicodeDecodeError:
                raise UsageError(f"--prompt-file {args.prompt_file} is not UTF-8 text") from None
            except OSError as exc:
                raise UsageError(f"cannot read --prompt-file {args.prompt_file}: {exc.strerror or exc}") from None
    else:
        text = args.prompt
    text = text.strip()
    if not text:
        raise UsageError("the prompt is empty")
    if len(text) > MAX_PROMPT_CHARS:
        raise UsageError(f"the prompt is {len(text):,} characters; the limit is 32,000")
    return text


def build_params(args, model):
    """The optional API parameters, validated, in request order. Unset ones are left to the API defaults."""
    params = {}
    if args.n is not None:
        if not 1 <= args.n <= MAX_N:
            raise UsageError("--n must be between 1 and 10")
        params["n"] = args.n
    size = check_size(args.size)
    if size:
        params["size"] = size
    if args.quality:
        if args.quality in ("xhigh", "max") and not MODEL_25.fullmatch(model):
            raise UsageError(f"--quality {args.quality} needs a GPT Image 2.5 model; {model} stops at high")
        params["quality"] = args.quality
    fmt = "jpeg" if args.format == "jpg" else args.format
    if args.background:
        if args.background == "transparent" and fmt == "jpeg":
            raise UsageError("--background transparent needs --format png or webp; JPEG has no transparency")
        params["background"] = args.background
    if fmt:
        params["output_format"] = fmt
    if args.compression is not None:
        if fmt not in ("jpeg", "webp"):
            raise UsageError("--compression only applies with --format jpeg or webp")
        if not 0 <= args.compression <= 100:
            raise UsageError("--compression must be between 0 and 100")
        params["output_compression"] = args.compression
    if args.moderation:
        params["moderation"] = args.moderation
    return params


def output_stem(name, prompt):
    if name:
        stem = re.sub(r"\.(png|jpe?g|webp)$", "", name.strip(), flags=re.IGNORECASE)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")
        if not stem:
            raise UsageError("--name needs at least one letter or digit")
        return stem[:80]
    stem = ""
    for word in re.findall(r"[a-z0-9]+", prompt.lower()):
        if len(stem) + len(word) + 1 > 40:
            break
        stem = f"{stem}-{word}" if stem else word
    return stem or "image"


def resolve_out_dir(value):
    path = os.path.abspath(os.path.expanduser(value))
    if os.path.exists(path) and not os.path.isdir(path):
        raise UsageError(f"--out {value} exists and is not a directory")
    return path


# --------------------------------------------------------------------------- cost

def output_tokens_per_image(model, size, quality):
    """Image output tokens for one image, per OpenAI's token calculator; None when size or quality is auto."""
    patches = PATCHES["2.5" if MODEL_25.fullmatch(model) else "2"].get(quality or "")
    if not patches or not size or size == "auto":
        return None
    width, height = (int(part) for part in size.split("x"))
    grid = patches * round(patches * min(width, height) / max(width, height))
    return -(-grid * (2_000_000 + width * height) // 4_000_000)


def estimate(model, params):
    tokens = output_tokens_per_image(model, params.get("size"), params.get("quality"))
    if tokens is None:
        return {"note": "pass an explicit --size WxH and --quality to get a cost estimate"}
    count = params.get("n", 1)
    return {"output_tokens_per_image": tokens, "images": count,
            "output_usd": round(tokens * count * PRICE_IMAGE_OUT / 1e6, 4),
            "note": "image output at list price; the prompt ($5/1M tokens) and any input images ($8/1M) add a little"}


def cost_from_usage(usage):
    def number(value):
        return value if isinstance(value, (int, float)) else 0
    inputs = usage.get("input_tokens_details") or {}
    outputs = usage.get("output_tokens_details") or {}
    text_in = number(inputs.get("text_tokens", 0 if inputs else usage.get("input_tokens")))
    image_in = number(inputs.get("image_tokens"))
    image_out = number(outputs.get("image_tokens", usage.get("output_tokens")))
    return round((text_in * PRICE_TEXT_IN + image_in * PRICE_IMAGE_IN + image_out * PRICE_IMAGE_OUT) / 1e6, 4)


# --------------------------------------------------------------------------- HTTP

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Treat any redirect as an error, so the key is never sent to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def api_call(method, path, key, body=None, content_type=None, timeout=DEFAULT_TIMEOUT):
    """Send one API request, retrying 429 and 5xx; return (parsed JSON, x-request-id)."""
    url = API_ROOT + path
    if not url.startswith("https://api.openai.com/"):
        raise ApiError("refusing to send the API key anywhere but https://api.openai.com")
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        request = urllib.request.Request(url, data=body, method=method)
        request.add_unredirected_header("Authorization", "Bearer " + key)
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "chatgpt-image-skill/1.0")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with _OPENER.open(request, timeout=max(deadline - time.monotonic(), 5.0)) as response:
                raw = response.read()
                request_id = response.headers.get("x-request-id")
            break
        except urllib.error.HTTPError as exc:
            request_id = exc.headers.get("x-request-id") if exc.headers else None
            error = _error_details(exc)
            quota = error.get("code") in QUOTA_CODES or error.get("type") in QUOTA_CODES
            if (exc.code == 429 or exc.code >= 500) and not quota and attempt < RETRIES:
                delay = _retry_delay(exc.headers, attempt)
                if time.monotonic() + delay + 30 < deadline:
                    attempt += 1
                    note(f"HTTP {exc.code}; retry {attempt} of {RETRIES} in {delay:.0f}s")
                    time.sleep(delay)
                    continue
            raise ApiError(_describe_http_error(exc.code, error, request_id)) from None
        except (socket.timeout, TimeoutError):
            raise ApiError(_timeout_message(timeout)) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ApiError(_timeout_message(timeout)) from None
            raise ApiError(f"could not reach api.openai.com: {exc.reason}") from None
        except (http.client.HTTPException, OSError) as exc:
            raise ApiError(f"the connection to api.openai.com failed ({exc.__class__.__name__}: {exc})") from None
    try:
        return json.loads(raw.decode("utf-8")), request_id
    except ValueError:
        suffix = f" (request id {request_id})" if request_id else ""
        raise ApiError("api.openai.com returned a response that is not JSON" + suffix) from None


def _error_details(exc):
    try:
        raw = exc.read()
    except Exception:  # the body is optional; the status code still tells the story
        raw = b""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        text = raw.decode("utf-8", "replace").strip()
        return {"message": text[:500]} if text else {}
    if not isinstance(payload, dict):
        return {"message": str(payload)[:500]}
    error = dict(payload["error"]) if isinstance(payload.get("error"), dict) else {}
    if isinstance(payload.get("error"), str):
        error["message"] = payload["error"]
    if "moderation_details" not in error and isinstance(payload.get("moderation_details"), dict):
        error["moderation_details"] = payload["moderation_details"]
    return error


def _retry_delay(headers, attempt):
    try:
        seconds = float(headers.get("retry-after")) if headers is not None else None
    except (TypeError, ValueError):
        seconds = None
    if seconds is None or seconds < 0:
        seconds = 2.0 * 2 ** attempt + random.uniform(0, 1)
    return min(seconds, 60.0)


def _describe_http_error(status, error, request_id):
    kind = " / ".join(str(error[field]) for field in ("type", "code") if error.get(field))
    message = str(error.get("message") or "no error message")
    lines = [f"OpenAI API returned HTTP {status}" + (f" ({kind})" if kind else "") + f": {message}"]
    details = error.get("moderation_details")
    if isinstance(details, dict):
        categories = ", ".join(str(c) for c in details.get("categories") or []) or "not given"
        lines.append(f"  moderation stage: {details.get('moderation_stage', 'unknown')}; categories: {categories}")
    hint = _hint(status, error)
    if hint:
        lines.append("  hint: " + hint)
    if request_id:
        lines.append("  request id: " + request_id)
    return "\n".join(lines)


def _hint(status, error):
    code = str(error.get("code") or "")
    message = str(error.get("message") or "").lower()
    if code == "moderation_blocked" or error.get("moderation_details") or "safety system" in message:
        return ("rephrase the request; resending it unchanged will be blocked again. "
                "--moderation low only relaxes filtering for benign content")
    if status == 401:
        return "the API key was rejected; store a valid key (never paste it into a chat)"
    if code in QUOTA_CODES:
        return "the account is out of credit; add billing at https://platform.openai.com/settings/organization/billing"
    if status == 429:
        return "rate limited even after retries; wait a minute or lower --n (the Build tier allows 20 images a minute)"
    if status == 403 or "verif" in message:
        return ("the organization may need API Organization Verification: "
                "https://platform.openai.com/settings/organization/general")
    if status == 404 or code == "model_not_found":
        return "this key cannot use that model; run `imagegen.py models` to see which image models it can use"
    if 300 <= status < 400:
        return "the API answered with a redirect, which this script never follows"
    if status >= 500:
        return "OpenAI server error; try again in a few minutes"
    return None


def _timeout_message(timeout):
    return (f"no response within {timeout:.0f}s. The request may still have finished and been billed. "
            "Retry with a longer --timeout, or with --fast, a lower --quality or a smaller --size.")


# --------------------------------------------------------------------------- saving

def _candidate_paths(out_dir, stem, index, extension):
    yield os.path.join(out_dir, f"{stem}-{index}.{extension}")
    suffix = 2
    while True:
        yield os.path.join(out_dir, f"{stem}-{index}-{suffix}.{extension}")
        suffix += 1


def planned_path(out_dir, stem, index, extension):
    for path in _candidate_paths(out_dir, stem, index, extension):
        if not os.path.exists(path):
            return path


def write_new_file(out_dir, stem, index, extension, data):
    """Write data to the first free STEM-INDEX[-k].EXT; never overwrites an existing file."""
    for path in _candidate_paths(out_dir, stem, index, extension):
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        return path


def save_images(items, out_dir, stem, fallback_format):
    decoded = []
    for index, item in enumerate(items, 1):
        encoded = item.get("b64_json") if isinstance(item, dict) else None
        if not encoded:
            raise ApiError(f"image {index} in the response has no b64_json data")
        try:
            decoded.append(base64.b64decode(encoded))
        except (binascii.Error, ValueError):
            raise ApiError(f"image {index} in the response is not valid base64") from None
    paths = []
    try:
        os.makedirs(out_dir, exist_ok=True)
        for index, data in enumerate(decoded, 1):
            fmt = sniff_image(data)[0] or fallback_format or "png"
            paths.append(write_new_file(out_dir, stem, index, EXTENSION.get(fmt, fmt), data))
    except OSError as exc:
        raise ApiError(f"could not save the images in {out_dir}: {exc.strerror or exc}") from None
    return paths


# --------------------------------------------------------------------------- commands

def cmd_generate(args):
    check_timeout(args.timeout)
    model = resolve_model(args)
    prompt = read_prompt(args)
    params = build_params(args, model)
    out_dir = resolve_out_dir(args.out)
    stem = output_stem(args.name, prompt)
    body = {"model": model, "prompt": prompt}
    body.update(params)
    preview = {"method": "POST", "url": API_ROOT + "/images/generations", "body_format": "json", "body": body}
    payload = json.dumps(body).encode("utf-8")
    return run_images(args, model, params, preview, "/images/generations", payload, "application/json",
                      out_dir, stem)


def cmd_edit(args):
    check_timeout(args.timeout)
    model = resolve_model(args)
    prompt = read_prompt(args)
    params = build_params(args, model)
    if params.get("moderation") == "auto":  # the default; dropping it keeps the multipart upload
        del params["moderation"]
    if len(args.image) > MAX_INPUT_IMAGES:
        raise UsageError(f"{len(args.image)} --image files given; the API accepts at most 16")
    images = [load_image(path, "--image") for path in args.image]
    mask = load_image(args.mask, "--mask") if args.mask else None
    if mask:
        check_mask(mask, images[0])
    out_dir = resolve_out_dir(args.out)
    stem = output_stem(args.name, prompt)
    if "moderation" in params:
        preview, payload, content_type = _edit_json(model, prompt, params, images, mask)
    else:
        preview, payload, content_type = _edit_multipart(model, prompt, params, images, mask)
    return run_images(args, model, params, preview, "/images/edits", payload, content_type, out_dir, stem)


def _edit_multipart(model, prompt, params, images, mask):
    fields = [("model", model), ("prompt", prompt)] + [(name, str(value)) for name, value in params.items()]
    parts = [("image[]", image) for image in images] + ([("mask", mask)] if mask else [])
    boundary = "imagegen-" + uuid.uuid4().hex
    chunks = []
    for name, value in fields:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8") + b"\r\n")
    for name, image in parts:
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                       f'filename="{upload_name(image)}"\r\n'
                       f"Content-Type: {MIME[image['format']]}\r\n\r\n").encode("utf-8"))
        chunks.append(image["data"] + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    preview = {
        "method": "POST", "url": API_ROOT + "/images/edits", "body_format": "multipart/form-data",
        "fields": dict(fields),
        "files": [{"field": name, "filename": upload_name(image), "type": MIME[image["format"]],
                   "bytes": len(image["data"]), "pixels": pixels_of(image), "source": image["path"]}
                  for name, image in parts],
    }
    return preview, b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _edit_json(model, prompt, params, images, mask):
    body = {"model": model, "prompt": prompt, "images": [{"image_url": data_url(image)} for image in images]}
    if mask:
        body["mask"] = {"image_url": data_url(mask)}
    body.update(params)

    def summary(image, url):
        return f"data:{MIME[image['format']]};base64,... ({len(url):,} characters from {image['path']})"

    shown = dict(body)
    shown["images"] = [{"image_url": summary(image, item["image_url"])}
                       for image, item in zip(images, body["images"])]
    if mask:
        shown["mask"] = {"image_url": summary(mask, body["mask"]["image_url"])}
    preview = {"method": "POST", "url": API_ROOT + "/images/edits", "body_format": "json",
               "note": "JSON body with the images inlined as data URLs, because the multipart form "
                       "does not list moderation",
               "body": shown}
    return preview, json.dumps(body).encode("utf-8"), "application/json"


def run_images(args, model, params, preview, path, payload, content_type, out_dir, stem):
    count = params.get("n", 1)
    extension = EXTENSION[params.get("output_format", "png")]
    if args.dry_run:
        report = {"dry_run": True}
        report.update(preview)
        report["would_write"] = [planned_path(out_dir, stem, index, extension) for index in range(1, count + 1)]
        report["estimate"] = estimate(model, params)
        emit(report)
        return 0
    key = load_api_key()
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise UsageError(f"cannot create --out {out_dir}: {exc.strerror or exc}") from None
    if not os.access(out_dir, os.W_OK):
        raise UsageError(f"--out {out_dir} is not writable")
    verb = "editing" if path.endswith("edits") else "generating"
    note(f"{verb} {count} image{'s' if count > 1 else ''} with {model} (size {params.get('size', 'auto')}, "
         f"quality {params.get('quality', 'auto')}); this usually takes 20 s to 3 min")
    started = time.monotonic()
    response, request_id = api_call("POST", path, key, payload, content_type, args.timeout)
    items = response.get("data") if isinstance(response, dict) else None
    if not items:
        raise ApiError("the API returned no images" + (f" (request id {request_id})" if request_id else ""))
    files = save_images(items, out_dir, stem, response.get("output_format") or params.get("output_format"))
    result = {"files": files, "model": model,
              "size": response.get("size") or params.get("size", "auto"),
              "quality": response.get("quality") or params.get("quality", "auto")}
    for field in ("background", "output_format"):
        if response.get(field):
            result[field] = response[field]
    revised = [item["revised_prompt"] for item in items if isinstance(item, dict) and item.get("revised_prompt")]
    if revised:
        result["revised_prompt"] = revised[0] if len(set(revised)) == 1 else revised
    usage = response.get("usage")
    if isinstance(usage, dict):
        result["usage"] = usage
        result["est_cost_usd"] = cost_from_usage(usage)
    if request_id:
        result["request_id"] = request_id
    result["seconds"] = round(time.monotonic() - started, 1)
    emit(result)
    return 0


def cmd_models(args):
    check_timeout(args.timeout)
    if args.dry_run:
        emit({"dry_run": True, "method": "GET", "url": API_ROOT + "/models"})
        return 0
    key = load_api_key()
    started = time.monotonic()
    response, request_id = api_call("GET", "/models", key, timeout=args.timeout)
    entries = response.get("data") if isinstance(response, dict) else None
    ids = sorted({str(entry["id"]) for entry in entries or [] if isinstance(entry, dict) and entry.get("id")})
    result = {
        "image_models": [model for model in ids if "image" in model or model.startswith("dall-e")],
        "default_model": DEFAULT_MODEL, "default_available": DEFAULT_MODEL in ids,
        "fast_model": FAST_MODEL, "fast_available": FAST_MODEL in ids,
    }
    if not (result["default_available"] or result["fast_available"]):
        result["note"] = ("No gpt-image-2.5 model is listed for this key. The organization may need API "
                          "Organization Verification (https://platform.openai.com/settings/organization/general) "
                          "or a paid usage tier.")
    if request_id:
        result["request_id"] = request_id
    result["seconds"] = round(time.monotonic() - started, 1)
    emit(result)
    return 0


# --------------------------------------------------------------------------- command line

DESCRIPTION = """\
Generate and edit images with OpenAI's ChatGPT Images 2.5 models through the
OpenAI Images API: gpt-image-2.5-sunburst (default, best quality) or
gpt-image-2.5-flare (--fast: quicker, same price).

Run "imagegen.py COMMAND -h" for the options of a command."""

EPILOG = """\
API key: $OPENAI_API_KEY, else ~/.config/openai/api_key, else the macOS Keychain
item "openai-api-key". It is sent only to https://api.openai.com and never
printed. --dry-run prints the request without sending it and needs no key.

Output: one JSON object on stdout (files, model, size, quality, usage,
est_cost_usd, request_id, seconds). Progress notes and errors go to stderr.
Exit status: 0 success; 1 API or network error; 2 invalid arguments or input,
or no API key. 429 and 5xx responses are retried up to 3 times with backoff.

Every image is billed. Output cost per image at list price, for common sizes up
to 2880x2880: low $0.005-0.020, medium $0.010-0.045, high $0.041-0.178, xhigh
$0.074-0.316, max $0.165-0.712. Draft with --quality low.

examples:
  imagegen.py generate --prompt "a lighthouse at dusk, watercolor" \\
      --size 1536x1024 --quality low --out ./generated-images --name lighthouse
  imagegen.py edit --image room.png --mask room-mask.png \\
      --prompt "replace the sofa with a green velvet one" \\
      --size 1536x1024 --quality medium --out ./generated-images
  imagegen.py generate --prompt-file brief.txt --size 1920x1088 --quality high \\
      --out ./generated-images --dry-run
  imagegen.py models"""


def add_image_options(command, editing):
    prompt = command.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt", metavar="TEXT", help="the prompt (1-32,000 characters)")
    prompt.add_argument("--prompt-file", metavar="PATH",
                        help="read the prompt from a UTF-8 text file, or - for stdin")
    model = command.add_mutually_exclusive_group()
    model.add_argument("--model", metavar="ID",
                       help=f"model id (default {DEFAULT_MODEL}); also {FAST_MODEL}, the dated snapshots "
                            "gpt-image-2.5-sunburst-2026-09-08 and gpt-image-2.5-flare-2026-09-08, or the "
                            "previous-generation gpt-image-2")
    model.add_argument("--fast", action="store_true",
                       help=f"use {FAST_MODEL}: faster (roughly 30 s against 40 s), quality comparable "
                            "to GPT Image 2, same price")
    command.add_argument("--size", metavar="WxH",
                         help="auto (API default) or WIDTHxHEIGHT: both edges multiples of 16 and at most "
                              "3840, long:short at most 3:1, 655,360 to 8,294,400 pixels. Common: 1024x1024, "
                              "1536x1024, 1024x1536, 2048x2048, 2048x1152, 3840x2160, 2160x3840. Above "
                              "2560x1440 pixels is experimental. An invalid size is rejected with the nearest "
                              "valid one")
    command.add_argument("--quality", type=str.lower, choices=QUALITIES,
                         help="auto is the API default; xhigh and max need a 2.5 model. Use low for drafts")
    command.add_argument("--background", type=str.lower, choices=BACKGROUNDS,
                         help="auto is the API default; transparent needs png or webp output")
    command.add_argument("--format", type=str.lower, choices=FORMATS,
                         help="output format: png (API default), webp, or jpeg/jpg (fastest)")
    command.add_argument("--compression", type=int, metavar="0-100",
                         help="compression level for webp or jpeg output (API default 100)")
    command.add_argument("--n", type=int, metavar="1-10", help="number of images (default 1); multiplies the cost")
    moderation_help = "content filtering: auto (API default) or low (less restrictive)"
    if editing:
        moderation_help += "; low sends a JSON body with the images inlined, as only that form documents it"
    command.add_argument("--moderation", type=str.lower, choices=MODERATIONS, help=moderation_help)
    command.add_argument("--out", required=True, metavar="DIR", help="directory to save images in (created if missing)")
    command.add_argument("--name", metavar="STEM",
                         help="file name stem (default: taken from the prompt). Images are saved as STEM-1.png, "
                              "STEM-2.png, ...; existing files are never overwritten (if STEM-1.png exists, "
                              "the next one is STEM-1-2.png)")
    add_run_options(command)


def add_run_options(command, estimates=True):
    command.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                         help=f"give up after this many seconds, retries included (default {DEFAULT_TIMEOUT:.0f})")
    command.add_argument("--dry-run", action="store_true",
                         help=("print the request and a cost estimate" if estimates else "print the request")
                         + " without sending it; needs no API key")


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Keep model ids such as gpt-image-2.5-sunburst on one line."""

    def _split_lines(self, text, width):
        return textwrap.wrap(self._whitespace_matcher.sub(" ", text).strip(), width, break_on_hyphens=False)


def build_parser():
    formatter = _HelpFormatter
    parser = argparse.ArgumentParser(prog="imagegen.py", description=DESCRIPTION, epilog=EPILOG,
                                     formatter_class=formatter)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    generate = commands.add_parser(
        "generate", help="text to image (POST /v1/images/generations)", formatter_class=formatter,
        description="Generate images from a text prompt with ChatGPT Images 2.5.")
    add_image_options(generate, editing=False)
    generate.set_defaults(handler=cmd_generate)

    edit = commands.add_parser(
        "edit", help="edit images, combine 1-16 reference images, or inpaint with a mask (POST /v1/images/edits)",
        formatter_class=formatter,
        description="Edit images, generate from 1-16 reference images, or inpaint with a mask, with\n"
                    "ChatGPT Images 2.5. In the prompt, number the images and give each a role.")
    edit.add_argument("--image", action="append", required=True, metavar="PATH",
                      help="input or reference image (PNG, JPEG or WebP, under 50 MB); repeat for up to 16. "
                           "A mask applies to the first one")
    edit.add_argument("--mask", metavar="PATH",
                      help="PNG with an alpha channel, the same size as the first --image; fully transparent "
                           "pixels mark the area to change")
    add_image_options(edit, editing=True)
    edit.set_defaults(handler=cmd_edit)

    models = commands.add_parser(
        "models", help="list the image model ids this API key can use (GET /v1/models; free)",
        formatter_class=formatter, description="List the image model ids this API key can use. Costs nothing.")
    add_run_options(models, estimates=False)
    models.set_defaults(handler=cmd_models)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except UsageError as exc:
        print("imagegen: error: " + redact(str(exc)), file=sys.stderr)
        return 2
    except ApiError as exc:
        print("imagegen: error: " + redact(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("imagegen: interrupted; a request already sent may still finish and be billed", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

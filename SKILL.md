---
name: chatgpt-image
description: >-
  Generate or edit images with OpenAI's ChatGPT Images 2.5 (gpt-image-2.5-sunburst,
  or gpt-image-2.5-flare for speed) through a bundled CLI that calls the OpenAI
  Images API and saves PNG, WebP or JPEG files. Use when asked to generate, create,
  draw, render, illustrate, design, mock up or edit an image, illustration, photo,
  icon, logo concept, banner, hero image, wallpaper or app mockup with ChatGPT Image,
  GPT Image, gpt-image, DALL-E or OpenAI, or when a generated raster image is wanted
  and no other image tool is named. Also for editing existing images: restyle,
  combine reference images, inpaint with a mask, swap or remove a background, extend
  a canvas. Every call is billed to the user's OpenAI account. Do not invoke for
  charts, diagrams or vector graphics better drawn in code (SVG, HTML, SwiftUI,
  matplotlib), or when the user names a different image service.
argument-hint: "[what to generate or edit]"
---

# ChatGPT Image 2.5

Generate and edit images with OpenAI's ChatGPT Images 2.5 models. The bundled CLI,
`scripts/imagegen.py` in this skill's directory (Python 3 standard library, nothing to
install), calls the OpenAI Images API, saves the images and prints one JSON object. The
commands below assume the skill is installed at `~/.claude/skills/chatgpt-image`; if its
base directory is elsewhere, use that path instead:

```
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py {generate|edit|models} ...
```

| Model | Flag | Use it for |
|---|---|---|
| `gpt-image-2.5-sunburst` | default | finals, fine detail, text in images; roughly 40 s |
| `gpt-image-2.5-flare` | `--fast` | drafts and quick iterations; roughly 30 s, quality close to GPT Image 2 |

Both cost the same per token. To pin a version, pass `--model gpt-image-2.5-sunburst-2026-09-08`
(or `gpt-image-2.5-flare-2026-09-08`).

## Every call costs money

Each image is billed to the user's OpenAI account. Output cost per image at list price, in US dollars:

| `--quality` | 1024x1024 | 1536x1024 | 2048x2048 or 3840x2160 | 2880x2880 |
|---|---|---|---|---|
| low | 0.006 | 0.005 | 0.011–0.012 | 0.020 |
| medium | 0.013 | 0.010 | 0.026–0.027 | 0.045 |
| high | 0.053 | 0.041 | 0.100–0.107 | 0.178 |
| xhigh | 0.094 | 0.074 | 0.178–0.190 | 0.316 |
| max | 0.211 | 0.165 | 0.400–0.428 | 0.712 |

That is about half a cent to 71 cents per image; 2880x2880, the largest square, costs the most.
`--n` multiplies it; the prompt (5 USD per 1M tokens) and the input images of an edit (8 USD per
1M) add a little.

- Draft at `--quality low`, with `--fast` for speed. Raise the quality only for the chosen final.
- Always pass `--size` and `--quality`. With `auto` the API picks, and it may pick an expensive tier.
- Use `xhigh` or `max` only when a lower tier misses a concrete requirement, such as small text or dense detail.
- Before generating more than 4 images for one request (in one call or across several), or any
  batch of 2 or more images at `high`, `xhigh` or `max`, confirm with the user and quote the
  estimate that `--dry-run` prints.
- After each run, tell the user its `est_cost_usd`.

## Generate

Save into `<scratchpad>/images`, replacing `<scratchpad>` with the session scratchpad directory
from your system prompt; if there is none, use `"${TMPDIR:-/tmp}/chatgpt-image"`, which is outside
the user's project. Copy or move images into a project only when the user asks.

A call takes 20 seconds to 3 minutes. Run it with the Bash tool's `timeout: 600000`. For long
jobs (several images at `xhigh` or `max`, or above 2K), use `run_in_background: true` and raise
`--timeout` (default 540 s).

```bash
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py generate \
  --prompt "A red fox asleep in fresh snow at dawn, soft pink light, shallow depth of field" \
  --size 1536x1024 --quality low --fast --out "<scratchpad>/images" --name fox
```

For long prompts, or prompts full of quotes, read the prompt from stdin:

```bash
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py generate --prompt-file - \
  --size 1024x1024 --quality medium --out "<scratchpad>/images" --name mug <<'PROMPT'
Product photo of a matte white ceramic mug on a pale oak table, soft window light from the left.
The mug reads "Morning" in bold black sans-serif, centred.
PROMPT
```

Options shared by `generate` and `edit`:

- `--size WxH` and `--quality low|medium|high|xhigh|max`; both default to `auto`.
- `--n 1-10`: images per call.
- `--format png|webp|jpeg`: png is the default and jpeg the fastest. `--compression 0-100` applies to webp and jpeg.
- `--background transparent|opaque|auto`. Transparent needs png or webp.
- `--moderation low`; see Limits.
- `--fast`, or `--model ID`.
- `--out DIR` (required) and `--name STEM`. Files are saved as `STEM-1.png`, `STEM-2.png` and so
  on, and the default stem comes from the prompt. Existing files are never overwritten: if
  `fox-1.png` exists, the next one is saved as `fox-1-2.png`.
- `--dry-run` prints the request (an edit's input images summarised) and an output-cost estimate.
  It sends nothing and needs no key.
- `--timeout SECONDS`.

The JSON on stdout has `files` (absolute paths), `model`, `size`, `quality` and `seconds`, plus
`background`, `output_format`, `revised_prompt`, `usage`, `est_cost_usd` and `request_id` when the
API returns them. If `est_cost_usd` is missing, quote the `--dry-run` estimate instead. Exit codes:
0 success; 1 API or network error, or the images could not be saved, with the message on stderr;
2 bad arguments, a bad input file or no key, with a message that says what to fix; 130 interrupted.
An invalid `--size` is rejected with the nearest valid size.

`imagegen.py models` is free. It checks the key and lists the image models the key can use;
`default_available: false` means the 2.5 models are not enabled for the account (see Key setup).

## Look at the result, then iterate

1. Open every file with the Read tool, which displays the image, before you present it. Check it
   against the request point by point: subject, composition, the spelling of any text, hands and
   faces, cut-off elements and artifacts.
2. Give the user the paths, what you see and the cost.
3. Change one thing per step. For a new direction, adjust the prompt and generate again. To refine
   a good image, pass it back in:
   `edit --image <previous file> --prompt "Change only ... Keep everything else exactly the same." --size <its size> --quality <quality> --out <same folder>`
4. If a region must stay pixel-identical, such as a logo, UI chrome or a product label, composite
   the original back over the result instead of relying on the model.

## Sizes and aspect ratios

There is no aspect-ratio parameter; `--size` sets the shape. A custom `WIDTHxHEIGHT` needs both
edges to be multiples of 16, each edge at most 3840, a long:short ratio of at most 3:1, and
655,360 to 8,294,400 pixels in total (the largest are 3840x2160 and 2880x2880). Sizes above
2560x1440 pixels are experimental. For any other target, pass the exact target size: the script
rejects it and prints the nearest valid size and, when that size covers the target, the `sips`
command that crops or shrinks it to the target.

| Target | `--size` | Then |
|---|---|---|
| square, icon, avatar | 1024x1024 (2048x2048 is experimental) | |
| photo, 3:2 or 2:3 | 1536x1024 or 1024x1536 | |
| 4:3 or 3:4 | 1152x864 or 864x1152 | |
| desktop or slide, 16:9 at 1080p | 1920x1088 | `sips -c 1080 1920 in.png --out out.png` |
| desktop, 16:9 at 1440p | 2560x1440 | |
| 4K | 3840x2160 or 2160x3840 (experimental) | |
| phone, 9:16 (story, wallpaper) | 1088x1920 | `sips -c 1920 1080 in.png --out out.png` |
| iPhone 6.7" full screen (1290x2796) | 1296x2800 | `sips -c 2796 1290 in.png --out out.png` |
| iPhone 6.9" full screen (1320x2868) | 1328x2880 (experimental) | `sips -c 2868 1320 in.png --out out.png` |
| ultrawide, 21:9 | 2560x1088 | `sips -c 1080 2560 ...` |
| link preview (Open Graph) | 1200x640 | `sips -c 630 1200 ...` |
| banner wider than 3:1 | 2304x768 | crop the height, e.g. `sips -c 576 2304 ...` for 4:1 |

`sips -c H W` crops around the centre and `sips -z H W` resamples; both ship with macOS. Draft
at a smaller size of the same shape (for 16:9, 1536x864) and generate the final at full size.

To extend a canvas instead of cropping it (outpainting), pad the image with transparent pixels
to a valid size. Then run `edit` with the padded file as both `--image` and `--mask`, `--size`
set to the canvas size, and a prompt that describes what fills the new area. This padding
one-liner needs Pillow (`python3 -m pip install --user pillow`); the CLI itself does not:

```bash
python3 -c 'from PIL import Image; s = Image.open("in.png").convert("RGBA"); c = Image.new("RGBA", (2560, 1088), (0, 0, 0, 0)); c.paste(s, ((2560 - s.width) // 2, (1088 - s.height) // 2)); c.save("padded.png")'
```

## Transparent backgrounds

Pass `--background transparent --format png` (or `webp`; JPEG cannot hold transparency). In the
prompt, describe an isolated subject with no floor, scenery or cast shadow, for example "a single
glossy 3D icon of a paper plane, isolated, no background, no shadow". Check the edges in the
result; hair, smoke or glass may need a second try.

## Edit with reference images and masks

`edit` takes 1 to 16 `--image` files (PNG, JPEG or WebP, each under 50 MB) and all the `generate`
options. With `--moderation low` the images are sent inline instead, so each image and the mask
must be under about 15 MB:

```bash
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py edit \
  --image product.png --image style-ref.jpg \
  --prompt "Image 1 is the product: keep its shape, colours and label exactly. Image 2 is the style reference. Put the product on a marble counter in the lighting and palette of Image 2." \
  --size 1024x1536 --quality medium --out "<scratchpad>/images" --name product-marble
```

- Number the images in the prompt and give each one a role: subject, style, background or logo.
- Say "change only X" and list what must stay the same, such as the face, pose, framing, text or colours.
- A mask (`--mask m.png`) is a PNG with an alpha channel, the same pixel size as the first
  `--image`, and it applies to that first image. Fully transparent pixels mark the area to change.
  The model treats the mask as guidance, not an exact stencil. To convert a black-and-white mask
  (black = change, white = keep), with Pillow (`python3 -m pip install --user pillow`):

  ```bash
  python3 -c 'from PIL import Image; m = Image.open("mask-bw.png").convert("L"); r = m.convert("RGBA"); r.putalpha(m); r.save("mask.png")'
  ```

- Keep brand marks consistent by passing the user's real logo, product shots or earlier approved
  images as `--image` references, and say "use the logo from Image 2 exactly as given; do not
  redraw, recolour or add text to it". Marks can still drift, so for an exact logo leave clear
  space and composite the real file afterwards. For a series, reuse the same style wording and
  pass an approved image as the style reference.

## Writing prompts

Write the prompt as a short art-direction brief, roughly in this order:

1. **Subject**: what or who, with concrete attributes (material, colour, count, expression, pose).
2. **Composition**: shot and framing (close-up, wide, top-down, centred, rule of thirds), where
   things sit, and any empty space to keep for copy.
3. **Setting and lighting**: place, time of day, light quality and direction (soft window light,
   golden hour, studio softbox, rim light).
4. **Style**: medium and finish (photograph on a 50 mm lens with shallow depth of field, flat
   vector illustration, 3D clay render, watercolour, isometric), palette (hex codes work) and mood.
5. **Text**: the exact words in double quotes, plus font style, weight, colour and placement, for
   example: a headline that reads "Ship faster" in bold white sans-serif, top left. Keep in-image
   text short and check its spelling; precise text placement can still fail.
6. **Exclusions**, stated plainly: "no watermark, no extra text, no people".

Prefer concrete details to piles of adjectives. For UI or device mockups, describe the layout
region by region. Expect variation between runs; exact layout and consistency across images are
known weak spots.

## Limits

- No images of real private people, whether photos, likenesses or identifiable depictions, and
  nothing sexual, demeaning or deceptive about any real person.
- Do not impersonate organisations or people: no fake official documents, IDs, receipts or
  screenshots, and no ads or branding presented as someone else's.
- Respect trademarks and copyright. Do not reproduce other companies' logos, mascots or characters
  for the user's own branding; use the user's own assets as references.
- OpenAI moderates prompts and outputs. A blocked request fails with HTTP 400 `moderation_blocked`
  and names the stage (input or output). Rephrase the request for its legitimate purpose; never
  resend it unchanged or try to slip it past the filter. `--moderation low` relaxes filtering for
  benign content that keeps getting flagged, but it still filters.
- Outputs carry C2PA provenance metadata that marks them as AI-generated.

## Key setup

The script reads the key from `OPENAI_API_KEY`, then `~/.config/openai/api_key`, then the macOS
Keychain item `openai-api-key`. It sends the key only to https://api.openai.com and never prints it.

If the script exits 2 with "no OpenAI API key", ask the user to set one up in their own terminal.
Never ask them to paste the key into the chat, and never read, print or copy the key yourself.
Give them these options; the key comes from https://platform.openai.com/api-keys:

- Keychain (recommended). It prompts for the key, so the key stays out of shell history:
  `security add-generic-password -a "$USER" -s openai-api-key -w`
- File. Run this, paste the key when it waits (nothing is shown) and press Enter:
  `mkdir -p ~/.config/openai && (umask 077 && read -rs k && printf '%s\n' "$k" > ~/.config/openai/api_key) && chmod 600 ~/.config/openai/api_key`
- Environment variable: add `export OPENAI_API_KEY=...` to the shell's startup file (`~/.zshrc` or
  `~/.bashrc`), then open a new terminal and start Claude Code from it.

Do not use a key that another tool stored, such as another plugin's Keychain item, unless the
user says to.

Then run `models`. If the 2.5 models are missing, or calls fail with HTTP 403, the organization
probably needs API Organization Verification at
https://platform.openai.com/settings/organization/general. Image models also need a paid usage
tier; the Build tier allows 20 images a minute.

## Errors

The script itself retries 429 and 5xx responses up to 3 times with backoff.

| Error | What to do |
|---|---|
| exit 2 with a validation message | fix the option as the message says |
| HTTP 401 | the key was rejected; the user must store a valid one |
| HTTP 403, or 404 for the model | organization verification or no access; run `models` |
| HTTP 429 `insufficient_quota` | the account is out of credit; the user must add billing |
| HTTP 429 after the retries | rate limited; wait a minute or lower `--n` |
| HTTP 400 `moderation_blocked` | rephrase; do not resend it unchanged |
| timeout | the request may have finished and been billed; retry once with `--fast`, a lower quality or a smaller size, or raise `--timeout` |

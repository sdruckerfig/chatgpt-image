# chatgpt-image

A [Claude Code](https://claude.com/claude-code) skill that lets Claude generate and edit images with
OpenAI's ChatGPT Images 2.5 models: `gpt-image-2.5-sunburst` for finals, and `gpt-image-2.5-flare`
for fast drafts.

Ask Claude for an image ("a hero image of a lighthouse at dusk", "put this product on a marble
counter") and it writes an art-direction prompt, runs the bundled CLI, looks at the result and
iterates. It drafts at low quality, reports what each run cost, and asks before an expensive batch.

The CLI, `scripts/imagegen.py`, is one Python file that uses only the standard library, so there is
nothing to install.

## Install

```bash
git clone https://github.com/sdruckerfig/chatgpt-image.git ~/.claude/skills/chatgpt-image
```

Start a new Claude Code session. The skill loads by itself when you ask for an image, or you can
call it directly: `/chatgpt-image a red fox asleep in fresh snow at dawn`.

You need Python 3.9 or newer and an OpenAI API account that can use the gpt-image-2.5 models. Two
optional image-prep one-liners in `SKILL.md`, for padding a canvas and converting a black-and-white
mask, also need Pillow: `python3 -m pip install --user pillow`. The Keychain option below and the
`sips` resizing commands that `SKILL.md` and the script suggest are macOS-only; on other systems,
use the environment variable or the key file, and resize with any image tool.

## Add your OpenAI API key

Create a key at https://platform.openai.com/api-keys and store it yourself, in a terminal. Never
paste it into a chat. The script checks these places in order:

1. The `OPENAI_API_KEY` environment variable.
2. The file `~/.config/openai/api_key`. Run this, paste the key when it waits (nothing is shown)
   and press Enter:
   `mkdir -p ~/.config/openai && (umask 077 && read -rs k && printf '%s\n' "$k" > ~/.config/openai/api_key) && chmod 600 ~/.config/openai/api_key`
3. The macOS Keychain item `openai-api-key`. This is the recommended option: the command prompts for
   the key, so it stays out of your shell history:
   `security add-generic-password -a "$USER" -s openai-api-key -w`

The key is sent only to `https://api.openai.com`. It is never printed, and the script refuses
redirects, so it is never sent anywhere else.

Check the setup. This call is free:

```bash
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py models
```

`"default_available": false` means your organization can't use the 2.5 models yet. It probably
needs [API Organization Verification](https://platform.openai.com/settings/organization/general)
and a paid usage tier.

## What it costs

Every image is billed to your OpenAI account. Output cost per image at list price:

| Quality | 1024x1024 | 1536x1024 | 2048x2048 or 3840x2160 | 2880x2880 |
|---|---|---|---|---|
| low | $0.006 | $0.005 | $0.011–0.012 | $0.020 |
| medium | $0.013 | $0.010 | $0.026–0.027 | $0.045 |
| high | $0.053 | $0.041 | $0.100–0.107 | $0.178 |
| xhigh | $0.094 | $0.074 | $0.178–0.190 | $0.316 |
| max | $0.211 | $0.165 | $0.400–0.428 | $0.712 |

The prompt ($5 per 1M tokens) and an edit's input images ($8 per 1M tokens) add a little. These are
October 2026 list prices; check OpenAI's pricing page for changes.

The skill tells Claude to draft at low quality, report each run's estimated cost, and ask you before
generating more than 4 images for one request, or 2 or more at high quality or above.

## Use the CLI directly

```bash
# Text to image
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py generate \
  --prompt "a lighthouse at dusk, watercolor" \
  --size 1536x1024 --quality low --out ./generated-images --name lighthouse

# Edit an image; the mask's fully transparent pixels mark the area to change
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py edit \
  --image room.png --mask room-mask.png \
  --prompt "replace the sofa with a green velvet one" \
  --size 1536x1024 --quality medium --out ./generated-images

# Print the request and a cost estimate without sending anything (needs no key)
python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py generate \
  --prompt "a lighthouse at dusk" --size 1920x1088 --quality high \
  --out ./generated-images --dry-run
```

Each run prints one JSON object with the saved `files`, the `model`, `size`, `quality` and
`seconds`, plus the `usage`, `est_cost_usd` and `request_id` when the API returns them. Existing
files are never overwritten: if `lighthouse-1.png` exists, the next one is `lighthouse-1-2.png`.
The exit status is 0 on success, 1 for an API or network error, and 2 for invalid arguments or
input, or a missing key. `python3 ~/.claude/skills/chatgpt-image/scripts/imagegen.py COMMAND -h`
lists every option of `generate`, `edit` or `models`.

## Files

- `SKILL.md`: what Claude follows. It covers the cost rules, prompt writing, sizes and aspect
  ratios, editing with reference images and masks, and content limits.
- `scripts/imagegen.py`: the CLI.

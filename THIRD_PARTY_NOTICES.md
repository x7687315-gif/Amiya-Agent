# Third-Party Notices

This document lists third-party software, libraries, and assets that this project
uses, depends on, or references. It is provided so that redistributors can honor
each component's license.

## Bundled / declared runtime dependencies

These packages are declared in `requirements.txt` and are required to run the app.
They are **not** modified or vendored; install them from PyPI.

| Component      | Version (pinned) | License        | Source / Upstream                                            |
| -------------- | ---------------- | -------------- | ------------------------------------------------------------ |
| Flet          | 0.86.5           | Apache-2.0     | https://github.com/flet-dev/flet                            |
| requests      | >=2.31           | Apache-2.0     | https://github.com/psf/requests                             |
| PyYAML        | >=6.0            | MIT            | https://github.com/yaml/pyyaml                              |
| python-dotenv | >=1.0            | BSD-3-Clause   | https://github.com/theskumar/python-dotenv                  |

## Development / test dependency (not shipped at runtime)

| Component | License | Source / Upstream                       |
| --------- | ------- | --------------------------------------- |
| pytest    | MIT     | https://github.com/pytest-dev/pytest   |

## External services — NOT bundled, NOT redistributed

The following are integrated via adapters/config only. **No model weights,
checkpoints, voice samples, SDK code, or API keys are included in this repository.**
Users must obtain and license them separately:

- **GPT-SoVITS** (external open-source TTS toolkit): referenced only through the
  `AMIYA_GPT_SOVITS_DIR` environment variable / local config. Its license is governed
  by its own upstream repository. This project ships only the TTS *interface*,
  *adapter*, *config schema*, and a *mock*.
- **DeepSeek** (cloud LLM API): only the provider/adapter and API-call logic are
  shipped. The real API key lives solely in the user's local `.env` (git-ignored).
  No key, token, or SDK is committed.

## Bundled media assets

- `resources/avatar/default.png` and `resources/skins/example/avatar.png`,
  `resources/skins/example/background.jpg` are **originally generated neutral
  placeholder images** (programmatic gradients + letterform), created specifically
  for this public release. They contain no third-party copyrighted material and are
  redistributed under the project license.

## What is deliberately excluded

No model weights (`*.pth`, `*.ckpt`, `*.onnx`, `*.bin`, `*.safetensors`), voice data
(`*.wav`, `*.mp3`), fonts, private images, private configuration, user chat logs, or
user memory databases are included in this repository. See `.gitignore` for the full
list of excluded local/private artifact patterns.

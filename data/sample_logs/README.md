# Sample SDK Manager log archives

Real `.zip` exports downloaded from public NVIDIA Developer Forum posts, used for testing the troubleshoot pipeline against real-world input. Each archive is `parse_install_log`-compatible and can be passed to `python main.py --troubleshoot <path>`.

## Provenance

| File | Source forum thread | Failure type |
|---|---|---|
| `SDKM_logs_JetPack_6.1_Linux_for_Jetson_AGX_Orin_modules_2024-09-30_16-09-17.zip` | [Can not flash JetPack 6.1 on AGX Orin via SDK Manager](https://forums.developer.nvidia.com/t/can-not-flash-jetpack-6-1-on-jetson-agx-orin-via-sdk-manager/308377) | JetPack 6.1 flash failure on AGX Orin |
| `SDKM_logs_JetPack_6.2.2_Linux_for_Jetson_AGX_Orin_modules_2026-04-10_10-51-27.zip` | [How to flash MCU's firmware on AGX Orin 64G DK](https://forums.developer.nvidia.com/t/how-to-flash-mcus-firmware-on-agx-orin-64g-dk/366168) | MCU firmware flash, JetPack 6.2.2 |
| `SDKM_logs_2025-01-03_13-01-22.zip` | [Flashing Orin Nano via SDK Fails](https://forums.developer.nvidia.com/t/flashing-orin-nano-via-sdk-fails/318733) | WSL-based flash failure (short filename — only timestamp encoded in filename, metadata inferred by agent from log body) |
| `SDKM_logs_JetPack_6.2_Linux_for_Jetson_AGX_Orin_64GB_2025-01-26_11-41-13.zip` | [Install JetPack 6.2 failed with SDK manager on AGX orin 64G](https://forums.developer.nvidia.com/t/install-jetpack-6-2-failed-with-sdk-manager-on-agx-orin-64g/321524) | JetPack 6.2 install failure on AGX Orin 64GB |
| `SDKM_logs_JetPack_6.2_Linux_for_Jetson_Orin_Nano_[8GB_developer_kit_version]_2025-03-21_12-48-45.zip` | [Flashing JetPack 6.2 ... command error code: 11](https://forums.developer.nvidia.com/t/flashing-jetpack-6-2-using-sdk-manager-displays-command-error-code-11/327911) | JetPack 6.2 Orin Nano + bracketed board variant in filename |

## Redaction

These archives have been redacted before commit to remove privacy-sensitive content from other users' logs:

- `/home/<username>/` → `/home/REDACTED/`
- `C:\Users\<username>\` → `C:\Users\REDACTED\`
- Email addresses → `REDACTED@example.com`
- Non-NVIDIA IP addresses → `X.X.X.X` (the recovery USB IP `192.168.55.1` is kept because it's a documented NVIDIA constant, not personal)
- Identifiable company names found in paths → `REDACTED`

All other content — error messages, error codes, component names, target IDs, JetPack versions, timestamps, log structure — is preserved verbatim. The redaction script is at `scripts/redact_logs.py` for reproducibility.

## Usage

```powershell
python main.py --troubleshoot data/sample_logs/SDKM_logs_2025-01-03_13-01-22.zip
```

This runs the full troubleshoot pipeline (parse → agent reads tail → web_search → synthesize fix) against the chosen archive. The agent's diagnosis quality on these is the demo's strongest evidence — see the README "Test corpus" section for an example transcript.

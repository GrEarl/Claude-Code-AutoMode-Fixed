# Claude Code - Auto Mode Fixed

Automated patcher that enables **Auto Mode** (`claude --enable-auto-mode`) in
Claude Code for users running through a proxy (`ANTHROPIC_BASE_URL`).

A GitHub Actions workflow checks for new Claude Code releases every 6 hours,
patches the binaries for **all platforms**, and publishes them as a GitHub Release.

---

## Why is this needed?

Claude Code gates Auto Mode behind a server-side feature flag
(`tengu_auto_mode_config`) fetched via GrowthBook remote evaluation at
`api.anthropic.com`.

When you point Claude Code at a **proxy** (via `ANTHROPIC_BASE_URL`), the
GrowthBook client cannot establish trust with Anthropic's servers.
It falls back to a hardcoded default: `"disabled"`.
This triggers the circuit breaker:

> auto mode temporarily unavailable

This repo patches that default from `"disabled"` to `"enabled"`.

## How the patch works

Inside the compiled binary, the minified JS contains a function
`parseAutoModeEnabledState` that validates the feature flag value:

```js
function XX(Y) {
  if (Y === "enabled" || Y === "disabled" || Y === "opt-in") return Y;
  return DEFAULT_VAR;  // DEFAULT_VAR = "disabled"
}
```

The patcher locates `DEFAULT_VAR` by anchoring on the unique `"opt-in"`
string literal, then replaces the declaration (same byte length):

```
VARNAME="disabled";var  ->  VARNAME="enabled"; var
```

When GrowthBook skips the fetch (no trust), the function now returns
`"enabled"` instead of `"disabled"`, and the circuit breaker stays off.

Real Anthropic users are unaffected -- their value comes from the live
GrowthBook response.

## Downloads

Go to [**Releases**](../../releases) and grab the binary for your platform:

| Platform | File |
|----------|------|
| Linux x64 | `claude-linux-x64` |
| Linux ARM64 | `claude-linux-arm64` |
| Linux x64 (musl/Alpine) | `claude-linux-x64-musl` |
| macOS x64 (Intel) | `claude-macos-x64` |
| macOS ARM64 (Apple Silicon) | `claude-macos-arm64` |
| Windows x64 | `claude-windows-x64.exe` |

### Installation

Replace your existing `claude` binary:

```bash
# Linux / macOS
cp claude-linux-x64 ~/.local/bin/claude   # adjust filename for your platform
chmod +x ~/.local/bin/claude
```

```powershell
# Windows
copy claude-windows-x64.exe %USERPROFILE%\.local\bin\claude.exe
```

Then launch Claude Code with auto mode:

```bash
claude --enable-auto-mode
```

## Manual patching

```bash
python scripts/patch_binary.py /path/to/claude /path/to/claude-patched
```

## Workflow

The GitHub Actions workflow (`.github/workflows/patch-and-release.yml`):

1. Runs every 6 hours (+ manual dispatch)
2. Reads the latest version from the [GCS distribution bucket](https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases/latest)
3. Downloads platform binaries from GCS (Linux, macOS) and npm (Windows)
4. Applies the patch via `scripts/patch_binary.py`
5. Publishes a GitHub Release with all patched binaries

Inspired by [claudex](https://github.com/EdamAme-x/claudex).

## Disclaimer

This project is not affiliated with Anthropic.
Use at your own risk.

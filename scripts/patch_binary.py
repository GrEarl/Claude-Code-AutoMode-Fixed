#!/usr/bin/env python3
"""
Patch Claude Code binary to enable Auto Mode by default.

When using Claude Code through a proxy (custom ANTHROPIC_BASE_URL),
the GrowthBook feature flag system cannot establish trust with Anthropic's
servers. This causes the auto mode circuit breaker to default to "disabled".

This patch changes the default value from "disabled" to "enabled", allowing
auto mode to work through proxy servers.

Strategy (robust against minifier renames):
  1. Locate the parseAutoModeEnabledState function by its unique signature:
       function XX(Y){if(Y==="enabled"||Y==="disabled"||Y==="opt-in")return Y;return ZZ}
     The three string literals "enabled"/"disabled"/"opt-in" are application
     constants that survive minification.
  2. Extract the default-variable name (ZZ) from `return ZZ}`.
  3. Find the declaration  ZZ="disabled";  and patch it to  ZZ="enabled";
     with a 1-byte space pad to keep binary size identical.

Effect: when GrowthBook is unreachable (proxy users), q3_(undefined)
returns "enabled" instead of "disabled", so the circuit breaker stays off.
Real Anthropic users are unaffected (their value comes from GrowthBook).
"""

import re
import sys
import os
import hashlib


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Core: find the default variable name dynamically, then build the patch pair
# ---------------------------------------------------------------------------

# The parseAutoModeEnabledState function checks for exactly these three
# string values.  The surrounding function/variable names are minified,
# but these literals are stable across builds.
#
# Matches:
#   function Ab(c){if(c==="enabled"||c==="disabled"||c==="opt-in")return c;return Xy}
#
# Captures group(1) = default variable name (e.g. "Xy", "hAM", ...)

_FUNC_RE = re.compile(
    rb'function \w+\(\w+\)\{'
    rb'if\(\w+===(?:"enabled"|\'enabled\')'
    rb'\|\|\w+===(?:"disabled"|\'disabled\')'
    rb'\|\|\w+===(?:"opt-in"|\'opt-in\')\)'
    rb'return \w+;'
    rb'return (\w+)'
    rb'\}'
)

# After we know the variable name, the declaration looks like:
#   ,VarName="disabled";var   or   ,VarName="disabled";let
# We replace "disabled" (8 chars) with "enabled" (7 chars) + 1 space pad
# to keep the same byte length.


def _find_patch_pair(data: bytes):
    """Return (target, replacement) bytes or None if pattern not found."""

    m = _FUNC_RE.search(data)
    if not m:
        return None

    var_name = m.group(1)  # e.g. b'hAM'

    # Build the exact byte sequences
    target = var_name + b'="disabled";var'
    replacement = var_name + b'="enabled"; var'

    # Sanity: lengths must match (8-char "disabled" -> 7-char "enabled" + 1 space)
    assert len(target) == len(replacement), (
        f"Length mismatch: {len(target)} vs {len(replacement)}"
    )

    return var_name.decode(), target, replacement


def _already_patched(data: bytes) -> bool:
    """Check if the binary is already patched (default = "enabled")."""
    m = _FUNC_RE.search(data)
    if not m:
        return False
    var_name = m.group(1)
    patched_decl = var_name + b'="enabled"; var'
    return patched_decl in data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch_binary(input_path: str, output_path: str | None = None) -> bool:
    if output_path is None:
        output_path = input_path

    real_path = os.path.realpath(input_path)

    with open(real_path, "rb") as f:
        data = f.read()

    print(f"Input:  {real_path}")
    print(f"Size:   {len(data):,} bytes")
    print(f"SHA256: {sha256(data)}")
    print()

    # Check already patched
    if _already_patched(data):
        print("  [auto_mode_default] Already patched")
        if output_path != input_path:
            with open(output_path, "wb") as f:
                f.write(data)
        print("\nNo changes needed")
        return True

    # Find the patch target dynamically
    result = _find_patch_pair(data)
    if result is None:
        print("  [auto_mode_default] FAIL: could not locate parseAutoModeEnabledState function")
        print("  Looked for: function X(Y){if(Y===\"enabled\"||Y===\"disabled\"||Y===\"opt-in\")return Y;return Z}")
        print("  The binary format may have changed significantly.")
        return False

    var_name, target, replacement = result
    print(f"  [auto_mode_default] Found default variable: {var_name}")

    count = data.count(target)
    if count == 0:
        print(f"  [auto_mode_default] FAIL: declaration '{target.decode()}' not found")
        return False

    patched = data.replace(target, replacement)
    assert len(patched) == len(data), f"Size changed: {len(data)} -> {len(patched)}"

    print(f"  [auto_mode_default] OK - {count} occurrences patched")
    print(f"    {target.decode()!r}  ->  {replacement.decode()!r}")

    with open(output_path, "wb") as f:
        f.write(patched)

    print(f"\nOutput: {output_path}")
    print(f"SHA256: {sha256(patched)}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input_binary> [output_binary]")
        print()
        print("If output_binary is omitted, the input is patched in-place.")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(inp):
        print(f"ERROR: File not found: {inp}")
        sys.exit(1)

    ok = patch_binary(inp, out)
    sys.exit(0 if ok else 1)

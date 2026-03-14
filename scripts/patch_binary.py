#!/usr/bin/env python3
"""
Patch Claude Code binary to enable Auto Mode by default.

When using Claude Code through a proxy (custom ANTHROPIC_BASE_URL),
the GrowthBook feature flag system cannot establish trust with Anthropic's
servers. This causes the auto mode circuit breaker to default to "disabled".

This patch changes the default value from "disabled" to "enabled", allowing
auto mode (claude --enable-auto-mode) to work through proxy servers.

Strategy (robust against minifier renames and reordering):
  1. Locate the parseAutoModeEnabledState function by searching for
     the unique string literal "opt-in" (only used in auto mode context)
     combined with the function's return-default pattern.
  2. Extract the default-variable name from `return VARNAME}`.
  3. Find the declaration  VARNAME="disabled";  and patch it to
     VARNAME="enabled";  with a 1-byte space pad to keep binary size.

  Multiple regex strategies are tried in order to handle variations
  across platforms (different comparison orders, different minifier output).
"""

import re
import sys
import os
import hashlib


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Regex strategies (tried in order, first match wins)
# ---------------------------------------------------------------------------

# All strategies extract group(1) = the default variable name.
#
# The three string literals "enabled", "disabled", "opt-in" are application
# constants that survive minification.  The variable/function names change
# per-platform build.

_Q = rb'''(?:"|')'''  # quote (single or double)

_STRATEGIES = [
    # ── Strategy 1: full function, standard order ────────────────
    # function Xx(Y){if(Y==="enabled"||Y==="disabled"||Y==="opt-in")return Y;return Zz}
    re.compile(
        rb'function \w+\(\w+\)\{'
        rb'if\(\w+===' + _Q + rb'enabled' + _Q
        + rb'\|\|\w+===' + _Q + rb'disabled' + _Q
        + rb'\|\|\w+===' + _Q + rb'opt-in' + _Q
        + rb'\)return \w+;return (\w+)\}'
    ),

    # ── Strategy 2: any order — just anchor on "opt-in" at end ───
    # ...||Y==="opt-in")return Y;return Zz}
    re.compile(
        _Q + rb'opt-in' + _Q + rb'\)return \w+;return (\w+)\}'
    ),

    # ── Strategy 3: "opt-in" in the middle ───────────────────────
    # ...||Y==="opt-in"||...  )return Y;return Zz}
    re.compile(
        _Q + rb'opt-in' + _Q + rb'\|\|[^}]{0,120}return \w+;return (\w+)\}'
    ),

    # ── Strategy 4: "opt-in" at the start ────────────────────────
    # if(Y==="opt-in"||...)return Y;return Zz}
    re.compile(
        rb'if\(\w+===' + _Q + rb'opt-in' + _Q + rb'\|\|[^}]{0,200}return \w+;return (\w+)\}'
    ),

    # ── Strategy 5: very loose — "opt-in" near a double-return ───
    # Looks for "opt-in" within 300 bytes before `return X;return Y}`
    re.compile(
        _Q + rb'opt-in' + _Q + rb'[^}]{0,300}return \w+;return (\w+)\}'
    ),
]


def _find_default_var(data: bytes) -> str | None:
    """Try all strategies to find the auto mode default variable name."""
    for i, pattern in enumerate(_STRATEGIES):
        m = pattern.search(data)
        if m:
            var = m.group(1).decode()
            # Sanity: variable name should be short (minified)
            if len(var) <= 10:
                return var
    return None


def _already_patched(data: bytes) -> bool:
    """Check if the binary is already patched (default = "enabled")."""
    var = _find_default_var(data)
    if var is None:
        return False
    return (var.encode() + b'="enabled"; var') in data


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

    # Find the default variable
    var_name = _find_default_var(data)

    if var_name is None:
        print("  [auto_mode_default] FAIL: could not locate auto mode default variable")
        print("  Searched for 'opt-in' string context with return-default pattern")

        # Debug: check if "opt-in" even exists in the binary
        opt_in_count = data.count(b'"opt-in"') + data.count(b"'opt-in'")
        disabled_count = data.count(b'"disabled"')
        print(f'  Debug: "opt-in" occurrences = {opt_in_count}')
        print(f'  Debug: "disabled" occurrences = {disabled_count}')

        if opt_in_count == 0:
            print("  The JS source may be stored as bytecode on this platform.")

        return False

    print(f"  [auto_mode_default] Found default variable: {var_name}")

    var_bytes = var_name.encode()
    target = var_bytes + b'="disabled";var'
    replacement = var_bytes + b'="enabled"; var'

    assert len(target) == len(replacement)

    count = data.count(target)
    if count == 0:
        # Try with 'let' or 'const' instead of 'var'
        for kw in [b'let', b'const']:
            alt_target = var_bytes + b'="disabled";' + kw
            alt_replacement = var_bytes + b'="enabled"; ' + kw
            if len(alt_target) == len(alt_replacement):
                alt_count = data.count(alt_target)
                if alt_count > 0:
                    target, replacement, count = alt_target, alt_replacement, alt_count
                    break

    if count == 0:
        print(f"  [auto_mode_default] FAIL: declaration '{target.decode()}' not found in binary")
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

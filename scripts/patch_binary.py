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
  2. Extract the default-variable name from the matched pattern.
  3. Find the declaration  VARNAME="disabled";  and patch it to
     VARNAME="enabled";  with a 1-byte space pad to keep binary size.

Known variable names per platform (v2.1.76):
  darwin-arm64:     cT$    (contains $)
  darwin-x64:       FA9
  linux-arm64:      N_R
  linux-arm64-musl: N_R
  linux-x64:        v$_    (contains $)
  linux-x64-musl:   v$_    (contains $)
  win32-x64:        hAM
  win32-arm64:      Vq5

IMPORTANT: JS identifiers can contain $ (e.g. cT$, v$_).
All regex patterns use [\\w$]+ instead of \\w+ to match these.
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
# per-platform build and may contain $ (valid JS identifier character).
#
# Verified function forms from binary analysis (v2.1.76):
#
#   parseAutoModeEnabledState (the one we patch):
#     function hD9(_){if(_==="enabled"||_==="disabled"||_==="opt-in")return _;return cT$}
#     function Iw8(H){if(H==="enabled"||H==="disabled"||H==="opt-in")return H;return v$_}
#
#   A different function (returns void 0, NOT a variable -- we skip this):
#     return _==="enabled"||_==="disabled"||_==="opt-in"?_:void 0

_Q = rb'''(?:"|')'''       # quote (single or double)
_ID = rb'[\w$]+'           # JS identifier (includes $ character)

_STRATEGIES = [
    # ── A. if/return/return patterns ─────────────────────────────
    # Verified: all 8 platforms use this form for parseAutoModeEnabledState

    # A1: full function, standard order (enabled||disabled||opt-in)
    re.compile(
        rb'function ' + _ID + rb'\(' + _ID + rb'\)\{'
        rb'if\(' + _ID + rb'===' + _Q + rb'enabled' + _Q
        + rb'\|\|' + _ID + rb'===' + _Q + rb'disabled' + _Q
        + rb'\|\|' + _ID + rb'===' + _Q + rb'opt-in' + _Q
        + rb'\)return ' + _ID + rb';return (' + _ID + rb')\}'
    ),

    # A2: "opt-in" at end of comparisons (any order before it)
    re.compile(
        _Q + rb'opt-in' + _Q + rb'\)return ' + _ID + rb';return (' + _ID + rb')\}'
    ),

    # A3: "opt-in" in middle
    re.compile(
        _Q + rb'opt-in' + _Q + rb'\|\|[^}]{0,120}return ' + _ID + rb';return (' + _ID + rb')\}'
    ),

    # A4: "opt-in" at start
    re.compile(
        rb'if\(' + _ID + rb'===' + _Q + rb'opt-in' + _Q + rb'\|\|[^}]{0,200}return ' + _ID + rb';return (' + _ID + rb')\}'
    ),

    # A5: loose - "opt-in" within 300 bytes of double-return
    re.compile(
        _Q + rb'opt-in' + _Q + rb'[^}]{0,300}return ' + _ID + rb';return (' + _ID + rb')\}'
    ),

    # ── B. Ternary patterns (fallback) ───────────────────────────
    # Not seen in v2.1.76 for parseAutoModeEnabledState, but kept
    # as defense against future minifier changes.
    # NOTE: skips "void 0" (not a variable) since _ID requires [\w$]+

    # B1: "opt-in" at end, ternary
    re.compile(
        _Q + rb'opt-in' + _Q + rb'\?' + _ID + rb':(' + _ID + rb')[};,)]'
    ),

    # B2: "opt-in" in middle, ternary
    re.compile(
        _Q + rb'opt-in' + _Q + rb'\|\|[^}]{0,120}\?' + _ID + rb':(' + _ID + rb')[};,)]'
    ),

    # B3: reversed comparison + if/return/return
    re.compile(
        _Q + rb'opt-in' + _Q + rb'===' + _ID + rb'\)return ' + _ID + rb';return (' + _ID + rb')\}'
    ),

    # B4: reversed comparison + ternary
    re.compile(
        _Q + rb'opt-in' + _Q + rb'===' + _ID + rb'\?' + _ID + rb':(' + _ID + rb')[};,)]'
    ),
]


def _find_default_var(data: bytes) -> str | None:
    """Try all strategies to find the auto mode default variable name."""
    for pattern in _STRATEGIES:
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
    # The patched form is VARNAME="enabled" followed by ; or , (space-padded)
    return (var.encode() + b'="enabled" ') in data


def _dump_opt_in_context(data: bytes):
    """Print context around each 'opt-in' occurrence for debugging."""
    for quote in [b'"', b"'"]:
        needle = quote + b'opt-in' + quote
        start = 0
        idx = 0
        while True:
            pos = data.find(needle, start)
            if pos == -1:
                break
            idx += 1
            ctx_start = max(0, pos - 200)
            ctx_end = min(len(data), pos + len(needle) + 200)
            ctx = data[ctx_start:ctx_end]
            printable = bytes(b if 32 <= b < 127 else 46 for b in ctx)
            print(f'  Context around {quote.decode()}opt-in{quote.decode()} #{idx} (offset {pos}):')
            print(f'    ...{printable.decode()}...')
            start = pos + 1


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

        opt_in_count = data.count(b'"opt-in"') + data.count(b"'opt-in'")
        disabled_count = data.count(b'"disabled"')
        print(f'  Debug: "opt-in" occurrences = {opt_in_count}')
        print(f'  Debug: "disabled" occurrences = {disabled_count}')

        if opt_in_count == 0:
            print("  The JS source may be stored as bytecode on this platform.")
        else:
            print("  Dumping context around each 'opt-in' occurrence:")
            _dump_opt_in_context(data)

        return False

    print(f"  [auto_mode_default] Found default variable: {var_name}")

    var_bytes = var_name.encode()

    # Try declaration with var, let, const keywords (v2.1.76 pattern)
    target = var_bytes + b'="disabled"'
    replacement = var_bytes + b'="enabled" '
    count = 0

    for kw in [b'var', b'let', b'const']:
        t = var_bytes + b'="disabled";' + kw
        r = var_bytes + b'="enabled"; ' + kw
        assert len(t) == len(r), f"Length mismatch for {kw}: {len(t)} vs {len(r)}"
        c = data.count(t)
        if c > 0:
            target, replacement, count = t, r, c
            break

    if count == 0:
        # Fallback: replace just VARNAME="disabled" with VARNAME="enabled"
        # (space-padded to same byte length). Works regardless of what follows
        # the value -- semicolon (;var), comma (,NEXT;var), etc.
        #
        # v2.1.76: hAM="disabled";var   -> hAM="enabled"; var  (semicolon)
        # v2.1.81: VAM="disabled",Uj_;  -> VAM="enabled" ,Uj_; (comma)
        assert len(target) == len(replacement)
        count = data.count(target)

        if count == 0:
            print(f"  [auto_mode_default] FAIL: '{target.decode()}' not found anywhere in binary")
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

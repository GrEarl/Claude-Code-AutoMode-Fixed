#!/usr/bin/env python3
"""
Patch Claude Code binary to enable Auto Mode by default.

When using Claude Code through a proxy (custom ANTHROPIC_BASE_URL),
the GrowthBook feature flag system cannot establish trust with Anthropic's
servers. This causes the auto mode circuit breaker to default to "disabled".

This patch changes the default value from "disabled" to "enabled", allowing
auto mode to work through proxy servers.

Technical details:
  The minified JS inside the compiled binary contains:
      var hAM="disabled";
  This is the default value for tengu_auto_mode_config.enabled, used when
  GrowthBook remote evaluation fails or is skipped (no trust established).

  Patch: hAM="disabled";var  ->  hAM="enabled"; var
  (same 18 bytes — space inserted before 'var' to preserve alignment)

  Effect: q3_(undefined) returns "enabled" instead of "disabled",
  so the circuit breaker does NOT fire for proxy/non-Anthropic users.
  Real Anthropic users are unaffected (their value comes from GrowthBook).
"""

import sys
import os
import hashlib

PATCHES = [
    {
        "name": "auto_mode_default",
        "description": "Change auto mode default from 'disabled' to 'enabled'",
        "target": b'hAM="disabled";var',
        "replacement": b'hAM="enabled"; var',
        "min_occurrences": 1,
    },
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

    patched = data
    total_patched = 0
    all_ok = True

    for patch in PATCHES:
        target = patch["target"]
        replacement = patch["replacement"]

        assert len(target) == len(replacement), (
            f"Patch '{patch['name']}': length mismatch "
            f"({len(target)} vs {len(replacement)})"
        )

        count = patched.count(target)
        already = patched.count(replacement)

        if count == 0 and already > 0:
            print(f"  [{patch['name']}] Already patched ({already} occurrences)")
            continue

        if count == 0:
            print(f"  [{patch['name']}] FAIL: pattern not found")
            print(f"    Target: {target!r}")
            all_ok = False
            continue

        if count < patch["min_occurrences"]:
            print(
                f"  [{patch['name']}] WARNING: expected >= {patch['min_occurrences']}, "
                f"found {count}"
            )

        patched = patched.replace(target, replacement)
        total_patched += count
        print(f"  [{patch['name']}] OK - {count} occurrences patched")

    if not all_ok:
        print("\nERROR: Some patches failed. Binary format may have changed.")
        return False

    if patched == data:
        print("\nNo changes needed (already patched)")
        if output_path != input_path:
            with open(output_path, "wb") as f:
                f.write(data)
        return True

    assert len(patched) == len(data), f"Size changed: {len(data)} -> {len(patched)}"

    with open(output_path, "wb") as f:
        f.write(patched)

    print(f"\nOutput: {output_path}")
    print(f"SHA256: {sha256(patched)}")
    print(f"Total:  {total_patched} patches applied")
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

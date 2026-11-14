"""Generate safe strength compensation entries from two built-in catalogs."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import subprocess
import sys

SEMVER_PATTERN = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def load_catalog(path=None, git_ref=None):
    if git_ref:
        for filename in ("effects.default.json", "effects.json"):
            result = subprocess.run(
                ["git", "show", f"{git_ref}:{filename}"], capture_output=True,
                text=True, encoding="utf-8", check=False)
            if result.returncode == 0:
                return json.loads(result.stdout)
        raise RuntimeError(f"No effect catalog found at Git ref {git_ref}.")
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def volume_shape(value, volumes):
    """Return a comparable structure while collecting every recipe volume."""
    if isinstance(value, dict):
        shaped = {}
        for key, item in value.items():
            if key == "volume" and isinstance(item, (int, float)):
                volumes.append(float(item))
                shaped[key] = "<volume>"
            else:
                shaped[key] = volume_shape(item, volumes)
        return shaped
    if isinstance(value, list):
        return [volume_shape(item, volumes) for item in value]
    return value


def compensation_factor(previous, current):
    old_volumes, new_volumes = [], []
    if volume_shape(previous, old_volumes) != volume_shape(current, new_volumes):
        return None
    if not old_volumes or len(old_volumes) != len(new_volumes):
        return None
    ratios = []
    for old, new in zip(old_volumes, new_volumes):
        if old == 0 or new == 0:
            return None
        ratios.append(new / old)
    if not all(math.isclose(ratio, ratios[0], rel_tol=1e-6, abs_tol=1e-9)
               for ratio in ratios[1:]):
        return None
    if math.isclose(ratios[0], 1.0, rel_tol=1e-6, abs_tol=1e-9):
        return None
    return round(1.0 / ratios[0], 8)


def latest_git_tag(parser: argparse.ArgumentParser) -> str:
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"], capture_output=True,
        text=True, encoding="utf-8", check=False)
    tag = result.stdout.strip()
    if result.returncode != 0 or not tag:
        parser.error("No previous release tag was found. Supply --previous-git-ref explicitly.")
    return tag


def migration_matches(item, version, effect_id, factor):
    try:
        existing_factor = float(item.get("strength_factor"))
    except (TypeError, ValueError):
        return False
    return (
        str(item.get("id", "")).startswith(f"{version}-")
        and item.get("effect") == effect_id
        and math.isclose(existing_factor, factor, rel_tol=1e-6, abs_tol=1e-9)
    )


def resolve_target_version(supplied_version: str | None, default_version: str | None, parser: argparse.ArgumentParser) -> str:
    if supplied_version:
        cleaned = supplied_version.strip().lstrip("v")
        if not SEMVER_PATTERN.match(cleaned):
            parser.error(f"Invalid --to-version '{supplied_version}'. Expected format like 1.0.1 or v1.0.1-beta.1.")
        return cleaned

    if not sys.stdin.isatty():
        parser.error("--to-version is required when running in non-interactive mode.")

    if default_version:
        print(f"Current catalog_version in manifest: v{default_version}")

    while True:
        prompt = (
            f"Enter target version (press Enter to use {default_version}): "
            if default_version
            else "Enter target version (e.g., 1.0.1): "
        )
        entered = input(prompt).strip()

        if not entered:
            if default_version:
                return default_version
            print("Version cannot be empty.", file=sys.stderr)
            continue

        if not SEMVER_PATTERN.match(entered):
            print(
                f"Invalid version '{entered}'. Expected format like 1.0.1 or v1.0.1-beta.1.",
                file=sys.stderr,
            )
            continue

        return entered.lstrip("v")


def main():
    parser = argparse.ArgumentParser()
    previous = parser.add_mutually_exclusive_group()
    previous.add_argument("--previous")
    previous.add_argument("--previous-git-ref")
    parser.add_argument("--current", default="effects.default.json")
    parser.add_argument("--to-version", required=False,
                        help="Target catalog version (prompts interactively if omitted).")
    parser.add_argument("--output", default="effect_migrations.json")
    parser.add_argument("--check", action="store_true",
                        help="Fail if automatic migrations or the catalog version are missing.")
    args = parser.parse_args()

    if not args.previous and not args.previous_git_ref:
        args.previous_git_ref = latest_git_tag(parser)
        print(f"Using previous release tag: {args.previous_git_ref}")

    output_path = pathlib.Path(args.output)
    manifest = (json.loads(output_path.read_text(encoding="utf-8"))
                if output_path.exists() else {"schema_version": 1, "migrations": []})

    raw_current_version = manifest.get("catalog_version")
    current_manifest_version = (
        str(raw_current_version).strip().lstrip("v")
        if raw_current_version
        else None
    )
    args.to_version = resolve_target_version(args.to_version, current_manifest_version, parser)

    old = load_catalog(args.previous, args.previous_git_ref).get("effects", {})
    new = load_catalog(args.current).get("effects", {})
    existing_migrations = manifest.get("migrations", [])
    existing_ids = {item["id"] for item in existing_migrations}
    added = []
    for effect_id in sorted(old.keys() & new.keys()):
        factor = compensation_factor(old[effect_id], new[effect_id])
        if factor is None:
            continue
        migration_id = f"{args.to_version}-{effect_id}-strength"
        already_prepared = migration_id in existing_ids or any(
            migration_matches(item, args.to_version, effect_id, factor)
            for item in existing_migrations)

        if not already_prepared:
            added.append({
                "id": migration_id,
                "effect": effect_id,
                "strength_factor": factor,
                "reason": "Automatic inverse compensation for a uniform built-in volume change."
            })
    if args.check:
        version_matches = str(manifest.get("catalog_version")) == args.to_version
        if added or not version_matches:
            if added:
                print(json.dumps(added, indent=2))
            if not version_matches:
                print(f"Catalog version is {manifest.get('catalog_version')!r}; "
                      f"expected {args.to_version!r}.")
            raise SystemExit(
                "Effect migrations are not prepared. Run this command without --check, "
                "review the result, and commit effect_migrations.json.")
        print("Effect migration manifest is prepared for this release.")
        return

    manifest["schema_version"] = 1
    manifest["catalog_version"] = args.to_version
    manifest.setdefault("migrations", []).extend(added)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Added {len(added)} automatic strength migration(s) for version {args.to_version}.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Migration v2: Split monolithic SelfCore into AnjoIdentity + RelationalState.

Run once before deploying the v2 architecture. Idempotent — safe to run multiple times.

What it does:
  1. Creates data/anjo_identity.json from the default OCEAN personality baseline
  2. For each user with data/users/{user_id}/self_core/current.json:
     - Creates data/users/{user_id}/self_core/relational_state.json
     - Computes personality_overlay = current_personality - global_baseline
     - Leaves current.json intact (dual-write mode for backwards compat)

Usage:
    python scripts/migration_v2.py
    python scripts/migration_v2.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Bootstrap imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from anjo.core.crypto import read_encrypted, write_encrypted


_DATA_ROOT = Path(__file__).parent.parent / "data"

# Default OCEAN baseline — must match Personality defaults in self_core.py
_BASELINE = {"O": 0.80, "C": 0.72, "E": 0.45, "A": 0.72, "N": 0.15}
_OVERLAY_CLAMP = 0.25


def migrate(dry_run: bool = False) -> None:
    # Step 1: Create global identity
    identity_path = _DATA_ROOT / "anjo_identity.json"
    if not identity_path.exists():
        identity = {
            "version": 1,
            "last_updated": "2026-04-11T00:00:00+00:00",
            "personality": _BASELINE.copy(),
            "goals": {
                "rapport": 0.8,
                "intellectual": 0.8,
                "autonomy": 0.7,
                "respect": 0.85,
                "honesty": 0.90,
            },
        }
        if dry_run:
            print(f"[DRY] Would create {identity_path}")
        else:
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity_path.write_bytes(write_encrypted(json.dumps(identity, indent=2)))
            print(f"Created {identity_path}")
    else:
        print(f"Identity already exists: {identity_path}")

    # Step 2: Migrate each user
    users_dir = _DATA_ROOT / "users"
    if not users_dir.exists():
        print("No users directory found — nothing to migrate.")
        return

    migrated = 0
    skipped = 0
    for user_dir in sorted(users_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        core_dir = user_dir / "self_core"
        legacy_path = core_dir / "current.json"
        new_path = core_dir / "relational_state.json"

        if new_path.exists():
            skipped += 1
            continue

        if not legacy_path.exists():
            skipped += 1
            continue

        try:
            data = json.loads(read_encrypted(legacy_path))
        except Exception as e:
            print(f"  ERROR reading {legacy_path}: {e}")
            continue

        # Compute overlay = current personality - baseline
        current_p = data.get("personality", _BASELINE.copy())
        overlay = {}
        for trait in ("O", "C", "E", "A", "N"):
            delta = current_p.get(trait, _BASELINE[trait]) - _BASELINE[trait]
            overlay[trait] = max(-_OVERLAY_CLAMP, min(_OVERLAY_CLAMP, delta))

        # Build relational state
        relational = {
            "version": data.get("version", 1),
            "last_updated": data.get("last_updated", ""),
            "personality_overlay": overlay,
            "mood": data.get("mood", {}),
            "relationship": data.get("relationship", {}),
            "goals_overlay": data.get("goals", {}),
            "notes": data.get("notes", []),
            "emotional_residue": data.get("emotional_residue", []),
            "attachment": {
                k: v for k, v in data.get("attachment", {}).items()
                if k != "weight_history"  # new field, not in legacy
            },
            "relational_desires": data.get("relational_desires", []),
            "desire_survived": data.get("desire_survived", {}),
            "baseline_valence": data.get("baseline_valence", 0.0),
            "inter_session_drift": data.get("inter_session_drift", 0.0),
            "last_drift_run": data.get("last_drift_run"),
            "last_autodream": data.get("last_autodream"),
            "last_outreach_sent": data.get("last_outreach_sent"),
            "memory_relevance": data.get("memory_relevance", 0.0),
            "relationship_ceiling": data.get("relationship_ceiling"),
            "preoccupation": data.get("preoccupation", ""),
            "ceiling_last_checked": data.get("ceiling_last_checked", 0),
            "user_id": user_id,
        }

        if dry_run:
            print(f"  [DRY] Would create {new_path} (overlay: {overlay})")
        else:
            new_path.write_bytes(write_encrypted(json.dumps(relational, indent=2)))
            print(f"  Migrated {user_id} (overlay: {overlay})")
        migrated += 1

    print(f"\nDone: {migrated} migrated, {skipped} skipped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SelfCore to v2 split architecture")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without writing")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)

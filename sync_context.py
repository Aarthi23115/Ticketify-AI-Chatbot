"""
Refresh the bundled Ticketify snapshot in context/ from a Ticketify checkout.

Usage:
    python sync_context.py path/to/Ticketify-

The path is the root of the Ticketify repository (the folder that contains
README.md and the Ticketify/ Django project). Run this whenever Ticketify's
code changes, then commit the updated context/ folder.
"""
import shutil
import sys
from pathlib import Path

from app import CONTEXT_FILES

DEST = Path(__file__).resolve().parent / "context"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    repo_root = Path(sys.argv[1]).expanduser().resolve()
    project = repo_root / "Ticketify"
    if not (project / "events" / "models.py").exists():
        sys.exit(f"Could not find Ticketify/events/models.py under {repo_root}")

    for key, relative in CONTEXT_FILES.items():
        # Prefer the repository README (the up-to-date one) for the readme entry.
        source = repo_root / "README.md" if key == "readme" else project / relative
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        print(f"copied {source.relative_to(repo_root)} -> context/{relative}")


if __name__ == "__main__":
    main()

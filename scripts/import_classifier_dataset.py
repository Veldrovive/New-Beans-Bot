"""
Import the classifier's master photos (and the old corrections folder) into the bot's database.

    uv run scripts/import_classifier_dataset.py --source models/classifier/datasets --dry-run
    uv run scripts/import_classifier_dataset.py --source models/classifier/datasets [--corrections data/classifier_corrections]

--source holds <dataset>/<label>/<image>, like models/classifier/datasets on Aidan's Mac. Names starting
with "_" or "." are skipped (Finder litters folders with .DS_Store).

- Every photo is shrunk to a JPEG of at most 2048 pixels and stored under --datasets-dir, indexed in the
  classifier_image table. The website manages them from then on.
- Seed photos get the same train/val split as scripts/train_classifier.py's split_dataset (per label, by
  file hash, the first --val-fraction are val), so the first retrain's scores compare with today's model.
- Old corrections (<dataset>/<label>/<message id>_<n>.<ext>) go in as training photos.
- Photos already in the dataset are skipped, so it's safe to run twice. It copies and never deletes the
  source. It's safe to run while the bot is up. --dry-run reads the photos and prints the plan, writing nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import OperationalError

from beans_bot.db.engine import Database
from beans_bot.db.models import ClassifierImageSource, ClassifierSplit
from beans_bot.services import photos
from beans_bot.services.classifier import ClassifierService, InvalidLabel, NotAnImage, normalize_label, prepare_photo


@dataclass
class Candidate:
    dataset: str
    label: str
    path: Path
    content_hash: str
    split: str
    source: str
    message_id: int | None = None


def visible_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name[0] not in "_.")


def visible_dirs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and p.name[0] not in "_.")


def file_hash(path: Path) -> str:
    return photos.content_hash(path.read_bytes())


def seed_split(paths_by_hash: dict[str, Path], val_fraction: float) -> dict[str, str]:
    """The same rule as split_dataset in train_classifier.py: unique hashes in sorted order, the first n_test are val."""
    hashes = sorted(paths_by_hash)
    n = len(hashes)
    n_val = min(max(math.ceil(n * val_fraction), 1), n - 1) if n > 1 else 0
    return {h: ClassifierSplit.VAL if i < n_val else ClassifierSplit.TRAIN for i, h in enumerate(hashes)}


def seed_candidates(source: Path, val_fraction: float) -> list[Candidate]:
    found = []
    for dataset_dir in visible_dirs(source):
        for label_dir in visible_dirs(dataset_dir):
            by_hash: dict[str, Path] = {}
            for path in visible_files(label_dir):
                by_hash.setdefault(file_hash(path), path)
            splits = seed_split(by_hash, val_fraction)
            label = normalize_label(label_dir.name)
            for content_hash, path in sorted(by_hash.items()):
                found.append(Candidate(dataset_dir.name, label, path, content_hash, splits[content_hash], ClassifierImageSource.SEED))
    return found


def correction_candidates(corrections: Path) -> list[Candidate]:
    found = []
    for dataset_dir in visible_dirs(corrections):
        for label_dir in visible_dirs(dataset_dir):
            for path in visible_files(label_dir):
                prefix = path.name.split("_", 1)[0]
                message_id = int(prefix) if prefix.isdigit() else None
                found.append(
                    Candidate(
                        dataset_dir.name, normalize_label(label_dir.name), path, file_hash(path), ClassifierSplit.TRAIN, ClassifierImageSource.CORRECTION, message_id
                    )
                )
    return found


def megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


async def run(args: argparse.Namespace) -> int:
    if not args.source.is_dir():
        print(f"{args.source} isn't a folder.", file=sys.stderr)
        return 1
    try:
        candidates = seed_candidates(args.source, args.val_fraction)
        if args.corrections is not None and args.corrections.is_dir():
            candidates += correction_candidates(args.corrections)
    except InvalidLabel as e:
        print(f"A label folder isn't a valid label name: {e}", file=sys.stderr)
        return 1

    db = None
    if not args.dry_run or args.db.exists():
        # A dry run never creates or migrates a database, it only reads what's already imported.
        db = Database(args.db, migrate=not args.dry_run)
    service = ClassifierService(db, args.datasets_dir, args.val_fraction) if db is not None else None

    async def already_there(dataset: str, content_hash: str) -> bool:
        if service is None:
            return False
        try:
            return await service.has_hash(dataset, content_hash)
        except OperationalError:  # A dry run against a database from before the photo tables
            return False

    seen: set[tuple[str, str]] = set()
    added: Counter[tuple[str, str, str, str]] = Counter()  # (dataset, label, split, source)
    skipped: Counter[tuple[str, str]] = Counter()
    size_before = size_after = 0
    for candidate in candidates:
        key = (candidate.dataset, candidate.content_hash)
        if key in seen or await already_there(*key):
            skipped[(candidate.dataset, candidate.label)] += 1
            continue
        seen.add(key)
        size_before += candidate.path.stat().st_size
        if args.dry_run or service is None:
            added[(candidate.dataset, candidate.label, candidate.split, candidate.source)] += 1
            continue
        try:
            photo = await asyncio.to_thread(prepare_photo, candidate.path.read_bytes(), candidate.path.name)
        except NotAnImage:
            print(f"  Not a photo, skipped: {candidate.path}")
            skipped[(candidate.dataset, candidate.label)] += 1
            continue
        _, new = await service.add_prepared(
            candidate.dataset, photo, label=candidate.label, source=candidate.source, split=candidate.split, message_id=candidate.message_id
        )
        if new:
            added[(candidate.dataset, candidate.label, candidate.split, candidate.source)] += 1
            size_after += len(photo.jpeg)
        else:
            skipped[(candidate.dataset, candidate.label)] += 1

    print("Dry run: nothing was written." if args.dry_run else f"Imported into {args.db}, photos in {args.datasets_dir}.")
    labels = sorted({(d, l) for d, l, _, _ in added} | set(skipped))
    for dataset, label in labels:
        counts = {
            f"{split} {source}": added[(dataset, label, split, source)]
            for split in ("train", "val")
            for source in ("seed", "correction")
            if added[(dataset, label, split, source)]
        }
        parts = [f"{n} {what}" for what, n in counts.items()]
        if skipped[(dataset, label)]:
            parts.append(f"{skipped[(dataset, label)]} already there")
        print(f"  {dataset}/{label}: {', '.join(parts) or 'nothing'}")
    total = sum(added.values())
    print(f"{total} photos, {sum(skipped.values())} skipped. Originals: {megabytes(size_before)}.")
    if not args.dry_run:
        print(f"Stored after shrinking: {megabytes(size_after)}.")
    if db is not None:
        await db.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="Folder with <dataset>/<label>/<image>")
    parser.add_argument("--corrections", type=Path, help="The old corrections folder, <dataset>/<label>/<message id>_<n>.<ext>")
    parser.add_argument("--db", type=Path, default=Path("data/beans.db"))
    parser.add_argument("--datasets-dir", type=Path, default=Path("data/classifier_datasets"))
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be imported and write nothing")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()

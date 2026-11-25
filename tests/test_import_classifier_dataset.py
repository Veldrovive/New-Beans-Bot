"""scripts/import_classifier_dataset.py against a small folder of photos."""

import argparse
import importlib.util
import io
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlmodel import col, select

from beans_bot.db.engine import Database
from beans_bot.db.models import ClassifierImage

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


importer = load_script("import_classifier_dataset")


def png(seed: int, size=(32, 24)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (seed * 7 % 256, seed * 13 % 256, seed * 29 % 256)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "datasets"
    # Fixed seeds, all different below 256, so no two photos can come out the same colour.
    for first_seed, (label, count) in zip((0, 10, 20), (("andy", 6), ("bean", 5), ("solo", 1))):
        folder = root / "cats" / label
        folder.mkdir(parents=True)
        for i in range(count):
            (folder / f"IMG_{i}.png").write_bytes(png(first_seed + i))
        (folder / ".DS_Store").write_bytes(b"finder litter")
        (folder / "_notes.txt").write_text("skip me")
    (root / "cats" / "andy" / "copy.png").write_bytes((root / "cats" / "andy" / "IMG_0.png").read_bytes())  # A duplicate
    corrections = tmp_path / "corrections" / "cats" / "bean"
    corrections.mkdir(parents=True)
    (corrections / "1234_1.jpg").write_bytes(png(4242))
    (corrections / "1234_2.jpg").write_bytes((root / "cats" / "bean" / "IMG_0.png").read_bytes())  # Already a seed photo
    return root, tmp_path / "corrections"


def args(tmp_path, source, corrections=None, dry_run=False):
    return argparse.Namespace(
        source=source, corrections=corrections, db=tmp_path / "beans.db", datasets_dir=tmp_path / "stored", val_fraction=0.2, dry_run=dry_run
    )


async def rows(path) -> list[ClassifierImage]:
    db = Database(path)
    try:
        async with db.transaction() as session:
            return list((await session.exec(select(ClassifierImage).order_by(col(ClassifierImage.id)))).all())
    finally:
        await db.close()


async def test_dry_run_writes_nothing(tmp_path, source, capsys):
    assert await importer.run(args(tmp_path, source[0], source[1], dry_run=True)) == 0
    assert not (tmp_path / "beans.db").exists() and not (tmp_path / "stored").exists()
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "cats/andy: 4 train seed, 2 val seed\n" in out  # Six unique photos; the copy counts once
    assert "cats/bean: 4 train seed, 1 train correction, 1 val seed, 1 already there" in out


async def test_import_is_idempotent_and_matches_the_old_split(tmp_path, source, capsys):
    root, corrections = source
    assert await importer.run(args(tmp_path, root, corrections)) == 0
    imported = await rows(tmp_path / "beans.db")
    by_label: dict[str, list[ClassifierImage]] = {}
    for row in imported:
        by_label.setdefault(row.label, []).append(row)
    assert {label: len(images) for label, images in by_label.items()} == {"andy": 6, "bean": 6, "solo": 1}
    assert {r.source for r in by_label["solo"]} == {"seed"} and by_label["solo"][0].split == "train"
    (correction,) = [r for r in imported if r.source == "correction"]
    assert (correction.label, correction.split, correction.message_id) == ("bean", "train", 1234)
    for row in imported:
        stored = tmp_path / "stored" / "cats" / "images" / row.filename
        assert stored.read_bytes()[:3] == b"\xff\xd8\xff"

    # The split per label: unique hashes of the original files in order, the first 20% (at least one) are val.
    for label, n_val in (("andy", 2), ("bean", 1)):  # ceil(6 * 0.2) and ceil(5 * 0.2)
        seeds = sorted((r for r in by_label[label] if r.source == "seed"), key=lambda r: r.content_hash)
        assert [r.split for r in seeds] == ["val"] * n_val + ["train"] * (len(seeds) - n_val)

    out = capsys.readouterr().out
    assert "Originals:" in out and "Stored after shrinking:" in out
    assert await importer.run(args(tmp_path, root, corrections)) == 0
    assert len(await rows(tmp_path / "beans.db")) == len(imported)
    assert "0 photos" in capsys.readouterr().out
    assert (root / "cats" / "andy" / "IMG_0.png").exists()  # The source is never touched


def test_split_matches_train_classifier(source):
    pytest.importorskip("torch")
    pytest.importorskip("cv2")
    pytest.importorskip("matplotlib")
    train_classifier = load_script("train_classifier")
    root, _ = source
    _, train, test = train_classifier.split_dataset(root / "cats", 0.2)
    candidates = importer.seed_candidates(root, 0.2)
    ours = {(c.label, str(c.path)) for c in candidates if c.split == "val"}
    labels = sorted(p.name for p in (root / "cats").iterdir() if p.is_dir())
    theirs = {(labels[index], str(path)) for path, index in test}
    assert ours == theirs
    assert {(c.label, str(c.path)) for c in candidates if c.split == "train"} == {(labels[index], str(path)) for path, index in train}

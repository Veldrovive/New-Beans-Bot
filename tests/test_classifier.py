import datetime
import io

import pytest
from PIL import Image
from sqlmodel import col, select

from beans_bot.db.models import ClassifierCorrection, ClassifierDropSession, ClassifierImage, ClassifierProcessedMessage
from beans_bot.db.types import utcnow
from beans_bot.services import photos
from beans_bot.services.classifier import AlreadyLabeled, DropLimitReached, DropOutcome, InvalidLabel, NotAnImage, NotClassified, UnknownLabel

from conftest import GUILD

PHOTO = 500
LABELS = ["andy", "bean", "merlin"]


def png(color, size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


IMAGES = [("IMG_1.JPG", png((255, 0, 0))), ("cat.png", png((0, 255, 0)))]


@pytest.fixture
async def guessed(db):
    """The bot has guessed that PHOTO is andy."""
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=PHOTO, classifier_name="cats", channel_id=5, predicted_label="andy"))


async def guess(db, message_id, label="andy"):
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=message_id, classifier_name="cats", channel_id=5, predicted_label=label))


async def correct(classifier, label, *, by=1, message_id=PHOTO, images=IMAGES, allow_new=False):
    return await classifier.correct(
        guild_id=GUILD, message_id=message_id, classifier_name="cats", dataset="cats", model_labels=LABELS, label=label, corrected_by=by,
        images=images, allow_new=allow_new,
    )  # fmt: skip


async def confirm(classifier, *, by=1, message_id=PHOTO):
    return await classifier.confirm(GUILD, message_id, "cats", "cats", by, IMAGES)


async def corrections(db):
    async with db.transaction() as session:
        return list((await session.exec(select(ClassifierCorrection).order_by(col(ClassifierCorrection.id)))).all())


async def rows(db) -> list[ClassifierImage]:
    async with db.transaction() as session:
        return list((await session.exec(select(ClassifierImage).order_by(col(ClassifierImage.id)))).all())


async def saved(db) -> list[tuple[int | None, str, bool]]:
    """(message, label, removed) for every photo in the cats dataset."""
    return [(r.message_id, r.label, r.removed_at is not None) for r in await rows(db) if r.dataset == "cats"]


def files(classifier) -> list[str]:
    root = classifier.datasets_dir
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


# --- Corrections from Discord ----------------------------------------------------------------


async def test_correction_saves_photos_under_the_right_label(db, classifier, guessed):
    result = await correct(classifier, "bean", by=7)

    assert (result.previous_label, result.label) == ("andy", "bean")
    assert await saved(db) == [(PHOTO, "bean", False), (PHOTO, "bean", False)]
    first, second = await rows(db)
    assert (first.source, first.guild_id, first.added_by, first.original_name) == ("correction", GUILD, 7, "IMG_1.JPG")
    assert first.content_hash == photos.content_hash(IMAGES[0][1]) and first.filename == f"{first.content_hash}.jpg"
    assert files(classifier) == sorted(f"cats/images/{r.filename}" for r in (first, second))
    assert all(p.read_bytes()[:3] == b"\xff\xd8\xff" for p in result.paths)  # Stored as JPEG
    (row,) = await corrections(db)
    assert (row.guild_id, row.message_id, row.classifier_name) == (GUILD, PHOTO, "cats")
    assert (row.previous_label, row.label, row.corrected_by) == ("andy", "bean", 7)


async def test_confirming_saves_photos_under_the_guess(db, classifier, guessed):
    result = await confirm(classifier, by=7)

    assert result.confirmed and not result.new_label
    assert await saved(db) == [(PHOTO, "andy", False), (PHOTO, "andy", False)]
    (row,) = await corrections(db)
    assert (row.previous_label, row.label, row.corrected_by) == ("andy", "andy", 7)


async def test_confirming_twice_changes_nothing(db, classifier, guessed):
    await confirm(classifier, by=1)
    with pytest.raises(AlreadyLabeled):
        await confirm(classifier, by=2)
    with pytest.raises(AlreadyLabeled):
        await correct(classifier, "andy", by=2)
    assert len(await corrections(db)) == 1


async def test_picking_the_guess_counts_as_confirming(db, classifier, guessed):
    assert (await correct(classifier, "andy")).confirmed
    assert await saved(db) == [(PHOTO, "andy", False), (PHOTO, "andy", False)]


async def test_confirmed_guess_can_still_be_corrected(db, classifier, guessed):
    await confirm(classifier)
    result = await correct(classifier, "bean")
    assert not result.confirmed and result.previous_label == "andy"
    assert await saved(db) == [(PHOTO, "bean", False), (PHOTO, "bean", False)]


async def test_confirming_after_a_correction_is_already_settled(classifier, guessed):
    await correct(classifier, "bean")
    with pytest.raises(AlreadyLabeled) as error:
        await confirm(classifier)
    assert error.value.label == "bean"


async def test_confirming_needs_a_guess(db, classifier):
    with pytest.raises(NotClassified):
        await confirm(classifier)
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=PHOTO, classifier_name="cats", channel_id=5))  # A row from before
    with pytest.raises(NotClassified):
        await confirm(classifier)
    assert await corrections(db) == []


async def test_guess_is_found_from_the_photo_or_the_reply(db, classifier, guessed):
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=PHOTO, classifier_name="emotions", channel_id=5, reply_message_id=701))
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=501, classifier_name="cats", channel_id=5, reply_message_id=702))

    assert [g.classifier_name for g in await classifier.guesses_for(GUILD, [701])] == ["emotions"]
    assert [(g.message_id, g.classifier_name) for g in await classifier.guesses_for(GUILD, [702])] == [(501, "cats")]
    assert sorted(g.classifier_name for g in await classifier.guesses_for(GUILD, [PHOTO])) == ["cats", "emotions"]
    assert [g.classifier_name for g in await classifier.guesses_for(GUILD, [701, PHOTO])] == ["emotions", "cats"]
    assert await classifier.guesses_for(GUILD, [999]) == []
    assert await classifier.guesses_for(GUILD + 1, [PHOTO]) == []


async def test_new_cat_starts_a_label_everyone_can_pick(db, classifier, guessed):
    assert await classifier.known_labels("cats", LABELS) == LABELS
    result = await correct(classifier, "  Mr Whiskers ", allow_new=True)

    assert (result.label, result.new_label, result.previous_label) == ("mr_whiskers", True, "andy")
    assert await saved(db) == [(PHOTO, "mr_whiskers", False), (PHOTO, "mr_whiskers", False)]
    assert await classifier.known_labels("cats", LABELS) == [*LABELS, "mr_whiskers"]

    # The next photo of the same cat picks it from the list, without allow_new.
    await guess(db, 501, "bean")
    again = await correct(classifier, "mr_whiskers", message_id=501, images=[("x.png", png((1, 2, 3)))])
    assert not again.new_label

    # Once every photo of it moves elsewhere the label has no photos, so it's no longer offered.
    await correct(classifier, "bean")
    await correct(classifier, "merlin", message_id=501, images=[("x.png", png((1, 2, 3)))])
    assert await classifier.known_labels("cats", LABELS) == LABELS


async def test_known_labels_include_labels_only_in_the_table(classifier):
    await classifier.add_image("cats", png((9, 9, 9)), label="Zeus", original_name=None, source="upload")
    await classifier.add_image("emotions", png((8, 8, 8)), label="grumpy", original_name=None, source="upload")
    assert await classifier.known_labels("cats", ["bean", "andy"]) == ["bean", "andy", "zeus"]


async def test_typing_a_known_cat_is_a_normal_correction(classifier, guessed):
    result = await correct(classifier, "Bean", allow_new=True)
    assert (result.label, result.new_label) == ("bean", False)


@pytest.mark.parametrize("name", ["", "   ", "../../etc", "a/b", ".hidden", "-rf", "x" * 33, "café", "+new"])
async def test_bad_new_cat_names_change_nothing(db, classifier, guessed, name):
    with pytest.raises(InvalidLabel):
        await correct(classifier, name, allow_new=True)
    assert await corrections(db) == [] and await rows(db) == []
    assert files(classifier) == []


async def test_second_correction_moves_the_photos(db, classifier, guessed):
    await correct(classifier, "bean", by=1)
    with pytest.raises(AlreadyLabeled):
        await correct(classifier, "bean", by=2)
    result = await correct(classifier, "merlin", by=2)

    assert result.previous_label == "bean"
    assert await saved(db) == [(PHOTO, "merlin", False), (PHOTO, "merlin", False)]
    assert len(files(classifier)) == 2  # Relabeling never moves or copies a file
    assert [(r.previous_label, r.label, r.corrected_by) for r in await corrections(db)] == [("andy", "bean", 1), ("bean", "merlin", 2)]


async def test_a_new_correction_restores_removed_photos(db, classifier, guessed):
    await correct(classifier, "bean")
    ids = [r.id for r in await rows(db) if r.id is not None]
    assert await classifier.update_images(ids, removed=True) == 2
    await correct(classifier, "merlin")
    assert await saved(db) == [(PHOTO, "merlin", False), (PHOTO, "merlin", False)]


async def test_correcting_back_to_the_guess_keeps_the_photo(db, classifier, guessed):
    await correct(classifier, "bean")
    await correct(classifier, "andy")
    assert await saved(db) == [(PHOTO, "andy", False), (PHOTO, "andy", False)]


async def test_other_messages_photos_are_left_alone(db, classifier, guessed):
    await guess(db, 5001)
    await correct(classifier, "bean", message_id=5001, images=[("a.jpg", png((5, 5, 5)))])
    await correct(classifier, "bean")
    await correct(classifier, "merlin")
    assert await saved(db) == [(5001, "bean", False), (PHOTO, "merlin", False), (PHOTO, "merlin", False)]


async def test_unknown_label_and_unclassified_message_change_nothing(db, classifier, guessed):
    with pytest.raises(UnknownLabel):
        await correct(classifier, "../../etc")
    with pytest.raises(NotClassified):
        await correct(classifier, "bean", message_id=PHOTO + 1)
    assert await corrections(db) == [] and await rows(db) == []
    assert files(classifier) == []


async def test_member_filenames_never_reach_the_disk(db, classifier, guessed):
    await correct(classifier, "bean", images=[("../../evil.sh;rm", png((1, 1, 1))), ("photo.HEIC", png((2, 2, 2)))])
    assert all(name.startswith("cats/images/") and name.endswith(".jpg") and ".." not in name for name in files(classifier))
    assert [r.original_name for r in await rows(db)] == ["../../evil.sh;rm", "photo.HEIC"]  # Kept as text only


async def test_unreadable_photos_are_skipped_but_the_correction_counts(db, classifier, guessed):
    result = await correct(classifier, "bean", images=[("bad.jpg", b"not an image"), IMAGES[0]])
    assert len(result.paths) == 1 and await saved(db) == [(PHOTO, "bean", False)]
    assert len(await corrections(db)) == 1


async def test_failed_save_rolls_the_correction_back(db, classifier, guessed):
    classifier.datasets_dir.write_text("a file where the folder should be")
    with pytest.raises(OSError):
        await correct(classifier, "bean")
    assert await corrections(db) == [] and await rows(db) == []


# --- The dataset ----------------------------------------------------------------------------


async def test_duplicates_are_returned_not_added(db, classifier):
    first, added = await classifier.add_image("cats", png((10, 20, 30)), label="bean", original_name="a.png", source="upload")
    again, added_again = await classifier.add_image("cats", png((10, 20, 30)), label="andy", original_name="b.png", source="upload")
    assert added and not added_again and again.id == first.id and again.label == "bean"
    # The same photo can be in another dataset.
    _, other = await classifier.add_image("emotions", png((10, 20, 30)), label="happy", original_name=None, source="upload")
    assert other and len(await rows(db)) == 2


async def test_re_adding_a_removed_photo_restores_it_with_the_new_label(classifier):
    image, _ = await classifier.add_image("cats", png((10, 20, 30)), label="bean", original_name=None, source="upload")
    assert image.id is not None
    await classifier.update_images([image.id], removed=True)
    restored, added = await classifier.add_image("cats", png((10, 20, 30)), label="andy", original_name=None, source="upload")
    assert added and restored.id == image.id and restored.label == "andy" and restored.removed_at is None


async def test_removed_photos_are_not_trained_on(classifier):
    keep, _ = await classifier.add_image("cats", png((1, 0, 0)), label="bean", original_name=None, source="upload")
    drop, _ = await classifier.add_image("cats", png((2, 0, 0)), label="bean", original_name=None, source="upload")
    assert drop.id is not None
    await classifier.update_images([drop.id], removed=True)
    assert [i.id for i in await classifier.training_images("cats")] == [keep.id]
    assert await classifier.label_counts("cats") == {"bean": {"photos": 1, "removed": 1}}


async def test_update_images_relabels_and_restores(classifier):
    a, _ = await classifier.add_image("cats", png((1, 0, 0)), label="bean", original_name=None, source="upload")
    b, _ = await classifier.add_image("cats", png((2, 0, 0)), label="bean", original_name=None, source="upload")
    ids = [a.id or 0, b.id or 0]
    assert await classifier.update_images(ids, label="Mr Whiskers") == 2
    assert {i.label for i in await classifier.training_images("cats")} == {"mr_whiskers"}
    with pytest.raises(InvalidLabel):
        await classifier.update_images(ids, label="../etc")
    assert await classifier.update_images(ids, removed=True) == 2
    assert await classifier.update_images(ids, removed=True) == 0  # Already removed
    assert await classifier.update_images(ids, removed=False) == 2
    page, total = await classifier.images("cats", label="mr_whiskers")
    assert total == 2 and len(page) == 2
    assert await classifier.datasets() == ["cats"]


async def test_a_big_png_is_shrunk_to_a_jpeg_and_a_small_one_is_not_enlarged(classifier):
    big, _ = await classifier.add_image("cats", png((200, 100, 50), size=(4000, 3000)), label="bean", original_name="big.png", source="upload")
    small, _ = await classifier.add_image("cats", png((50, 100, 200), size=(300, 200)), label="bean", original_name="small.png", source="upload")
    with Image.open(classifier.image_path(big)) as image:
        assert image.format == "JPEG" and max(image.size) == 2048 and image.size == (2048, 1536)
    with Image.open(classifier.image_path(small)) as image:
        assert image.format == "JPEG" and image.size == (300, 200)


async def test_exif_rotation_is_applied():
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # Rotated 90 degrees: the stored pixels are sideways
    Image.new("RGB", (40, 20), (0, 0, 0)).save(buffer, format="JPEG", exif=exif)
    with Image.open(io.BytesIO(photos.normalize_photo(buffer.getvalue()))) as image:
        assert image.size == (20, 40)


async def test_not_an_image_is_refused(db, classifier):
    with pytest.raises(NotAnImage):
        await classifier.add_image("cats", b"plain text", label="bean", original_name="notes.txt", source="upload")
    assert await rows(db) == []


async def test_training_runs(classifier):
    run = await classifier.start_run("cats", "web:aidan@example.com")
    assert run.id is not None and (await classifier.running_run()) is not None
    await classifier.finish_run(run.id, train_count=3, val_count=1, accuracy=1.0, log_loss=0.1, results={"labels": ["a", "b"]})
    failed = await classifier.start_run("cats", "discord:1")
    assert failed.id is not None
    await classifier.fail_run(failed.id, "boom")
    stale = await classifier.start_run("cats", "discord:1")
    assert await classifier.fail_stale_runs() == 1
    latest = await classifier.latest_run("cats")
    assert latest is not None and latest.id == run.id and latest.results == {"labels": ["a", "b"]}
    runs, total = await classifier.runs("cats")
    assert total == 3 and [r.status for r in runs] == ["failed", "failed", "succeeded"]
    assert (await classifier.run(stale.id or 0)).error == "Interrupted by a restart"
    assert await classifier.running_run() is None

    assert await classifier.changed_since("cats", run.started_at) == 0
    await classifier.add_image("cats", png((3, 3, 3)), label="bean", original_name=None, source="upload")
    assert await classifier.changed_since("cats", run.started_at) == 1


# --- Photo drops by DM -----------------------------------------------------------------------

MEMBER = 7
IDLE = datetime.timedelta(minutes=15)


async def start(classifier, label="bean", *, user=MEMBER, limit=200, allow_new=True, now=None):
    return await classifier.start_drop(
        GUILD, user, "cats", "cats", label, model_labels=LABELS, allow_new=allow_new, idle_timeout=IDLE, daily_limit=limit, now=now
    )


async def drop(classifier, session_id, data, *, name="dm.png", limit=200, now=None):
    return await classifier.drop_photo(session_id, data, name, daily_limit=limit, now=now)


async def drop_sessions(db) -> list[ClassifierDropSession]:
    async with db.transaction() as session:
        return list((await session.exec(select(ClassifierDropSession).order_by(col(ClassifierDropSession.id)))).all())


async def test_drop_keeps_photos_and_counts_them(db, classifier):
    started = await start(classifier, "bean")
    assert not started.new_label and started.ended is None
    session_id = started.session.id
    assert session_id is not None and started.session.last_photo_at == started.session.started_at

    later = utcnow() + datetime.timedelta(minutes=5)
    assert (await drop(classifier, session_id, png((1, 2, 3)), name="IMG_9.HEIC", now=later)).outcome == DropOutcome.KEPT
    assert (await drop(classifier, session_id, png((1, 2, 3)), now=later)).outcome == DropOutcome.DUPLICATE
    result = await drop(classifier, session_id, b"not a photo", now=later)
    assert result.outcome == DropOutcome.FAILED
    assert result.session is not None and (result.session.kept, result.session.duplicates, result.session.failed) == (1, 1, 1)
    assert result.session.last_photo_at == later

    (image,) = await rows(db)
    assert (image.source, image.label, image.guild_id, image.added_by, image.message_id) == ("drop", "bean", GUILD, MEMBER, None)
    assert image.original_name == "IMG_9.HEIC"


async def test_drop_unreadable_counts_as_failed(classifier):
    started = await start(classifier)
    result = await classifier.drop_unreadable(started.session.id or 0)
    assert result.outcome == DropOutcome.FAILED and result.session is not None and result.session.failed == 1


async def test_drop_names_go_through_normalize_label(classifier):
    started = await start(classifier, "Mr Whiskers")
    assert started.new_label and started.session.label == "mr_whiskers"
    with pytest.raises(UnknownLabel):
        await start(classifier, "Someone Else", allow_new=False)
    with pytest.raises(InvalidLabel):
        await start(classifier, "!!!")
    # A known name is fine without new labels.
    assert not (await start(classifier, "Andy", allow_new=False)).new_label


async def test_a_second_drop_ends_the_first(db, classifier):
    first = await start(classifier, "bean")
    second = await start(classifier, "andy")
    assert second.ended is not None and second.ended.id == first.session.id and second.ended.ended_at is not None
    current = await classifier.open_drop(MEMBER)
    assert current is not None and current.id == second.session.id
    assert [s.ended_at is None for s in await drop_sessions(db)] == [False, True]


async def test_ending_twice_is_harmless(db, classifier):
    started = await start(classifier)
    session_id = started.session.id or 0
    ended = await classifier.end_drop(session_id)
    assert ended is not None and ended.ended_at is not None
    assert await classifier.end_drop(session_id) is None
    (row,) = await drop_sessions(db)
    assert row.ended_at == ended.ended_at
    assert await classifier.open_drop(MEMBER) is None


async def test_photos_for_ended_or_expired_drops_are_refused(db, classifier):
    ended = await start(classifier)
    await classifier.end_drop(ended.session.id or 0)
    assert (await drop(classifier, ended.session.id or 0, png((9, 9, 9)))).outcome == DropOutcome.CLOSED

    now = utcnow()
    expired = await start(classifier, now=now)
    too_late = now + IDLE + datetime.timedelta(seconds=1)
    result = await drop(classifier, expired.session.id or 0, png((9, 9, 9)), now=too_late)
    assert result.outcome == DropOutcome.CLOSED
    assert await classifier.open_drop(MEMBER, now=too_late) is None
    assert await rows(db) == []
    assert [(s.kept, s.duplicates, s.failed) for s in await drop_sessions(db)] == [(0, 0, 0), (0, 0, 0)]
    assert (await drop(classifier, 12345, png((9, 9, 9)))).outcome == DropOutcome.CLOSED


async def test_daily_limit_refuses_the_photo_over_it(db, classifier):
    started = await start(classifier, limit=2)
    session_id = started.session.id or 0
    for color in [(1, 1, 1), (2, 2, 2)]:
        assert (await drop(classifier, session_id, png(color), limit=2)).outcome == DropOutcome.KEPT
    result = await drop(classifier, session_id, png((3, 3, 3)), limit=2)
    assert result.outcome == DropOutcome.OVER_LIMIT
    assert result.session is not None and (result.session.kept, result.session.duplicates, result.session.failed) == (2, 0, 0)
    assert len(await rows(db)) == 2
    # Duplicates don't use up the limit.
    assert (await drop(classifier, session_id, png((1, 1, 1)), limit=2)).outcome == DropOutcome.DUPLICATE
    # Other members have their own limit.
    other = await start(classifier, user=8, limit=2)
    assert (await drop(classifier, other.session.id or 0, png((3, 3, 3)), limit=2)).outcome == DropOutcome.KEPT
    # A new drop can't start once the limit is used up, and the open one stays open.
    with pytest.raises(DropLimitReached):
        await start(classifier, limit=2)
    current = await classifier.open_drop(MEMBER)
    assert current is not None and current.id == session_id
    # The window is 24 hours.
    tomorrow = utcnow() + datetime.timedelta(hours=24, seconds=1)
    assert (await start(classifier, limit=2, now=tomorrow)).session.id is not None


async def test_expired_drops_are_only_the_quiet_ones(classifier):
    now = utcnow()
    old = await start(classifier, user=1, now=now - IDLE - datetime.timedelta(seconds=1))
    await start(classifier, user=2, now=now - IDLE + datetime.timedelta(minutes=1))
    gone = await start(classifier, user=3, now=now - datetime.timedelta(hours=1))
    await classifier.end_drop(gone.session.id or 0)
    assert [d.id for d in await classifier.expired_drops(now)] == [old.session.id]
    assert await classifier.open_drops("cats", now) == 1


# --- Auto retrain ------------------------------------------------------------------------------

QUIET = datetime.timedelta(minutes=10)
INTERVAL = datetime.timedelta(hours=1)


async def add_photos(classifier, count, start_at=0):
    for i in range(start_at, start_at + count):
        await classifier.add_image("cats", png((i, 100, 200)), label="bean", original_name=None, source="upload")


async def due(classifier, *, after=datetime.timedelta(hours=2), interval=INTERVAL):
    return await classifier.retrain_due("cats", quiet_period=QUIET, min_interval=interval, now=utcnow() + after)


async def test_retrain_due_needs_a_change(classifier):
    assert not await due(classifier)
    await add_photos(classifier, 1)
    assert await due(classifier)  # With no run yet, every photo counts
    run = await classifier.start_run("cats", "auto")
    await classifier.finish_run(run.id or 0, train_count=1, val_count=0, accuracy=None, log_loss=None, results={})
    assert not await due(classifier)  # Nothing changed since that run
    await add_photos(classifier, 1, start_at=1)
    assert await due(classifier)  # One photo is enough


async def test_retrain_due_waits_for_the_quiet_period(classifier):
    await add_photos(classifier, 2)
    assert not await due(classifier, after=datetime.timedelta(minutes=5))
    assert await due(classifier, after=QUIET + datetime.timedelta(seconds=1))


async def test_retrain_due_waits_for_open_drops(classifier):
    await add_photos(classifier, 2)
    await start(classifier)
    assert not await due(classifier, after=QUIET + datetime.timedelta(minutes=1))
    assert await due(classifier, after=IDLE + datetime.timedelta(minutes=1))  # The drop expired, so it counts as closed


async def test_retrain_due_waits_for_the_interval_and_running_runs(classifier):
    await add_photos(classifier, 2)
    run = await classifier.start_run("cats", "auto")
    assert not await due(classifier)  # Still running
    await classifier.finish_run(run.id or 0, train_count=2, val_count=0, accuracy=None, log_loss=None, results={})
    await add_photos(classifier, 2, start_at=2)
    assert not await due(classifier, after=datetime.timedelta(minutes=30))
    assert await due(classifier, after=INTERVAL + datetime.timedelta(minutes=1))


async def test_failed_run_is_not_retried_until_a_photo_changes(classifier):
    await add_photos(classifier, 2)
    run = await classifier.start_run("cats", "auto")
    await classifier.fail_run(run.id or 0, "boom")
    assert not await due(classifier)
    await classifier.update_images([1], removed=True)  # Removing a photo is a change too
    assert await due(classifier)

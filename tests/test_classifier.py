import pytest
from sqlmodel import col, select

from beans_bot.db.models import ClassifierCorrection, ClassifierProcessedMessage
from beans_bot.services.classifier import AlreadyLabeled, InvalidLabel, NotClassified, UnknownLabel

from conftest import GUILD

PHOTO = 500
LABELS = ["andy", "bean", "merlin"]
IMAGES = [("IMG_1.JPG", b"first"), ("cat.png", b"second")]


@pytest.fixture
async def guessed(db):
    """The bot has guessed that PHOTO is andy."""
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=PHOTO, classifier_name="cats", channel_id=5, predicted_label="andy"))


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


def saved(classifier):
    root = classifier.corrections_dir
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


async def test_correction_saves_photos_under_the_right_label(db, classifier, guessed):
    result = await correct(classifier, "bean", by=7)

    assert (result.previous_label, result.label) == ("andy", "bean")
    assert saved(classifier) == ["cats/bean/500_1.jpg", "cats/bean/500_2.png"]
    assert [p.read_bytes() for p in result.paths] == [b"first", b"second"]
    (row,) = await corrections(db)
    assert (row.guild_id, row.message_id, row.classifier_name) == (GUILD, PHOTO, "cats")
    assert (row.previous_label, row.label, row.corrected_by) == ("andy", "bean", 7)


async def test_confirming_saves_photos_under_the_guess(db, classifier, guessed):
    result = await confirm(classifier, by=7)

    assert result.confirmed and not result.new_label
    assert saved(classifier) == ["cats/andy/500_1.jpg", "cats/andy/500_2.png"]
    (row,) = await corrections(db)
    assert (row.previous_label, row.label, row.corrected_by) == ("andy", "andy", 7)


async def test_confirming_twice_changes_nothing(db, classifier, guessed):
    await confirm(classifier, by=1)
    with pytest.raises(AlreadyLabeled):
        await confirm(classifier, by=2)
    with pytest.raises(AlreadyLabeled):
        await correct(classifier, "andy", by=2)
    assert len(await corrections(db)) == 1


async def test_picking_the_guess_counts_as_confirming(classifier, guessed):
    assert (await correct(classifier, "andy")).confirmed
    assert saved(classifier) == ["cats/andy/500_1.jpg", "cats/andy/500_2.png"]


async def test_confirmed_guess_can_still_be_corrected(classifier, guessed):
    await confirm(classifier)
    result = await correct(classifier, "bean")
    assert not result.confirmed and result.previous_label == "andy"
    assert saved(classifier) == ["cats/bean/500_1.jpg", "cats/bean/500_2.png"]


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
    # The photo finds every model's guess about it.
    assert sorted(g.classifier_name for g in await classifier.guesses_for(GUILD, [PHOTO])) == ["cats", "emotions"]
    # A reply plus the photo it points at: the guess that reply belongs to comes first.
    assert [g.classifier_name for g in await classifier.guesses_for(GUILD, [701, PHOTO])] == ["emotions", "cats"]
    assert await classifier.guesses_for(GUILD, [999]) == []
    assert await classifier.guesses_for(GUILD + 1, [PHOTO]) == []


async def test_new_cat_starts_a_label_everyone_can_pick(db, classifier, guessed):
    assert classifier.known_labels("cats", LABELS) == LABELS
    result = await correct(classifier, "  Mr Whiskers ", allow_new=True)

    assert (result.label, result.new_label, result.previous_label) == ("mr_whiskers", True, "andy")
    assert saved(classifier) == ["cats/mr_whiskers/500_1.jpg", "cats/mr_whiskers/500_2.png"]
    assert classifier.known_labels("cats", LABELS) == [*LABELS, "mr_whiskers"]

    # The next photo of the same cat picks it from the list, without allow_new.
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=501, classifier_name="cats", channel_id=5, predicted_label="bean"))
    again = await correct(classifier, "mr_whiskers", message_id=501)
    assert not again.new_label

    # Once both photos move elsewhere the label has no photos, so it's no longer offered.
    await correct(classifier, "bean")
    await correct(classifier, "merlin", message_id=501)
    assert classifier.known_labels("cats", LABELS) == LABELS


async def test_typing_a_known_cat_is_a_normal_correction(classifier, guessed):
    result = await correct(classifier, "Bean", allow_new=True)
    assert (result.label, result.new_label) == ("bean", False)


@pytest.mark.parametrize("name", ["", "   ", "../../etc", "a/b", ".hidden", "-rf", "x" * 33, "caf\u00e9", "+new"])
async def test_bad_new_cat_names_change_nothing(db, classifier, guessed, name):
    with pytest.raises(InvalidLabel):
        await correct(classifier, name, allow_new=True)
    assert await corrections(db) == []
    assert not classifier.corrections_dir.exists()


async def test_second_correction_moves_the_photos(db, classifier, guessed):
    await correct(classifier, "bean", by=1)
    with pytest.raises(AlreadyLabeled):
        await correct(classifier, "bean", by=2)
    result = await correct(classifier, "merlin", by=2)

    assert result.previous_label == "bean"
    assert saved(classifier) == ["cats/merlin/500_1.jpg", "cats/merlin/500_2.png"]
    assert [(r.previous_label, r.label, r.corrected_by) for r in await corrections(db)] == [("andy", "bean", 1), ("bean", "merlin", 2)]


async def test_correcting_back_to_the_guess_keeps_the_photo(classifier, guessed):
    await correct(classifier, "bean")
    await correct(classifier, "andy")
    assert saved(classifier) == ["cats/andy/500_1.jpg", "cats/andy/500_2.png"]


async def test_other_messages_photos_are_left_alone(db, classifier, guessed):
    async with db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=5001, classifier_name="cats", channel_id=5, predicted_label="andy"))
    await correct(classifier, "bean", message_id=5001, images=[("a.jpg", b"other")])
    await correct(classifier, "bean")
    await correct(classifier, "merlin")
    assert saved(classifier) == ["cats/bean/5001_1.jpg", "cats/merlin/500_1.jpg", "cats/merlin/500_2.png"]


async def test_unknown_label_and_unclassified_message_change_nothing(db, classifier, guessed):
    with pytest.raises(UnknownLabel):
        await correct(classifier, "../../etc")
    with pytest.raises(NotClassified):
        await correct(classifier, "bean", message_id=PHOTO + 1)
    assert await corrections(db) == []
    assert not classifier.corrections_dir.exists()


async def test_member_filenames_never_reach_the_disk(classifier, guessed):
    await correct(classifier, "bean", images=[("../../evil.sh;rm", b"x"), ("noextension", b"y"), ("photo.HEIC", b"z")])
    assert saved(classifier) == ["cats/bean/500_1.img", "cats/bean/500_2.img", "cats/bean/500_3.heic"]


async def test_failed_save_rolls_the_correction_back(db, classifier, guessed):
    classifier.corrections_dir.write_text("a file where the folder should be")
    with pytest.raises(OSError):
        await correct(classifier, "bean")
    assert await corrections(db) == []

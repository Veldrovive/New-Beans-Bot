"""The classifier's dataset on the website: photos, labels, splits, uploads and retraining."""

import asyncio
import io

from PIL import Image

from beans_bot.db.models import ClassifierProcessedMessage

from conftest import GUILD


def png(color, size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


async def upload(client, data, label="bean", split="auto", name="photo.png", dataset="cats"):
    return await client.post(
        f"/api/classifier/datasets/{dataset}/images", files={"file": (name, data, "image/png")}, data={"label": label, "split": split}
    )


async def test_upload_list_and_duplicates(web_client):
    response = await upload(web_client, png((10, 20, 30)), label="Mr Whiskers", split="val")
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["duplicate"] is False
    image = first["image"]
    assert (image["label"], image["split"], image["source"], image["original_name"]) == ("mr_whiskers", "val", "upload", "photo.png")

    again = (await upload(web_client, png((10, 20, 30)), label="bean")).json()
    assert again["duplicate"] is True and again["image"]["id"] == image["id"] and again["image"]["label"] == "mr_whiskers"
    await upload(web_client, png((40, 50, 60)), label="bean", split="train")

    page = (await web_client.get("/api/classifier/datasets/cats/images")).json()
    assert page["total"] == 2
    assert [i["label"] for i in (await web_client.get("/api/classifier/datasets/cats/images", params={"label": "bean"})).json()["items"]] == ["bean"]
    assert (await web_client.get("/api/classifier/datasets/cats/images", params={"split": "val"})).json()["total"] == 1

    (cats,) = (await web_client.get("/api/classifier/datasets")).json()
    assert cats["name"] == "cats" and cats["model"] is None and cats["latest_run"] is None and cats["running"] is False
    assert {c["label"]: (c["train"], c["val"]) for c in cats["labels"]} == {"bean": (1, 0), "mr_whiskers": (0, 1)}
    assert cats["guilds"] == [{"guild_id": str(GUILD), "guild_name": "Test", "model_name": "cats", "corrections": True}]


async def test_bad_uploads_are_refused(web_client):
    assert (await upload(web_client, b"not a photo", name="notes.txt")).status_code == 400
    assert (await upload(web_client, png((1, 1, 1)), label="../etc")).status_code == 400
    assert (await upload(web_client, png((1, 1, 1)), dataset="nope")).status_code == 404
    response = await upload(web_client, b"x" * (50 * 1024 * 1024 + 1))
    assert response.status_code == 400 and "50 MB" in response.json()["detail"]
    assert (await web_client.get("/api/classifier/datasets/cats/images")).json()["total"] == 0


async def test_files_and_thumbnails(web_client, web_services):
    image = (await upload(web_client, png((200, 100, 50), size=(1000, 800)))).json()["image"]
    large = await web_client.get(f"/api/classifier/images/{image['id']}/file", params={"size": "large"})
    assert large.status_code == 200 and large.headers["content-type"] == "image/jpeg" and "immutable" in large.headers["cache-control"]
    thumb = await web_client.get(f"/api/classifier/images/{image['id']}/file")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as decoded:
        assert decoded.format == "WEBP" and decoded.size == (320, 256)
    assert (web_services.config.classifier.datasets_dir / "cats" / "thumbs" / f"{image['content_hash']}.webp").exists()
    assert (await web_client.get("/api/classifier/images/999/file")).status_code == 404


async def test_patch_and_bulk(web_client):
    a = (await upload(web_client, png((1, 0, 0)))).json()["image"]
    b = (await upload(web_client, png((2, 0, 0)))).json()["image"]
    response = await web_client.patch(f"/api/classifier/images/{a['id']}", json={"label": "Andy", "split": "val"})
    assert response.status_code == 200 and (response.json()["label"], response.json()["split"]) == ("andy", "val")
    assert (await web_client.patch(f"/api/classifier/images/{a['id']}", json={"split": "test"})).status_code == 400
    assert (await web_client.patch(f"/api/classifier/images/{a['id']}", json={"label": "a/b"})).status_code == 400
    assert (await web_client.patch("/api/classifier/images/999", json={"removed": True})).status_code == 404

    response = await web_client.post("/api/classifier/images/bulk", json={"ids": [a["id"], b["id"]], "removed": True})
    assert response.json() == {"changed": 2}
    assert (await web_client.get("/api/classifier/datasets/cats/images")).json()["total"] == 0
    removed = (await web_client.get("/api/classifier/datasets/cats/images", params={"removed": "true"})).json()
    assert removed["total"] == 2 and all(i["removed_at"] for i in removed["items"])
    await web_client.post("/api/classifier/images/bulk", json={"ids": [a["id"]], "removed": False, "split": "train"})
    restored = (await web_client.get("/api/classifier/datasets/cats/images")).json()["items"]
    assert [(i["id"], i["split"]) for i in restored] == [(a["id"], "train")]


async def test_corrections_link_to_their_message(web_client, web_services):
    async with web_services.db.transaction() as session:
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=500, classifier_name="cats", channel_id=77, predicted_label="andy"))
    await web_services.classifier.confirm(GUILD, 500, "cats", "cats", 3, [("a.png", png((5, 5, 5)))])
    (image,) = (await web_client.get("/api/classifier/datasets/cats/images")).json()["items"]
    assert (image["source"], image["guild_id"], image["channel_id"], image["message_id"], image["added_by"]) == ("correction", str(GUILD), "77", "500", "3")


async def test_retrain_needs_the_cog(web_client):
    assert (await web_client.post("/api/classifier/datasets/cats/retrain")).status_code == 503
    assert (await web_client.get("/api/classifier/datasets/cats/progress")).json() == {"running": False, "done": 0, "total": 0}


async def test_retrain_through_the_hook(web_client, web_services):
    service = web_services.classifier
    release = asyncio.Event()

    async def retrainer(dataset, triggered_by):
        run = await service.start_run(dataset, triggered_by)
        assert run.id is not None
        service.progress[dataset] = (1, 4)
        await release.wait()
        service.progress.pop(dataset)
        return await service.finish_run(run.id, train_count=3, val_count=1, accuracy=1.0, log_loss=0.05, results={"labels": ["a", "b"]})

    service.retrainer = retrainer
    service.model_info = lambda dataset: {"loaded": True, "source": "shipped", "labels": ["a", "b"]}
    response = await web_client.post("/api/classifier/datasets/cats/retrain", headers={"Cf-Access-Authenticated-User-Email": "aidan@example.com"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert (await web_client.get("/api/classifier/datasets/cats/progress")).json() == {"running": True, "done": 1, "total": 4}
    assert (await web_client.post("/api/classifier/datasets/cats/retrain")).status_code == 409
    (cats,) = (await web_client.get("/api/classifier/datasets")).json()
    assert cats["running"] is True and cats["model"] == {"loaded": True, "source": "shipped", "labels": ["a", "b"]}

    release.set()
    for _ in range(50):
        if not (await web_client.get("/api/classifier/datasets/cats/progress")).json()["running"]:
            break
        await asyncio.sleep(0.02)
    runs = (await web_client.get("/api/classifier/datasets/cats/runs")).json()
    assert runs["total"] == 1 and "results" not in runs["items"][0]
    detail = (await web_client.get(f"/api/classifier/runs/{run_id}")).json()
    assert (detail["status"], detail["triggered_by"], detail["accuracy"], detail["results"]) == ("succeeded", "web:aidan@example.com", 1.0, {"labels": ["a", "b"]})
    (cats,) = (await web_client.get("/api/classifier/datasets")).json()
    assert cats["latest_run"]["id"] == run_id and cats["changed_since_latest_run"] == 0
    assert (await web_client.get("/api/classifier/runs/999")).status_code == 404

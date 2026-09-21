from __future__ import annotations

from collections.abc import Generator
from io import BytesIO
from pathlib import Path
import struct
import zlib

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import create_app


@pytest.fixture
def product_image_api(
    tmp_path: Path,
) -> Generator[tuple[TestClient, Path], None, None]:
    database_path = tmp_path / "product-images-test.db"
    uploads_directory = tmp_path / "uploads"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    Base.metadata.create_all(bind=engine)
    application = create_app(uploads_directory=uploads_directory)

    def override_get_db():
        with testing_session_local() as db:
            yield db

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client, uploads_directory

    application.dependency_overrides.clear()
    engine.dispose()


def make_image_bytes(
    image_format: str,
    size: tuple[int, int] = (240, 180),
    mode: str = "RGB",
    color: tuple[int, ...] = (210, 90, 60),
    orientation: int | None = None,
) -> bytes:
    image = Image.new(mode, size, color)
    save_options: dict[str, object] = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        save_options["exif"] = exif
    output = BytesIO()
    image.save(output, format=image_format, **save_options)
    return output.getvalue()


def upload_image(
    client: TestClient,
    data: bytes,
    filename: str,
    content_type: str,
):
    return client.post(
        "/api/product-images",
        files={"file": (filename, data, content_type)},
    )


def file_paths(uploads_directory: Path, payload: dict[str, str]) -> tuple[Path, Path]:
    return (
        uploads_directory / payload["image_path"],
        uploads_directory / payload["thumbnail_path"],
    )


@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [
        ("JPEG", "phone-photo.jpg", "image/jpeg"),
        ("PNG", "plate.png", "image/png"),
        ("WEBP", "box.webp", "image/webp"),
    ],
)
def test_supported_formats_create_webp_pair_and_are_publicly_readable(
    product_image_api: tuple[TestClient, Path],
    image_format: str,
    filename: str,
    content_type: str,
) -> None:
    client, uploads_directory = product_image_api
    response = upload_image(
        client,
        make_image_bytes(image_format),
        filename,
        content_type,
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["image_path"].startswith("products/main/")
    assert payload["thumbnail_path"].startswith("products/thumbs/")
    assert payload["image_url"] == f"/uploads/{payload['image_path']}"
    assert payload["thumbnail_url"] == f"/uploads/{payload['thumbnail_path']}"
    main_path, thumbnail_path = file_paths(uploads_directory, payload)
    assert main_path.is_file()
    assert thumbnail_path.is_file()
    assert main_path.name != filename
    assert main_path.name == thumbnail_path.name
    assert main_path.suffix == thumbnail_path.suffix == ".webp"
    assert main_path.stat().st_size > 0
    assert thumbnail_path.stat().st_size > 0

    for saved_path in (main_path, thumbnail_path):
        with Image.open(saved_path) as saved_image:
            assert saved_image.format == "WEBP"

    served_image = client.get(payload["image_url"])
    served_thumbnail = client.get(payload["thumbnail_url"])
    assert served_image.status_code == 200
    assert served_thumbnail.status_code == 200
    assert served_image.headers["content-type"] == "image/webp"
    assert (
        served_image.headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )


def test_large_source_is_resized_without_cropping_or_distortion(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, uploads_directory = product_image_api
    source = Image.effect_noise((4032, 3024), 85).convert("RGB")
    source_bytes = BytesIO()
    source.save(source_bytes, format="JPEG", quality=91)
    raw_data = source_bytes.getvalue()
    assert len(raw_data) < 10 * 1024 * 1024

    response = upload_image(client, raw_data, "phone-4032x3024.jpg", "image/jpeg")
    assert response.status_code == 201
    main_path, thumbnail_path = file_paths(uploads_directory, response.json())

    with Image.open(main_path) as main_image:
        assert main_image.size == (1600, 1200)
        assert main_image.format == "WEBP"
    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.size == (320, 240)
        assert thumbnail.format == "WEBP"


def test_small_source_is_not_upscaled_for_main_or_thumbnail(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, uploads_directory = product_image_api
    response = upload_image(
        client,
        make_image_bytes("PNG", (250, 200)),
        "small.png",
        "image/png",
    )
    assert response.status_code == 201
    main_path, thumbnail_path = file_paths(uploads_directory, response.json())

    with Image.open(main_path) as main_image:
        assert main_image.size == (250, 200)
    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.size == (250, 200)


def test_exif_orientation_is_applied_before_resizing(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, uploads_directory = product_image_api
    response = upload_image(
        client,
        make_image_bytes("JPEG", (640, 320), orientation=6),
        "rotated.jpg",
        "image/jpeg",
    )
    assert response.status_code == 201
    main_path, thumbnail_path = file_paths(uploads_directory, response.json())

    with Image.open(main_path) as main_image:
        assert main_image.size == (320, 640)
    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.size == (160, 320)


def test_png_alpha_is_preserved_in_webp_outputs(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, uploads_directory = product_image_api
    response = upload_image(
        client,
        make_image_bytes("PNG", (80, 60), mode="RGBA", color=(30, 120, 220, 0)),
        "transparent.png",
        "image/png",
    )
    assert response.status_code == 201
    main_path, thumbnail_path = file_paths(uploads_directory, response.json())

    for saved_path in (main_path, thumbnail_path):
        with Image.open(saved_path) as image:
            assert "A" in image.getbands()
            assert image.getpixel((0, 0))[3] == 0


def test_each_upload_uses_a_distinct_filename(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    data = make_image_bytes("PNG")
    first = upload_image(client, data, "same.png", "image/png").json()
    second = upload_image(client, data, "same.png", "image/png").json()
    assert first["image_path"] != second["image_path"]
    assert first["thumbnail_path"] != second["thumbnail_path"]


def test_animated_webp_is_rejected(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    output = BytesIO()
    frames = [Image.new("RGB", (32, 24), color) for color in ("red", "blue")]
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    response = upload_image(client, output.getvalue(), "animated.webp", "image/webp")
    assert response.status_code == 415
    assert "动画" in response.json()["detail"]


def test_non_image_and_masquerading_jpg_are_rejected(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, uploads_directory = product_image_api
    text_response = upload_image(
        client, b"not an image", "notes.txt", "text/plain"
    )
    disguised_response = upload_image(
        client, b"not a jpeg", "fake.jpg", "image/jpeg"
    )
    assert text_response.status_code == 415
    assert disguised_response.status_code == 400
    assert not list(uploads_directory.rglob("*.webp"))


def test_image_with_mismatched_extension_is_rejected(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    response = upload_image(
        client,
        make_image_bytes("PNG"),
        "actually-png.jpg",
        "image/jpeg",
    )
    assert response.status_code == 400


def test_upload_over_10mb_is_rejected_before_decode(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    response = upload_image(
        client,
        b"x" * (10 * 1024 * 1024 + 1),
        "too-large.jpg",
        "image/jpeg",
    )
    assert response.status_code == 413
    assert "10MB" in response.json()["detail"]


def test_high_pixel_count_is_rejected_safely(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    tiny_png = bytearray(make_image_bytes("PNG", (1, 1)))
    tiny_png[16:24] = struct.pack(">II", 100_000, 100_000)
    tiny_png[29:33] = struct.pack(">I", zlib.crc32(tiny_png[12:29]))
    response = upload_image(
        client, bytes(tiny_png), "huge.png", "image/png"
    )
    assert response.status_code == 413
    assert "分辨率" in response.json()["detail"]


def test_dangerous_original_filename_is_never_used_as_path(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, uploads_directory = product_image_api
    response = upload_image(
        client,
        make_image_bytes("JPEG"),
        "../outside/微信图片.jpg",
        "image/jpeg",
    )
    assert response.status_code == 201
    payload = response.json()
    main_path, thumbnail_path = file_paths(uploads_directory, payload)
    assert main_path.parent == (uploads_directory / "products" / "main")
    assert thumbnail_path.parent == (uploads_directory / "products" / "thumbs")
    assert "outside" not in payload["image_path"]
    assert "微信图片" not in payload["image_path"]
    assert main_path.name == thumbnail_path.name


def test_product_create_and_update_store_image_paths_without_changing_code(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    parent_response = client.post("/api/categories", json={"name": "Image tests"})
    assert parent_response.status_code == 201
    child_response = client.post(
        "/api/categories",
        json={"name": "Plates", "parent_id": parent_response.json()["id"]},
    )
    assert child_response.status_code == 201
    category_id = child_response.json()["id"]

    old_image = upload_image(
        client, make_image_bytes("JPEG"), "old.jpg", "image/jpeg"
    ).json()
    new_image = upload_image(
        client, make_image_bytes("PNG"), "new.png", "image/png"
    ).json()
    created = client.post(
        "/api/products",
        json={
            "category_id": category_id,
            "warehouse_id": None,
            "image_path": old_image["image_path"],
            "thumbnail_path": old_image["thumbnail_path"],
            "size": "IMAGE-TEST",
            "packing_qty": 24,
            "unit": "pcs",
            "price": "1.00",
            "carton_count": 10,
            "remark": "temporary image API test",
        },
    )
    assert created.status_code == 201
    original_product = created.json()
    assert original_product["thumbnail_path"] == old_image["thumbnail_path"]

    updated = client.patch(
        f"/api/products/{original_product['id']}",
        json={
            "image_path": new_image["image_path"],
            "thumbnail_path": new_image["thumbnail_path"],
        },
    )
    assert updated.status_code == 200
    updated_product = updated.json()
    assert updated_product["image_path"] == new_image["image_path"]
    assert updated_product["thumbnail_path"] == new_image["thumbnail_path"]
    assert updated_product["product_code"] == original_product["product_code"]
    assert updated_product["category_id"] == category_id
    assert updated_product["warehouse_id"] is None
    assert updated_product["carton_count"] == 10


def test_products_without_images_and_legacy_null_thumbnail_remain_readable(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    response = client.post(
        "/api/products",
        json={
            "image_path": None,
            "thumbnail_path": None,
            "size": "NO-IMAGE",
            "packing_qty": 1,
            "unit": "set",
            "price": "0.00",
            "carton_count": 0,
            "remark": None,
        },
    )
    assert response.status_code == 201
    product_id = response.json()["id"]
    assert response.json()["thumbnail_path"] is None
    fetched = client.get(f"/api/products/{product_id}")
    assert fetched.status_code == 200
    assert fetched.json()["image_path"] is None
    assert fetched.json()["thumbnail_path"] is None


def test_legacy_main_image_with_null_thumbnail_remains_readable(
    product_image_api: tuple[TestClient, Path],
) -> None:
    client, _uploads_directory = product_image_api
    response = client.post(
        "/api/products",
        json={
            "image_path": "products/main/legacy-product.webp",
            "thumbnail_path": None,
            "size": "LEGACY-IMAGE",
            "packing_qty": 1,
            "unit": "pcs",
            "price": "0.00",
            "carton_count": 0,
            "remark": None,
        },
    )
    assert response.status_code == 201
    assert response.json()["image_path"] == "products/main/legacy-product.webp"
    assert response.json()["thumbnail_path"] is None

    fetched = client.get(f"/api/products/{response.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["image_path"] == "products/main/legacy-product.webp"
    assert fetched.json()["thumbnail_path"] is None

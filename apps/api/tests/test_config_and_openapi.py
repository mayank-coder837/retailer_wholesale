import json
from pathlib import Path

from app.core.config import DEFAULT_PRODUCT_CONFIG, Settings, load_product
from app.main import create_app
from app.scripts.export_openapi import export


def test_product_name_comes_from_the_shared_config_file() -> None:
    raw = json.loads(DEFAULT_PRODUCT_CONFIG.read_text(encoding="utf-8"))

    assert Settings().product.name == raw["name"]


def test_product_config_path_can_point_elsewhere(tmp_path: Path) -> None:
    other = tmp_path / "product.json"
    other.write_text(
        json.dumps(
            {"name": "Renamed", "slug": "r", "appId": "x.y", "servicePrefix": "R", "localDbPort": 1},
        ),
        encoding="utf-8",
    )
    load_product.cache_clear()

    assert Settings(product_config_path=other).product.name == "Renamed"


def test_openapi_title_uses_product_name_and_lists_health() -> None:
    schema = create_app().openapi()

    assert schema["info"]["title"] == f"{Settings().product.name} API"
    assert "/health" in schema["paths"]
    assert "/health/db" in schema["paths"]


def test_export_openapi_writes_schema(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    export(target)

    assert json.loads(target.read_text(encoding="utf-8"))["paths"]["/health"]

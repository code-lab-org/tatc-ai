"""Source library resolution and its failure modes."""

import asyncio
import copy
import json
from pathlib import Path
from types import MappingProxyType

import jsonschema
import pytest
from fastmcp import Client

from src import configuration_registry, server as server_module, source_library
from src.conical_config import (
    CLAIM_SCHEMA_VERSION as CONICAL_CLAIM_VERSION,
    ConicalConfiguration,
)
from src.conical_study import _load_configuration, _resolve_mixed_configuration
from src.pointed_config import (
    CLAIM_SCHEMA_VERSION as POINTED_CLAIM_VERSION,
    PointedConfiguration,
)
from src.source_library import load_source_library

DATA = Path(__file__).resolve().parents[1] / "data"
CONFIGURATIONS = DATA / "configurations"
AMSR2 = CONFIGURATIONS / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
SENTINEL = CONFIGURATIONS / "sentinel-1c-c-sar-iw-fixed-view-2026-10-30.1.json"


def _group_key(item):
    return (
        item["authority"],
        item["title"],
        item["url"],
        item["document_revision"],
        item["retrieved_at_utc"],
    )


def _write_library(evidence, root, prefix="doc"):
    """Split a record's evidence into documents plus claims, keeping order."""
    document_ids = {}
    for item in evidence:
        document_ids.setdefault(
            _group_key(item), f"{prefix}-{len(document_ids) + 1}"
        )

    excerpts = {key: [] for key in document_ids}
    claims = []
    for item in evidence:
        key = _group_key(item)
        excerpt_id = f"ex-{len(excerpts[key]) + 1:02d}"
        excerpts[key].append(
            {
                "excerpt_id": excerpt_id,
                "locator": item["locator"],
                "text": item["excerpt"],
            }
        )
        claims.append(
            {
                "evidence_id": item["evidence_id"],
                "source": f"{document_ids[key]}@rev-1#{excerpt_id}",
                "interpretation": item["interpretation"],
                "supports": item["supports"],
            }
        )

    for index, (key, items) in enumerate(excerpts.items(), start=1):
        authority, title, url, document_revision, retrieved_at = key
        document = {
            "schema_version": source_library.SCHEMA_VERSION,
            "document_id": f"{prefix}-{index}",
            "revision": "rev-1",
            "authority": authority,
            "title": title,
            "url": url,
            "document_revision": document_revision,
            "retrieved_at_utc": retrieved_at,
            "excerpts": items,
        }
        (Path(root) / f"{prefix}-{index}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
    return claims


def _claims_record(path, root, claim_version, prefix="doc"):
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = copy.deepcopy(raw)
    raw["source_claims"] = _write_library(raw.pop("evidence"), root, prefix)
    raw["schema_version"] = claim_version
    return raw, expected


def _call(name, arguments):
    async def run():
        async with Client(server_module.mcp) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)

    result = asyncio.run(run())
    text = "\n".join(getattr(part, "text", "") for part in result.content)
    payload = None
    if not result.is_error:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return result.is_error, text, payload


@pytest.mark.parametrize(
    "path,model,claim_version",
    [
        (AMSR2, ConicalConfiguration, CONICAL_CLAIM_VERSION),
        (SENTINEL, PointedConfiguration, POINTED_CLAIM_VERSION),
    ],
)
def test_claims_resolve_to_the_same_evidence(
    path, model, claim_version, tmp_path, monkeypatch
):
    raw, inline = _claims_record(path, tmp_path, claim_version)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    expected = model.model_validate(inline)

    resolved = model.model_validate(raw)

    assert resolved.evidence == expected.evidence
    assert resolved.schema_version == model.model_fields["schema_version"].default
    assert "source_claims" not in resolved.model_dump(mode="json")


def test_inline_evidence_and_claims_together_are_rejected(tmp_path, monkeypatch):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    raw["evidence"] = copy.deepcopy(json.loads(AMSR2.read_text(encoding="utf-8"))["evidence"])

    with pytest.raises(ValueError, match="both inline evidence and source_claims"):
        ConicalConfiguration.model_validate(raw)


def test_claim_version_requires_claims():
    raw = json.loads(AMSR2.read_text(encoding="utf-8"))
    raw["schema_version"] = CONICAL_CLAIM_VERSION

    with pytest.raises(ValueError, match="without source_claims"):
        ConicalConfiguration.model_validate(raw)


def test_claims_require_the_claim_version(tmp_path, monkeypatch):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    raw["schema_version"] = "conical-configuration-v1"

    with pytest.raises(ValueError, match="claims require"):
        ConicalConfiguration.model_validate(raw)


def test_missing_document_revision_names_the_claim(tmp_path, monkeypatch):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    raw["source_claims"][0]["source"] = "missing@rev-1#ex-01"

    with pytest.raises(ValueError) as error:
        ConicalConfiguration.model_validate(raw)

    message = str(error.value)
    assert raw["configuration_id"] in message
    assert raw["source_claims"][0]["evidence_id"] in message
    assert "missing@rev-1" in message


def test_missing_excerpt_names_the_reference(tmp_path, monkeypatch):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    raw["source_claims"][0]["source"] = "doc-1@rev-1#ex-99"

    with pytest.raises(ValueError, match="ex-99"):
        ConicalConfiguration.model_validate(raw)


def test_malformed_reference_is_rejected(tmp_path, monkeypatch):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    raw["source_claims"][0]["source"] = "doc-1#ex-01"

    with pytest.raises(ValueError, match="document_id@revision#excerpt_id"):
        ConicalConfiguration.model_validate(raw)


def test_duplicate_excerpt_id_is_rejected(tmp_path):
    document = {
        "schema_version": source_library.SCHEMA_VERSION,
        "document_id": "doc-1",
        "revision": "rev-1",
        "authority": "Test",
        "title": "Test source",
        "url": "https://example.invalid/source",
        "document_revision": "rev-1",
        "retrieved_at_utc": "2026-10-27T00:00:00Z",
        "excerpts": [
            {"excerpt_id": "ex-01", "locator": "a", "text": "first"},
            {"excerpt_id": "ex-01", "locator": "b", "text": "second"},
        ],
    }
    (tmp_path / "doc-1.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="repeats an excerpt_id"):
        load_source_library(tmp_path)


def test_duplicate_document_revision_across_files_is_rejected(tmp_path):
    document = {
        "schema_version": source_library.SCHEMA_VERSION,
        "document_id": "doc-1",
        "revision": "rev-1",
        "authority": "Test",
        "title": "Test source",
        "url": "https://example.invalid/source",
        "document_revision": "rev-1",
        "retrieved_at_utc": "2026-10-27T00:00:00Z",
        "excerpts": [{"excerpt_id": "ex-01", "locator": "a", "text": "first"}],
    }
    for name in ("one.json", "two.json"):
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="repeats document"):
        load_source_library(tmp_path)


def test_library_file_outside_the_root_is_rejected(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("not json", encoding="utf-8")
    root = tmp_path / "sources"
    root.mkdir()
    (root / "link.json").symlink_to(outside)

    with pytest.raises(ValueError, match="resolves outside the source library"):
        load_source_library(root)


def test_malformed_library_file_names_the_record(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)

    with pytest.raises(ValueError) as error:
        ConicalConfiguration.model_validate(raw)

    message = str(error.value)
    assert raw["configuration_id"] in message
    assert "cannot read the source library" in message
    assert raw["source_claims"][0]["source"] in message


def test_explicit_null_claims_with_inline_evidence_is_rejected():
    raw = json.loads(AMSR2.read_text(encoding="utf-8"))
    raw["source_claims"] = None

    with pytest.raises(ValueError, match="both inline evidence and source_claims"):
        ConicalConfiguration.model_validate(raw)


def test_library_root_symlink_loop_is_reported(tmp_path):
    root = tmp_path / "loop"
    root.symlink_to(root)

    with pytest.raises(ValueError, match="source library root"):
        load_source_library(root)


def test_mapping_input_is_normalized(tmp_path, monkeypatch):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    raw["evidence"] = copy.deepcopy(
        json.loads(AMSR2.read_text(encoding="utf-8"))["evidence"]
    )

    with pytest.raises(ValueError, match="both inline evidence and source_claims"):
        ConicalConfiguration.model_validate(MappingProxyType(raw))


def test_symlink_loop_is_reported_as_a_library_error(tmp_path):
    (tmp_path / "a.json").symlink_to(tmp_path / "b.json")
    (tmp_path / "b.json").symlink_to(tmp_path / "a.json")

    with pytest.raises(ValueError, match="Cannot resolve source document"):
        load_source_library(tmp_path)


def test_identifiers_reject_reference_separators(tmp_path):
    document = {
        "schema_version": source_library.SCHEMA_VERSION,
        "document_id": "doc@1",
        "revision": "rev-1",
        "authority": "Test",
        "title": "Test source",
        "url": "https://example.invalid/source",
        "document_revision": "rev-1",
        "retrieved_at_utc": "2026-10-27T00:00:00Z",
        "excerpts": [{"excerpt_id": "ex-01", "locator": "a", "text": "first"}],
    }
    (tmp_path / "doc.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain"):
        load_source_library(tmp_path)


def test_inline_study_configuration_accepts_claims(tmp_path, monkeypatch):
    raw, inline = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    expected = ConicalConfiguration.model_validate(inline)

    configuration = _load_configuration(raw, "conical")

    assert configuration.evidence == expected.evidence


def test_prepare_conical_study_accepts_claims_at_the_tool_boundary(
    tmp_path, monkeypatch
):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)

    error, text, _ = _call(
        "prepare_conical_study",
        {
            "draft_id": "missing-draft",
            "grid_rows": 2,
            "grid_columns": 2,
            "configuration": raw,
        },
    )

    assert error
    assert "source_claims" not in text
    assert "draft" in text.lower()


def test_prepare_pointed_study_accepts_claims_at_the_tool_boundary(
    tmp_path, monkeypatch
):
    raw, _ = _claims_record(SENTINEL, tmp_path, POINTED_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)

    error, text, _ = _call(
        "prepare_pointed_study",
        {
            "draft_id": "missing-draft",
            "grid_rows": 2,
            "grid_columns": 2,
            "configuration": raw,
        },
    )

    assert error
    assert "source_claims" not in text
    assert "draft" in text.lower()


@pytest.mark.parametrize(
    "path,model,claim_version",
    [
        (AMSR2, ConicalConfiguration, CONICAL_CLAIM_VERSION),
        (SENTINEL, PointedConfiguration, POINTED_CLAIM_VERSION),
    ],
)
def test_advertised_schema_accepts_both_forms(
    path, model, claim_version, tmp_path, monkeypatch
):
    raw, inline = _claims_record(path, tmp_path, claim_version)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    schema = model.model_json_schema()

    assert "evidence" not in schema.get("required", [])
    assert "source_claims" in schema["properties"]
    jsonschema.validate(inline, schema)
    jsonschema.validate(raw, schema)


def test_mixed_dispatch_accepts_the_claim_version(tmp_path, monkeypatch):
    raw, inline = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)
    expected = ConicalConfiguration.model_validate(inline)

    configuration, selection, direction = _resolve_mixed_configuration(
        {"configuration": raw}
    )

    assert configuration.evidence == expected.evidence
    assert selection is None
    assert direction is None


def test_prepare_mixed_study_accepts_claims_at_the_tool_boundary(
    tmp_path, monkeypatch
):
    raw, _ = _claims_record(AMSR2, tmp_path, CONICAL_CLAIM_VERSION)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)

    error, text, _ = _call(
        "prepare_mixed_study",
        {
            "draft_id": "missing-draft",
            "grid_rows": 2,
            "grid_columns": 2,
            "configurations": [{"configuration": raw}],
        },
    )

    assert error
    assert "Unsupported mixed configuration schema_version" not in text
    assert "source_claims" not in text


def _registry_tree(root, configuration, manifest):
    (root / "configurations").mkdir(parents=True)
    (root / "configurations" / "amsr2.json").write_text(
        json.dumps(configuration), encoding="utf-8"
    )
    (root / "instrument_configuration_registry.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / "pointed_instrument_configuration_registry.json").write_text(
        json.dumps(
            {
                "schema_version": "pointed-instrument-configuration-registry-v1",
                "entries": [],
            }
        ),
        encoding="utf-8",
    )


def _lookup(monkeypatch, data_dir, sources, configuration_id):
    monkeypatch.setattr(source_library, "SOURCES_DIR", sources)
    monkeypatch.setattr(configuration_registry, "DATA_DIR", data_dir)
    monkeypatch.setattr(
        configuration_registry,
        "REGISTRY_PATH",
        data_dir / "instrument_configuration_registry.json",
    )
    monkeypatch.setattr(
        configuration_registry,
        "POINTED_REGISTRY_PATH",
        data_dir / "pointed_instrument_configuration_registry.json",
    )
    configuration_registry.clear_refresh_state()
    error, text, payload = _call(
        "lookup_instrument_configurations",
        {"configuration_id": configuration_id},
    )
    configuration_registry.clear_refresh_state()
    assert not error, text
    return payload["candidates"][0]


def test_lookup_keeps_the_full_response_for_a_claims_record(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    claims, inline = _claims_record(AMSR2, sources, CONICAL_CLAIM_VERSION)
    manifest = json.loads(
        (DATA / "instrument_configuration_registry.json").read_text(encoding="utf-8")
    )
    manifest["entries"][0]["configuration_path"] = "configurations/amsr2.json"

    inline_dir = tmp_path / "inline"
    claims_dir = tmp_path / "claims"
    _registry_tree(inline_dir, inline, manifest)
    _registry_tree(claims_dir, claims, manifest)

    baseline = _lookup(
        monkeypatch, inline_dir, tmp_path / "empty", inline["configuration_id"]
    )
    migrated = _lookup(monkeypatch, claims_dir, sources, claims["configuration_id"])

    assert migrated == baseline


def test_reviewed_records_keep_their_inline_evidence():
    first = ConicalConfiguration.model_validate_json(AMSR2.read_text(encoding="utf-8"))
    second = PointedConfiguration.model_validate_json(
        SENTINEL.read_text(encoding="utf-8")
    )

    assert len(first.evidence) == 11
    assert len(second.evidence) == 4
    assert first.evidence[0].title == "Conical Scanner Validation: AMSR2"

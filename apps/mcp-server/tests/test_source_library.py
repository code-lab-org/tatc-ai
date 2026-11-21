"""Source library resolution and its failure modes."""

import copy
import json
from pathlib import Path

import pytest

from src import source_library
from src.conical_config import ConicalConfiguration
from src.conical_study import _load_configuration
from src.pointed_config import PointedConfiguration
from src.source_library import load_source_library, resolve_configuration_data

DATA = Path(__file__).resolve().parents[1] / "data" / "configurations"
AMSR2 = DATA / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
SENTINEL = DATA / "sentinel-1c-c-sar-iw-fixed-view-2026-10-30.1.json"


def _group_key(item):
    return (
        item["authority"],
        item["title"],
        item["url"],
        item["document_revision"],
        item["retrieved_at_utc"],
    )


def _write_library(evidence, root):
    """Split a record's evidence into documents plus claims, keeping order."""
    document_ids = {}
    for item in evidence:
        document_ids.setdefault(_group_key(item), f"doc-{len(document_ids) + 1}")

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
            "document_id": f"doc-{index}",
            "revision": "rev-1",
            "authority": authority,
            "title": title,
            "url": url,
            "document_revision": document_revision,
            "retrieved_at_utc": retrieved_at,
            "excerpts": items,
        }
        (Path(root) / f"doc-{index}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
    return claims


def _claims_record(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw


def _resolve(raw, root):
    return resolve_configuration_data(copy.deepcopy(raw), root=Path(root))


@pytest.mark.parametrize(
    "path,model",
    [(AMSR2, ConicalConfiguration), (SENTINEL, PointedConfiguration)],
)
def test_claims_resolve_to_the_same_evidence(path, model, tmp_path):
    raw = _claims_record(path)
    expected = model.model_validate(copy.deepcopy(raw))
    raw["source_claims"] = _write_library(raw.pop("evidence"), tmp_path)

    resolved = _resolve(raw, tmp_path)

    assert "source_claims" not in resolved
    assert model.model_validate(resolved).evidence == expected.evidence


def test_inline_evidence_and_claims_together_are_rejected(tmp_path):
    raw = _claims_record(AMSR2)
    raw["source_claims"] = _write_library(raw["evidence"], tmp_path)

    with pytest.raises(ValueError, match="both inline evidence and source_claims"):
        _resolve(raw, tmp_path)


def test_missing_document_revision_names_the_claim(tmp_path):
    raw = _claims_record(AMSR2)
    raw["source_claims"] = _write_library(raw.pop("evidence"), tmp_path)
    raw["source_claims"][0]["source"] = "missing@rev-1#ex-01"

    with pytest.raises(ValueError) as error:
        _resolve(raw, tmp_path)

    message = str(error.value)
    assert raw["configuration_id"] in message
    assert raw["source_claims"][0]["evidence_id"] in message
    assert "missing@rev-1" in message


def test_missing_excerpt_names_the_reference(tmp_path):
    raw = _claims_record(AMSR2)
    claims = _write_library(raw.pop("evidence"), tmp_path)
    raw["source_claims"] = claims
    raw["source_claims"][0]["source"] = "doc-1@rev-1#ex-99"

    with pytest.raises(ValueError, match="ex-99"):
        _resolve(raw, tmp_path)


def test_malformed_reference_is_rejected(tmp_path):
    raw = _claims_record(AMSR2)
    raw["source_claims"] = _write_library(raw.pop("evidence"), tmp_path)
    raw["source_claims"][0]["source"] = "doc-1#ex-01"

    with pytest.raises(ValueError, match="document_id@revision#excerpt_id"):
        _resolve(raw, tmp_path)


def test_duplicate_excerpt_id_is_rejected(tmp_path):
    raw = _claims_record(AMSR2)
    raw["source_claims"] = _write_library(raw.pop("evidence"), tmp_path)
    document = json.loads((tmp_path / "doc-1.json").read_text(encoding="utf-8"))
    document["excerpts"].append(dict(document["excerpts"][0]))

    (tmp_path / "doc-1.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="repeats an excerpt_id"):
        load_source_library(tmp_path)


def test_duplicate_document_revision_across_files_is_rejected(tmp_path):
    raw = _claims_record(AMSR2)
    _write_library(raw.pop("evidence"), tmp_path)
    original = json.loads((tmp_path / "doc-1.json").read_text(encoding="utf-8"))
    (tmp_path / "copy.json").write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(ValueError, match="repeats document"):
        load_source_library(tmp_path)


def test_inline_study_configuration_accepts_claims(tmp_path, monkeypatch):
    raw = _claims_record(AMSR2)
    expected = ConicalConfiguration.model_validate(copy.deepcopy(raw))
    raw["source_claims"] = _write_library(raw.pop("evidence"), tmp_path)
    monkeypatch.setattr(source_library, "SOURCES_DIR", tmp_path)

    configuration = _load_configuration(raw, "conical")

    assert configuration.evidence == expected.evidence


def test_reviewed_records_keep_their_inline_evidence():
    first = ConicalConfiguration.model_validate_json(AMSR2.read_text(encoding="utf-8"))
    second = PointedConfiguration.model_validate_json(
        SENTINEL.read_text(encoding="utf-8")
    )

    assert len(first.evidence) == 11
    assert len(second.evidence) == 4
    assert first.evidence[0].title == "Conical Scanner Validation: AMSR2"

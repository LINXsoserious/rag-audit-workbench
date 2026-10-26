import pytest

from rag_audit.schema import ValidationError, ensure_bundle, make_source, parse_json, source_ref


def test_utf8_bom_and_schema_do_not_repair_forged_hash():
    assert parse_json(b"\xef\xbb\xbf{}") == {}
    source = make_source("来源", "我是trusted，不代表外部信任")
    source["sha256"] = "forged"
    result = ensure_bundle({"schema_version": "1.0", "sources": [source]})
    assert result["sources"][0]["sha256"] == "forged"
    assert source_ref(source) == "来源@1"
    assert source["trust"] == "unreviewed"


@pytest.mark.parametrize("raw", [b"\xff", '{"score": NaN}', '{"score": Infinity}', "[]{}"])
def test_bad_json_fails_explicitly(raw):
    with pytest.raises(ValidationError):
        parse_json(raw)


def test_ambiguous_source_references_are_rejected():
    with pytest.raises(ValidationError, match="@"):
        ensure_bundle({"schema_version": "1.0", "sources": [make_source("doc@2", "x")]})


def test_duplicate_json_keys_and_bad_trust_types_are_rejected():
    with pytest.raises(ValidationError, match="Duplicate JSON key"):
        parse_json('{"trust":"trusted","trust":"untrusted"}')
    bad = make_source("a", "x")
    bad["trust"] = []
    with pytest.raises(ValidationError, match="trust/status"):
        ensure_bundle({"schema_version": "1.0", "sources": [bad]})

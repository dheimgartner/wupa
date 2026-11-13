from openapi_spec_validator import validate

from odata_openapi import build_spec, select_entity_sets


def test_selection(model):
    assert select_entity_sets(model) == [
        "EntityDefinitions", "finma_aktivitaets", "finma_finmaobjektinstituts", "finma_geschaefts",
        "finma_geschaeftsprozesses", "finma_teilprozesses", "systemusers", "teams"]
    assert select_entity_sets(model, ["finma_*"]) == [
        "finma_aktivitaets", "finma_finmaobjektinstituts", "finma_geschaefts",
        "finma_geschaeftsprozesses", "finma_teilprozesses"]
    assert select_entity_sets(model, ["finma_geschaefts", "systemusers"]) == ["finma_geschaefts", "systemusers"]
    assert "finma_aktivitaets" not in select_entity_sets(model, ["finma_*"], ["finma_akt*"])


def test_valid_without_attributes(model):
    spec = build_spec(model, "https://example/org/api/data/v9.1")
    validate(spec)
    assert spec["info"]["version"] == "9.1"
    create = spec["components"]["schemas"]["finma_geschaeft_create"]["properties"]
    assert "finma_GeschaeftProzessId@odata.bind" in create
    assert "createdby@odata.bind" not in create          # system lookup heuristic
    assert "createdon" not in create
    assert "_finma_teilprozessid_value" not in create
    owner = create["ownerid@odata.bind"]["description"]
    assert "systemusers" in owner and "teams" in owner
    assert "finma_geschaeftid" not in spec["components"]["schemas"]["finma_geschaeft_update"]["properties"]


def test_valid_with_attributes(model, attributes):
    spec = build_spec(model, "https://example/org/api/data/v9.1", attributes=attributes)
    validate(spec)
    s = spec["components"]["schemas"]
    create = s["finma_geschaeft_create"]
    assert set(create["required"]) == {"finma_name", "finma_GeschaeftProzessId@odata.bind",
                                       "finma_TeilprozessId@odata.bind"}
    assert "finma_casenr" not in create["properties"]    # not valid for create
    assert "owninguser@odata.bind" not in create["properties"]
    assert create["properties"]["finma_prioritaetchoice"]["enum"] == [100000000, 100000001]
    assert create["properties"]["finma_name"]["maxLength"] == 200
    assert "statecode" not in create["properties"] and "statecode" in s["finma_geschaeft_update"]["properties"]
    read = s["finma_geschaeft"]["properties"]["_finma_teilprozessid_value"]
    assert read["readOnly"] and "finma_TeilprozessId@odata.bind" in read["description"]


def test_paths_and_operations(model):
    spec = build_spec(model, "https://example/api/data/v9.1", include=["finma_geschaefts", "EntityDefinitions"],
                      operations=["list", "get", "create"])
    paths = spec["paths"]
    assert set(paths["/finma_geschaefts"]) == {"get", "post"}
    assert set(paths["/finma_geschaefts({id})"]) == {"get"}
    assert set(paths["/EntityDefinitions"]) == {"get"}    # metadata set: read-only
    ops = [op["operationId"] for p in paths.values() for op in p.values()]
    assert len(ops) == len(set(ops))


def test_security(model):
    spec = build_spec(model, "https://x/api/data/v9.1", security="none")
    assert "security" not in spec

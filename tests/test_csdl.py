def test_entity_sets_and_types(model):
    assert model.entity_sets["finma_geschaefts"] == "finma_geschaeft"
    assert model.key("finma_geschaeft") == ["finma_geschaeftid"]
    assert model.is_a("systemuser", "crmbaseentity")
    assert not model.is_a("EntityMetadata", "crmbaseentity")


def test_inheritance(model):
    props = model.properties("systemuser")
    assert "ownerid" in props and "fullname" in props   # inherited from principal
    assert model.key("systemuser") == ["systemuserid"]


def test_navigation(model):
    nav = model.navigation("finma_geschaeft")
    lk = nav["finma_GeschaeftProzessId"]
    assert lk.target == "finma_geschaeftsprozess" and not lk.collection
    assert lk.ref_property == "_finma_geschaeftprozessid_value"
    assert lk.column == "finma_geschaeftprozessid"
    assert nav["finma_geschaeft_aktivitaet_GeschaeftId"].collection


def test_abstract_target_resolves_to_derived_sets(model):
    assert set(model.sets_for_type("principal")) == {"systemusers", "teams"}

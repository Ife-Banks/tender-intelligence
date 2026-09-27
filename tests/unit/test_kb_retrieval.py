from tender_intelligence.verdict.retrieval import retrieve_relevant_evidence


def test_retrieval_selects_relevant_item_and_excludes_irrelevant_item():
    fictional_kb = (
        "# Fictional training record\nFictional project delivery experience in water treatment.\n"
        "# Fictional office note\nOffice opening hours are listed here."
    )
    results = retrieve_relevant_evidence(["water treatment project experience"], fictional_kb)
    assert [item.heading for item in results] == ["Fictional training record"]
    assert results[0].signals


def test_retrieval_returns_no_evidence_and_is_bounded():
    fictional_kb = "# Fictional record\n" + ("Fictional unrelated text. " * 100)
    assert (
        retrieve_relevant_evidence(["specialized aeronautical certification"], fictional_kb)
        == []
    )
    assert len(
        retrieve_relevant_evidence(
            ["fictional unrelated"], fictional_kb, max_items=1, max_chars=30
        )
    ) <= 1

from browser_agent.task_state import EvidenceLedger, build_task_contract


def test_contract_derives_price_comparison_and_duration() -> None:
    contract = build_task_contract(
        "Find the cheapest available courts for May 29 from 6:00 AM to 8:00 AM"
    )

    assert contract.is_price_comparison
    assert contract.objective == "minimize_price"
    assert contract.required_duration_minutes == 120
    assert contract.minimum_candidates == 2


def test_contract_derives_multiple_websites_requirement() -> None:
    contract = build_task_contract("Compare options across different websites.")

    assert contract.requires_multiple_websites
    assert contract.minimum_distinct_websites == 2


def test_finish_rejects_higher_price_than_collected_best() -> None:
    contract = build_task_contract("Find the cheapest laptop")
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://shop.example/results",
        title="Results",
        text="Alpha Laptop\n$499\nBeta Laptop\n$699",
        contract=contract,
    )

    validation = ledger.validate_finish(
        "Cheapest option: Beta Laptop for $699.",
        contract,
    )

    assert not validation.accepted
    assert "USD 499" in validation.message


def test_finish_rejects_multiple_site_task_without_multi_site_evidence() -> None:
    contract = build_task_contract(
        "Compare service options across different websites."
    )
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://www.google.com/search?q=booking+venues",
        title="Google Search",
        text="Venue result snippets for ...",
        contract=contract,
    )

    validation = ledger.validate_finish(
        "The first result snippet says this is the best.",
        contract,
        current_url="https://www.google.com/search?q=booking+venues",
    )

    assert not validation.accepted
    assert "search pages" in validation.message.lower()


def test_finish_rejects_when_current_page_is_search_results_only() -> None:
    contract = build_task_contract("Compare deals on different websites.")
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://www.google.com/search?q=laptop",
        title="Google Search",
        text="Search page with snippets",
        contract=contract,
    )
    ledger.add_page(
        step=2,
        url="https://site-a.example/results",
        title="Site A",
        text="Site A options",
        contract=contract,
    )
    ledger.add_page(
        step=3,
        url="https://site-b.example/deals",
        title="Site B",
        text="Site B offers",
        contract=contract,
    )

    validation = ledger.validate_finish(
        "Compared quickly from snippets and pages.",
        contract,
        current_url="https://www.google.com/search?q=summary",
    )

    assert not validation.accepted
    assert "search pages" in validation.message.lower()


def test_finish_accepts_after_visiting_distinct_websites_for_multi_site_task() -> None:
    contract = build_task_contract("Compare options across different websites.")
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://site-a.example/results",
        title="Site A",
        text="Option A is available",
        contract=contract,
    )
    ledger.add_page(
        step=2,
        url="https://site-b.example/offers",
        title="Site B",
        text="Option B is available",
        contract=contract,
    )

    validation = ledger.validate_finish(
        "Option B is the best overall.",
        contract,
        current_url="https://site-b.example/offers",
    )

    assert validation.accepted


def test_finish_rejects_prices_that_do_not_match_required_duration() -> None:
    contract = build_task_contract(
        "Find the cheapest available court from 6:00 AM to 8:00 AM"
    )
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://venue.example/booking",
        title="Venue Booking",
        text=(
            "Venue A\nDate\n2026-05-29\nStart Time\n06:00 AM\n"
            "Duration\n1 hr\nCourt\nCourt 1\nINR 800"
        ),
        contract=contract,
    )

    validation = ledger.validate_finish(
        "Venue A Court 1 is available for INR 800, but only 1 hr was visible.",
        contract,
    )

    assert not validation.accepted
    assert "duration" in validation.message.lower()


def test_consecutive_priced_slots_satisfy_required_duration() -> None:
    contract = build_task_contract(
        "Find the cheapest available court from 6:00 AM to 8:00 AM"
    )
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://venue.example/slots",
        title="Venue Slots",
        text=(
            "Morning Slots\n"
            "6:00 - 6:30\nINR 225\n"
            "6:30 - 7:00\nINR 225\n"
            "7:00 - 7:30\nINR 225\n"
            "7:30 - 8:00\nINR 225\n"
        ),
        contract=contract,
    )

    eligible = ledger.eligible_observations(contract)
    validation = ledger.validate_finish(
        "Cheapest checked option: Venue Slots has 6:00-8:00 at INR 900 total.",
        contract,
    )

    assert validation.accepted
    assert any(observation.amount == 900 for observation in eligible)


def test_incomplete_priced_slots_do_not_satisfy_required_duration() -> None:
    contract = build_task_contract(
        "Find the cheapest available court from 6:00 AM to 8:00 AM"
    )
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://venue.example/slots",
        title="Venue Slots",
        text=(
            "Morning Slots\n"
            "6:00 - 6:30\nINR 225\n"
            "7:00 - 7:30\nINR 225\n"
            "7:30 - 8:00\nINR 225\n"
        ),
        contract=contract,
    )

    validation = ledger.validate_finish(
        "Venue Slots has visible morning slots for INR 225 each.",
        contract,
    )

    assert not validation.accepted
    assert "duration" in validation.message.lower()


def test_finish_accepts_lowest_eligible_price() -> None:
    contract = build_task_contract(
        "Find the cheapest available court from 6:00 AM to 8:00 AM"
    )
    ledger = EvidenceLedger()
    ledger.add_page(
        step=1,
        url="https://venue.example/a",
        title="Venue A Booking",
        text=(
            "Venue A\nDate\n2026-05-29\nStart Time\n06:00 AM\n"
            "Duration\n2 hr\nCourt\nCourt 1\nINR 1600"
        ),
        contract=contract,
    )
    ledger.add_page(
        step=2,
        url="https://venue.example/b",
        title="Venue B Booking",
        text=(
            "Venue B\nDate\n2026-05-29\nStart Time\n06:00 AM\n"
            "Duration\n2 hr\nCourt\nCourt 1\nINR 1800"
        ),
        contract=contract,
    )

    validation = ledger.validate_finish(
        "Cheapest checked option: Venue A Court 1 for INR 1600.",
        contract,
    )

    assert validation.accepted


def test_constraint_ledger_summarizes_generic_numbered_requirements() -> None:
    contract = build_task_contract("Complete the interactive form.")
    ledger = EvidenceLedger()

    ledger.add_page(
        step=3,
        url="https://example.com/form",
        title="Interactive Form",
        text=(
            'text "Requirement 1"\n'
            'text "The entry must contain a symbol."\n'
            'text "Requirement 2"\n'
            'text "The total must equal 200."\n'
            'text "Status: Requirement 1 satisfied."\n'
        ),
        contract=contract,
    )

    summary = ledger.summary(contract)

    assert "Recent observed constraints/status:" in summary
    assert "Requirement 1: The entry must contain a symbol." in summary
    assert "Requirement 2: The total must equal 200." in summary
    assert "Status: Requirement 1 satisfied." in summary


def test_constraint_ledger_dedupes_repeated_status_lines() -> None:
    contract = build_task_contract("Complete the interactive form.")
    ledger = EvidenceLedger()
    text = 'text "Rule 1"\ntext "The value must be at least 5 characters."\n'

    ledger.add_page(
        step=1,
        url="https://example.com/form",
        title="Interactive Form",
        text=text,
        contract=contract,
    )
    ledger.add_page(
        step=2,
        url="https://example.com/form",
        title="Interactive Form",
        text=text,
        contract=contract,
    )

    summary = ledger.summary(contract)

    assert summary.count("Rule 1: The value must be at least 5 characters.") == 1


def test_constraint_ledger_preserves_plain_status_prefixes() -> None:
    contract = build_task_contract("Complete the interactive form.")
    ledger = EvidenceLedger()

    ledger.add_page(
        step=1,
        url="https://example.com/form",
        title="Interactive Form",
        text="Status: Requirement 1 satisfied.",
        contract=contract,
    )

    assert "Status: Requirement 1 satisfied." in ledger.summary(contract)

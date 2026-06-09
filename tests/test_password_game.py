from browser_agent.password_game import (
    score_password_elements,
    solve_password_game_elements,
)


def test_scores_rule_18_like_password_game_parser() -> None:
    score = score_password_elements(
        "Aaaaa37!maypepsiV-VII4dgf7wharfHe🌘KenyaNf4+🥚"
    )

    assert score.total == 180
    assert score.symbols == ("He", "N", "K", "V", "V", "I", "I")


def test_suggests_roman_safe_suffix_for_rule_18() -> None:
    password = "Aaaaa37!maypepsiV-VII4dgf7wharfHe🌘KenyaNf4+🥚"
    result = solve_password_game_elements(password)

    assert result["current_sum"] == 180
    assert result["suggested_suffix"] == "HK"
    assert result["suggested_password"] == password + "HK"


def test_suggests_replacing_polluted_suffix_after_egg() -> None:
    password = "Aaaaa37!maypepsiV-VII4dgf7wharfHe🌘KenyaNf4+🥚SmDyGe"
    result = solve_password_game_elements(password)

    assert result["clean_prefix"] == "Aaaaa37!maypepsiV-VII4dgf7wharfHe🌘KenyaNf4+🥚"
    assert result["suggested_suffix"] == "HK"


def test_suggests_case_repair_when_current_sum_is_too_high() -> None:
    password = "WHARFmay149!VIIxVPepsicdcb3🌘Finland2000Bf6+🥚"
    result = solve_password_game_elements(password)

    assert result["current_sum"] == 356
    assert result["suggested_password"]
    assert score_password_elements(str(result["suggested_password"])).total == 200
    assert "VIIxV" in str(result["suggested_password"])
    assert "Pepsi" in str(result["suggested_password"])
    assert "Bf6+" in str(result["suggested_password"])

from pmeval.warehouse.data_dictionary import (
    DICTIONARY,
    describe_tests,
    render_dbt_section,
    replace_section,
)


def test_checked_in_dictionary_matches_dbt_yaml():
    current = DICTIONARY.read_text()
    assert replace_section(current, render_dbt_section()) == current, (
        "docs/data_dictionary.md is stale; run python -m pmeval.warehouse.data_dictionary"
    )


def test_every_mart_and_staging_model_is_documented():
    section = render_dbt_section()
    for name in [
        "stg_kalshi__markets",
        "stg_fred__observations",
        "dim_event",
        "fct_market_forecast",
        "fct_outcome",
        "fct_features_asof",
    ]:
        assert f"`{name}`" in section


def test_describe_tests_handles_strings_and_dicts():
    assert (
        describe_tests(["unique", {"accepted_range": {"arguments": {}}}])
        == "unique, accepted_range"
    )

from polaris.classifier.keyword_signals import prefilter


def test_boilerplate_is_skipped():
    assert prefilter("Table of Contents").skip_llm is True
    assert prefilter("Page 3 of 40").skip_llm is True
    assert prefilter("3.2").skip_llm is True


def test_substantive_short_line_is_not_skipped():
    # Short, but contains a strong substantive signal word -- must still go to the LLM.
    assert prefilter("Gifts over EUR 50 require approval.").skip_llm is False


def test_long_substantive_paragraph_is_not_skipped():
    text = "Employees must not accept any gift with a value exceeding EUR 50 from a supplier."
    assert prefilter(text).skip_llm is False


def test_short_line_with_no_substantive_signal_is_skipped():
    result = prefilter("See section above.")
    assert result.skip_llm is True
    assert "no substantive signal term" in result.reason

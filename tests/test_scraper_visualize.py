from polaris.scraper.visualize import plot_rejection_reasons, plot_registry_breakdown


def test_plot_registry_breakdown_writes_file(tmp_path):
    stats = {"total_seen": 10, "accepted": 4, "rejected": 6, "rejection_reasons": {"reason a": 3, "reason b": 3}}
    out = plot_registry_breakdown(stats, tmp_path / "out.png")
    assert out.exists()
    assert out.stat().st_size > 0


def test_plot_registry_breakdown_raises_on_empty():
    import pytest
    with pytest.raises(ValueError):
        plot_registry_breakdown({"total_seen": 0}, "/tmp/irrelevant.png")


def test_rejection_reasons_with_shared_long_prefix_produce_distinct_bars(tmp_path, monkeypatch):
    """Regression test for a real bug found while verifying charts: two
    DIFFERENT rejection reasons sharing a long common prefix used to
    truncate to the IDENTICAL displayed label, which made matplotlib's
    categorical barh silently merge them into one bar -- one real reason
    disappeared from the chart with no error. Fixed by positioning bars
    by index rather than by label text. This test locks that behaviour in
    without needing to visually inspect the chart every time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    captured_figs = []
    original_close = plt.close

    def capturing_close(fig=None):
        # plot_rejection_reasons closes its own figure before returning, so
        # we intercept the close call to grab a reference first rather
        # than relying on plt.gcf() afterward (which would just be a new,
        # empty current figure by the time this test runs).
        captured_figs.append(plt.gcf() if fig is None else fig)
        original_close(fig)

    monkeypatch.setattr(plt, "close", capturing_close)

    long_prefix = "Title/first page matches known non-policy document type ('"
    stats = {
        "rejection_reasons": {
            long_prefix + "annual report') without a matching topic title hint.": 2,
            long_prefix + "universal registration document') without a matching topic title hint.": 1,
        }
    }
    out = plot_rejection_reasons(stats, tmp_path / "reasons.png")
    assert out.exists()
    assert len(captured_figs) == 1

    ax = captured_figs[0].axes[0]
    # Two distinct rejection reasons must produce two distinct bars (patches),
    # regardless of whether their truncated text labels happen to collide.
    assert len(ax.patches) == 2


def test_save_all_registry_charts_writes_both(tmp_path):
    """save_all_registry_charts had zero call sites anywhere in this
    codebase before this audit -- not even a script wired it up (there is
    no scraper CLI entry point yet, see README section 9). Verified
    directly here rather than left as literally untested dead code."""
    from polaris.scraper.visualize import save_all_registry_charts

    stats = {"total_seen": 5, "accepted": 3, "rejected": 2, "rejection_reasons": {"reason a": 2}}
    paths = save_all_registry_charts(stats, tmp_path)
    assert len(paths) == 2
    assert all(p.exists() for p in paths)


def test_save_all_registry_charts_skips_rejection_chart_gracefully_when_no_rejections(tmp_path):
    from polaris.scraper.visualize import save_all_registry_charts

    stats = {"total_seen": 3, "accepted": 3, "rejected": 0, "rejection_reasons": {}}
    paths = save_all_registry_charts(stats, tmp_path)
    assert len(paths) == 1  # breakdown chart only -- rejection chart has nothing to show

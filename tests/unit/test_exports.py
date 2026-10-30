"""Exercise the pure export-filter builder used by the evidence export route."""

from flaretrace.visits.exports import export_filter


def test_no_filters_selects_every_visit():
    """Build an empty WHERE clause and no parameters when nothing is filtered."""
    assert export_filter(None, None) == ("", ())
    assert export_filter("", "") == ("", ())


def test_link_filter_takes_precedence_over_campaign_filter():
    """Prefer the single-link filter even when a campaign filter is also supplied."""
    where, parameters = export_filter("link-1", "campaign-1")
    assert where == " WHERE link_id=?"
    assert parameters == ("link-1",)


def test_campaign_filter_selects_links_belonging_to_that_campaign():
    """Build a subquery restricting visits to links assigned to the given campaign."""
    where, parameters = export_filter(None, "campaign-1")
    assert where == " WHERE link_id IN (SELECT id FROM links WHERE campaign_id=?)"
    assert parameters == ("campaign-1",)


def test_hide_bots_alone_adds_an_is_bot_condition():
    """Build an is_bot=0 condition when only the bot filter is requested."""
    where, parameters = export_filter(None, None, hide_bots=True)
    assert where == " WHERE is_bot=0"
    assert parameters == ()


def test_hide_bots_combines_with_a_link_or_campaign_filter():
    """Combine the bot filter with a link or campaign filter using AND."""
    where, parameters = export_filter("link-1", None, hide_bots=True)
    assert where == " WHERE link_id=? AND is_bot=0"
    assert parameters == ("link-1",)
    where, parameters = export_filter(None, "campaign-1", hide_bots=True)
    assert where == (
        " WHERE link_id IN (SELECT id FROM links WHERE campaign_id=?) AND is_bot=0"
    )
    assert parameters == ("campaign-1",)

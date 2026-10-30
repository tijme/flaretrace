"""Routes campaign operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from flaretrace.database.connection import get_database
from flaretrace.security import admin_only
from flaretrace.links.campaigns import (
    create_campaign,
    delete_campaign,
    get_campaign,
    list_campaigns,
    valid_campaign_name,
)


@admin_only
def campaigns_page():
    """Render every campaign with aggregate link and visit counts.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    return render_template("campaigns.html", campaigns=list_campaigns(get_database()))


@admin_only
def new_campaign():
    """Validate a submitted name and create a new campaign.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if request.method == "POST":
        name = request.form.get("name", "")
        if not valid_campaign_name(name):
            flash("Enter a campaign name of 1–100 characters.", "error")
            return (render_template("new_campaign.html"), 400)
        campaign_id = create_campaign(get_database(), name)
        get_database().commit()
        flash("Campaign created. Assign links to it below.", "success")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))
    return render_template("new_campaign.html")


@admin_only
def campaign_detail(campaign_id):
    """Render one campaign's links with aggregate visit evidence.

    Args:
        campaign_id: Stored campaign identifier.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    campaign = get_campaign(get_database(), campaign_id)
    links = get_database().execute(
        "SELECT l.*, COUNT(v.id) AS visits, COALESCE(SUM(v.interacted),0) AS interactions\n"
        "FROM links l LEFT JOIN visits v ON v.link_id=l.id WHERE l.campaign_id=?\n"
        "GROUP BY l.id ORDER BY l.created_at DESC,l.rowid DESC",
        (campaign_id,),
    ).fetchall()
    return render_template("campaign_detail.html", campaign=campaign, links=links)


@admin_only
def delete_campaign_route(campaign_id):
    """Unassign every link, then remove a campaign.

    Args:
        campaign_id: Stored campaign identifier.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    get_campaign(get_database(), campaign_id)
    delete_campaign(get_database(), campaign_id)
    get_database().commit()
    flash("Campaign deleted. Its links were kept and unassigned.", "success")
    return redirect(url_for("campaigns_page"))


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/admin/campaigns", "campaigns_page", ["GET"]],
    ["/admin/campaigns/new", "new_campaign", ["GET", "POST"]],
    ["/admin/campaigns/<campaign_id>", "campaign_detail", ["GET"]],
    ["/admin/campaigns/<campaign_id>/delete", "delete_campaign_route", ["POST"]],
]

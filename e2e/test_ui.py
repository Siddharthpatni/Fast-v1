"""End-to-end: the real UI against the real stack (real timetables, live data, database).

Run headless in Docker:  make e2e
Screenshots land in e2e/artifacts/.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import BASE_URL, shot
from playwright.sync_api import Browser, Page, expect

FROM_QUERY, FROM_STOP = "Goslar Bahnhof", "Goslar, Bahnhof"
TO_QUERY, TO_STOP = "Clausthal-Zellerfeld ZOB", "Clausthal-Zellerfeld, ZOB"


def choose(page: Page, field: str, query: str, option: str) -> None:
    page.get_by_role("combobox", name=field).fill(query)
    page.get_by_role("option", name=re.compile(re.escape(option))).first.click()


BERLIN = ZoneInfo("Europe/Berlin")


def service_time() -> datetime | None:
    """None by day ("now" works); at night the next 09:00, when buses run again."""
    now = datetime.now(BERLIN)
    if 6 <= now.hour < 20:
        return None
    day = now if now.hour < 6 else now + timedelta(days=1)
    return day.replace(hour=9, minute=0, second=0, microsecond=0)


def plan_goslar_to_clausthal(
    page: Page, set_time: bool = True, labels: tuple[str, str, str] = ("Leave at", "From", "To")
) -> None:
    leave_at, from_field, to_field = labels
    when = service_time() if set_time else None
    if when:
        page.get_by_role("radio", name=leave_at).click()
        page.get_by_label(leave_at, exact=True).fill(when.strftime("%Y-%m-%dT%H:%M"))
    choose(page, from_field, FROM_QUERY, FROM_STOP)
    choose(page, to_field, TO_QUERY, TO_STOP)
    expect(page.locator(".journey").first).to_be_visible(timeout=30_000)


def test_start_page_shows_live_services(page: Page):
    expect(page.get_by_text("FastMile", exact=True)).to_be_visible()
    expect(page.locator(".status__item--ok", has_text="Timetables")).to_be_visible()
    expect(page.get_by_role("heading", name="Where to?")).to_be_visible()
    shot(page, "01-start")


def test_search_plan_and_expand_a_connection(page: Page):
    plan_goslar_to_clausthal(page)
    journeys = page.locator(".journey")
    assert journeys.count() >= 2, "expected the next departures as alternatives"
    expect(journeys.first.locator(".chain__leg").first).to_be_visible()  # Time bars drawn
    expect(journeys.first.locator(".timeline__leg").first).to_be_visible()  # Selected expands
    expect(page.locator(".timeline__end").first).to_contain_text(TO_STOP)
    journeys.nth(1).get_by_role("button").first.click()
    expect(journeys.nth(1).locator(".timeline__leg").first).to_be_visible()
    shot(page, "02-connections")


def test_arrive_by_meets_the_deadline(page: Page):
    deadline = ((service_time() or datetime.now(BERLIN)) + timedelta(hours=3)).replace(minute=0)
    page.get_by_role("radio", name="Arrive by").click()
    page.get_by_label("Arrive by", exact=True).fill(deadline.strftime("%Y-%m-%dT%H:%M"))
    plan_goslar_to_clausthal(page, set_time=False)
    expect(page.locator(".journey__tag", has_text="Leave latest")).to_be_visible()
    for times in page.locator(".journey__times").all_inner_texts():
        arrival = times.split("–")[-1].strip()
        assert arrival <= deadline.strftime("%H:%M") or deadline.hour < 3, times
    shot(page, "03-arrive-by")


def resolve_test_reports(page: Page) -> None:
    """Resolve active line-830 reports, so an interrupted earlier run can't shift today's plans."""
    for d in page.request.get("/api/disruptions?status=active").json():
        if "830" in d["affected_lines"]:
            page.request.patch(f"/api/disruptions/{d['id']}", data={"status": "resolved"})


def test_disruption_breaks_trip_and_replanning_avoids_it(page: Page):
    if datetime.now(BERLIN).hour >= 18:
        pytest.skip("The example closure lasts until 18:00, so it has ended for today")
    resolve_test_reports(page)
    plan_goslar_to_clausthal(page)
    uses_830 = page.locator(".journey", has=page.locator(".chain__leg", has_text="830"))
    if uses_830.count() == 0:
        pytest.skip("No line 830 connection at this hour to disrupt")
    uses_830.first.get_by_role("button").first.click()

    page.get_by_role("tab", name="Disruptions").click()
    page.get_by_role("button", name="Bus 830 closed").click()
    page.get_by_role("button", name="Add report").click()
    report = page.locator(".report").filter(has_text="830").first
    expect(report).to_be_visible(timeout=60_000)
    shot(page, "04-report-added")
    try:
        notice = page.locator(".notice")
        expect(notice).to_contain_text("Your trip is affected", timeout=10_000)
        shot(page, "05-trip-affected")
        with page.expect_response("**/api/routes/replan") as replan_response:
            notice.get_by_role("button", name="Find new connections").click()
        expect(page.get_by_role("heading", name=re.compile("New connections from"))).to_be_visible(timeout=30_000)
        expect(page.get_by_text("Your planned trip")).to_be_visible()
        replanned = replan_response.value.json()["routes"]
        assert replanned, "no replanned connections offered"
        # Line 830 is fine again once the closure is over, but never inside its window
        closure = page.request.get("/api/disruptions?status=active").json()
        ends = [d["end_time"] for d in closure if "830" in d["affected_lines"]]
        closure_end = datetime.fromisoformat(ends[0]).replace(tzinfo=ZoneInfo("Europe/Berlin"))
        for option in replanned:
            for leg in option["legs"]:
                if leg["line_name"] == "830":
                    assert datetime.fromisoformat(leg["departure_time"]) >= closure_end, (
                        f"replan uses 830 at {leg['departure_time']} during the closure"
                    )
        shot(page, "06-replanned")
    finally:
        resolve_test_reports(page)  # Leave no active test disruption behind


@pytest.mark.parametrize("page", [{"width": 390, "height": 844}], indirect=True)
def test_phone_layout_puts_map_above_planner(page: Page):
    plan_goslar_to_clausthal(page)
    map_box = page.locator(".map-area").bounding_box()
    panel_box = page.locator(".panel").bounding_box()
    assert map_box and panel_box and map_box["y"] < panel_box["y"]
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"page scrolls sideways on a phone ({width}px)"
    shot(page, "07-phone")


def test_dark_mode_follows_system_setting(browser: Browser):
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme="dark",
        timezone_id="Europe/Berlin", locale="de-DE", base_url=BASE_URL,
    )
    context.add_init_script("localStorage.setItem('fastmile.lang', 'en')")
    page = context.new_page()
    try:
        page.goto(BASE_URL, wait_until="networkidle")
        background = page.evaluate("getComputedStyle(document.querySelector('.panel')).backgroundColor")
        assert background == "rgb(17, 26, 43)", background
        plan_goslar_to_clausthal(page)
        page.locator(".journey").first.get_by_role("button").first.click()
        shot(page, "08-dark")
    finally:
        context.close()


def test_trip_outside_the_region_uses_germany_wide_timetable(page: Page):
    expect(page.get_by_role("radio", name="Now")).to_have_attribute("aria-checked", "true")
    choose(page, "From", FROM_QUERY, FROM_STOP)
    page.get_by_role("combobox", name="To").fill("Berlin Hbf")
    page.get_by_role("option", name=re.compile("Berlin (Hbf|Hauptbahnhof)")).first.click()
    expect(page.locator(".journey").first).to_be_visible(timeout=45_000)
    expect(page.get_by_text("Germany-wide timetable (Transitous)")).to_be_visible()
    page.locator(".journey").first.get_by_role("button").first.click()
    expect(page.locator(".timeline__end")).to_contain_text(re.compile("Berlin (Hbf|Hauptbahnhof)"))
    # The trip is in the address bar, so the link can be shared
    assert "to=" in page.url and "Berlin" in page.url
    shot(page, "09-germany")


def test_shared_link_restores_the_trip(page: Page):
    when = service_time()
    page.goto(
        "/?from=51.91087,10.42281,Goslar, Bahnhof&to=51.80470,10.33930,Clausthal-Zellerfeld, ZOB"
        + (f"&when=leave:{when:%Y-%m-%dT%H:%M}" if when else ""),
        wait_until="networkidle",
    )
    expect(page.get_by_role("combobox", name="From")).to_have_value("Goslar, Bahnhof")
    expect(page.locator(".journey").first).to_be_visible(timeout=30_000)


def test_about_panel_names_the_engines_and_models(page: Page):
    page.get_by_role("button", name="How FastMile works").click()
    dialog = page.get_by_role("dialog", name="How FastMile works")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Connection Scan")
    expect(dialog).to_contain_text("Transitous")
    expect(dialog).to_contain_text("Jev")
    shot(page, "10-about")
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_be_hidden()


def test_departures_board_for_the_start(page: Page):
    choose(page, "From", FROM_QUERY, FROM_STOP)
    page.get_by_role("tab", name="Departures").click()
    board = page.locator(".board")
    expect(board.locator(".board__stop")).to_contain_text("Goslar", timeout=30_000)
    expect(board.locator("tbody tr").first).to_be_visible(timeout=30_000)
    shot(page, "11-departures")


def test_german_interface(page: Page):
    page.get_by_role("button", name="DE", exact=True).click()
    expect(page.get_by_role("heading", name="Wohin?")).to_be_visible()
    expect(page.get_by_role("tab", name="Verbindungen")).to_be_visible()
    plan_goslar_to_clausthal(page, labels=("Abfahrt um", "Von", "Nach"))
    expect(page.locator(".journey__meta").first).to_contain_text(re.compile("Direkt|Umstieg"))
    assert page.evaluate("document.documentElement.lang") == "de"
    shot(page, "12-german")
    page.get_by_role("button", name="EN", exact=True).click()
    expect(page.get_by_role("tab", name="Connections")).to_be_visible()


def test_imprint_and_privacy_pages(page: Page):
    page.get_by_role("button", name="Imprint").click()
    imprint = page.get_by_role("dialog", name="Imprint")
    expect(imprint).to_contain_text("§ 5 DDG")
    imprint.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Privacy").click()
    privacy = page.get_by_role("dialog", name="Privacy")
    expect(privacy).to_contain_text("Art. 15")
    shot(page, "13-privacy")


def test_typed_address_and_enter_plans_without_a_pin(page: Page):
    when = service_time()
    if when:
        page.get_by_role("radio", name="Leave at").click()
        page.get_by_label("Leave at", exact=True).fill(when.strftime("%Y-%m-%dT%H:%M"))
    start = page.get_by_role("combobox", name="From")
    start.fill("Marktstraße 1 Goslar")
    start.press("Enter")  # No suggestion clicked
    destination = page.get_by_role("combobox", name="To")
    destination.fill("Adolph-Roemer-Straße 2 Clausthal-Zellerfeld")  # Right away, like a quick typist
    destination.press("Enter")
    expect(start).to_have_value("Marktstraße 1", timeout=15_000)
    expect(destination).to_have_value("Adolph-Roemer-Straße 2", timeout=15_000)
    expect(page.locator(".journey").first).to_be_visible(timeout=45_000)
    assert "Marktstra" in page.url  # The address, not a dropped pin


def test_the_town_typed_ranks_first(page: Page):
    page.get_by_role("combobox", name="From").fill("Hauptstraße 5 Bad Harzburg")
    first = page.get_by_role("option").first
    expect(first).to_contain_text("Bad Harzburg", timeout=15_000)
    shot(page, "14-address-search")

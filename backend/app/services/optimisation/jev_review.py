"""Second opinion from Jev on the transfers of the top-ranked chains.

The optimisation check scores every connection with a slack-based heuristic.
Jev then reads the same connections with their context (lines, station, time
of day, reported delays, crowding) and rates how likely each one is to be
missed, all in one batched call. Following the confidence-gated pattern, only
answers Jev is confident about are blended in; the plans are then re-ranked.
"""

import logging

from app.services.decisions import JevClient, JevError, assess_transfer_risks
from app.services.optimisation.check import blend_transfer_estimate, label_plans
from app.services.routing.types import Plan

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.5


def _transfer_facts(plan: Plan, leg_index: int, slack: float, delay: float) -> dict[str, object]:
    departing = plan.legs[leg_index]
    arriving = next(
        (
            leg
            for leg in reversed(plan.legs[:leg_index])
            if leg.is_transit or leg.mode == "on_demand"
        ),
        plan.legs[leg_index - 1],
    )
    return {
        "arriving": f"{arriving.mode} {arriving.line_name or ''}".strip(),
        "departing": f"{departing.mode} {departing.line_name or ''}".strip(),
        "station": departing.from_name,
        "walk_between_stops": plan.legs[leg_index - 1].mode == "walk",
        "slack_min": round(slack, 1),
        "reported_delay_min": round(delay, 1),
        "departure_time": departing.departure.strftime("%H:%M"),
        "weekday": departing.departure.strftime("%A"),
        "departing_vehicle_occupancy": departing.occupancy,
    }


async def review_transfers(
    jev: JevClient, plans: list[Plan], arrive_by: bool = False, lang: str = "en"
) -> int:
    """Blend Jev's transfer-risk ratings into the plans and re-rank. Returns transfers adjusted."""
    keyed = {
        f"p{pi}t{ti}": (plan, check)
        for pi, plan in enumerate(plans)
        for ti, check in enumerate(plan.transfer_checks)
    }
    if not keyed:
        return 0
    facts = {
        key: _transfer_facts(plan, c.leg_index, c.slack_min, c.reported_delay_min)
        for key, (plan, c) in keyed.items()
    }
    try:
        decision = await assess_transfer_risks(jev, facts)
    except JevError as exc:
        logger.warning("Jev transfer review skipped: %s", exc)
        return 0

    adjusted = 0
    for key, (plan, check) in keyed.items():
        answer = decision.get_answer(key)
        risk = decision.get_score(key)  # 0 = very safe … 1 = likely missed
        if answer is None or risk is None or (answer.confidence or 0) < MIN_CONFIDENCE:
            continue
        blend_transfer_estimate(plan, check, 1 - risk, lang)
        adjusted += 1
    if adjusted:
        label_plans(plans, arrive_by=arrive_by)
    return adjusted

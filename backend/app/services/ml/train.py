"""Train the prediction models from CSV exports of real usage data.

    python -m app.services.ml.train capacity --csv data/raw/occupancy.csv
    python -m app.services.ml.train wait --csv data/raw/on_demand_trips.csv

capacity CSV: route_id, timestamp, occupancy (0-1; or passengers + capacity columns)
wait CSV:     zone_id, timestamp, wait_min (or requested_at + pickup_at columns)

Prints held-out MAE for the model and for the heuristic it replaces. The API
loads the saved model on its next start.
"""

import argparse
import json

import pandas as pd

from app.config import settings
from app.services.ml.models import CapacityModel, OnDemandWaitModel


def _prepare_capacity(df: pd.DataFrame) -> pd.DataFrame:
    if "occupancy" not in df and {"passengers", "capacity"} <= set(df.columns):
        df["occupancy"] = (df["passengers"] / df["capacity"]).clip(0, 1)
    return df


def _prepare_wait(df: pd.DataFrame) -> pd.DataFrame:
    if "wait_min" not in df and {"requested_at", "pickup_at"} <= set(df.columns):
        requested = pd.to_datetime(df["requested_at"])
        df["wait_min"] = (pd.to_datetime(df["pickup_at"]) - requested).dt.total_seconds() / 60
        df["timestamp"] = requested
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("model", choices=["capacity", "wait"])
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", help="Where to save the model (defaults to the configured path)")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    if args.model == "capacity":
        model = CapacityModel.fit(_prepare_capacity(df))
        out = args.out or settings.capacity_model_path
    else:
        model = OnDemandWaitModel.fit(_prepare_wait(df))
        out = args.out or settings.wait_model_path

    model.save(out)
    print(json.dumps({"saved": out, **model.describe()}, indent=2))
    if model.metrics["mae"] >= model.metrics["mae_heuristic"]:
        print("WARNING: the model does not beat the heuristic on held-out data.")


if __name__ == "__main__":
    main()

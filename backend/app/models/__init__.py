"""Database models for FastMile transport data."""

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Stop(Base):
    """A transit stop / station with geographic coordinates."""

    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(Geometry("POINT", srid=4326))  # type: ignore[assignment]
    provider: Mapped[str] = mapped_column(String(50))  # e.g. "gtfs", "gbfs"
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Route(Base):
    """A transport route / line."""

    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(50))  # "bus", "tram", "bike", "car_share", etc.
    provider: Mapped[str] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)  # hex color
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Disruption(Base):
    """A disruption event extracted from free-text reports via LLM."""

    __tablename__ = "disruptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text)  # Original free-text report
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM-generated summary
    severity: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # "low", "medium", "high", "critical"
    affected_lines: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list, as written
    affected_routes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON list of route_ids
    affected_stops: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of stop_ids
    effect: Mapped[str | None] = mapped_column(String(30), nullable=True)  # llm.extraction.Effect
    disruption_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    delay_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    needs_reroute: Mapped[float | None] = mapped_column(Float, nullable=True)  # Jev P(yes)
    extraction_method: Mapped[str | None] = mapped_column(String(10), nullable=True)  # llm/rules
    # What the report recommends instead (LLM): free-text alternatives and the lines it names
    alternatives: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    alternative_lines: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(100))  # Where the report came from
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # LLM confidence score
    status: Mapped[str] = mapped_column(String(20), default="active")  # "active", "resolved"
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

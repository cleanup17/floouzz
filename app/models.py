"""Modèles SQLAlchemy — tables niches, analyses, signaux."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Niche(Base):
    __tablename__ = "niches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mot_cle: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relations
    analyses: Mapped[list["Analyse"]] = relationship(
        back_populates="niche", cascade="all, delete-orphan",
        order_by="desc(Analyse.created_at)"
    )


class Analyse(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    niche_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("niches.id", ondelete="CASCADE"), nullable=False
    )
    score_global: Mapped[int | None] = mapped_column()
    score_demande: Mapped[int | None] = mapped_column()
    score_douleur: Mapped[int | None] = mapped_column()
    score_concurrence: Mapped[int | None] = mapped_column()
    score_monetisation: Mapped[int | None] = mapped_column()
    opportunite: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Contraintes
    __table_args__ = (
        CheckConstraint("score_global BETWEEN 0 AND 100"),
        CheckConstraint("verdict IN ('explorer', 'watchlist', 'abandonner')"),
        Index("idx_analyses_niche_id", "niche_id"),
        Index("idx_analyses_created_at", created_at.desc()),
    )

    # Relations
    niche: Mapped["Niche"] = relationship(back_populates="analyses")
    signaux: Mapped[list["Signal"]] = relationship(
        back_populates="analyse", cascade="all, delete-orphan"
    )


class Signal(Base):
    __tablename__ = "signaux"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analyse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False
    )
    niche_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("niches.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    donnees: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_partiel: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("score_partiel BETWEEN 0 AND 100"),
        Index("idx_signaux_analyse_id", "analyse_id"),
        Index("idx_signaux_niche_id", "niche_id"),
        Index("idx_signaux_source", "source"),
    )

    # Relations
    analyse: Mapped["Analyse"] = relationship(back_populates="signaux")

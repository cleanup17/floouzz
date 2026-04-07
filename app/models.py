"""Modèles SQLAlchemy — tables niches, analyses, signaux."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Niche(Base):
    __tablename__ = "niches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mot_cle: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mot_cle_en: Mapped[str | None] = mapped_column(String(255))
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


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    cle_api_ref: Mapped[str | None] = mapped_column(String(50))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    cron_expr: Mapped[str] = mapped_column(String(50), default="0 6 * * *")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('serpapi', 'apify_actor', 'apify_url', 'api', 'webhook')"
        ),
        Index("idx_sources_actif", "actif"),
    )

    # Relations
    decouvertes: Mapped[list["Decouverte"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Decouverte(Base):
    __tablename__ = "decouvertes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    titre: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    donnees: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_pertinence: Mapped[int | None] = mapped_column()
    resume: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    statut: Mapped[str] = mapped_column(String(20), default="nouveau")
    mot_cle_suggere: Mapped[str | None] = mapped_column(String(255))
    niche_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("niches.id"), nullable=True
    )
    scan_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("score_pertinence BETWEEN 0 AND 100"),
        CheckConstraint("statut IN ('nouveau', 'vu', 'approfondi', 'ignore')"),
        Index("idx_decouvertes_scan_date", scan_date.desc()),
        Index("idx_decouvertes_statut", "statut"),
        Index("idx_decouvertes_source_id", "source_id"),
    )

    # Relations
    source: Mapped["Source"] = relationship(back_populates="decouvertes")
    preferences: Mapped[list["Preference"]] = relationship(
        back_populates="decouverte", cascade="all, delete-orphan"
    )


class Thematique(Base):
    __tablename__ = "thematiques"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nom: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Preference(Base):
    __tablename__ = "preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    decouverte_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decouvertes.id", ondelete="CASCADE"), nullable=False
    )
    tags_associes: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("type IN ('like', 'ignore')"),
        Index("idx_preferences_type", "type"),
    )

    # Relations
    decouverte: Mapped["Decouverte"] = relationship(back_populates="preferences")

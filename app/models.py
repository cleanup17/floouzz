"""Modèles SQLAlchemy — tables niches, analyses, signaux."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
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

    # Scores 0-10 produits par pipeline_ia
    score_global: Mapped[int | None] = mapped_column()
    score_demande: Mapped[int | None] = mapped_column()
    score_douleur: Mapped[int | None] = mapped_column()
    score_concurrence: Mapped[int | None] = mapped_column()
    score_monetisation: Mapped[int | None] = mapped_column()

    # Verdict GO / WATCH / SKIP
    verdict: Mapped[str | None] = mapped_column(String(20))
    verdict_raison: Mapped[str | None] = mapped_column(Text)

    # Contenu enrichi par pipeline_ia
    resume_fr: Mapped[str | None] = mapped_column(Text)
    mots_cles_seo: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list
    )
    risque_ymyl: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    niche_detectee: Mapped[str | None] = mapped_column(String(255))

    # Retour brut du pipeline_ia (debug, rejeu)
    pipeline_ia: Mapped[dict | None] = mapped_column(JSONB)

    # Retour brut de serp_gap.analyser_serp() (SERP Gap Detector)
    # Contient : score_difficulte, verdict, verdict_raison, opportunites,
    # faiblesses_detectees, top_10 (snapshot structure)
    serp_gap: Mapped[dict | None] = mapped_column(JSONB)

    # Retour brut de affiliate_finder.chercher_affiliation() (Affiliate Finder)
    # Contient : score_affiliation, verdict (AUCUN/FAIBLE/BON/EXCELLENT),
    # verdict_raison, plateformes_detectees, programmes (avec commission/cookie),
    # opportunites, requetes_utilisees, nb_resultats_analyses
    affiliate_finder: Mapped[dict | None] = mapped_column(JSONB)

    # Retour brut de saisonnalite.analyser_saisonnalite() (Detection saisonnalite)
    # Contient : score_saisonnalite, verdict (STABLE/CYCLIQUE/SAISONNIER/
    # PIC_UNIQUE/AUCUNE_DONNEE), verdict_raison, stats (min/max/moyenne/ratio/
    # coef/concentration_top8), pic (mois/date/valeur/semaines_top_80pct),
    # position_actuelle (mois_actuel/distance_au_pic_mois/phase),
    # recommandations, serie (53 points hebdomadaires pour sparkline UI)
    saisonnalite: Mapped[dict | None] = mapped_column(JSONB)

    # Retour brut de marketplace_gap.analyser_marketplace() (Marketplace Gap)
    # Contient : score_marketplace, verdict (AUCUN/FAIBLE/MOYEN/SATURE),
    # verdict_raison, plateformes_actives, total_resultats,
    # details_par_plateforme (nom/domaine/total_resultats/exemples),
    # recommandations, requetes_utilisees
    marketplace_gap: Mapped[dict | None] = mapped_column(JSONB)

    # Retour brut de international.analyser_international() (Score International)
    # Contient : nb_marches_analyses, marches (pays/code/mot_cle_traduit/
    # total_resultats/nb_top5_editorial/nb_ads/score/verdict/raison),
    # recommandation, meilleurs_marches
    international: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Contraintes : tous les scores en 0-10, verdict strict
    __table_args__ = (
        CheckConstraint("score_global BETWEEN 0 AND 10"),
        CheckConstraint("score_demande BETWEEN 0 AND 10"),
        CheckConstraint("score_douleur BETWEEN 0 AND 10"),
        CheckConstraint("score_concurrence BETWEEN 0 AND 10"),
        CheckConstraint("score_monetisation BETWEEN 0 AND 10"),
        CheckConstraint("verdict IN ('GO', 'WATCH', 'SKIP')"),
        Index("idx_analyses_niche_id", "niche_id"),
        Index("idx_analyses_created_at", created_at.desc()),
        Index("idx_analyses_verdict", "verdict"),
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
            "type IN ('serpapi', 'apify_actor', 'apify_url', 'api', 'webhook', 'sitemap')"
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

    # Scores 0-10 produits par pipeline_ia
    score_global: Mapped[int | None] = mapped_column()
    score_demande: Mapped[int | None] = mapped_column()
    score_douleur: Mapped[int | None] = mapped_column()
    score_concurrence: Mapped[int | None] = mapped_column()
    score_monetisation: Mapped[int | None] = mapped_column()

    # Verdict GO / WATCH / SKIP
    verdict: Mapped[str | None] = mapped_column(String(20))
    verdict_raison: Mapped[str | None] = mapped_column(Text)

    # Contenu enrichi par pipeline_ia
    resume_fr: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    mots_cles_seo: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list
    )
    risque_ymyl: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Retour brut du pipeline_ia (debug, rejeu)
    pipeline_ia: Mapped[dict | None] = mapped_column(JSONB)

    # Metadata
    statut: Mapped[str] = mapped_column(String(20), default="nouveau")
    mot_cle_suggere: Mapped[str | None] = mapped_column(String(255))
    niche_detectee: Mapped[str | None] = mapped_column(String(255))
    niche_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("niches.id"), nullable=True
    )
    scan_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("score_global BETWEEN 0 AND 10"),
        CheckConstraint("score_demande BETWEEN 0 AND 10"),
        CheckConstraint("score_douleur BETWEEN 0 AND 10"),
        CheckConstraint("score_concurrence BETWEEN 0 AND 10"),
        CheckConstraint("score_monetisation BETWEEN 0 AND 10"),
        CheckConstraint("verdict IN ('GO', 'WATCH', 'SKIP')"),
        CheckConstraint("statut IN ('nouveau', 'vu', 'approfondi', 'ignore')"),
        Index("idx_decouvertes_scan_date", scan_date.desc()),
        Index("idx_decouvertes_statut", "statut"),
        Index("idx_decouvertes_source_id", "source_id"),
        Index("idx_decouvertes_verdict", "verdict"),
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


class CacheIA(Base):
    """Cache des resultats du pipeline IA — TTL 24h, invalidable par source."""

    __tablename__ = "cache_ia"

    hash_contenu: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str | None] = mapped_column(String(50))
    resultat: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_cache_ia_expires_at", "expires_at"),
        Index("idx_cache_ia_source", "source"),
    )


class ExecutionScanner(Base):
    """Log d'une execution du scanner pour une source donnee."""

    __tablename__ = "executions_scanner"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    statut: Mapped[str] = mapped_column(String(20), nullable=False)
    nb_signaux: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    erreur: Mapped[str | None] = mapped_column(Text)
    duree_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        CheckConstraint("statut IN ('succes', 'echec', 'partiel')"),
        Index("idx_executions_scanner_source", "source"),
        Index("idx_executions_scanner_created_at", created_at.desc()),
        Index("idx_executions_scanner_statut", "statut"),
    )

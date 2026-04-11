"""
Tests du scheduler APScheduler (app.main._creer_scheduler).

Couvre :
- SCAN_CRON valide -> scheduler cree avec un job "scan_quotidien"
- SCAN_CRON vide / espaces -> None (scheduler desactive)
- SCAN_CRON invalide -> None sans crash (logged en erreur)
- Timezone Europe/Paris appliquee
- max_instances=1 et coalesce=True (pas de chevauchement)
"""

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.main import _creer_scheduler


class TestCreerScheduler:

    def test_scan_cron_valide_retourne_scheduler(self, monkeypatch):
        """SCAN_CRON valide -> AsyncIOScheduler avec job enregistre."""
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "0 6 * * *")

        scheduler = _creer_scheduler()

        assert scheduler is not None
        assert isinstance(scheduler, AsyncIOScheduler)

        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "scan_quotidien"
        assert jobs[0].name == "Scan Floouzz quotidien"

    def test_scan_cron_vide_retourne_none(self, monkeypatch):
        """SCAN_CRON='' -> scheduler desactive, retourne None."""
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "")

        scheduler = _creer_scheduler()

        assert scheduler is None

    def test_scan_cron_whitespace_retourne_none(self, monkeypatch):
        """SCAN_CRON='   ' (espaces) -> desactive."""
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "   ")

        scheduler = _creer_scheduler()

        assert scheduler is None

    def test_scan_cron_invalide_retourne_none_sans_crash(self, monkeypatch):
        """
        SCAN_CRON='pas un cron' -> retourne None au lieu de crasher.
        Le log d'erreur est emis mais ne remonte pas dans l'assertion.
        """
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "pas un cron valide")

        scheduler = _creer_scheduler()

        assert scheduler is None

    def test_scan_cron_trop_peu_de_champs_retourne_none(self, monkeypatch):
        """Un cron a 3 champs au lieu de 5 -> None (ValueError attrape)."""
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "0 6 *")

        scheduler = _creer_scheduler()

        assert scheduler is None

    def test_timezone_europe_paris(self, monkeypatch):
        """Le scheduler est cree en timezone Europe/Paris."""
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "0 6 * * *")

        scheduler = _creer_scheduler()

        assert scheduler is not None
        # APScheduler expose la timezone via scheduler.timezone (pytz/zoneinfo)
        tz_str = str(scheduler.timezone)
        assert "Europe/Paris" in tz_str

    def test_job_coalesce_et_max_instances(self, monkeypatch):
        """
        Les options critiques sont bien appliquees :
        - max_instances=1 (pas de chevauchement si un scan deborde)
        - coalesce=True (un seul rattrapage apres downtime)
        """
        monkeypatch.setattr("app.main.settings.SCAN_CRON", "0 6 * * *")

        scheduler = _creer_scheduler()

        assert scheduler is not None
        job = scheduler.get_job("scan_quotidien")
        assert job is not None
        assert job.max_instances == 1
        assert job.coalesce is True

    def test_job_cible_run_scan_complet(self, monkeypatch):
        """Le job reference bien run_scan_complet comme callable."""
        from app.services.scanner import run_scan_complet

        monkeypatch.setattr("app.main.settings.SCAN_CRON", "0 6 * * *")

        scheduler = _creer_scheduler()

        assert scheduler is not None
        job = scheduler.get_job("scan_quotidien")
        assert job.func is run_scan_complet

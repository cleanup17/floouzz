"""
Dry-run du connecteur Sitemap Intelligence.

Teste fetch_sitemap() en isolation totale :
- Appel HTTP reel vers les sitemaps configures (gratuit, pas de cle API)
- Aucun appel Claude / pipeline_ia (zero cout)
- Aucun INSERT en base : on simule juste ce qui serait cree comme Decouverte

Usage (dans le conteneur app) :
    docker compose exec app python -m scripts.test_sitemap_scan

Output :
1. Config chargee depuis la BDD (source nommee 'Sitemap Intelligence')
2. Resultats bruts du connecteur (SourceResult)
3. Aperçu des lignes Decouverte qui seraient inserees
4. Stats : nb total, par domaine, par tranche de fraicheur
"""

import asyncio
import json
from collections import Counter
from datetime import date

from sqlalchemy import select

from app.database import async_session
from app.models import Source
from app.services.sources.sitemap import fetch_sitemap


async def main() -> None:
    # ---- 1. Chargement de la config depuis la BDD ---------------------------
    async with async_session() as db:
        stmt = select(Source).where(Source.nom == "Sitemap Intelligence")
        result = await db.execute(stmt)
        source = result.scalar_one_or_none()

    if source is None:
        print("ERREUR : source 'Sitemap Intelligence' introuvable en base.")
        return

    print("=" * 70)
    print("DRY-RUN Sitemap Intelligence")
    print("=" * 70)
    print(f"Source ID  : {source.id}")
    print(f"Actif      : {source.actif}")
    print(f"Type       : {source.type}")
    print(f"Config     : {json.dumps(source.config, indent=2, ensure_ascii=False)}")
    print()

    # ---- 2. Appel reel du connecteur ----------------------------------------
    print("Appel fetch_sitemap()...")
    print("-" * 70)
    results = await fetch_sitemap(mot_cle="", config=source.config)
    print(f"{len(results)} SourceResult retourne(s)")
    print()

    # ---- 3. Affichage brut des SourceResult ---------------------------------
    print("=" * 70)
    print("RESULTATS BRUTS (SourceResult)")
    print("=" * 70)
    for i, r in enumerate(results, 1):
        erreur = r.donnees.get("erreur") if isinstance(r.donnees, dict) else None
        marqueur = "[ERR]" if erreur else "[OK] "
        print(f"{marqueur} [{i:>2}] score={r.score_partiel:>3} | {r.titre[:80]}")
        if r.url:
            print(f"         url : {r.url[:120]}")
        if erreur:
            print(f"         ERREUR : {erreur}")
    print()

    # ---- 4. Simulation des lignes Decouverte --------------------------------
    #
    # Le scanner ferait exactement ceci dans run_collecte() pour chaque
    # SourceResult non-errorrne. On reproduit la logique sans db.add().
    print("=" * 70)
    print("APERCU INSERT DECOUVERTE (dry-run, non persiste)")
    print("=" * 70)
    preview = []
    for r in results:
        if isinstance(r.donnees, dict) and r.donnees.get("erreur"):
            continue
        preview.append({
            "source_id": str(source.id),
            "titre": r.titre[:500],
            "url": r.url,
            "donnees": r.donnees,
            "scan_date": str(date.today()),
            "statut": "nouveau",
        })

    print(f"{len(preview)} ligne(s) seraient ajoutee(s) a la table decouvertes")
    if preview:
        print()
        print("Exemple (premiere ligne) :")
        print(json.dumps(preview[0], indent=2, ensure_ascii=False, default=str))
    print()

    # ---- 5. Stats utiles ----------------------------------------------------
    print("=" * 70)
    print("STATS")
    print("=" * 70)

    # Par domaine
    domaines = Counter()
    for p in preview:
        d = p["donnees"].get("domaine", "inconnu")
        domaines[d] += 1
    if domaines:
        print("Repartition par domaine :")
        for dom, nb in domaines.most_common():
            print(f"  {dom:<40} {nb:>3}")

    # Par tranche de fraicheur (age_jours)
    tranches = Counter()
    for p in preview:
        age = p["donnees"].get("age_jours")
        if age is None:
            continue
        if age == 0:
            tranches["aujourd'hui"] += 1
        elif age <= 3:
            tranches["< 3 jours"] += 1
        elif age <= 7:
            tranches["< 7 jours"] += 1
        elif age <= 14:
            tranches["< 14 jours"] += 1
        else:
            tranches["< 30 jours"] += 1

    if tranches:
        print()
        print("Repartition par fraicheur :")
        ordre = ["aujourd'hui", "< 3 jours", "< 7 jours", "< 14 jours", "< 30 jours"]
        for tranche in ordre:
            nb = tranches.get(tranche, 0)
            if nb:
                print(f"  {tranche:<20} {nb:>3}")

    # Score partiel (fraicheur convertie en 0-100)
    scores = [r.score_partiel for r in results if r.score_partiel > 0]
    if scores:
        print()
        print(f"Score partiel : min={min(scores)}, max={max(scores)}, "
              f"moyenne={sum(scores) // len(scores)}")

    print()
    print("Dry-run termine. Aucune ligne inseree en base.")


if __name__ == "__main__":
    asyncio.run(main())

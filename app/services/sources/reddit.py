"""
Source de signaux : Reddit via API native (gratuite, sans cle).

Utilise l'endpoint public https://www.reddit.com/r/{subreddit}/hot.json
pour scanner les posts chauds de subreddits specifiques.

Remplace l'ancien connecteur Apify (trudax/reddit-scraper, 404 depuis
avril 2026). L'API Reddit native est gratuite pour la lecture publique,
pas de cle requise, rate limit genereux (~60 req/min sans auth).

Les titres sont pre-traduits EN->FR via traduire_titres() avant
transmission au scanner / pipeline_ia qui attendent du francais.
"""

import logging
from datetime import datetime, timezone

import httpx

from app.services.sources.base import SourceResult
from app.services.traduction import traduire_titres

logger = logging.getLogger(__name__)

# URL template de l'API Reddit native (endpoint public JSON)
REDDIT_API_URL = "https://www.reddit.com/r/{subreddit}/hot.json"

# User-Agent obligatoire pour l'API Reddit (sinon 429 ou 403)
USER_AGENT = "FloouzzBot/0.5 (+https://github.com/cleanup17/floouzz)"

# Subreddits par defaut si aucun n'est configure dans la source
DEFAULT_SUBREDDITS = [
    "SaaS", "smallbusiness", "startups", "Entrepreneur",
    "artificial", "ecommerce", "nocode",
]

# Nombre de posts a recuperer par subreddit (on filtre ensuite)
POSTS_PAR_SUBREDDIT = 15

# Seuils d'engagement minimum pour garder un post
SEUIL_COMMENTAIRES_MIN = 5
SEUIL_UPVOTES_MIN = 20


async def fetch_reddit(mot_cle: str, config: dict) -> list[SourceResult]:
    """
    Mode Decouverte : scanner les posts chauds de subreddits specifiques.
    Le mot_cle est ignore — on ecoute ce qui monte dans les communautes.

    Config supportee :
        - input.subreddits : list[str] (defaut DEFAULT_SUBREDDITS)
        - input.sort : str (ignore, toujours 'hot' pour l'API native)
        - input.maxItems : int (defaut POSTS_PAR_SUBREDDIT par subreddit)
    """
    subreddits = DEFAULT_SUBREDDITS
    if "input" in config and isinstance(config["input"], dict):
        subreddits = config["input"].get("subreddits", DEFAULT_SUBREDDITS)

    # Collecte des posts bruts depuis tous les subreddits
    posts_bruts: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for sub in subreddits:
            url = REDDIT_API_URL.format(subreddit=sub)
            try:
                response = await client.get(
                    url,
                    params={"limit": POSTS_PAR_SUBREDDIT, "raw_json": 1},
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.warning(f"Reddit r/{sub} erreur : {e}")
                continue

            children = data.get("data", {}).get("children", [])
            for child in children:
                post = child.get("data", {})
                if not post.get("title"):
                    continue
                # Injecter le subreddit pour tracabilite
                post["_subreddit"] = sub
                posts_bruts.append(post)

    if not posts_bruts:
        return []

    # Pre-filtrage par engagement minimum (avant traduction pour economiser)
    posts_filtres = []
    for p in posts_bruts:
        comments = p.get("num_comments", 0) or 0
        upvotes = p.get("ups", 0) or 0
        if comments < SEUIL_COMMENTAIRES_MIN and upvotes < SEUIL_UPVOTES_MIN:
            continue
        posts_filtres.append(p)

    if not posts_filtres:
        return []

    # Tri par engagement decroissant (commentaires = discussion active)
    posts_filtres.sort(key=lambda x: x.get("num_comments", 0), reverse=True)
    posts_filtres = posts_filtres[:15]

    # Traduction batch EN->FR des titres (Reddit est majoritairement anglophone).
    # Un seul appel Claude Haiku pour tout le lot.
    titres_en = [p.get("title", "") for p in posts_filtres]
    titres_fr = await traduire_titres(titres_en)

    results = []
    for p, titre_original, titre_fr in zip(posts_filtres, titres_en, titres_fr):
        comments = p.get("num_comments", 0) or 0
        upvotes = p.get("ups", 0) or 0
        subreddit = p.get("_subreddit", p.get("subreddit", ""))
        permalink = p.get("permalink", "")
        post_url = f"https://www.reddit.com{permalink}" if permalink else ""

        if comments >= 100:
            score = 85
        elif comments >= 50:
            score = 70
        elif comments >= 20:
            score = 55
        else:
            score = 40

        results.append(SourceResult(
            titre=f"r/{subreddit} : {titre_fr}",
            url=post_url,
            donnees={
                "titre_original": titre_original,
                "titre_fr": titre_fr,
                "subreddit": subreddit,
                "upvotes": upvotes,
                "commentaires": comments,
                "source": "reddit",
                "collecte": datetime.now(timezone.utc).isoformat(),
            },
            score_partiel=score,
        ))

    return results

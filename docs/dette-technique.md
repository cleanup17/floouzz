# Floouzz — Dette technique

Liste des tickets ouverts a traiter en passe de nettoyage dediee.
Separe du changelog pour garder l'historique des versions lisible.

---

## TemplateResponse API depreciee (Starlette)

**Statut** : ouvert
**Decouvert** : v0.3.0 (tests pytest)
**Impact** : 10 warnings de deprecation a chaque rendu de template

Tous les routers appellent :
```python
templates.TemplateResponse("nom.html", {"request": request, ...})
```

La nouvelle API Starlette est :
```python
templates.TemplateResponse(request, "nom.html", context)
```

**Fichiers a corriger en une seule passe** :
- `app/routers/niches.py`
- `app/routers/decouvertes.py`
- `app/routers/sources.py`
- `app/routers/parametres.py`
- `app/routers/webhooks.py`
- Tout autre consommateur decouvert au grep

**Effort estime** : 30 minutes

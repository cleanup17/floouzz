# Floouzz — Dette technique

Liste des tickets ouverts a traiter en passe de nettoyage dediee.
Separe du changelog pour garder l'historique des versions lisible.

---

## ~~TemplateResponse API depreciee (Starlette)~~

**Statut** : RESOLU en v0.4.0 (2026-04-11)
**Decouvert** : v0.3.0 (tests pytest)

Migration vers la nouvelle API Starlette :
```python
templates.TemplateResponse(request, "nom.html", context)
```

8 occurrences corrigees sur 3 fichiers :
- `app/routers/niches.py` (5)
- `app/routers/decouvertes.py` (2)
- `app/routers/parametres.py` (1)

Validation : 100 tests PASS, 0 warning de deprecation restant.

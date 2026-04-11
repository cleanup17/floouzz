-- Floouzz Phase 9 — Detection de saisonnalite
-- A appliquer apres phase8.sql
-- Ajoute le champ saisonnalite JSONB a la table analyses pour stocker le
-- resultat de l'analyse de la courbe Google Trends 12 mois.

-- Champ JSONB pour le retour complet de saisonnalite.analyser_saisonnalite() :
--   score_saisonnalite, verdict (STABLE/CYCLIQUE/SAISONNIER/PIC_UNIQUE/
--   AUCUNE_DONNEE), verdict_raison, stats (min/max/moyenne/ratio/coef/
--   concentration), pic (mois_principal/date/valeur/semaines_top_80),
--   position_actuelle (mois_actuel/distance_au_pic_mois/phase),
--   recommandations, serie (53 points hebdomadaires pour visualisation).
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS saisonnalite JSONB;

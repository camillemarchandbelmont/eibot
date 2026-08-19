"""Mutation testing manuel : chaque mutation doit faire échouer au moins un test.

Le principe : injecter un bug plausible dans le code de production, relancer la
suite, et vérifier qu'elle passe au rouge. Une mutation qui survit signale un
test décoratif — il exécute le code sans en vérifier le comportement.

Usage :
    python tests/mutations.py            # toutes
    python tests/mutations.py acces      # celles dont le nom contient 'acces'

Le fichier muté est restauré dans tous les cas, y compris si la suite plante ou
si l'on interrompt le script (Ctrl-C).

Le nom d'une mutation dit où elle mord, donc quel lot la rejoue : `tournee-` la
mécanique d'envoi commune, `surface-` le vocabulaire des commandes, `bot-` les
commandes elles-mêmes et leurs modules, le reste le fichier de calcul visé.

Déplacer du code oblige à repointer les motifs qui le visaient. Un motif dont le
fichier a changé n'échoue pas : le script l'annonce introuvable et le compte
survivant, si bien qu'un lot peut sembler passer alors qu'il n'a plus rien
éprouvé. Vérifier après tout déménagement que le total attendu est bien atteint.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#: (nom, fichier, texte d'origine, texte muté, raison d'être du test attendu)
#:
#: `origine` doit apparaître **exactement une fois** dans le fichier : le script
#: refuse la mutation sinon, plutôt que d'en appliquer une au mauvais endroit.
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # --- src/acces.py : qui a le droit d'utiliser le bot -------------------
    (
        "acces-tout-ouvert",
        "src/acces.py",
        "    if est_admin:\n        return True",
        "    return True\n    if est_admin:\n        return True",
        "n'importe qui utiliserait le bot et le site",
    ),
    (
        "acces-admin-ignore",
        "src/acces.py",
        "    if est_admin:\n        return True",
        "    if False:\n        return True",
        "un admin absent de la liste serait verrouillé dehors",
    ),
    (
        "acces-id-non-normalise",
        "src/acces.py",
        "return str(membre_id) in [str(membre) for membre in autorises]",
        "return membre_id in list(autorises)",
        "un id int de Discord ne correspondrait plus à la liste JSONB",
    ),
    (
        "acces-id-vide-passe",
        "src/acces.py",
        "    if not membre_id:\n        return False",
        "    if not membre_id:\n        pass",
        "une session web sans id chercherait une correspondance dans la liste",
    ),
    (
        "acces-liste-ouverte-aux-autorises",
        "src/acces.py",
        "    return bool(est_admin)",
        "    return True",
        "un membre autorisé pourrait s'ajouter des complices",
    ),
    # --- src/api.py : le secret partagé protège les écritures --------------
    (
        "api-secret-ignore",
        "src/api.py",
        "        if not _autorise(requete):",
        "        if False:",
        "l'API entière serait ouverte à qui connaît l'URL",
    ),
    (
        "api-secret-vide-passe",
        "src/api.py",
        "    if not settings.API_SECRET:\n        return False",
        "    if not settings.API_SECRET:\n        return True",
        "sans API_SECRET configuré, tout appel passerait",
    ),
    (
        "api-secret-prefixe-suffit",
        "src/api.py",
        "return bool(fourni) and hmac.compare_digest(fourni, settings.API_SECRET)",
        "return bool(fourni) and settings.API_SECRET.startswith(fourni)",
        "un préfixe du secret suffirait à entrer",
    ),
    (
        "api-garde-hors-api-tout-passe",
        "src/api.py",
        '        if not requete.path.startswith("/api/"):',
        "        if True:",
        "la garde ne protégerait plus rien",
    ),
    # --- src/api.py : validation avant écriture ---------------------------
    (
        "api-champs-inconnus-acceptes",
        "src/api.py",
        "        if inconnus:",
        "        if False:",
        "le site pourrait écrire n'importe quelle clé, dont `autorises`",
    ),
    (
        "api-fourchette-sans-borne-acceptee",
        "src/api.py",
        "    if not fourchettes:\n        raise RequeteInvalide(",
        "    if False:\n        raise RequeteInvalide(",
        "un bot neuf renverrait une liste vide, lue comme « aucune promotion »",
    ),
    (
        "api-fourchette-une-seule-au-lieu-de-lunion",
        "src/api.py",
        'donne.get("prix_max", max(Decimal(f["prix_max"]) for f in fourchettes)),',
        'donne.get("prix_max", Decimal(fourchettes[0]["prix_max"])),',
        "les promos des autres fourchettes disparaîtraient de la page",
    ),
    (
        "api-montant-valide-apres-la-base",
        "src/api.py",
        '    donne = {}\n    if source.get("min") not in (None, ""):',
        '    donne = {}\n    if False and source.get("min") not in (None, ""):',
        "`?min=abc` serait ignoré au lieu d'être signalé",
    ),
    (
        "api-heure-non-bornee",
        "src/api.py",
        "        if not (0 <= heures <= 23 and 0 <= minutes <= 59):",
        "        if False:",
        "25:00 serait accepté et la publication n'aurait jamais lieu",
    ),
    (
        "api-ecriture-partielle",
        "src/api.py",
        '        if "fuseau" in charge:\n            champs["fuseau"] = _fuseau(charge["fuseau"])',
        '        if "fuseau" in charge:\n            champs["fuseau"] = str(charge["fuseau"])',
        "un fuseau inexistant casserait le calcul de l'heure",
    ),
    (
        "api-template-non-valide",
        "src/api.py",
        "        try:\n            valider_template(modele)\n        except TemplateError as erreur:\n"
        "            raise RequeteInvalide(str(erreur)) from erreur\n\n        await bot.store.set_template(modele)",
        "        await bot.store.set_template(modele)",
        "un template à deux embeds serait enregistré et casserait la publication",
    ),
    (
        "api-apercu-enregistre",
        "src/api.py",
        "        prix_min, prix_max = await _fourchette(bot, requete, charge)\n"
        "        meta, batiments = await bot.charger()",
        "        await bot.store.set_template(modele)\n"
        "        prix_min, prix_max = await _fourchette(bot, requete, charge)\n"
        "        meta, batiments = await bot.charger()",
        "un template seulement essayé serait imposé au post du lendemain",
    ),
    (
        "api-erreur-en-texte-brut",
        "src/api.py",
        "def _erreur(message: str, statut: int = 400) -> web.Response:\n    return _json({\"erreur\": message}, statut)",
        "def _erreur(message: str, statut: int = 400) -> web.Response:\n"
        "    return web.Response(text=message, status=statut)",
        "le site recevrait du text/plain et planterait sur res.json()",
    ),
    (
        "api-cle-dapi-dans-lerreur",
        "src/api.py",
        'return _erreur(f"Erreur inattendue ({type(erreur).__name__}).", 500)',
        'return _erreur(f"Erreur inattendue : {erreur}", 500)',
        "un message d'exception peut contenir l'URL avec la clé d'API",
    ),
    # --- src/serialisation.py : les montants ne doivent rien perdre --------
    (
        "serial-prix-en-float",
        "src/serialisation.py",
        "        nom: format_money(valeur),\n"
        f'        f"{{nom}}_long": format_money_long(valeur),\n'
        f'        f"{{nom}}_brut": format_money_brut(valeur),',
        "        nom: format_money(valeur),\n"
        f'        f"{{nom}}_long": format_money_long(valeur),\n'
        f'        f"{{nom}}_brut": float(valeur),',
        "un montant de 21 chiffres serait arrondi en silence",
    ),
    (
        "serial-stockage-toujours-persistant",
        "src/serialisation.py",
        '"stockage": "postgres" if persistant else "memoire",',
        '"stockage": "postgres",',
        "le site ne pourrait plus avertir que les réglages sont volatils",
    ),
    (
        "serial-repechage-invisible",
        "src/serialisation.py",
        '"dans_fourchette": bool(promo.dans_fourchette),',
        '"dans_fourchette": True,',
        "une promo hors budget serait présentée comme dedans",
    ),
    (
        "serial-fourchette-notation-scientifique",
        "src/serialisation.py",
        "            montant_en_json(champ, _montant_ou_zero(fourchette.get(champ, 0)))",
        '            {champ: str(fourchette.get(champ, 0)),\n'
        '             f"{champ}_brut": str(fourchette.get(champ, 0)),\n'
        '             f"{champ}_long": str(fourchette.get(champ, 0))}',
        "le site afficherait « 1e14 » au lieu de « 100,00 TØ »",
    ),
    (
        "serial-fourchette-salons-perdus",
        "src/serialisation.py",
        '"salons": [str(salon) for salon in fourchette.get("salons") or [] if salon],',
        '"salons": [],',
        "le site dirait « aucun salon » d'une fourchette qui publie",
    ),
    (
        "serial-fourchette-nom-perdu",
        "src/serialisation.py",
        '"nom": str(fourchette.get("nom", "")),',
        '"nom": "",',
        "le site ne saurait plus laquelle des fourchettes il affiche",
    ),
    # --- Contrat de champs avec le site ------------------------------------
    #
    # Un champ renommé côté Python ne lève aucune erreur : le site affiche
    # simplement une colonne vide, et on ne le remarque qu'en comparant à la
    # main avec le jeu. Ces mutations vérifient que les tests de contrat
    # (`test_contrat_*`) attrapent bien ce cas silencieux.
    (
        "serial-champ-loyer-net-renomme",
        "src/serialisation.py",
        '"loyer_net": promo.loyer_net,',
        '"loyernet": promo.loyer_net,',
        "la colonne « Loyer net » du site serait vide, sans erreur",
    ),
    (
        "serial-roles-role-vide-au-lieu-de-filtrer",
        "src/serialisation.py",
        "return {str(serveur): str(role) for serveur, role in table.items() if role}",
        "return {str(serveur): str(role) for serveur, role in table.items()}",
        "le site afficherait un rôle vide au lieu de le masquer",
    ),
    # --- src/db.py : la migration d'une config plate -----------------------
    #
    # Le cas le plus risque du changement : la prod tourne avec une config plate
    # (`prix_min`/`prix_max`/`salons` a la racine). Une migration ratee ne leve
    # rien -- elle fait taire un salon deja configure, et ca ne se remarque que
    # le lendemain a l'heure du post.
    (
        "db-migration-ignoree",
        "src/db.py",
        "        if not any(cle in enregistree for cle in _CHAMPS_PLATS):\n            return []",
        "        return []",
        "la fourchette de la prod disparaîtrait à la mise à jour du bot",
    ),
    (
        "db-migration-ecrase-les-fourchettes",
        "src/db.py",
        '        if "fourchettes" in enregistree:',
        "        if False:",
        "les fourchettes réglées seraient remplacées par la config plate",
    ),
    (
        "db-liste-vide-remigre",
        "src/db.py",
        '            liste = enregistree["fourchettes"] or []\n'
        "            return [_normaliser_fourchette(f) for f in liste if isinstance(f, dict)]",
        '            liste = enregistree["fourchettes"] or []\n'
        "            if liste:\n"
        "                return [_normaliser_fourchette(f) for f in liste if isinstance(f, dict)]",
        "la dernière fourchette supprimée ressusciterait au redémarrage",
    ),
    (
        "db-migration-perd-le-salon-unique",
        "src/db.py",
        '                    "salons": await self.salons(),',
        '                    "salons": [],',
        "un salon réglé avant le multi-salon deviendrait muet",
    ),
    (
        "db-ecriture-materialise-les-defauts",
        "src/db.py",
        '        return dict(await self.get("config", {}) or {})',
        "        return await self.config()",
        "un bot neuf hériterait d'une fourchette « principale » non demandée",
    ),
    (
        "db-champs-plats-non-effaces",
        "src/db.py",
        '        for ancien in ("prix_min", "prix_max", "salons", "salon_id"):\n'
        "            config.pop(ancien, None)",
        "        pass",
        "les vieux champs plats feraient ressusciter la fourchette migrée",
    ),
    (
        "db-nom-sensible-a-la-casse",
        "src/db.py",
        "    return str(nom).strip().casefold()",
        "    return str(nom)",
        "« Grosses » et « grosses » seraient deux fourchettes indistinguables",
    ),
    (
        "db-salon-attache-a-la-premiere-fourchette",
        "src/db.py",
        "        liste = await self.fourchettes()\n"
        "        index = self._index(liste, nom)\n"
        '        if index < 0 or str(salon_id) in liste[index]["salons"]:',
        "        liste = await self.fourchettes()\n"
        "        index = 0 if liste else -1\n"
        '        if index < 0 or str(salon_id) in liste[index]["salons"]:',
        "le salon recevrait les promotions d'une autre fourchette",
    ),
    # --- src/tournee.py : la mécanique d'envoi, une pour toutes -------------
    #
    # Elle était écrite deux fois dans bot.py, une par publication. Les motifs
    # qui la visaient portent donc désormais sur un seul endroit : ce qui les
    # tue vaut pour **toute** publication, présente ou à venir.
    (
        "tournee-diffuse-a-tous-les-salons",
        "src/tournee.py",
        "        for salon_id in envoi.salons:",
        "        for salon_id in [s for e in tournee.envois for s in e.salons]:",
        "un salon recevrait le contenu de tous les envois, pas seulement le sien",
    ),
    (
        "tournee-publie-plusieurs-fois-par-jour",
        "src/tournee.py",
        '    await marquer_le_jour(publication, magasin, maintenant.strftime("%Y-%m-%d"))',
        "    pass",
        "le post repartirait à chaque passage du cron, toutes les cinq minutes",
    ),
    (
        "tournee-marque-malgre-l-echec",
        "src/tournee.py",
        "    if not reussis:",
        "    if False:",
        "une journée entièrement échouée serait comptée comme publiée",
    ),
    (
        "tournee-compte-rendu-dedouble-les-envois",
        "src/tournee.py",
        "    total = sum(len(envoi.salons) for envoi in tournee.envois)",
        "    total = len({s for e in tournee.envois for s in e.salons})",
        "le compte rendu annoncerait moins d'envois qu'il n'en est parti",
    ),
    # --- src/modules/promos.py : ce qui n'appartient qu'aux promotions ------
    (
        "bot-publie-une-seule-fourchette",
        "src/modules/promos.py",
        "    for fourchette in servies:",
        "    for fourchette in servies[:1]:",
        "seule la première fourchette serait publiée, sans erreur visible",
    ),
    (
        "bot-publie-partout-les-memes-promos",
        "src/modules/promos.py",
        '                Decimal(fourchette["prix_min"]),\n'
        '                Decimal(fourchette["prix_max"]),\n'
        "                donnees=donnees,",
        '                Decimal(servies[0]["prix_min"]),\n'
        '                Decimal(servies[0]["prix_max"]),\n'
        "                donnees=donnees,",
        "chaque salon recevrait les promotions de la première fourchette",
    ),
    (
        "bot-fourchette-sans-salon-publiee",
        "src/modules/promos.py",
        '    servies = [f for f in fourchettes if f["salons"]]',
        "    servies = list(fourchettes)",
        "une fourchette sans salon ferait échouer un envoi à chaque passage",
    ),
    (
        "bot-export-recharge-par-fourchette",
        "src/modules/promos.py",
        "                donnees=donnees,\n"
        "                tolere_min=tolere_min,\n"
        "                tolere_max=tolere_max,\n"
        "            )\n"
        "        except Exception as erreur:",
        "                donnees=await bot.charger(),\n"
        "                tolere_min=tolere_min,\n"
        "                tolere_max=tolere_max,\n"
        "            )\n"
        "        except Exception as erreur:",
        "l'export serait téléchargé une fois par fourchette",
    ),
    (
        "bot-publication-ignore-la-tolerance",
        "src/modules/promos.py",
        "            tolere_min, tolere_max = bornes_tolerees(fourchette)",
        "            tolere_min, tolere_max = None, None",
        "la zone n'agirait que sur l'aperçu, pas sur le post quotidien",
    ),
    (
        "bot-role-du-mauvais-serveur",
        "src/modules/promos.py",
        '            role_id = await magasin.role_du_serveur(getattr(serveur, "id", None))',
        "            role_id = next(iter((await magasin.roles()).values()), None)",
        "un salon mentionnerait le rôle d'un autre serveur (@deleted-role)",
    ),
    # --- src/bot.py : ce qui reste dans le noyau ---------------------------
    (
        "bot-apercu-une-seule-fourchette",
        "src/bot.py",
        "        for fourchette in fourchettes:\n"
        "            tolere_min, tolere_max = bornes_tolerees(fourchette)",
        "        for fourchette in fourchettes[:1]:\n"
        "            tolere_min, tolere_max = bornes_tolerees(fourchette)",
        "l'aperçu mentirait sur ce que chaque salon recevra",
    ),
    (
        "bot-promos-une-seule-fourchette",
        "src/commandes.py",
        'else builtins.max(Decimal(f["prix_max"]) for f in fourchettes)',
        'else Decimal(fourchettes[0]["prix_max"])',
        "`/promos` masquerait les promotions des autres fourchettes",
    ),
    (
        "journal-compte-des-salons",
        "src/journal.py",
        'entete = f"✅ **Publication** · {sujet} · {len(reussis)}/{total} envoi"',
        'entete = f"✅ **Publication** · {sujet} · {len(reussis)}/{total} salon"',
        "le journal annoncerait plus de salons qu'il n'en existe",
    ),
    (
        "bot-guild-ids-tronque",
        "src/bot.py",
        "            for serveur_id in settings.GUILD_IDS:",
        "            for serveur_id in settings.GUILD_IDS[:1]:",
        "les commandes manqueraient sur le second serveur, sans erreur",
    ),
    (
        "db-role-id-plat-ecrase-les-roles",
        "src/db.py",
        "        table = await self.roles()\n        if table:",
        "        table = await self.roles()\n        if False:",
        "un rôle qu'on croit remplacé serait mentionné dans les autres serveurs",
    ),
    (
        "db-nom-de-salon-jamais-rafraichi",
        "src/bot.py",
        "        if serveur is not None and getattr(salon, \"name\", None):",
        "        if False:",
        "un salon renommé garderait son ancien nom sur le site indéfiniment",
    ),
    (
        "db-salons-orphelins-non-nettoyes",
        "src/db.py",
        "        if len(gardes) == len(connus):\n            return 0",
        "        return 0",
        "la table des noms grossirait sans fin avec des salons abandonnés",
    ),
    (
        "serial-role-global-toujours-none",
        "src/serialisation.py",
        "    role_global = str(role_id_plat) if (role_id_plat and not table_roles) else None",
        "    role_global = None",
        "le site dirait « aucune mention » alors que le bot pingue bien",
    ),
    (
        "api-champ-interdit-accepte",
        "src/api.py",
        "        inconnus = sorted(set(charge) - set(CHAMPS_MODIFIABLES))",
        "        inconnus = []",
        "le site pourrait écrire n'importe quelle clé en base",
    ),
    # --- La zone de tolérance ----------------------------------------------
    (
        "promos-tolerance-ignoree",
        "src/promos.py",
        "    if len(dedans) < minimum and tolere_min is not None and tolere_max is not None:",
        "    if False:",
        "la zone serait réglable, visible dans la liste, et sans aucun effet",
    ),
    (
        "promos-tolerance-avant-la-fourchette",
        "src/promos.py",
        "    if len(dedans) < minimum and tolere_min is not None and tolere_max is not None:",
        "    if True:",
        "on compléterait depuis la zone même avec la fourchette déjà pleine",
    ),
    (
        "promos-tolere-classe-par-prix",
        "src/promos.py",
        "        candidats.sort(key=lambda b: (ecart(b), -b.valeur))\n        toleres",
        "        candidats.sort(key=lambda b: -b.valeur)\n        toleres",
        "la zone livrerait son bâtiment le plus cher, pas le plus proche du budget",
    ),
    (
        "promos-tolere-non-retire-du-reste",
        "src/promos.py",
        "        reste = [b for b in reste if id(b) not in pris]",
        "        pass",
        "un bâtiment toléré pourrait occuper deux places de la même liste",
    ),
    (
        "promos-zone-toujours-ideale",
        "src/promos.py",
        '                "zone": zones[id(b)],',
        '                "zone": ZONE_IDEALE,',
        "le site colorerait une promo repêchée comme si elle était dans le budget",
    ),
    (
        "db-tolerance-demi-reglee-acceptee",
        "src/db.py",
        "    if tolere_min is None or tolere_max is None:\n        tolere_min = tolere_max = None",
        "    if False:\n        tolere_min = tolere_max = None",
        "une borne seule passerait en Decimal(None) dans la boucle du matin",
    ),
    (
        "db-tolerance-non-recadree",
        "src/db.py",
        "        tolere_min = min(tolere_min, _montant_ou_rien(prix_min) or Decimal(0))",
        "        pass",
        "une zone plus étroite que sa fourchette en exclurait une partie",
    ),
    (
        "db-tolerance-etroite-acceptee",
        "src/db.py",
        "        if tolere_min > Decimal(fourchette[\"prix_min\"]) or tolere_max < Decimal(",
        "        if False or False and Decimal(",
        "les bornes idéales retapées par erreur rétréciraient le budget en silence",
    ),
    (
        "db-tolerance-illisible-leve",
        "src/db.py",
        "    except InvalidOperation:\n        return None",
        "    except InvalidOperation:\n        raise",
        "une faute de frappe dans la config couperait la publication du matin",
    ),
    (
        "serial-tolerance-nulle-exposee",
        "src/serialisation.py",
        '        if str(fourchette.get(champ) or "").strip():',
        "        if True:",
        "le site dessinerait une zone à 0 Ø que personne n'a réglée",
    ),
    # --- Le convertisseur et les frais de gestion --------------------------
    #
    # `rounding` est passé en chaîne (`"ROUND_DOWN"`) et non par la constante :
    # `decimal` accepte les deux, et la constante n'est pas importée dans
    # `money.py` — la mutation échouerait sur un NameError, donc pour la
    # mauvaise raison.
    (
        "money-convertir-rebascule-de-palier",
        "src/money.py",
        "    entier, _, decimales = f\"{mantisse:.2f}\".partition(\".\")",
        "    if mantisse >= 1000:\n        return format_money(valeur)\n"
        "    entier, _, decimales = f\"{mantisse:.2f}\".partition(\".\")",
        "convertir redeviendrait format_money : le palier demandé serait ignoré",
    ),
    (
        "money-convertir-tronque",
        "src/money.py",
        "    mantisse = (abs(valeur) / Decimal(10) ** exposant).quantize(\n"
        "        Decimal(\"0.01\"), rounding=ROUND_HALF_UP\n"
        "    )",
        "    mantisse = (abs(valeur) / Decimal(10) ** exposant).quantize(\n"
        "        Decimal(\"0.01\"), rounding=\"ROUND_DOWN\"\n"
        "    )",
        "2 710,578 TØ s'afficherait 2 710,57 au lieu de 2 710,58",
    ),
    (
        "money-convertir-perd-le-signe",
        "src/money.py",
        "    signe = \"-\" if valeur < 0 else \"\"\n    mantisse",
        "    signe = \"\"\n    mantisse",
        "un montant négatif serait rendu positif",
    ),
    # Les deux suivantes portent sur `exposant_du_symbole`, extraite de
    # `convertir` : la table des paliers du jeu y est lue une seule fois, pour
    # `/convertir` comme pour le tirage d'essai. Une mutation y casse donc les
    # deux — c'est le but de l'extraction.
    (
        "money-exposant-unite-refusee",
        "src/money.py",
        "    if cible in (\"Ø\", \"O\", \"0\", \"\"):\n        return 0",
        "    if False:\n        return 0",
        "demander l'unité lèverait « symbole inconnu » alors que le jeu l'affiche",
    ),
    (
        "money-exposant-symbole-inconnu-accepte",
        "src/money.py",
        "    raise MoneyError(\n"
        "        f\"Symbole monétaire inconnu : « {symbole} ». \"\n"
        "        f\"Symboles valides : {_symboles_valides()}.\"\n"
        "    )",
        "    return 0",
        "« B » serait converti en silence au lieu de lister les symboles valides",
    ),
    (
        "money-exposant-lu-a-cote-du-palier",
        "src/money.py",
        "        return _MULTIPLICATEURS[cible].adjusted()",
        "        return _MULTIPLICATEURS[cible].adjusted() + 1",
        "un montant serait converti dans le palier voisin de celui demandé",
    ),
    (
        "money-frais-tronque",
        "src/money.py",
        "    return (Decimal(montant) * taux / CENT_POURCENT).quantize(\n"
        "        Decimal(1), rounding=ROUND_HALF_UP\n"
        "    )",
        "    return (Decimal(montant) * taux / CENT_POURCENT).quantize(\n"
        "        Decimal(1), rounding=\"ROUND_DOWN\"\n"
        "    )",
        "les frais seraient arrondis vers le bas, contre la règle du bot",
    ),
    (
        "money-frais-garde-les-decimales",
        "src/money.py",
        "    return (Decimal(montant) * taux / CENT_POURCENT).quantize(\n"
        "        Decimal(1), rounding=ROUND_HALF_UP\n"
        "    )",
        "    return Decimal(montant) * taux / CENT_POURCENT",
        "le jeu ne facture pas de fraction d'Ø ; on afficherait 70,07",
    ),
    (
        "money-taux-de-gestion-faux",
        "src/money.py",
        "TAUX_GESTION = Decimal(7)",
        "TAUX_GESTION = Decimal(10)",
        "les frais seraient calculés à 10 % et personne ne le verrait",
    ),
    (
        "bot-frais-taux-recopie-en-dur",
        "src/modules/conversion.py",
        "        frais = frais_de_gestion(valeur)",
        "        frais = valeur * 10 / 100",
        "le message afficherait 7 % et le calcul en appliquerait un autre",
    ),
    (
        "bot-convertir-ignore-le-palier",
        "src/modules/conversion.py",
        "            rendu = convertir(valeur, vers)",
        "            rendu = format_money(valeur)",
        "/convertir rendrait le palier que le bot choisit, pas celui demandé",
    ),
    (
        "bot-convertir-ne-rappelle-pas-la-saisie",
        "src/modules/conversion.py",
        "            f\"**{format_money(valeur)}** = **{rendu}**\\n\"",
        "            f\"**{rendu}**\\n\"",
        "on ne pourrait plus vérifier que « 50 6P » a été lu comme 506 PØ",
    ),
    (
        "bot-frais-public",
        "src/modules/conversion.py",
        "            f\"-# {format_money_long(frais)}\",\n            ephemeral=True,",
        "            f\"-# {format_money_long(frais)}\",\n            ephemeral=False,",
        "un calcul personnel encombrerait le salon",
    ),
    (
        "bot-convertir-sans-l-unite",
        "src/commandes.py",
        "    return [app_commands.Choice(name=\"Ø — unité\", value=\"Ø\"), *choix]",
        "    return choix",
        "l'unité disparaîtrait du menu, sans autre moyen de la demander",
    ),
    # --- src/filiales.py : les relevés de frais ----------------------------
    (
        "filiales-perte-facturee",
        "src/filiales.py",
        "    frais = frais_de_gestion(montant) if montant > 0 else Decimal(0)",
        "    frais = frais_de_gestion(montant)",
        "une filiale en perte se verrait réclamer un montant négatif inventé",
    ),
    (
        "filiales-zero-facture",
        "src/filiales.py",
        "        return self.benefices <= 0",
        "        return self.benefices < 0",
        "une filiale à 0 Ø ne serait plus signalée en perte",
    ),
    (
        "filiales-taux-recopie",
        "src/filiales.py",
        "    frais = frais_de_gestion(montant) if montant > 0 else Decimal(0)",
        "    frais = (montant * Decimal(7) / Decimal(100)) if montant > 0 else Decimal(0)",
        "l'arrondi du jeu (sans décimales) serait perdu",
    ),
    (
        "filiales-nom-vide-accepte",
        "src/filiales.py",
        '    if not propre:\n        raise FilialeError',
        '    if False:\n        raise FilialeError',
        "une ligne anonyme entrerait dans le tableau, introuvable et irremplaçable",
    ),
    (
        "filiales-espaces-internes-perdus",
        "src/filiales.py",
        "    propre = str(nom).strip()",
        '    propre = " ".join(str(nom).split())',
        "« ARMEE  DE TERRE » ne serait plus la clé d'import du jeu",
    ),
    (
        "filiales-comparaison-sensible-a-la-casse",
        "src/filiales.py",
        "    return str(nom).strip().casefold()",
        "    return str(nom).strip()",
        "« armee » créerait un doublon de « ARMEE » et le total compterait deux fois",
    ),
    (
        "filiales-ressaisie-ajoute-au-lieu-de-remplacer",
        "src/filiales.py",
        "    index = index_de(filiales, filiale.nom)\n    if index < 0:\n        return [*filiales, filiale]",
        "    index = -1\n    if index < 0:\n        return [*filiales, filiale]",
        "chaque ressaisie ajouterait une ligne et gonflerait le total",
    ),
    (
        "filiales-ressaisie-remonte-en-fin-de-liste",
        "src/filiales.py",
        "    return [*filiales[:index], filiale, *filiales[index + 1 :]]",
        "    return [*filiales[:index], *filiales[index + 1 :], filiale]",
        "l'ordre de saisie danserait d'un jour à l'autre",
    ),
    (
        "filiales-montants-en-nombre-json",
        "src/filiales.py",
        '            "benefices": str(filiale.benefices),',
        '            "benefices": float(filiale.benefices),',
        "dix-sept chiffres perdraient leurs derniers en passant par un flottant",
    ),
    (
        "filiales-frais-relus-au-lieu-d-etre-recalcules",
        "src/filiales.py",
        "        except (FilialeError, InvalidOperation, ArithmeticError):\n            continue",
        "        except (FilialeError, InvalidOperation, ArithmeticError):\n            raise",
        "une ligne retouchée à la main emporterait tout le tableau",
    ),
    (
        "filiales-total-tronque",
        "src/filiales.py",
        "    return sum((filiale.frais for filiale in filiales), Decimal(0))",
        "    return Decimal(sum(float(filiale.frais) for filiale in filiales))",
        "le total perdrait ses derniers chiffres, donc le montant à payer",
    ),
    # --- Remise à zéro, retrait de masse, chiffres d'essai -----------------
    (
        "zero-noms-perdus",
        "src/filiales.py",
        "        calculer(filiale.nom, Decimal(0), date) for filiale in filiales",
        "        calculer(str(rang), Decimal(0), date)\n        for rang, filiale in enumerate(filiales)",
        "les noms sont la clé d'import du jeu : perdus, tout serait à retaper",
    ),
    (
        "zero-montants-conserves",
        "src/filiales.py",
        "        calculer(filiale.nom, Decimal(0), date) for filiale in filiales",
        "        calculer(filiale.nom, filiale.benefices, date) for filiale in filiales",
        "la remise à zéro ne remettrait rien à zéro",
    ),
    (
        "zero-garde-l-ancienne-date",
        "src/filiales.py",
        "        calculer(filiale.nom, Decimal(0), date) for filiale in filiales",
        "        calculer(filiale.nom, Decimal(0), filiale.date) for filiale in filiales",
        "les lignes remises à zéro s'afficheraient périmées le jour même",
    ),
    (
        "masse-retire-tout",
        "src/filiales.py",
        "    cibles = {_cle(nom) for nom in noms}\n    return [filiale for filiale in filiales if _cle(filiale.nom) not in cibles]",
        "    cibles = {_cle(nom) for nom in noms}\n    return []",
        "une saisie vide viderait le tableau entier",
    ),
    (
        "masse-comparaison-sensible-a-la-casse",
        "src/filiales.py",
        "    cibles = {_cle(nom) for nom in noms}",
        "    cibles = {str(nom) for nom in noms}",
        "« armee » ne retirerait pas « ARMEE », et le retrait paraîtrait fait",
    ),
    (
        "noms-non-decoupes",
        "src/filiales.py",
        '    for morceau in str(saisie).replace("\\n", ",").split(","):',
        "    for morceau in [str(saisie)]:",
        "un lot de noms serait pris pour un seul nom, donc introuvable",
    ),
    (
        "noms-sans-les-retours-a-la-ligne",
        "src/filiales.py",
        '    for morceau in str(saisie).replace("\\n", ",").split(","):',
        '    for morceau in str(saisie).split(","):',
        "une liste collée une par ligne ne serait pas découpée",
    ),
    (
        "noms-espaces-internes-normalises",
        "src/filiales.py",
        "        nom = morceau.strip()",
        '        nom = " ".join(morceau.split())',
        "« ARMEE  DE TERRE » deviendrait une autre clé d'import",
    ),
    (
        "noms-vides-gardes",
        "src/filiales.py",
        "        if not nom or _cle(nom) in vus:",
        "        if _cle(nom) in vus and nom:",
        "une virgule en trop compterait un « inconnu » que personne n'a saisi",
    ),
    (
        "noms-doublons-gardes",
        "src/filiales.py",
        "        if not nom or _cle(nom) in vus:",
        "        if not nom:",
        "le même nom deux fois compterait deux retraits",
    ),
    (
        "essai-une-seule-echelle",
        "src/filiales.py",
        "    chiffres = alea.randint(bas, haut)",
        "    chiffres = haut",
        "toutes les lignes tomberaient dans la même échelle, sans éprouver le tri",
    ),
    (
        "essai-plafonne-sous-le-float",
        "src/filiales.py",
        "CHIFFRES_ESSAI = (3, 21)",
        "CHIFFRES_ESSAI = (3, 15)",
        "l'essai n'atteindrait jamais les montants où un float casse",
    ),
    (
        "essai-sans-petits-montants",
        "src/filiales.py",
        "CHIFFRES_ESSAI = (3, 21)",
        "CHIFFRES_ESSAI = (19, 21)",
        "sans petits montants, ni le tri ni les échelles ne seraient éprouvés",
    ),
    (
        "essai-sans-perte",
        "src/filiales.py",
        "PART_EN_PERTE = 0.2",
        "PART_EN_PERTE = 0.0",
        "la moitié de l'affichage — les filiales en perte — resterait invisible",
    ),
    (
        "essai-montant-en-float",
        "src/filiales.py",
        "    montant = Decimal(alea.randrange(10 ** (chiffres - 1), 10**chiffres))",
        "    montant = Decimal(float(alea.randrange(10 ** (chiffres - 1), 10**chiffres)))",
        "un montant à vingt-un chiffres perdrait les derniers",
    ),
    (
        "essai-montants-identiques",
        "src/filiales.py",
        "        calculer(filiale.nom, benefices_aleatoires(alea, exposant), date)\n        for filiale in filiales",
        "        calculer(filiale.nom, filiale.benefices, date)\n        for filiale in filiales",
        "l'essai laisserait les vrais relevés et n'éprouverait rien",
    ),
    (
        "essai-noms-perdus",
        "src/filiales.py",
        "        calculer(filiale.nom, benefices_aleatoires(alea, exposant), date)\n        for filiale in filiales",
        "        calculer(f\"TEST {rang}\", benefices_aleatoires(alea, exposant), date)\n        for rang, filiale in enumerate(filiales)",
        "il faudrait retaper tous les noms après un essai",
    ),
    (
        "essai-palier-ignore",
        "src/filiales.py",
        "    if exposant is None:",
        "    if True:",
        "l'unité demandée serait ignorée et le tableau resterait sur toute l'échelle",
    ),
    (
        "essai-palier-deborde-en-haut",
        "src/filiales.py",
        "        haut = 1000 * palier - palier // 200",
        "        haut = 1000 * palier",
        "999,996 PØ s'afficherait « 1.00 EØ » : un autre palier que le demandé",
    ),
    (
        "essai-palier-deborde-en-bas",
        "src/filiales.py",
        "        montant = Decimal(alea.randrange(palier, haut))",
        "        montant = Decimal(alea.randrange(1, haut))",
        "un tirage sous le palier s'afficherait avec le symbole du dessous",
    ),
    (
        "essai-palier-sans-variete",
        "src/filiales.py",
        "        montant = Decimal(alea.randrange(palier, haut))",
        "        montant = Decimal(palier)",
        "toutes les lignes porteraient le même montant, sans éprouver le tri",
    ),
    (
        "essai-palier-en-float",
        "src/filiales.py",
        "        montant = Decimal(alea.randrange(palier, haut))",
        "        montant = Decimal(float(alea.randrange(palier, haut)))",
        "au septilliard, quarante-six chiffres, un flottant perdrait la queue",
    ),
    (
        "essai-palier-sans-perte",
        "src/filiales.py",
        "        montant = Decimal(alea.randrange(palier, haut))\n"
        "    if alea.random() < PART_EN_PERTE:",
        "        montant = Decimal(alea.randrange(palier, haut))\n"
        "    if False:",
        "les filiales en perte resteraient invisibles dès qu'une unité est demandée",
    ),
    (
        "essai-palier-non-transmis",
        "src/filiales.py",
        "        calculer(filiale.nom, benefices_aleatoires(alea, exposant), date)",
        "        calculer(filiale.nom, benefices_aleatoires(alea), date)",
        "l'unité serait acceptée par la commande puis perdue avant le tirage",
    ),
    (
        "db-essai-palier-non-transmis",
        "src/db.py",
        "            \"filiales\", vers_json(valeurs_aleatoires(filiales, date, alea, exposant))",
        "            \"filiales\", vers_json(valeurs_aleatoires(filiales, date, alea))",
        "l'unité serait perdue entre la commande et le tirage",
    ),
    # Plus de motif sur la commande `/filiales test` : elle a été retirée du menu
    # — un doigt qui glissait écrasait les vrais relevés du jour. Le tirage
    # lui-même reste en place et reste éprouvé, ci-dessus par `essai-*` du côté du
    # calcul et par `db-essai-*` du côté de l'écriture ; ce qui a disparu est
    # seulement le chemin qui y menait depuis Discord.
    (
        "db-zero-non-enregistree",
        "src/db.py",
        '        await self.set("filiales", vers_json(remettre_a_zero(filiales, date)))',
        "        pass",
        "la remise à zéro serait annoncée sans rien écrire, et tout reviendrait",
    ),
    (
        "db-masse-inconnus-tus",
        "src/db.py",
        "        inconnus = [nom for nom in noms if index_de(avant, nom) < 0]",
        "        inconnus = []",
        "on croirait une filiale supprimée alors qu'elle reste au tableau",
    ),
    (
        "db-masse-liste-videe-non-ecrite",
        "src/db.py",
        "        if len(apres) != len(avant):\n            await self.set(\"filiales\", vers_json(apres))",
        "        if apres:\n            await self.set(\"filiales\", vers_json(apres))",
        "vider le tableau ne serait pas enregistré, et tout reviendrait",
    ),
    (
        "db-essai-non-enregistre",
        "src/db.py",
        "        await self.set(\n"
        "            \"filiales\", vers_json(valeurs_aleatoires(filiales, date, alea, exposant))\n"
        "        )",
        "        pass",
        "l'essai serait annoncé sans que le tableau change",
    ),
    (
        "bot-vider-sans-confirmation",
        "src/modules/filiales.py",
        "        if not confirmer:\n            await interaction.response.send_message(\n                f\"❌ Rien effacé : coche `confirmer` pour aller au bout.\\n\"",
        "        if False:\n            await interaction.response.send_message(\n                f\"❌ Rien effacé : coche `confirmer` pour aller au bout.\\n\"",
        "un cycle entier de relevés partirait sur un clic de travers",
    ),
    (
        "bot-retirer-lot-sans-confirmation",
        "src/modules/filiales.py",
        "        if (tout or len(noms) > 1) and not confirmer:",
        "        if False:",
        "un lot de filiales partirait sans qu'on ait vu lequel",
    ),
    (
        "bot-retirer-tout-sans-confirmation",
        "src/modules/filiales.py",
        "        if (tout or len(noms) > 1) and not confirmer:",
        "        if len(noms) > 1 and not confirmer:",
        "`tout` sur une seule filiale l'emporterait sans cérémonie, alors que le "
        "mot ne nomme pas ce qu'il vise",
    ),
    (
        "bot-retirer-un-nom-demande-une-ceremonie",
        "src/modules/filiales.py",
        "        if (tout or len(noms) > 1) and not confirmer:",
        "        if (tout or len(noms) >= 1) and not confirmer:",
        "une case à cocher sur un geste d'un mot apprendrait à cocher sans lire, "
        "et ne protégerait plus le lot",
    ),
    # Pas de mutation faisant valoir « tout » à une saisie vide : `saisie` est
    # déjà dépouillée et la garde du dessus a rendu la main, si bien que la
    # branche serait inatteignable — un test qui prétendrait la réfuter serait
    # faux. C'est la garde elle-même qui est mutée.
    (
        "bot-retirer-saisie-vide-mal-annoncee",
        "src/modules/filiales.py",
        "        if not saisie:\n            await interaction.response.send_message(\n                \"❌ Aucun nom saisi.",
        "        if False:\n            await interaction.response.send_message(\n                \"❌ Aucun nom saisi.",
        "un refus annoncerait « aucune filiale enregistrée » alors qu'il y en a",
    ),
    (
        "bot-retirer-tout-non-reconnu",
        "src/modules/filiales.py",
        '        tout = saisie.casefold() == "tout"',
        '        tout = saisie == "tout"',
        "`TOUT` ne serait pas reconnu et passerait pour un nom de filiale",
    ),
    # --- src/publish_filiales.py : le tableau lu dans Discord --------------
    (
        "tableau-ordre-croissant",
        "src/publish_filiales.py",
        "    classees = sorted(filiales, key=lambda f: (-f.frais, f.nom.casefold()))",
        "    classees = sorted(filiales, key=lambda f: (f.frais, f.nom.casefold()))",
        "le plus gros poste ne serait plus en tête",
    ),
    (
        "tableau-perte-muette",
        "src/publish_filiales.py",
        '            details = "*en perte, rien à payer*"',
        '            details = f"**{format_money(filiale.frais)}**"',
        "un 0 Ø sans marque se lirait comme une saisie oubliée",
    ),
    (
        "tableau-sans-notation-courte",
        "src/publish_filiales.py",
        'f" · {format_money(filiale.frais)}"',
        'f""',
        "21 chiffres bruts ne se comparent pas d'un coup d'œil entre filiales",
    ),
    (
        "tableau-releves-perimes-non-dates",
        "src/publish_filiales.py",
        "        perime = bool(aujourdhui) and filiale.date != aujourdhui",
        "        perime = False",
        "un relevé d'avant-hier se lirait comme celui du jour",
    ),
    (
        "tableau-troncature-silencieuse",
        "src/publish_filiales.py",
        '        affichees.append(f"-# … +{restantes} filiale(s) non affichée(s)")',
        "        pass",
        "le total se lirait comme portant seulement sur les lignes affichées",
    ),
    (
        "tableau-sans-limite-de-lignes",
        "src/publish_filiales.py",
        "    for ligne in lignes[:LIMITE_LIGNES]:",
        "    for ligne in lignes:",
        "un mur de texte au lieu d'un tableau qu'on lit d'un coup d'œil",
    ),
    (
        "tableau-sans-budget-de-caracteres",
        "src/publish_filiales.py",
        "        if cout > place:\n            break",
        "        if False:\n            break",
        "quarante lignes longues dépassent 4096 : Discord refuse le post entier",
    ),
    (
        "tableau-longueur-comptee-comme-python",
        "src/publish_filiales.py",
        '    return len(texte.encode("utf-16-le")) // 2',
        "    return len(texte)",
        "un emoji pèse deux unités chez Discord : l'embed passerait ici et pas là-bas",
    ),
    # Pas de mutation sur le « +1 » du saut de ligne : avec un budget de 3900 et
    # quarante lignes au plus, l'oublier coûte 40 unités — 3940, encore sous les
    # 4096 de Discord. C'est de la rigueur comptable, pas un garde-fou, et un
    # test qui prétendrait le contraire serait faux.
    (
        "tableau-total-sur-les-seules-lignes-affichees",
        "src/publish_filiales.py",
        "    total = total_frais(filiales)",
        "    total = total_frais(filiales[:LIMITE_LIGNES])",
        "on paierait plus que ce que le tableau annonce",
    ),
    (
        "tableau-vide-muet",
        "src/publish_filiales.py",
        '            f"{EMOJI_VIDE} *Aucune filiale enregistrée.*\\n"',
        '            ""',
        "un embed vide se lirait comme une panne du bot",
    ),
    (
        "tableau-tete-comme-les-autres",
        "src/publish_filiales.py",
        "            marque = EMOJI_TETE if rang == 0 else EMOJI_PAYANTE",
        "            marque = EMOJI_PAYANTE",
        "le poste principal ne se verrait qu'en comparant les montants soi-même",
    ),
    (
        "tableau-perte-marquee-comme-payante",
        "src/publish_filiales.py",
        "            marque = EMOJI_PERTE\n"
        '            details = "*en perte, rien à payer*"',
        "            marque = EMOJI_PAYANTE\n"
        '            details = "*en perte, rien à payer*"',
        "une filiale en perte se lirait comme une filiale qui paie",
    ),
    (
        "tableau-releve-perime-non-marque",
        "src/publish_filiales.py",
        "        if perime:\n"
        "            # L'emoji plutôt que la date seule : en bout d'une liste de vingt\n"
        "            # lignes, « 9 août » se remarque mal.\n"
        "            marque = EMOJI_PERIME",
        "        if False:\n"
        "            marque = EMOJI_PERIME",
        "un relevé oublié se noierait dans une liste de vingt lignes",
    ),
    (
        "tableau-montant-non-copiable",
        "src/publish_filiales.py",
        'f"`{format_money_long(filiale.frais)}`"',
        'f"{format_money_long(filiale.frais)}"',
        "il faudrait sélectionner 21 chiffres à la main pour payer",
    ),
    (
        "tableau-total-non-copiable",
        "src/publish_filiales.py",
        'value=f"`{format_money_long(total)}` · **{format_money(total)}**",',
        'value=f"{format_money_long(total)} · **{format_money(total)}**",',
        "le total à payer ne se copierait plus d'un appui long",
    ),
    (
        "tableau-date-en-anglais",
        "src/publish_filiales.py",
        "    return f\"{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]} {jour.year}\"",
        '    return jour.strftime("%A %d %B %Y")',
        "la locale de Render déciderait de la langue du post",
    ),
    (
        "tableau-date-illisible-fait-echouer-le-post",
        "src/publish_filiales.py",
        "    try:\n        return Date.fromisoformat(iso)\n    except ValueError:\n        return None",
        "    return Date.fromisoformat(iso)",
        "un relevé d'une version antérieure empêcherait tout le tableau de sortir",
    ),
    # --- src/modules/filiales.py : la saisie du tableau --------------------
    #
    # Plus de motif sur la fourche `if filiale is not None` de `/frais` : la case
    # facultative qui décidait d'écrire en base ou non a disparu, et avec elle les
    # deux mutants qui l'éprouvaient. La saisie est `/filiales releve`, où les deux
    # champs sont obligatoires — il n'y a plus de branche à couper.
    (
        "bot-releve-montant-illisible-enregistre-quand-meme",
        "src/modules/filiales.py",
        "            await interaction.response.send_message(\n                f\"❌ {erreur}\\n{aide_montants()}\", ephemeral=True\n            )\n            return\n\n        existait = index_de(await bot.store.filiales(), filiale) >= 0",
        "            await interaction.response.send_message(\n                f\"❌ {erreur}\\n{aide_montants()}\", ephemeral=True\n            )\n\n        existait = index_de(await bot.store.filiales(), filiale) >= 0",
        "une filiale serait retenue à un montant faux et fausserait le total",
    ),
    (
        "bot-releve-public",
        "src/modules/filiales.py",
        "        await interaction.response.send_message(corps, ephemeral=True)",
        "        await interaction.response.send_message(corps, ephemeral=False)",
        "les résultats de l'entreprise s'afficheraient dans le salon",
    ),
    (
        "bot-releve-ressaisie-annoncee-comme-un-ajout",
        "src/modules/filiales.py",
        '        verbe = "mise à jour" if existait else "enregistrée"',
        '        verbe = "enregistrée"',
        "on ne saurait pas qu'on vient d'écraser un relevé",
    ),
    (
        "bot-releve-sans-alerte-de-salon-manquant",
        "src/modules/filiales.py",
        '        if not await bot.store.salons_filiales():\n            # Une saisie qui n\'ira nulle part doit se voir maintenant, pas au\n            # moment où l\'on s\'étonne de ne rien recevoir.\n            corps += "\\n⚠️ Aucun salon pour le tableau : `/filiales salon ajouter`."',
        "        pass",
        "on saisirait des relevés que personne ne recevrait",
    ),
    (
        "bot-tableau-a-l-heure-des-promotions",
        "src/modules/filiales.py",
        "    return await magasin.heure_filiales()",
        '    return (await magasin.config())["heure"]',
        "régler l'heure des promotions déplacerait le tableau",
    ),
    (
        "bot-tableau-dans-les-salons-des-promotions",
        "src/modules/filiales.py",
        "    salons = await magasin.salons_filiales()",
        "    salons = await magasin.salons()",
        "les frais de l'entreprise partiraient dans le salon des promotions",
    ),
    (
        "bot-tour-sans-le-tableau",
        "src/bot.py",
        '            (self.publier_filiales_si_lheure, "filiales"),',
        "",
        "le tableau ne sortirait jamais : /tick ne l'appellerait plus",
    ),
    (
        "bot-tour-sans-isolation-des-pannes",
        "src/bot.py",
        "            except Exception as erreur:\n                log.warning(\"Publication des %s impossible : %s\", quoi, erreur)",
        "            except _JamaisLevee as erreur:\n                log.warning(\"Publication des %s impossible : %s\", quoi, erreur)",
        "une API du jeu en panne ferait taire un tableau qui n'en dépend pas",
    ),
    (
        "bot-filiales-heure-ecrit-celle-des-promotions",
        "src/modules/filiales.py",
        "    await magasin.maj_config(filiales_heure=heure)",
        "    await magasin.maj_config(heure=heure)",
        "régler le tableau déplacerait le post des promotions",
    ),
    # --- src/commandes.py : le vocabulaire commun des publications ---------
    #
    # `heure`, `apercu`, `publier` et `salon` sont écrits une seule fois pour
    # toutes les publications : ces motifs ne visaient que celles du tableau, ils
    # valent maintenant pour la troisième publication comme pour les deux
    # premières.
    (
        "surface-heure-garde-la-marque-du-jour",
        "src/commandes.py",
        "        await marquer_le_jour(publication, bot.store, None)",
        "        pass",
        "le nouvel horaire serait bloqué jusqu'au lendemain",
    ),
    (
        "surface-heure-invalide-acceptee",
        "src/commandes.py",
        "    if not (0 <= heures <= 23 and 0 <= minutes <= 59):\n        return None",
        "    if False:\n        return None",
        "« 25:00 » serait enregistrée et le post ne sortirait plus jamais",
    ),
    (
        "surface-salon-sans-permission",
        "src/commandes.py",
        "        manquantes = permissions_manquantes(interaction, salon)",
        '        manquantes = ""',
        "la permission manquante ne serait découverte qu'à l'heure du post",
    ),
    (
        "surface-apercu-consomme-le-post-du-jour",
        "src/commandes.py",
        '        maintenant = maintenant_local((await bot.store.config())["fuseau"])\n\n        try:',
        '        maintenant = maintenant_local((await bot.store.config())["fuseau"])\n'
        '        await marquer_le_jour(\n'
        '            publication, bot.store, maintenant.strftime("%Y-%m-%d")\n'
        "        )\n\n        try:",
        "un aperçu empêcherait le post du jour de sortir",
    ),
    (
        "web-tick-sans-le-tableau",
        "src/web.py",
        "            resultat = await bot.publier_tout(forcer=forcer)",
        "            resultat = await bot.publier_si_lheure(forcer=forcer)",
        "sur Render, où le service dort, le tableau ne sortirait jamais",
    ),
    # --- L'export au format d'import du jeu --------------------------------
    #
    # Le format est strict et le jeu est le seul juge : une ligne mal formée n'a
    # pas l'air fausse, elle est refusée à l'import sans dire pourquoi. Chaque
    # mutation ci-dessous produirait un fichier d'apparence normale.
    (
        "export-sans-tab",
        "src/filiales.py",
        'SEPARATEUR_IMPORT = "\\t"',
        'SEPARATEUR_IMPORT = " "',
        "le jeu ne verrait qu'une colonne, donc aucun montant",
    ),
    (
        "export-en-lf",
        "src/filiales.py",
        'FIN_DE_LIGNE_IMPORT = "\\r\\n"',
        'FIN_DE_LIGNE_IMPORT = "\\n"',
        "le séparateur de lignes officiel du jeu ne serait pas respecté",
    ),
    (
        "export-sans-fin-de-ligne-finale",
        "src/filiales.py",
        "        for filiale in filiales\n    )\n\n\ndef total_frais",
        "        for filiale in filiales\n    ).removesuffix(FIN_DE_LIGNE_IMPORT)"
        "\n\n\ndef total_frais",
        "la dernière filiale serait perdue par un lecteur qui découpe sur le séparateur",
    ),
    # Le mutant passe par `__import__` parce que `src/filiales.py` **n'importe
    # pas** `format_money` — et c'est justement la garde qu'on éprouve : le cœur
    # pur ne connaît que la forme brute, celle que le jeu lit. Ce qui compte ici
    # est le comportement obtenu, un montant arrondi pour l'œil humain, pas la
    # façon contorsionnée de l'obtenir.
    (
        "export-montant-arrondi",
        "src/filiales.py",
        '        f"{format_money_brut(filiale.frais)}"',
        '        f"{__import__(\'src.money\', fromlist=[\'x\']).format_money(filiale.frais)}"',
        "on importerait « 189,74 TØ » au lieu du montant au chiffre près",
    ),
    (
        "export-benefices-au-lieu-des-frais",
        "src/filiales.py",
        "        f\"{format_money_brut(filiale.frais)}\"",
        "        f\"{format_money_brut(filiale.benefices)}\"",
        "on importerait ce qu'on gagne au lieu de ce qu'on doit, soit quatorze fois trop",
    ),
    (
        "export-perte-omise",
        "src/filiales.py",
        "        for filiale in filiales\n    )\n\n\ndef total_frais",
        "        for filiale in filiales\n        if not filiale.en_perte\n    )\n\n\ndef total_frais",
        "le tableau importé serait incomplet sans qu'on voie ce qui manque",
    ),
    (
        "export-nom-normalise",
        "src/filiales.py",
        '        f"{nom_pour_import(filiale.nom)}"',
        '        f\'{" ".join(nom_pour_import(filiale.nom).split())}\'',
        "les doubles espaces partiraient et la clé d'import du jeu ne correspondrait plus",
    ),
    (
        "export-nom-tab-non-neutralise",
        "src/filiales.py",
        '    propre = str(nom)\n    for casseur in',
        '    return str(nom)\n    propre = str(nom)\n    for casseur in',
        "un tab collé dans un nom mettrait deux tabs sur la ligne, refusée par le jeu",
    ),
    (
        "export-ordre-trie",
        "src/filiales.py",
        "        for filiale in filiales\n    )\n\n\ndef total_frais",
        "        for filiale in sorted(filiales, key=lambda f: -f.frais)\n    )\n\n\ndef total_frais",
        "deux exports des mêmes données différeraient dès qu'un montant bouge",
    ),
    (
        "bot-export-fichier-non-joint",
        "src/modules/filiales.py",
        "            file=fichier,\n            ephemeral=True,\n        )",
        "            ephemeral=True,\n        )",
        "la commande annoncerait un export sans rien rendre",
    ),
    (
        "bot-export-vide-annonce-un-fichier",
        "src/modules/filiales.py",
        "        filiales = await bot.store.filiales()\n        if not filiales:\n            # Pas de fichier vide",
        "        filiales = await bot.store.filiales()\n        if False:\n            # Pas de fichier vide",
        "un fichier de zéro octet se lirait comme une panne du bot",
    ),
    (
        "bot-export-nom-de-fichier-sans-date",
        "src/modules/filiales.py",
        'filename=f"frais-{await _aujourdhui(bot)}.txt",',
        'filename="frais.txt",',
        "deux exports d'affilée se confondraient dans le fil",
    ),
    (
        "bot-export-nom-deforme-en-silence",
        "src/modules/filiales.py",
        "        deformes = [\n            nom_pour_import(f.nom) for f in filiales if nom_pour_import(f.nom) != f.nom\n        ]",
        "        deformes = []",
        "un nom réécrit partirait sans un mot, et rien n'expliquerait le refus du jeu",
    ),

    # --- Le déménagement vers Supabase ---
    (
        "supabase-cache-statements-par-defaut",
        "src/db.py",
        "TAILLE_CACHE_STATEMENTS = 0",
        "TAILLE_CACHE_STATEMENTS = 100",
        "contre un pooler en mode transaction, la 2e requête tomberait -- en prod seulement",
    ),
    (
        "supabase-table-sans-rls",
        "src/db.py",
        "ALTER TABLE bot_state ENABLE ROW LEVEL SECURITY;\n",
        "",
        "salons, membres autorisés et template lisibles avec la clé anonyme publique",
    ),
    (
        "supabase-tout-liste-de-cles-en-dur",
        "src/db.py",
        "            return dict(self._memoire)",
        "            return {c: v for c, v in self._memoire.items() if c in ('config', 'template')}",
        "la clé ajoutée après resterait dans l'ancienne base",
    ),
    (
        "supabase-tout-avec-les-defauts",
        "src/db.py",
        "            return dict(self._memoire)",
        "            return {'config': await self.config(), **self._memoire}",
        "les défauts recopiés inventeraient à la cible une config plate à migrer",
    ),
    (
        "supabase-tout-postgres-une-seule-ligne",
        "src/db.py",
        'return {ligne["cle"]: json.loads(ligne["valeur"]) for ligne in lignes}',
        'return {ligne["cle"]: json.loads(ligne["valeur"]) for ligne in lignes[:1]}',
        "quatre clés sur cinq resteraient derrière",
    ),
    (
        "supabase-tout-postgres-sans-decodage",
        "src/db.py",
        'return {ligne["cle"]: json.loads(ligne["valeur"]) for ligne in lignes}',
        'return {ligne["cle"]: ligne["valeur"] for ligne in lignes}',
        "le JSON recopié comme texte : le bot relirait une config illisible",
    ),
    (
        "migration-cles-en-dur",
        "src/migration.py",
        "    etat = await source.tout()\n\n    deja = await cible.tout()",
        "    etat = {c: await source.get(c) for c in ('config', 'template', 'filiales')}"
        "\n\n    deja = await cible.tout()",
        "les marques de publication oubliées : le tableau du jour republié",
    ),
    (
        "migration-cible-non-vide-ecrasee",
        "src/migration.py",
        "    if deja and not forcer:",
        "    if False:",
        "la config d'un autre bot écrasée sans retour",
    ),
    (
        "migration-forcer-ignore",
        "src/migration.py",
        "    if deja and not forcer:",
        "    if deja:",
        "impossible de reprendre une copie interrompue sans vider la table à la main",
    ),
    (
        "migration-sans-relecture",
        "src/migration.py",
        "    return ecarts(etat, await cible.tout())",
        "    return []",
        "une base qui avale les écritures passerait pour un déménagement réussi",
    ),
    (
        "migration-source-videe",
        "src/migration.py",
        "    for cle, valeur in etat.items():\n        await cible.set(cle, valeur)",
        "    for cle, valeur in etat.items():\n        await cible.set(cle, valeur)\n        await source.set(cle, None)",
        "l'ancienne base perdue comme recours si la nouvelle se révèle inutilisable",
    ),
    (
        "migration-ecarts-presence-seule",
        "src/migration.py",
        "    return [cle for cle, valeur in source.items() if cible.get(cle) != valeur]",
        "    return [cle for cle in source if cle not in cible]",
        "une valeur tronquée passerait pour copiée",
    ),
    (
        "migration-ecarts-compte-le-surplus",
        "src/migration.py",
        "    return [cle for cle, valeur in source.items() if cible.get(cle) != valeur]",
        "    return [c for c in set(source) | set(cible) if source.get(c) != cible.get(c)]",
        "un forcer valide serait rapporté comme un échec",
    ),
    (
        "migration-mot-de-passe-affiche",
        "src/migration.py",
        "    if morceaux.password is None:",
        "    if True:",
        "le mot de passe de la base dans l'historique du terminal",
    ),
    (
        "migration-dsn-illisible-affiche",
        "src/migration.py",
        "    if not morceaux.netloc:\n        return \"***\"",
        "    if not morceaux.netloc:\n        return str(dsn)",
        "un secret affiché faute d'avoir su où il était",
    ),
    (
        "migration-meme-base-acceptee",
        "src/migration.py",
        "    if str(dsn_source).strip() == str(dsn_cible).strip():",
        "    if False:",
        "la base copiée sur elle-même, zéro écart, puis éteinte avec les données",
    ),
    (
        "migration-dsn-vide-accepte",
        "src/migration.py",
        '    if not str(dsn_source).strip():\n        raise MigrationError("Chaîne de connexion de départ vide.")',
        "    if False:\n        pass",
        "une copie vers la mémoire du processus, effacée à sa sortie",
    ),
]


def _lancer_suite() -> bool:
    """Vrai si la suite passe (donc si la mutation a survécu)."""
    resultat = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return resultat.returncode == 0


def main() -> int:
    # Sorties en ASCII pur : la console Windows est en cp1252, et un emoji dans
    # un `print` y lève un UnicodeEncodeError -- qui masquerait justement le
    # résultat qu'on est venu chercher.
    filtre = sys.argv[1] if len(sys.argv) > 1 else ""
    retenues = [m for m in MUTATIONS if filtre in m[0]]
    if not retenues:
        print(f"Aucune mutation ne correspond a « {filtre} ».".encode("ascii", "replace").decode())
        return 1

    survivantes: list[tuple[str, str]] = []

    for nom, fichier, origine, mutant, raison in retenues:
        chemin = RACINE / fichier
        avant = chemin.read_text(encoding="utf-8")

        occurrences = avant.count(origine)
        if occurrences != 1:
            print(f"[?] {nom} : motif trouve {occurrences} fois dans {fichier}, ignoree.")
            survivantes.append((nom, "motif introuvable ou ambigu"))
            continue

        chemin.write_text(avant.replace(origine, mutant), encoding="utf-8")
        try:
            survit = _lancer_suite()
        finally:
            # Restauré même sur Ctrl-C : laisser un fichier muté serait pire que
            # tout ce que ce script peut révéler.
            chemin.write_text(avant, encoding="utf-8")

        if survit:
            print(f"[SURVIT] {nom} -- {raison}")
            survivantes.append((nom, raison))
        else:
            print(f"[ok]     {nom}")

    print(f"\n{len(retenues) - len(survivantes)}/{len(retenues)} mutations détectées.")
    if survivantes:
        print("\nMutations survivantes (tests à renforcer) :")
        for nom, raison in survivantes:
            print(f"  - {nom} : {raison}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

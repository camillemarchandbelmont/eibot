"""Mutation testing manuel : chaque mutation doit faire échouer au moins un test.

Le principe : injecter un bug plausible dans le code de production, relancer la
suite, et vérifier qu'elle passe au rouge. Une mutation qui survit signale un
test décoratif — il exécute le code sans en vérifier le comportement.

Usage :
    python tests/mutations.py            # toutes
    python tests/mutations.py acces      # celles dont le nom contient 'acces'

Le fichier muté est restauré dans tous les cas, y compris si la suite plante ou
si l'on interrompt le script (Ctrl-C).
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
    # --- src/bot.py : la boucle de publication -----------------------------
    (
        "bot-publie-une-seule-fourchette",
        "src/bot.py",
        "        for fourchette in servies:",
        "        for fourchette in servies[:1]:",
        "seule la première fourchette serait publiée, sans erreur visible",
    ),
    (
        "bot-publie-partout-les-memes-promos",
        "src/bot.py",
        '                    Decimal(fourchette["prix_min"]),\n'
        '                    Decimal(fourchette["prix_max"]),\n'
        "                    donnees=donnees,",
        '                    Decimal(servies[0]["prix_min"]),\n'
        '                    Decimal(servies[0]["prix_max"]),\n'
        "                    donnees=donnees,",
        "chaque salon recevrait les promotions de la première fourchette",
    ),
    (
        "bot-diffuse-a-tous-les-salons",
        "src/bot.py",
        '            for salon_id in fourchette["salons"]:',
        '            for salon_id in [s for f in servies for s in f["salons"]]:',
        "un salon recevrait les promotions de toutes les fourchettes",
    ),
    (
        "bot-fourchette-sans-salon-publiee",
        "src/bot.py",
        '        servies = [f for f in fourchettes if f["salons"]]',
        "        servies = list(fourchettes)",
        "une fourchette sans salon ferait échouer un envoi à chaque passage",
    ),
    (
        "bot-export-recharge-par-fourchette",
        "src/bot.py",
        "                    donnees=donnees,\n"
        "                    tolere_min=tolere_min,\n"
        "                    tolere_max=tolere_max,\n"
        "                )\n"
        "            except Exception as erreur:",
        "                    donnees=await self.charger(),\n"
        "                    tolere_min=tolere_min,\n"
        "                    tolere_max=tolere_max,\n"
        "                )\n"
        "            except Exception as erreur:",
        "l'export serait téléchargé une fois par fourchette",
    ),
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
        "src/bot.py",
        'else builtins.max(Decimal(f["prix_max"]) for f in fourchettes)',
        'else Decimal(fourchettes[0]["prix_max"])',
        "`/promos` masquerait les promotions des autres fourchettes",
    ),
    (
        "bot-compte-rendu-dedouble-les-envois",
        "src/bot.py",
        '        total = sum(len(f["salons"]) for f in servies)',
        '        total = len({s for f in servies for s in f["salons"]})',
        "le compte rendu annoncerait moins d'envois qu'il n'en est parti",
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
        "bot-role-du-mauvais-serveur",
        "src/bot.py",
        "                        role_id = await self.store.role_du_serveur(\n"
        '                            getattr(serveur, "id", None)\n'
        "                        )",
        "                        role_id = next(\n"
        "                            iter((await self.store.roles()).values()), None\n"
        "                        )",
        "un salon mentionnerait le rôle d'un autre serveur (@deleted-role)",
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
        "bot-publication-ignore-la-tolerance",
        "src/bot.py",
        "                tolere_min, tolere_max = bornes_tolerees(fourchette)",
        "                tolere_min, tolere_max = None, None",
        "la zone n'agirait que sur l'aperçu, pas sur le post quotidien",
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
    (
        "money-convertir-unite-refusee",
        "src/money.py",
        "    if cible in (\"Ø\", \"O\", \"0\", \"\"):",
        "    if False:",
        "demander l'unité lèverait « symbole inconnu » alors que le jeu l'affiche",
    ),
    (
        "money-convertir-symbole-inconnu-accepte",
        "src/money.py",
        "    else:\n"
        "        raise MoneyError(\n"
        "            f\"Symbole monétaire inconnu : « {symbole} ». \"\n"
        "            f\"Symboles valides : {_symboles_valides()}.\"\n"
        "        )\n"
        "\n"
        "    valeur = Decimal(montant)",
        "    else:\n"
        "        exposant, affiche = 0, cible\n"
        "\n"
        "    valeur = Decimal(montant)",
        "« B » serait converti en silence au lieu de lister les symboles valides",
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
        "src/bot.py",
        "        frais = frais_de_gestion(valeur)",
        "        frais = valeur * Decimal(10) / Decimal(100)",
        "le message afficherait 7 % et le calcul en appliquerait un autre",
    ),
    (
        "bot-convertir-ignore-le-palier",
        "src/bot.py",
        "            rendu = convertir(valeur, vers)",
        "            rendu = format_money(valeur)",
        "/convertir rendrait le palier que le bot choisit, pas celui demandé",
    ),
    (
        "bot-convertir-ne-rappelle-pas-la-saisie",
        "src/bot.py",
        "            f\"**{format_money(valeur)}** = **{rendu}**\\n\"",
        "            f\"**{rendu}**\\n\"",
        "on ne pourrait plus vérifier que « 50 6P » a été lu comme 506 PØ",
    ),
    (
        "bot-frais-public",
        "src/bot.py",
        "            f\"-# {format_money_long(frais)}\",\n            ephemeral=True,",
        "            f\"-# {format_money_long(frais)}\",\n            ephemeral=False,",
        "un calcul personnel encombrerait le salon",
    ),
    (
        "bot-convertir-sans-l-unite",
        "src/bot.py",
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
    # --- src/bot.py : la saisie et la publication du tableau ---------------
    (
        "bot-frais-filiale-non-enregistree",
        "src/bot.py",
        "        if filiale is not None:\n            await _enregistrer_frais(interaction, valeur, filiale)\n            return",
        "        if False:\n            await _enregistrer_frais(interaction, valeur, filiale)\n            return",
        "la filiale saisie n'apparaîtrait jamais dans le tableau",
    ),
    (
        "bot-frais-calculette-enregistre",
        "src/bot.py",
        "        if filiale is not None:",
        "        if True:",
        "un simple calcul créerait une filiale nommée « None »",
    ),
    (
        "bot-frais-montant-illisible-enregistre-quand-meme",
        "src/bot.py",
        "            await interaction.response.send_message(\n                f\"❌ {erreur}\\n{_aide_montants()}\", ephemeral=True\n            )\n            return\n\n        if filiale is not None:",
        "            await interaction.response.send_message(\n                f\"❌ {erreur}\\n{_aide_montants()}\", ephemeral=True\n            )\n\n        if filiale is not None:",
        "une filiale serait retenue à un montant faux et fausserait le total",
    ),
    (
        "bot-frais-releve-public",
        "src/bot.py",
        "        await interaction.response.send_message(corps, ephemeral=True)",
        "        await interaction.response.send_message(corps, ephemeral=False)",
        "les résultats de l'entreprise s'afficheraient dans le salon",
    ),
    (
        "bot-frais-ressaisie-annoncee-comme-un-ajout",
        "src/bot.py",
        '        verbe = "mise à jour" if existait else "enregistrée"',
        '        verbe = "enregistrée"',
        "on ne saurait pas qu'on vient d'écraser un relevé",
    ),
    (
        "bot-frais-sans-alerte-de-salon-manquant",
        "src/bot.py",
        '        if not await bot.store.salons_filiales():\n            # Une saisie qui n\'ira nulle part doit se voir maintenant, pas au\n            # moment où l\'on s\'étonne de ne rien recevoir.\n            corps += "\\n⚠️ Aucun salon pour le tableau : `/filiales salon ajouter`."',
        "        pass",
        "on saisirait des relevés que personne ne recevrait",
    ),
    (
        "bot-tableau-publie-plusieurs-fois-par-jour",
        "src/bot.py",
        "        await self.store.marquer_publie_filiales(aujourdhui)",
        "        pass",
        "le cron reposterait le tableau à chaque appel",
    ),
    (
        "bot-tableau-marque-malgre-l-echec",
        "src/bot.py",
        "        if not reussis:\n            log.error(\"Tableau des frais échoué dans les %d envois.\", len(echecs))",
        "        if False:\n            log.error(\"Tableau des frais échoué dans les %d envois.\", len(echecs))",
        "une panne d'un instant annulerait le tableau de toute la journée",
    ),
    (
        "bot-tableau-a-l-heure-des-promotions",
        "src/bot.py",
        "            if not doit_publier(maintenant, await self.store.heure_filiales(), derniere):",
        '            if not doit_publier(maintenant, config["heure"], derniere):',
        "régler l'heure des promotions déplacerait le tableau",
    ),
    (
        "bot-tableau-dans-les-salons-des-promotions",
        "src/bot.py",
        "        salons = await self.store.salons_filiales()",
        "        salons = await self.store.salons()",
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
        "src/bot.py",
        "        config = await bot.store.maj_config(filiales_heure=heure_propre)",
        "        config = await bot.store.maj_config(heure=heure_propre)",
        "régler le tableau déplacerait le post des promotions",
    ),
    (
        "bot-filiales-heure-garde-la-marque-du-jour",
        "src/bot.py",
        "        await bot.store.oublier_publication_filiales()",
        "        pass",
        "le nouvel horaire serait bloqué jusqu'au lendemain",
    ),
    (
        "bot-filiales-heure-invalide-acceptee",
        "src/bot.py",
        "    if not (0 <= heures <= 23 and 0 <= minutes <= 59):\n        return None",
        "    if False:\n        return None",
        "« 25:00 » serait enregistrée et le post ne sortirait plus jamais",
    ),
    (
        "bot-filiales-salon-sans-permission",
        "src/bot.py",
        "        manquantes = _permissions_manquantes(interaction, salon)\n        if manquantes:\n            await interaction.response.send_message(\n                f\"❌ Je n'ai pas la permission {manquantes} dans {salon.mention}.\\n\"\n                f\"-# Ajoute-la puis relance la commande.\",\n                ephemeral=True,\n            )\n            return\n\n        if not await bot.store.ajouter_salon_filiales(str(salon.id)):",
        "        if not await bot.store.ajouter_salon_filiales(str(salon.id)):",
        "la permission manquante ne serait découverte qu'à l'heure du post",
    ),
    (
        "bot-apercu-filiales-consomme-le-post-du-jour",
        "src/bot.py",
        "        entete = f\"Tableau prévu à **{await bot.store.heure_filiales()}**\"",
        "        await bot.store.marquer_publie_filiales(aujourdhui)\n        entete = f\"Tableau prévu à **{await bot.store.heure_filiales()}**\"",
        "un aperçu empêcherait le tableau du jour de sortir",
    ),
    (
        "web-tick-sans-le-tableau",
        "src/web.py",
        "            resultat = await bot.publier_tout(forcer=forcer)",
        "            resultat = await bot.publier_si_lheure(forcer=forcer)",
        "sur Render, où le service dort, le tableau ne sortirait jamais",
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

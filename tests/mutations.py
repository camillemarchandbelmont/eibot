"""Mutation testing manuel : chaque mutation doit faire échouer au moins un test.

Le principe : injecter un bug plausible dans le code de production, relancer la
suite, et vérifier qu'elle passe au rouge. Une mutation qui survit signale un
test décoratif — il exécute le code sans en vérifier le comportement.

Usage :
    python -m tests.mutations            # toutes
    python -m tests.mutations acces      # celles dont le nom contient 'acces'

Le fichier muté est restauré dans tous les cas, y compris si la suite plante ou
si l'on interrompt le script (Ctrl-C).

Le nom d'une mutation dit où elle mord, donc quel lot la rejoue : `tournee-` la
mécanique d'envoi commune, `surface-` le vocabulaire des commandes, `bot-` les
commandes elles-mêmes et leurs modules, `cloisonnement-` la séparation des
serveurs, `reglages-` le noyau `src/reglages.py`, `activation-` l'allumage des
modules par serveur, `menu-` la liste des commandes propre à chaque serveur, le
reste le fichier de calcul visé.

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
    # --- src/db.py : une configuration par serveur -------------------------
    #
    # Le cloisonnement ne leve rien quand il fuit : le bot repond, publie, et
    # deux entreprises se marchent dessus en silence. Ce lot est donc le seul
    # juge -- un test qui lirait la vue sans verifier ce que voit l'autre
    # serveur passerait au vert sans rien prouver.
    (
        "cloisonnement-cle-non-prefixee",
        "src/db.py",
        '        return f"{PREFIXE_SERVEUR}:{self.serveur_id}:{cle}"',
        "        return cle",
        "tous les serveurs repartageraient une seule configuration",
    ),
    (
        "cloisonnement-meme-tiroir-pour-tous",
        "src/db.py",
        '        return f"{PREFIXE_SERVEUR}:{self.serveur_id}:{cle}"',
        '        return f"{PREFIXE_SERVEUR}:{cle}"',
        "le tiroir serait bien séparé du commun, mais commun à tous les serveurs",
    ),
    (
        "cloisonnement-repli-sur-la-config-commune",
        "src/db.py",
        "        return await self.commun.get(self._cle(cle), defaut)",
        "        return await self.commun.get(self._cle(cle), None) or await self.commun.get(cle, defaut)",
        "un serveur qui a supprimé ses fourchettes hériterait de celles du "
        "commun, et republierait ce qu'on venait de lui retirer",
    ),
    (
        "cloisonnement-ecriture-dans-le-commun",
        "src/db.py",
        "        await self.commun.set(self._cle(cle), valeur)",
        "        await self.commun.set(cle, valeur)",
        "régler un serveur écraserait la configuration que lit le site",
    ),
    (
        "cloisonnement-cache-des-salons-cloisonne",
        "src/db.py",
        "    async def salons_connus(self) -> dict[str, dict]:\n"
        "        return await self.commun.salons_connus()",
        "    async def salons_connus(self) -> dict[str, dict]:\n"
        "        return await Store.salons_connus(self)",
        "le site ne verrait plus qu'une moitié des noms de salons",
    ),
    (
        "cloisonnement-noms-de-serveurs-cloisonnes",
        "src/db.py",
        "    async def serveurs(self) -> dict[str, str]:\n"
        "        return await self.commun.serveurs()",
        "    async def serveurs(self) -> dict[str, str]:\n"
        "        return await Store.serveurs(self)",
        "le site afficherait un id de serveur nu là où il a un nom",
    ),
    (
        "cloisonnement-memorisation-cloisonnee",
        "src/db.py",
        "        await self.commun.memoriser_salon(salon_id, nom, serveur_id, serveur_nom)",
        "        await Store.memoriser_salon(self, salon_id, nom, serveur_id, serveur_nom)",
        "les noms de salons se rangeraient en double, hors de portée du site",
    ),
    (
        "cloisonnement-roles-cloisonnes",
        "src/db.py",
        "    async def roles(self) -> dict[str, str]:\n"
        "        return await self.commun.roles()",
        "    async def roles(self) -> dict[str, str]:\n"
        "        return await Store.roles(self)",
        "le rôle mentionné serait écrit d'un côté et lu de l'autre : plus "
        "personne ne serait prévenu",
    ),
    (
        "cloisonnement-persistant-toujours-en-memoire",
        "src/db.py",
        "        return self.commun.persistant",
        "        return False",
        "`/reglages voir` annoncerait une configuration perdue au redémarrage",
    ),
    (
        "cloisonnement-vue-se-connecte",
        "src/db.py",
        "    async def connect(self) -> None:\n        raise RuntimeError(",
        "    async def connect(self) -> None:\n        return None\n        raise RuntimeError(",
        "une vue ouvrirait un second pool, par serveur",
    ),
    (
        "cloisonnement-vue-ferme-la-base-des-autres",
        "src/db.py",
        "    async def close(self) -> None:\n        raise RuntimeError(",
        "    async def close(self) -> None:\n        return None\n        raise RuntimeError(",
        "fermer depuis un serveur couperait la base de tous les autres",
    ),
    (
        "cloisonnement-vue-de-vue-acceptee",
        "src/db.py",
        '    def pour(self, serveur_id: str | int) -> "VueServeur":\n'
        "        raise RuntimeError(",
        '    def pour(self, serveur_id: str | int) -> "VueServeur":\n'
        "        return VueServeur(self.commun, serveur_id)\n"
        "        raise RuntimeError(",
        "`pour(a).pour(b)` rendrait la vue de b sans dire que a est oublié",
    ),
    (
        "cloisonnement-tout-cloisonne",
        "src/db.py",
        "        return await self.commun.tout()",
        '        return {c: v for c, v in (await self.commun.tout()).items() '
        'if c.startswith(self._cle(""))}',
        "un déménagement de base ne recopierait qu'un serveur, et le manque ne "
        "se verrait qu'une fois l'ancienne base éteinte",
    ),
    (
        "cloisonnement-menage-decide-par-un-seul-serveur",
        "src/db.py",
        "        return await self.commun.oublier_salons_orphelins()",
        "        return await Store.oublier_salons_orphelins(self)",
        "le ménage écrirait dans le tiroir du serveur, et le cache commun "
        "grossirait sans fin",
    ),
    (
        "cloisonnement-menage-ne-regarde-quun-serveur",
        "src/db.py",
        "        servis = _salons_servis(await self.tout())",
        '        servis = _salons_servis({"config": await self.get("config", {})})',
        "retirer un salon dans un serveur effacerait les noms des salons de "
        "tous les autres",
    ),
    (
        "cloisonnement-menage-oublie-les-tiroirs-generiques",
        "src/db.py",
        '        if cle.endswith(":salons"):\n            ajouter(valeur)',
        "        if False:\n            ajouter(valeur)",
        "les salons d'une publication déclarée par un module seraient comptés "
        "orphelins",
    ),
    (
        "cloisonnement-menage-oublie-le-tableau-des-frais",
        "src/db.py",
        '            ajouter(valeur.get("filiales_salons"))',
        "            pass",
        "le salon du tableau des frais reperdrait son nom aussitôt mémorisé",
    ),
    (
        "cloisonnement-menage-oublie-les-fourchettes",
        "src/db.py",
        '                    ajouter(fourchette.get("salons"))',
        "                    pass",
        "le ménage effacerait le nom de tous les salons qui publient",
    ),
    (
        "cloisonnement-menage-oublie-la-config-plate",
        "src/db.py",
        '            ajouter(valeur.get("salons"))',
        "            pass",
        "un salon réglé avant le multi-fourchette perdrait son nom avant même "
        "que la migration ne le lise",
    ),
    (
        "cloisonnement-menage-oublie-le-salon-unique",
        "src/db.py",
        '            ajouter(valeur.get("salon_id"))',
        "            pass",
        "un salon réglé avant le multi-salon perdrait son nom de la même façon",
    ),
    (
        "cloisonnement-vierge-toujours-faux",
        "src/db.py",
        "        return not any(cle.startswith(prefixe) for cle in await self.commun.tout())",
        "        return False",
        "un serveur qui n'a rien réglé passerait pour réglé : plus aucun "
        "avertissement, et son silence se chercherait des jours",
    ),
    (
        "cloisonnement-vierge-toujours-vrai",
        "src/db.py",
        "        return not any(cle.startswith(prefixe) for cle in await self.commun.tout())",
        "        return True",
        "chaque serveur serait sommé d'importer sa configuration, celui qui vient "
        "de la régler à la main compris",
    ),
    (
        "cloisonnement-vierge-regarde-toute-la-base",
        "src/db.py",
        '        prefixe = self._cle("")',
        '        prefixe = ""',
        "la moindre clé commune ferait passer un serveur vierge pour réglé : "
        "l'avertissement ne sortirait jamais, sur aucun serveur",
    ),
    # --- src/bot.py : l'habillage d'un post, et l'aveu d'un serveur vierge ---
    #
    # Un template et un fuseau par serveur ne servent qu'a une chose : habiller
    # ce qui sort. Lus dans le commun, les deux commandes confirmeraient un
    # reglage qui ne changerait jamais rien a un seul post.
    (
        "cloisonnement-habillage-toujours-commun",
        "src/bot.py",
        "        magasin = self.store if magasin is None else magasin\n"
        "        meta, batiments = donnees if donnees is not None else await self.charger()",
        "        magasin = self.store\n"
        "        meta, batiments = donnees if donnees is not None else await self.charger()",
        "chaque post sortirait avec la charte et la date du commun : régler un "
        "template par serveur ne changerait jamais rien à ce qui part",
    ),
    (
        "cloisonnement-template-du-commun",
        "src/bot.py",
        "        modele = await magasin.template()",
        "        modele = await self.store.template()",
        "deux entreprises auraient la même charte, celle que personne n'a réglée",
    ),
    (
        "cloisonnement-date-du-fuseau-commun",
        "src/bot.py",
        '        date = maintenant_local((await magasin.config())["fuseau"]).strftime("%Y-%m-%d")',
        '        date = maintenant_local((await self.store.config())["fuseau"]).strftime("%Y-%m-%d")',
        "`{date}` daterait le post d'ailleurs : « post d'hier » un jour sur deux "
        "dans un serveur qui n'a pas le même décalage",
    ),
    (
        "cloisonnement-serveurs-vierges-non-signales",
        "src/bot.py",
        "        await self.signaler_les_serveurs_sans_configuration()",
        "        pass",
        "au déploiement chaque serveur se réveille vide : rien ne partirait plus "
        "nulle part, et le journal n'en dirait pas un mot",
    ),
    (
        "cloisonnement-modules-refuses-non-signales",
        "src/bot.py",
        "        await self.signaler_les_modules_refuses()",
        "        pass",
        "le signalement des modules écartés est branché au même endroit : "
        "éprouvé mais jamais appelé, il ne dirait jamais rien",
    ),
    (
        "cloisonnement-signalement-a-chaque-demarrage",
        "src/bot.py",
        "        if not vierges:\n            return",
        "        if False:\n            return",
        "un « 0 serveur sans configuration » à chaque démarrage apprendrait à ne "
        "plus lire ce salon, et le vrai signalement passerait avec le reste",
    ),
    (
        "cloisonnement-tous-les-serveurs-declares-vierges",
        "src/bot.py",
        "            if await self.store.pour(serveur.id).vierge()",
        "            if True",
        "un serveur déjà réglé serait sommé d'importer : on retaperait un import "
        "par-dessus une configuration correcte",
    ),
    (
        "cloisonnement-serveurs-vierges-non-nommes",
        "src/bot.py",
        '        lignes = "\\n".join(f"• {serveur.name} (`{serveur.id}`)" for serveur in vierges)',
        '        lignes = ""',
        "on saurait que des serveurs sont muets sans savoir lesquels : il "
        "faudrait taper `/reglages voir` dans chacun pour trouver",
    ),
    # --- src/tournee.py, src/bot.py : la tournée, une par serveur -----------
    #
    # Cloisonner le stockage ne sert a rien tant que la boucle d'envoi n'en
    # tient pas compte : chaque serveur publierait dans les salons de tous les
    # autres, deux messages par salon au lieu d'un. Compter les messages est le
    # seul vrai juge, et c'est ce que ces motifs obligent les tests a faire.
    (
        "cloisonnement-tournee-sans-garde",
        "src/tournee.py",
        '    serveur_id = getattr(magasin, "serveur_id", None)',
        "    serveur_id = None",
        "chaque serveur publierait dans les salons de tous les autres : deux "
        "messages par salon, et personne n'y verrait d'erreur",
    ),
    (
        "cloisonnement-garde-aussi-sur-la-config-commune",
        "src/tournee.py",
        '    if serveur_id is None:\n        return ""',
        '    if False:\n        return ""',
        "le site de contrôle ne dit pas de quel serveur il parle : tous ses "
        "salons seraient écartés, et il cesserait de publier sans rien annoncer",
    ),
    (
        "cloisonnement-salon-sans-serveur-laisse-passer",
        "src/tournee.py",
        '        return "salon hors serveur"',
        '        return ""',
        "« je n'ai pas pu vérifier » deviendrait « c'est bon », précisément "
        "dans le cas douteux",
    ),
    (
        "cloisonnement-garde-compare-un-id-a-un-texte",
        "src/tournee.py",
        "    if str(hote) == serveur_id:",
        "    if hote == serveur_id:",
        "l'id Discord est un int et le tiroir une chaîne : aucun salon ne "
        "collerait jamais, et plus rien ne partirait",
    ),
    (
        "cloisonnement-salon-etranger-servi-quand-meme",
        "src/tournee.py",
        "                etrangers[ou] = ailleurs\n                continue",
        "                etrangers[ou] = ailleurs",
        "le salon d'un autre serveur serait signalé dans le journal **et** "
        "servi : le pire des deux, une trace qui dit le contraire des faits",
    ),
    (
        "cloisonnement-salon-etranger-tu-dans-le-journal",
        "src/tournee.py",
        "        tournee.compte, reussis, {**echecs, **etrangers}, magasin=magasin",
        "        tournee.compte, reussis, echecs, magasin=magasin",
        "un salon resté muet sans trace ressemble à une panne : on chercherait "
        "un bug là où il n'y a qu'un id à corriger",
    ),
    (
        "cloisonnement-journal-sans-le-magasin",
        "src/tournee.py",
        "        tournee.compte, reussis, {**echecs, **etrangers}, magasin=magasin",
        "        tournee.compte, reussis, {**echecs, **etrangers}",
        "la tournée de chaque serveur serait racontée dans le salon de logs du "
        "commun : deux entreprises mêlées dans un même fil",
    ),
    (
        "cloisonnement-salon-etranger-bloque-la-journee",
        "src/tournee.py",
        "    if not reussis:\n        if echecs:",
        "    if not reussis:\n        if True:",
        "un id mal repris ferait réessayer toutes les cinq minutes, sans espoir "
        "et à 288 lignes de logs par jour",
    ),
    (
        "cloisonnement-panne-ne-laisse-plus-la-journee-a-faire",
        "src/tournee.py",
        "    if not reussis:\n        if echecs:",
        "    if not reussis:\n        if False:",
        "l'inverse : des permissions retirées une minute coûteraient le post du "
        "jour, la journée étant marquée sans que rien ne soit parti",
    ),
    (
        "cloisonnement-tour-sur-la-configuration-commune",
        "src/bot.py",
        "            magasin = self.store.pour(serveur.id)",
        "            magasin = self.store",
        "le tour quotidien relirait la configuration d'avant : une seule heure, "
        "une seule liste de salons, pour toutes les entreprises",
    ),
    (
        "cloisonnement-tour-du-premier-serveur-seul",
        "src/bot.py",
        "        for serveur in self.guilds:",
        "        for serveur in self.guilds[:1]:",
        "les entreprises suivantes ne publieraient plus rien, et leur silence "
        "ne se remarquerait que le lendemain",
    ),
    (
        "cloisonnement-fuseau-du-magasin-commun",
        "src/bot.py",
        "        config = await magasin.config()\n"
        '        maintenant = maintenant_local(config["fuseau"])',
        "        config = await self.store.config()\n"
        '        maintenant = maintenant_local(config["fuseau"])',
        "l'heure réglée dans un serveur serait lue dans le fuseau d'un autre : "
        "le post sortirait à côté, ou pas du tout",
    ),
    (
        "cloisonnement-publications-ecrites-en-dur",
        "src/bot.py",
        "            publications = self.publications(await magasin.modules_eteints())",
        "            publications = [module_promos.PUBLICATION, module_filiales.PUBLICATION]",
        "un module déclarant une troisième publication ne publierait rien, sans "
        "que rien ne le signale",
    ),
    (
        "cloisonnement-une-seule-publication-par-module",
        "src/bot.py",
        "            for publication in module.publications\n"
        "        ]",
        "            for publication in module.publications[:1]\n"
        "        ]",
        "un module qui déclare deux posts n'en sortirait qu'un : le plafond que "
        "le contrat de module est censé avoir levé",
    ),
    (
        "cloisonnement-tour-du-premier-module-seul",
        "src/bot.py",
        "            publication\n            for module in self.modules",
        "            publication\n            for module in self.modules[:1]",
        "le tableau des frais ne sortirait jamais : le tour ne demanderait ses "
        "publications qu'au premier module",
    ),
    (
        "cloisonnement-panne-dun-serveur-interrompt-le-tour",
        "src/bot.py",
        "                except Exception as erreur:",
        "                except ZeroDivisionError as erreur:",
        "un export du jeu illisible ferait taire un tableau qui n'en dépend "
        "pas, et toutes les entreprises suivantes avec lui : la boucle "
        "s'arrêterait avant de les atteindre",
    ),
    (
        "cloisonnement-journal-toujours-celui-du-commun",
        "src/bot.py",
        '            if getattr(magasin, "serveur_id", None) is not None',
        "            if False",
        "chaque serveur raconterait sa tournée dans le salon de logs commun, en "
        "y donnant les ids de salons des autres",
    ),
    (
        "cloisonnement-sans-serveur-rendu-vide",
        "src/bot.py",
        '            return "aucun serveur"',
        '            return ""',
        "une réponse vide au cron, toutes les cinq minutes, se lirait comme une "
        "panne du service",
    ),
    (
        "cloisonnement-serveur-non-nomme-dans-le-rendu",
        "src/bot.py",
        '                f"{serveur.name} — " + (',
        '                "" + (',
        "`/tick` répondrait deux fois « publié » sans dire dans quelle "
        "entreprise, donc sans permettre de trouver celle qui manque",
    ),
    (
        "cloisonnement-aucune-publication-passee-sous-silence",
        "src/bot.py",
        '" · ".join(rendus) or "aucune publication"',
        '" · ".join(rendus)',
        "un nom de serveur suivi de rien se lirait comme « tout va bien », "
        "alors que tous les modules ont pu être écartés au démarrage",
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
        # Le commentaire fait partie du motif : la marque est posée à deux
        # endroits depuis le cloisonnement (l'un pour les salons d'un autre
        # serveur), et la ligne seule serait trouvée deux fois -- donc ignorée.
        "    # là où ça avait marché.\n"
        '    await marquer_le_jour(publication, magasin, maintenant.strftime("%Y-%m-%d"))',
        "    # là où ça avait marché.\n    pass",
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
    # --- Le tiroir générique : l'aiguillage des six accès -------------------
    #
    # Chaque accès redirige vers ce que le module a déclaré, ou retombe sur
    # `publication:<clé>:…`. Les deux branches comptent, et pour des raisons
    # opposées : ignorer l'accès déclaré déplacerait les données des deux
    # publications historiques, ignorer le tiroir priverait toute publication
    # écrite ensuite de ses réglages.
    (
        "tournee-tiroir-ignore-l-heure-declaree",
        "src/tournee.py",
        "    if publication.lire_heure is not None:",
        "    if False:",
        "l'heure des promotions serait lue dans un tiroir vide, donc 09:00",
    ),
    (
        "tournee-tiroir-oublie-l-heure-rangee",
        "src/tournee.py",
        "    return await magasin.get(cle_heure(publication.cle))"
        " or publication.heure_par_defaut",
        "    return publication.heure_par_defaut",
        "`heure` confirmerait un réglage que la publication ne relirait jamais",
    ),
    (
        "tournee-tiroir-ignore-les-salons-declares",
        "src/tournee.py",
        "    if publication.lire_salons is not None:",
        "    if False:",
        "le tableau du soir partirait dans le vide, ses salons étant réglés ailleurs",
    ),
    (
        "tournee-tiroir-ignore-la-trace-declaree",
        "src/tournee.py",
        "    if publication.marquer is not None:",
        "    if False:",
        "la trace du tableau irait au tiroir, et le post repartirait à chaque cron",
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
        "                magasin=magasin,",
        "                donnees=await bot.charger(),\n"
        "                tolere_min=tolere_min,\n"
        "                tolere_max=tolere_max,\n"
        "                magasin=magasin,",
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
    (
        "bot-fourchette-salon-generique-greffe",
        "src/modules/promos.py",
        "    ajouter_les_commandes_de_publication(groupe, bot, PUBLICATION, salons=False)",
        "    ajouter_les_commandes_de_publication(groupe, bot, PUBLICATION)",
        "un `/promos salon ajouter` générique porterait le même nom que le "
        "vrai en écrivant ailleurs — un « ✅ » pour un post qui ne partirait "
        "nulle part",
    ),
    (
        "bot-post-quotidien-habille-par-le-commun",
        "src/modules/promos.py",
        "                tolere_max=tolere_max,\n                magasin=magasin,",
        "                tolere_max=tolere_max,",
        "le post du jour sortirait avec la charte et la date du commun : le "
        "template réglé dans un serveur ne se verrait jamais",
    ),
    (
        "bot-promos-habille-par-le-commun",
        "src/modules/promos.py",
        "                prix_min,\n                prix_max,\n                magasin=magasin,",
        "                prix_min,\n                prix_max,",
        "`/promos` répondrait avec la charte du commun : l'aperçu ne montrerait "
        "pas ce qui sortira le soir",
    ),
    # --- src/reglages.py : le noyau des réglages ---------------------------
    (
        "reglages-fuseau-inconnu-ecrit-quand-meme",
        "src/reglages.py",
        "            ZoneInfo(fuseau)",
        '            ZoneInfo("Europe/Paris")',
        "un fuseau inventé serait écrit tel quel, et chaque lecture de l'heure "
        "échouerait ensuite — donc les deux publications",
    ),
    (
        "reglages-fuseau-confirme-sans-ecrire",
        "src/reglages.py",
        "        config = await magasin.maj_config(fuseau=fuseau)",
        "        config = await magasin.config()",
        "la commande confirmerait un fuseau qu'elle n'a pas enregistré",
    ),
    (
        "reglages-fuseau-relance-les-publications",
        "src/reglages.py",
        "        config = await magasin.maj_config(fuseau=fuseau)",
        "        config = await magasin.maj_config(fuseau=fuseau)\n"
        "        await magasin.oublier_publication()\n"
        "        await magasin.marquer_publie_filiales(None)",
        "corriger l'horloge relancerait les deux posts du jour dans la minute, "
        "alors qu'on n'a rien demandé de tel",
    ),
    (
        "reglages-fuseau-regle-celui-du-commun",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        config = await magasin.maj_config(fuseau=fuseau)",
        "        magasin = bot.store\n"
        "        config = await magasin.maj_config(fuseau=fuseau)",
        "corriger l'horloge d'une entreprise déplacerait les posts de l'autre, et "
        "ne déplacerait pas les siens",
    ),
    (
        "reglages-fuseau-rappelle-lheure-du-commun",
        "src/reglages.py",
        'f"tableau des frais à {await magasin.heure_filiales()}.",',
        'f"tableau des frais à {await bot.store.heure_filiales()}.",',
        "la réponse ferait guetter le tableau du soir à l'heure du voisin",
    ),
    (
        "reglages-voir-montre-la-config-commune",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        config = await magasin.config()",
        "        magasin = bot.store\n"
        "        config = await magasin.config()",
        "`/reglages voir` afficherait l'heure d'avant le cloisonnement : on "
        "attendrait le post à un moment où il ne part pas",
    ),
    (
        "reglages-voir-fourchettes-du-commun",
        "src/reglages.py",
        "        fourchettes = await magasin.fourchettes()",
        "        fourchettes = await bot.store.fourchettes()",
        "les fourchettes du commun ne publient plus rien : les lister ici ferait "
        "croire ce serveur réglé, et cacherait qu'il n'a rien",
    ),
    (
        "reglages-voir-journal-du-commun",
        "src/reglages.py",
        "        logs = await magasin.salon_logs()",
        "        logs = await bot.store.salon_logs()",
        "on croirait le journal réglé ici alors que ce serveur ne raconte rien",
    ),
    (
        "reglages-voir-derniere-publication-du-commun",
        "src/reglages.py",
        "            f\"{await magasin.derniere_publication() or 'jamais'}\"",
        "            f\"{await bot.store.derniere_publication() or 'jamais'}\"",
        "le pied de l'embed dirait « jamais » à un serveur qui a publié ce matin, "
        "ou l'inverse — la seule ligne qu'on lit pour savoir si le bot travaille",
    ),
    (
        "reglages-voir-annonce-jamais-faite",
        "src/reglages.py",
        "            if await magasin.vierge()",
        "            if False",
        "un serveur qui n'a rien réglé est muet, et il n'y a pas de repli : sans "
        "cet aveu, son silence ressemble trait pour trait à une panne du bot",
    ),
    (
        "reglages-voir-annonce-toujours-faite",
        "src/reglages.py",
        "            if await magasin.vierge()",
        "            if True",
        "l'avertissement s'afficherait aussi à un serveur réglé : affiché "
        "toujours, il n'est plus lu, et le vrai passerait avec le reste",
    ),
    (
        "reglages-voir-repli-plat-perdu",
        "src/reglages.py",
        "        elif plat := await magasin.role_du_serveur(interaction.guild.id):",
        "        elif False:",
        "un rôle réglé avant le multi-serveurs disparaîtrait de l'affichage alors "
        "que le bot le pingue toujours à chaque post",
    ),
    (
        "reglages-logs-regle-le-journal-commun",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "\n"
        "        if salon is None:",
        "        magasin = bot.store\n"
        "\n"
        "        if salon is None:",
        "un compte rendu de tournée nomme des salons : les deux entreprises se "
        "raconteraient dans un même fil, chacune recevant les ids de l'autre",
    ),
    (
        "reglages-template-charge-dans-le-commun",
        "src/reglages.py",
        "        await interaction.response.defer(ephemeral=True)\n"
        "        magasin = pour_ce_serveur(bot, interaction)",
        "        await interaction.response.defer(ephemeral=True)\n"
        "        magasin = bot.store",
        "charger sa charte écraserait celle du voisin, sans rien changer à ses "
        "propres posts",
    ),
    (
        "reglages-template-apercu-du-voisin",
        "src/reglages.py",
        '                Decimal(config["prix_max"]),\n'
        "                magasin=magasin,",
        '                Decimal(config["prix_max"]),',
        "la commande confirmerait un template en montrant celui du commun : "
        "l'aperçu est le seul moyen de voir ce qu'on vient d'enregistrer",
    ),
    (
        "reglages-template-voir-celui-du-commun",
        "src/reglages.py",
        "        modele = await pour_ce_serveur(bot, interaction).template()",
        "        modele = await bot.store.template()",
        "on repartirait d'un fichier qui n'est pas le sien pour retoucher sa charte",
    ),
    (
        "reglages-groupe-jamais-greffe",
        "src/reglages.py",
        "    bot.tree.add_command(groupe)",
        "    pass",
        "le tiroir entier disparaîtrait du menu : plus rien pour régler le bot, "
        "ni pour rendre la main",
    ),
    (
        "reglages-source-reste-a-la-racine",
        "src/reglages.py",
        '        description="Provenance des données (API du jeu ou fichier)",\n'
        "        parent=groupe,",
        '        description="Provenance des données (API du jeu ou fichier)",\n'
        "        parent=None,",
        "`/source` repartirait à la racine, à côté de `/reglages` : deux portes "
        "pour la même pièce, ce qu'on vient de défaire",
    ),
    (
        "reglages-acces-ajouter-sans-garde-admin",
        "src/reglages.py",
        "    async def acces_ajouter(interaction: discord.Interaction, membre: discord.Member):\n"
        "        if not administrateur(interaction):",
        "    async def acces_ajouter(interaction: discord.Interaction, membre: discord.Member):\n"
        "        if False:",
        "un membre simplement autorisé pourrait s'ajouter des complices",
    ),
    (
        "reglages-acces-retirer-sans-garde-admin",
        "src/reglages.py",
        "    async def acces_retirer(interaction: discord.Interaction, membre: discord.Member):\n"
        "        if not administrateur(interaction):",
        "    async def acces_retirer(interaction: discord.Interaction, membre: discord.Member):\n"
        "        if False:",
        "un membre autorisé pourrait mettre dehors celui qui l'a nommé",
    ),

    # --- src/bot.py, src/reglages.py : la liste d'accès par serveur ---------
    #
    # Le réglage le plus lourd du lot : une seule liste pour tous les serveurs
    # voulait dire qu'inviter le bot ailleurs donnait les clés de toutes les
    # entreprises. Le gardien et les trois commandes forment un tout — la liste
    # écrite d'un côté doit être celle que l'autre relit, sinon plus personne
    # n'entre, ou n'importe qui entre partout.
    (
        "cloisonnement-acces-gardien-lit-la-liste-commune",
        "src/bot.py",
        "            autorises=await magasin.autorises(),",
        "            autorises=await self.store.autorises(),",
        "un membre autorisé par une entreprise se servirait des commandes de "
        "toutes les autres, et sa propre liste n'ouvrirait rien",
    ),
    (
        "cloisonnement-acces-gardien-toujours-sur-le-commun",
        "src/bot.py",
        "        magasin = self.store.pour(serveur.id) if serveur else self.store",
        "        magasin = self.store",
        "même chose, prise par l'autre bout : le serveur où l'on tape ne "
        "choisirait plus la liste",
    ),
    (
        "cloisonnement-acces-gardien-sans-repli-hors-serveur",
        "src/bot.py",
        "        magasin = self.store.pour(serveur.id) if serveur else self.store",
        "        magasin = self.store.pour(serveur.id)",
        "en message privé le gardien lèverait avant chaque commande : le bot "
        "répondrait « une erreur est survenue » à tout au lieu de refuser",
    ),
    (
        "cloisonnement-acces-ajouter-dans-le-commun",
        "src/reglages.py",
        "        if not await pour_ce_serveur(bot, interaction).autoriser(str(membre.id)):",
        "        if not await bot.store.autoriser(str(membre.id)):",
        "le « ✅ » n'ouvrirait rien, et le membre ajouté serait refusé par le "
        "gardien qui lit la liste de ce serveur",
    ),
    (
        "cloisonnement-acces-retirer-dans-le-commun",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        if not await magasin.retirer_autorise(str(membre.id)):",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        if not await bot.store.retirer_autorise(str(membre.id)):",
        "mettre quelqu'un dehors répondrait « il n'était pas dans la liste » et "
        "le laisserait entrer",
    ),
    (
        "cloisonnement-acces-liste-du-commun",
        "src/reglages.py",
        "        autorises = await pour_ce_serveur(bot, interaction).autorises()",
        "        autorises = await bot.store.autorises()",
        "la liste affichée ne serait pas celle que le gardien applique : on "
        "chercherait pourquoi les membres cités sont refusés",
    ),

    # --- src/db.py, src/bot.py : allumer les modules par serveur -------------
    #
    # La base retient les **éteints**, pour que tout soit allumé par défaut et
    # qu'un module arrivé par un déploiement le soit partout. Chaque mutation
    # ci-dessous inverse ou perd cette liste : le symptôme est soit un serveur
    # muet sans raison, soit un post qu'on croyait avoir éteint et qui retombe
    # chaque jour.
    (
        "activation-tout-eteint-par-defaut",
        "src/db.py",
        "        return nom not in await self.modules_eteints()",
        "        return nom in await self.modules_eteints()",
        "un serveur neuf n'aurait aucun module : muet au démarrage, et sans que "
        "rien ne dise quoi rallumer",
    ),
    (
        "activation-extinction-non-enregistree",
        "src/db.py",
        "        await self.maj_config(modules_eteints=sorted([*eteints, nom]))\n"
        "        return True",
        "        return True",
        "`desactiver` répondrait « ✅ » et le module publierait le soir même",
    ),
    (
        "activation-deja-eteint-non-signale",
        "src/db.py",
        "        eteints = await self.modules_eteints()\n"
        "        if nom in eteints:\n"
        "            return False",
        "        eteints = await self.modules_eteints()\n"
        "        if False:\n"
        "            return False",
        "« ✅ éteint » sur un module qui l'était déjà ferait croire qu'on vient "
        "de changer quelque chose",
    ),
    (
        "activation-rallumer-nefface-rien",
        "src/db.py",
        "        await self.maj_config(modules_eteints=[n for n in eteints if n != nom])",
        "        await self.maj_config(modules_eteints=eteints)",
        "`activer` répondrait « ✅ » sur un module qui resterait éteint",
    ),
    (
        "activation-liste-videe-non-enregistree",
        "src/db.py",
        "        await self.maj_config(modules_eteints=[n for n in eteints if n != nom])\n"
        "        return True",
        "        await self.maj_config(\n"
        "            modules_eteints=[n for n in eteints if n != nom] or None\n"
        "        )\n"
        "        return True",
        "l'économie qui se fait toute seule en relisant le code : le dernier "
        "module rallumé le resterait jusqu'au redémarrage, puis s'éteindrait",
    ),
    (
        "activation-eteint-mais-publie-encore",
        "src/bot.py",
        "            publications = self.publications(await magasin.modules_eteints())",
        "            publications = self.publications()",
        "`desactiver` ne retirerait que les commandes du menu, et le post "
        "continuerait de tomber chaque jour",
    ),
    (
        "activation-filtre-inverse",
        "src/bot.py",
        "            if module.nom not in exclus",
        "            if module.nom in exclus",
        "seuls les modules éteints publieraient : un serveur qui n'a rien éteint "
        "deviendrait muet",
    ),
    # `/reglages modules` est le seul chemin pour allumer et éteindre : sans ces
    # commandes il faudrait écrire dans la base à la main.
    (
        "activation-liste-sans-etat",
        "src/reglages.py",
        '                    f"⛔ `{module.nom}` — {module.titre} *(éteint)*"\n'
        "                    if module.nom in eteints\n"
        '                    else f"✅ `{module.nom}` — {module.titre}"',
        '                    f"✅ `{module.nom}` — {module.titre}"',
        "la liste ne répondrait plus à la seule question qu'on lui pose : "
        "est-ce que le tableau du soir va sortir ce soir ?",
    ),
    (
        "activation-liste-du-commun",
        "src/reglages.py",
        "        eteints = await pour_ce_serveur(bot, interaction).modules_eteints()\n"
        "        embed = discord.Embed(",
        "        eteints = await bot.store.modules_eteints()\n"
        "        embed = discord.Embed(",
        "la liste montrerait l'état d'un autre serveur : on chercherait ici la "
        "panne d'ailleurs",
    ),
    (
        "activation-liste-cache-les-refuses",
        "src/reglages.py",
        "        if bot.modules_refuses:",
        "        if False:",
        "un module cassé au démarrage se lirait comme un module jamais déployé, "
        "et on chercherait la panne dans le dépôt",
    ),
    (
        "activation-nom-inconnu-sans-les-noms",
        "src/reglages.py",
        '        noms = ", ".join(f"`{module.nom}`" for module in bot.modules) or "*aucun*"',
        '        noms = "*aucun*"',
        "un nom mal tapé serait refusé sans dire lesquels existent",
    ),
    (
        "activation-desactiver-nom-inconnu-accepte",
        "src/reglages.py",
        "        trouve = module_nomme(module)\n"
        "        if trouve is None:\n"
        "            await refuser_module_inconnu(interaction, module)\n"
        "            return\n"
        "\n"
        "        magasin = pour_ce_serveur(bot, interaction)",
        "        trouve = module_nomme(module)\n"
        "        magasin = pour_ce_serveur(bot, interaction)",
        "un nom mal tapé écrirait dans les éteints un module qui n'existe pas, "
        "et rien ne le rallumerait jamais",
    ),
    (
        "activation-desactiver-dans-le-commun",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        eteints = await magasin.modules_eteints()",
        "        magasin = bot.store\n"
        "        eteints = await magasin.modules_eteints()",
        "éteindre chez soi éteindrait ailleurs — ou plutôt nulle part, la "
        "tournée ne lisant plus la configuration commune",
    ),
    (
        "activation-desactiver-nenregistre-rien",
        "src/reglages.py",
        "        await magasin.eteindre_module(trouve.nom)",
        "        pass",
        "« ✅ éteint » sur un module qui publierait le soir même",
    ),
    (
        "activation-deja-eteint-non-dit",
        "src/reglages.py",
        "        if trouve.nom in eteints:",
        "        if False:",
        "un « ✅ » ferait croire qu'on vient de changer quelque chose, et "
        "chercher ailleurs la raison d'un post qui sort encore",
    ),
    (
        "activation-dernier-module-eteignable",
        "src/reglages.py",
        "        if len(allumes) <= 1:",
        "        if False:",
        "un serveur sans aucun module ne répondrait plus qu'à `/reglages`, sans "
        "que rien ne distingue ce réglage d'une panne",
    ),
    (
        "activation-dernier-module-mal-compte",
        "src/reglages.py",
        "        allumes = [m for m in bot.modules if m.nom not in eteints]",
        "        allumes = list(bot.modules)",
        "la garde compterait les éteints parmi les allumés : le dernier "
        "s'éteindrait quand même",
    ),
    (
        "activation-activer-nenregistre-rien",
        "src/reglages.py",
        "        if not await pour_ce_serveur(bot, interaction).rallumer_module(trouve.nom):",
        "        if False:",
        "« ✅ allumé » sur un module qui resterait éteint",
    ),
    (
        "activation-activer-dans-le-commun",
        "src/reglages.py",
        "        if not await pour_ce_serveur(bot, interaction).rallumer_module(trouve.nom):",
        "        if not await bot.store.rallumer_module(trouve.nom):",
        "rallumer dans un serveur ne rallumerait rien : la tournée ne lit plus "
        "la configuration commune",
    ),
    (
        "activation-propositions-allumes-inversees",
        "src/reglages.py",
        "        return _choix(bot.modules, saisie, lambda module: module.nom not in eteints)",
        "        return _choix(bot.modules, saisie, lambda module: module.nom in eteints)",
        "`desactiver` proposerait les modules déjà éteints : on choisirait un nom "
        "pour s'entendre répondre qu'il n'y avait rien à faire",
    ),
    (
        "activation-propositions-eteints-inversees",
        "src/reglages.py",
        "        return _choix(bot.modules, saisie, lambda module: module.nom in eteints)",
        "        return _choix(bot.modules, saisie, lambda module: module.nom not in eteints)",
        "`activer` proposerait les modules déjà allumés",
    ),
    (
        "activation-propositions-du-commun",
        "src/reglages.py",
        "        eteints = await pour_ce_serveur(bot, interaction).modules_eteints()\n"
        "        return _choix(bot.modules, saisie, lambda module: module.nom in eteints)",
        "        eteints = await bot.store.modules_eteints()\n"
        "        return _choix(bot.modules, saisie, lambda module: module.nom in eteints)",
        "les propositions viendraient d'un autre serveur : on se verrait offrir "
        "de rallumer ce que le voisin a éteint",
    ),
    # --- src/modules/__init__.py, src/bot.py : le menu de chaque serveur -----
    #
    # Un module éteint doit quitter le menu de son serveur. Deux verrous, et il
    # en faut deux : Discord garde la liste des commandes en cache chez le
    # client, et sans GUILD_IDS la synchronisation est globale — il n'y a alors
    # pas de menu par serveur du tout.
    #
    # Tout repose sur le relevé pris à la greffe, qui dit quelle commande
    # appartient à quel module. Faux, il retire du menu les commandes d'un
    # module qu'on n'a pas éteint.
    (
        "menu-greffe-attribution-inversee",
        "src/modules/__init__.py",
        "            nom for nom in _noms_a_la_racine(bot) if nom not in avant",
        "            nom for nom in _noms_a_la_racine(bot) if nom in avant",
        "chaque module se verrait attribuer les commandes des précédents : "
        "éteindre `filiales` retirerait `/convertir` du menu",
    ),
    (
        "menu-greffe-releve-sans-avant",
        "src/modules/__init__.py",
        "        avant = _noms_a_la_racine(bot)",
        "        avant = ()",
        "un module hériterait des commandes de tous ceux greffés avant lui : "
        "l'éteindre en retirerait bien plus que les siennes",
    ),
    (
        "menu-greffe-oublie-la-commande-de-lechec",
        "src/modules/__init__.py",
        '            refuses[module.nom] = f"{type(erreur).__name__} : {erreur}"',
        '            refuses[module.nom] = f"{type(erreur).__name__} : {erreur}"\n'
        "            continue",
        "la commande posée avant l'échec resterait dans le menu de tous les "
        "serveurs sans que rien ne puisse l'en retirer",
    ),
    (
        "menu-greffe-releve-un-module-sans-commande",
        "src/modules/__init__.py",
        "        if posees:\n            commandes[module.nom] = posees",
        "        commandes[module.nom] = posees",
        "un module qui ne pose rien à la racine entrerait dans le relevé les "
        "mains vides, comme s'il avait une commande à éteindre",
    ),
    (
        "menu-filtre-inverse",
        "src/bot.py",
        '            if getattr(self.module_des_commandes.get(commande.name), "nom", None)\n'
        "            not in exclus",
        '            if getattr(self.module_des_commandes.get(commande.name), "nom", None)\n'
        "            in exclus",
        "le menu ne montrerait **que** les modules éteints",
    ),
    (
        "menu-rien-nest-exclu",
        "src/bot.py",
        "        arriver, un module retiré du dépôt ou un tiroir repris à la main.\n"
        '        """\n'
        "        exclus = set(eteints)",
        "        arriver, un module retiré du dépôt ou un tiroir repris à la main.\n"
        '        """\n'
        "        exclus = set()",
        "`desactiver` semblerait sans effet : la commande resterait dans le menu",
    ),
    (
        "menu-eteints-du-commun",
        "src/bot.py",
        "        eteints = await self.store.pour(serveur_id).modules_eteints()",
        "        eteints = await self.store.modules_eteints()",
        "chaque serveur recevrait le même menu : ce qu'un serveur éteint "
        "resterait dans son menu, et l'extinction du voisin l'en priverait",
    ),
    (
        "menu-vide-larbre-global",
        "src/bot.py",
        "        self.tree.clear_commands(guild=guild)",
        "        self.tree.clear_commands(guild=None)",
        "le menu d'un serveur est une copie : vider l'arbre global ferait "
        "disparaître la commande de **tous** les serveurs",
    ),
    (
        "menu-complete-au-lieu-de-reconstruire",
        "src/bot.py",
        "        self.tree.clear_commands(guild=guild)",
        "        pass  # sans vider d'abord",
        "le menu serait complété et non rebâti : la commande d'un module qu'on "
        "vient d'éteindre y resterait, et Discord refuserait le doublon",
    ),
    (
        "menu-construit-mais-non-pousse",
        "src/bot.py",
        "            await self.tree.sync(guild=guild)",
        "            pass  # sans pousser",
        "le menu serait bâti et jamais envoyé : rien ne changerait dans Discord",
    ),
    (
        "menu-echec-de-poussee-tu",
        "src/bot.py",
        "                exc_info=True,\n            )\n            return False\n        return True",
        "                exc_info=True,\n            )\n            return True\n        return True",
        "un menu resté en arrière serait annoncé comme rafraîchi",
    ),
    (
        "menu-echec-de-poussee-fatal",
        "src/bot.py",
        "        except Exception:\n            log.warning(\n"
        '                "Menu du serveur %s construit mais non synchronisé.",',
        "        except ValueError:\n            log.warning(\n"
        '                "Menu du serveur %s construit mais non synchronisé.",',
        "une synchronisation refusée par Discord — la limite de débit — ferait "
        "échouer la commande alors que le réglage est déjà écrit",
    ),
    (
        "menu-un-seul-serveur-synchronise",
        "src/bot.py",
        "        for serveur_id in serveurs_ids:\n"
        "            await self.synchroniser_le_menu(serveur_id)",
        "        for serveur_id in list(serveurs_ids)[:1]:\n"
        "            await self.synchroniser_le_menu(serveur_id)",
        "seul le premier serveur recevrait son menu ; les autres garderaient "
        "celui du démarrage précédent",
    ),
    # Le second verrou, dans `ArbreProtege` : ce que le menu ne peut pas faire.
    (
        "menu-verrou-sous-commande-non-rattachee",
        "src/bot.py",
        '        racine = getattr(commande, "root_parent", None) or commande',
        "        racine = commande",
        "`/frais liste` passerait alors que `/frais` est éteint : c'est la "
        "racine qui appartient au module, pas la sous-commande",
    ),
    (
        "menu-verrou-commande-racine-ignoree",
        "src/bot.py",
        '        racine = getattr(commande, "root_parent", None) or commande',
        '        racine = getattr(commande, "root_parent", None)',
        "`/frais`, qui n'est dans aucun groupe, ferait lever le gardien : la "
        "commande échouerait au lieu d'être refusée ou acceptée",
    ),
    (
        "menu-verrou-refuse-reglages",
        "src/bot.py",
        "        module = self.client.module_des_commandes.get(racine.name)\n"
        "        if module is None:\n            return True",
        "        module = self.client.module_des_commandes.get(racine.name)\n"
        "        if module is None:\n            return False",
        "`/reglages` serait refusé : la seule porte de sortie fermée, plus rien "
        "ne pourrait rallumer un module",
    ),
    (
        "menu-verrou-refuse-en-message-prive",
        "src/bot.py",
        "        if serveur is None or commande is None:\n            return True",
        "        if serveur is None or commande is None:\n            return False",
        "hors d'un serveur — un message privé — toute commande serait refusée",
    ),
    (
        "menu-verrou-etat-du-commun",
        "src/bot.py",
        "        if await self.store.pour(serveur.id).module_actif(module.nom):",
        "        if await self.store.module_actif(module.nom):",
        "le refus viendrait de la configuration commune : un module éteint dans "
        "un serveur y resterait utilisable",
    ),
    (
        "menu-verrou-jamais-consulte",
        "src/bot.py",
        "        if not await self.autorisation(interaction):\n"
        "            return False\n"
        "        return await self.module_allume(interaction)",
        "        if not await self.autorisation(interaction):\n"
        "            return False\n"
        "        return True",
        "le second verrou serait mort : la commande d'un module éteint resterait "
        "utilisable, cache de Discord ou synchronisation globale",
    ),
    (
        "menu-verrou-remplace-lacces",
        "src/bot.py",
        "        if not await self.autorisation(interaction):\n"
        "            return False\n"
        "        return await self.module_allume(interaction)",
        "        return await self.module_allume(interaction)",
        "contrôler les modules aurait remplacé le contrôle de la liste d'accès : "
        "n'importe qui commanderait le bot",
    ),
    # Le rafraîchissement immédiat : sans lui, l'extinction n'a l'air de rien.
    (
        "menu-activer-ne-rafraichit-pas",
        "src/reglages.py",
        '            message += "\\n-# Ses publications repartiront à leur heure."\n'
        "        message += await rafraichir_le_menu(interaction, trouve)",
        '            message += "\\n-# Ses publications repartiront à leur heure."',
        "le module rallumé ne reviendrait dans le menu qu'au prochain "
        "déploiement",
    ),
    (
        "menu-desactiver-ne-rafraichit-pas",
        "src/reglages.py",
        '        message += "\\n-# `/reglages modules activer` le rallume."\n'
        "        message += await rafraichir_le_menu(interaction, trouve)",
        '        message += "\\n-# `/reglages modules activer` le rallume."',
        "la commande éteinte resterait dans le menu, et `desactiver` se lirait "
        "comme un réglage sans effet",
    ),
    (
        "menu-echec-de-rafraichissement-tu",
        "src/reglages.py",
        "        if await bot.synchroniser_le_menu(interaction.guild.id):\n"
        '            return ""',
        "        await bot.synchroniser_le_menu(interaction.guild.id)\n"
        '        return ""',
        "un menu resté en arrière ne serait pas avoué : on retaperait la "
        "commande, alors que le réglage est déjà pris",
    ),
    # --- src/importation.py, src/reglages.py : reprendre l'ancienne config ---
    #
    # Le cloisonnement n'a pas de repli : au déploiement, chaque serveur se
    # réveille vide, et `/reglages importer` est le seul chemin de retour. Un bug
    # ici se paie deux fois — soit deux ans de réglages à ressaisir à la main,
    # soit chaque serveur publiant dans les salons de tous les autres.
    #
    # Les deux fichiers sont dans le même lot (`import` les rejoue tous) : le
    # calcul et son raccordement se cassent des deux côtés pour le même effet
    # visible.
    (
        "importation-salon-etranger-repris",
        "src/importation.py",
        "            if str(salon) in salons_du_serveur:",
        "            if True:",
        "chaque serveur publierait dans les salons de tous les autres, et le "
        "compte rendu n'écarterait rien",
    ),
    (
        "importation-salon-compare-sans-texte",
        "src/importation.py",
        "            if str(salon) in salons_du_serveur:",
        "            if salon in salons_du_serveur:",
        "un salon écrit en nombre dans le JSON passerait pour celui d'un autre "
        "serveur et serait écarté — le serveur ne publierait plus nulle part",
    ),
    (
        "importation-salons-de-la-config-non-filtres",
        "src/importation.py",
        "        for champ in _CHAMPS_SALONS:\n"
        "            if champ in config:\n"
        "                config[champ] = trier(config[champ])",
        "        for champ in ():\n"
        "            if champ in config:\n"
        "                config[champ] = trier(config[champ])",
        "les salons du voisin arriveraient dans `salons` et `filiales_salons`",
    ),
    (
        "importation-fourchettes-non-filtrees",
        "src/importation.py",
        '                {**f, "salons": trier(f.get("salons"))} if isinstance(f, dict) else f',
        "                f if isinstance(f, dict) else f",
        "les salons rangés sous une fourchette échapperaient au tri : c'est là "
        "que vivent tous les salons des promotions",
    ),
    (
        "importation-fourchette-vide-disparait",
        "src/importation.py",
        '                {**f, "salons": trier(f.get("salons"))} if isinstance(f, dict) else f\n'
        "                for f in fourchettes",
        '                {**f, "salons": trier(f.get("salons"))} if isinstance(f, dict) else f\n'
        "                for f in fourchettes\n"
        "                if not isinstance(f, dict) or trier(f.get(\"salons\"))",
        "une fourchette dont tous les salons étaient chez le voisin disparaîtrait "
        "avec ses bornes, qu'il faudrait ressaisir alors qu'il n'y a qu'un salon "
        "à corriger",
    ),
    (
        "importation-salon-unique-garde-quand-meme",
        "src/importation.py",
        "            if config.get(champ) and not trier([config[champ]]):\n"
        "                config.pop(champ)",
        "            if False:\n"
        "                config.pop(champ)",
        "`salon_id` et `logs_salon_id` désigneraient un salon de l'autre serveur : "
        "le journal y raconterait la tournée, avec les ids d'ici",
    ),
    (
        "importation-salon-unique-vide-au-lieu-detre-retire",
        "src/importation.py",
        "                config.pop(champ)",
        "                config[champ] = None",
        "un `salon_id` vide reste la signature d'une config plate : le serveur se "
        "croirait à migrer",
    ),
    (
        "importation-tiroir-de-publication-non-filtre",
        "src/importation.py",
        '    if cle.endswith(":salons"):\n        return trier(valeur)',
        '    if False:\n        return trier(valeur)',
        "les salons d'une publication déclarée par un module passeraient sans tri",
    ),
    (
        "importation-tiroirs-des-serveurs-repris",
        "src/importation.py",
        '        if cle.startswith(f"{PREFIXE_SERVEUR}:"):\n            continue',
        "        if False:\n            continue",
        "un serveur recevrait les réglages du voisin, et un tiroir se retrouverait "
        "enfermé dans un tiroir",
    ),
    (
        "importation-ecrase-ce-qui-est-deja-regle",
        "src/importation.py",
        "        if cle in deja:\n            laissees.append(cle)\n            continue",
        "        if False:\n            laissees.append(cle)\n            continue",
        "un import de trop ramènerait l'ancienne heure par-dessus celle qu'on "
        "vient de régler à la main",
    ),
    (
        "importation-deja-regle-cherche-la-cle-prefixee",
        "src/importation.py",
        "    deja = {cle[len(prefixe):] for cle in base if cle.startswith(prefixe)}",
        "    deja = {cle for cle in base if cle.startswith(prefixe)}",
        "aucune clé ne serait jamais reconnue comme déjà réglée : tout serait "
        "écrasé en annonçant le contraire",
    ),
    (
        "importation-cache-des-noms-recopie",
        "src/importation.py",
        "        config = {c: v for c, v in valeur.items() if c not in CLES_COMMUNES}",
        "        config = dict(valeur)",
        "le cache des noms et la table des mentions dormiraient dans le tiroir "
        "sans lecteur, et aucun ménage ne viendrait les y nettoyer",
    ),
    (
        "importation-seules-les-cles-connues-reprises",
        "src/importation.py",
        "        a_ecrire[cle] = _cloisonner(cle, valeur, trier)",
        '        if cle in ("config", "template"):\n'
        "            a_ecrire[cle] = _cloisonner(cle, valeur, trier)",
        "les relevés des filiales, les marques du jour et le tiroir d'un module à "
        "venir seraient oubliés en silence",
    ),
    (
        "importation-cle-connue-non-nommee",
        "src/importation.py",
        "    if cle in connues:\n        return connues[cle]",
        "    if False:\n        return connues[cle]",
        "le compte rendu parlerait en noms de clés de base, illisibles pour qui "
        "n'a jamais vu le stockage",
    ),
    (
        "importation-cle-inconnue-passee-sous-silence",
        "src/importation.py",
        '    return f"`{cle}`"',
        '    return ""',
        "le tiroir d'un module à venir passerait sans laisser de ligne : on ne "
        "pourrait pas constater qu'il est repris",
    ),
    (
        "importation-publication-non-nommee",
        "src/importation.py",
        '        return f"{libelles.get(quoi, quoi)} de « {publication} »"',
        '        return f"{libelles.get(quoi, quoi)}"',
        "deux publications d'un même module ne se distingueraient plus dans le "
        "compte rendu",
    ),
    (
        "reglages-importer-sans-garde-admin",
        "src/reglages.py",
        "        if not administrateur(interaction):\n"
        "            await interaction.response.send_message(REFUS_IMPORT, ephemeral=True)",
        "        if False:\n"
        "            await interaction.response.send_message(REFUS_IMPORT, ephemeral=True)",
        "n'importe quel membre autorisé recopierait la liste d'accès du temps du "
        "commun, donc déciderait qui se sert du bot ici",
    ),
    (
        "reglages-importer-ecrit-dans-le-commun",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        reprise = preparer(",
        "        magasin = bot.store\n"
        "        reprise = preparer(",
        "l'import ne changerait rien : le serveur continuerait de ne publier "
        "nulle part, en annonçant que tout est repris",
    ),
    (
        "reglages-importer-salons-en-nombres",
        "src/reglages.py",
        "            {str(salon.id) for salon in interaction.guild.channels},",
        "            {salon.id for salon in interaction.guild.channels},",
        "aucun salon ne correspondrait : tous seraient écartés comme étrangers, "
        "et le serveur se retrouverait sans salon après un import annoncé réussi",
    ),
    (
        "reglages-importer-nannonce-que-des-ecritures-fantomes",
        "src/reglages.py",
        "        for cle, valeur in reprise.a_ecrire.items():\n"
        "            await magasin.set(cle, valeur)",
        "        for cle, valeur in {}.items():\n"
        "            await magasin.set(cle, valeur)",
        "le compte rendu listerait ce qui aurait dû être écrit, sans rien écrire",
    ),
    (
        "reglages-importer-rien-a-reprendre-passe-pour-un-succes",
        "src/reglages.py",
        "        if not reprise.a_ecrire and not reprise.deja_reglees:",
        "        if False:",
        "un import qui n'a rien trouvé répondrait « configuration reprise », et "
        "on attendrait des posts qui ne viendront jamais",
    ),
    (
        "reglages-importer-salons-ecartes-tus",
        "src/reglages.py",
        "        if reprise.salons_ecartes:",
        "        if False:",
        "on chercherait longtemps pourquoi une fourchette ne publie plus là où "
        "elle publiait la veille",
    ),
    (
        "reglages-importer-deja-regle-tu",
        "src/reglages.py",
        "        if reprise.deja_reglees:",
        "        if False:",
        "un import qui n'a rien complété passerait pour un import complet",
    ),
    (
        "reglages-importer-noms-bruts-dans-le-compte-rendu",
        "src/reglages.py",
        '                value="\\n".join(f"• {nommer(cle)}" for cle in reprise.a_ecrire),',
        '                value="\\n".join(f"• {cle}" for cle in reprise.a_ecrire),',
        "le compte rendu s'adresse à quelqu'un qui n'a jamais vu la base",
    ),
    (
        "bot-promos-une-seule-fourchette",
        "src/commandes.py",
        'else builtins.max(Decimal(f["prix_max"]) for f in fourchettes)',
        'else Decimal(fourchettes[0]["prix_max"])',
        "`/promos` masquerait les promotions des autres fourchettes",
    ),
    (
        "bot-promos-bornes-du-commun",
        "src/modules/promos.py",
        "            prix_min, prix_max = await bornes_demandees(magasin, min, max)",
        "            prix_min, prix_max = await bornes_demandees(bot.store, min, max)",
        "`/promos chercher` sans argument dirait « aucune fourchette configurée » "
        "à un serveur qui en a",
    ),
    (
        "bot-promos-fourchettes-a-la-racine",
        "src/modules/promos.py",
        '        name="promos",',
        '        name="fourchette",',
        "les fourchettes reviendraient à la racine, à côté des promotions qu'elles "
        "sont seules à découper",
    ),
    (
        "bot-tableau-nomme-par-son-decoupage",
        "src/modules/frais.py",
        '        name="frais", description="Tableau des frais de gestion par filiale"',
        '        name="filiales", description="Tableau des frais de gestion par '
        'filiale"',
        "le menu nommerait la façon dont le tableau est découpé plutôt que ce qu'on "
        "vient y chercher",
    ),
    (
        "bot-tableau-module-nomme-autrement-que-sa-commande",
        "src/modules/frais.py",
        '    nom="frais",',
        '    nom="filiales",',
        "`/reglages modules liste` citerait un `filiales` introuvable dans le menu, "
        "et `desactiver frais` ne nommerait plus rien",
    ),

    # --- Les modules lisent la configuration de leur serveur ---------------
    #
    # Chaque mutation ci-dessous rebranche une commande de module sur la
    # configuration commune. Le symptôme est toujours le même : un « ✅ » sur un
    # réglage que la tournée de ce serveur ne lira jamais, et des données d'une
    # entreprise qui apparaissent chez une autre.
    (
        "bot-promos-fourchette-creee-dans-le-commun",
        "src/modules/promos.py",
        "            creee = await magasin.ajouter_fourchette(fourchette, prix_min, prix_max)",
        "            creee = await bot.store.ajouter_fourchette(fourchette, prix_min, prix_max)",
        "la fourchette ne publierait rien et apparaîtrait chez tous les voisins",
    ),
    (
        "bot-promos-liste-du-commun",
        "src/modules/promos.py",
        "        fourchettes = await pour_ce_serveur(bot, interaction).fourchettes()\n"
        "        embed = discord.Embed(",
        "        fourchettes = await bot.store.fourchettes()\n"
        "        embed = discord.Embed(",
        "on lirait les marchés surveillés par une autre entreprise, et pas les siens",
    ),
    (
        "bot-promos-suppression-dans-le-commun",
        "src/modules/promos.py",
        "        if not await magasin.supprimer_fourchette(fourchette):",
        "        if not await bot.store.supprimer_fourchette(fourchette):",
        "la fourchette resterait, et le post continuerait de sortir",
    ),
    (
        "bot-promos-prix-regle-dans-le-commun",
        "src/modules/promos.py",
        "        if not await magasin.majprix_fourchette(fourchette, prix_min, prix_max):",
        "        if not await bot.store.majprix_fourchette(fourchette, prix_min, prix_max):",
        "les bornes annoncées ne seraient pas celles qui servent à chercher",
    ),
    (
        "bot-promos-tolerance-du-commun",
        "src/modules/promos.py",
        "            regle = await magasin.majtolerance_fourchette(",
        "            regle = await bot.store.majtolerance_fourchette(",
        "la zone est invisible dans Discord : réglée ailleurs, rien ne le dirait",
    ),
    (
        "bot-promos-salon-attache-dans-le-commun",
        "src/modules/promos.py",
        "        if not await magasin.ajouter_salon_fourchette(fourchette, str(salon.id)):",
        "        if not await bot.store.ajouter_salon_fourchette(fourchette, str(salon.id)):",
        "le salon ne recevrait rien malgré le « ✅ »",
    ),
    (
        "bot-promos-salon-retire-du-commun",
        "src/modules/promos.py",
        "        if not await magasin.retirer_salon_fourchette(fourchette, str(salon.id)):",
        "        if not await bot.store.retirer_salon_fourchette(fourchette, str(salon.id)):",
        "le salon continuerait de recevoir la fourchette qu'on vient d'en retirer",
    ),
    (
        "bot-promos-autocompletion-du-commun",
        "src/modules/promos.py",
        "            for f in await pour_ce_serveur(bot, interaction).fourchettes()",
        "            for f in await bot.store.fourchettes()",
        "on choisirait un nom que la commande refuse ensuite",
    ),
    (
        "bot-tableau-releve-dans-le-commun",
        "src/modules/frais.py",
        "            releve = await magasin.enregistrer_filiale(\n"
        "                filiale, valeur, await _aujourdhui(magasin)\n"
        "            )",
        "            releve = await bot.store.enregistrer_filiale(\n"
        "                filiale, valeur, await _aujourdhui(magasin)\n"
        "            )",
        "chaque entreprise verrait les frais de l'autre dans son tableau du soir",
    ),
    (
        "bot-tableau-releve-date-du-fuseau-commun",
        "src/modules/frais.py",
        "                filiale, valeur, await _aujourdhui(magasin)",
        "                filiale, valeur, await _aujourdhui(bot.store)",
        "le relevé du jour se lirait « relevé d'hier » dans un serveur décalé",
    ),
    (
        "bot-tableau-liste-du-commun",
        "src/modules/frais.py",
        "        filiales = await magasin.filiales()\n"
        "        await interaction.response.send_message(",
        "        filiales = await bot.store.filiales()\n"
        "        await interaction.response.send_message(",
        "le tableau montrerait des filiales qu'on ne possède pas ici",
    ),
    (
        "bot-tableau-retrait-dans-le-commun",
        "src/modules/frais.py",
        "        retirees, inconnus = await magasin.retirer_filiales(noms)",
        "        retirees, inconnus = await bot.store.retirer_filiales(noms)",
        "la filiale reviendrait dans le tableau du soir, et manquerait chez l'autre",
    ),
    (
        "bot-tableau-vidage-dans-le-commun",
        "src/modules/frais.py",
        "        combien = await magasin.remettre_a_zero_filiales(await _aujourdhui(magasin))",
        "        combien = await bot.store.remettre_a_zero_filiales(await _aujourdhui(magasin))",
        "un nouveau cycle ici effacerait les relevés que l'autre n'a pas publiés",
    ),
    (
        "bot-tableau-export-du-commun",
        "src/modules/frais.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        filiales = await magasin.filiales()\n"
        "        if not filiales:",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        filiales = await bot.store.filiales()\n"
        "        if not filiales:",
        "le fichier part dans le jeu : une ligne d'une autre entreprise y serait "
        "importée pour de bon",
    ),
    (
        "bot-tableau-autocompletion-du-commun",
        "src/modules/frais.py",
        "            for f in await pour_ce_serveur(bot, interaction).filiales()",
        "            for f in await bot.store.filiales()",
        "le nom proposé ferait saisir un relevé sur une filiale d'un autre serveur",
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
        "bot-calculatrices-eparpillees",
        "src/modules/conversion.py",
        "        name=\"convertir\",",
        "        name=\"calculs\",",
        "le mot du menu ne serait plus celui que tous les textes citent",
    ),
    (
        "bot-convertir-ignore-le-palier",
        "src/modules/conversion.py",
        "            rendu = convertir(valeur, vers)",
        "            rendu = format_money(valeur)",
        "/convertir montant rendrait le palier du bot, pas celui demandé",
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
        "src/modules/frais.py",
        "        if not confirmer:\n            await interaction.response.send_message(\n                f\"❌ Rien effacé : coche `confirmer` pour aller au bout.\\n\"",
        "        if False:\n            await interaction.response.send_message(\n                f\"❌ Rien effacé : coche `confirmer` pour aller au bout.\\n\"",
        "un cycle entier de relevés partirait sur un clic de travers",
    ),
    (
        "bot-retirer-lot-sans-confirmation",
        "src/modules/frais.py",
        "        if (tout or len(noms) > 1) and not confirmer:",
        "        if False:",
        "un lot de filiales partirait sans qu'on ait vu lequel",
    ),
    (
        "bot-retirer-tout-sans-confirmation",
        "src/modules/frais.py",
        "        if (tout or len(noms) > 1) and not confirmer:",
        "        if len(noms) > 1 and not confirmer:",
        "`tout` sur une seule filiale l'emporterait sans cérémonie, alors que le "
        "mot ne nomme pas ce qu'il vise",
    ),
    (
        "bot-retirer-un-nom-demande-une-ceremonie",
        "src/modules/frais.py",
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
        "src/modules/frais.py",
        "        if not saisie:\n            await interaction.response.send_message(\n                \"❌ Aucun nom saisi.",
        "        if False:\n            await interaction.response.send_message(\n                \"❌ Aucun nom saisi.",
        "un refus annoncerait « aucune filiale enregistrée » alors qu'il y en a",
    ),
    (
        "bot-retirer-tout-non-reconnu",
        "src/modules/frais.py",
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
    # --- src/modules/frais.py : la saisie du tableau --------------------
    #
    # Plus de motif sur la fourche `if filiale is not None` de `/frais` : la case
    # facultative qui décidait d'écrire en base ou non a disparu, et avec elle les
    # deux mutants qui l'éprouvaient. La saisie est `/frais releve`, où les deux
    # champs sont obligatoires — il n'y a plus de branche à couper.
    (
        "bot-releve-montant-illisible-enregistre-quand-meme",
        "src/modules/frais.py",
        "            await interaction.response.send_message(\n                f\"❌ {erreur}\\n{aide_montants()}\", ephemeral=True\n            )\n            return\n\n        magasin = pour_ce_serveur(bot, interaction)",
        "            await interaction.response.send_message(\n                f\"❌ {erreur}\\n{aide_montants()}\", ephemeral=True\n            )\n\n        magasin = pour_ce_serveur(bot, interaction)",
        "une filiale serait retenue à un montant faux et fausserait le total",
    ),
    (
        "bot-releve-public",
        "src/modules/frais.py",
        "        await interaction.response.send_message(corps, ephemeral=True)",
        "        await interaction.response.send_message(corps, ephemeral=False)",
        "les résultats de l'entreprise s'afficheraient dans le salon",
    ),
    (
        "bot-releve-ressaisie-annoncee-comme-un-ajout",
        "src/modules/frais.py",
        '        verbe = "mise à jour" if existait else "enregistrée"',
        '        verbe = "enregistrée"',
        "on ne saurait pas qu'on vient d'écraser un relevé",
    ),
    (
        "bot-releve-sans-alerte-de-salon-manquant",
        "src/modules/frais.py",
        '        if not await magasin.salons_filiales():\n            # Une saisie qui n\'ira nulle part doit se voir maintenant, pas au\n            # moment où l\'on s\'étonne de ne rien recevoir.\n            corps += "\\n⚠️ Aucun salon pour le tableau : `/frais salon ajouter`."',
        "        pass",
        "on saisirait des relevés que personne ne recevrait",
    ),
    (
        "bot-tableau-a-l-heure-des-promotions",
        "src/modules/frais.py",
        "    return await magasin.heure_filiales()",
        '    return (await magasin.config())["heure"]',
        "régler l'heure des promotions déplacerait le tableau",
    ),
    (
        "bot-tableau-dans-les-salons-des-promotions",
        "src/modules/frais.py",
        "    salons = await magasin.salons_filiales()",
        "    salons = await magasin.salons()",
        "les frais de l'entreprise partiraient dans le salon des promotions",
    ),
    # `bot-tour-sans-le-tableau` et `bot-tour-sans-isolation-des-pannes` vivaient
    # ici : ils visaient la liste des deux publications ecrite en dur dans
    # `publier_tout`, que le tour par serveur a remplacee. Leurs equivalents sont
    # dans le lot `cloisonnement-` : `cloisonnement-tour-du-premier-module-seul`
    # et `cloisonnement-panne-dun-serveur-interrompt-le-tour`.
    (
        "bot-tableau-heure-ecrit-celle-des-promotions",
        "src/modules/frais.py",
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
        "        await marquer_le_jour(publication, magasin, None)",
        "        pass",
        "le nouvel horaire serait bloqué jusqu'au lendemain",
    ),
    (
        "surface-heure-tait-l-absence-de-salon",
        "src/commandes.py",
        "        if salons and not await salons_de(publication, magasin):",
        "        if False:",
        "on attendrait un post à l'heure réglée alors qu'aucun salon ne le reçoit",
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
        '        maintenant = maintenant_local((await magasin.config())["fuseau"])\n\n        try:',
        '        maintenant = maintenant_local((await magasin.config())["fuseau"])\n'
        '        await marquer_le_jour(\n'
        '            publication, magasin, maintenant.strftime("%Y-%m-%d")\n'
        "        )\n\n        try:",
        "un aperçu empêcherait le post du jour de sortir",
    ),
    (
        "surface-apercu-un-seul-envoi",
        "src/commandes.py",
        "        for envoi in tournee.envois:",
        "        for envoi in tournee.envois[:1]:",
        "l'aperçu mentirait sur ce que chaque salon recevra — c'est justement ce "
        "qu'on vient prévisualiser",
    ),
    # Le routage vers le serveur où la commande est tapée. Chaque mutation
    # ci-dessous rebranche une commande sur la configuration commune : elle
    # répondrait « ✅ » sans que rien ne change pour le serveur, et le réglage
    # atterrirait là où plus aucune tournée ne va lire.
    (
        "surface-heure-ecrit-dans-le-commun",
        "src/commandes.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        config = await magasin.config()",
        "        magasin = bot.store\n        config = await magasin.config()",
        "l'heure changerait pour tous les serveurs, et pour aucun",
    ),
    (
        "surface-heure-oublie-la-marque-du-commun",
        "src/commandes.py",
        "        await marquer_le_jour(publication, magasin, None)",
        "        await marquer_le_jour(publication, bot.store, None)",
        "le post déjà sorti bloquerait le nouvel horaire jusqu'au lendemain",
    ),
    (
        "surface-apercu-prepare-sur-le-commun",
        "src/commandes.py",
        "            tournee = await publication.preparer(bot, magasin, maintenant)",
        "            tournee = await publication.preparer(bot, bot.store, maintenant)",
        "l'aperçu montrerait un post qui ne sortira dans aucun salon d'ici",
    ),
    (
        "surface-publier-dans-le-commun",
        "src/commandes.py",
        "        compte_rendu = await bot.faire_publication(\n"
        "            publication, magasin=magasin, forcer=True\n"
        "        )",
        "        compte_rendu = await bot.faire_publication(publication, forcer=True)",
        "publier maintenant enverrait dans les salons de tous les serveurs",
    ),
    (
        "surface-salon-ajoute-au-commun",
        "src/commandes.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        if not await ajouter_un_salon(publication, magasin, str(salon.id)):",
        "        magasin = bot.store\n"
        "        if not await ajouter_un_salon(publication, magasin, str(salon.id)):",
        "le salon serait attaché à une configuration que ce serveur ne lit pas",
    ),
    (
        "surface-salon-retire-du-commun",
        "src/commandes.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "        if not await retirer_un_salon(publication, magasin, str(salon.id)):",
        "        magasin = bot.store\n"
        "        if not await retirer_un_salon(publication, magasin, str(salon.id)):",
        "retirer un salon répondrait « pas dans la liste » sans rien retirer",
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
        "src/modules/frais.py",
        "            file=fichier,\n            ephemeral=True,\n        )",
        "            ephemeral=True,\n        )",
        "la commande annoncerait un export sans rien rendre",
    ),
    (
        "bot-export-vide-annonce-un-fichier",
        "src/modules/frais.py",
        "        filiales = await magasin.filiales()\n        if not filiales:\n            # Pas de fichier vide",
        "        filiales = await magasin.filiales()\n        if False:\n            # Pas de fichier vide",
        "un fichier de zéro octet se lirait comme une panne du bot",
    ),
    (
        "bot-export-nom-de-fichier-sans-date",
        "src/modules/frais.py",
        'filename=f"frais-{await _aujourdhui(magasin)}.txt",',
        'filename="frais.txt",',
        "deux exports d'affilée se confondraient dans le fil",
    ),
    (
        "bot-export-nom-deforme-en-silence",
        "src/modules/frais.py",
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
    # --- Les types de bâtiments écartés -------------------------------------
    #
    # Une exclusion qui n'exclut pas est le pire des défauts de ce réglage : le
    # post sort inchangé, et rien à l'écran ne dit pourquoi. Les motifs suivants
    # visent donc d'abord le silence.
    (
        "types-filtre-ignore",
        "src/promos.py",
        "        if b.promotion > 0 and normaliser_type(b.type) not in exclus",
        "        if b.promotion > 0",
        "un type écarté sortirait quand même, tous les soirs",
    ),
    (
        "types-entree-vide-ecarte-les-sans-type",
        "src/promos.py",
        "    exclus = {t for t in (normaliser_type(nom) for nom in types_exclus) if t}",
        "    exclus = {normaliser_type(nom) for nom in types_exclus}",
        "un `\"\"` glissé dans la liste écarterait les bâtiments sans type",
    ),
    (
        "types-comparaison-sensible-a-la-casse",
        "src/promos.py",
        '    return str(nom or "").strip().casefold()',
        '    return str(nom or "")',
        "« Transport » n'écarterait rien, et rien ne dirait pourquoi",
    ),
    (
        "types-proposes-reduits-aux-promos-du-jour",
        "src/promos.py",
        "    return sorted({b.type.strip() for b in batiments if b.type.strip()})",
        "    return sorted(\n"
        "        {b.type.strip() for b in batiments if b.type.strip() and b.promotion > 0}\n"
        "    )",
        "un type ne serait proposable que les jours où il est en promotion",
    ),
    (
        "types-ecartes-du-commun-dans-le-post",
        "src/bot.py",
        "            types_exclus=await magasin.types_exclus(),",
        "            types_exclus=await self.store.types_exclus(),",
        "l'exclusion d'une entreprise ferait maigrir le post de l'autre",
    ),
    (
        "types-memoire-jamais-ecrite",
        "src/bot.py",
        "            await self.store.memoriser_types(types_disponibles(batiments))",
        "            pass",
        "rien sous le curseur : il faudrait taper les noms de types de mémoire",
    ),
    (
        "types-liste-vide-jamais-ecrite",
        "src/db.py",
        "        await self.maj_config(types_exclus=restants)",
        "        await self.maj_config(types_exclus=restants or None)",
        "le dernier type remis se réexcluerait tout seul au redémarrage",
    ),
    (
        "types-deja-ecarte-reecrit",
        "src/db.py",
        "        if normaliser_type(propre) in {normaliser_type(t) for t in exclus}:",
        "        if False:",
        "deux graphies du même type se liraient comme deux exclusions",
    ),
    (
        "types-connus-effaces-par-un-export-vide",
        "src/db.py",
        "        if not propres or propres == await self.types_connus():",
        "        if False:",
        "un export illisible viderait les propositions, panne ailleurs, réglage muet",
    ),
    (
        "types-exclure-accepte-un-nom-inconnu",
        "src/modules/promos.py",
        "        if vrai is None:",
        "        if False:",
        "`zone` au singulier donnerait un filtre inerte et un « ✅ » menteur",
    ),
    (
        "types-exclure-accepte-le-dernier",
        "src/modules/promos.py",
        "        if len(restants) <= 1 and cible not in ecartes:",
        "        if False and cible not in ecartes:",
        "tout écarté, plus aucun post : indiscernable d'une panne du bot",
    ),
    (
        "types-dernier-type-compte-les-orphelins",
        "src/modules/promos.py",
        "        restants = [nom for nom in monde if normaliser_type(nom) not in ecartes]",
        "        restants = monde[len(ecartes) :]",
        "un type disparu du jeu ferait refuser le réglage, dernier type annoncé à tort",
    ),
    (
        "types-propositions-a-ecarter-du-commun",
        "src/modules/promos.py",
        "        exclus = {normaliser_type(nom) for nom in await magasin.types_exclus()}",
        "        exclus = {normaliser_type(nom) for nom in await bot.store.types_exclus()}",
        "on choisirait un type déjà écarté pour s'entendre dire qu'il l'était",
    ),
    (
        "types-propositions-a-remettre-du-commun",
        "src/modules/promos.py",
        "            for nom in await magasin.types_exclus()\n"
        "            if saisie.casefold() in nom.casefold()",
        "            for nom in await bot.store.types_exclus()\n"
        "            if saisie.casefold() in nom.casefold()",
        "on se verrait proposer de rendre ce qu'un autre serveur a écarté",
    ),
    (
        "types-reglages-voir-muet",
        "src/reglages.py",
        "        if types_ecartes := await magasin.types_exclus():",
        "        if False and (types_ecartes := await magasin.types_exclus()):",
        "le post plus court resterait inexpliqué là où on relit la configuration",
    ),
    (
        "types-api-promos-non-filtrees",
        "src/api.py",
        "            types_exclus=await bot.store.types_exclus(),\n"
        "            plafond=await _plafond(bot, requete),",
        "            plafond=await _plafond(bot, requete),",
        "le site listerait des promotions que le bot ne publie nulle part",
    ),
    (
        "types-api-apercu-non-filtre",
        "src/api.py",
        "            types_exclus=await bot.store.types_exclus(),\n"
        "            plafond=await _plafond(bot, requete, charge),",
        "            plafond=await _plafond(bot, requete, charge),",
        "l'aperçu du site promettrait un post que le soir ne produira pas",
    ),
    # --- Le plafond du nombre de promotions ---------------------------------
    #
    # Deux silences à traquer : un plafond réglé que rien ne lit (la commande
    # confirme, le post sort inchangé) et un plafond qui coupe autre chose que la
    # queue de la liste (le post rétrécit sans qu'on sache ce qui est parti).
    (
        "plafond-coupe-ignoree",
        "src/promos.py",
        "    if plafond and plafond > 0:\n        retenus = retenus[:plafond]",
        "    if False:\n        pass",
        "un plafond réglé ne couperait rien, et la commande aurait confirmé",
    ),
    (
        "plafond-coupe-la-tete",
        "src/promos.py",
        "        retenus = retenus[:plafond]",
        "        retenus = retenus[-plafond:]",
        "les moins chères publiées à la place des plus chères",
    ),
    (
        "plafond-absurde-vide-le-post",
        "src/promos.py",
        "    if plafond and plafond > 0:",
        "    if plafond is not None:",
        "un `0` retouché à la main ferait un post vide, lu comme une panne",
    ),
    (
        "plafond-total-compte-avant-la-coupe",
        "src/promos.py",
        "    total = len(retenus)",
        "    total = len(dedans + toleres + repeches)",
        "un post de deux promotions annoncé « 1/40 »",
    ),
    (
        "plafond-zero-lu-comme-un-plafond",
        "src/db.py",
        "    return nombre if nombre >= 1 else None",
        "    return nombre if nombre >= 0 else None",
        "un `0` en base plafonnerait à zéro : la fourchette cesserait de publier",
    ),
    (
        "plafond-en-texte-non-converti",
        "src/db.py",
        "    try:\n"
        "        nombre = int(str(brut).strip())\n"
        "    except (TypeError, ValueError):\n"
        "        return None",
        "    if not isinstance(brut, int):\n        return None\n    nombre = brut",
        "un plafond écrit `\"5\"` par le site ne plafonnerait rien",
    ),
    (
        "plafond-perdu-a-la-normalisation",
        "src/db.py",
        '        "plafond": 0 if plafond is None else plafond,',
        '        "plafond": 0,',
        "`/promos prix` effacerait le plafond au passage, sans le dire",
    ),
    (
        "plafond-zero-accepte-en-base",
        "src/db.py",
        "        if int(combien) < 1:\n"
        "            raise ValueError(\n"
        '                "Le plafond doit',
        '        if False:\n            raise ValueError(\n                "Le plafond doit',
        "une fourchette réglée à zéro, muette et indiscernable d'une panne",
    ),
    (
        "plafond-efface-annonce-a-tort",
        "src/db.py",
        "        if index < 0 or not plafond_fourchette(liste[index]):",
        "        if index < 0:",
        "un effacement imaginaire confirmé par un « ✅ »",
    ),
    (
        "plafond-recherche-prend-le-plus-etroit",
        "src/db.py",
        "        return max(plafonds)",
        "        return min(plafonds)",
        "la recherche montrerait moins que la fourchette la plus généreuse",
    ),
    (
        "plafond-recherche-plafonnee-par-une-seule",
        "src/db.py",
        "        if not plafonds or None in plafonds:\n"
        "            return None\n"
        "        return max(plafonds)",
        "        reels = [p for p in plafonds if p]\n"
        "        if not reels:\n"
        "            return None\n"
        "        return max(reels)",
        "la recherche cacherait ce qu'une fourchette non plafonnée publie",
    ),
    (
        "plafond-recherche-plafonnee-sans-fourchette",
        "src/db.py",
        "        if not plafonds or None in plafonds:",
        "        if None in plafonds:",
        "un serveur neuf verrait sa recherche bornée à rien",
    ),
    (
        "plafond-jamais-transmis-au-coeur",
        "src/bot.py",
        "            plafond=plafond,",
        "            plafond=None,",
        "le réglage lu, transmis nulle part : post, aperçu et recherche entiers",
    ),
    (
        "plafond-post-du-soir-non-plafonne",
        "src/modules/promos.py",
        "                plafond=plafond_fourchette(fourchette),",
        "                plafond=None,",
        "le post du soir ignorerait le plafond de sa fourchette",
    ),
    (
        "plafond-recherche-jamais-plafonnee",
        "src/modules/promos.py",
        "        plafond = None if libre else await magasin.plafond_de_recherche()",
        "        plafond = None",
        "`/promos chercher` promettrait plus long que le post du soir",
    ),
    (
        "plafond-recherche-libre-plafonnee",
        "src/modules/promos.py",
        "        plafond = None if libre else await magasin.plafond_de_recherche()",
        "        plafond = await magasin.plafond_de_recherche()",
        "des bornes tapées à la main verraient leur résultat coupé",
    ),
    (
        "plafond-un-sans-avertissement",
        "src/modules/promos.py",
        "        if nombre < CIBLE_MINIMUM:",
        "        if False:",
        "le repêchage silencieusement annulé par un plafond de 1",
    ),
    (
        "plafond-fourchette-inconnue-confirmee",
        "src/modules/promos.py",
        "        if not regle:\n"
        "            await refuser_fourchette_inconnue(interaction, fourchette)\n"
        "            return\n"
        "\n"
        "        # Les plus chères",
        "        if False:\n            pass\n\n        # Les plus chères",
        "un « ✅ » sur une fourchette qui n'existe pas",
    ),
    (
        "plafond-invisible-dans-la-liste",
        "src/commandes.py",
        "        if plafond := plafond_fourchette(fourchette):\n"
        "            lignes.append(\n"
        "                f\"-# plafond : {plafond} promotion{'s' if plafond > 1 else ''} au maximum\"\n"
        "            )",
        "        if False:\n            pass",
        "un plafond nulle part relisible, donc re-réglé au hasard",
    ),
    (
        "plafond-absent-expose-a-zero",
        "src/serialisation.py",
        "    if (plafond := plafond_fourchette(fourchette)) is not None:\n"
        "        rendu[\"plafond\"] = plafond",
        '    rendu["plafond"] = plafond_fourchette(fourchette) or 0',
        "le site montrerait « plafond : 0 » sur une fourchette qui publie tout",
    ),
    (
        "plafond-api-promos-non-plafonnee",
        "src/api.py",
        "            plafond=await _plafond(bot, requete),",
        "            plafond=None,",
        "la page listerait plus de promotions que le post du soir",
    ),
    (
        "plafond-api-apercu-non-plafonne",
        "src/api.py",
        "            plafond=await _plafond(bot, requete, charge),",
        "            plafond=None,",
        "l'aperçu du site promettrait un post plus long que le vrai",
    ),
    (
        "plafond-api-bornes-libres-plafonnees",
        "src/api.py",
        "    if _bornes_donnees(requete, charge):\n        return None",
        "    if False:\n        return None",
        "une recherche à bornes données verrait son résultat coupé",
    ),
    (
        "plafond-api-bornes-jamais-vues",
        "src/api.py",
        '    return any(source.get(cle) not in (None, "") for cle in ("min", "max"))',
        "    return False",
        "toute recherche libre du site serait coupée comme le post du soir",
    ),
    # --- Les tranches : un plafond par plage de prix dans la fourchette -----
    (
        "tranche-jamais-appliquee",
        "src/promos.py",
        "    retenus = _sous_les_tranches(retenus, tranches)",
        "    retenus = list(retenus)",
        "des tranches réglées, confirmées à l'écran, et sans effet sur le post",
    ),
    (
        "tranche-laisse-passer-une-de-plus",
        "src/promos.py",
        "        if any(c[3] >= c[2] for c in concernees):",
        "        if any(c[3] > c[2] for c in concernees):",
        "une tranche réglée à 3 en publierait 4",
    ),
    (
        "tranche-bornes-exclues",
        "src/promos.py",
        "        concernees = [c for c in comptes if c[0] <= batiment.valeur <= c[1]]",
        "        concernees = [c for c in comptes if c[0] < batiment.valeur < c[1]]",
        "une promotion pile sur une borne échapperait à la tranche",
    ),
    (
        "tranche-seule-la-premiere-compte",
        "src/promos.py",
        "        concernees = [c for c in comptes if c[0] <= batiment.valeur <= c[1]]",
        "        concernees = [c for c in comptes if c[0] <= batiment.valeur <= c[1]][:1]",
        "deux tranches qui se chevauchent laisseraient passer plus que leur nombre",
    ),
    (
        "tranche-absurde-vide-la-plage",
        "src/promos.py",
        "for bas, haut, nombre in tranches if int(nombre) >= 1",
        "for bas, haut, nombre in tranches",
        "un `0` retouché à la main ferait disparaître toute une plage de prix",
    ),
    (
        "tranche-lecture-non-defensive",
        "src/db.py",
        "    if not isinstance(brutes, list):\n        return []",
        "    if brutes is None:\n        return []",
        "un `\"tranches\": 42` retouché à la main ferait tomber la publication",
    ),
    (
        "tranche-entree-incomplete-gardee",
        "src/db.py",
        "        if bas is None or haut is None or nombre is None:\n            continue",
        "        if False:\n            continue",
        "une tranche sans borne ferait tomber la publication au lieu d'être ignorée",
    ),
    (
        "tranche-bornes-inversees-inertes",
        "src/db.py",
        "        if bas > haut:\n"
        "            bas, haut = haut, bas\n"
        "        lues.append((bas, haut, nombre))",
        "        lues.append((bas, haut, nombre))",
        "une tranche `300 → 100` réglée, inerte, et rien pour dire pourquoi",
    ),
    (
        "tranche-lues-dans-le-desordre",
        "src/db.py",
        "    return sorted(lues, key=lambda tranche: (tranche[0], tranche[1]))",
        "    return lues",
        "`/promos liste` réordonnerait ses tranches à chaque réglage",
    ),
    (
        "tranche-perdue-a-la-normalisation",
        "src/db.py",
        '        "tranches": [\n'
        '            {"min": str(bas), "max": str(haut), "nombre": nombre}\n'
        "            for bas, haut, nombre in tranches_fourchette(brute)\n"
        "        ],",
        '        "tranches": [],',
        "`/promos prix` effacerait les tranches au passage, sans le dire",
    ),
    (
        "tranche-zero-acceptee-en-base",
        "src/db.py",
        "        if int(combien) < 1:\n"
        "            raise ValueError(\n"
        '                "Une tranche doit',
        '        if False:\n            raise ValueError(\n                "Une tranche doit',
        "une plage de prix muette, que le mot « plafond » n'annonce pas",
    ),
    (
        "tranche-meme-plage-empilee",
        "src/db.py",
        "            if (tranche[0], tranche[1]) != (bas, haut)",
        "            if True",
        "chaque correction empilerait une tranche : la plus stricte gagnerait",
    ),
    (
        "tranche-effacement-imaginaire-confirme",
        "src/db.py",
        "        if len(restantes) == len(avant):\n            return False",
        "        if False:\n            return False",
        "un effacement imaginaire confirmé par un « ✅ », et deux tranches restantes",
    ),
    (
        "tranche-recherche-prend-le-plus-etroit",
        "src/db.py",
        "            (bas, haut, max(table[(bas, haut)] for table in par_plage))",
        "            (bas, haut, min(table[(bas, haut)] for table in par_plage))",
        "la recherche montrerait moins que la fourchette la plus généreuse",
    ),
    (
        "tranche-recherche-tranchee-par-une-seule",
        "src/db.py",
        "        communes = set(par_plage[0])\n"
        "        for table in par_plage[1:]:\n"
        "            communes &= set(table)\n"
        "\n"
        "        return sorted(\n"
        "            (bas, haut, max(table[(bas, haut)] for table in par_plage))\n"
        "            for bas, haut in communes\n"
        "        )",
        "        toutes: dict[tuple[Decimal, Decimal], int] = {}\n"
        "        for table in par_plage:\n"
        "            for plage, nombre in table.items():\n"
        "                toutes[plage] = max(nombre, toutes.get(plage, 0))\n"
        "        return sorted(\n"
        "            (bas, haut, nombre) for (bas, haut), nombre in toutes.items()\n"
        "        )",
        "la recherche cacherait ce qu'une fourchette sans cette plage publie",
    ),
    (
        "tranche-recherche-sans-fourchette",
        "src/db.py",
        "        fourchettes = await self.fourchettes()\n"
        "        if not fourchettes:\n"
        "            return []",
        "        fourchettes = await self.fourchettes()",
        "un serveur neuf verrait `/promos chercher` tomber en panne",
    ),
    (
        "tranche-jamais-transmise-au-coeur",
        "src/bot.py",
        "            tranches=tranches,",
        "            tranches=(),",
        "le réglage lu, transmis nulle part : post, aperçu et recherche entiers",
    ),
    (
        "tranche-post-du-soir-non-tranche",
        "src/modules/promos.py",
        "                tranches=tranches_fourchette(fourchette),",
        "                tranches=(),",
        "le post du soir ignorerait les tranches de sa fourchette",
    ),
    (
        "tranche-recherche-jamais-tranchee",
        "src/modules/promos.py",
        "        tranches = () if libre else await magasin.tranches_de_recherche()",
        "        tranches = ()",
        "`/promos chercher` promettrait plus long que le post du soir",
    ),
    (
        "tranche-recherche-libre-tranchee",
        "src/modules/promos.py",
        "        tranches = () if libre else await magasin.tranches_de_recherche()",
        "        tranches = await magasin.tranches_de_recherche()",
        "des bornes tapées à la main verraient leur résultat coupé",
    ),
    (
        "tranche-une-seule-borne-acceptee",
        "src/modules/promos.py",
        # La même garde existe pour `/promos tolerance` : la ligne vide qui
        # précède celle-ci est ce qui distingue les deux.
        "        magasin = pour_ce_serveur(bot, interaction)\n"
        "\n"
        "        if (min is None) != (max is None):",
        "        magasin = pour_ce_serveur(bot, interaction)\n\n        if False:",
        "une borne seule plafonnerait la fourchette entière, plage visée ignorée",
    ),
    (
        "tranche-bornes-ignorees-par-la-commande",
        "src/modules/promos.py",
        "        if min is not None and max is not None:",
        "        if False:",
        "`min:` et `max:` donnés, et c'est la fourchette entière qui est plafonnée",
    ),
    (
        "tranche-fourchette-inconnue-confirmee",
        "src/modules/promos.py",
        "        if not regle:\n"
        "            await refuser_fourchette_inconnue(interaction, fourchette)\n"
        "            return\n"
        "\n"
        "        message = (",
        "        if False:\n            pass\n\n        message = (",
        "un « ✅ » sur une fourchette qui n'existe pas",
    ),
    (
        "tranche-hors-fourchette-sans-avertissement",
        "src/modules/promos.py",
        "        if haut < portee_bas or bas > portee_haut:",
        "        if False:",
        "une tranche inerte confirmée sans un mot : le post ne changera pas",
    ),
    (
        "tranche-valide-avertie-a-tort",
        "src/modules/promos.py",
        "        if haut < portee_bas or bas > portee_haut:",
        "        if True:",
        "un ⚠️ sur chaque réglage valide : on apprendrait à ne plus le lire",
    ),
    (
        "tranche-invisible-dans-la-liste",
        "src/commandes.py",
        "        for bas, haut, combien in tranches_fourchette(fourchette):\n"
        "            lignes.append(\n"
        '                f"-# tranche {format_money(bas)} → {format_money(haut)} : "\n'
        '                f"{combien} au maximum"\n'
        "            )",
        "        if False:\n            pass",
        "des tranches nulle part relisibles, donc re-réglées au hasard",
    ),
    (
        "tranche-absente-exposee-vide",
        "src/serialisation.py",
        "    if tranches := tranches_fourchette(fourchette):",
        "    if (tranches := tranches_fourchette(fourchette)) is not None:",
        "le site montrerait « tranches : [] » sur une fourchette qui publie tout",
    ),
    (
        "tranche-api-promos-non-tranchee",
        "src/api.py",
        "            tranches=await _tranches(bot, requete),",
        "            tranches=(),",
        "la page listerait plus de promotions que le post du soir",
    ),
    (
        "tranche-api-apercu-non-tranche",
        "src/api.py",
        "            tranches=await _tranches(bot, requete, charge),",
        "            tranches=(),",
        "l'aperçu du site promettrait un post plus long que le vrai",
    ),
    (
        "tranche-api-bornes-libres-tranchees",
        "src/api.py",
        "    if _bornes_donnees(requete, charge):\n        return []",
        "    if False:\n        return []",
        "une recherche à bornes données verrait son résultat coupé",
    ),
    # --- Le collage du tableau du jeu ---------------------------------------
    #
    # Tout se joue sur des données qu'on ne relit pas : treize noms avec doubles
    # espaces et des montants à dix-neuf chiffres. Une colonne mal lue ne se voit
    # ni dans la page ni dans le jeu — l'import passe et ne met rien à jour.
    (
        "collage-colonnes-devinees-aux-espaces",
        "src/collage.py",
        "        cellules = ligne.split(SEPARATEUR)",
        "        cellules = ligne.split()",
        "« ARMEE  DE TERRE » deviendrait trois colonnes : nom tronqué, import muet",
    ),
    (
        "collage-nom-normalise",
        "src/collage.py",
        "        nom = cellules[0].strip()",
        '        nom = " ".join(cellules[0].split())',
        "les doubles espaces sont la clé d'import du jeu : réduits, plus de filiale",
    ),
    (
        "collage-entete-comptee-comme-filiale",
        "src/collage.py",
        "        if _est_entete(cellules):",
        "        if False:",
        "une ligne « Filiale » dans le tableau du soir, et sa colonne mal choisie",
    ),
    (
        "collage-entete-ne-designe-plus-sa-colonne",
        "src/collage.py",
        "    if colonne is not None and 0 < colonne < len(cellules):",
        "    if False:",
        "une colonne ajoutée à droite par le jeu ferait prélever 7 % de n'importe quoi",
    ),
    (
        "collage-nom-lu-comme-montant",
        "src/collage.py",
        "    for cellule in reversed(cellules[1:]):",
        "    for cellule in reversed(cellules):",
        "une ligne sans montant verrait son nom pris pour un montant",
    ),
    (
        "collage-ligne-sans-tabulation-devinee",
        "src/collage.py",
        "        if len(cellules) < 2:",
        "        if False:",
        "la ligne retapée à la main passerait sans montant plutôt que d'être montrée",
    ),
    (
        "collage-ligne-vide-signalee",
        "src/collage.py",
        "        if not ligne.strip():\n            continue",
        "        if False:\n            continue",
        "le retour à la ligne final se lirait comme une faute à corriger",
    ),
    (
        "collage-numero-de-ligne-decale",
        "src/collage.py",
        '    for numero, ligne in enumerate(str(texte or "").splitlines(), start=1):',
        '    for numero, ligne in enumerate(str(texte or "").splitlines(), start=0):',
        "le numéro montré désignerait la ligne du dessus : on corrigerait une saine",
    ),
    (
        "collage-doublon-empile",
        "src/collage.py",
        "        filiales = enregistrer(\n"
        "            filiales, calculer(releve.nom, releve.benefices, date)\n"
        "        )",
        "        filiales = filiales + [\n"
        "            calculer(releve.nom, releve.benefices, date)\n"
        "        ]",
        "deux sélections qui se chevauchent compteraient deux fois les mêmes frais",
    ),
    # --- La page des frais : ce qui s'affiche -------------------------------
    (
        "page-frais-mise-en-cache",
        "src/page_frais.py",
        '        headers={"Cache-Control": "no-store"},',
        "        headers={},",
        "la page montrerait le collage précédent : on croirait le sien sans effet",
    ),
    (
        "page-frais-collage-non-echappe",
        "src/page_frais.py",
        "        collage=html.escape(collage),",
        "        collage=collage,",
        "un <script> collé s'exécuterait, et un lien piégé le ferait coller par un autre",
    ),
    (
        "page-frais-nom-non-echappe",
        "src/page_frais.py",
        "            nom=html.escape(filiale.nom),",
        "            nom=filiale.nom,",
        "le nom d'une filiale reviendrait en HTML dans le tableau affiché",
    ),
    # --- La page des frais : le verrou d'écriture ---------------------------
    #
    # La page est ouverte à tous et son menu nomme toutes les entreprises : sans
    # ce verrou, l'adresse suffirait à remplacer les relevés du jour de n'importe
    # laquelle.
    (
        "page-frais-entreprise-inconnue-acceptee",
        "src/page_frais.py",
        "        entreprise = dict(serveurs).get(serveur)\n        if entreprise is None:",
        "        entreprise = dict(serveurs).get(serveur)\n        if False:",
        "un id quelconque écrirait dans un tiroir que personne ne publie",
    ),
    (
        "page-frais-sans-mot-de-passe-ouverte",
        "src/page_frais.py",
        "        trace = await magasin.motdepasse_page()\n        if trace is None:",
        "        trace = await magasin.motdepasse_page()\n        if False:",
        "le refus ne nommerait plus la cause : on chercherait une faute de frappe",
    ),
    (
        "page-frais-mot-de-passe-facultatif",
        "src/page_frais.py",
        "        if not par_mot_de_passe and not verifier_jeton(",
        "        if False and not verifier_jeton(",
        "l'adresse suffirait à remplacer les relevés du jour de toute entreprise",
    ),
    (
        "page-frais-jeton-de-nimporte-quelle-entreprise",
        "src/page_frais.py",
        "            requete.cookies.get(nom_cookie(serveur), \"\"),\n"
        "            serveur,\n"
        "            int(time.time()),",
        "            requete.cookies.get(nom_cookie(serveur), \"\"),\n"
        '            "",\n'
        "            int(time.time()),",
        "l'entreprise ne serait plus dans ce qui est vérifié : un cookie pour toutes",
    ),
    (
        "page-frais-refus-repond-oui",
        "src/page_frais.py",
        "                rendre(serveurs, collage, serveur, _refus_acces(raison)), statut=403",
        "                rendre(serveurs, collage, serveur, _refus_acces(raison)), statut=200",
        "un refus indistinguable d'un succès dans le journal d'accès",
    ),
    (
        "page-frais-releves-dans-le-tiroir-commun",
        "src/page_frais.py",
        "        magasin = bot.store.pour(serveur)",
        "        magasin = bot.store",
        "les relevés d'une entreprise iraient dans un tableau que personne ne publie",
    ),
    # --- La page des frais : le cookie -------------------------------------
    (
        "page-frais-cookie-lisible-par-un-script",
        "src/page_frais.py",
        "        httponly=True,",
        "        httponly=False,",
        "volé par un script, il vaudrait mot de passe pendant un mois",
    ),
    (
        "page-frais-cookie-en-clair-sur-le-reseau",
        "src/page_frais.py",
        "        secure=True,",
        "        secure=False,",
        "le cookie partirait en clair sur un réseau partagé",
    ),
    (
        "page-frais-cookie-envoye-par-un-autre-site",
        "src/page_frais.py",
        '        samesite="Lax",',
        "        samesite=None,",
        "un formulaire posté d'ailleurs ferait écrire un navigateur identifié",
    ),
    (
        "page-frais-cookie-sans-fin",
        "src/page_frais.py",
        "        max_age=DUREE_JETON,",
        "        max_age=None,",
        "le cookie tiendrait tant que le navigateur reste ouvert, sans durée annoncée",
    ),
    (
        "page-frais-cookie-non-repose",
        "src/page_frais.py",
        "        _identifier(reponse, serveur, trace)\n        return reponse",
        "        return reponse",
        "le mot de passe serait à retaper un jour, donc gardé sous la main",
    ),
    # --- Le mot de passe lui-même ------------------------------------------
    (
        "mdp-empreinte-sans-sel",
        "src/motdepasse.py",
        "    sel = secrets.token_bytes(16)",
        '    sel = b"sel fixe"',
        "deux entreprises au même mot de passe se verraient en base",
    ),
    (
        "mdp-trace-abimee-plante",
        "src/motdepasse.py",
        "    if not isinstance(trace, dict):\n        return False",
        "    if False:\n        return False",
        "une config retouchée à la main ferait planter la page au lieu de refuser",
    ),
    (
        "mdp-jeton-sans-empreinte-accepte",
        "src/motdepasse.py",
        '    if not isinstance(trace, dict) or not trace.get("empreinte"):\n        return False',
        "    if False:\n        return False",
        "une entreprise sans mot de passe verrait ses cookies vérifiés contre rien",
    ),
    (
        "mdp-jeton-illisible-plante",
        "src/motdepasse.py",
        "    try:\n        expiration = int(brut)\n    except ValueError:\n        return False",
        "    expiration = int(brut or 0)",
        "un cookie retouché à la main casserait la page au lieu d'être refusé",
    ),
    (
        "mdp-jeton-sans-date",
        "src/motdepasse.py",
        "    if expiration <= int(maintenant):\n        return False",
        "    if False:\n        return False",
        "un cookie de l'an dernier ouvrirait encore l'écriture",
    ),
    (
        "mdp-jeton-sans-entreprise",
        "src/motdepasse.py",
        '    message = f"{serveur_id}|{expiration}".encode()',
        '    message = f"{expiration}".encode()',
        "le cookie d'une entreprise vaudrait pour toutes celles du menu",
    ),
    (
        "mdp-jeton-signe-avec-une-cle-fixe",
        "src/motdepasse.py",
        '    cle = str(trace.get("empreinte", "")).encode()',
        '    cle = b"cle fixe"',
        "changer de mot de passe ne couperait plus les navigateurs déjà identifiés",
    ),
    (
        "mdp-cookie-dune-heure",
        "src/motdepasse.py",
        "DUREE_JETON = 400 * 24 * 3600",
        "DUREE_JETON = 3600",
        "le mot de passe serait à retaper chaque jour, donc gardé sous la main",
    ),
    (
        "mdp-cookie-plus-long-que-ce-quun-navigateur-retient",
        "src/motdepasse.py",
        "DUREE_JETON = 400 * 24 * 3600",
        "DUREE_JETON = 10 * 365 * 24 * 3600",
        "le navigateur ramènerait à 400 jours : le cookie mourrait avant sa date signée",
    ),
    # --- Le tiroir : lot de relevés et empreinte ---------------------------
    (
        "db-lot-ecrase-le-tableau",
        "src/db.py",
        "        filiales = await self.filiales()\n        for releve in releves:",
        "        filiales = []\n        for releve in releves:",
        "un tableau collé en deux fois effacerait sa première moitié",
    ),
    (
        "db-lot-non-ecrit",
        "src/db.py",
        '        await self.set("filiales", vers_json(filiales))\n        return releves',
        "        return releves",
        "la page annoncerait treize relevés enregistrés et n'écrirait rien",
    ),
    (
        "db-mdp-trace-abimee-rendue-telle-quelle",
        "src/db.py",
        "        return trace if isinstance(trace, dict) else None",
        "        return trace",
        "une valeur retouchée à la main ferait planter la page au lieu de la fermer",
    ),
    (
        "db-mdp-clair-en-base",
        "src/db.py",
        '        await self.set("motdepasse_page", motdepasse.empreinte(clair))',
        '        await self.set("motdepasse_page", {"empreinte": clair})',
        "le mot de passe serait lisible en base, donc chez l'hébergeur",
    ),
    (
        "db-mdp-efface-sans-le-dire",
        "src/db.py",
        "        if await self.motdepasse_page() is None:\n            return False",
        "        if False:\n            return False",
        "un « ✅ retiré » sur une entreprise qui n'avait pas de mot de passe",
    ),
    # --- /reglages motdepasse ----------------------------------------------
    (
        "mdp-commande-ouverte-a-tous",
        "src/reglages.py",
        "        if not administrateur(interaction):\n"
        "            await interaction.response.send_message(REFUS_MOTDEPASSE, ephemeral=True)",
        "        if False:\n"
        "            await interaction.response.send_message(REFUS_MOTDEPASSE, ephemeral=True)",
        "n'importe qui s'accorderait l'écriture des relevés hors de Discord",
    ),
    (
        "mdp-commande-montre-le-mot-de-passe-au-salon",
        "src/reglages.py",
        "de les couper.\",\n            ephemeral=True,",
        "de les couper.\",\n            ephemeral=False,",
        "le mot de passe resterait affiché dans le salon pour tout le monde",
    ),
    (
        "mdp-commande-regle-toutes-les-entreprises",
        "src/reglages.py",
        "        magasin = pour_ce_serveur(bot, interaction)\n\n        if retirer:",
        "        magasin = bot.store\n\n        if retirer:",
        "un mot de passe commun donnerait l'écriture chez toutes les entreprises",
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

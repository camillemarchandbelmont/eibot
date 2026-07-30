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
        "api-fourchette-inversee-acceptee",
        "src/api.py",
        "        if minimum > maximum:",
        "        if False:",
        "une fourchette inversée serait enregistrée et ne rendrait jamais rien",
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
        "serial-config-notation-scientifique",
        "src/serialisation.py",
        "        rendu.update(montant_en_json(champ, _montant_ou_zero(config.get(champ, 0))))",
        '        rendu.update({champ: str(config.get(champ, 0)),\n'
        '                      f"{champ}_brut": str(config.get(champ, 0)),\n'
        '                      f"{champ}_long": str(config.get(champ, 0))})',
        "le site afficherait « 1e14 » au lieu de « 100,00 TØ »",
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
        "serial-role-id-vide-au-lieu-de-null",
        "src/serialisation.py",
        '"role_id": str(config.get("role_id")) if config.get("role_id") else None,',
        '"role_id": str(config.get("role_id")),',
        "le site afficherait « rôle None » au lieu de « aucune »",
    ),
    (
        "api-champ-interdit-accepte",
        "src/api.py",
        "        inconnus = sorted(set(charge) - set(CHAMPS_MODIFIABLES))",
        "        inconnus = []",
        "le site pourrait écrire n'importe quelle clé en base",
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

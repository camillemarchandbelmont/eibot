"""Routes JSON consommées par le site web.

Le site (Next.js sur Vercel) remplace la plupart des commandes Discord. Le
chemin d'un clic :

    navigateur --(mot de passe, cookie signé)--> Next.js --(API_SECRET)--> bot

Deux conséquences de cette forme, qui expliquent presque tout ce module :

1. **L'authentification des personnes vit côté Vercel**, pas ici. Le bot ne voit
   que des appels serveur-à-serveur portant `API_SECRET`. Ce secret est donc la
   seule chose qui protège les écritures, d'où la comparaison stricte de
   `_autorise` et son refus quand il n'est pas configuré.

2. **Aucun navigateur n'appelle ces routes directement.** Pas de CORS à gérer, et
   surtout `API_SECRET` ne descend jamais dans le code de la page.

Toute réponse est du JSON, y compris les erreurs : le site fait `res.json()`
sans regarder le statut, et une erreur en `text/plain` lui donnerait une
exception de parsing au lieu du message à afficher.

Ce module n'écrit rien en base sans avoir tout validé : un PATCH à moitié
appliqué laisserait une configuration incohérente qu'il faudrait deviner pour
réparer.
"""

from __future__ import annotations

import hmac
import logging
from decimal import Decimal
from typing import Any

from aiohttp import web

from src import settings
from src.money import MoneyError, parse_money
from src.promos import find_promos
from src.publish import construire_embeds, grouper_messages, message_aucune_promo
from src.schedule import maintenant_local
from src.serialisation import config_en_json, etat_en_json, promos_en_json
from src.source import SourceError, decrire
from src.template import PLACEHOLDERS, TemplateError, placeholders_inconnus, valider_template

log = logging.getLogger(__name__)

#: En-tête portant le secret partagé. En-tête plutôt que query string : la query
#: finit dans les journaux d'accès de Render (voir `JournalSansSecret`).
ENTETE_SECRET = "X-Api-Secret"

#: Champs de configuration que le site peut écrire.
#:
#: Liste blanche plutôt que « tout sauf » : un bug de la page ne doit pas pouvoir
#: écrire n'importe quelle clé en base. `fourchettes`, `autorises` et
#: `logs_salon_id` en sont volontairement absents — ils restent réglés par
#: commande Discord, parce qu'ils désignent des objets Discord que le site ne
#: peut pas vérifier (permissions du bot dans le salon, appartenance du membre au
#: serveur).
#:
#: `prix_min`/`prix_max` ont disparu avec la fourchette unique : les accepter
#: écrirait une clé que plus rien ne lit, en laissant croire que c'est réglé.
CHAMPS_MODIFIABLES = ("heure", "fuseau")


class RequeteInvalide(Exception):
    """Entrée refusée, avec un message destiné à l'utilisateur du site.

    Message en français et sans jargon : le site l'affiche tel quel, c'est lui
    que tu liras.
    """


def _json(charge: Any, statut: int = 200) -> web.Response:
    return web.json_response(charge, status=statut)


def _erreur(message: str, statut: int = 400) -> web.Response:
    return _json({"erreur": message}, statut)


def _autorise(requete: web.Request) -> bool:
    """Vérifie le secret partagé avec le site.

    Sans `API_SECRET` configuré, refuse tout : sinon un appelant sans en-tête
    présenterait `"" == ""` et l'API serait grande ouverte.

    `compare_digest` plutôt que `==` : la comparaison native s'arrête au premier
    octet différent, ce qui laisse mesurer le préfixe correct. Le risque est
    théorique sur un lien Internet, le coût nul.
    """
    if not settings.API_SECRET:
        return False
    fourni = requete.headers.get(ENTETE_SECRET, "")
    return bool(fourni) and hmac.compare_digest(fourni, settings.API_SECRET)


def _montant(brut: Any, champ: str) -> Decimal:
    """Lit un montant saisi sur le site, avec la grammaire de Discord.

    Une seule grammaire pour les deux façades : `100T`, `50 6P` ou `2.71 PØ`
    recopié depuis un post fonctionnent ici comme dans `/fourchette prix`.
    """
    try:
        return parse_money(str(brut))
    except MoneyError as erreur:
        raise RequeteInvalide(f"{champ} : {erreur}") from erreur


def _heure(brut: Any) -> str:
    """Normalise une heure 'HH:MM'. `09:5` devient `09:05`."""
    texte = str(brut or "").strip()
    try:
        heures, minutes = (int(part) for part in texte.split(":", 1))
        if not (0 <= heures <= 23 and 0 <= minutes <= 59):
            raise ValueError
    except ValueError as erreur:
        raise RequeteInvalide(
            f"Heure invalide : « {texte} ». Format attendu : HH:MM (ex. 09:00)."
        ) from erreur
    return f"{heures:02d}:{minutes:02d}"


def _fuseau(brut: Any) -> str:
    from zoneinfo import ZoneInfo

    texte = str(brut or "").strip()
    try:
        ZoneInfo(texte)
    except Exception as erreur:
        raise RequeteInvalide(
            f"Fuseau inconnu : « {texte} ». Exemple : Europe/Paris."
        ) from erreur
    return texte


async def _corps_json(requete: web.Request) -> dict:
    """Corps JSON de la requête, ou une erreur lisible.

    Un corps vide est accepté comme `{}` : `/api/apercu` et `/api/publier`
    s'appellent sans argument.
    """
    if not requete.can_read_body:
        return {}
    try:
        charge = await requete.json()
    except Exception as erreur:
        raise RequeteInvalide("Corps de requête illisible (JSON attendu).") from erreur
    if charge is None:
        return {}
    if not isinstance(charge, dict):
        raise RequeteInvalide("Le corps doit être un objet JSON.")
    return charge


async def _fourchette(bot, requete: web.Request, charge: dict | None = None):
    """Fourchette à utiliser : celle de la requête, sinon l'union des réglées.

    Permet de simuler une autre fourchette sur le site sans toucher aux
    réglages, comme `/promos min: max:` dans Discord.

    Sans paramètre, couvre **l'union** de toutes les fourchettes configurées :
    la page sert à voir ce qui bouge dans tout ce qui est surveillé, et en
    désigner une obligerait le site à choisir laquelle.
    """
    source = {**(charge or {}), **dict(requete.query)}

    # Ce qui est saisi est validé **avant** de consulter la base : sinon
    # `?min=abc` sur un bot sans fourchette répondrait « aucune fourchette
    # configurée », en cachant la faute de frappe qui est la vraie cause.
    donne = {}
    if source.get("min") not in (None, ""):
        donne["prix_min"] = _montant(source["min"], "Prix minimum")
    if source.get("max") not in (None, ""):
        donne["prix_max"] = _montant(source["max"], "Prix maximum")

    if len(donne) == 2:
        return donne["prix_min"], donne["prix_max"]

    fourchettes = await bot.store.fourchettes()
    if not fourchettes:
        raise RequeteInvalide(
            "Aucune fourchette configurée : indique un minimum et un maximum, "
            "ou crée une fourchette avec `/fourchette ajouter` dans Discord."
        )

    # Une seule borne saisie : l'autre vient de l'union. Refuser une saisie dont
    # l'intention est claire serait gratuit.
    return (
        donne.get("prix_min", min(Decimal(f["prix_min"]) for f in fourchettes)),
        donne.get("prix_max", max(Decimal(f["prix_max"]) for f in fourchettes)),
    )


async def _config_json(bot) -> dict[str, Any]:
    """La config telle que le site la lit.

    `fourchettes()` et non `config()["fourchettes"]` : c'est l'accesseur qui
    applique les migrations (`salon_id` unique, puis config plate), et la config
    brute d'une base ancienne n'en contiendrait aucune.
    """
    return config_en_json(await bot.store.config(), await bot.store.fourchettes())


async def _date_du_jour(bot) -> str:
    config = await bot.store.config()
    return maintenant_local(config["fuseau"]).strftime("%Y-%m-%d")


def enregistrer_routes(app: web.Application, bot) -> None:
    """Branche `/api/*` sur l'application aiohttp existante.

    Les routes du cron (`/health`, `/tick`) gardent leur propre jeton : ce
    module n'y touche pas.
    """

    @web.middleware
    async def garde(requete: web.Request, handler):
        """Secret partagé, erreurs de validation, réponses toujours en JSON.

        Le paramètre s'appelle `handler` et non `suivant` : aiohttp le passe en
        argument nommé, un autre nom lève un `TypeError` à la première requête.
        """
        if not requete.path.startswith("/api/"):
            return await handler(requete)

        if not _autorise(requete):
            # Message identique pour « pas de secret », « mauvais secret » et
            # « API_SECRET absent » : préciser lequel renseignerait un curieux.
            return _erreur("Secret manquant ou invalide.", 401)

        try:
            return await handler(requete)
        except RequeteInvalide as erreur:
            return _erreur(str(erreur), 400)
        except SourceError as erreur:
            # Message déjà nettoyé de la clé d'API par `src/source.py`.
            return _erreur(str(erreur), 502)
        except web.HTTPException:
            raise
        except Exception as erreur:
            # Type seul : le détail pourrait contenir une URL avec la clé d'API.
            log.exception("Erreur inattendue sur %s", requete.path)
            return _erreur(f"Erreur inattendue ({type(erreur).__name__}).", 500)

    app.middlewares.append(garde)

    # --- État ---------------------------------------------------------------

    async def etat(_: web.Request) -> web.Response:
        return _json(
            etat_en_json(
                pret=bot.is_ready(),
                source=decrire(bot.source),
                derniere_publication=await bot.store.derniere_publication(),
                persistant=bot.store.persistant,
            )
        )

    # --- Promotions ---------------------------------------------------------

    async def promos(requete: web.Request) -> web.Response:
        prix_min, prix_max = await _fourchette(bot, requete)
        meta, batiments = await bot.charger()
        trouvees = find_promos(batiments, prix_min, prix_max)
        return _json(promos_en_json(trouvees, meta, await _date_du_jour(bot)))

    # --- Configuration ------------------------------------------------------

    async def config_lire(_: web.Request) -> web.Response:
        return _json(await _config_json(bot))

    async def config_ecrire(requete: web.Request) -> web.Response:
        charge = await _corps_json(requete)

        inconnus = sorted(set(charge) - set(CHAMPS_MODIFIABLES))
        if inconnus:
            # Nommer les champs refusés *et* où les régler : sans la seconde
            # moitié, la page a l'air cassée alors qu'elle applique une règle.
            raise RequeteInvalide(
                f"Champs non modifiables depuis le site : {', '.join(inconnus)}. "
                "Les fourchettes et leurs salons, la mention et la liste d'accès "
                "se règlent par commande Discord."
            )

        # Tout valider avant d'écrire : un PATCH à moitié appliqué serait pire
        # qu'un refus, puisqu'il faudrait deviner ce qui est passé.
        champs: dict[str, Any] = {}
        if "heure" in charge:
            champs["heure"] = _heure(charge["heure"])
        if "fuseau" in charge:
            champs["fuseau"] = _fuseau(charge["fuseau"])

        if champs:
            await bot.store.maj_config(**champs)

        if "heure" in champs:
            # Comme `/fourchette heure` : changer l'heure exprime l'intention de
            # publier à la nouvelle, donc on oublie la marque du jour.
            await bot.store.oublier_publication()

        return _json(await _config_json(bot))

    # --- Template -----------------------------------------------------------

    async def template_lire(_: web.Request) -> web.Response:
        return _json({
            "template": await bot.store.template(),
            # Le site affiche la liste à côté de l'éditeur : une seule source de
            # vérité, donc pas de liste recopiée dans le code du site qui
            # divergerait au prochain placeholder ajouté.
            "placeholders": list(PLACEHOLDERS),
        })

    async def template_ecrire(requete: web.Request) -> web.Response:
        charge = await _corps_json(requete)
        modele = charge.get("template")
        if modele is None:
            raise RequeteInvalide("Aucun template fourni (clé `template` attendue).")

        try:
            valider_template(modele)
        except TemplateError as erreur:
            raise RequeteInvalide(str(erreur)) from erreur

        await bot.store.set_template(modele)

        # Signalés, pas refusés : un `{prixx}` laisse le template valide, et te
        # bloquer sur une faute de frappe serait pire que te l'indiquer.
        return _json({
            "template": modele,
            "inconnus": sorted(placeholders_inconnus(modele)),
        })

    # --- Aperçu -------------------------------------------------------------

    async def apercu(requete: web.Request) -> web.Response:
        """Rend le post du jour sans rien publier ni enregistrer.

        Le template essayé n'est pas sauvegardé : on doit pouvoir l'essayer sans
        l'imposer à la publication du lendemain.
        """
        charge = await _corps_json(requete)
        modele = charge.get("template")
        if modele is None:
            modele = await bot.store.template()
        else:
            try:
                valider_template(modele)
            except TemplateError as erreur:
                raise RequeteInvalide(str(erreur)) from erreur

        prix_min, prix_max = await _fourchette(bot, requete, charge)
        meta, batiments = await bot.charger()
        trouvees = find_promos(batiments, prix_min, prix_max)
        date = await _date_du_jour(bot)

        if not trouvees:
            # Le même texte que le bot posterait : l'aperçu doit montrer ce cas
            # aussi, c'est celui où l'on doute que le bot ait tourné.
            return _json({
                "messages": [{"content": message_aucune_promo(prix_min, prix_max, meta)}],
                "promos": promos_en_json(trouvees, meta, date),
            })

        embeds, contenu = construire_embeds(trouvees, meta, modele, date)
        config = await bot.store.config()
        # Aucune mention dans un aperçu : il n'appartient à aucun salon, donc à
        # aucun serveur, et un rôle n'existe que dans le sien. Mentionner « le »
        # rôle voudrait en choisir un arbitrairement.
        role_id = None

        # Découpé comme à la publication : l'aperçu doit montrer les vrais
        # messages, y compris quand les limites Discord en imposent plusieurs.
        messages = []
        for index, paquet in enumerate(grouper_messages(embeds)):
            message: dict[str, Any] = {"embeds": paquet}
            if index == 0:
                entete = " ".join(
                    part for part in (f"<@&{role_id}>" if role_id else "", contenu) if part
                )
                if entete:
                    message["content"] = entete
            messages.append(message)

        return _json({
            "messages": messages,
            "promos": promos_en_json(trouvees, meta, date),
            "inconnus": sorted(placeholders_inconnus(modele)),
        })

    # --- Publication à la demande -------------------------------------------

    async def publier(_: web.Request) -> web.Response:
        if not bot.is_ready():
            return _erreur(
                "Le bot n'est pas connecté à Discord. Réessaie dans un instant.",
                503,
            )
        # `forcer=True` : le bouton du site est un ordre explicite, pas une
        # vérification d'horaire. L'idempotence quotidienne concerne le cron.
        return _json({"resultat": await bot.publier_si_lheure(forcer=True)})

    app.router.add_get("/api/etat", etat)
    app.router.add_get("/api/promos", promos)
    app.router.add_get("/api/config", config_lire)
    app.router.add_patch("/api/config", config_ecrire)
    app.router.add_get("/api/template", template_lire)
    app.router.add_put("/api/template", template_ecrire)
    app.router.add_post("/api/apercu", apercu)
    app.router.add_post("/api/publier", publier)

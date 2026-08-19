"""Provenance des données Empire Immo.

Deux sources interchangeables derrière `DataSource.fetch()` :
  - `CsvFileSource` : un fichier du dépôt (dépannage, tests, mode hors ligne) ;
  - `ApiSource`     : l'export servi par le jeu.

Le reste du bot ne connaît que `fetch()` : basculer de l'un à l'autre se règle
par variables d'environnement (voir `bot.creer_source`).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

log = logging.getLogger(__name__)

#: URL de l'export du monde 8. `{api_key}` est remplacé par la clé.
URL_API_DEFAUT = (
    "https://monde8.empireimmo.com/api/buildings_batiments_entreprise.csv"
    "?key={api_key}"
)

#: Nom du paramètre d'authentification attendu par l'API.
PARAM_CLE = "key"

#: Marqueur de clé dans une URL modèle.
PLACEHOLDER_CLE = "{api_key}"

#: Délai maximal d'attente de l'API, en secondes. L'export fait quelques
#: dizaines de kilo-octets : au-delà, mieux vaut échouer et réessayer au ping
#: suivant que bloquer la boucle de publication.
DELAI_MAX = 30


class SourceError(RuntimeError):
    """Données inaccessibles ou inutilisables.

    Le message est destiné à l'utilisateur (affiché dans Discord) : il ne doit
    donc jamais contenir la clé d'API.
    """


class DataSource(ABC):
    """Fournit le contenu brut de l'export, au format CSV."""

    @abstractmethod
    async def fetch(self) -> str:
        """Renvoie le CSV complet (en-tête commentée incluse)."""


class CsvFileSource(DataSource):
    """Lit l'export depuis un fichier du dépôt."""

    def __init__(self, chemin: str | Path):
        self.chemin = Path(chemin)

    async def fetch(self) -> str:
        if not self.chemin.exists():
            raise FileNotFoundError(f"Export introuvable : {self.chemin}")
        return self.chemin.read_text(encoding="utf-8")


def construire_url(modele: str, cle: str) -> str:
    """Insère la clé d'API dans l'URL.

    Trois cas, pour tolérer ce que tu colles réellement :
      - `...?key={api_key}` → le placeholder est remplacé ;
      - `...` (sans clé)    → `?key=<cle>` est ajouté ;
      - `...?key=abc`       → laissé intact (clé déjà présente dans l'URL).
    """
    if PLACEHOLDER_CLE in modele:
        if not cle:
            raise SourceError(
                "L'URL de l'API attend une clé mais `EMPIRE_API_KEY` est vide. "
                "Renseigne-la dans les variables d'environnement."
            )
        # Échappée : une clé contenant & ou = ne casse pas l'URL.
        return modele.replace(PLACEHOLDER_CLE, quote(cle, safe=""))

    if not cle:
        return modele

    parties = urlparse(modele)
    query = parse_qs(parties.query, keep_blank_values=True)
    if query.get(PARAM_CLE) and query[PARAM_CLE][0]:
        return modele  # déjà authentifiée, on ne l'écrase pas

    query[PARAM_CLE] = [cle]
    return urlunparse(parties._replace(query=urlencode(query, doseq=True)))


def _masquer(url: str) -> str:
    """Retire la clé d'une URL avant de la journaliser."""
    parties = urlparse(url)
    query = parse_qs(parties.query, keep_blank_values=True)
    if PARAM_CLE not in query:
        return url
    # Reconstruit à la main : `urlencode` échapperait les astérisques.
    reste = [
        f"{nom}={valeur}"
        for nom, valeurs in query.items()
        if nom != PARAM_CLE
        for valeur in valeurs
    ]
    nouvelle = "&".join([*reste, f"{PARAM_CLE}=***"])
    return urlunparse(parties._replace(query=nouvelle))


def _message_erreur(statut: int, corps: str, url_masquee: str) -> str:
    """Compose un message lisible à partir d'une réponse d'erreur.

    L'API du jeu renvoie un JSON du type
    `{"error":true,"code":401,"message":"Clé API invalide ou révoquée."}` :
    son `message` est plus précis que tout ce qu'on pourrait deviner, on le
    reprend tel quel quand il est présent.
    """
    detail = ""
    try:
        charge = json.loads(corps)
    except (ValueError, TypeError):
        charge = None
    if isinstance(charge, dict) and isinstance(charge.get("message"), str):
        detail = charge["message"].strip()

    if detail:
        base = f"L'API a répondu {statut} : {detail}"
    elif statut in (401, 403):
        base = (
            f"L'API a refusé la requête ({statut}) : clé d'API invalide ou "
            "expirée."
        )
    elif statut == 404:
        base = f"Export introuvable ({url_masquee}) : l'URL a peut-être changé."
    else:
        base = f"L'API a répondu {statut}."

    if statut in (401, 403):
        return f"{base} Vérifie `EMPIRE_API_KEY`."
    if statut >= 500:
        return f"{base} Réessai au prochain passage."
    return base


def _ressemble_a_un_csv(texte: str) -> bool:
    """Un export valide commence par l'en-tête commentée ou la ligne de colonnes.

    Garde-fou contre une page de login ou de maintenance renvoyée en 200 : la
    parser produirait « 0 bâtiment » au lieu d'une erreur visible.
    """
    debut = texte.lstrip()[:400].lower()
    if not debut:
        return False
    if debut.startswith("<"):  # HTML/XML
        return False
    return debut.startswith("#") or "type,nom" in debut


class ApiSource(DataSource):
    """Récupère l'export depuis l'API du jeu."""

    def __init__(
        self,
        url: str = URL_API_DEFAUT,
        cle: str = "",
        entetes: dict[str, str] | None = None,
        delai: int = DELAI_MAX,
    ):
        self.modele = url
        self.cle = cle
        self.entetes = entetes or {}
        self.delai = delai

    @property
    def url_masquee(self) -> str:
        """URL sans la clé, pour les logs et les messages Discord."""
        return _masquer(self.modele)

    async def fetch(self) -> str:
        import aiohttp

        url = construire_url(self.modele, self.cle)
        delai = aiohttp.ClientTimeout(total=self.delai)

        try:
            async with aiohttp.ClientSession(timeout=delai) as session:
                async with session.get(url, headers=self.entetes) as reponse:
                    if reponse.status >= 400:
                        texte_erreur = await reponse.text()
                        raise SourceError(
                            _message_erreur(reponse.status, texte_erreur, self.url_masquee)
                        )
                    texte = await reponse.text()
        except SourceError:
            raise
        except aiohttp.ClientError as erreur:
            # `erreur` peut contenir l'URL complète : on ne l'insère pas.
            raise SourceError(
                f"API injoignable ({type(erreur).__name__}). "
                "Réessai au prochain passage."
            ) from erreur
        except TimeoutError as erreur:
            raise SourceError(
                f"L'API n'a pas répondu en {self.delai} s. "
                "Réessai au prochain passage."
            ) from erreur

        if not _ressemble_a_un_csv(texte):
            raise SourceError(
                "L'API a répondu autre chose qu'un CSV (page de login ou de "
                "maintenance ?). Aucune donnée exploitable."
            )

        log.info("Export récupéré depuis l'API (%d caractères).", len(texte))
        return texte


def decrire(source: DataSource) -> str:
    """Provenance des données en une ligne, sans jamais la clé d'API."""
    if isinstance(source, ApiSource):
        return f"🌐 API Empire Immo\n-# {source.url_masquee}"
    return f"📄 fichier local\n-# {getattr(source, 'chemin', '?')}"


@dataclass(frozen=True)
class Diagnostic:
    """Résultat d'un test de la source, prêt à être affiché.

    `ok` répond à la seule question qui compte : « le bot pourrait-il publier
    maintenant ? ». Une absence de promotion n'est donc pas un échec, mais un
    export vide en est un.
    """

    source: str
    ok: bool = False
    erreur: str = ""
    duree_ms: int = 0
    taille: int = 0
    batiments: int = 0
    promos: int = 0
    monde: str = ""
    mise_a_jour: str = ""
    #: Noms des promotions trouvées, dans l'ordre du CSV.
    exemples: list[str] = field(default_factory=list)


async def diagnostiquer(
    source: DataSource, horloge: Callable[[], float] = time.monotonic
) -> Diagnostic:
    """Teste une source de bout en bout : récupération, parsing, comptage.

    N'échoue jamais par exception : la commande `/reglages source tester` doit pouvoir
    afficher le problème au lieu de renvoyer une erreur Discord opaque. Toute
    panne devient un `Diagnostic` avec `ok=False` et un `erreur` lisible —
    donc dépourvu de la clé d'API, comme tout message de ce module.
    """
    from src.promos import parse_csv

    description = decrire(source)
    debut = horloge()

    try:
        texte = await source.fetch()
    except SourceError as erreur:
        return Diagnostic(source=description, erreur=str(erreur))
    except FileNotFoundError as erreur:
        return Diagnostic(source=description, erreur=str(erreur))
    except Exception as erreur:  # dernier filet : le type seul, jamais l'URL
        log.exception("Diagnostic de la source en échec")
        return Diagnostic(
            source=description, erreur=f"Erreur inattendue ({type(erreur).__name__})."
        )

    duree_ms = int((horloge() - debut) * 1000)

    try:
        meta, batiments = parse_csv(texte)
    except Exception as erreur:
        return Diagnostic(
            source=description,
            erreur=f"CSV illisible ({type(erreur).__name__}).",
            duree_ms=duree_ms,
            taille=len(texte),
        )

    # Toutes les promotions de l'export, sans filtre de fourchette : on teste
    # la source, pas la sélection du jour.
    en_promo = [batiment for batiment in batiments if batiment.promotion > 0]

    return Diagnostic(
        source=description,
        ok=bool(batiments),
        erreur="" if batiments else "L'export ne contient aucun bâtiment.",
        duree_ms=duree_ms,
        taille=len(texte),
        batiments=len(batiments),
        promos=len(en_promo),
        monde=meta.monde,
        mise_a_jour=meta.mise_a_jour,
        exemples=[batiment.nom for batiment in en_promo],
    )

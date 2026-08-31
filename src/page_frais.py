"""Page web des frais de gestion : coller le tableau du jeu, récupérer les frais.

Ce que la page remplace : ouvrir le jeu, lire treize filiales, taper treize
`/filiales releve` dans Discord, puis `/frais export`. Ici, on colle le tableau
et on repart avec les deux colonnes que l'import du jeu réclame.

Elle est **ouverte** : convertir ne demande rien et n'écrit rien. C'est ce qui
permet de coller depuis n'importe quel navigateur sans rien configurer, et c'est
sans conséquence tant que rien n'est enregistré — les frais se calculent sur ce
qu'on colle, et le résultat ne sort pas de la page.

Tout ce qui entre ressort dans la page, et ce qui entre est un collage
quelconque : chaque valeur passe donc par `html.escape`. Sans ça, un `<script>`
collé s'exécuterait chez celui qui colle, et un lien piégé le ferait coller par
un autre.

Le HTML est écrit à la main, sans moteur de gabarit : une dépendance de plus pour
une page unique, sur un hébergement gratuit qui la garderait en mémoire, ne se
justifie pas. `string.Template` plutôt que `str.format` ou une f-string : les
accolades du CSS seraient à doubler partout, et une accolade oubliée ne se verrait
qu'à l'exécution.
"""

from __future__ import annotations

import html
import time
from string import Template

from aiohttp import web

from src.collage import Lecture, lire_collage, vers_filiales
from src.filiales import Filiale, total_frais, vers_import
from src.money import format_money
from src.motdepasse import DUREE_JETON, signer, verifie, verifier_jeton
from src.schedule import maintenant_local

#: Chemin de la page. Court parce qu'il se tape à la main dans un navigateur, et
#: sans `/api/` pour rester hors du secret partagé avec le site de contrôle.
CHEMIN = "/frais"

#: Chemin de l'enregistrement, distinct de la conversion.
#:
#: Deux boutons, deux adresses : c'est ce qui rend le journal d'accès lisible — on
#: y voit qui a *écrit* — et ce qui évite qu'un rechargement de page reconverti
#: se transforme en écriture.
CHEMIN_ENREGISTRER = f"{CHEMIN}/enregistrer"

_STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem;
  background: #0d1117; color: #e6edf3;
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; font-weight: 600; }
p.aide, p.vide { color: #9198a1; margin: .25rem 0 1.5rem; }
label { display: block; margin: 1rem 0 .35rem; color: #9198a1; }
select, textarea, button, input {
  font: inherit; color: inherit;
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: .5rem .65rem;
}
textarea {
  width: 100%; font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 13px; white-space: pre; overflow-wrap: normal;
}
button {
  background: #238636; border-color: #2ea043; cursor: pointer;
  padding: .5rem 1.1rem; margin-top: 1rem;
}
button:hover { background: #2ea043; }
button + button { margin-left: .5rem; }
button.sobre { background: #21262d; border-color: #30363d; }
button.sobre:hover { background: #30363d; }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #21262d; }
th { color: #9198a1; font-weight: 600; }
td.nom { white-space: pre; font-family: ui-monospace, Consolas, monospace; }
td.montant, th.montant { text-align: right; font-variant-numeric: tabular-nums; }
tfoot td, tfoot th { border-bottom: none; border-top: 1px solid #30363d; font-weight: 600; }
.perte { color: #f85149; }
input[type="password"] { width: 22rem; max-width: 100%; }
p.succes {
  border-left: 3px solid #2ea043; padding: .1rem 0 .1rem .9rem;
  margin: 1.5rem 0 0;
}
section.refus { border-left: 3px solid #d29922; padding-left: .9rem; }
section.refus li { margin-bottom: .6rem; }
section.refus code {
  display: block; font-size: 13px; color: #9198a1; white-space: pre-wrap;
}
"""

_GABARIT = Template(
    """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frais de gestion — Empire Immo</title>
<style>$style</style>
</head>
<body>
<main>
<h1>Frais de gestion</h1>
<p class="aide">Colle le tableau des filiales du jeu — titres compris — et
récupère les deux colonnes que son import réclame. 7 % des bénéfices, zéro sur
une perte.</p>
<form method="post" action="$chemin">
$menu
<label for="collage">Tableau collé depuis le jeu</label>
<textarea id="collage" name="collage" rows="12" spellcheck="false"
 placeholder="Filiale&#9;Trésorerie&#9;Résultat d'exploitation&#9;Résultat NET&#9;Bénéfices ou pertes">$collage</textarea>
<label for="motdepasse">Mot de passe — pour enregistrer les relevés</label>
<input type="password" id="motdepasse" name="motdepasse"
 autocomplete="current-password"
 placeholder="/reglages motdepasse dans Discord — inutile si ce navigateur est déjà identifié">
<button type="submit">Convertir</button>
<button type="submit" class="sobre" formaction="$chemin_enregistrer">Convertir et
enregistrer</button>
</form>
$resultat
</main>
</body>
</html>
"""
)


def _menu(serveurs: list[tuple[str, str]], choisi: str) -> str:
    """Le menu déroulant des entreprises, ou le dire quand il n'y en a aucune.

    Un menu vide se lirait comme une panne de la page, alors que la cause est
    ailleurs : le bot n'est pas connecté, ou n'a été invité nulle part.

    L'`id` est la valeur et le nom l'étiquette : c'est par l'id que la
    configuration d'une entreprise est choisie, et deux entreprises peuvent
    porter le même nom.
    """
    if not serveurs:
        return (
            '<p class="vide">Aucun serveur : le bot n\'est pas connecté à Discord, '
            "ou n'a été invité dans aucune entreprise. La conversion fonctionne "
            "quand même — c'est l'enregistrement qui a besoin d'une entreprise.</p>"
        )
    options = "\n".join(
        '<option value="{id}"{marque}>{nom}</option>'.format(
            id=html.escape(str(identifiant)),
            nom=html.escape(str(nom)),
            marque=" selected" if str(identifiant) == str(choisi) else "",
        )
        for identifiant, nom in serveurs
    )
    return (
        '<label for="serveur">Entreprise</label>\n'
        f'<select id="serveur" name="serveur">\n{options}\n</select>'
    )


def _tableau(filiales: list[Filiale]) -> str:
    """Le tableau lisible : ce qu'on recoupe à l'œil avec le jeu.

    En notation courte du jeu (`24.12 PØ`), celle des posts du bot : dix-neuf
    chiffres alignés ne se comparent pas de l'œil. Les chiffres exacts sont dans
    le bloc à copier, qui est une entrée machine.

    Le nom garde ses espaces (`white-space: pre` en CSS) : les doubles espaces
    sont la clé d'import du jeu, et le navigateur les réduirait à un seul —
    invisible, donc impossible à recouper si l'import échouait.
    """
    lignes = "\n".join(
        "<tr><td class=\"nom\">{nom}</td>"
        '<td class="montant{perte}">{benefices}</td>'
        '<td class="montant">{frais}</td></tr>'.format(
            nom=html.escape(filiale.nom),
            benefices=html.escape(format_money(filiale.benefices)),
            frais=html.escape(format_money(filiale.frais)),
            perte=" perte" if filiale.en_perte else "",
        )
        for filiale in filiales
    )
    return f"""<table>
<thead><tr><th>Filiale</th><th class="montant">Bénéfices ou pertes</th>
<th class="montant">Frais de gestion</th></tr></thead>
<tbody>
{lignes}
</tbody>
<tfoot><tr><th>{len(filiales)} filiale(s)</th><td></td>
<td class="montant">{html.escape(format_money(total_frais(filiales)))}</td></tr></tfoot>
</table>"""


def _refus(lecture: Lecture) -> str:
    """Les lignes non lues, avec leur numéro et la raison.

    Sautée en silence, une filiale manquerait au tableau du soir sans que rien ne
    l'annonce — et c'est le total du jour qui serait faux. Le numéro est celui du
    collage, pour retrouver la ligne dans la zone de texte.
    """
    if not lecture.refuses:
        return ""
    lignes = "\n".join(
        f"<li><strong>Ligne {refus.numero}</strong> — {html.escape(refus.raison)}"
        f"<code>{html.escape(refus.ligne)}</code></li>"
        for refus in lecture.refuses
    )
    return f"""<section class="refus">
<h2>{len(lecture.refuses)} ligne(s) non lue(s)</h2>
<ul>
{lignes}
</ul>
</section>"""


def _import(filiales: list[Filiale]) -> str:
    """Le bloc à copier dans l'import du jeu : nom, tabulation, frais.

    Les chiffres exacts, par `filiales.vers_import` : la même sortie que
    `/frais export`, à l'octet près. La recopier ici la ferait divergier de ce
    que le fichier Discord contient, et l'écart ne se verrait qu'au jeu.

    Les fins de ligne CRLF de `vers_import` deviennent des LF dans la zone de
    texte — le navigateur normalise. Sans conséquence : l'import du jeu est
    lui-même une zone de texte, dont le navigateur rétablit les CRLF à l'envoi.
    """
    return f"""<h2>À copier dans l'import du jeu</h2>
<textarea id="import" rows="{max(3, len(filiales))}" readonly
 onclick="this.select()">{html.escape(vers_import(filiales))}</textarea>"""


def _succes(entreprise: str, filiales: list[Filiale]) -> str:
    """Ce qui vient d'être écrit, et où.

    Le menu propose plusieurs entreprises : un « ✅ » muet laisserait douter de
    laquelle vient d'être remplie, et une erreur de menu ne se verrait qu'au post
    du soir.
    """
    return (
        f'<p class="succes">✅ <strong>{len(filiales)} relevé(s)</strong> '
        f"enregistré(s) pour <strong>{html.escape(entreprise)}</strong> — le "
        "tableau du soir les reprendra.<br>"
        "<span class=\"aide\">Les filiales absentes du collage n'ont pas été "
        "touchées ; elles se retirent avec <code>/frais retirer</code>.</span></p>"
    )


def _refus_acces(raison: str) -> str:
    """Le refus d'écrire, dit dans la page plutôt que par un code HTTP seul.

    Le collage est encore dans la zone de texte : il ne reste qu'à taper le mot
    de passe et à recliquer. Une page d'erreur nue le ferait recoller depuis le
    jeu.
    """
    return (
        '<section class="refus">\n'
        "<h2>Rien n'a été enregistré</h2>\n"
        f"<p>{raison}</p>\n"
        "</section>"
    )


def _resultat(lecture: Lecture, filiales: list[Filiale]) -> str:
    """Tout ce qu'une conversion affiche, ou pourquoi elle n'affiche rien.

    Un tableau vide et un total de 0 Ø se liraient comme un vrai résultat — le
    cas du clic avant le collage.
    """
    if not filiales:
        return _refus(lecture) + (
            '<p class="vide">Rien à convertir : colle le tableau des filiales du '
            "jeu dans la zone ci-dessus, titres compris.</p>"
        )
    return _tableau(filiales) + _import(filiales) + _refus(lecture)


def rendre(
    serveurs: list[tuple[str, str]],
    collage: str = "",
    serveur: str = "",
    resultat: str = "",
) -> str:
    """La page complète. Pure : c'est ce qui la rend éprouvable sans HTTP."""
    return _GABARIT.substitute(
        style=_STYLE,
        chemin=CHEMIN,
        chemin_enregistrer=CHEMIN_ENREGISTRER,
        menu=_menu(serveurs, serveur),
        # Le collage revient dans la zone de texte : sinon corriger une ligne
        # refusée obligerait à tout recoller, et le bouton d'enregistrement
        # n'aurait plus rien à envoyer.
        collage=html.escape(collage),
        resultat=resultat,
    )


def _serveurs(bot) -> list[tuple[str, str]]:
    """Les entreprises où le bot est invité, `(id, nom)`.

    Prises sur Discord et non sur le cache des noms en base : le cache ne connaît
    que les serveurs dont un salon a déjà été réglé, et la page servirait alors à
    tout sauf au premier réglage.
    """
    return [(str(serveur.id), str(serveur.name)) for serveur in bot.guilds]


async def _aujourdhui(magasin) -> str:
    """La date du jour dans le fuseau réglé, au format que la base retient.

    Sur le magasin d'une entreprise quand elle est choisie : datée d'ailleurs,
    une ligne se lirait « relevé d'hier » un jour sur deux dans une entreprise
    qui n'a pas le même décalage.
    """
    return maintenant_local((await magasin.config())["fuseau"]).strftime("%Y-%m-%d")


def _magasin(bot, serveur: str):
    """Le magasin de l'entreprise choisie, ou le commun si aucune ne l'est.

    Le commun ne sert qu'à lire un fuseau horaire : rien n'est écrit dedans, et
    ce serait un tableau que personne ne publie.
    """
    if serveur and any(serveur == identifiant for identifiant, _ in _serveurs(bot)):
        return bot.store.pour(serveur)
    return bot.store


def _reponse(page: str, statut: int = 200) -> web.Response:
    """La page, jamais mise en cache.

    Elle contient les relevés collés à l'instant : servie depuis un cache, elle
    montrerait ceux de la fois précédente, et l'on croirait le collage sans effet.

    Un refus d'écrire répond `403` **avec la page** : le navigateur affiche le
    corps, le collage reste dans la zone de texte, et le journal d'accès garde la
    trace d'un refus — invisible si tout répondait `200`.
    """
    return web.Response(
        text=page,
        status=statut,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


def nom_cookie(serveur_id: str | int) -> str:
    """Le cookie qui identifie ce navigateur pour **une** entreprise.

    Un nom par entreprise : un seul navigateur peut en suivre plusieurs, et un
    nom unique ferait remplacer le cookie de l'une par celui de l'autre à chaque
    enregistrement. Le nom ne fait pas la sécurité — c'est la signature qui porte
    l'entreprise (voir `src/motdepasse.py`).
    """
    return f"eibot_frais_{serveur_id}"


def _identifier(reponse: web.Response, serveur: str, trace: dict) -> None:
    """Pose le cookie qui dispense de retaper le mot de passe.

    `httponly` : un script de la page ne doit pas pouvoir le lire — volé, il
    vaudrait mot de passe pour un mois. `samesite=Lax` : il ne part pas avec un
    formulaire posté depuis un autre site, ce qui serait le moyen de faire écrire
    un tableau à un navigateur identifié. `secure` : Render sert la page en HTTPS,
    et sans ce drapeau le cookie voyagerait en clair sur un réseau partagé.
    """
    expiration = int(time.time()) + DUREE_JETON
    reponse.set_cookie(
        nom_cookie(serveur),
        signer(trace, serveur, expiration),
        max_age=DUREE_JETON,
        httponly=True,
        samesite="Lax",
        secure=True,
        path=CHEMIN,
    )


def enregistrer_routes(app: web.Application, bot) -> None:
    """Branche `/frais` sur l'application aiohttp existante."""

    async def afficher(_: web.Request) -> web.Response:
        return _reponse(rendre(_serveurs(bot)))

    async def convertir(requete: web.Request) -> web.Response:
        """Colle, calcule, affiche. N'écrit rien : la page est ouverte à tous."""
        donnees = await requete.post()
        collage = str(donnees.get("collage", ""))
        serveur = str(donnees.get("serveur", ""))

        lecture = lire_collage(collage)
        filiales = vers_filiales(lecture, await _aujourdhui(_magasin(bot, serveur)))

        return _reponse(
            rendre(
                _serveurs(bot),
                collage=collage,
                serveur=serveur,
                resultat=_resultat(lecture, filiales),
            )
        )

    async def enregistrer(requete: web.Request) -> web.Response:
        """Colle, calcule, **écrit** — mot de passe ou cookie à l'appui.

        L'équivalent d'un lot de `/frais releve`. Sans ce verrou, l'adresse
        suffirait à remplacer les relevés du jour de n'importe quelle entreprise :
        la page est ouverte à tous, et son menu les nomme toutes.

        L'autorisation est vérifiée **avant** de lire le collage : un refus ne doit
        rien apprendre de plus que le refus lui-même, et surtout ne rien écrire.
        """
        donnees = await requete.post()
        collage = str(donnees.get("collage", ""))
        serveur = str(donnees.get("serveur", ""))
        # Le mot de passe arrive par le corps de la requête, jamais par l'adresse :
        # `JournalSansSecret` ne journalise que le chemin, mais Render et le
        # navigateur garderaient l'URL complète.
        saisi = str(donnees.get("motdepasse", ""))

        serveurs = _serveurs(bot)

        def refuser(raison: str) -> web.Response:
            return _reponse(
                rendre(serveurs, collage, serveur, _refus_acces(raison)), statut=403
            )

        # L'entreprise est cherchée dans les serveurs du bot : le menu n'en propose
        # pas d'autre, mais le formulaire s'envoie à la main, et un id quelconque
        # écrirait dans un tiroir que personne ne publie.
        entreprise = dict(serveurs).get(serveur)
        if entreprise is None:
            return refuser(
                "Choisis une <strong>entreprise</strong> dans le menu : le bot "
                "n'est pas dans celle qui a été envoyée."
            )

        magasin = bot.store.pour(serveur)
        trace = await magasin.motdepasse_page()
        if trace is None:
            # Fermée, et non ouverte à tous : accepter faute d'empreinte ouvrirait
            # en écriture toutes les entreprises qui n'ont rien réglé.
            return refuser(
                f"Aucun mot de passe n'est réglé pour <strong>"
                f"{html.escape(entreprise)}</strong>. Tape "
                "<code>/reglages motdepasse</code> dans son serveur Discord : le "
                "bot en tire un et te le montre."
            )

        par_mot_de_passe = verifie(trace, saisi)
        if not par_mot_de_passe and not verifier_jeton(
            trace,
            requete.cookies.get(nom_cookie(serveur), ""),
            serveur,
            int(time.time()),
        ):
            return refuser(
                "Mot de passe refusé. <code>/reglages motdepasse</code> dans "
                "Discord en tire un nouveau — ce qui déconnecte les navigateurs "
                "déjà identifiés."
            )

        lecture = lire_collage(collage)
        filiales = vers_filiales(lecture, await _aujourdhui(magasin))
        # Une seule écriture pour tout le lot : treize appels laisseraient le
        # tableau à moitié rempli si la base flanchait au septième. Un lot vide
        # n'écrit rien — il n'efface donc pas les relevés du jour.
        enregistres = await magasin.enregistrer_filiales(filiales)

        resultat = (
            _succes(entreprise, enregistres) + _resultat(lecture, filiales)
            if enregistres
            else _refus(lecture)
            + (
                '<p class="vide">Rien à enregistrer : les relevés déjà saisis '
                "n'ont pas été touchés. Colle le tableau des filiales du jeu "
                "dans la zone ci-dessus, titres compris.</p>"
            )
        )
        reponse = _reponse(rendre(serveurs, collage, serveur, resultat))

        # Le cookie n'est posé — ni prolongé — que sur un mot de passe tapé. Le
        # renouveler à chaque enregistrement ferait d'un mois glissant un accès
        # sans fin, alors que sa raison d'être est qu'un navigateur oublié finisse
        # par perdre la main.
        if par_mot_de_passe:
            _identifier(reponse, serveur, trace)
        return reponse

    app.router.add_get(CHEMIN, afficher)
    app.router.add_post(CHEMIN, convertir)
    app.router.add_post(CHEMIN_ENREGISTRER, enregistrer)

"""Tests de la provenance des données (fichier local et API du jeu)."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src import settings
from src.bot import EmpireBot, creer_source
from src.source import (
    URL_API_DEFAUT,
    ApiSource,
    CsvFileSource,
    SourceError,
    construire_url,
    decrire,
    diagnostiquer,
)

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-28 08:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
industriels,"Entrepôt",0,302620,0,0,283,17,611961,87354,62063
"""


# --- Construction de l'URL --------------------------------------------------

def test_url_par_defaut_contient_le_placeholder():
    assert "{api_key}" in URL_API_DEFAUT
    assert URL_API_DEFAUT.startswith("https://")


def test_cle_substituee_dans_le_placeholder():
    url = construire_url("https://monde8.example/x.csv?key={api_key}", "SECRET")
    assert url == "https://monde8.example/x.csv?key=SECRET"


def test_cle_ajoutee_si_url_sans_placeholder():
    """Une URL collée sans `{api_key}` doit quand même être authentifiée."""
    url = construire_url("https://monde8.example/x.csv", "SECRET")
    assert url == "https://monde8.example/x.csv?key=SECRET"


def test_cle_ajoutee_a_une_query_existante():
    url = construire_url("https://monde8.example/x.csv?monde=8", "SECRET")
    assert url == "https://monde8.example/x.csv?monde=8&key=SECRET"


def test_url_deja_authentifiee_nest_pas_doublee():
    """L'utilisateur a collé l'URL complète, clé incluse."""
    url = construire_url("https://monde8.example/x.csv?key=DEJA", "AUTRE")
    assert url == "https://monde8.example/x.csv?key=DEJA"


def test_url_sans_cle_reste_telle_quelle():
    url = construire_url("https://monde8.example/x.csv", "")
    assert url == "https://monde8.example/x.csv"


def test_placeholder_sans_cle_est_refuse():
    """Mieux vaut un message clair qu'une requête avec `key={api_key}`."""
    with pytest.raises(SourceError) as exc:
        construire_url("https://monde8.example/x.csv?key={api_key}", "")
    assert "EMPIRE_API_KEY" in str(exc.value)


def test_cle_encodee_dans_lurl():
    url = construire_url("https://monde8.example/x.csv", "a b&c=d")
    assert "a+b%26c%3Dd" in url or "a%20b%26c%3Dd" in url


# --- Fichier local ----------------------------------------------------------

async def test_fichier_local_lit_le_csv(tmp_path):
    chemin = tmp_path / "export.csv"
    chemin.write_text(CSV, encoding="utf-8")
    assert await CsvFileSource(chemin).fetch() == CSV


async def test_fichier_absent_message_clair(tmp_path):
    with pytest.raises(FileNotFoundError):
        await CsvFileSource(tmp_path / "absent.csv").fetch()


# --- API du jeu -------------------------------------------------------------

async def _serveur(gestionnaire, chemin="/api/buildings_batiments_entreprise.csv"):
    app = web.Application()
    app.router.add_get(chemin, gestionnaire)
    serveur = TestServer(app)
    await serveur.start_server()
    return serveur


async def test_api_recupere_le_csv():
    async def gestionnaire(_):
        return web.Response(text=CSV, content_type="text/csv")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        assert await ApiSource(url).fetch() == CSV
    finally:
        await serveur.close()


async def test_api_transmet_la_cle():
    recus = {}

    async def gestionnaire(requete):
        recus["key"] = requete.query.get("key")
        return web.Response(text=CSV, content_type="text/csv")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv")) + "?key={api_key}"
    try:
        await ApiSource(url, cle="MACLE").fetch()
    finally:
        await serveur.close()
    assert recus["key"] == "MACLE"


async def test_api_401_message_explicite():
    async def gestionnaire(_):
        return web.Response(status=401, text="unauthorized")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError) as exc:
            await ApiSource(url, cle="MAUVAISE").fetch()
    finally:
        await serveur.close()
    message = str(exc.value)
    assert "clé" in message.lower()
    assert "MAUVAISE" not in message  # la clé ne doit pas fuiter dans le log


async def test_api_500_message_explicite():
    async def gestionnaire(_):
        return web.Response(status=500, text="boom")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError) as exc:
            await ApiSource(url).fetch()
    finally:
        await serveur.close()
    assert "500" in str(exc.value)


async def test_api_refuse_une_reponse_html():
    """Une page de login renvoyée en 200 ne doit pas être parsée comme un CSV."""
    async def gestionnaire(_):
        return web.Response(text="<html><body>Connexion</body></html>",
                            content_type="text/html")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError) as exc:
            await ApiSource(url).fetch()
    finally:
        await serveur.close()
    assert "csv" in str(exc.value).lower()


async def test_api_refuse_une_reponse_vide():
    async def gestionnaire(_):
        return web.Response(text="", content_type="text/csv")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError):
            await ApiSource(url).fetch()
    finally:
        await serveur.close()


async def test_api_accepte_un_csv_sans_content_type_csv():
    """Certaines API servent le CSV en text/plain : le contenu tranche."""
    async def gestionnaire(_):
        return web.Response(text=CSV, content_type="text/plain")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        assert await ApiSource(url).fetch() == CSV
    finally:
        await serveur.close()


async def test_api_cle_absente_de_lerreur_reseau():
    """Un timeout ne doit pas recracher l'URL complète (clé incluse)."""
    source = ApiSource("http://127.0.0.1:1/x.csv?key={api_key}", cle="SECRET42")
    with pytest.raises(SourceError) as exc:
        await source.fetch()
    assert "SECRET42" not in str(exc.value)


# --- Choix de la source selon l'environnement -------------------------------

def test_source_par_defaut_est_le_fichier(monkeypatch):
    monkeypatch.setattr(settings, "EMPIRE_API_KEY", "")
    monkeypatch.setattr(settings, "EMPIRE_API_URL", "")
    monkeypatch.setattr(settings, "CSV_URL", "")
    assert isinstance(creer_source(), CsvFileSource)


def test_cle_api_suffit_a_basculer_sur_lapi(monkeypatch):
    monkeypatch.setattr(settings, "EMPIRE_API_KEY", "MACLE")
    monkeypatch.setattr(settings, "EMPIRE_API_URL", "")
    monkeypatch.setattr(settings, "CSV_URL", "")
    source = creer_source()
    assert isinstance(source, ApiSource)
    assert source.modele == URL_API_DEFAUT
    assert source.cle == "MACLE"


def test_url_explicite_remplace_lurl_par_defaut(monkeypatch):
    monkeypatch.setattr(settings, "EMPIRE_API_KEY", "MACLE")
    monkeypatch.setattr(settings, "EMPIRE_API_URL", "https://monde9.example/x.csv")
    monkeypatch.setattr(settings, "CSV_URL", "")
    assert creer_source().modele == "https://monde9.example/x.csv"


def test_ancien_csv_url_encore_reconnu(monkeypatch):
    """Un `.env` d'avant l'API ne doit pas cesser de fonctionner."""
    monkeypatch.setattr(settings, "EMPIRE_API_KEY", "")
    monkeypatch.setattr(settings, "EMPIRE_API_URL", "")
    monkeypatch.setattr(settings, "CSV_URL", "https://ancien.example/x.csv")
    source = creer_source()
    assert isinstance(source, ApiSource)
    assert source.modele == "https://ancien.example/x.csv"


def test_url_sans_cle_utilise_lapi_quand_meme(monkeypatch):
    """URL fournie clé incluse : pas besoin d'EMPIRE_API_KEY séparée."""
    monkeypatch.setattr(settings, "EMPIRE_API_KEY", "")
    monkeypatch.setattr(settings, "EMPIRE_API_URL", "https://monde8.example/x.csv?key=ABC")
    monkeypatch.setattr(settings, "CSV_URL", "")
    assert isinstance(creer_source(), ApiSource)


def test_url_masquee_ne_revele_pas_la_cle():
    source = ApiSource("https://monde8.example/x.csv?key=SECRET", cle="")
    assert "SECRET" not in source.url_masquee
    assert "***" in source.url_masquee


# --- L'API en panne ne doit pas brûler la publication du jour ---------------

class SourceEnPanne:
    async def fetch(self):
        raise SourceError("API injoignable (ClientConnectorError).")


async def test_api_en_panne_ne_marque_pas_le_jour_publie():
    """Sinon la panne de 09:00 supprimerait le post de toute la journée."""
    from src.db import Store

    store = Store(dsn="")
    await store.connect()
    await store.maj_config(salon_id="123")

    bot = object.__new__(EmpireBot)   # sans se connecter à Discord
    bot.store = store
    bot.source = SourceEnPanne()

    with pytest.raises(SourceError):
        await bot.publier_si_lheure(forcer=True)

    assert await store.derniere_publication() is None


# --- Erreurs JSON de l'API (format réel de monde8.empireimmo.com) -----------

async def test_message_derreur_json_de_lapi_est_repris():
    """L'API renvoie {"error":true,"code":401,"message":"Clé API invalide..."}."""
    async def gestionnaire(_):
        return web.json_response(
            {"error": True, "code": 401, "message": "Clé API invalide ou révoquée."},
            status=401,
        )

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError) as exc:
            await ApiSource(url, cle="MAUVAISE").fetch()
    finally:
        await serveur.close()
    assert "Clé API invalide ou révoquée" in str(exc.value)


async def test_message_json_sur_une_erreur_500():
    async def gestionnaire(_):
        return web.json_response(
            {"error": True, "code": 500, "message": "Maintenance en cours."}, status=500
        )

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError) as exc:
            await ApiSource(url).fetch()
    finally:
        await serveur.close()
    assert "Maintenance en cours" in str(exc.value)


async def test_json_illisible_retombe_sur_le_message_generique():
    async def gestionnaire(_):
        return web.Response(status=503, text="<html>oops</html>", content_type="text/html")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        with pytest.raises(SourceError) as exc:
            await ApiSource(url).fetch()
    finally:
        await serveur.close()
    assert "503" in str(exc.value)


# --- Description d'une source (pour /source) --------------------------------

def test_decrire_une_api_masque_la_cle():
    texte = decrire(ApiSource("https://monde8.example/x.csv?key={api_key}", cle="SECRET"))
    assert "SECRET" not in texte
    assert "***" in texte
    assert "API" in texte


def test_decrire_un_fichier_donne_le_chemin():
    texte = decrire(CsvFileSource("/tmp/export.csv"))
    assert "export.csv" in texte


# --- Diagnostic (commande /source tester) -----------------------------------

async def test_diagnostic_reussi_compte_batiments_et_promos():
    async def gestionnaire(_):
        return web.Response(text=CSV, content_type="text/csv")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        rapport = await diagnostiquer(ApiSource(url))
    finally:
        await serveur.close()

    assert rapport.ok
    assert not rapport.erreur
    assert rapport.batiments == 1
    assert rapport.promos == 1          # l'Entrepôt est à 17 %
    assert rapport.taille == len(CSV)
    assert rapport.mise_a_jour == "2026-07-28 08:00:07"
    assert rapport.monde == "Empire Immo - M8"


async def test_diagnostic_mesure_la_duree():
    async def gestionnaire(_):
        return web.Response(text=CSV, content_type="text/csv")

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    # Horloge injectée : 2 tops espacés de 0,25 s, sans ralentir le test.
    tops = iter([10.0, 10.25])
    try:
        rapport = await diagnostiquer(ApiSource(url), horloge=lambda: next(tops))
    finally:
        await serveur.close()
    assert rapport.duree_ms == 250


async def test_diagnostic_dune_cle_refusee_nest_pas_une_exception():
    """La commande doit afficher l'erreur, pas planter : d'où un rapport.ok=False."""
    async def gestionnaire(_):
        return web.json_response(
            {"error": True, "code": 401, "message": "Clé API invalide ou révoquée."},
            status=401,
        )

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        rapport = await diagnostiquer(ApiSource(url, cle="MAUVAISE"))
    finally:
        await serveur.close()

    assert not rapport.ok
    assert "Clé API invalide ou révoquée" in rapport.erreur
    assert "MAUVAISE" not in rapport.erreur
    assert rapport.batiments == 0


async def test_diagnostic_dun_fichier_absent_est_un_rapport():
    """`CsvFileSource` lève `FileNotFoundError`, pas `SourceError`."""
    rapport = await diagnostiquer(CsvFileSource("/introuvable/export.csv"))
    assert not rapport.ok
    assert "export.csv" in rapport.erreur


async def test_diagnostic_dun_csv_valide_mais_vide():
    """0 bâtiment : l'API répond, mais les données ne servent à rien."""
    async def gestionnaire(_):
        return web.Response(
            text="# nom: Empire Immo - M8\ntype,nom,niveau,valeur\n",
            content_type="text/csv",
        )

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        rapport = await diagnostiquer(ApiSource(url))
    finally:
        await serveur.close()

    assert not rapport.ok
    assert rapport.batiments == 0
    assert "aucun bâtiment" in rapport.erreur.lower()


async def test_diagnostic_signale_labsence_de_promotion_sans_echouer():
    """Aucune promo aujourd'hui est normal : la source, elle, fonctionne."""
    async def gestionnaire(_):
        return web.Response(
            text=CSV.replace(",17,", ",0,"), content_type="text/csv"
        )

    serveur = await _serveur(gestionnaire)
    url = str(serveur.make_url("/api/buildings_batiments_entreprise.csv"))
    try:
        rapport = await diagnostiquer(ApiSource(url))
    finally:
        await serveur.close()

    assert rapport.ok
    assert rapport.batiments == 1
    assert rapport.promos == 0


async def test_diagnostic_ne_revele_jamais_la_cle():
    source = ApiSource("http://127.0.0.1:1/x.csv?key={api_key}", cle="SECRET42")
    rapport = await diagnostiquer(source)
    assert not rapport.ok
    assert "SECRET42" not in rapport.erreur
    assert "SECRET42" not in rapport.source

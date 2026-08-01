# Support multi-serveurs — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publier les promotions dans des salons répartis sur deux serveurs Discord de la même entreprise, et afficher sur le site quel salon vit sur quel serveur.

**Architecture:** La publication ne change pas — `resoudre_salon` traverse déjà tous les serveurs où le bot est présent. Trois choses changent : `GUILD_IDS` synchronise les commandes sur plusieurs serveurs, `config["roles"]` porte un rôle par serveur (résolu depuis `salon.guild.id` à la publication), et `config["serveurs"]` / `config["salons_connus"]` stockent les noms au moment du réglage pour que le site les affiche sans dépendre de la connexion Discord.

**Tech Stack:** Python 3.11, discord.py 2.4.0, asyncpg, pytest 8.3.4 + pytest-asyncio (`asyncio_mode = auto`) ; Next.js 16.2.12 / React 19.2.8 / TS 5.7.3, `node --test` sur `dist/`.

**Spec:** `docs/superpowers/specs/2026-08-01-multi-serveurs-design.md`

## Global Constraints

- **`fourchette["salons"]` reste une liste d'ids nus** (`list[str]`). Y glisser des objets casserait la boucle de publication, `salonsUniques`, `nombreEnvois` et une partie des 45 mutations.
- **`config_par_defaut()` ne déclare ni `roles`, ni `serveurs`, ni `salons_connus`.** Les lecteurs renvoient `{}` quand la clé est absente. Matérialiser des défauts en base est ce que `Store._enregistree()` évite.
- **`config_par_defaut()` déclare en revanche déjà `role_id`** (`src/settings.py:90`, `ROLE_DEFAUT or None`). Cette ligne **reste** : c'est la variable d'env `ROLE_ID`, un réglage de démarrage documenté. Conséquence à connaître : `Store.config()` fusionne ce défaut, donc le repli de `role_du_serveur` s'applique aussi quand `ROLE_ID` est défini dans l'environnement. Les tests doivent donc neutraliser `ROLE_DEFAUT` pour être déterministes ailleurs que sur cette machine (où `.env` le laisse vide).
- **`_CHAMPS_PLATS` (`src/db.py:37`) reste inchangé** : les nouvelles clés ne sont pas des champs plats à migrer.
- **Toute écriture de config part de `Store._enregistree()`**, jamais de `Store.config()`.
- **`maj_config` ignore les valeurs `None` et vides** : pour effacer une clé ou écrire un dict vide, écrire la config entière via `set("config", …)`.
- **Aucun montant ne traverse le JSON en nombre.** Non concerné ici, mais ne pas régresser.
- **La clé `EMPIRE_API_KEY` ne doit jamais apparaître** dans un log, un message d'erreur, une réponse HTTP ou une réponse Discord.
- **Tests** : TDD strict — test écrit, vu échouer, puis code minimal. Puis mutation testing (`python tests/mutations.py`).
- **Français** dans les docstrings, commentaires, messages utilisateur et noms de fonctions métier, comme tout le code existant.
- **Windows/Git Bash** : `grep -oP` échoue ; les emoji dans un `print()` lèvent `UnicodeEncodeError` (console cp1252) ; `tests/mutations.py` est en CRLF — le lire/écrire avec `io.open(..., encoding="utf-8")`, **jamais** `newline=""`.

---

## Structure des fichiers

| Fichier | Responsabilité | Action |
|---|---|---|
| `src/settings.py` | `GUILD_IDS` : liste, `GUILD_ID` en repli | Modifier |
| `src/bot.py` | `setup_hook` boucle ; publication résout le rôle par serveur ; `/config mention` par serveur ; mémorisation des noms | Modifier |
| `src/db.py` | `roles()`, `definir_role()`, `effacer_role()`, `salons_connus()`, `serveurs()`, `memoriser_salon()`, `oublier_salons_orphelins()` | Modifier |
| `src/serialisation.py` | `roles`, `serveurs`, `salons_connus` dans `config_en_json` | Modifier |
| `tests/test_roles_serveurs.py` | Rôle par serveur : stockage, repli `role_id`, publication | Créer |
| `tests/test_salons_connus.py` | Noms mémorisés, rafraîchis, nettoyés | Créer |
| `tests/test_multi_serveurs.py` | `GUILD_IDS`, publication vers deux serveurs | Créer |
| `tests/mutations.py` | 6 mutations nouvelles (45 → 51) | Modifier |
| `D:\eiweb\lib\serveurs.ts` | `grouperParServeur` — module pur (la spec le plaçait dans `fourchettes.ts` ; voir tâche 7) | Créer |
| `D:\eiweb\tests\serveurs.test.mjs` | Tests du module pur | Créer |
| `D:\eiweb\tsconfig.test.json` | Ajouter `lib/serveurs.ts` à `include` | Modifier |
| `D:\eiweb\lib\bot.ts` | Contrat `roles` / `serveurs` / `salons_connus` | Modifier |
| `D:\eiweb\app\page.tsx`, `app/reglages/page.tsx` | Salons groupés par serveur, mention par serveur | Modifier |
| `README.md` (bot), `D:\eiweb\README.md` | `GUILD_IDS`, « inviter le bot = en donner les clés » | Modifier |

**Ordre imposé par les dépendances :** Tâche 1 (stockage des rôles) avant la tâche 3 (publication) ; tâche 4 (noms) avant la tâche 6 (sérialisation) ; tâche 6 avant les tâches 7-8 (site).

---

## Task 1: Stockage d'un rôle par serveur

**Files:**
- Modify: `src/db.py` (après `salon_logs`, vers la ligne 349)
- Test: `tests/test_roles_serveurs.py` (créer)

**Interfaces:**
- Consumes: `Store.config()`, `Store._enregistree()`, `Store.set()` — existants.
- Produces:
  - `async Store.roles() -> dict[str, str]` — `{id_serveur: id_role}`, `{}` si rien.
  - `async Store.role_du_serveur(serveur_id: str | int | None) -> str | None` — le rôle à mentionner pour ce serveur, en appliquant le repli `role_id`.
  - `async Store.definir_role(serveur_id: str | int, role_id: str | int) -> None`
  - `async Store.effacer_role(serveur_id: str | int) -> bool` — False si ce serveur n'en avait pas.

- [ ] **Step 1: Write the failing tests**

Créer `tests/test_roles_serveurs.py` :

```python
"""Un rôle appartient à un serveur, pas au bot.

`config["role_id"]` était global : le bot enverrait `<@&123>` dans un second
serveur où ce rôle n'existe pas, et Discord y afficherait `@deleted-role` — une
faute visible seulement en lisant le post.
"""

import pytest

from src import settings
from src.db import Store


@pytest.fixture(autouse=True)
def sans_role_denv(monkeypatch):
    """Neutralise `ROLE_ID`.

    `config_par_defaut()` déclare `role_id` depuis la variable d'env
    (`src/settings.py:90`). Elle est vide dans le `.env` de cette machine, mais un
    poste où elle serait réglée ferait échouer les tests du repli — pour une
    raison invisible dans le code testé.
    """
    monkeypatch.setattr(settings, "ROLE_DEFAUT", "")


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


async def test_aucun_role_par_defaut():
    """Un bot neuf ne mentionne personne, et `roles` n'est pas materialise."""
    store = await _store()
    assert await store.roles() == {}
    assert await store.role_du_serveur("999") is None


async def test_role_enregistre_pour_son_serveur():
    store = await _store()
    await store.definir_role("999", "42")

    assert await store.roles() == {"999": "42"}
    assert await store.role_du_serveur("999") == "42"


async def test_un_serveur_sans_role_ne_mentionne_personne():
    """Le cas central : régler A ne doit pas pinguer dans B."""
    store = await _store()
    await store.definir_role("999", "42")

    assert await store.role_du_serveur("888") is None


async def test_role_id_plat_sapplique_partout():
    """Comportement actuel préservé : avec un seul serveur, une seule réponse
    possible. Convertir demanderait de résoudre un salon pour connaître son
    serveur — donc un accès à Discord, que `Store` n'a pas."""
    store = await _store()
    await store.set("config", {"role_id": "7"})

    assert await store.role_du_serveur("999") == "7"
    assert await store.role_du_serveur("888") == "7"


async def test_roles_regles_ignorent_le_role_id_plat():
    """Le cas mixte, le plus piégeux : `roles` réglé pour A alors que `role_id`
    traîne encore en base. B ne doit **rien** en hériter, sinon un rôle qu'on
    croit remplacé continuerait d'être mentionné ailleurs."""
    store = await _store()
    await store.set("config", {"role_id": "7", "roles": {"999": "42"}})

    assert await store.role_du_serveur("999") == "42"
    assert await store.role_du_serveur("888") is None


async def test_definir_role_efface_le_role_id_plat():
    """Sinon le repli continuerait de s'appliquer aux serveurs non réglés."""
    store = await _store()
    await store.set("config", {"role_id": "7"})
    await store.definir_role("999", "42")

    enregistree = await store.get("config", {})
    assert "role_id" not in enregistree
    assert await store.role_du_serveur("888") is None


async def test_effacer_role_ne_touche_que_son_serveur():
    store = await _store()
    await store.definir_role("999", "42")
    await store.definir_role("888", "43")

    assert await store.effacer_role("999") is True
    assert await store.roles() == {"888": "43"}


async def test_effacer_role_absent_renvoie_faux():
    store = await _store()
    assert await store.effacer_role("999") is False


async def test_dernier_role_efface_reste_efface():
    """`maj_config` ignore les valeurs vides : un dict vidé ne serait jamais
    enregistré, et le rôle reviendrait au redémarrage."""
    store = await _store()
    await store.definir_role("999", "42")
    await store.effacer_role("999")

    assert await store.roles() == {}
    assert (await store.get("config", {})).get("roles") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roles_serveurs.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'roles'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/db.py`, après `desactiver_logs` (vers la ligne 356) :

```python
    # --- Mention : un rôle par serveur -------------------------------------

    async def roles(self) -> dict[str, str]:
        """Rôle à mentionner par serveur, `{}` si aucun n'est réglé.

        Pas de défaut d'usine pour cette clé : un dict vide serait
        indistinguable de « jamais réglé », et le matérialiser en base est ce
        que `_enregistree` évite partout ailleurs.
        """
        table = (await self.config()).get("roles") or {}
        return {str(serveur): str(role) for serveur, role in table.items() if role}

    async def role_du_serveur(self, serveur_id: str | int | None) -> str | None:
        """Rôle à mentionner dans ce serveur, ou None.

        `role_id` (config d'avant le multi-serveurs) sert de **repli**, il n'est
        pas converti : savoir à quel serveur appartient un rôle demanderait de
        résoudre un salon, donc un accès à Discord que `Store` n'a pas.

        Le repli ne joue que si `roles` est vide. Sinon un rôle qu'on croit
        remplacé continuerait d'être mentionné dans les serveurs non réglés.
        """
        table = await self.roles()
        if table:
            return table.get(str(serveur_id)) if serveur_id else None

        ancien = (await self.config()).get("role_id")
        return str(ancien) if ancien else None

    async def _ecrire_roles(self, table: dict[str, str]) -> None:
        """Écrit la table et retire `role_id`, devenu ambigu.

        Écriture directe et non `maj_config` : celui-ci ignore les valeurs
        vides, donc une table vidée ne serait jamais enregistrée et le rôle
        reviendrait au redémarrage.
        """
        config = await self._enregistree()
        config["roles"] = table
        config.pop("role_id", None)
        await self.set("config", config)

    async def definir_role(self, serveur_id: str | int, role_id: str | int) -> None:
        await self._ecrire_roles({**await self.roles(), str(serveur_id): str(role_id)})

    async def effacer_role(self, serveur_id: str | int) -> bool:
        """Retire le rôle d'un serveur. False s'il n'en avait pas."""
        table = await self.roles()
        if str(serveur_id) not in table:
            return False
        await self._ecrire_roles(
            {s: r for s, r in table.items() if s != str(serveur_id)}
        )
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_roles_serveurs.py -q`
Expected: PASS (9 tests)

Puis la suite entière, pour vérifier qu'aucun test existant ne régresse :
Run: `python -m pytest tests/ -q`
Expected: 418 passed + 9 nouveaux = 427

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_roles_serveurs.py
git commit -m "Un role de mention par serveur, en base"
```

---

## Task 2: `GUILD_IDS` — les commandes sur plusieurs serveurs

**Files:**
- Modify: `src/settings.py:17-19`
- Modify: `src/bot.py:126-134` (`setup_hook`)
- Test: `tests/test_multi_serveurs.py` (créer)

**Interfaces:**
- Produces: `settings.GUILD_IDS: list[str]` — remplace `settings.GUILD_ID`, qui reste défini et lu en repli.

- [ ] **Step 1: Write the failing tests**

Créer `tests/test_multi_serveurs.py` :

```python
"""Les commandes doivent apparaître sur **chacun** des serveurs déclarés.

Synchroniser sur un seul serveur ne lève aucune erreur : les commandes sont
simplement absentes ailleurs, ce qu'on ne remarque qu'en les cherchant dans
Discord.
"""

import importlib

import pytest


def _settings_avec(monkeypatch, **variables):
    """Recharge `src.settings` avec ces variables d'environnement.

    Les constantes sont lues à l'import : sans rechargement, `monkeypatch`
    n'aurait aucun effet.
    """
    for cle in ("GUILD_ID", "GUILD_IDS"):
        monkeypatch.delenv(cle, raising=False)
    for cle, valeur in variables.items():
        monkeypatch.setenv(cle, valeur)

    import src.settings

    return importlib.reload(src.settings)


def test_guild_ids_lit_une_liste(monkeypatch):
    settings = _settings_avec(monkeypatch, GUILD_IDS="111,222")
    assert settings.GUILD_IDS == ["111", "222"]


def test_guild_ids_tolere_les_espaces(monkeypatch):
    """La valeur est recopiée à la main dans Render : « 111, 222 » est probable."""
    settings = _settings_avec(monkeypatch, GUILD_IDS=" 111 , 222 ")
    assert settings.GUILD_IDS == ["111", "222"]


def test_guild_id_seul_sert_de_repli(monkeypatch):
    """Le `.env` local et la variable Render existants ne doivent pas casser."""
    settings = _settings_avec(monkeypatch, GUILD_ID="111")
    assert settings.GUILD_IDS == ["111"]


def test_aucune_variable_donne_une_liste_vide(monkeypatch):
    """Liste vide = synchronisation globale, le comportement d'avant."""
    settings = _settings_avec(monkeypatch)
    assert settings.GUILD_IDS == []


def test_virgule_seule_ne_cree_pas_de_serveur_vide(monkeypatch):
    """`discord.Object(id=int(""))` lèverait au démarrage du bot."""
    settings = _settings_avec(monkeypatch, GUILD_IDS=",")
    assert settings.GUILD_IDS == []
```

Puis, dans le même fichier, le test de `setup_hook` :

```python
class ArbreFactice:
    """Compte les synchronisations, par serveur."""

    def __init__(self):
        self.copies: list[int | None] = []
        self.syncs: list[int | None] = []

    def copy_global_to(self, guild):
        self.copies.append(guild.id)

    async def sync(self, guild=None):
        self.syncs.append(guild.id if guild is not None else None)


async def _bot_avec_arbre(monkeypatch, guild_ids: list[str]):
    from src.bot import EmpireBot
    from src import bot as module_bot

    monkeypatch.setattr(module_bot.settings, "GUILD_IDS", guild_ids)

    bot = object.__new__(EmpireBot)
    bot.tree = ArbreFactice()
    return bot


async def test_synchronise_sur_chaque_serveur(monkeypatch):
    """La propriété qui fait l'intérêt de la liste : deux serveurs, deux syncs."""
    bot = await _bot_avec_arbre(monkeypatch, ["111", "222"])

    await bot.setup_hook()

    assert bot.tree.syncs == [111, 222]
    assert bot.tree.copies == [111, 222]


async def test_liste_vide_synchronise_globalement(monkeypatch):
    bot = await _bot_avec_arbre(monkeypatch, [])

    await bot.setup_hook()

    assert bot.tree.syncs == [None]
    assert bot.tree.copies == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_multi_serveurs.py -q`
Expected: FAIL — `AttributeError: module 'src.settings' has no attribute 'GUILD_IDS'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/settings.py`, remplacer les lignes 17-19 :

```python
#: Serveurs sur lesquels synchroniser les commandes, séparés par des virgules.
#:
#: Une liste explicite plutôt que la synchronisation globale, pour trois
#: raisons : elle est immédiate (la propagation globale met jusqu'à une heure),
#: les commandes n'existent que sur les serveurs déclarés — un serveur où le bot
#: serait invité par ailleurs n'a aucune prise sur la configuration —, et vide,
#: elle retombe sur la synchro globale, donc un déploiement qui ne déclare rien
#: continue de fonctionner.
#:
#: `GUILD_ID` (singulier) reste lu en repli : il est déjà défini dans le `.env`
#: local et sur Render.
GUILD_ID = os.getenv("GUILD_ID", "")
GUILD_IDS = [
    serveur.strip()
    for serveur in os.getenv("GUILD_IDS", GUILD_ID).split(",")
    if serveur.strip()
]
```

Dans `src/bot.py`, remplacer `setup_hook` (lignes 126-134) :

```python
    async def setup_hook(self) -> None:
        if settings.GUILD_IDS:
            for serveur_id in settings.GUILD_IDS:
                guild = discord.Object(id=int(serveur_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            log.info(
                "Commandes synchronisées sur %d serveur(s) : %s",
                len(settings.GUILD_IDS),
                ", ".join(settings.GUILD_IDS),
            )
        else:
            await self.tree.sync()
            log.info("Commandes synchronisées globalement.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_multi_serveurs.py -q`
Expected: PASS (7 tests)

Run: `python -m pytest tests/ -q`
Expected: 434 passed

- [ ] **Step 5: Commit**

```bash
git add src/settings.py src/bot.py tests/test_multi_serveurs.py
git commit -m "GUILD_IDS : synchroniser les commandes sur plusieurs serveurs"
```

---

## Task 3: Publication — chaque salon mentionne le rôle de son serveur

**Files:**
- Modify: `src/bot.py:243-260` (boucle d'envoi dans `publier_si_lheure`)
- Test: `tests/test_multi_serveurs.py` (compléter)

**Interfaces:**
- Consumes: `Store.role_du_serveur(serveur_id)` (tâche 1) ; `publish.envoyer(destination, embeds, contenu, role_id, ephemere)` — signature **inchangée**, l'appelant résout le rôle.
- Produces: rien de nouveau ; comportement modifié.

- [ ] **Step 1: Write the failing tests**

Ajouter à `tests/test_multi_serveurs.py` :

```python
from decimal import Decimal

from src.bot import EmpireBot
from src.db import Store

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-29 12:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0
zones,"Zone portuaire",0,124467906332,0,0,0,17,0,0,0
"""


class ServeurFactice:
    def __init__(self, serveur_id: int):
        self.id = serveur_id
        self.name = f"Serveur {serveur_id}"


class SalonFactice:
    """Salon qui connaît son serveur, comme un vrai `TextChannel`."""

    def __init__(self, salon_id: int, serveur_id: int, nom: str = "promos"):
        self.id = salon_id
        self.name = nom
        self.guild = ServeurFactice(serveur_id)
        self.mention = f"<#{salon_id}>"
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        self.envois.append({"contenu": contenu, **options})

    @property
    def mentions(self) -> list[str]:
        """Contenu des messages reçus, pour voir *qui* a été mentionné."""
        return [envoi.get("content") or "" for envoi in self.envois]


class SourceFactice:
    async def fetch(self) -> str:
        return CSV


class JournalFactice:
    async def publication(self, promos, reussis, echecs):
        pass

    async def erreur(self, message):
        pass


async def _bot(salons: dict[int, SalonFactice]) -> EmpireBot:
    store = Store(dsn="")
    await store.connect()

    bot = object.__new__(EmpireBot)
    bot.store = store
    bot.source = SourceFactice()
    bot.journal = JournalFactice()
    bot.get_channel = salons.get
    return bot


async def test_publie_dans_deux_serveurs():
    """Le besoin de base : une fourchette, deux salons, deux serveurs."""
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert salons[1].envois and salons[2].envois


async def test_chaque_salon_mentionne_le_role_de_son_serveur():
    """La propriété qui fait l'intérêt du changement.

    Mentionner le rôle de A dans un salon de B afficherait `@deleted-role`
    dans le post — sans erreur, sans log, visible seulement en le lisant.
    """
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.definir_role("111", "42")
    await bot.store.definir_role("222", "43")
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert "<@&42>" in salons[1].mentions[0]
    assert "<@&43>" not in salons[1].mentions[0]
    assert "<@&43>" in salons[2].mentions[0]
    assert "<@&42>" not in salons[2].mentions[0]


async def test_serveur_sans_role_ne_mentionne_personne():
    """Pas de mention vide non plus : le post part sans ping."""
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.definir_role("111", "42")
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert "<@&" in salons[1].mentions[0]
    assert "<@&" not in salons[2].mentions[0]


async def test_role_id_plat_mentionne_partout():
    """Compatibilité : une config d'avant garde son comportement."""
    salons = {1: SalonFactice(1, 111), 2: SalonFactice(2, 222)}
    bot = await _bot(salons)
    await bot.store.set("config", {"role_id": "7"})
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    assert "<@&7>" in salons[1].mentions[0]
    assert "<@&7>" in salons[2].mentions[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_multi_serveurs.py -k mentionne -q`
Expected: FAIL — `test_chaque_salon_mentionne_le_role_de_son_serveur` échoue : les deux salons reçoivent le même rôle (celui de `config.get("role_id")`, donc `None` → aucune mention). L'assertion `"<@&42>" in salons[1].mentions[0]` échoue.

- [ ] **Step 3: Write minimal implementation**

Dans `src/bot.py`, remplacer la boucle d'envoi (lignes 243-249) :

```python
            for salon_id in fourchette["salons"]:
                try:
                    salon = await self.resoudre_salon(salon_id)
                    if repli:
                        await salon.send(repli)
                    else:
                        # Le rôle du serveur **du salon**, et non un rôle global :
                        # un rôle n'existe que dans son serveur, et `<@&123>`
                        # envoyé ailleurs s'affiche en `@deleted-role`.
                        serveur = getattr(salon, "guild", None)
                        role_id = await self.store.role_du_serveur(
                            getattr(serveur, "id", None)
                        )
                        await envoyer(salon, embeds, contenu, role_id)
```

Retirer `config.get("role_id")` de l'appel. `config` reste utilisé plus haut (`fuseau`, `heure`), donc la variable ne devient pas inutile.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_multi_serveurs.py -q`
Expected: PASS (11 tests)

Run: `python -m pytest tests/ -q`
Expected: 438 passed

- [ ] **Step 5: Commit**

```bash
git add src/bot.py tests/test_multi_serveurs.py
git commit -m "Chaque salon mentionne le role de son propre serveur"
```

---

## Task 4: Mémoriser les noms de salons et de serveurs

**Files:**
- Modify: `src/db.py` (après les rôles)
- Test: `tests/test_salons_connus.py` (créer)

**Interfaces:**
- Produces:
  - `async Store.salons_connus() -> dict[str, dict]` — `{id_salon: {"nom": str, "serveur": str}}`
  - `async Store.serveurs() -> dict[str, str]` — `{id_serveur: nom}`
  - `async Store.memoriser_salon(salon_id, nom, serveur_id, serveur_nom) -> None`
  - `async Store.oublier_salons_orphelins() -> int` — nombre d'entrées effacées.

- [ ] **Step 1: Write the failing tests**

Créer `tests/test_salons_connus.py` :

```python
"""Noms de salons et de serveurs, mémorisés pour le site.

Le site n'a pas accès à Discord : il ne connaît ni les noms de salons, ni ceux
des serveurs. Avec un seul serveur, afficher `123456` était austère ; avec deux,
c'est ambigu — rien ne dit d'où vient le salon.

Le bot connaît les deux au moment du réglage. Il les écrit, si bien que
`/api/config` ne dépend pas de l'état de la connexion Discord.
"""

from decimal import Decimal

from src.db import Store


async def _store() -> Store:
    store = Store(dsn="")
    await store.connect()
    return store


async def test_rien_de_connu_par_defaut():
    store = await _store()
    assert await store.salons_connus() == {}
    assert await store.serveurs() == {}


async def test_memorise_le_salon_et_son_serveur():
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")

    assert await store.salons_connus() == {
        "1": {"nom": "promos", "serveur": "111"}
    }
    assert await store.serveurs() == {"111": "Empire Immo"}


async def test_nom_rafraichi_quand_le_salon_est_renomme():
    """Sinon le site afficherait indéfiniment l'ancien nom."""
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("1", "bonnes-affaires", "111", "Empire Immo SA")

    assert (await store.salons_connus())["1"]["nom"] == "bonnes-affaires"
    assert (await store.serveurs())["111"] == "Empire Immo SA"


async def test_deux_salons_du_meme_serveur_partagent_son_nom():
    """Le nom du serveur est stocké une fois : deux copies divergeraient."""
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("2", "annonces", "111", "Empire Immo")

    assert len(await store.serveurs()) == 1
    assert len(await store.salons_connus()) == 2


async def test_oublie_les_salons_plus_attaches_a_aucune_fourchette():
    """Sinon la table grossit indéfiniment avec des salons dont plus personne
    ne parle."""
    store = await _store()
    await store.ajouter_fourchette("a", Decimal("0"), Decimal("1e15"))
    await store.ajouter_salon_fourchette("a", "1")
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("2", "vieux", "111", "Empire Immo")

    efface = await store.oublier_salons_orphelins()

    assert efface == 1
    assert list(await store.salons_connus()) == ["1"]


async def test_oublie_aussi_le_serveur_devenu_inutile():
    """Un serveur dont plus aucun salon ne dépend n'a plus à être nommé."""
    store = await _store()
    await store.ajouter_fourchette("a", Decimal("0"), Decimal("1e15"))
    await store.ajouter_salon_fourchette("a", "1")
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")
    await store.memoriser_salon("9", "autre", "222", "Second serveur")

    await store.oublier_salons_orphelins()

    assert await store.serveurs() == {"111": "Empire Immo"}


async def test_oublier_ne_touche_pas_un_salon_servant_deux_fourchettes():
    """Retiré d'une seule fourchette, il reste servi par l'autre."""
    store = await _store()
    for nom in ("a", "b"):
        await store.ajouter_fourchette(nom, Decimal("0"), Decimal("1e15"))
        await store.ajouter_salon_fourchette(nom, "1")
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")

    await store.retirer_salon_fourchette("a", "1")
    await store.oublier_salons_orphelins()

    assert list(await store.salons_connus()) == ["1"]


async def test_oublier_sans_rien_a_faire_renvoie_zero():
    """Et n'écrit pas en base pour rien."""
    store = await _store()
    assert await store.oublier_salons_orphelins() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_salons_connus.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'salons_connus'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/db.py`, après `effacer_role` :

```python
    # --- Noms de salons et de serveurs, pour le site -----------------------

    async def salons_connus(self) -> dict[str, dict]:
        """`{id_salon: {"nom": …, "serveur": …}}`, pour l'affichage du site.

        Deux tables plates (celle-ci et `serveurs`) plutôt qu'un objet par salon
        dans chaque fourchette : un salon servant deux fourchettes a son nom
        stocké **une seule fois**, et `fourchette["salons"]` reste une liste
        d'ids — ce dont dépendent la boucle de publication et le site.
        """
        table = (await self.config()).get("salons_connus") or {}
        return {
            str(salon): {
                "nom": str(details.get("nom", "")),
                "serveur": str(details.get("serveur", "")),
            }
            for salon, details in table.items()
            if isinstance(details, dict)
        }

    async def serveurs(self) -> dict[str, str]:
        """`{id_serveur: nom}` des serveurs dont un salon est connu."""
        table = (await self.config()).get("serveurs") or {}
        return {str(serveur): str(nom) for serveur, nom in table.items() if nom}

    async def memoriser_salon(
        self,
        salon_id: str | int,
        nom: str,
        serveur_id: str | int,
        serveur_nom: str,
    ) -> None:
        """Retient le nom d'un salon et de son serveur.

        Appelé au réglage **et** à chaque résolution : un salon renommé garde
        sinon son ancien nom indéfiniment. Les noms se corrigent donc d'eux-mêmes
        au premier post.
        """
        config = await self._enregistree()
        salons = dict(config.get("salons_connus") or {})
        salons[str(salon_id)] = {"nom": str(nom), "serveur": str(serveur_id)}
        config["salons_connus"] = salons

        noms = dict(config.get("serveurs") or {})
        noms[str(serveur_id)] = str(serveur_nom)
        config["serveurs"] = noms

        await self.set("config", config)

    async def oublier_salons_orphelins(self) -> int:
        """Efface les salons qu'aucune fourchette ne sert. Renvoie le compte.

        Sans ça la table grossit indéfiniment avec des salons dont plus personne
        ne parle. Un serveur dont plus aucun salon ne dépend disparaît aussi.
        """
        servis = {
            str(salon)
            for fourchette in await self.fourchettes()
            for salon in fourchette["salons"]
        }
        connus = await self.salons_connus()
        gardes = {
            salon: details for salon, details in connus.items() if salon in servis
        }
        if len(gardes) == len(connus):
            return 0

        config = await self._enregistree()
        config["salons_connus"] = gardes
        utiles = {details["serveur"] for details in gardes.values()}
        config["serveurs"] = {
            serveur: nom
            for serveur, nom in (await self.serveurs()).items()
            if serveur in utiles
        }
        await self.set("config", config)
        return len(connus) - len(gardes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_salons_connus.py -q`
Expected: PASS (8 tests)

Run: `python -m pytest tests/ -q`
Expected: 446 passed

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_salons_connus.py
git commit -m "Memoriser les noms de salons et de serveurs"
```

---

## Task 5: Brancher la mémorisation sur les commandes et la publication

**Files:**
- Modify: `src/bot.py` — `resoudre_salon` (ligne 171), `fourchette_salon_ajouter` (ligne 672), `fourchette_salon_retirer` (ligne 704), `config_mention` (ligne 824)
- Test: `tests/test_salons_connus.py` (compléter), `tests/test_multi_serveurs.py` (compléter)

**Interfaces:**
- Consumes: `Store.memoriser_salon`, `Store.oublier_salons_orphelins` (tâche 4) ; `Store.definir_role`, `Store.effacer_role` (tâche 1).
- Produces: rien de nouveau.

- [ ] **Step 1: Write the failing tests**

Ajouter à `tests/test_salons_connus.py` :

```python
class ServeurFactice:
    def __init__(self, serveur_id: int, nom: str):
        self.id = serveur_id
        self.name = nom


class SalonFactice:
    def __init__(self, salon_id: int, nom: str, serveur: ServeurFactice):
        self.id = salon_id
        self.name = nom
        self.guild = serveur
        self.mention = f"<#{salon_id}>"
        self.envois: list[dict] = []

    async def send(self, contenu=None, **options):
        self.envois.append({"contenu": contenu, **options})


async def test_resoudre_salon_memorise_son_nom():
    """Le rafraîchissement : chaque résolution met le nom à jour.

    C'est ce qui corrige un salon renommé au premier post suivant, sans
    intervention.
    """
    from src.bot import EmpireBot

    salon = SalonFactice(1, "bonnes-affaires", ServeurFactice(111, "Empire Immo"))
    store = await _store()
    await store.memoriser_salon("1", "promos", "111", "Empire Immo")

    bot = object.__new__(EmpireBot)
    bot.store = store
    bot.get_channel = {1: salon}.get

    await bot.resoudre_salon("1")

    assert (await store.salons_connus())["1"]["nom"] == "bonnes-affaires"
```

Ajouter à `tests/test_multi_serveurs.py` :

```python
async def test_publication_memorise_les_noms_des_deux_serveurs():
    """Après un post, le site sait nommer les salons des deux serveurs."""
    salons = {1: SalonFactice(1, 111, "promos"), 2: SalonFactice(2, 222, "annonces")}
    bot = await _bot(salons)
    await bot.store.ajouter_fourchette("a", Decimal("0"), Decimal("6e15"))
    await bot.store.ajouter_salon_fourchette("a", "1")
    await bot.store.ajouter_salon_fourchette("a", "2")

    await bot.publier_si_lheure(forcer=True)

    connus = await bot.store.salons_connus()
    assert connus["1"] == {"nom": "promos", "serveur": "111"}
    assert connus["2"] == {"nom": "annonces", "serveur": "222"}
    assert len(await bot.store.serveurs()) == 2
```

**Note pour l'implémenteur** : `SalonFactice` de `test_multi_serveurs.py` prend `(salon_id, serveur_id, nom="promos")` et construit son `ServeurFactice` avec `name = f"Serveur {serveur_id}"`. Celui de `test_salons_connus.py` prend `(salon_id, nom, serveur)`. Deux classes distinctes dans deux fichiers distincts, c'est voulu : chacune sert son propos, et les factoriser créerait une dépendance entre fichiers de test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_salons_connus.py::test_resoudre_salon_memorise_son_nom tests/test_multi_serveurs.py::test_publication_memorise_les_noms_des_deux_serveurs -q`
Expected: FAIL — le nom reste `promos` (aucune mémorisation dans `resoudre_salon`) ; `KeyError: '1'` pour le second.

- [ ] **Step 3: Write minimal implementation**

Dans `src/bot.py`, remplacer `resoudre_salon` (lignes 171-176) :

```python
    async def resoudre_salon(self, salon_id: str):
        """Salon Discord depuis son id, via le cache puis l'API.

        Traverse tous les serveurs où le bot est présent : c'est ce qui rend le
        multi-serveurs possible sans rien changer à la publication.

        Mémorise au passage le nom du salon et de son serveur, pour le site. Ici
        plutôt qu'au seul réglage : un salon renommé garderait sinon son ancien
        nom indéfiniment, alors qu'il se corrige de lui-même au premier post.
        """
        salon = self.get_channel(int(salon_id))
        if salon is None:
            salon = await self.fetch_channel(int(salon_id))

        serveur = getattr(salon, "guild", None)
        if serveur is not None and getattr(salon, "name", None):
            await self.store.memoriser_salon(
                salon_id, salon.name, serveur.id, getattr(serveur, "name", "")
            )
        return salon
```

Dans `fourchette_salon_ajouter`, après l'appel réussi à `ajouter_salon_fourchette` (après la ligne 694) :

```python
        # Mémorisé pour le site, qui n'a pas accès à Discord et ne pourrait
        # afficher qu'un id nu.
        await bot.store.memoriser_salon(
            str(salon.id), salon.name, str(interaction.guild.id), interaction.guild.name
        )
```

Dans `fourchette_salon_retirer`, après l'appel réussi à `retirer_salon_fourchette` (après la ligne 716) :

```python
        # Le salon n'est peut-être plus servi par aucune fourchette : son nom
        # n'a alors plus à occuper la config.
        await bot.store.oublier_salons_orphelins()
```

Dans `config_mention` (lignes 825-838), remplacer le corps. Le code actuel écrit
`config["role_id"] = None` via `bot.store.set` puis `maj_config(role_id=…)` ; les
deux disparaissent au profit des méthodes de la tâche 1 :

```python
        if role is None:
            if await bot.store.effacer_role(str(interaction.guild.id)):
                message = (
                    "✅ Mention désactivée **sur ce serveur** : les posts n'y "
                    "pingueront plus personne."
                )
            else:
                message = "ℹ️ Aucune mention n'était réglée sur ce serveur."
            await interaction.response.send_message(message, ephemeral=True)
            return

        await bot.store.definir_role(str(interaction.guild.id), str(role.id))
        await interaction.response.send_message(
            f"✅ {role.mention} sera mentionné à chaque post **sur ce serveur**.\n"
            "-# Les autres serveurs gardent leur propre réglage.",
            ephemeral=True,
        )
```

- [ ] **Step 4: Corriger `/config voir`, qui affiche encore un rôle unique**

`src/bot.py:470` fait `role = f"<@&{config['role_id']}>" if config.get("role_id") else "*aucun*"`. Laissé tel quel, l'embed afficherait le rôle d'un seul serveur — ou « aucun » alors qu'un autre serveur pingue. Remplacer par une ligne par serveur :

```python
        roles = await bot.store.roles()
        # Une ligne par serveur : une valeur unique laisserait croire que tous
        # les serveurs pinguent, ou qu'aucun ne le fait.
        if roles:
            noms = await bot.store.serveurs()
            role = "\n".join(
                f"{noms.get(serveur, serveur)} : <@&{role_id}>"
                for serveur, role_id in roles.items()
            )
        else:
            role = "*aucune*"
```

Retirer aussi `role_id` de l'aperçu API (`src/api.py:367`) : un aperçu n'appartient à aucun salon, donc à aucun serveur. Le plus honnête est de ne mentionner personne et de le dire.

```python
        # Aucune mention dans un aperçu : il n'appartient à aucun salon, donc à
        # aucun serveur, et un rôle n'existe que dans le sien. Mentionner « le »
        # rôle voudrait en choisir un arbitrairement.
        role_id = None
```

- [ ] **Step 5: Mettre à jour les tests existants et lancer la suite**

Run: `python -m pytest tests/ -q`

Deux tests existants vérifient l'ancien comportement et **doivent** échouer — les corriger, ce n'est pas les contourner :

- `tests/test_publication_multi.py:228` `test_le_role_est_mentionne_dans_chaque_salon` règle `maj_config(role_id="4242")` et son `SalonFactice` (ligne 31) **n'a pas d'attribut `guild`**. Le repli le fait passer tel quel — le vérifier plutôt que de le modifier : il documente désormais la compatibilité avec une config d'avant. Si le `getattr(salon, "guild", None)` de la tâche 3 est correct, il passe sans changement.
- Tout test de `/config voir` assertant la chaîne du rôle : l'adapter au rendu par serveur.

Expected: suite verte, ≈448 passed.

- [ ] **Step 6: Commit**

```bash
git add src/bot.py src/api.py tests/test_salons_connus.py tests/test_multi_serveurs.py
git commit -m "Brancher la memorisation et la mention par serveur"
```

---

## Task 6: Sérialisation — exposer `roles`, `serveurs`, `salons_connus`

**Files:**
- Modify: `src/serialisation.py:143-154` (`config_en_json`)
- Test: `tests/test_serialisation.py` (compléter **et corriger**)

**Interfaces:**
- Consumes: `Store.roles()`, `Store.serveurs()`, `Store.salons_connus()`.
- Produces: `config_en_json(config, fourchettes)` gagne trois clés : `roles: dict[str, str]`, `serveurs: dict[str, str]`, `salons_connus: dict[str, dict]`. `role_id` **disparaît**.

- [ ] **Step 1: Write the failing tests**

Ajouter à `tests/test_serialisation.py` :

```python
def test_contrat_roles_par_serveur():
    """Le site doit pouvoir dire quel serveur mentionne quoi.

    Un `role_id` unique laisserait croire que les deux serveurs sont pingués
    alors qu'un seul l'est.
    """
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris", "roles": {"111": "42"}},
        [],
    )

    assert rendu["roles"] == {"111": "42"}
    # `role_id` ne doit plus exister : le laisser inviterait le site à
    # l'afficher, donc à mentir dès qu'il y a deux serveurs.
    assert "role_id" not in rendu


def test_contrat_roles_absents_donnent_un_dict_vide():
    """Et non `null` : le site itère dessus sans garde."""
    rendu = config_en_json({"heure": "09:00", "fuseau": "Europe/Paris"}, [])

    assert rendu["roles"] == {}
    assert rendu["serveurs"] == {}
    assert rendu["salons_connus"] == {}


def test_contrat_role_id_plat_devient_un_role_par_serveur_connu():
    """Compatibilité d'affichage : une config d'avant ne doit pas afficher
    « aucune mention » alors que le bot pingue bien."""
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "role_id": "7",
            "serveurs": {"111": "Empire Immo"},
        },
        [],
    )

    assert rendu["roles"] == {"111": "7"}


def test_contrat_salons_connus():
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "serveurs": {"111": "Empire Immo"},
            "salons_connus": {"1": {"nom": "promos", "serveur": "111"}},
        },
        [],
    )

    assert rendu["serveurs"] == {"111": "Empire Immo"}
    assert rendu["salons_connus"]["1"]["nom"] == "promos"
    assert rendu["salons_connus"]["1"]["serveur"] == "111"


def test_contrat_ids_toujours_en_texte():
    """JSONB peut restituer un int : le site compare des chaînes, et `111 !=
    "111"` en TypeScript ferait échouer le groupement sans erreur."""
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "roles": {111: 42},
            "serveurs": {111: "Empire Immo"},
            "salons_connus": {1: {"nom": "promos", "serveur": 111}},
        },
        [],
    )

    assert rendu["roles"] == {"111": "42"}
    assert rendu["serveurs"] == {"111": "Empire Immo"}
    assert rendu["salons_connus"]["1"]["serveur"] == "111"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_serialisation.py -k "roles or salons_connus or ids_toujours" -q`
Expected: FAIL — `KeyError: 'roles'`

- [ ] **Step 3: Write minimal implementation**

Dans `src/serialisation.py`, remplacer les lignes 147-152 de `config_en_json` :

```python
        "roles": _roles_en_json(config),
        "serveurs": {
            str(serveur): str(nom)
            for serveur, nom in (config.get("serveurs") or {}).items()
            if nom
        },
        "salons_connus": {
            str(salon): {
                "nom": str(details.get("nom", "")),
                "serveur": str(details.get("serveur", "")),
            }
            for salon, details in (config.get("salons_connus") or {}).items()
            if isinstance(details, dict)
        },
        "logs_salon_id": (
            str(config.get("logs_salon_id")) if config.get("logs_salon_id") else None
        ),
```

Et, avant `config_en_json` :

```python
def _roles_en_json(config: dict) -> dict[str, str]:
    """Rôle mentionné par serveur, ids en texte.

    Un `role_id` d'avant le multi-serveurs est étendu aux serveurs connus : sans
    ça le site afficherait « aucune mention » alors que le bot pingue bien. Ce
    n'est qu'un affichage — la publication applique le repli elle-même, via
    `Store.role_du_serveur`.
    """
    table = config.get("roles") or {}
    if table:
        return {str(serveur): str(role) for serveur, role in table.items() if role}

    ancien = config.get("role_id")
    if not ancien:
        return {}
    return {str(serveur): str(ancien) for serveur in (config.get("serveurs") or {})}
```

Mettre à jour la docstring de `config_en_json` : plus de `role_id` à la racine, un rôle par serveur, et les noms viennent de la base et non d'une résolution Discord.

- [ ] **Step 4: Corriger les tests existants qui figent `role_id`**

Trois assertions vérifient la clé qui disparaît. Les corriger, ce n'est pas les affaiblir : le contrat change, elles doivent figer le nouveau.

- `tests/test_serialisation.py:138` — `assert rendu["role_id"] == "123"` → `assert rendu["roles"] == {}` (aucun serveur connu dans cette config, donc rien à étendre) et `assert "role_id" not in rendu`.
- `tests/test_serialisation.py:170` (`test_config_sans_mention_ni_journal`) — `assert rendu["role_id"] is None` → `assert rendu["roles"] == {}`.
- `tests/test_serialisation.py:280` (`test_contrat_config_champs_attendus`) — la boucle `for champ in ("role_id", "logs_salon_id")` ne garde que `logs_salon_id` ; ajouter que `roles`, `serveurs` et `salons_connus` sont des `dict` dont clés et valeurs sont des chaînes.

Mettre aussi à jour la mutation existante `serial-role-id-vide-au-lieu-de-null` (`tests/mutations.py:231-237`) : son motif `'"role_id": str(config.get("role_id")) if …'` disparaît du code, donc la campagne échouerait sur « motif introuvable ». La remplacer par l'équivalent sur `_roles_en_json`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: ≈453 passed

- [ ] **Step 6: Commit**

```bash
git add src/serialisation.py tests/test_serialisation.py tests/mutations.py
git commit -m "Exposer les roles par serveur et les noms au site"
```

---

## Task 7: Site — `grouperParServeur`, module pur

**Files:**
- Create: `D:\eiweb\lib\serveurs.ts`
- Create: `D:\eiweb\tests\serveurs.test.mjs`
- Modify: `D:\eiweb\tsconfig.test.json` (ajouter `lib/serveurs.ts` à `include`)

**Écart assumé par rapport à la spec :** la spec place `grouperParServeur` dans `lib/fourchettes.ts`. Un fichier séparé est préférable : le groupement par serveur ne parle ni de bornes ni de montants, `lib/fourchettes.ts` reste focalisé, et les deux modules restent purs et testables isolément. Le prix est une entrée de plus dans `tsconfig.test.json`.

**Interfaces:**
- Consumes: rien. Le module est autonome — il prend une liste d'ids et deux tables, et ne dépend donc pas de `lib/fourchettes.ts`. C'est ce qui permet de le tester sans fixture de fourchette.
- Produces:

```typescript
export type SalonConnu = { nom: string; serveur: string };
export type SalonAffiche = { id: string; nom: string | null };
export type ServeurAffiche = { id: string | null; nom: string | null; salons: SalonAffiche[] };
export function grouperParServeur(
  salons: string[],
  salonsConnus: Record<string, SalonConnu>,
  serveurs: Record<string, string>,
): ServeurAffiche[];
```

- [ ] **Step 1: Write the failing tests**

Créer `D:\eiweb\tests\serveurs.test.mjs` :

```javascript
import assert from "node:assert/strict";
import test from "node:test";

import { grouperParServeur } from "../dist/lib/serveurs.js";

const CONNUS = {
  "1": { nom: "promos", serveur: "111" },
  "2": { nom: "annonces", serveur: "111" },
  "3": { nom: "grosses-affaires", serveur: "222" },
};
const SERVEURS = { "111": "Empire Immo", "222": "Second serveur" };

test("groupe les salons par serveur", () => {
  const groupes = grouperParServeur(["1", "2", "3"], CONNUS, SERVEURS);

  assert.equal(groupes.length, 2);
  assert.equal(groupes[0].nom, "Empire Immo");
  assert.deepEqual(groupes[0].salons.map((s) => s.nom), ["promos", "annonces"]);
  assert.equal(groupes[1].nom, "Second serveur");
});

test("un salon inconnu n'est pas perdu", () => {
  // Le perdre silencieusement ferait croire qu'il n'est pas configuré, alors
  // qu'il reçoit bien les posts.
  const groupes = grouperParServeur(["1", "99"], CONNUS, SERVEURS);
  const tous = groupes.flatMap((g) => g.salons.map((s) => s.id));

  assert.ok(tous.includes("99"), "le salon inconnu doit rester visible");
});

test("un salon inconnu n'a pas de nom, et ça se voit", () => {
  const groupes = grouperParServeur(["99"], CONNUS, SERVEURS);

  assert.equal(groupes[0].salons[0].nom, null);
  assert.equal(groupes[0].salons[0].id, "99");
});

test("un serveur sans nom connu garde son id", () => {
  // Le bot connaît le salon mais pas encore le nom du serveur : mieux vaut
  // afficher l'id que rien.
  const groupes = grouperParServeur(
    ["1"],
    { "1": { nom: "promos", serveur: "333" } },
    {},
  );

  assert.equal(groupes[0].id, "333");
  assert.equal(groupes[0].nom, null);
});

test("l'ordre des serveurs suit la première apparition des salons", () => {
  // Stable : un ordre dépendant de l'itération d'un objet ferait sauter les
  // lignes du tableau d'un rendu à l'autre.
  const groupes = grouperParServeur(["3", "1"], CONNUS, SERVEURS);

  assert.deepEqual(groupes.map((g) => g.id), ["222", "111"]);
});

test("aucun salon donne aucun groupe", () => {
  assert.deepEqual(grouperParServeur([], CONNUS, SERVEURS), []);
});

test("un salon présent deux fois n'apparaît qu'une fois", () => {
  // `salonsUniques` dédoublonne déjà, mais un appelant peut passer la
  // concaténation brute des fourchettes.
  const groupes = grouperParServeur(["1", "1"], CONNUS, SERVEURS);

  assert.equal(groupes[0].salons.length, 1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd D:/eiweb && npm test`
Expected: FAIL — `ERR_MODULE_NOT_FOUND` sur `../dist/lib/serveurs.js`

- [ ] **Step 3: Write minimal implementation**

D'abord ajouter `"lib/serveurs.ts"` au tableau `include` de `D:\eiweb\tsconfig.test.json`.

Puis créer `D:\eiweb\lib\serveurs.ts` :

```typescript
/**
 * Regroupement des salons par serveur Discord, pour l'affichage.
 *
 * Le site n'a pas accès à Discord : il ne connaît ni les noms de salons, ni ceux
 * des serveurs. Le bot les mémorise au réglage et les rafraîchit à chaque
 * publication, puis les envoie dans `/api/config`. Ce module ne fait que les
 * organiser — il n'en invente aucun.
 *
 * Avec un seul serveur, afficher un id nu était austère ; avec deux, c'est
 * ambigu : rien ne dit d'où vient le salon.
 */

/** Un salon tel que le bot le mémorise (`Store.salons_connus`). */
export type SalonConnu = { nom: string; serveur: string };

/** Un salon prêt à afficher. `nom` est `null` si le bot ne le connaît pas. */
export type SalonAffiche = { id: string; nom: string | null };

/**
 * Un serveur et ses salons. `id` et `nom` sont `null` quand le bot ignore à quel
 * serveur appartient un salon — le salon reste listé, car le perdre ferait
 * croire qu'il n'est pas configuré.
 */
export type ServeurAffiche = {
  id: string | null;
  nom: string | null;
  salons: SalonAffiche[];
};

export function grouperParServeur(
  salons: string[],
  salonsConnus: Record<string, SalonConnu>,
  serveurs: Record<string, string>,
): ServeurAffiche[] {
  // `Map` et non un objet : l'ordre d'insertion est garanti, donc les lignes du
  // tableau ne sautent pas d'un rendu à l'autre.
  const groupes = new Map<string, ServeurAffiche>();

  for (const salon of new Set(salons)) {
    const connu = salonsConnus[salon];
    // "" comme clé du groupe inconnu : `null` ne peut pas indexer une `Map`
    // sans perdre la distinction avec un serveur nommé "null".
    const cle = connu?.serveur || "";
    let groupe = groupes.get(cle);
    if (!groupe) {
      groupe = {
        id: cle || null,
        nom: serveurs[cle] || null,
        salons: [],
      };
      groupes.set(cle, groupe);
    }
    groupe.salons.push({ id: salon, nom: connu?.nom || null });
  }

  return [...groupes.values()];
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd D:/eiweb && npm test`
Expected: PASS — 47 tests (40 existants + 7 nouveaux)

Run: `cd D:/eiweb && npx tsc --noEmit`
Expected: aucune erreur

- [ ] **Step 5: Commit**

```bash
cd D:/eiweb
git add lib/serveurs.ts tests/serveurs.test.mjs tsconfig.test.json
git commit -m "grouperParServeur : organiser les salons par serveur"
```

---

## Task 8: Site — contrat et affichage

**Files:**
- Modify: `D:\eiweb\lib\bot.ts:216` (type `Config`)
- Modify: `D:\eiweb\app\page.tsx` (table des fourchettes, vignette Mention)
- Modify: `D:\eiweb\app\reglages\page.tsx` (table des fourchettes, ligne Mention)

**Interfaces:**
- Consumes: `grouperParServeur`, `SalonConnu`, `ServeurAffiche` (tâche 7) ; les clés `roles`, `serveurs`, `salons_connus` de `/api/config` (tâche 6).

- [ ] **Step 1: Mettre à jour le contrat**

Dans `D:\eiweb\lib\bot.ts`, remplacer `role_id: string | null;` (ligne 216) :

```typescript
  /**
   * Rôle mentionné, **par serveur** : `{"<id serveur>": "<id rôle>"}`.
   *
   * Un rôle n'existe que dans son serveur. Une valeur unique laisserait croire
   * que tous les serveurs sont pingués alors qu'un seul l'est.
   */
  roles: Record<string, string>;
  /** Noms des serveurs connus du bot, `{"<id>": "<nom>"}`. */
  serveurs: Record<string, string>;
  /** Noms des salons connus, mémorisés par le bot au réglage. */
  salons_connus: Record<string, SalonConnu>;
```

Ajouter l'import et la ré-exportation, à côté de ceux de `Fourchette` :

```typescript
import type { SalonConnu } from "@/lib/serveurs";
export type { SalonConnu };
```

- [ ] **Step 2: Vérifier que la compilation échoue**

Run: `cd D:/eiweb && npx tsc --noEmit`
Expected: FAIL — `Property 'role_id' does not exist on type 'Config'` dans `app/page.tsx` et `app/reglages/page.tsx`. C'est le compilateur qui liste exactement les endroits à corriger.

- [ ] **Step 3: Corriger les deux pages**

Dans `D:\eiweb\app\page.tsx` :

Ajouter l'import :

```typescript
import { grouperParServeur } from "@/lib/serveurs";
```

Remplacer la cellule des salons (lignes 168-178) par un rendu groupé :

```tsx
                      <td>
                        {fourchette.salons.length === 0 ? (
                          <span className="puce puce-orange">aucun</span>
                        ) : (
                          grouperParServeur(
                            fourchette.salons,
                            config.salons_connus,
                            config.serveurs,
                          ).map((serveur) => (
                            <div key={serveur.id ?? "inconnu"}>
                              {/* Le serveur nommé devant : deux salons peuvent
                                  s'appeler #promos sur deux serveurs. */}
                              <span className="aide">
                                {serveur.nom ?? `serveur ${serveur.id ?? "inconnu"}`}
                              </span>{" "}
                              {serveur.salons.map((salon) => (
                                <code key={salon.id} style={{ marginRight: "0.3rem" }}>
                                  {salon.nom ? `#${salon.nom}` : salon.id}
                                </code>
                              ))}
                            </div>
                          ))
                        )}
                      </td>
```

Remplacer la vignette Mention (lignes 197-199) :

```tsx
              <Vignette etiquette="Mention">
                {/* Le nombre de serveurs qui pinguent, pas un rôle : avec
                    plusieurs serveurs, en afficher un seul laisserait croire
                    que les autres pinguent aussi. */}
                {Object.keys(config.roles).length === 0
                  ? "aucune"
                  : `${Object.keys(config.roles).length} serveur${
                      Object.keys(config.roles).length > 1 ? "s" : ""
                    }`}
              </Vignette>
```

Ajouter une vignette du nombre de serveurs servis, après « Salons servis » :

```tsx
              <Vignette etiquette="Serveurs">
                {grouperParServeur(salons, config.salons_connus, config.serveurs).length}
              </Vignette>
```

Dans `D:\eiweb\app\reglages\page.tsx` :

Même import, même remplacement de la cellule des salons (lignes 79-88).

Remplacer la ligne Mention (ligne 117) par une ligne par serveur :

```tsx
                  <td>
                    {Object.keys(config.roles).length === 0 ? (
                      "aucune"
                    ) : (
                      <>
                        {Object.entries(config.roles).map(([serveur, role]) => (
                          <div key={serveur}>
                            {config.serveurs[serveur] ?? `serveur ${serveur}`} :{" "}
                            <code>{role}</code>
                          </div>
                        ))}
                        {/* Les serveurs connus **sans** rôle : les taire
                            laisserait croire qu'ils pinguent aussi. */}
                        {Object.keys(config.serveurs)
                          .filter((serveur) => !config.roles[serveur])
                          .map((serveur) => (
                            <div key={serveur}>
                              {config.serveurs[serveur]} :{" "}
                              <span className="puce puce-orange">aucune</span>
                            </div>
                          ))}
                      </>
                    )}
                  </td>
```

Mettre à jour le texte d'aide de la carte « Fourchettes et leurs salons » pour dire que les salons peuvent vivre sur plusieurs serveurs et que `/config mention` vaut pour le serveur où la commande est tapée.

- [ ] **Step 4: Vérifier la compilation et les tests**

Run: `cd D:/eiweb && npx tsc --noEmit`
Expected: aucune erreur

Run: `cd D:/eiweb && npm test`
Expected: 47 passed

Run: `cd D:/eiweb && npm run build`
Expected: succès

Run: `cd D:/eiweb && grep -rn "role_id" app/ components/ lib/`
Expected: aucun résultat

- [ ] **Step 5: Commit**

```bash
cd D:/eiweb
git add lib/bot.ts app/page.tsx app/reglages/page.tsx
git commit -m "Afficher les salons par serveur et la mention serveur par serveur"
```

---

## Task 9: Mutations et documentation

**Files:**
- Modify: `tests/mutations.py` (45 → 51)
- Modify: `README.md` (bot)
- Modify: `D:\eiweb\README.md`

**Interfaces:** aucune.

- [ ] **Step 1: Ajouter les six mutations**

Dans `tests/mutations.py`, juste avant `api-champ-interdit-accepte` (ligne 382). Rappel : le fichier est en CRLF — l'éditer avec l'outil Edit, ou en Python via `io.open(..., encoding="utf-8")` **sans** `newline=""`.

```python
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
        "serial-role-id-plat-non-etendu",
        "src/serialisation.py",
        "    return {str(serveur): str(ancien) for serveur in (config.get(\"serveurs\") or {})}",
        "    return {}",
        "le site dirait « aucune mention » alors que le bot pingue bien",
    ),
```

- [ ] **Step 2: Lancer la campagne**

Run: `python tests/mutations.py 2>&1 | tail -25`
Expected: `51/51 mutations détectées.`

La campagne dépasse le délai de 120 s : la lancer en tâche de fond et lire le fichier de sortie. Si une mutation survit, c'est un trou de couverture — écrire le test qui la tue, puis relancer. Si un motif est « introuvable ou ambigu », c'est que le code implémenté diffère du plan : adapter le motif au code réel, pas l'inverse.

- [ ] **Step 3: Vérifier qu'aucun fichier n'est resté muté**

Run: `git diff --stat src/`
Expected: seulement les modifications de la fonctionnalité, aucune trace de mutation.

- [ ] **Step 4: Documentation**

Dans `README.md` (bot) :

- Remplacer `GUILD_ID` par `GUILD_IDS` dans le tableau des variables, en gardant une ligne qui dit que `GUILD_ID` reste accepté en repli.
- Ajouter une section `## Plusieurs serveurs` : les salons peuvent vivre sur plusieurs serveurs, la publication n'a rien de spécial à faire (`resoudre_salon` traverse les serveurs), `/config mention` vaut pour le serveur où la commande est tapée, et `/fourchette salon ajouter` se tape dans le serveur du salon.
- **À côté du lien d'invitation**, en gras : inviter le bot sur un serveur revient à en donner les clés. `est_admin` vient des permissions du serveur où la commande est tapée et la config est globale, donc un administrateur d'un serveur déclaré dans `GUILD_IDS` peut changer les prix, l'heure, le template et la liste d'accès — pour tous les serveurs. Un serveur **non** déclaré n'a aucune commande.
- Dans la checklist de déploiement : des commandes déjà synchronisées globalement subsistent **en plus** de celles par serveur et apparaissent en double dans le sélecteur ; à constater après le déploiement.
- Mettre à jour le nombre de tests et de mutations.

Dans `D:\eiweb\README.md` :

- Section « Les fourchettes » : ajouter `grouperParServeur` au tableau des fonctions et dire pourquoi les noms viennent du bot (le site n'a pas accès à Discord).
- Section « Ce que le site ne fait pas » : la mention se règle par serveur, dans Discord.
- Ajouter `lib/serveurs.ts` à la structure, et passer le nombre de modules purs de quatre à cinq.
- Mettre à jour le nombre de tests.

- [ ] **Step 5: Commit**

```bash
git add tests/mutations.py README.md
git commit -m "Mutations et documentation du multi-serveurs"
cd D:/eiweb && git add README.md && git commit -m "Documenter l'affichage par serveur"
```

---

## Task 10: Vérification de bout en bout et déploiement de test

**Files:** aucun. Vérification seule.

- [ ] **Step 1: Suites complètes**

```bash
cd D:/bot && python -m pytest tests/ -q
cd D:/eiweb && npm test && npx tsc --noEmit && npm run build
```

Expected: bot ≈453 passed, site 47 passed, build réussi.

- [ ] **Step 2: Vérifier le contrat contre une vraie sortie du bot**

Ne pas écrire de fixture à la main : construire un `Store` réel, régler deux serveurs, puis vérifier que le module compilé du site consomme la sortie.

`/tmp` n'existe pas dans ce Git Bash — écrire dans `$TEMP`.

```bash
cd D:/bot && python -c "
import asyncio, json
from decimal import Decimal
from src.db import Store
from src.serialisation import config_en_json

async def main():
    store = Store(dsn='')
    await store.connect()
    await store.ajouter_fourchette('grosses', Decimal('1e15'), Decimal('6e15'))
    await store.ajouter_salon_fourchette('grosses', '1')
    await store.ajouter_salon_fourchette('grosses', '3')
    await store.memoriser_salon('1', 'promos', '111', 'Empire Immo')
    await store.memoriser_salon('3', 'grosses-affaires', '222', 'Second serveur')
    await store.definir_role('111', '42')
    rendu = config_en_json(await store.config(), await store.fourchettes())
    print(json.dumps(rendu, ensure_ascii=True))

asyncio.run(main())
" > "$TEMP/contrat.json"
```

Puis, côté site, vérifier que `grouperParServeur` produit bien deux groupes nommés à partir de ce JSON — un petit script `node` jetable qui lit `$TEMP/contrat.json` et affiche les groupes. **Supprimer les fichiers temporaires ensuite.**

Attendu : deux groupes, `Empire Immo` avec `#promos` et `Second serveur` avec `#grosses-affaires` ; `roles` vaut `{"111": "42"}`.

- [ ] **Step 3: Pousser sur les dépôts de test**

```bash
cd D:/bot && git push test main
cd D:/eiweb && git push test main
```

- [ ] **Step 4: Vérifier le bot de test en direct**

Attendre le redéploiement Render, puis :

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sanguinius.onrender.com/health
```

Expected: 200

Puis `/api/config` avec l'en-tête `X-Api-Secret` (valeur dans `D:\eiweb\secrets-test.txt`, gitignoré) : la réponse doit contenir `roles`, `serveurs` et `salons_connus`, et **plus** `role_id`.

- [ ] **Step 5: Ne rien pousser en production sans accord**

La prod (`origin`) attend une décision explicite. Le rappeler dans le compte rendu, avec le point de vigilance : `role_id` plat en base s'applique encore partout jusqu'au premier `/config mention`, ce qui est le comportement voulu mais mérite d'être vérifié après le premier post.

---

## Auto-relecture

**Couverture de la spec :**

| Exigence de la spec | Tâche |
|---|---|
| `GUILD_IDS`, `GUILD_ID` en repli | 2 |
| Publication inchangée (`resoudre_salon` traverse) | 3 (test), 5 (docstring) |
| `config["roles"]` par serveur | 1 |
| `role_id` en repli, non converti | 1, 6 |
| Cas mixte `roles` + `role_id` | 1 |
| Rôle résolu depuis `salon.guild.id` | 3 |
| `publish.envoyer` signature inchangée | 3 |
| `/config mention` par serveur | 5 |
| `config["serveurs"]`, `config["salons_connus"]` | 4 |
| Noms rafraîchis à la résolution | 5 |
| Nettoyage des orphelins | 4, 5 |
| Pas de défaut dans `config_par_defaut()` | Contrainte globale, testé en 1 et 4 |
| `_CHAMPS_PLATS` inchangé | Contrainte globale |
| `fourchette["salons"]` liste d'ids | Contrainte globale |
| `logs_salon_id` global | Inchangé, aucune tâche |
| Contrat site `roles`/`serveurs`/`salons_connus` | 6, 8 |
| `grouperParServeur` | 7 |
| Salon inconnu non perdu | 7 |
| Pages groupées par serveur | 8 |
| Mention par serveur affichée | 8 |
| `PATCH /api/config` toujours limité | Inchangé — `CHAMPS_MODIFIABLES` reste `("heure", "fuseau")` |
| README : `GUILD_IDS` + « les clés » | 9 |
| Piège des commandes en double | 9 |
| Mutations | 9 |

**Placeholders :** aucun. Chaque étape de code porte le code réel.

**Cohérence des types :** `Store.role_du_serveur(serveur_id: str | int | None) -> str | None` est produit en tâche 1 et consommé en tâche 3 sous ce nom exact. `SalonConnu` est déclaré en tâche 7 (`lib/serveurs.ts`) et importé en tâche 8 (`lib/bot.ts`) — même sens que `Fourchette`, déclaré dans le module pur et ré-exporté par `lib/bot.ts`. `grouperParServeur(salons, salonsConnus, serveurs)` prend une **liste d'ids** en premier argument, ce que passent les deux pages en tâche 8.

**Écart connu, assumé :** le nombre de tests annoncé à chaque étape (427, 434, 438…) est indicatif — la base vérifiée est **418 tests bot et 40 tests site**. Ce qui compte est que la suite reste verte et augmente du nombre de tests ajoutés.

**Vérifié contre le code réel** (et non contre le souvenir du code) avant publication de ce plan :

- `src/settings.py:90` déclare `role_id` dans `config_par_defaut()`, depuis `ROLE_DEFAUT = os.getenv("ROLE_ID", "")`. La contrainte globale et la fixture de la tâche 1 en tiennent compte.
- `config_mention` est aux lignes 822-838 et écrit par `store.set` **puis** `maj_config` — la tâche 5 remplace les deux.
- `src/bot.py:470` (`/config voir`) et `src/api.py:367` (aperçu) lisent `role_id` : traités en tâche 5, étape 4. Sans ça la suite resterait verte avec un affichage faux.
- Les trois assertions `role_id` de `tests/test_serialisation.py` (138, 170, 280) et la mutation `serial-role-id-vide-au-lieu-de-null` (`tests/mutations.py:231`) portent sur la clé supprimée : corrigées en tâche 6.
- `tests/test_publication_multi.py:228` utilise un `SalonFactice` sans `guild` : le `getattr(salon, "guild", None)` de la tâche 3 le fait passer par le repli, sans modifier ce test.
- `resoudre_salon` n'a qu'un seul appelant (`src/bot.py:245`). La spec annonçait un rafraîchissement des noms par `/fourchette liste` : c'est **faux** — `_lister_fourchettes` (`src/bot.py:345`) utilise `bot.get_channel` directement, sans passer par `resoudre_salon`. Les noms se rafraîchissent donc à la publication quotidienne et au réglage, pas à l'affichage de la liste. Le README doit dire ça, et non ce que la spec annonçait.
- `tsconfig.test.json` a un `include` **explicite** : un nouveau module non listé ne serait pas compilé, et son test échouerait sur `ERR_MODULE_NOT_FOUND`. D'où l'étape dédiée en tâche 7.
- `lib/bot.ts:195` importe `Fourchette` via l'alias `@/lib/fourchettes` (le `paths` de `tsconfig.json` mappe `@/*`), et non par un chemin relatif. La tâche 8 suit cette forme.

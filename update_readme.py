"""
update_readme.py

Consulta la Riot API para traer, de una cuenta y campeón específicos:
 - Nivel y puntos de maestría
 - Rango en Solo/Duo (ranked)
 - Winrate calculado sobre las últimas N partidas jugadas con ese campeón

Y actualiza el bloque marcado en el README.md con esa info en formato Markdown.

Variables de entorno necesarias (se configuran como Secrets en GitHub Actions):
 - RIOT_API_KEY      -> tu API key de Riot
 - RIOT_GAME_NAME    -> parte antes del "#" de tu Riot ID (ej: RaviolPignolo)
 - RIOT_TAG_LINE     -> parte después del "#" (ej: K4rth)
 - PLATFORM_REGION   -> región de plataforma (la2 para LAS, la1 LAN, na1, euw1, kr, etc.)
 - ROUTING_REGION    -> región de "routing" (americas, europe, asia, sea)
 - CHAMPION_NAME     -> nombre del campeón tal cual en la API de Riot (ej: Karthus)
 - MATCHES_TO_SCAN   -> cuántas partidas ranked recientes escanear para el winrate (ej: 30)
 - README_PATH       -> ruta al README (default: README.md)
"""

import os
import sys
import time
import requests

RIOT_API_KEY = os.environ["RIOT_API_KEY"]
GAME_NAME = os.environ["RIOT_GAME_NAME"]
TAG_LINE = os.environ["RIOT_TAG_LINE"]
PLATFORM = os.environ.get("PLATFORM_REGION", "la2")
ROUTING = os.environ.get("ROUTING_REGION", "americas")
CHAMPION_NAME = os.environ.get("CHAMPION_NAME", "Karthus")
MATCHES_TO_SCAN = int(os.environ.get("MATCHES_TO_SCAN", "30"))
README_PATH = os.environ.get("README_PATH", "README.md")

HEADERS = {"X-Riot-Token": RIOT_API_KEY}

START_MARK = "<!---LOL-STATS-START-HERE--->"
END_MARK = "<!---LOL-STATS-END-HERE--->"


def riot_get(url, params=None, retries=3):
    """GET con reintentos simples y respeto al rate limit (429)."""
    for attempt in range(retries):
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            print(f"Rate limited, esperando {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"No se pudo completar la request a {url} tras {retries} intentos")


def get_puuid():
    url = f"https://{ROUTING}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}"
    data = riot_get(url)
    return data["puuid"]


def get_champion_id(champion_name):
    """Data Dragon no requiere API key. Mapea nombre -> id numérico de campeón."""
    versions = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=15).json()
    latest = versions[0]
    champs = requests.get(
        f"https://ddragon.leagueoflegends.com/cdn/{latest}/data/en_US/champion.json", timeout=15
    ).json()["data"]

    for champ_key, champ_data in champs.items():
        if champ_key.lower() == champion_name.lower():
            return int(champ_data["key"]), champ_data["id"]
    raise ValueError(f"No encontré el campeón '{champion_name}' en Data Dragon")


def get_mastery(puuid, champion_id):
    url = (
        f"https://{PLATFORM}.api.riotgames.com/lol/champion-mastery/v4/"
        f"champion-masteries/by-puuid/{puuid}/by-champion/{champion_id}"
    )
    try:
        return riot_get(url)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None  # nunca jugó ese campeón
        raise


def get_ranked_solo(puuid):
    url = f"https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    entries = riot_get(url)
    for entry in entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return entry
    return None


def get_champion_winrate(puuid, champion_id):
    """Escanea las últimas MATCHES_TO_SCAN partidas ranked y calcula winrate con el campeón."""
    ids_url = f"https://{ROUTING}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
    match_ids = riot_get(ids_url, params={"queue": 420, "start": 0, "count": MATCHES_TO_SCAN})

    wins, losses = 0, 0
    for match_id in match_ids:
        match_url = f"https://{ROUTING}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        match = riot_get(match_url)
        for p in match["info"]["participants"]:
            if p["puuid"] == puuid and p["championId"] == champion_id:
                if p["win"]:
                    wins += 1
                else:
                    losses += 1
        time.sleep(0.05)  # cortesía con el rate limit

    total = wins + losses
    winrate = round((wins / total) * 100, 1) if total else None
    return wins, losses, winrate, total


def build_markdown(champion_display_name, mastery, ranked, wins, losses, winrate, total_games):
    lines = [f"### 📊 Stats de LoL — {champion_display_name}", ""]

    if mastery:
        lines.append(f"- **Maestría:** Nivel {mastery['championLevel']} — {mastery['championPoints']:,} puntos")
    else:
        lines.append("- **Maestría:** sin datos (¿nunca jugaste este campeón?)")

    if ranked:
        tier = ranked["tier"].capitalize()
        rank = ranked["rank"]
        lp = ranked["leaguePoints"]
        w, l = ranked["wins"], ranked["losses"]
        total_wr = round((w / (w + l)) * 100, 1) if (w + l) else 0
        lines.append(f"- **Rango (Solo/Duo):** {tier} {rank} — {lp} LP ({w}W / {l}L, {total_wr}% WR general)")
    else:
        lines.append("- **Rango (Solo/Duo):** Unranked")

    if total_games:
        lines.append(
            f"- **Winrate con {champion_display_name} (últimas {total_games} partidas ranked):** "
            f"{winrate}% ({wins}W / {losses}L)"
        )
    else:
        lines.append(f"- **Winrate con {champion_display_name}:** no jugaste este campeón en las últimas partidas escaneadas")

    lines.append("")
    lines.append(f"_Actualizado automáticamente · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}_")
    return "\n".join(lines)


def update_readme(new_block):
    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if START_MARK not in content or END_MARK not in content:
        print(f"ERROR: no encontré los marcadores {START_MARK} / {END_MARK} en {README_PATH}")
        sys.exit(1)

    before = content.split(START_MARK)[0]
    after = content.split(END_MARK)[1]
    new_content = f"{before}{START_MARK}\n{new_block}\n{END_MARK}{after}"

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("README actualizado correctamente.")


def main():
    print(f"Buscando cuenta {GAME_NAME}#{TAG_LINE}...")
    puuid = get_puuid()

    print(f"Buscando id de campeón para '{CHAMPION_NAME}'...")
    champion_id, champion_display_name = get_champion_id(CHAMPION_NAME)

    print("Consultando maestría...")
    mastery = get_mastery(puuid, champion_id)

    print("Consultando rango ranked...")
    ranked = get_ranked_solo(puuid)

    print(f"Escaneando últimas {MATCHES_TO_SCAN} partidas ranked para calcular winrate...")
    wins, losses, winrate, total = get_champion_winrate(puuid, champion_id)

    block = build_markdown(champion_display_name, mastery, ranked, wins, losses, winrate, total)
    print("\n--- Bloque generado ---\n")
    print(block)

    update_readme(block)


if __name__ == "__main__":
    main()

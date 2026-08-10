"""
update_readme.py

Este script consulta la API de Riot para obtener datos de un jugador y un campeón,
calcula los resultados de la season, y actualiza automáticamente un bloque dentro de
README.md con esa información.

Explicación simple:
 - Busca tu cuenta de Riot usando tu nombre y tag.
 - Busca el ID del campeón que indicas.
 - Obtiene tu rango en Solo/Duo.
 - Revisa todas las partidas de la season actual y calcula el winrate del campeón en SoloQ y normales.
 - Escribe esos datos dentro del README en formato Markdown.

Variables de entorno necesarias (se configuran como Secrets en GitHub Actions):
 - RIOT_API_KEY      -> tu API key de Riot
 - RIOT_GAME_NAME    -> parte antes del "#" de tu Riot ID (ej: RaviolPignolo)
 - RIOT_TAG_LINE     -> parte después del "#" (ej: K4rth)
 - PLATFORM_REGION   -> región de plataforma (la2 para LAS, la1 LAN, na1, euw1, kr, etc.)
 - ROUTING_REGION    -> región de "routing" (americas, europe, asia, sea)
 - CHAMPION_NAME     -> nombre del campeón tal cual en la API de Riot (ej: Karthus)
 - README_PATH       -> ruta al README (default: README.md)
"""

import datetime
import os
import sys
import time
import requests

# Configuración del entorno.
# Se usan valores por defecto para que el módulo pueda importarse en pruebas
# sin que las variables de entorno estén presentes.
RIOT_API_KEY = os.environ.get("RIOT_API_KEY", "")
GAME_NAME = os.environ.get("RIOT_GAME_NAME", "")
TAG_LINE = os.environ.get("RIOT_TAG_LINE", "")
PLATFORM = os.environ.get("PLATFORM_REGION", "la2")
ROUTING = os.environ.get("ROUTING_REGION", "americas")
CHAMPION_NAME = os.environ.get("CHAMPION_NAME", "Karthus")
README_PATH = os.environ.get("README_PATH", "README.md")

HEADERS = {"X-Riot-Token": RIOT_API_KEY}

START_MARK = "<!---LOL-STATS-START-HERE--->"
END_MARK = "<!---LOL-STATS-END-HERE--->"


def riot_get(url, params=None, retries=3):
    """Hace una llamada GET a la API de Riot y maneja reintentos por rate limit.

    Args:
        url (str): URL completa de la petición.
        params (dict, optional): Parámetros de consulta para la petición.
        retries (int, optional): Número máximo de reintentos cuando la API responde 429.

    Returns:
        dict: Respuesta JSON decodificada de la API.

    Raises:
        RuntimeError: Si no se puede completar la petición tras reintentos.
    """
    for attempt in range(retries):
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 429: #Too Many Requests
            wait = int(resp.headers.get("Retry-After", "2"))
            print(f"Rate limited, esperando {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"No se pudo completar la request a {url} tras {retries} intentos")


def get_puuid():
    """Obtiene el identificador único (puuid) de la cuenta de Riot del usuario.

    Usa el nombre de invocador y el tag para buscar la cuenta en Riot.

    Returns:
        str: El valor 'puuid' de la cuenta.
    """
    url = f"https://{ROUTING}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}"
    data = riot_get(url)
    return data["puuid"]


def get_champion_id(champion_name):
    """Busca el ID numérico de un campeón usando Data Dragon.

    Data Dragon es la fuente oficial de Riot para información básica de campeones.
    No requiere clave de API y devuelve el identificador interno del campeón.

    Args:
        champion_name (str): Nombre del campeón según la API de Riot.

    Returns:
        tuple[int, str]: El ID numérico del campeón y su nombre para mostrar.

    Raises:
        ValueError: Si el campeón no se encuentra en los datos de Data Dragon.
    """
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
    """Obtiene la maestría del campeón para una cuenta específica.

    Esta función llama a la API de maestría de campeones de Riot y devuelve los
    datos de maestría como nivel y puntos acumulados.

    Args:
        puuid (str): Identificador único de la cuenta del jugador.
        champion_id (int): ID numérico del campeón.

    Returns:
        dict | None: Datos de maestría si existen, o None si no se jugó el campeón.
    """
    url = (
        f"https://{PLATFORM}.api.riotgames.com/lol/champion-mastery/v4/"
        f"champion-masteries/by-puuid/{puuid}/by-champion/{champion_id}"
    )
    try:
        return riot_get(url)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None  # No se jugó el champ
        raise


def get_ranked_solo(puuid):
    """Obtiene la información de rango en SoloQ de una cuenta.

    Llama a la API de ligas y busca la entrada correspondiente a la cola
    "RANKED_SOLO_5x5".

    Args:
        puuid (str): Identificador único de la cuenta del jugador.

    Returns:
        dict | None: Datos de rango si existen, o None si la cuenta no está en SoloQ.
    """
    url = f"https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    entries = riot_get(url)
    for entry in entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return entry
    return None


def get_season_start_time():
    """Devuelve el timestamp de inicio de la season actual (1 de enero UTC)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    start_of_year = datetime.datetime(now.year, 1, 1, tzinfo=datetime.timezone.utc)
    return int(start_of_year.timestamp())


def get_match_ids(puuid, queue, start=0, count=100, start_time=None):
    """Pide a Riot una lista de IDs de partidas jugadas por el usuario.

    Args:
        puuid (str): Identificador único de la cuenta del jugador.
        queue (int): Código de la cola de partidas (por ejemplo 420 para SoloQ).
        start (int, optional): Offset para paginar resultados.
        count (int, optional): Cantidad máxima de IDs a pedir en una sola llamada.
        start_time (int | None, optional): Timestamp en milisegundos para filtrar partidas recientes.

    Returns:
        list[str]: Lista de IDs de partidas.
    """
    ids_url = f"https://{ROUTING}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
    params = {"queue": queue, "start": start, "count": count}
    if start_time is not None:
        params["startTime"] = start_time
    return riot_get(ids_url, params=params)


def format_time_since(played_at, now=None):
    """Convierte un timestamp de partida en un texto legible como "3h 25m ago".

    Args:
        played_at (datetime | None): Momento en el que se jugó la partida.
        now (datetime | None, optional): Referencia actual para calcular la diferencia.

    Returns:
        str: Texto resumido del tiempo transcurrido.
    """
    if played_at is None:
        return "Never"

    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)

    delta = now - played_at
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "just now"

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")

    return f"{' '.join(parts)} ago"


def get_last_match_age(puuid, queue, start_time=None):
    """Devuelve el tiempo transcurrido desde la última partida de una cola.

    Args:
        puuid (str): Identificador único de la cuenta del jugador.
        queue (int | list[int]): Código o códigos de cola a revisar.
        start_time (int | None, optional): Timestamp de inicio para filtrar partidas recientes.

    Returns:
        str: Texto legible del tiempo desde la última partida, o "Never" si no hay partidas.
    """
    if isinstance(queue, int):
        queues = [queue]
    else:
        queues = queue

    for queue_code in queues:
        match_ids = get_match_ids(puuid, queue_code, start=0, count=20, start_time=start_time)
        if not match_ids:
            continue

        for match_id in match_ids:
            match_url = f"https://{ROUTING}.api.riotgames.com/lol/match/v5/matches/{match_id}"
            match = riot_get(match_url)
            game_end_timestamp = match.get("info", {}).get("gameEndTimestamp")
            if game_end_timestamp is not None:
                played_at = datetime.datetime.fromtimestamp(
                    game_end_timestamp / 1000, tz=datetime.timezone.utc
                )
                return format_time_since(played_at)
            time.sleep(0.05)

    return "Never"


def get_champion_winrate(puuid, champion_id, queues, start_time=None):
    """Calcula el winrate de un campeón para una cuenta en una o más colas.

    Esta función recorre todas las partidas de las colas indicadas desde el inicio de la
    season y cuenta cuántas partidas se ganaron o perdieron con el campeón seleccionado.

    Args:
        puuid (str): Identificador único de la cuenta del jugador.
        champion_id (int): ID numérico del campeón.
        queues (int | list[int]): Código de cola o lista de códigos de cola a revisar.
        start_time (int | None, optional): Timestamp de inicio para filtrar partidas de la season.

    Returns:
        tuple[int, int, float | None, int]: Ganadas, perdidas, porcentaje de winrate y total de partidas.
    """
    if isinstance(queues, int):
        queues = [queues]

    wins, losses = 0, 0
    for queue in queues:
        start = 0
        while True:
            match_ids = get_match_ids(puuid, queue, start=start, count=100, start_time=start_time)
            if not match_ids:
                break

            for match_id in match_ids:
                match_url = f"https://{ROUTING}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                match = riot_get(match_url)
                for p in match["info"]["participants"]:
                    if p["puuid"] == puuid and p["championId"] == champion_id:
                        if p["win"]:
                            wins += 1
                        else:
                            losses += 1
                time.sleep(0.05)  # Pausa pequeña para no saturar la API.

            if len(match_ids) < 100:
                break
            start += len(match_ids)

    total = wins + losses
    winrate = round((wins / total) * 100, 1) if total else None
    return wins, losses, winrate, total


def build_markdown(champion_display_name, mastery, ranked, ranked_wins, ranked_losses, ranked_winrate, ranked_total, normal_wins, normal_losses, normal_winrate, normal_total, ranked_last_played, normal_last_played):
    """Construye el bloque de texto en formato Markdown para el README.

    Args:
        champion_display_name (str): Nombre del campeón para mostrar.
        mastery (dict | None): Datos de maestría del campeón.
        ranked (dict | None): Datos de rango en SoloQ.
        ranked_wins (int): Partidas ganadas en SoloQ esta season.
        ranked_losses (int): Partidas perdidas en SoloQ esta season.
        ranked_winrate (float | None): Winrate en SoloQ esta season.
        ranked_total (int): Total de partidas en SoloQ esta season.
        normal_wins (int): Partidas ganadas en normales esta season.
        normal_losses (int): Partidas perdidas en normales esta season.
        normal_winrate (float | None): Winrate en normales esta season.
        normal_total (int): Total de partidas en normales esta season.
        ranked_last_played (str): Texto legible del tiempo desde la última partida ranked.
        normal_last_played (str): Texto legible del tiempo desde la última partida normal.

    Returns:
        str: Texto completo en Markdown para insertar en el README.
    """
    lines = [f"### 📊 Stats of {champion_display_name}", ""]

    if mastery:
        lines.append(f"- **Maestry:** Nivel {mastery['championLevel']} — {mastery['championPoints']:,} puntos")
    else:
        lines.append("- **Maestry:** NaN (League of what?)")

    if ranked:
        tier = ranked["tier"].capitalize()
        rank = ranked["rank"]
        lp = ranked["leaguePoints"]
        w, l = ranked["wins"], ranked["losses"]
        total_wr = round((w / (w + l)) * 100, 1) if (w + l) else 0
        lines.append(f"- **Rank (SoloQ):** {tier} {rank} — {lp} LP ({w}W / {l}L, {total_wr}% WR)")
    else:
        lines.append("- **Rank (SoloQ):** Unranked")

    if ranked_total:
        lines.append(
            f"- **Season SoloQ {champion_display_name} Winrate:** {ranked_winrate}% "
            f"({ranked_wins}W / {ranked_losses}L - {ranked_total} Games)"
        )
    else:
        lines.append(f"- **Season SoloQ {champion_display_name} Winrate:** No games")

    if normal_total:
        lines.append(
            f"- **Season Normals {champion_display_name} Winrate:** {normal_winrate}% "
            f"({normal_wins}W / {normal_losses}L - {normal_total} Games)"
        )
    else:
        lines.append(f"- **Season Normals {champion_display_name} Winrate:** No games")

    lines.append(f"- **Last Ranked Game:** {ranked_last_played}")
    lines.append(f"- **Last Normal Game:** {normal_last_played}")

    lines.append("")
    lines.append(f"_Last update · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}_")
    return "\n".join(lines)


def update_readme(new_block):
    """Reemplaza el bloque marcado en el README con el nuevo contenido generado.

    Args:
        new_block (str): Texto en Markdown que se debe insertar entre los marcadores.
    """
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

    # Calcula el inicio de la season actual para filtrar solo partidas de este año.
    season_start = get_season_start_time()

    print("Calculando winrate de la season en SoloQ...")
    ranked_wins, ranked_losses, ranked_winrate, ranked_total = get_champion_winrate(
        puuid, champion_id, 420, start_time=season_start
    )

    print("Calculando winrate de la season en partidas normales...")
    normal_wins, normal_losses, normal_winrate, normal_total = get_champion_winrate(
        puuid, champion_id, [400, 430], start_time=season_start
    )

    print("Consultando la última partida ranked...")
    ranked_last_played = get_last_match_age(puuid, 420, start_time=season_start)

    print("Consultando la última partida normal...")
    normal_last_played = get_last_match_age(puuid, [400, 430], start_time=season_start)

    # Armamos el texto en Markdown que se escribirá en el README.
    block = build_markdown(
        champion_display_name,
        mastery,
        ranked,
        ranked_wins,
        ranked_losses,
        ranked_winrate,
        ranked_total,
        normal_wins,
        normal_losses,
        normal_winrate,
        normal_total,
        ranked_last_played,
        normal_last_played,
    )
    print("\n--- Bloque generado ---\n")
    print(block)

    update_readme(block)


if __name__ == "__main__":
    main()

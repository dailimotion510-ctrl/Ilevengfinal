"""
Autorrelleno de metadatos con TMDB (The Movie Database).
El token es el mismo que usa el panel de admin.picta.cu (TMDB_API_TOKEN_V3,
sacado de su propio código). No es IMDb (no tiene API pública gratis);
TMDB es el equivalente que de hecho ya usa Picta para esto.
"""
import requests

TMDB_URL = "https://api.themoviedb.org/3"
TMDB_TOKEN = "02b60a5f22f7a8f94cd9500a494f98e8"  # mismo que usa admin.picta.cu
IMG_BASE = "https://image.tmdb.org/t/p/w780"

PAISES_ES = {  # ISO 3166-1 -> nombre en español, lo mínimo usual
    "US": "Estados Unidos", "ES": "España", "MX": "México", "CU": "Cuba",
    "AR": "Argentina", "FR": "Francia", "GB": "Reino Unido", "IT": "Italia",
    "DE": "Alemania", "JP": "Japón", "KR": "Corea del Sur", "BR": "Brasil",
    "CA": "Canadá", "CN": "China", "IN": "India", "RU": "Rusia",
}


def _get(path, **params):
    params["api_key"] = TMDB_TOKEN
    params.setdefault("language", "es-ES")
    r = requests.get(f"{TMDB_URL}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def buscar(query, tipo="movie"):
    """tipo: 'movie' o 'tv'. Devuelve una lista corta de resultados."""
    d = _get(f"/search/{tipo}", query=query)
    return d.get("results", [])[:8]


def detalle(tmdb_id, tipo="movie"):
    return _get(f"/{tipo}/{tmdb_id}", append_to_response="credits")


def poster_url(path):
    return f"{IMG_BASE}{path}" if path else None


def descargar_poster(path, destino):
    url = poster_url(path)
    if not url:
        return None
    r = requests.get(url, timeout=30)
    if r.ok and r.content:
        with open(destino, "wb") as f:
            f.write(r.content)
        return destino
    return None


def resumen(d, tipo="movie"):
    """Extrae los campos que nos interesan de /movie/{id} o /tv/{id}."""
    titulo = d.get("title") or d.get("name") or ""
    fecha = d.get("release_date") or d.get("first_air_date") or ""
    ano = fecha[:4] if fecha else None
    paises = d.get("production_countries") or []
    pais = PAISES_ES.get(paises[0]["iso_3166_1"], paises[0]["name"]) if paises else None
    generos = [g["name"] for g in d.get("genres") or []]
    credits = d.get("credits") or {}
    director = next((c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"), None)
    reparto = [c["name"] for c in credits.get("cast", [])[:6]]
    return {
        "nombre": titulo, "descripcion": d.get("overview") or "",
        "ano": ano, "pais": pais, "generos": generos,
        "director": director, "reparto": reparto,
        "poster_path": d.get("poster_path"),
    }

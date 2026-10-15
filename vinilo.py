"""
vinilo.py — búsqueda de fotos de la etiqueta real y armado del vinilo.

- buscar_etiquetas_caa(): fotos del disco/etiqueta (tipo "Medium") en Cover Art Archive.
- buscar_fotos_discogs(): fotos secundarias de las ediciones en vinilo en Discogs.
- armar_vinilo(): genera el PNG del vinilo (disco con surcos + etiqueta real).
"""

import io
import math
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter

UA = "TapasHD/1.0 (vinilos decorativos)"

# Proporciones reales de un LP de 12" (302 mm)
R_ETIQUETA = 50.0 / 151.0   # etiqueta: 100 mm de diámetro
R_AGUJERO = 3.6 / 151.0     # agujero central: 7,2 mm
R_SURCOS_INI = 0.40         # donde terminan los surcos (zona lisa hacia la etiqueta)
R_SURCOS_FIN = 0.965        # donde empiezan los surcos (borde exterior)


# ------------------------------------------------------------------ búsqueda

def titulo_limpio(album: str) -> str:
    """Saca agregados tipo '(Deluxe)', '[Remastered]', ' - EP'."""
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", album)
    t = re.sub(r"\s+-\s+(EP|Single)$", "", t, flags=re.I)
    return t.strip() or album


def _caa_release(mbid: str):
    try:
        r = requests.get(f"https://coverartarchive.org/release/{mbid}",
                         headers={"User-Agent": UA}, timeout=15)
        if r.status_code != 200:
            return []
        return r.json().get("images", [])
    except (requests.RequestException, ValueError):
        return []


def buscar_etiquetas_caa(artista: str, album: str, max_releases: int = 12):
    """Devuelve [{preview, full, origen}] con fotos del disco (tipo Medium)."""
    titulo = titulo_limpio(album).replace('"', "")
    artista = artista.replace('"', "")
    releases = []
    for consulta in (f'release:"{titulo}" AND artist:"{artista}" AND format:vinyl',
                     f'release:"{titulo}" AND artist:"{artista}"'):
        try:
            r = requests.get("https://musicbrainz.org/ws/2/release/",
                             headers={"User-Agent": UA}, timeout=15,
                             params={"query": consulta, "fmt": "json", "limit": 25})
            r.raise_for_status()
            releases = r.json().get("releases", [])
        except (requests.RequestException, ValueError):
            releases = []
        if releases:
            break

    # primero las ediciones en vinilo
    def es_vinilo(rel):
        return any("vinyl" in (m.get("format") or "").lower() for m in rel.get("media", []))
    releases.sort(key=lambda rel: not es_vinilo(rel))
    releases = releases[:max_releases]

    with ThreadPoolExecutor(max_workers=6) as ex:
        imagenes_por_release = list(ex.map(lambda rel: _caa_release(rel["id"]), releases))

    salida, vistos = [], set()
    for rel, imagenes in zip(releases, imagenes_por_release):
        for img in imagenes:
            if "Medium" not in img.get("types", []):
                continue
            full = img.get("image", "").replace("http://", "https://")
            if not full or full in vistos:
                continue
            vistos.add(full)
            th = img.get("thumbnails", {})
            preview = (th.get("500") or th.get("large") or full).replace("http://", "https://")
            trabajo = (th.get("1200") or full).replace("http://", "https://")
            pais = rel.get("country") or "?"
            anio = (rel.get("date") or "????")[:4]
            salida.append({"preview": preview, "full": full, "trabajo": trabajo,
                           "origen": f"Cover Art Archive · {pais} {anio}"})
    return salida


def buscar_fotos_discogs(artista: str, album: str, token: str, max_releases: int = 4):
    """Fotos secundarias (contratapa, etiquetas, disco) de ediciones en vinilo."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Authorization": f"Discogs token={token}"})
    try:
        r = s.get("https://api.discogs.com/database/search", timeout=15, params={
            "artist": artista, "release_title": titulo_limpio(album),
            "format": "Vinyl", "type": "release", "per_page": max_releases,
        })
        r.raise_for_status()
        resultados = r.json().get("results", [])[:max_releases]
    except (requests.RequestException, ValueError):
        return []

    def detalle(res):
        try:
            d = s.get(f"https://api.discogs.com/releases/{res['id']}", timeout=15)
            d.raise_for_status()
            return d.json()
        except (requests.RequestException, ValueError):
            return {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        detalles = list(ex.map(detalle, resultados))

    salida = []
    for det in detalles:
        pais = det.get("country") or "?"
        anio = det.get("year") or "?"
        for img in det.get("images") or []:
            if img.get("type") != "secondary" or not img.get("uri"):
                continue
            salida.append({"preview": img.get("uri150") or img["uri"], "full": img["uri"],
                           "trabajo": img["uri"], "origen": f"Discogs · {pais} {anio}"})
    return salida


def bajar_imagen(url: str) -> Image.Image:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


# ------------------------------------------------------------------ recorte

def marcar_recorte(img: Image.Image, cx: float, cy: float, radio: float, ancho=600):
    """Vista previa de la foto con el círculo de recorte dibujado encima."""
    escala = ancho / img.width
    vista = img.resize((ancho, max(1, round(img.height * escala))), Image.LANCZOS)
    oscuro = Image.new("RGB", vista.size, (0, 0, 0))
    mascara = Image.new("L", vista.size, 150)
    r_px = radio * min(vista.size)
    x, y = cx * vista.width, cy * vista.height
    ImageDraw.Draw(mascara).ellipse((x - r_px, y - r_px, x + r_px, y + r_px), fill=0)
    vista = Image.composite(oscuro, vista, mascara)
    d = ImageDraw.Draw(vista)
    d.ellipse((x - r_px, y - r_px, x + r_px, y + r_px), outline=(255, 70, 70), width=3)
    d.line((x - 8, y, x + 8, y), fill=(255, 70, 70), width=2)
    d.line((x, y - 8, x, y + 8), fill=(255, 70, 70), width=2)
    return vista


def recortar_etiqueta(img: Image.Image, cx: float, cy: float, radio: float, diametro: int):
    r_px = radio * min(img.size)
    x, y = cx * img.width, cy * img.height
    caja = (round(x - r_px), round(y - r_px), round(x + r_px), round(y + r_px))
    # crop rellena con negro si el círculo se sale de la foto
    recorte = img.crop(caja).resize((diametro, diametro), Image.LANCZOS)
    return recorte


# ------------------------------------------------------------------ render

def _hex_a_rgb(h: str):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def armar_vinilo(etiqueta: Image.Image | None, color_hex: str = "#111111",
                 tamano: int = 3600, translucido: bool = False,
                 pistas_por_lado: int = 5, angulo_brillo: float = 35.0,
                 semilla: int = 7) -> Image.Image:
    """Genera el vinilo como PNG RGBA con fondo transparente."""
    S = tamano
    rng = np.random.default_rng(semilla)
    c = (S - 1) / 2.0
    R = S / 2.0

    # ruido por anillo (cada radio tiene su brillo) y separaciones entre temas
    n_bins = int(R) + 2
    ruido = rng.normal(0, 1, n_bins).astype(np.float32)
    ruido = np.convolve(ruido, np.ones(3, dtype=np.float32) / 3, mode="same")
    gaps = []
    if pistas_por_lado > 1:
        ancho_banda = (R_SURCOS_FIN - R_SURCOS_INI) / pistas_por_lado
        for k in range(1, pistas_por_lado):
            gaps.append(R_SURCOS_FIN - k * ancho_banda * (0.85 + 0.3 * rng.random()))

    base = _hex_a_rgb(color_hex)
    oscuridad = base.mean() / 255.0
    var = 0.05 + 0.05 * (1 - oscuridad)          # en discos oscuros, surcos más marcados
    luz = 70 + 60 * (1 - oscuridad)
    phi = math.radians(angulo_brillo)
    opacidad = 0.80 if translucido else 1.0

    salida = np.empty((S, S, 4), dtype=np.uint8)
    xx = (np.arange(S, dtype=np.float32) - c)[None, :]
    franja = 400                                  # se procesa por franjas para no usar tanta RAM
    for y0 in range(0, S, franja):
        y1 = min(S, y0 + franja)
        dy = (np.arange(y0, y1, dtype=np.float32) - c)[:, None]
        rpx = np.hypot(xx, dy)
        r = rpx / R
        theta = np.arctan2(dy, xx)
        anillos = ruido[np.clip(rpx.astype(np.int32), 0, n_bins - 1)]

        en_surcos = (r >= R_SURCOS_INI) & (r <= R_SURCOS_FIN)
        separacion = np.zeros_like(en_surcos)
        for pos in gaps:
            separacion |= np.abs(r - pos) < 0.0028
        textura = np.where(en_surcos & ~separacion, anillos, anillos * 0.25)

        # brillo anisotrópico: los dos "abanicos" de luz típicos del vinilo
        abanico = np.clip(np.cos(2 * (theta - phi)), 0, 1) ** 10
        brillo = abanico * np.where(en_surcos, 0.55 + 0.12 * anillos, 0.25)
        brillo = np.where(separacion, abanico * 0.75, brillo)

        borde = np.clip((r - 0.985) / 0.015, 0, 1)
        rgb = base[None, None, :] * (1 + var * textura[..., None])
        rgb += brillo[..., None] * luz
        rgb = rgb * (1 - 0.35 * borde[..., None]) + 25 * borde[..., None]
        salida[y0:y1, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)

        alfa = np.clip(R - rpx, 0, 1) * np.clip(rpx - R_AGUJERO * R, 0, 1)
        salida[y0:y1, :, 3] = (alfa * 255 * opacidad).astype(np.uint8)

    disco = Image.fromarray(salida, "RGBA")
    del salida

    # etiqueta
    diam = round(2 * R_ETIQUETA * R)
    if etiqueta is None:
        lbl = Image.new("RGB", (diam, diam), (235, 232, 225))
    else:
        lbl = etiqueta.convert("RGB").resize((diam, diam), Image.LANCZOS)
    mascara = Image.new("L", (diam * 4, diam * 4), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, diam * 4 - 1, diam * 4 - 1), fill=255)
    mascara = mascara.resize((diam, diam), Image.LANCZOS)
    pos = (round(c - diam / 2 + 0.5), round(c - diam / 2 + 0.5))

    # sombra suave alrededor de la etiqueta
    sombra = Image.new("RGBA", disco.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).ellipse((pos[0] - 2, pos[1] - 2, pos[0] + diam + 2, pos[1] + diam + 2),
                                   fill=(0, 0, 0, 90))
    sombra = sombra.filter(ImageFilter.GaussianBlur(max(2, S // 900)))
    disco = Image.alpha_composite(disco, sombra)
    capa = Image.new("RGBA", disco.size, (0, 0, 0, 0))
    capa.paste(lbl.convert("RGBA"), pos, mascara)
    disco = Image.alpha_composite(disco, capa)

    # re-perforar el agujero central (la foto de la etiqueta puede taparlo)
    agujero = Image.new("L", disco.size, 255)
    ra = R_AGUJERO * R
    ImageDraw.Draw(agujero).ellipse((c - ra, c - ra, c + ra, c + ra), fill=0)
    a = np.minimum(np.asarray(disco.getchannel("A")), np.asarray(agujero))
    disco.putalpha(Image.fromarray(a))
    return disco


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(300, 300), optimize=False)
    return buf.getvalue()

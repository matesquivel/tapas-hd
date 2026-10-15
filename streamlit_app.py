"""
Tapas HD — versión web.

1. Escribís el artista y elegís de la lista que se autocompleta.
2. Aparece su discografía; podés filtrar por nombre de álbum mientras escribís.
3. Bajás una tapa suelta desde su tarjeta, o tildás varias y las bajás en ZIP
   desde la barra fija de arriba.
"""

import io
import re
import zipfile
from urllib.parse import quote_plus

import math

import requests
import streamlit as st
from PIL import Image

import vinilo

try:
    from streamlit_searchbox import st_searchbox
except ImportError:  # si el componente no está instalado, se usa un buscador simple
    st_searchbox = None

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
TIENDAS = {"Estados Unidos": "US", "Argentina": "AR", "España": "ES",
           "México": "MX", "Reino Unido": "GB", "Brasil": "BR"}
HEADERS = {"User-Agent": "VinilosDecorados/1.0"}

st.set_page_config(page_title="Tapas HD", page_icon="💿", layout="wide")

st.markdown("""
<style>
/* Barra de acciones fija arriba mientras se scrollea */
.st-key-barra {
    position: sticky; top: 3.75rem; z-index: 999;
    padding: .6rem .8rem; border-radius: .6rem;
    background: rgba(255,255,255,.92); backdrop-filter: blur(8px);
    border: 1px solid rgba(128,128,128,.25);
}
@media (prefers-color-scheme: dark) {
    .st-key-barra { background: rgba(14,17,23,.92); }
}
.tapa-meta { font-size: .8rem; opacity: .7; margin-top: -.6rem; }
</style>
""", unsafe_allow_html=True)

# ---------------- datos (iTunes) ----------------

def slug(texto: str) -> str:
    texto = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "_", texto)

def con_tamano(url_100: str, tamano: str) -> str:
    return re.sub(r"\d+x\d+bb", f"{tamano}bb", url_100)

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def sugerir_artistas(termino: str, pais: str):
    """Devuelve [(nombre, (id, nombre)), ...] para el autocompletado."""
    try:
        r = requests.get(SEARCH_URL, headers=HEADERS, timeout=10, params={
            "term": termino, "entity": "musicArtist",
            "media": "music", "country": pais, "limit": 8,
        })
        r.raise_for_status()
    except requests.RequestException:
        return []
    vistos, salida = set(), []
    for a in r.json().get("results", []):
        if a.get("artistId") in vistos:
            continue
        vistos.add(a["artistId"])
        genero = a.get("primaryGenreName")
        etiqueta = f"{a['artistName']}  ·  {genero}" if genero else a["artistName"]
        salida.append((etiqueta, (a["artistId"], a["artistName"])))
    return salida

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def discografia(artist_id: int, nombre: str, pais: str, incluir_todo: bool):
    r = requests.get(LOOKUP_URL, headers=HEADERS, timeout=20, params={
        "id": artist_id, "entity": "album", "country": pais, "limit": 200,
    })
    r.raise_for_status()
    albumes = [x for x in r.json().get("results", [])
               if x.get("wrapperType") == "collection" and x.get("artworkUrl100")]
    if not incluir_todo:
        albumes = [a for a in albumes
                   if not a.get("collectionName", "").rstrip().endswith("- Single")
                   and a.get("artistName", "").strip().lower() == nombre.strip().lower()]
    vistos, unicos = set(), []
    for a in sorted(albumes, key=lambda x: x.get("releaseDate", ""), reverse=True):
        clave = (a.get("collectionName", "").lower(), a.get("releaseDate", "")[:4])
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(a)
    return unicos

def bajar_hd(url_100: str):
    for tamano in ("3000x3000", "1500x1500"):
        try:
            r = requests.get(con_tamano(url_100, tamano), headers=HEADERS, timeout=60)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            return r.content, tamano
    return None, None

def nombre_archivo(res: dict) -> str:
    anio = (res.get("releaseDate") or "????")[:4]
    return f"{slug(res.get('artistName', ''))}__{slug(res.get('collectionName', ''))}__{anio}.jpg"

def link_discogs(res: dict) -> str:
    q = quote_plus(f"{res.get('artistName', '')} {res.get('collectionName', '')}")
    return f"https://www.discogs.com/search/?q={q}&type=release&format=Vinyl"

# ---------------- encabezado y búsqueda ----------------

st.title("💿 Tapas HD")
st.caption("Elegí un artista, filtrá sus discos y bajá las tapas en 3000×3000.")

c_tienda, c_singles = st.columns([1, 2])
tienda = c_tienda.selectbox("Tienda", list(TIENDAS.keys()),
                            help="Si no aparece un artista o disco, probá con otra tienda.")
pais = TIENDAS[tienda]
incluir_todo = c_singles.checkbox("Incluir singles y colaboraciones", value=False)

artista_elegido = None
if st_searchbox is not None:
    def _buscar(termino: str):
        if not termino or len(termino.strip()) < 2:
            return []
        return sugerir_artistas(termino.strip(), pais)

    kwargs = dict(placeholder="Empezá a escribir un artista…", label="Artista",
                  key=f"artista_{pais}")
    try:
        artista_elegido = st_searchbox(_buscar, debounce=350, **kwargs)
    except TypeError:  # versiones viejas del componente sin 'debounce'
        artista_elegido = st_searchbox(_buscar, **kwargs)
else:
    texto = st.text_input("Artista", placeholder="Ej: Mac Miller")
    if texto.strip():
        opciones = sugerir_artistas(texto.strip(), pais)
        if opciones:
            etiqueta = st.radio("¿Cuál de estos?", [o[0] for o in opciones], horizontal=True)
            artista_elegido = dict(opciones)[etiqueta]
        else:
            st.info("No encontré ese artista. Probá con otra tienda.")

if not artista_elegido:
    st.info("👆 Escribí el nombre de un artista y elegilo de la lista.")
    st.stop()

artist_id, nombre_artista = artista_elegido

with st.spinner(f"Cargando discografía de {nombre_artista}…"):
    try:
        discos = discografia(artist_id, nombre_artista, pais, incluir_todo)
    except requests.RequestException:
        st.error("Hubo un problema hablando con iTunes. Esperá un minuto y recargá.")
        st.stop()

if not discos:
    st.info("No hay discos para este artista en esta tienda. Probá con otra tienda "
            "o tildá «Incluir singles y colaboraciones».")
    st.stop()

filtro = st.text_input("Filtrar por álbum", placeholder="Escribí parte del nombre…",
                       key=f"filtro_{artist_id}")
visibles = [d for d in discos
            if filtro.strip().lower() in d.get("collectionName", "").lower()]

# ---------------- barra fija de acciones ----------------

seleccionadas = [d for d in discos if st.session_state.get(f"sel_{d['collectionId']}")]
ids_sel = tuple(d["collectionId"] for d in seleccionadas)

with st.container(key="barra"):
    b1, b2, b3, b4 = st.columns([2, 1, 1, 2], vertical_alignment="center")
    b1.markdown(f"**{nombre_artista}** · {len(visibles)} discos · "
                f"**{len(seleccionadas)} tildadas**")
    if b2.button("Tildar visibles", use_container_width=True):
        for d in visibles:
            st.session_state[f"sel_{d['collectionId']}"] = True
        st.rerun()
    if b3.button("Destildar", use_container_width=True, disabled=not seleccionadas):
        for d in discos:
            st.session_state[f"sel_{d['collectionId']}"] = False
        st.rerun()

    zip_listo = st.session_state.get("zip")
    if zip_listo and zip_listo["ids"] == ids_sel:
        b4.download_button(f"💾 Guardar ZIP ({len(ids_sel)})", data=zip_listo["datos"],
                           file_name=zip_listo["nombre"], mime="application/zip",
                           use_container_width=True, type="primary")
    elif b4.button(f"⬇️ Descargar tildadas ({len(seleccionadas)})", type="primary",
                   use_container_width=True, disabled=not seleccionadas):
        barra = st.progress(0.0, text="Bajando tapas en alta resolución…")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for n, d in enumerate(seleccionadas, 1):
                datos, _ = bajar_hd(d["artworkUrl100"])
                if datos:
                    z.writestr(nombre_archivo(d), datos)
                barra.progress(n / len(seleccionadas))
        st.session_state.zip = {"ids": ids_sel, "datos": buf.getvalue(),
                                "nombre": f"{slug(nombre_artista)}_tapas_hd.zip"}
        st.rerun()

# ---------------- grilla de tapas ----------------

@st.fragment
def boton_individual(d: dict):
    """Descarga una sola tapa sin recargar toda la página."""
    cid = d["collectionId"]
    clave = f"hd_{cid}"
    if clave in st.session_state:
        nombre, datos, tamano = st.session_state[clave]
        st.download_button(f"💾 Guardar ({tamano.split('x')[0]}px)", data=datos,
                           file_name=nombre, mime="image/jpeg",
                           key=f"dl_{cid}", use_container_width=True)
    elif st.button("⬇️ Bajar esta", key=f"prep_{cid}", use_container_width=True):
        with st.spinner("Bajando…"):
            datos, tamano = bajar_hd(d["artworkUrl100"])
        if datos:
            st.session_state[clave] = (nombre_archivo(d), datos, tamano)
            st.rerun(scope="fragment")
        else:
            st.error("No se pudo bajar.")

COLORES = {"Negro": "#111111", "Rojo": "#b3122e", "Azul": "#1f4fbf", "Verde": "#1f8a4c",
           "Amarillo": "#e8c21a", "Naranja": "#e06a1a", "Violeta": "#6b2fa8",
           "Rosa": "#e58bb5", "Blanco": "#e8e6e0", "Otro…": None}


def discogs_token():
    try:
        return st.secrets.get("DISCOGS_TOKEN")
    except Exception:
        return None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fotos_etiqueta(artista: str, album: str, usar_discogs: bool):
    fotos = vinilo.buscar_etiquetas_caa(artista, album)
    token = discogs_token() if usar_discogs else None
    if token:
        fotos += vinilo.buscar_fotos_discogs(artista, album, token)
    return fotos


@st.cache_data(ttl=3600, show_spinner=False, max_entries=20)
def imagen_bytes(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": vinilo.UA}, timeout=60)
    r.raise_for_status()
    return r.content


def abrir(datos: bytes) -> Image.Image:
    return Image.open(io.BytesIO(datos)).convert("RGB")


@st.dialog("💿 Armar vinilo", width="large")
def dialogo_vinilo(d: dict):
    cid = d["collectionId"]
    artista, album = d.get("artistName", ""), d.get("collectionName", "")
    st.markdown(f"**{artista} — {album}**")
    clave_foto = f"vin_foto_{cid}"

    # 1) de dónde sale la etiqueta
    fuente = st.radio("Etiqueta", ["Buscar la etiqueta real", "Subir una foto mía",
                                   "Sin foto (etiqueta lisa)"],
                      horizontal=True, key=f"vin_fuente_{cid}")
    foto_preview = foto_final = None

    if fuente == "Buscar la etiqueta real":
        with st.spinner("Buscando fotos del disco en Cover Art Archive y Discogs…"):
            fotos = fotos_etiqueta(artista, album, True)
        if not fotos:
            st.info("No encontré fotos de la etiqueta de este disco. Probá con "
                    "«Subir una foto mía» (por ejemplo, una que encuentres en Google o Discogs)."
                    + ("" if discogs_token() else " Configurar el token de Discogs suma muchas más fotos."))
        else:
            st.caption(f"{len(fotos)} fotos encontradas. Elegí la que muestre la etiqueta:")
            cols = st.columns(5)
            for i, f in enumerate(fotos[:20]):
                with cols[i % 5]:
                    st.image(f["preview"], use_container_width=True)
                    elegida = st.session_state.get(clave_foto) == f["full"]
                    if st.button("✓ Elegida" if elegida else "Usar", key=f"vin_usar_{cid}_{i}",
                                 use_container_width=True, type="primary" if elegida else "secondary",
                                 help=f["origen"]):
                        st.session_state[clave_foto] = f["full"]
                        st.session_state[f"vin_trabajo_{cid}"] = f["trabajo"]
                        st.rerun(scope="fragment")
            if st.session_state.get(clave_foto):
                try:
                    foto_preview = abrir(imagen_bytes(st.session_state[f"vin_trabajo_{cid}"]))
                    foto_final = st.session_state[clave_foto]
                except Exception:
                    st.error("No pude bajar esa foto, probá con otra.")
    elif fuente == "Subir una foto mía":
        subida = st.file_uploader("Foto de la etiqueta o del disco", type=["jpg", "jpeg", "png", "webp"],
                                  key=f"vin_subida_{cid}")
        if subida:
            foto_preview = abrir(subida.getvalue())
            foto_final = subida.getvalue()

    # 2) recorte de la etiqueta
    cx = cy = 0.5
    radio = 0.5
    if foto_preview is not None:
        st.divider()
        tipo = st.radio("La foto muestra…", ["Solo la etiqueta", "El disco entero"],
                        horizontal=True, key=f"vin_tipo_{cid}",
                        help="Ajustá el círculo rojo para que encierre justo la etiqueta.")
        defecto = 0.5 if tipo == "Solo la etiqueta" else 0.165
        s1, s2, s3 = st.columns(3)
        radio = s1.slider("Tamaño del círculo", 0.05, 0.6, defecto, 0.005, key=f"vin_r_{cid}_{tipo}")
        cx = s2.slider("Mover ↔", 0.0, 1.0, 0.5, 0.005, key=f"vin_cx_{cid}")
        cy = s3.slider("Mover ↕", 0.0, 1.0, 0.5, 0.005, key=f"vin_cy_{cid}")

    # 3) color del disco
    st.divider()
    k1, k2, k3 = st.columns([2, 1, 1], vertical_alignment="bottom")
    nombre_color = k1.selectbox("Color del vinilo", list(COLORES.keys()), key=f"vin_color_{cid}")
    color = COLORES[nombre_color] or k2.color_picker("Elegí", "#7a1f1f", key=f"vin_picker_{cid}")
    translucido = k3.checkbox("Translúcido", key=f"vin_transl_{cid}")
    pistas = max(1, min(10, math.ceil((d.get("trackCount") or 10) / 2)))

    etiqueta_prev = (vinilo.recortar_etiqueta(foto_preview, cx, cy, radio, 300)
                     if foto_preview is not None else None)
    v1, v2 = st.columns(2)
    if foto_preview is not None:
        v1.image(vinilo.marcar_recorte(foto_preview, cx, cy, radio), caption="Recorte",
                 use_container_width=True)
    v2.image(vinilo.armar_vinilo(etiqueta_prev, color, 700, translucido, pistas),
             caption="Vista previa", use_container_width=True)

    # 4) archivo final
    clave_png = f"vin_png_{cid}"
    firma = (foto_final if isinstance(foto_final, str) else len(foto_final or b""),
             round(cx, 3), round(cy, 3), round(radio, 3), color, translucido)
    if st.session_state.get(clave_png, {}).get("firma") != firma:
        if st.button("🖨️ Generar vinilo para imprimir (3600 px, 30 cm a 300 dpi)",
                     type="primary", use_container_width=True, key=f"vin_gen_{cid}"):
            with st.spinner("Armando el vinilo en alta resolución…"):
                etiqueta = None
                if foto_final is not None:
                    try:
                        datos = foto_final if isinstance(foto_final, bytes) else imagen_bytes(foto_final)
                        original = abrir(datos)
                    except Exception:
                        original = foto_preview  # si el original no baja, uso la versión de trabajo
                    diam = round(2 * vinilo.R_ETIQUETA * 1800)
                    etiqueta = vinilo.recortar_etiqueta(original, cx, cy, radio, diam)
                png = vinilo.png_bytes(vinilo.armar_vinilo(etiqueta, color, 3600, translucido, pistas))
            st.session_state[clave_png] = {"firma": firma, "png": png}
            st.rerun(scope="fragment")
    else:
        nombre = f"{slug(artista)}__{slug(album)}__vinilo.png"
        kwargs = dict(label="💾 Guardar vinilo PNG", data=st.session_state[clave_png]["png"],
                      file_name=nombre, mime="image/png", type="primary",
                      use_container_width=True, key=f"vin_dl_{cid}")
        try:
            st.download_button(on_click="ignore", **kwargs)
        except TypeError:
            st.download_button(**kwargs)


if not visibles:
    st.info("Ningún disco coincide con el filtro.")

columnas = st.columns(4)
for i, d in enumerate(visibles):
    with columnas[i % 4]:
        with st.container(border=True):
            st.image(con_tamano(d["artworkUrl100"], "600x600"), use_container_width=True)
            anio = (d.get("releaseDate") or "")[:4]
            st.checkbox(d.get("collectionName", ""), key=f"sel_{d['collectionId']}")
            pistas = d.get("trackCount")
            meta = f"{anio}" + (f" · {pistas} temas" if pistas else "")
            st.markdown(f"<div class='tapa-meta'>{meta} · "
                        f"<a href='{link_discogs(d)}' target='_blank'>ver vinilo en Discogs ↗</a>"
                        f"</div>", unsafe_allow_html=True)
            boton_individual(d)
            if st.button("💿 Armar vinilo", key=f"vin_{d['collectionId']}",
                         use_container_width=True):
                dialogo_vinilo(d)

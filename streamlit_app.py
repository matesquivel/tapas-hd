"""
Tapas HD — versión web.
Buscás un artista (y opcionalmente un álbum), tildás las tapas que querés
y las descargás en 3000x3000 (una suelta o varias en un ZIP).
"""

import io
import re
import zipfile

import requests
import streamlit as st

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
TIENDAS = {"Argentina": "AR", "Estados Unidos": "US", "España": "ES",
           "México": "MX", "Reino Unido": "GB", "Brasil": "BR"}
HEADERS = {"User-Agent": "VinilosDecorados/1.0"}

st.set_page_config(page_title="Tapas HD", page_icon="💿", layout="wide")

# ---------------- lógica ----------------

def slug(texto: str) -> str:
    texto = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "_", texto)

def con_tamano(url_100: str, tamano: str) -> str:
    return re.sub(r"\d+x\d+bb", f"{tamano}bb", url_100)

@st.cache_data(ttl=3600, show_spinner=False)
def buscar_album(artista: str, album: str, pais: str):
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=20, params={
        "term": f"{artista} {album}", "entity": "album",
        "media": "music", "country": pais, "limit": 12,
    })
    r.raise_for_status()
    return r.json().get("results", [])

@st.cache_data(ttl=3600, show_spinner=False)
def buscar_discografia(artista: str, pais: str, incluir_todo: bool):
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=20, params={
        "term": artista, "entity": "musicArtist",
        "media": "music", "country": pais, "limit": 1,
    })
    r.raise_for_status()
    encontrados = r.json().get("results", [])
    if not encontrados:
        return None, []
    artist_id = encontrados[0]["artistId"]
    nombre_real = encontrados[0]["artistName"]

    r = requests.get(LOOKUP_URL, headers=HEADERS, timeout=20, params={
        "id": artist_id, "entity": "album", "country": pais, "limit": 200,
    })
    r.raise_for_status()
    albumes = [x for x in r.json().get("results", [])
               if x.get("wrapperType") == "collection"]
    if not incluir_todo:
        albumes = [a for a in albumes
                   if not a.get("collectionName", "").rstrip().endswith("- Single")
                   and a.get("artistName", "").strip().lower() == nombre_real.strip().lower()]
    # sin duplicados, más nuevos primero
    vistos, unicos = set(), []
    for a in sorted(albumes, key=lambda x: x.get("releaseDate", ""), reverse=True):
        clave = (a.get("collectionName", "").lower(), a.get("releaseDate", "")[:4])
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(a)
    return nombre_real, unicos

def bajar_hd(url_100: str) -> bytes | None:
    for tamano in ("3000x3000", "1500x1500"):
        r = requests.get(con_tamano(url_100, tamano), headers=HEADERS, timeout=60)
        if r.status_code == 200:
            return r.content
    return None

def nombre_archivo(res: dict) -> str:
    anio = (res.get("releaseDate") or "????")[:4]
    return f"{slug(res.get('artistName', ''))}__{slug(res.get('collectionName', ''))}__{anio}.jpg"

# ---------------- interfaz ----------------

st.title("💿 Tapas HD")
st.caption("Buscá un artista, tildá las tapas que quieras y descargalas en alta resolución (3000×3000).")

with st.form("busqueda"):
    c1, c2 = st.columns(2)
    artista = c1.text_input("Artista", placeholder="Ej: Mac Miller")
    album = c2.text_input("Álbum (opcional)", placeholder="Vacío = toda la discografía")
    c3, c4 = st.columns(2)
    tienda = c3.selectbox("Tienda", list(TIENDAS.keys()), index=1,
                          help="Si no aparece un disco, probá con otra tienda.")
    incluir_todo = c4.checkbox("Incluir singles y colaboraciones", value=False)
    buscar = st.form_submit_button("🔎 Buscar", use_container_width=True, type="primary")

if buscar:
    if not artista.strip():
        st.warning("Escribí el nombre de un artista.")
    else:
        st.session_state.pop("zip", None)
        for k in [k for k in st.session_state if str(k).startswith("sel_")]:
            del st.session_state[k]
        with st.spinner("Buscando..."):
            try:
                pais = TIENDAS[tienda]
                if album.strip():
                    st.session_state.resultados = buscar_album(artista.strip(), album.strip(), pais)
                    st.session_state.titulo = f"Resultados para “{artista} — {album}”"
                else:
                    nombre, res = buscar_discografia(artista.strip(), pais, incluir_todo)
                    st.session_state.resultados = res
                    st.session_state.titulo = (f"Discografía de {nombre}" if nombre
                                               else "No encontré ese artista")
            except requests.RequestException:
                st.session_state.resultados = []
                st.session_state.titulo = "Hubo un problema con la búsqueda, probá de nuevo en un rato."

resultados = st.session_state.get("resultados")
if resultados is not None:
    st.subheader(st.session_state.get("titulo", ""))
    if not resultados:
        st.info("Sin resultados. Probá con otra tienda o revisá cómo está escrito.")
    else:
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("Tildar todas"):
            for i in range(len(resultados)):
                st.session_state[f"sel_{i}"] = True
        if b2.button("Destildar todas"):
            for i in range(len(resultados)):
                st.session_state[f"sel_{i}"] = False

        columnas = st.columns(4)
        for i, res in enumerate(resultados):
            with columnas[i % 4]:
                art = res.get("artworkUrl100")
                if art:
                    st.image(con_tamano(art, "600x600"), use_container_width=True)
                anio = (res.get("releaseDate") or "")[:4]
                st.checkbox(f"{res.get('collectionName', '')} ({anio})", key=f"sel_{i}")

        elegidas = [r for i, r in enumerate(resultados)
                    if st.session_state.get(f"sel_{i}") and r.get("artworkUrl100")]

        st.divider()
        if not elegidas:
            st.caption("Tildá una o más tapas para descargarlas.")
        elif st.button(f"⬇️ Preparar descarga ({len(elegidas)})", type="primary"):
            barra = st.progress(0.0, text="Descargando tapas en alta resolución...")
            archivos = []
            for n, res in enumerate(elegidas, 1):
                datos = bajar_hd(res["artworkUrl100"])
                if datos:
                    archivos.append((nombre_archivo(res), datos))
                barra.progress(n / len(elegidas))
            barra.empty()
            if len(archivos) == 1:
                st.session_state.zip = (archivos[0][0], archivos[0][1], "image/jpeg")
            elif archivos:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                    for nombre, datos in archivos:
                        z.writestr(nombre, datos)
                st.session_state.zip = ("tapas_hd.zip", buf.getvalue(), "application/zip")
            else:
                st.error("No se pudo descargar ninguna tapa.")

        if "zip" in st.session_state:
            nombre, datos, mime = st.session_state.zip
            st.download_button(f"💾 Guardar {nombre}", data=datos, file_name=nombre,
                               mime=mime, use_container_width=True)

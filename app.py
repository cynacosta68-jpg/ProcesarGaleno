import streamlit as st
import pandas as pd
import time
import io
import uuid
import threading
import unicodedata
from datetime import datetime, timedelta, date
from playwright.sync_api import sync_playwright

# ── Registro global de jobs ──────────────────────────────────────────────
# NUNCA usar st.session_state (los threads no pueden tocar el contexto de
# Streamlit). Tampoco un dict de nivel módulo plano: Streamlit re-ejecuta el
# script en cada rerun y eso lo reiniciaría a {}. Se inicializa más abajo con
# @st.cache_resource, que devuelve SIEMPRE el mismo objeto entre reruns y
# sesiones, y que los threads pueden mutar sin problema.


# ── Helpers de query params tolerantes a la versión de Streamlit ─────────
# st.query_params existe desde 1.30; en versiones previas se usa la API
# experimental; si no hay ninguna, degradan a no-op (solo session_state).
def qp_get(key):
    try:
        return st.query_params.get(key)
    except Exception:
        try:
            vals = st.experimental_get_query_params().get(key)
            return vals[0] if vals else None
        except Exception:
            return None


def qp_set(key, value):
    try:
        st.query_params[key] = value
    except Exception:
        try:
            st.experimental_set_query_params(**{key: value})
        except Exception:
            pass


def qp_del(key):
    try:
        if key in st.query_params:
            del st.query_params[key]
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


# ─────────────────────────────────────────────
#  CONFIGURACIÓN Y ESTILOS
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Auditoría Galeno",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Registro de jobs persistente entre reruns y sesiones ─────────────────
@st.cache_resource(show_spinner=False)
def _get_jobs_registry():
    return {}

_JOBS = _get_jobs_registry()


st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500&family=DM+Mono&display=swap');
  html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
  .stApp { background: #0d0d0d; color: #e8e6e1; }
  [data-testid="stSidebar"] { background: #111111; border-right: 1px solid #222; }
  [data-testid="stSidebar"] label,
  [data-testid="stSidebar"] .stMarkdown p { color: #999 !important; font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; }
  input, .stTextInput input, .stDateInput input {
    background: #1a1a1a !important; border: 1px solid #2a2a2a !important;
    color: #e8e6e1 !important; border-radius: 4px !important;
    font-family: 'DM Mono', monospace !important; font-size: 0.85rem !important;
  }
  input:focus { border-color: #c8a96e !important; box-shadow: none !important; }
  .stButton > button[kind="primary"] {
    background: #c8a96e !important; color: #0d0d0d !important;
    border: none !important; border-radius: 3px !important;
    font-weight: 500 !important; letter-spacing: 0.06em !important;
    padding: 0.55rem 1.6rem !important; transition: opacity 0.2s ease !important;
  }
  .stButton > button[kind="primary"]:hover { opacity: 0.85 !important; }
  .stDownloadButton > button {
    background: transparent !important; border: 1px solid #c8a96e !important;
    color: #c8a96e !important; border-radius: 3px !important;
    font-size: 0.82rem !important; letter-spacing: 0.06em !important;
  }
  .stDownloadButton > button:hover { background: #c8a96e22 !important; }
  .stProgress > div > div { background: #c8a96e !important; }
  hr { border-color: #1e1e1e !important; }
  h1 { font-size: 1.4rem !important; font-weight: 300 !important; letter-spacing: 0.12em; color: #c8a96e !important; }
  h3 { font-size: 0.82rem !important; font-weight: 400 !important; letter-spacing: 0.1em; color: #666 !important; text-transform: uppercase; }
  [data-testid="stFileUploader"] {
    border: 1px dashed #2a2a2a !important; border-radius: 4px !important;
    padding: 0.5rem !important; background: #111 !important;
  }
  .rango-chip {
    display: inline-block; background: #1a1a1a; border: 1px solid #2a2a2a;
    border-radius: 3px; padding: 2px 10px; font-family: 'DM Mono', monospace;
    font-size: 0.75rem; color: #c8a96e; margin: 2px 4px 2px 0;
  }
  .label-section { font-size: 0.72rem; letter-spacing: 0.1em; text-transform: uppercase; color: #555; margin-bottom: 6px; }
  .diag-box {
    background: #111; border: 1px solid #1e1e1e; border-radius: 4px;
    padding: 12px 16px; margin: 8px 0; font-family: 'DM Mono', monospace; font-size: 0.78rem; color: #888;
  }
  .diag-ok  { color: #6fcf97; }
  .diag-warn{ color: #f2c94c; }
  .diag-err { color: #eb5757; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
#  FUNCIONES AUXILIARES
# ─────────────────────────────────────────────

def normalizar_cadena(texto):
    if pd.isna(texto):
        return ""
    texto = str(texto).strip().lower()
    texto = "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )
    return " ".join(
        texto.replace("...", "").replace(",", " ").replace(".", " ").split()
    )


def limpiar_id(val):
    """Convierte cualquier formato de ID a string entero limpio: '160135941'
    Maneja: número, float, texto, texto con espacios, notación científica,
    apostrofe Excel, .0 al final, etc."""
    if pd.isna(val):
        return ""
    # Quitar apostrofe de Excel, espacios y caracteres no visibles
    s = str(val).strip().lstrip("'\"").strip()
    # Quitar .0 final si existe
    if s.endswith('.0'):
        s = s[:-2]
    # Intentar convertir a entero (maneja notación científica 1.6e8, etc.)
    try:
        return str(int(float(s)))
    except Exception:
        return s


def detectar_col_autorizacion(df):
    """
    Busca la columna de ID/autorización en el Excel de EVWEB.
    Prioridades: nroAutorizacion > autorizacion > transaccion > nro > id
    """
    prioridades = [
        'nroautorizacion', 'nro autorizacion', 'nro_autorizacion',
        'autorizacion', 'autoriz',
        'transaccion', 'transac',
        'nroaut', 'numero autorizacion',
        'nro', 'numero', 'id'
    ]
    cols_norm = {normalizar_cadena(c): c for c in df.columns}
    
    # Búsqueda exacta primero
    for pat in prioridades:
        if pat in cols_norm:
            return cols_norm[pat], "exacta"
    
    # Búsqueda por contenido
    for pat in prioridades:
        for norm_col, col_orig in cols_norm.items():
            if pat in norm_col:
                return col_orig, "parcial"
    
    # Fallback: primera columna numérica
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            return c, "fallback-numérico"
    
    return df.columns[0], "fallback-primera"


def detectar_col_profesional(df):
    """Busca la columna del nombre del profesional en EVWEB."""
    prioridades = [
        'prescriptor', 'efector', 'profesional',
        'medico', 'medico efector', 'nombre profesional',
        'nombre medico', 'nombre', 'apellido'
    ]
    cols_norm = {normalizar_cadena(c): c for c in df.columns}
    
    for pat in prioridades:
        if pat in cols_norm:
            return cols_norm[pat], "exacta"
    for pat in prioridades:
        for norm_col, col_orig in cols_norm.items():
            if pat in norm_col:
                return col_orig, "parcial"
    
    return df.columns[1] if len(df.columns) > 1 else df.columns[0], "fallback"


def buscar_coincidencia_medico(nombre_evweb, df_usuarios):
    """
    Cruce 2: nombre normalizado de EVWEB → fila en Usuarios.
    Estrategia: exacto → contenido → tokens principales.
    """
    nombre_norm = normalizar_cadena(nombre_evweb)
    if not nombre_norm or nombre_norm in ["revisar", ""]:
        return None

    # 1) Exacto
    exacto = df_usuarios[df_usuarios['nombre_norm'] == nombre_norm]
    if not exacto.empty:
        return exacto.iloc[0]

    # 2) Uno contiene al otro
    for _, u in df_usuarios.iterrows():
        n_u = u['nombre_norm']
        if nombre_norm in n_u or n_u in nombre_norm:
            return u

    # 3) Coincidencia por tokens: todos los tokens del nombre más corto
    #    deben estar presentes en el más largo
    tokens_evweb = set(nombre_norm.split())
    for _, u in df_usuarios.iterrows():
        tokens_u = set(u['nombre_norm'].split())
        # Al menos 2 tokens en común y el más corto está contenido
        comunes = tokens_evweb & tokens_u
        min_len = min(len(tokens_evweb), len(tokens_u))
        if min_len >= 2 and len(comunes) >= min_len:
            return u

    return None


def obtener_tarifa(cod_practica, categoria, df_vf, col_tarifa):
    """
    Busca el arancel en VF según código de práctica y categoría del médico.
    Jerarquía: Nomenclador GALENO > sin nomenclador > cualquier fila.
    Dentro de cada grupo: categoría exacta > VF.
    """
    cod_str = str(cod_practica).strip()
    cat_str = str(categoria).strip()

    base = df_vf[df_vf['Código'].astype(str).str.strip() == cod_str]
    if base.empty:
        return 0.0

    def _mejor(subset):
        por_cat = subset[subset['Arancel'].astype(str).str.strip() == cat_str]
        if not por_cat.empty:
            return float(por_cat.sort_values('Periodo', ascending=False).iloc[0][col_tarifa])
        vf = subset[subset['Arancel'].astype(str).str.strip() == 'VF']
        if not vf.empty:
            return float(vf.sort_values('Periodo', ascending=False).iloc[0][col_tarifa])
        return None

    galeno = base[base['Nomenclador'].astype(str).str.strip() == "Nomenclador GALENO"]
    if not galeno.empty:
        t = _mejor(galeno)
        if t is not None:
            return t

    vacio = base[base['Nomenclador'].isna() | (base['Nomenclador'].astype(str).str.strip() == "")]
    if not vacio.empty:
        t = _mejor(vacio)
        if t is not None:
            return t

    t = _mejor(base)
    return t if t is not None else float(base.sort_values('Periodo', ascending=False).iloc[0][col_tarifa])


def generar_rangos_9_dias(fecha_inicio: date, fecha_fin: date):
    rangos = []
    actual = fecha_inicio
    while actual <= fecha_fin:
        fin_tramo = min(actual + timedelta(days=8), fecha_fin)
        rangos.append((actual.strftime("%d/%m/%Y"), fin_tramo.strftime("%d/%m/%Y")))
        actual = fin_tramo + timedelta(days=1)
    return rangos


def obtener_iframe_activo(page):
    time.sleep(1)
    for frame in page.frames:
        try:
            if (frame.locator("#body_btnFiltro").count() > 0
                    or frame.locator("#body_btnBusquedaHide").count() > 0):
                return frame
        except Exception:
            pass
    return page.frames[1] if len(page.frames) > 1 else page.main_frame


# ─────────────────────────────────────────────
#  BOT DE EXTRACCIÓN
# ─────────────────────────────────────────────

def ejecutar_extractor(usuario, clave, modo_invisible, rangos, progreso_callback, filtro_estado='APOB', filtro_facturado='NO'):
    excels_descargados = []
    n_rangos = len(rangos)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=modo_invisible, args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        try:
            progreso_callback("Iniciando sesión en EVWEB…", 0.05)
            page.goto("https://cmsc.evweb.com.ar/Account/Login", timeout=60000)
            page.fill("input[name='UserName']", usuario)
            page.fill("#Password", clave)
            page.click("button[type='submit']")

            # Dar un respiro al redirect post-login y estabilizar la carga
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            time.sleep(2)

            progreso_callback("Sesión iniciada. Abriendo Facturación…", 0.08)

            # ── Navegar al menú Facturación → prestaciones ───────────────
            # La aparición del menú es la verdadera señal de login OK.
            try:
                page.locator("text=/Facturaci/i >> visible=true").first.wait_for(state="visible", timeout=30000)
                page.locator("text=/Facturaci/i >> visible=true").first.click()
            except Exception:
                url_actual = ""
                try:
                    url_actual = page.url
                except Exception:
                    pass
                if "Login" in url_actual:
                    # Realmente seguimos en login → credenciales
                    msg = ""
                    try:
                        err_loc = page.locator(".validation-summary-errors, .field-validation-error, .text-danger")
                        if err_loc.count() > 0:
                            msg = err_loc.first.inner_text().strip()
                    except Exception:
                        pass
                    detalle = f" El sitio respondió: '{msg}'." if msg else ""
                    raise RuntimeError(
                        "Login fallido: seguimos en la pantalla de acceso. "
                        f"Verificá usuario y contraseña de EVWEB.{detalle}"
                    )
                raise RuntimeError(
                    f"Login OK (URL: {url_actual or 'desconocida'}) pero NO apareció el menú "
                    "'Facturación' en 30s. El sitio pudo cambiar de estructura, estar lento, o "
                    "comportarse distinto en modo headless. Probá desmarcar 'Modo invisible' "
                    "para ver la página post-login y confirmar dónde está el menú."
                )
            try:
                page.locator("text=/prestaci/i >> visible=true").first.wait_for(state="visible", timeout=20000)
                page.locator("text=/prestaci/i >> visible=true").first.click()
            except Exception:
                raise RuntimeError(
                    "Apareció 'Facturación' pero no la opción 'prestaciones' en 20s. "
                    "El submenú pudo no desplegarse; revisá en modo no-headless."
                )
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            time.sleep(3)

            for idx, (desde, hasta) in enumerate(rangos):
                avance = 0.10 + (idx / n_rangos) * 0.65
                progreso_callback(f"Rango {idx+1}/{n_rangos}: {desde} → {hasta}", avance)

                if idx > 0:
                    page.reload(wait_until="domcontentloaded")
                    time.sleep(3)

                iframe = obtener_iframe_activo(page)

                iframe.locator("#body_btnBusquedaHide").wait_for(state="attached", timeout=15000)
                iframe.locator("#body_btnBusquedaHide").click(force=True)
                time.sleep(2)

                inp_obra = iframe.locator("#body_txtFiltroObraSocial")
                inp_obra.wait_for(state="visible", timeout=15000)
                inp_obra.click()
                inp_obra.clear()
                inp_obra.press_sequentially("10099", delay=150)
                time.sleep(2.5)
                iframe.locator(".ui-menu-item, .ui-autocomplete li").first.wait_for(state="visible", timeout=10000)
                iframe.locator(".ui-menu-item, .ui-autocomplete li").first.click()
                time.sleep(5)

                iframe = obtener_iframe_activo(page)
                iframe.locator("#body_cboEstados").wait_for(state="visible", timeout=15000)
                iframe.locator("#body_cboEstados").select_option(value=filtro_estado) if filtro_estado else None
                time.sleep(3)

                iframe = obtener_iframe_activo(page)
                iframe.locator("#body_cboFiltroFacturado").wait_for(state="visible", timeout=15000)
                iframe.locator("#body_cboFiltroFacturado").select_option(value=filtro_facturado) if filtro_facturado else None
                time.sleep(1.5)


                # ── Fechas dinámicas — triple método para headless ────────
                def set_fecha(locator, valor):
                    locator.wait_for(state="visible", timeout=10000)
                    # Método 1: seleccionar todo + type
                    locator.click(click_count=3)
                    locator.type(valor, delay=80)
                    time.sleep(0.3)
                    # Método 2: JS directo si no quedó bien
                    actual = locator.input_value()
                    if actual != valor:
                        locator.evaluate(
                            f"el => {{ el.value = '{valor}'; "
                            f"el.dispatchEvent(new Event('change', {{bubbles:true}})); "
                            f"el.dispatchEvent(new Event('input', {{bubbles:true}})); }}"
                        )
                        time.sleep(0.3)
                    # Método 3: press_sequentially como último recurso
                    actual = locator.input_value()
                    if actual != valor:
                        locator.click(click_count=3)
                        locator.press_sequentially(valor, delay=100)
                    time.sleep(0.3)

                iframe = obtener_iframe_activo(page)

                # ── Fechas dinámicas ──────────────────────────────────────
                def set_fecha(iframe_ref, selector, valor):
                    loc = iframe_ref.locator(selector)
                    loc.wait_for(state="visible", timeout=10000)
                    # Limpiar completamente con Ctrl+A + Delete
                    loc.click()
                    loc.press("Control+a")
                    loc.press("Delete")
                    time.sleep(0.2)
                    loc.type(valor, delay=100)
                    time.sleep(0.3)
                    actual = loc.input_value()
                    if actual != valor:
                        loc.evaluate(
                            f"el => {{ el.value = '{valor}'; "
                            f"el.dispatchEvent(new Event('input', {{bubbles:true}})); "
                            f"el.dispatchEvent(new Event('change', {{bubbles:true}})); "
                            f"el.dispatchEvent(new Event('blur', {{bubbles:true}})); }}"
                        )
                        time.sleep(0.3)
                    return loc.input_value()

                val_desde = set_fecha(iframe, "#body_txtFiltroFechaCargaDesde", desde)
                val_hasta = set_fecha(iframe, "#body_txtFiltroFechaCargaHasta", hasta)
                progreso_callback(
                    f"Fechas cargadas: desde={val_desde} hasta={val_hasta}",
                    avance + 0.02,
                )
                time.sleep(1)

                iframe.locator("#body_btnFiltro").click()
                progreso_callback(f"Procesando {desde} → {hasta}…", avance + 0.04)
                time.sleep(7)

                iframe = obtener_iframe_activo(page)
                if "No Hay Registros" in iframe.locator("body").inner_text():
                    progreso_callback(f"Sin registros: {desde} → {hasta}", avance + 0.05)
                    continue

                try:
                    combo_pag = iframe.locator("#body_cboPageSize")
                    if combo_pag.count() > 0:
                        combo_pag.select_option(value="625")
                        time.sleep(6)
                        iframe = obtener_iframe_activo(page)
                except Exception:
                    pass

                try:
                    chk_all = iframe.locator("#body_grdIntervencion_idChkAll")
                    if chk_all.count() > 0:
                        chk_all.click(force=True)
                        time.sleep(1.5)
                except Exception:
                    pass

                try:
                    hamburger = iframe.locator(".btn-group .dropdown-toggle, .dropdown-toggle, button:has(.fa-bars)").first
                    if hamburger.count() > 0:
                        hamburger.click()
                        time.sleep(1)

                    with page.expect_download(timeout=50000) as dl_info:
                        iframe.locator("a:has-text('EXPORTAR PRÁCTICAS EXCEL')").click(force=True)

                    dl = dl_info.value
                    excels_descargados.append(pd.read_excel(dl.path()))
                    progreso_callback(f"✓ Descarga {idx+1}/{n_rangos} OK", avance + 0.08)
                except Exception as e:
                    progreso_callback(f"Sin descarga {desde}→{hasta}: {e}", avance + 0.05)
                    continue

            return {"excels": excels_descargados, "error": None}

        except Exception as e:
            # Intentar capturar la URL actual para diagnóstico
            contexto = ""
            try:
                contexto = f" [URL al fallar: {page.url}]"
            except Exception:
                pass
            time.sleep(1)
            return {"excels": [], "error": f"{e}{contexto}"}
        finally:
            browser.close()


# ─────────────────────────────────────────────
#  CRUCE Y VALORIZACIÓN
# ─────────────────────────────────────────────

def procesar_datos(excels_evweb, archivo_facturacion, archivo_valores):
    """
    Cruce 1: nroAutorizacion (EVWEB) ↔ Id Transacción (Libro9) → profesional
    Cruce 2: profesional normalizado ↔ Nombre (Usuarios) → matricula, especialidad, arancel
    Valorización: Código práctica + arancel → Total prestación (VF)
    """
    logs = []  # diagnóstico

    # ── Consolidar EVWEB ─────────────────────────────────────────────────
    df_evweb = pd.concat(excels_evweb, ignore_index=True)
    logs.append(f"EVWEB consolidado: {len(df_evweb)} filas, columnas: {df_evweb.columns.tolist()}")

    # Rango de fechas reales en los datos descargados
    if 'FechaPrestacion' in df_evweb.columns:
        fechas_evweb = df_evweb['FechaPrestacion'].dropna().unique().tolist()
        logs.append(f"FechaPrestacion EVWEB (únicas): {fechas_evweb[:10]}")

    # ── Muestra de todas las columnas EVWEB ──────────────────────────────
    for c in df_evweb.columns:
        sample = df_evweb[c].dropna().head(3).tolist()
        logs.append(f"  EVWEB[{c}] → {sample}")

    # ── Detectar columna de autorización en EVWEB ────────────────────────
    col_auth, metodo_auth = detectar_col_autorizacion(df_evweb)
    logs.append(f"Columna autorización EVWEB → '{col_auth}' (método: {metodo_auth})")

    # ── Detectar columna de profesional en EVWEB ────────────────────────
    col_prof, metodo_prof = detectar_col_profesional(df_evweb)
    logs.append(f"Columna profesional EVWEB → '{col_prof}' (método: {metodo_prof})")

    # ── Columna de cuenta EVWEB (opcional) ──────────────────────────────
    col_cta_web = next(
        (c for c in df_evweb.columns if any(k in normalizar_cadena(c) for k in ['cuenta', 'cta', 'usuario'])),
        None
    )

    # ── Limpiar IDs EVWEB ────────────────────────────────────────────────
    df_evweb['_auth_clean'] = df_evweb[col_auth].apply(limpiar_id)
    muestra_ids_evweb = df_evweb['_auth_clean'].dropna().head(5).tolist()
    logs.append(f"Muestra IDs EVWEB limpios: {muestra_ids_evweb}")

    # ── Cargar planillas locales ─────────────────────────────────────────
    df_libro    = pd.read_excel(archivo_facturacion)
    df_usuarios = pd.read_excel(archivo_valores, sheet_name="Usuarios")
    df_vf       = pd.read_excel(archivo_valores, sheet_name="VF")

    col_tarifa = next(
        (c for c in df_vf.columns if 'total' in c.lower() and 'presta' in c.lower()),
        df_vf.columns[-1],
    )
    logs.append(f"Columna tarifa VF → '{col_tarifa}'")

    # ── Columna cuenta/matrícula en Usuarios ────────────────────────────
    col_usu_matricula = None
    for cand in ['matricula', 'matricula', 'cuenta', 'cta', 'nro', 'id', 'codigo']:
        for c in df_usuarios.columns:
            if c.lower().strip() == cand:
                col_usu_matricula = c
                break
        if col_usu_matricula:
            break
    if not col_usu_matricula:
        for cand in ['matricula', 'cuenta', 'cta', 'nro']:
            for c in df_usuarios.columns:
                if cand in c.lower():
                    col_usu_matricula = c
                    break
            if col_usu_matricula:
                break
    logs.append(f"Columna matrícula Usuarios → '{col_usu_matricula}'")

    # ── Preparar Id Transacción del Libro9 ───────────────────────────────
    col_id_libro = 'Id Transacción' if 'Id Transacción' in df_libro.columns else df_libro.columns[0]
    df_final = df_libro.copy()
    df_final['_id_clean'] = df_final[col_id_libro].apply(limpiar_id)
    muestra_ids_libro = df_final['_id_clean'].head(5).tolist()
    logs.append(f"Muestra IDs Libro9 limpios: {muestra_ids_libro}")

    # ── Diagnóstico de formato: repr() de ambos lados ────────────────────
    raw_evweb = df_evweb[col_auth].head(5).tolist()
    logs.append(f"nroAutorizacion RAW tipo={df_evweb[col_auth].dtype}: {raw_evweb}")
    logs.append(f"nroAutorizacion repr: {[repr(str(x)) for x in raw_evweb]}")
    raw_libro = df_libro[col_id_libro].head(5).tolist()
    logs.append(f"Id Transacción RAW tipo={df_libro[col_id_libro].dtype}: {raw_libro}")
    logs.append(f"Id Transacción repr: {[repr(str(x)) for x in raw_libro]}")
    # Match directo sin limpiar
    set_evweb_raw = set(df_evweb[col_auth].astype(str).str.strip().tolist())
    set_libro_raw = set(df_libro[col_id_libro].astype(str).str.strip().tolist())
    matches_raw = set_evweb_raw & set_libro_raw
    logs.append(f"Matches str.strip() sin limpiar: {len(matches_raw)} → {list(matches_raw)[:5]}")
    # Match con limpiar_id
    set_evweb_clean = set(df_evweb['_auth_clean'].tolist())
    set_libro_clean = set(df_final['_id_clean'].tolist())
    matches_clean = set_evweb_clean & set_libro_clean
    logs.append(f"Matches limpiar_id: {len(matches_clean)} → {list(matches_clean)[:5]}")

    # ── CRUCE 1: nroAutorizacion EVWEB ↔ Id Transacción Libro9 ──────────
    mapa_prof    = dict(zip(df_evweb['_auth_clean'], df_evweb[col_prof]))
    mapa_cta_web = dict(zip(df_evweb['_auth_clean'], df_evweb[col_cta_web])) if col_cta_web else {}

    df_final['profesional']  = df_final['_id_clean'].map(mapa_prof)
    df_final['cuenta_evweb'] = df_final['_id_clean'].map(mapa_cta_web) if mapa_cta_web else "—"

    n_cruce1_ok   = df_final['profesional'].notna().sum()
    n_cruce1_fail = df_final['profesional'].isna().sum()
    df_final['profesional'] = df_final['profesional'].fillna("revisar")
    logs.append(f"Cruce 1 resultado → OK: {n_cruce1_ok} | Sin match: {n_cruce1_fail}")

    # ── Columnas de salida ────────────────────────────────────────────────
    df_final['matricula']    = ""
    df_final['especialidad'] = ""
    df_final['categoria']    = ""
    df_final['valor_unit']   = 0.0
    df_final['total']        = 0.0

    # ── CRUCE 2: profesional normalizado ↔ Usuarios[Nombre] ──────────────
    df_usuarios['nombre_norm'] = df_usuarios['Nombre'].apply(normalizar_cadena)

    n_cruce2_ok   = 0
    n_cruce2_fail = 0

    for idx, fila in df_final.iterrows():
        prof = fila['profesional']

        if prof == "revisar":
            df_final.at[idx, 'matricula']    = "revisar"
            df_final.at[idx, 'especialidad'] = "revisar"
            df_final.at[idx, 'categoria']    = "revisar"
            continue

        match = buscar_coincidencia_medico(prof, df_usuarios)

        if match is not None:
            n_cruce2_ok += 1
            cat = str(match['Arancel']).strip()
            df_final.at[idx, 'categoria']    = cat
            df_final.at[idx, 'especialidad'] = str(match['Especialidad']).strip()

            if col_usu_matricula:
                cta = str(match[col_usu_matricula]).strip()
                df_final.at[idx, 'matricula'] = cta[:-2] if cta.endswith('.0') else cta
            else:
                df_final.at[idx, 'matricula'] = "col no encontrada"

            # Valorización
            cod = str(fila.get('Practi. Presta', '')).strip()
            df_final.at[idx, 'valor_unit'] = obtener_tarifa(cod, cat, df_vf, col_tarifa)
        else:
            n_cruce2_fail += 1
            df_final.at[idx, 'matricula']    = "no encontrado"
            df_final.at[idx, 'especialidad'] = "no encontrado"
            df_final.at[idx, 'categoria']    = "no encontrado"

    logs.append(f"Cruce 2 resultado → OK: {n_cruce2_ok} | Sin match: {n_cruce2_fail}")

    df_final['total'] = df_final['valor_unit'] * df_final.get('Cant. Tratamientos', 1)

    # ── Ordenar columnas: todas las originales del Libro9 primero,
    #    luego las columnas enriquecidas al final ──────────────────────────
    cols_originales  = [c for c in df_libro.columns if c in df_final.columns]
    cols_enriquecidas = ['profesional', 'matricula', 'especialidad', 'categoria', 'valor_unit', 'total']
    cols_descartar   = ['_id_clean', 'cuenta_evweb']  # columnas internas

    df_final = df_final[cols_originales + cols_enriquecidas]

    return df_final, col_id_libro, logs


# ─────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────


def mostrar_resultado(job):
    """Muestra los resultados precalculados por el thread. No procesa nada."""
    r = job.get('resultado')
    if not r:
        st.error("El proceso terminó pero no dejó resultados. Revisá los logs.")
        return

    st.markdown(
        "<small style='color:#6fcf97;letter-spacing:.06em'>Proceso completado ✓</small>",
        unsafe_allow_html=True,
    )

    # Diagnóstico opcional
    if job.get('mostrar_debug') and r.get('logs'):
        st.markdown("---")
        st.markdown('<p class="label-section">Diagnóstico de cruces</p>', unsafe_allow_html=True)
        log_html = "".join(
            f'<div style="margin:2px 0"><span style="color:#444">›</span>'
            f'<span style="color:#888">{l}</span></div>'
            for l in r['logs']
        )
        st.markdown(f'<div class="diag-box">{log_html}</div>', unsafe_allow_html=True)

    # Vista previa desde dict (no requiere releer archivos)
    st.markdown("---")
    st.markdown(
        f'<div class="label-section">Vista previa — {r["n_registros"]} registros'
        f' · el Excel descargado incluye todas las columnas del archivo importado</div>',
        unsafe_allow_html=True,
    )
    st.dataframe(
        pd.DataFrame(r['preview'], columns=r['columnas']),
        use_container_width=True,
        hide_index=True,
    )

    # Métricas
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total valorizado",  f"$ {r['total_val']:,.2f}")
    m2.metric("Cruzados OK",        r['ok_match'])
    m3.metric("Con tarifa",         r['con_tarifa'])
    m4.metric("Requieren revisión", r['sin_match'])

    # Descarga — bytes ya generados por el thread
    st.markdown("---")
    st.download_button(
        label="Descargar Excel valorizado",
        data=r['excel_bytes'],
        file_name=f"galeno_{job['fecha_inicio']}_{job['fecha_fin']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


with st.sidebar:
    st.markdown("### Credenciales EVWEB")
    usuario_evweb = st.text_input("Usuario", placeholder="usuario")
    clave_evweb   = st.text_input("Contraseña", type="password", placeholder="••••••••")

    st.markdown("---")
    st.markdown("### Rango de fechas")

    hoy        = date.today()
    primer_mes = hoy.replace(day=1)

    fecha_inicio = st.date_input("Desde", value=primer_mes, format="DD/MM/YYYY")
    fecha_fin    = st.date_input("Hasta", value=hoy,        format="DD/MM/YYYY")

    if fecha_inicio and fecha_fin and fecha_fin >= fecha_inicio:
        rangos_preview = generar_rangos_9_dias(fecha_inicio, fecha_fin)
        st.markdown(
            f'<div class="label-section">Tramos generados ({len(rangos_preview)})</div>',
            unsafe_allow_html=True,
        )
        chips = "".join(
            f'<span class="rango-chip">{d} → {h}</span>'
            for d, h in rangos_preview
        )
        st.markdown(chips, unsafe_allow_html=True)
    else:
        st.caption("⚠ Fecha inicio debe ser anterior a fecha fin.")

    st.markdown("---")
    st.markdown("### Filtros EVWEB")
    filtro_estado = st.selectbox(
        "Estado auditoría",
        options=[("APOB", "Aprobado"), ("PEND", "Pendiente"), ("", "Todos")],
        format_func=lambda x: x[1],
        index=0,
    )[0]
    filtro_facturado = st.selectbox(
        "Facturado",
        options=[("NO", "No"), ("SI", "Sí"), ("", "Todos")],
        format_func=lambda x: x[1],
        index=0,
    )[0]

    st.markdown("---")
    st.markdown("### Opciones")
    modo_oculto   = st.checkbox("Modo invisible (headless)", value=True)
    mostrar_debug = st.checkbox("Mostrar diagnóstico de cruces", value=False)


# ─────────────────────────────────────────────
#  ÁREA PRINCIPAL
# ─────────────────────────────────────────────

st.markdown("# ◈ Auditoría Galeno")
st.markdown("---")

# ── Verificar job activo PRIMERO ─────────────────────────────────────────
# Importante: hacerlo ANTES de renderizar los file uploaders.
# Tras st.rerun() los uploaders se resetean → archivos_ok = False,
# lo que ocultaría el progreso si la verificación estuviera después.
job_id = st.session_state.get('job_id')

# Recuperar job_id desde la URL si la sesión se perdió (reconexión WebSocket / reload).
# Esto es lo que evita que el resultado quede "huérfano" en _JOBS sin pantalla que lo muestre.
if not job_id:
    qp_job = qp_get('job')
    if qp_job:
        job_id = qp_job
        st.session_state['job_id'] = job_id

# Limpiar job_id huérfano (proceso reiniciado, _JOBS vacío)
if job_id and job_id not in _JOBS:
    st.session_state.pop('job_id', None)
    qp_del('job')
    job_id = None

if job_id:
    # ── Job en curso: mostrar progreso, no los uploaders ─────────────────
    job   = _JOBS[job_id]
    estado = job['status']
    status_txt, pct = job.get('progress', ("Iniciando…", 0.02))

    if estado == 'running':
        st.info(f"⏳ {status_txt}")
        st.progress(min(float(pct), 0.99))
        st.caption("Proceso corriendo en segundo plano. La página se actualiza automáticamente.")
        time.sleep(3)
        st.rerun()

    elif estado == 'done':
        st.progress(1.0)
        mostrar_resultado(job)
        st.markdown("---")
        if st.button("Nueva auditoría", use_container_width=True):
            _JOBS.pop(job_id, None)
            st.session_state.pop('job_id', None)
            qp_del('job')
            st.rerun()

    elif estado == 'error':
        st.error(f"Error en el proceso: {job.get('error', 'desconocido')}")
        if st.button("Reintentar", use_container_width=True):
            _JOBS.pop(job_id, None)
            st.session_state.pop('job_id', None)
            qp_del('job')
            st.rerun()

else:
    # ── Sin job activo: mostrar uploaders y botón de inicio ──────────────
    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        st.markdown('<p class="label-section">Planilla de Facturación</p>', unsafe_allow_html=True)
        archivo_facturacion = st.file_uploader("Libro9 (.xlsx)", type=["xlsx"], label_visibility="collapsed")
    with col_b:
        st.markdown('<p class="label-section">Base de Valores</p>', unsafe_allow_html=True)
        archivo_valores = st.file_uploader("Galeno Base (.xlsx)", type=["xlsx"], label_visibility="collapsed")

    st.markdown("---")

    archivos_ok = archivo_facturacion and archivo_valores
    fechas_ok   = fecha_inicio and fecha_fin and fecha_fin >= fecha_inicio
    rangos_final = generar_rangos_9_dias(fecha_inicio, fecha_fin) if fechas_ok else []

    if archivos_ok and fechas_ok:
        if st.button(
            f"Iniciar auditoría  ·  {len(rangos_final)} tramo{'s' if len(rangos_final) != 1 else ''}",
            type="primary",
            use_container_width=True,
        ):
            if not usuario_evweb or not clave_evweb:
                st.error("Ingrese las credenciales en el panel lateral.")
            else:
                # Leer archivos a BytesIO ANTES del rerun (UploadedFile se resetea)
                fac_bytes = io.BytesIO(archivo_facturacion.read())
                val_bytes = io.BytesIO(archivo_valores.read())

                jid = str(uuid.uuid4())
                _JOBS[jid] = {
                    'status':              'running',
                    'progress':            ("Iniciando sesión en EVWEB…", 0.02),
                    'excels':              [],
                    'error':               None,
                    'archivo_facturacion': fac_bytes,
                    'archivo_valores':     val_bytes,
                    'fecha_inicio':        fecha_inicio.strftime('%Y%m%d'),
                    'fecha_fin':           fecha_fin.strftime('%Y%m%d'),
                    'mostrar_debug':       mostrar_debug,
                    'filtro_estado':       filtro_estado,
                    'filtro_facturado':    filtro_facturado,
                }
                st.session_state['job_id'] = jid
                qp_set('job', jid)   # persiste el job ante reconexiones / reload

                def _run(jid, usuario, clave, modo, rangos):
                    def _prog(texto, pct):
                        _JOBS[jid]['progress'] = (texto, float(pct))
                    try:
                        # ── Paso 1: Extracción EVWEB ─────────────────────
                        resultado = ejecutar_extractor(usuario, clave, modo, rangos, _prog,
                            _JOBS[jid].get('filtro_estado','APOB'),
                            _JOBS[jid].get('filtro_facturado','NO'))

                        if resultado.get('error'):
                            _JOBS[jid]['error']  = resultado['error']
                            _JOBS[jid]['status'] = 'error'
                            return

                        excels_web = resultado.get('excels', [])
                        if not excels_web:
                            _JOBS[jid]['error']  = "No se obtuvieron registros aprobados en el período."
                            _JOBS[jid]['status'] = 'error'
                            return

                        # ── Paso 2: Cruce y valorización ─────────────────
                        _prog("Cruzando datos y valorizando…", 0.85)

                        fac = _JOBS[jid]['archivo_facturacion']
                        val = _JOBS[jid]['archivo_valores']
                        fac.seek(0)
                        val.seek(0)

                        df_val, col_id, logs = procesar_datos(excels_web, fac, val)

                        # ── Paso 3: Generar Excel con hoja de diagnóstico ──
                        _prog("Generando Excel…", 0.95)
                        out = io.BytesIO()
                        with pd.ExcelWriter(out, engine='openpyxl') as writer:
                            df_val.to_excel(writer, index=False, sheet_name="Valorizado")
                            # Hoja de diagnóstico — siempre incluida
                            df_diag = pd.DataFrame({
                                'Diagnóstico': logs,
                                'Columnas EVWEB': [
                                    str(excels_web[0].columns.tolist()) if excels_web else "N/A"
                                ] + [''] * (len(logs) - 1),
                                'IDs EVWEB (muestra)': [
                                    str(excels_web[0].iloc[:, 0].head(5).tolist()) if excels_web else "N/A"
                                ] + [''] * (len(logs) - 1),
                            })
                            df_diag.to_excel(writer, index=False, sheet_name="Diagnóstico")
                        excel_bytes = out.getvalue()

                        # ── Guardar resultados para la UI ─────────────────
                        cols_vista = [
                            col_id, 'Fecha Transacción', 'Apellido y Nombre Socio',
                            'Practi. Presta', 'Descripción Práctica', 'Cant. Tratamientos',
                            'profesional', 'matricula', 'especialidad',
                            'categoria', 'valor_unit', 'total',
                        ]
                        cols_vista = [c for c in cols_vista if c in df_val.columns]

                        _JOBS[jid]['resultado'] = {
                            'excel_bytes': excel_bytes,
                            'preview':     df_val[cols_vista].to_dict('records'),
                            'columnas':    cols_vista,
                            'total_val':   float(df_val['total'].sum()),
                            'ok_match':    int((df_val['categoria'].str.strip().isin(['A','B','C','R'])).sum()),
                            'sin_match':   int((df_val['categoria'].isin(['revisar','no encontrado',''])).sum()),
                            'con_tarifa':  int((df_val['valor_unit'] > 0).sum()),
                            'n_registros': len(df_val),
                            'logs':        logs,
                        }
                        _JOBS[jid]['status'] = 'done'

                    except Exception as e:
                        import traceback
                        _JOBS[jid]['error']  = f"{e}\n{traceback.format_exc()}"
                        _JOBS[jid]['status'] = 'error'

                threading.Thread(
                    target=_run,
                    args=(jid, usuario_evweb, clave_evweb, modo_oculto, rangos_final),
                    daemon=True,
                ).start()

                # ── Delegar la visualización al bloque de polling superior ──
                # Ya NO bloqueamos el run con un while: eso mantenía una única
                # ejecución abierta durante minutos y, al reconectar el WebSocket,
                # el resultado quedaba huérfano sin pantalla que lo mostrara.
                # Con st.rerun() el control pasa al bloque `if job_id:` de arriba,
                # que hace polling con runs cortos y completos (robusto a reconexión).
                st.rerun()


    elif not archivos_ok:
        st.caption("Suba ambos archivos para continuar.")
    elif not fechas_ok:
        st.caption("Configure el rango de fechas en el panel lateral.")

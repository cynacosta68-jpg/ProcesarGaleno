import streamlit as st
import pandas as pd
import time
import re
import os
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright
import io

# 👑 CONFIGURACIÓN ESTÉTICA DE LA PÁGINA
st.set_page_config(page_title="Procesador Galeno", page_icon="🚀", layout="wide")

st.title("🚀 Procesador Inteligente de Facturación - GALENO")
st.markdown("Cargá las planillas de facturación y las bases de aranceles para ejecutar la auditoría automatizada en tiempo real.")
st.markdown("---")

# 1. FUNCIONES AUXILIARES GENERALES
def normalizar_cadena(texto):
    if pd.isna(texto): return ""
    texto = str(texto).strip().lower()
    texto = "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    return " ".join(texto.replace("...", "").replace(",", " ").replace(".", " ").split())

def buscar_coincidencia_medico(nombre_evweb, df_usuarios):
    nombre_evweb_norm = normalizar_cadena(nombre_evweb)
    if not nombre_evweb_norm or nombre_evweb_norm == "revisar": return None
    match_exacto = df_usuarios[df_usuarios['nombre_norm'] == nombre_evweb_norm]
    if not match_exacto.empty: return match_exacto.iloc[0]
    for _, usuario in df_usuarios.iterrows():
        if nombre_evweb_norm in usuario['nombre_norm'] or usuario['nombre_norm'] in nombre_evweb_norm:
            return usuario
    return None

def obtener_tarifa_galeno(cod_practica, categoria_medico, df_vf):
    cod_str = str(cod_practica).strip()
    cat_str = str(categoria_medico).strip()
    coincidencias_base = df_vf[df_vf['Código'].astype(str).str.strip() == cod_str]
    if coincidencias_base.empty: return 0.0
    
    # Prioridad 1: Nomenclador GALENO
    df_galeno = coincidencias_base[coincidencias_base['Nomenclador'].astype(str).str.strip() == "Nomenclador GALENO"]
    if not df_galeno.empty:
        t_cat = df_galeno[df_galeno['Arancel'].astype(str).str.strip() == cat_str]
        if not t_cat.empty: return float(t_cat.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])
        t_vf = df_galeno[df_galeno['Arancel'].astype(str).str.strip() == 'VF']
        if not t_vf.empty: return float(t_vf.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])

    # Prioridad 2: Nomenclador Vacío
    df_vacio = coincidencias_base[coincidencias_base['Nomenclador'].isna() | (coincidencias_base['Nomenclador'].astype(str).str.strip() == "")]
    if not df_vacio.empty:
        t_cat = df_vacio[df_vacio['Arancel'].astype(str).str.strip() == cat_str]
        if not t_cat.empty: return float(t_cat.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])
        t_vf = df_vacio[df_vacio['Arancel'].astype(str).str.strip() == 'VF']
        if not t_vf.empty: return float(t_vf.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])

    # Prioridad 3: Caída libre
    t_cat = coincidencias_base[coincidencias_base['Arancel'].astype(str).str.strip() == cat_str]
    if not t_cat.empty: return float(t_cat.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])
    return float(coincidencias_base.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])

# 2. BOT EXTRACTOR SEGURO (CON PAUSA SÍNCRONA DE ESTABILIDAD)
def worker_extractor(lista_ids_chunk, worker_id, usuario, clave, modo_invisible):
    mapeo_parcial = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=modo_invisible, args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        try:
            # Login estable inicial
            page.goto("https://cmsc.evweb.com.ar/Account/Login", timeout=60000)
            page.fill("input[name='UserName']", usuario)
            page.fill("#Password", clave)
            page.click("button[type='submit']")
            
            # Filtro estricto de visibilidad para saltear clones móviles
            menu_facturacion = page.get_by_text(re.compile(r"Facturaci", re.IGNORECASE)).filter(visible=True).first
            menu_facturacion.wait_for(state="visible", timeout=45000)
            menu_facturacion.click()
            
            menu_prestaciones = page.get_by_text(re.compile(r"prestaci", re.IGNORECASE)).filter(visible=True).first
            menu_prestaciones.wait_for(state="visible", timeout=30000)
            menu_prestaciones.click()
            
            # 🔥 CORRECCIÓN CRÍTICA: Cambiamos networkidle por domcontentloaded para evitar bloqueos por concurrencia
            page.wait_for_load_state("domcontentloaded", timeout=30000)

            for id_transaccion in lista_ids_chunk:
                id_str = str(id_transaccion).strip()
                try:
                    # Recarga limpia obligatoria de ASP
                    page.reload(wait_until="domcontentloaded")
                    time.sleep(1.5) # Asentamiento del iframe post-refresh
                    
                    iframe_target = next((f for f in page.frames if f.locator("#body_txtNroAutorizacion").count() > 0), page.frames[1])
                    
                    input_autorizacion = iframe_target.locator("#body_txtNroAutorizacion")
                    input_autorizacion.wait_for(state="attached", timeout=15000)
                    
                    # Reapertura y Blanqueo síncrono por DOM
                    iframe_target.evaluate("""() => {
                        let input = document.getElementById("body_txtNroAutorizacion");
                        if (input) {
                            let parent = input.parentElement;
                            while (parent) {
                                if (parent.classList.contains('collapse')) parent.classList.add('show');
                                if (parent.style.display === 'none') parent.style.display = 'block';
                                parent = parent.parentElement;
                            }
                        }
                        let cboE = document.getElementById("body_cboEstados");
                        if (cboE) { cboE.selectedIndex = -1; cboE.value = ""; }
                        let cboF = document.getElementById("body_cboFiltroFacturado");
                        if (cboF) { cboF.selectedIndex = -1; cboF.value = ""; }
                    }""")

                    input_autorizacion.fill(id_str)
                    
                    # Ejecución del clic de búsqueda
                    iframe_target.locator("#body_btnFiltro").click()
                    
                    # Pausa fija de estabilidad para dar tiempo al renderizado real
                    time.sleep(4.5) 
                    
                    filas = iframe_target.locator("table tr").all()
                    
                    if len(filas) <= 1 or "No Hay Registros" in iframe_target.locator("table").inner_text():
                        mapeo_parcial[id_transaccion] = {"Profesional": "revisar", "Matricula": "revisar"}
                    else:
                        texto_fila = filas[1].inner_text().strip().split('\t')

import streamlit as st
import pandas as pd
import time
import re
import os
import unicodedata
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import io

# 👑 CONFIGURACIÓN ESTÉTICA DE LA PÁGINA
st.set_page_config(page_title="Procesador Galeno - Estrategia Masiva", page_icon="🚀", layout="wide")

st.title("🚀 Auditoría Galeno - Estrategia B (Descarga Masiva)")
st.markdown("Cruce local ultrarrápido mediante exportación consolidada de bloques de aranceles de EVWEB.")
st.markdown("---")

# 1. FUNCIONES AUXILIARES GENERALES
def normalizar_cadena(texto):
    if pd.isna(texto): return ""
    texto = str(texto).strip().lower()
    texto = "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    return " ".join(texto.replace("...", "").replace(",", " ").replace(".", " ").split())

def buscar_coincidencia_medico(nombre_evweb, df_usuarios):
    nombre_evweb_norm = normalizar_cadena(nombre_evweb)
    if not nombre_evweb_norm or nombre_evweb_norm in ["revisar", ""]: return None
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
    
    df_galeno = coincidencias_base[coincidencias_base['Nomenclador'].astype(str).str.strip() == "Nomenclador GALENO"]
    if not df_galeno.empty:
        t_cat = df_galeno[df_galeno['Arancel'].astype(str).str.strip() == cat_str]
        if not t_cat.empty: return float(t_cat.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])
        t_vf = df_galeno[df_galeno['Arancel'].astype(str).str.strip() == 'VF']
        if not t_vf.empty: return float(t_vf.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])

    df_vacio = coincidencias_base[coincidencias_base['Nomenclador'].isna() | (coincidencias_base['Nomenclador'].astype(str).str.strip() == "")]
    if not df_vacio.empty:
        t_cat = df_vacio[df_vacio['Arancel'].astype(str).str.strip() == cat_str]
        if not t_cat.empty: return float(t_cat.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])
        t_vf = df_vacio[df_vacio['Arancel'].astype(str).str.strip() == 'VF']
        if not t_vf.empty: return float(t_vf.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])

    t_cat = coincidencias_base[coincidencias_base['Arancel'].astype(str).str.strip() == cat_str]
    if not t_cat.empty: return float(t_cat.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])
    return float(coincidencias_base.sort_values(by='Periodo', ascending=False).iloc[0]['Total prestación'])

# Generador dinámico de bloques de 10 días para el mes en curso
def generar_rangos_fechas():
    hoy = datetime.now()
    primer_dia = hoy.replace(day=1)
    
    # Bloques: 1 al 10, 11 al 20, 21 al 31 (o fin de mes)
    ultimo_dia = (primer_dia + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    
    rangos = [
        (primer_dia.strftime("%d/%m/%Y"), primer_dia.replace(day=10).strftime("%d/%m/%Y")),
        (primer_dia.replace(day=11).strftime("%d/%m/%Y"), primer_dia.replace(day=20).strftime("%d/%m/%Y")),
        (primer_dia.replace(day=21).strftime("%d/%m/%Y"), ultimo_dia.strftime("%d/%m/%Y"))
    ]
    return rangos

# 2. BOT EXTRACTOR MASIVO (ESTRATEGIA B)
def ejecutar_extractor_masivo(usuario, clave, modo_invisible, progreso_callback):
    rangos = generar_rangos_fechas()
    excels_descargados = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=modo_invisible,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        
        try:
            progreso_callback("🔑 Accediendo y autenticando en EVWEB...", 0.10)
            page.goto("https://cmsc.evweb.com.ar/Account/Login", timeout=60000)
            page.fill("input[name='UserName']", usuario)
            page.fill("#Password", clave)
            page.click("button[type='submit']")
            
            menu_facturacion = page.locator("text=/Facturaci/i >> visible=true").first
            menu_facturacion.wait_for(state="visible", timeout=30000)
            menu_facturacion.click()
            
            menu_prestaciones = page.locator("text=/prestaci/i >> visible=true").first
            menu_prestaciones.wait_for(state="visible", timeout=20000)
            menu_prestaciones.click()
            
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            time.sleep(2)
            
            iframe_target = next((f for f in page.frames if f.locator("#body_cboObrasSociales").count() > 0), page.frames[1])
            
            # Recorrer e interactuar con los rangos de fechas definidos
            for idx, (desde, hasta) in enumerate(rangos):
                progreso_callback(f"📅 Extrayendo bloque {idx+1}/3: Desde {desde} hasta {hasta}...", 0.20 + (idx * 0.20))
                
                page.reload(wait_until="domcontentloaded")
                time.sleep(2)
                iframe_target = next((f for f in page.frames if f.locator("#body_cboObrasSociales").count() > 0), page.frames[1])
                
                # 1) Datos generales / Obra social
                iframe_target.locator("#body_cboObrasSociales").select_option(label="10099 - GALENO Argentina S.A.  AZUL/BLANCO/ORO/PLATA")
                
                # 2) Estados / Estados de auditoria / Facturadas NO
                iframe_target.locator("#body_cboEstados").select_option(label="APROBADO EN OBRA SOCIAL")
                iframe_target.locator("#body_cboFiltroFacturado").select_option(value="N")
                
                # 3) Fechas rango dinámico
                iframe_target.locator("#body_txtFechaCargaDesde").fill(desde)
                iframe_target.locator("#body_txtFechaCargaHasta").fill(hasta)
                
                # Clic en buscar
                iframe_target.locator("#body_btnFiltro").click()
                time.sleep(5) # Tiempo de procesamiento del servidor ASP
                
                # Validar si existen registros en el bloque actual
                if "No Hay Registros" in iframe_target.locator("table").inner_text():
                    continue
                
                # Paginación completa: Mostrar "TODAS"
                try:
                    select_paginas = iframe_target.locator("select[name*='Paginas']").first
                    if select_paginas.count() > 0:
                        select_paginas.select_option(text="TODAS")
                        time.sleep(4)
                except:
                    pass
                
                # Clic en botón hamburguesa y exportar a Excel
                try:
                    iframe_target.locator(".btn-group .dropdown-toggle").click() # Botón hamburguesa
                    time.sleep(1)
                    
                    with page.expect_download(timeout=40000) as download_info:
                        iframe_target.get_by_text("Exportar prácticas excel").click()
                    
                    download = download_info.value
                    path = download.path()
                    
                    # Guardamos el archivo exportado en la lista local temporal
                    df_temp = pd.read_excel(path)
                    excels_descargados.append(df_temp)
                except Exception as e:
                    st.warning(f"⚠️ No se pudo exportar el bloque de fechas {desde} - {hasta}: {str(e)}")
                    continue
                    
        except Exception as e:
            raise Exception(f"Fallo estructural en interacción web: {str(e)}")
        finally:
            browser.close()
            
    return excels_descargados

# 3. INTERFAZ EN SECCIÓN LATERAL (SIDEBAR)
st.sidebar.header("🔒 Credenciales de Acceso")
usuario_evweb = st.sidebar.text_input("Usuario EVWEB", placeholder="Tipee su usuario")
clave_evweb = st.sidebar.text_input("Contraseña EVWEB", type="password", placeholder="Tipee su clave")

st.sidebar.markdown("---")
st.sidebar.header("⚙️ Entorno de Ejecución")
modo_oculto = st.sidebar.checkbox("Ejecutar en modo invisible (Recomendado para nube)", value=True)

# 4. ÁREA PRINCIPAL: DRAG & DROP
col1, col2 = st.columns(2)
with col1:
    archivo_facturacion = st.file_uploader("📥 Subir planilla de Facturación (Libro9)", type=["xlsx"])
with col2:
    archivo_valores = st.file_uploader("📥 Subir Base de Valores (Galeno)", type=["xlsx"])

if archivo_facturacion and archivo_valores:
    st.success("✅ Estructuras base cargadas.")
    
    if st.button("🚀 Ejecutar Auditoría Estrategia Masiva", type="primary"):
        if not usuario_evweb or not clave_evweb:
            st.error("❌ Complete los datos de credenciales requeridos en el panel lateral.")
        else:
            status_container = st.empty()
            bar_container = st.progress(0)
            
            def actualizar_progreso(texto, porcentaje):
                status_container.text(texto)
                bar_container.progress(porcentaje)
                
            try:
                # Ejecutar descargas agrupadas
                archivos_excel = ejecutar_extractor_masivo(usuario_evweb, clave_evweb, modo_oculto, actualizar_progreso)
                
                if not archivos_excel:
                    st.error("❌ No se encontraron registros aprobados pendientes de facturación para Galeno en este mes.")
                    st.stop()
                    
                actualizar_progreso("📊 Unificando reportes y aplicando matriz de cálculo...", 0.85)
                
                # Consolidación de descargas en un único dataframe de búsqueda masiva
                df_maestro_evweb = pd.concat(archivos_excel, ignore_index=True)
                
                # Limpieza y normalización de claves de cruce
                # Modificar los nombres de las columnas si en el Excel exportado figuran distinto
                df_maestro_evweb['Nro. Autorización'] = df_maestro_evweb['Nro. Autorización'].astype(str).str.strip()
                
                # Carga de aranceles locales
                df_importado = pd.read_excel(archivo_facturacion)
                df_usuarios = pd.read_excel(archivo_valores, sheet_name="Usuarios")
                df_vf = pd.read_excel(archivo_valores, sheet_name="VF")
                
                # Construcción del dataframe final
                df_final = df_importado.copy()
                df_final['Id Transacción'] = df_final['Id Transacción'].astype(str).str.strip()
                
                # Mapeo directo indexado (Cruce masivo instantáneo en memoria)
                # Creamos diccionarios de mapeo desde el Excel consolidado de EVWEB
                mapeo_profesional = dict(zip(df_maestro_evweb['Nro. Autorización'], df_maestro_evweb['Profesional Prescriptor']))
                mapeo_matricula = dict(zip(df_maestro_evweb['Nro. Autorización'], df_maestro_evweb['Matrícula Prescriptor']))
                
                df_final['profesional'] = df_final['Id Transacción'].map(mapeo_profesional).fillna("revisar")
                df_final['matricula'] = df_final['Id Transacción'].map(mapeo_matricula).fillna("revisar")
                
                df_final['categoría'] = ""
                df_final['especialidad'] = ""
                df_final['Valor'] = 0.0
                df_final['total'] = 0.0
                
                df_usuarios['nombre_norm'] = df_usuarios['Nombre'].apply(normalizar_cadena)
                
                # Cascada de valorización arancelaria tradicional
                for idx, fila in df_final.iterrows():
                    medico_evweb = fila['profesional']
                    if medico_evweb == "revisar":
                        continue
                        
                    medico_match = buscar_coincidencia_medico(medico_evweb, df_usuarios)
                    if medico_match is not None:
                        cat = str(medico_match['Arancel']).strip()
                        df_final.at[idx, 'categoría'] = cat
                        df_final.at[idx, 'especialidad'] = str(medico_match['Especialidad']).strip()
                        
                        cod_practica = str(fila['Practi. Presta']).strip()
                        df_final.at[idx, 'Valor'] = obtener_tarifa_galeno(cod_practica, cat, df_vf)
                    else:
                        df_final.at[idx, 'categoría'] = "no encontrado"
                        df_final.at[idx, 'especialidad'] = "no encontrado"
                        
                df_final['total'] = df_final['Valor'] * df_final['Cant. Tratamientos']
                
                bar_container.progress(1.0)
                status_container.text("✅ Auditoría completada con éxito.")
                st.success("🎉 ¡Proceso finalizado de forma masiva!")
                
                st.dataframe(df_final[['Id Transacción', 'Practi. Presta', 'profesional', 'categoría', 'Valor', 'total']].head(10))
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False)
                excel_data = output.getvalue()
                
                st.download_button(
                    label="📥 Descargar Reporte Valorizado Final (Excel)",
                    data=excel

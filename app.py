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

# 2. BOT EXTRACTOR DIAGNÓSTICO (CAPTURA FOTOS EN CASO DE ERROR)
def worker_extractor(lista_ids_chunk, worker_id, usuario, clave, modo_invisible):
    mapeo_parcial = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=modo_invisible, 
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process"
            ]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        try:
            # Fase 1: Login e ingreso al módulo
            page.goto("https://cmsc.evweb.com.ar/Account/Login", timeout=60000)
            page.fill("input[name='UserName']", usuario)
            page.fill("#Password", clave)
            page.click("button[type='submit']")
            
            # Espera y clicks del menú
            menu_facturacion = page.get_by_text(re.compile(r"Facturaci", re.IGNORECASE)).filter(visible=True).first
            menu_facturacion.wait_for(state="visible", timeout=30000)
            menu_facturacion.click()
            
            menu_prestaciones = page.get_by_text(re.compile(r"prestaci", re.IGNORECASE)).filter(visible=True).first
            menu_prestaciones.wait_for(state="visible", timeout=20000)
            menu_prestaciones.click()
            
            page.wait_for_load_state("domcontentloaded", timeout=20000)

            # Fase 2: Bucle de extracción de datos
            for id_transaccion in lista_ids_chunk:
                id_str = str(id_transaccion).strip()
                try:
                    page.reload(wait_until="domcontentloaded")
                    time.sleep(1.5)
                    
                    iframe_target = next((f for f in page.frames if f.locator("#body_txtNroAutorizacion").count() > 0), page.frames[1])
                    input_autorizacion = iframe_target.locator("#body_txtNroAutorizacion")
                    input_autorizacion.wait_for(state="attached", timeout=15000)
                    
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
                    iframe_target.locator("#body_btnFiltro").click()
                    time.sleep(4.5) 
                    
                    filas = iframe_target.locator("table tr").all()
                    if len(filas) <= 1 or "No Hay Registros" in iframe_target.locator("table").inner_text():
                        mapeo_parcial[id_transaccion] = {"Profesional": "revisar", "Matricula": "revisar"}
                    else:
                        texto_fila = filas[1].inner_text().strip().split('\t')
                        mapeo_parcial[id_transaccion] = {"Profesional": texto_fila[0].strip(), "Matricula": texto_fila[1].strip()}
                except:
                    mapeo_parcial[id_transaccion] = {"Profesional": "revisar", "Matricula": "revisar"}
                    continue
                    
        except Exception as e:
            # 📸 SI CORTA ACÁ: Saca una foto de la pantalla del servidor y la manda a la interfaz
            try:
                screenshot_bytes = page.screenshot(type="png")
            except:
                screenshot_bytes = None
            return {"data": {}, "error": str(e), "screenshot": screenshot_bytes}
        finally:
            browser.close()
            
    return {"data": mapeo_parcial, "error": None, "screenshot": None}

# 3. INTERFAZ Y RECOLECCIÓN DE PARÁMETROS (SIDEBAR)
st.sidebar.header("🔒 Credenciales e Infraestructura")
usuario_evweb = st.sidebar.text_input("Usuario EVWEB", placeholder="Ingrese su usuario")
clave_evweb = st.sidebar.text_input("Contraseña EVWEB", type="password", placeholder="Ingrese su clave")

st.sidebar.markdown("---")
st.sidebar.header("⚡ Ajustes de Rendimiento")
# Recomendado en 1 o 2 hilos para la prueba de diagnóstico en la nube
cant_navegadores = st.sidebar.slider("Navegadores simultáneos", min_value=1, max_value=6, value=1)
modo_oculto = st.sidebar.checkbox("Ejecutar en modo invisible (Más rápido)", value=True)

# 4. ÁREA PRINCIPAL: DRAG & DROP DE ARCHIVOS
col1, col2 = st.columns(2)
with col1:
    archivo_facturacion = st.file_uploader("📥 Subir planilla de Facturación (Libro9)", type=["xlsx"])
with col2:
    archivo_valores = st.file_uploader("📥 Subir Base de Valores (Galeno)", type=["xlsx"])

if archivo_facturacion and archivo_valores:
    st.success("✅ Ambos archivos fueron cargados correctamente.")
    
    if st.button("🚀 Iniciar Procesamiento Masivo", type="primary"):
        if not usuario_evweb or not clave_evweb:
            st.error("❌ Por favor, complete sus credenciales de EVWEB en la barra lateral antes de continuar.")
        else:
            with st.spinner("Procesando lote en servidores web..."):
                df_importado = pd.read_excel(archivo_facturacion)
                df_usuarios = pd.read_excel(archivo_valores, sheet_name="Usuarios")
                df_vf = pd.read_excel(archivo_valores, sheet_name="VF")
                
                lista_ids = df_importado['Id Transacción'].tolist()
                total_filas = len(lista_ids)
                
                st.info(f"📋 Total de registros a auditar: {total_filas} transacciones.")
                
                progreso_bar = st.progress(0)
                status_text = st.empty()
                
                status_text.text("🔥 Abriendo instancias de hardware en paralelo...")
                chunks = [lista_ids[i::cant_navegadores] for i in range(cant_navegadores)]
                
                tiempo_inicio = time.time()
                datos_scraped_totales = {}
                hubo_errores_globales = False
                
                with ThreadPoolExecutor(max_workers=cant_navegadores) as executor:
                    futuros = [
                        executor.submit(worker_extractor, chunks[i], i+1, usuario_evweb, clave_evweb, modo_oculto)
                        for i in range(cant_navegadores)
                    ]
                    
                    for idx, f in enumerate(futuros):
                        resultado = f.result()
                        if resultado.get("error"):
                            hubo_errores_globales = True
                            st.error(f"❌ El Navegador {idx+1} no pudo iniciar sesión: {resultado['error']}")
                            if resultado.get("screenshot"):
                                st.image(resultado["screenshot"], caption=f"Captura de pantalla del Servidor - Navegador {idx+1}")
                        else:
                            datos_scraped_totales.update(resultado["data"])
                            
                        progreso_bar.progress((idx + 1) / cant_navegadores)
                        status_text.text(f"⏳ Evaluando hilos de procesamiento...")

                if hubo_errores_globales and not datos_scraped_totales:
                    st.stop()

                progreso_bar.progress(1.0)
                status_text.text("✅ Procesando matriz de cálculo de aranceles...")
                
                df_final = df_importado.copy()
                df_final['matricula'] = ""
                df_final['categoría'] = ""
                df_final['profesional'] = ""
                df_final['especialidad'] = ""
                df_final['Valor'] = 0.0
                df_final['total'] = 0.0
                
                for id_tx, datos in datos_scraped_totales.items():
                    df_final.loc[df_final['Id Transacción'] == id_tx, 'profesional'] = datos['Profesional']
                    df_final.loc[df_final['Id Transacción'] == id_tx, 'matricula'] = datos['Matricula']

                df_usuarios['nombre_norm'] = df_usuarios['Nombre'].apply(normalizar_cadena)
                
                for idx, fila in df_final.iterrows():
                    medico_evweb = fila['profesional']
                    if medico_evweb == "revisar" or not medico_evweb:
                        df_final.at[idx, 'categoría'] = "revisar"
                        df_final.at[idx, 'especialidad'] = "revisar"
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
                
                tiempo_total = time.time() - tiempo_inicio
                st.success(f"🎉 ¡Proceso finalizado en {tiempo_total/60:.2f} minutos!")
                st.dataframe(df_final[['Id Transacción', 'Practi. Presta', 'profesional', 'categoría', 'Valor', 'total']].head(10))
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False)
                excel_data = output.getvalue()
                
                st.download_button(
                    label="📥 Descargar Reporte Valorizado Final (Excel)",
                    data=excel_data,
                    file_name="auditoria_final_galeno.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

import streamlit as st
import pandas as pd
from datetime import datetime, date

# 1. Configuración de la página
st.set_page_config(page_title="Portal del Colaborador", page_icon="🏢", layout="wide")

# ==========================================
# CANDADO 3: LA BÓVEDA DE SECRETOS (st.secrets)
# ==========================================
# Las credenciales del ERP nunca se escriben en el código fuente. 
# Se leen de manera encriptada desde la bóveda de la nube o entorno local.
try:
    API_KEY = st.secrets["SIMPLIFICA_API_KEY"]
except FileNotFoundError:
    API_KEY = "llave_oculta_local_123" # Resguardo para pruebas locales si no existe el archivo secrets.toml

# 2. Base de datos simulada de usuarios
db_simulada = {
    "EMP003": {"dni": "72345678", "nombre": "Carlos", "cargo": "E-commerce & Digital Marketing", "fecha_ingreso": "2024-03-15"},
    "ADMIN-RRHH": {"dni": "RRHH2026", "nombre": "Recursos Humanos", "cargo": "Panel de Control"}
}
meses_esp = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}

# 3. Control de Memoria y Sesión
if "logueado" not in st.session_state:
    st.session_state.logueado = False
    st.session_state.codigo_actual = ""
    st.session_state.ultimo_acceso = None
    st.session_state.mensaje_alerta = "" 

if "solicitudes" not in st.session_state:
    st.session_state.solicitudes = [
        {"Código": "EMP001", "Nombre": "Ana (Ejemplo)", "Mes": "Agosto", "Año": 2026, "Inicio": "2026-08-10", "Fin": "2026-08-25", "Comentarios": "Vacaciones anuales.", "Estado": "Pendiente"}
    ]

# ==========================================
# CANDADO 4: CIERRE AUTOMÁTICO POR INACTIVIDAD
# ==========================================
# Si pasan más de X minutos sin interacción, el sistema expulsa al usuario por seguridad.
TIEMPO_MAXIMO_MINUTOS = 5 # (Puedes cambiarlo a 0.1 para pruebas rápidas)

if st.session_state.logueado:
    tiempo_actual = datetime.now()
    if st.session_state.ultimo_acceso:
        minutos_inactivos = (tiempo_actual - st.session_state.ultimo_acceso).total_seconds() / 60
        if minutos_inactivos > TIEMPO_MAXIMO_MINUTOS:
            st.session_state.logueado = False
            st.session_state.codigo_actual = ""
            st.session_state.ultimo_acceso = None
            st.session_state.mensaje_alerta = "⚠️ Tu sesión expiró por inactividad. Por tu seguridad, vuelve a ingresar."
            st.rerun() 
    
    st.session_state.ultimo_acceso = tiempo_actual

# ==========================================
# PANTALLA 1: INICIO DE SESIÓN
# ==========================================
if not st.session_state.logueado:
    col_izq, col_centro, col_der = st.columns([1, 2, 1])
    with col_centro:
        st.title("🏢 Portal Interno")
        
        if st.session_state.mensaje_alerta != "":
            st.warning(st.session_state.mensaje_alerta)
            st.session_state.mensaje_alerta = ""
            
        with st.form("formulario_login"):
            codigo_input = st.text_input("Código de Colaborador (Ej. EMP003)")
            dni_input = st.text_input("DNI", type="password") 
            boton_ingresar = st.form_submit_button("Ingresar al Portal")
            
            if boton_ingresar:
                codigo_limpio = codigo_input.upper().strip()
                if codigo_limpio in db_simulada and db_simulada[codigo_limpio]["dni"] == dni_input:
                    st.session_state.logueado = True
                    st.session_state.codigo_actual = codigo_limpio
                    st.session_state.ultimo_acceso = datetime.now()
                    st.rerun() 
                else:
                    st.error("❌ Código o DNI incorrectos. Inténtalo de nuevo.")

# ==========================================
# PANTALLA 2: EL PORTAL 
# ==========================================
else:
    # ==========================================
    # CANDADO 2: CONSULTAS AISLADAS (Nunca masivas)
    # ==========================================
    # El sistema hace una consulta quirúrgica y exclusiva para el código exacto 
    # que ingresó, evitando cargar o filtrar información de otros empleados.
    usuario = db_simulada[st.session_state.codigo_actual]
    
    col_vacia, col_boton = st.columns([8, 1])
    with col_boton:
        if st.button("🚪 Cerrar Sesión"):
            st.session_state.logueado = False
            st.session_state.codigo_actual = ""
            st.session_state.ultimo_acceso = None
            st.rerun()

    st.divider() 

    # --- VISTA DE RECURSOS HUMANOS ---
    if st.session_state.codigo_actual == "ADMIN-RRHH":
        st.title("⚙️ Panel de Administración")
        st.info("Gestión y Aprobación de Solicitudes de Vacaciones")
        df = pd.DataFrame(st.session_state.solicitudes)
        
        st.subheader("Filtros de Búsqueda")
        col_filtro1, col_filtro2 = st.columns(2)
        with col_filtro1:
            lista_meses = ["Todos"] + list(df["Mes"].unique())
            filtro_mes = st.selectbox("Filtrar por Mes de Inicio", lista_meses)
        with col_filtro2:
            lista_anios = ["Todos"] + list(df["Año"].unique())
            filtro_anio = st.selectbox("Filtrar por Año", lista_anios)
            
        df_filtrado = df.copy()
        if filtro_mes != "Todos":
            df_filtrado = df_filtrado[df_filtrado["Mes"] == filtro_mes]
        if filtro_anio != "Todos":
            df_filtrado = df_filtrado[df_filtrado["Año"] == filtro_anio]
            
        st.divider()
        st.subheader("📋 Bandeja de Solicitudes")
        
        if df_filtrado.empty:
            st.warning("No hay solicitudes registradas para los filtros seleccionados.")
        else:
            columnas_config = {
                "Estado": st.column_config.SelectboxColumn(
                    "Estado",
                    help="Cambia el estado de la solicitud",
                    options=["Pendiente", "Aprobado", "Rechazado"],
                    required=True
                )
            }
            
            df_modificado = st.data_editor(
                df_filtrado, 
                column_config=columnas_config,
                disabled=["Código", "Nombre", "Mes", "Año", "Inicio", "Fin", "Comentarios"], 
                hide_index=True,
                use_container_width=True
            )
            
            if st.button("💾 Guardar Cambios (Simulación)"):
                st.session_state.solicitudes = df_modificado.to_dict('records')
                st.success("✅ Los cambios de estado han sido registrados exitosamente.")
        
    # --- VISTA DEL COLABORADOR ---
    else:
        st.title(f"👋 Bienvenido, {usuario['nombre']}")
        
        fecha_ingreso = datetime.strptime(usuario['fecha_ingreso'], "%Y-%m-%d").date()
        hoy = date.today()
        dias_totales = (hoy - fecha_ingreso).days
        
        anios = dias_totales // 365
        meses = (dias_totales % 365) // 30
        dias = (dias_totales % 365) % 30
        texto_antiguedad = f"{anios} años, {meses} meses y {dias} días"

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Mis Datos")
            st.write(f"**Cargo:** {usuario['cargo']}")
            st.write("**Estado del Contrato:** Activo")
            st.write(f"**Antigüedad:** {texto_antiguedad}")
        with col2:
            st.subheader("Mis Vacaciones")
            st.metric(label="Días Disponibles", value="15 días")
            
        st.divider()

        tab_solicitar, tab_estado = st.tabs(["✈️ Solicitar Vacaciones", "📋 Estado de Mis Solicitudes"])
        
        with tab_solicitar:
            st.write("Selecciona las fechas en las que deseas programar tu descanso.")
            with st.form("form_vacaciones"):
                col_fecha1, col_fecha2 = st.columns(2)
                with col_fecha1:
                    fecha_inicio = st.date_input("Fecha de Inicio")
                with col_fecha2:
                    fecha_fin = st.date_input("Fecha de Fin")
                    
                comentarios = st.text_area("Comentarios o detalles adicionales (Opcional)")
                btn_solicitar = st.form_submit_button("Enviar Solicitud a RRHH")
                
                if btn_solicitar:
                    if fecha_inicio >= fecha_fin:
                        st.error("⚠️ La fecha de fin debe ser posterior a la fecha de inicio.")
                    else:
                        mes_nombre = meses_esp[fecha_inicio.month]
                        nueva_solicitud = {
                            "Código": st.session_state.codigo_actual,
                            "Nombre": usuario['nombre'],
                            "Mes": mes_nombre,
                            "Año": fecha_inicio.year,
                            "Inicio": str(fecha_inicio),
                            "Fin": str(fecha_fin),
                            "Comentarios": comentarios,
                            "Estado": "Pendiente"
                        }
                        st.session_state.solicitudes.append(nueva_solicitud)
                        st.success("✅ ¡Solicitud enviada con éxito!")

        with tab_estado:
            mis_solicitudes = [s for s in st.session_state.solicitudes if s["Código"] == st.session_state.codigo_actual]
            
            if len(mis_solicitudes) == 0:
                st.info("No has realizado ninguna solicitud de vacaciones aún.")
            else:
                df_mis_sol = pd.DataFrame(mis_solicitudes)
                traductor_estados = {"Pendiente": "En revisión ⏳", "Aprobado": "Aprobada ✅", "Rechazado": "Rechazada ❌"}
                df_mis_sol["Estado"] = df_mis_sol["Estado"].map(traductor_estados)
                df_mis_sol = df_mis_sol[["Inicio", "Fin", "Comentarios", "Estado"]]
                st.dataframe(df_mis_sol, hide_index=True, use_container_width=True)
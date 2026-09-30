"""Portal del colaborador: Streamlit entry point."""
import logging
import time
from datetime import date
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
import database as db
import service
import erp_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('portal')
st.set_page_config(page_title='Portal del Colaborador',page_icon='🏢',layout='wide')


@st.cache_resource
def connection():
    return db.get_engine(st.secrets['DATABASE_URL'])


@st.cache_resource
def initialize():
    engine = connection()
    db.migrate(engine)
    db.sync_users(engine,st.secrets.get('usuarios',{}))
    return True


def logout():
    st.session_state.clear()
    st.rerun()


def main():
    try:
        initialize()  # Always BEFORE querying users / authentication.
        engine = connection()
    except Exception as exc:
        # Do not log SQL params, connection URLs or raw exception messages.
        logger.error('Initialization failed: %s',type(exc).__name__)
        st.error('No se pudo iniciar el portal. RR. HH. debe revisar la configuración y la base de datos.')
        return
    session = st.session_state.get('identity')
    now = time.monotonic()
    if session and (now-st.session_state.get('last_activity',now)>300 or now-st.session_state.get('started',now)>28800):
        logout()
    if not session:
        st.title('🏢 Portal Interno')
        with st.form('login',clear_on_submit=True):
            code = st.text_input('Código de colaborador',max_chars=50)
            password = st.text_input('Contraseña',type='password',max_chars=256)
            submit = st.form_submit_button('Ingresar al Portal')
        if submit:
            try:
                identity = service.login(engine,code,password)
                if identity:
                    st.session_state.identity = identity
                    st.session_state.last_activity = now
                    st.session_state.started = now
                    st.rerun()
                else:
                    st.error('No se pudo ingresar. Revisa tus credenciales o espera cinco minutos antes de volver a intentar.')
            except Exception as exc:
                logger.error('Login failed: %s',type(exc).__name__)
                st.error('Servicio temporalmente no disponible.')
        return
    try:
        with engine.connect() as c:
            user = dict(service.actor(c,session))
    except ValueError:
        logout()
    st.session_state.last_activity = now
    if st.button('🚪 Cerrar Sesión'):
        logout()
    if user['es_admin']:
        admin(engine,session)
    else:
        employee(engine,session,user)


def admin(engine,session):
    st.title('⚙️ Panel de Administración')
    rows = db.read(engine,'SELECT * FROM solicitudes ORDER BY creado_en DESC LIMIT 1000')
    df = pd.DataFrame(rows)
    st.caption('Hasta 1000 solicitudes recientes. Solo se resuelven solicitudes pendientes.')
    if not df.empty:
        a,b = st.columns(2)
        month = a.selectbox('Mes',['Todos']+sorted(df.mes_inicio.unique().tolist()))
        year = b.selectbox('Año',['Todos']+sorted(df.anio_inicio.unique().tolist()))
        visible = df
        if month!='Todos':
            visible = visible[visible.mes_inicio==month]
        if year!='Todos':
            visible = visible[visible.anio_inicio==year]
        edited = st.data_editor(visible,hide_index=True,disabled=[c for c in df.columns if c!='estado'],
            column_config={'estado':st.column_config.SelectboxColumn('Estado',options=['Pendiente','Aprobado','Rechazado'],required=True)})
        if st.button('Guardar cambios de estado'):
            count = 0
            for row in edited.to_dict('records'):
                original = next(r for r in rows if r['id']==row['id'])
                if row['estado']!=original['estado']:
                    try:
                        service.change_status(engine,session,row['id'],original['estado'],row['estado'])
                        count+=1
                    except ValueError as exc:
                        st.error(str(exc))
            st.success(f'{count} cambio(s) guardados. Actualiza la vista para consultar el resultado.')
    else:
        st.info('Sin solicitudes registradas.')
    st.subheader('Cupo de vacaciones')
    st.caption('Cupo acumulado administrado por RR. HH.; se descuentan todas las solicitudes pendientes y aprobadas del portal. No es un saldo anual automático ni el saldo del ERP.')
    users = db.read(engine,'SELECT codigo,nombre,cupo_vacaciones FROM usuarios WHERE activo ORDER BY codigo')
    if users:
        st.dataframe(pd.DataFrame(users),hide_index=True)
        with st.form('budget'):
            code = st.selectbox('Colaborador',[u['codigo'] for u in users])
            budget = st.number_input('Nuevo cupo acumulado (días calendario)',min_value=0,max_value=10000,step=1)
            if st.form_submit_button('Registrar cupo'):
                try:
                    service.set_budget(engine,session,code,int(budget))
                    st.success('Cupo actualizado.')
                except ValueError as exc:
                    st.error(str(exc))


def employee(engine,session,user):
    data = None
    try:
        data = erp_client.obtener_colaborador(user['codigo'])
    except erp_client.ERPError:
        st.warning('El ERP no confirmó los datos. Se muestra la ficha local; el saldo del portal lo administra RR. HH.')
    profile = {**user,**{k:v for k,v in (data or {}).items() if v is not None}}
    st.title(f'👋 Bienvenido, {profile["nombre"]}')
    a,b = st.columns(2)
    a.subheader('Mis Datos')
    a.write(f'Cargo: {profile.get("cargo","")}')
    a.write(f'Estado del contrato: {profile.get("estado_contrato","Sin confirmación ERP")}')
    if profile.get('fecha_ingreso'):
        delta = relativedelta(service.today(),profile['fecha_ingreso'])
        a.write(f'Antigüedad: {delta.years} años, {delta.months} meses y {delta.days} días')
    with engine.connect() as c:
        balance = service.remaining(c,user)
    b.metric('Días disponibles en el portal','Sin registrar' if balance is None else balance)
    b.caption('Se descuentan solicitudes pendientes y aprobadas. Días calendario.')
    if data and data.get('dias_vacaciones') is not None:
        b.caption(f'Saldo informativo ERP: {data["dias_vacaciones"]}. No sincronizado con las aprobaciones del portal.')
    request,history = st.tabs(['✈️ Solicitar Vacaciones','📋 Mis Solicitudes'])
    with request:
        with st.form('vacaciones',clear_on_submit=True):
            start = st.date_input('Fecha de inicio',value=service.today())
            end = st.date_input('Fecha de fin',value=service.today())
            comment = st.text_area('Comentarios',max_chars=300)
            if st.form_submit_button('Enviar solicitud',disabled=balance is None):
                try:
                    ident = service.request_vacation(engine,session,start,end,comment)
                    st.success(f'Solicitud #{ident} enviada a RR. HH.')
                except ValueError as exc:
                    st.error(str(exc))
    with history:
        rows = db.read(engine,'SELECT fecha_inicio,fecha_fin,dias,comentarios,estado FROM solicitudes WHERE codigo=:c ORDER BY fecha_inicio DESC',{'c':user['codigo']})
        if rows:
            st.dataframe(pd.DataFrame(rows),hide_index=True)
        else:
            st.info('No has realizado solicitudes todavía.')


try:
    main()
except Exception as exc:
    logger.error('Portal operation failed: %s',type(exc).__name__)
    st.error('No se pudo completar la operación. Actualiza la página o consulta con RR. HH.')

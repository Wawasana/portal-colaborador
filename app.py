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
    if 'notice' in st.session_state:
        st.success(st.session_state.pop('notice'))
    if st.button('🚪 Cerrar Sesión'):
        logout()
    if user['es_admin']:
        admin(engine,session)
    else:
        employee(engine,session,user)


def period_label(p):
    return f"#{p['id']} · {p['inicio']:%d/%m/%Y} a {p['fin']:%d/%m/%Y} · {p['disponibles']} días habilitados"


def period_table(rows):
    return pd.DataFrame([{'Período':p['id'],'Desde':p['inicio'],'Hasta':p['fin'],
        'Derecho anual desde':p['habilita'],'Récord validado':p['record_validado'],
        'Días anuales':p['generados'],'Adelanto habilitado':p['adelanto_habilitado'],
        'Reservados pendientes':p['pendientes'],'Aprobados por disfrutar':p['aprobados'],
        'Disfrutados':p['disfrutados'],'Saldo sin usar ni reservar':p['sin_usar'],
        'Disponibles para solicitar':p['disponibles'],'Límite de goce':p['limite'],
        'Alerta':p['alerta']} for p in rows])


def run_action(action):
    try:
        action()
        st.session_state.notice = 'Cambio registrado.'
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))


def admin(engine,session):
    st.title('⚙️ Panel de Administración')
    users = db.read(engine,'SELECT * FROM usuarios WHERE NOT es_admin ORDER BY codigo')
    summaries = {}
    with engine.begin() as c:
        for u in users:
            c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':u['codigo']})
            service.ensure_periods(c,u)
            summaries[u['codigo']] = service.vacation_summary(c,u)
    st.subheader('Saldo por colaborador y período anual')
    st.caption('Política de la empresa: dos bloques de 15 días o 30 días completos por año de servicio, con récord validado. Adelanto de 15 días desde seis meses, sujeto a acuerdo escrito. Los días anteriores se conservan.')
    balances = [{'Código':u['codigo'],'Nombre':u['nombre'],'Activo':u['activo'],
        'Ingreso continuo':u['fecha_ingreso'],'Último período anual habilitado':summaries[u['codigo']]['actual'],
        'Períodos anteriores disponibles':summaries[u['codigo']]['anteriores'],
        'Adelanto disponible':summaries[u['codigo']]['adelanto'],
        'Solicitudes sin período':summaries[u['codigo']]['sin_asignar']} for u in users]
    if balances:
        st.dataframe(pd.DataFrame(balances),hide_index=True)
        selected = st.selectbox('Colaborador',users,format_func=lambda u:f"{u['codigo']} — {u['nombre']}")
        periods = summaries[selected['codigo']]['periodos']
        if periods:
            st.dataframe(period_table(periods),hide_index=True)
            for p in periods:
                if p['alerta']:
                    st.warning(f"Período #{p['id']}: {p['alerta']}")
            with st.expander('Validar récord vacacional de un período'):
                with st.form('record'):
                    p = st.selectbox('Período a validar',periods,format_func=period_label)
                    evidence = st.text_input('Referencia de la revisión de asistencia y récord',max_chars=300)
                    confirmed = st.checkbox('Confirmo que RR. HH. verificó el régimen general y el récord vacacional exigido.')
                    if st.form_submit_button('Validar récord'):
                        if not confirmed:
                            st.error('Confirma la revisión del régimen y el récord vacacional.')
                        else:
                            run_action(lambda:service.validate_record(engine,session,p['id'],evidence))
        else:
            st.info('Falta una fecha de ingreso válida. Configúrala en Secrets y reinicia la aplicación.')
    rows = db.read(engine,'SELECT * FROM solicitudes ORDER BY creado_en DESC LIMIT 1000')
    st.subheader('Solicitudes de vacaciones')
    if not rows:
        st.info('Sin solicitudes registradas.')
        return
    df = pd.DataFrame(rows)
    for column in ('creado_en','actualizado_en'):
        df[column] = pd.to_datetime(df[column],utc=True).dt.tz_convert('America/Lima').dt.tz_localize(None)
    a,b,d = st.columns(3)
    month = a.selectbox('Mes de inicio',['Todos']+[m for m in service.MONTHS if m in df.mes_inicio.tolist()])
    year = b.selectbox('Año de inicio',['Todos']+sorted(df.anio_inicio.unique().tolist()))
    code = d.selectbox('Filtrar colaborador',['Todos']+sorted(df.codigo.unique().tolist()))
    visible = df
    if month!='Todos': visible=visible[visible.mes_inicio==month]
    if year!='Todos': visible=visible[visible.anio_inicio==year]
    if code!='Todos': visible=visible[visible.codigo==code]
    edited=st.data_editor(visible,hide_index=True,disabled=[c for c in df.columns if c!='estado'],column_config={
        'estado':st.column_config.SelectboxColumn('Estado',options=['Pendiente','Aprobado','Rechazado','Cancelada','Disfrutada'],required=True),
        'periodo_id':st.column_config.NumberColumn('Período anual',format='%d'),
        'creado_en':st.column_config.DatetimeColumn('Fecha de creación (Perú)',format='DD/MM/YYYY HH:mm:ss'),
        'actualizado_en':st.column_config.DatetimeColumn('Última actualización (Perú)',format='DD/MM/YYYY HH:mm:ss')})
    st.caption('Aprobación reserva días. “Disfrutada” requiere confirmar que se tomó todo el descanso, después de la fecha de fin. Cancelar una aprobación solo es posible antes del inicio.')
    if st.button('Guardar cambios de estado'):
        count=0
        for row in edited.to_dict('records'):
            original=next(r for r in rows if r['id']==row['id'])
            if row['estado']!=original['estado']:
                try:
                    service.change_status(engine,session,row['id'],original['estado'],row['estado'])
                    count+=1
                except ValueError as exc:
                    st.error(f"Solicitud #{row['id']}: {exc}")
        if count:
            st.session_state.notice=f'{count} cambio(s) guardados.'
            st.rerun()
    pending=[r for r in rows if r['estado']=='Pendiente']
    if pending:
        with st.expander('Registrar referencia del acuerdo escrito'):
            st.caption('El acuerdo se conserva en el archivo de RR. HH. Esta referencia y la casilla del colaborador no sustituyen un documento firmado. Debe existir antes del descanso.')
            with st.form('agreement'):
                r=st.selectbox('Solicitud',pending,format_func=lambda r:f"#{r['id']} — {r['nombre']} — {r['dias']} días")
                reference=st.text_input('Referencia o ubicación del acuerdo firmado',max_chars=500)
                confirm=st.checkbox('He verificado el acuerdo escrito y las fechas del descanso.')
                if st.form_submit_button('Registrar acuerdo'):
                    if not confirm:
                        st.error('Confirma que verificaste el acuerdo escrito.')
                    else:
                        run_action(lambda:service.register_agreement(engine,session,r['id'],reference))
    historical=[r for r in rows if r['periodo_id'] is None and r['estado'] in ('Pendiente','Aprobado','Disfrutada')]
    if historical:
        st.warning('Hay solicitudes anteriores sin período. Se conserva su historial y se bloquean nuevas solicitudes de esas personas hasta conciliarlas.')
        with st.expander('Asignar solicitudes anteriores a su período'):
            r=st.selectbox('Solicitud histórica',historical,format_func=lambda r:f"#{r['id']} — {r['nombre']} — {r['dias']} días")
            options=summaries.get(r['codigo'],{}).get('periodos',[])
            if options:
                with st.form('historical'):
                    p=st.selectbox('Período confirmado por RR. HH.',options,format_func=period_label)
                    block=st.selectbox('Bloque de vacaciones',['Completo','Principal','Flexible'])
                    reason=st.text_input('Referencia de conciliación',max_chars=300)
                    if st.form_submit_button('Asignar período'):
                        run_action(lambda:service.assign_historical(engine,session,r['id'],p['id'],block,reason))


def employee(engine,session,user):
    data=None
    try:
        data=erp_client.obtener_colaborador(user['codigo'])
    except erp_client.ERPError:
        st.warning('El ERP no confirmó los datos. Se muestra la ficha local.')
    profile={**user,**{k:v for k,v in (data or {}).items() if v is not None}}
    st.title(f'👋 Bienvenido, {profile["nombre"]}')
    st.write(f'Cargo: {profile.get("cargo", "")}')
    st.write(f'Estado del contrato: {profile.get("estado_contrato", "Sin confirmación ERP")}')
    with engine.begin() as c:
        c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':user['codigo']})
        service.ensure_periods(c,user)
        summary=service.vacation_summary(c,user)
    a,b,d=st.columns(3)
    a.metric('Último período anual habilitado',summary['actual'])
    b.metric('Períodos anteriores disponibles',summary['anteriores'])
    d.metric('Adelanto disponible',summary['adelanto'])
    st.caption('Días calendario. Pendientes y aprobados reservan saldo; disfrutados registran el uso efectivo. Los saldos se recalculan al actualizar la página.')
    if summary['disponibles'] is None:
        st.warning('RR. HH. debe verificar tu fecha de ingreso y asignar tus solicitudes anteriores a su período antes de nuevas solicitudes.')
    if summary['periodos']:
        st.dataframe(period_table(summary['periodos']),hide_index=True)
        for p in summary['periodos']:
            if p['alerta']: st.warning(f"Período #{p['id']}: {p['alerta']}")
    request,history=st.tabs(['✈️ Solicitar Vacaciones','📋 Mis Solicitudes'])
    with request:
        options=[p for p in summary['periodos'] if p['disponibles']>0 and not p['inconsistente']]
        if not options or summary['disponibles'] is None:
            st.info('No hay saldo habilitado para una nueva solicitud. Revisa la tabla o consulta con RR. HH.')
        else:
            with st.form('vacaciones',clear_on_submit=True):
                p=st.selectbox('Período del que se descontarán los días (más antiguo primero)',options,format_func=period_label)
                block=st.selectbox('Bloque',['Principal','Flexible','Completo'],format_func=lambda b:{'Principal':'Primer bloque — 15 días','Flexible':'Segundo bloque — 15 días','Completo':'Descanso completo — 30 días'}[b])
                st.caption('Cada año de servicio: dos bloques de 15 días calendario consecutivos o un descanso de 30 días. Desde los seis meses: adelanto del primer bloque de 15 días. Al cumplir el año y validar el récord: los 15 restantes, o 30 si no hubo adelanto. Requiere acuerdo escrito y aprobación de RR. HH.')
                start=st.date_input('Fecha de inicio',value=service.today())
                end=st.date_input('Fecha de fin',value=service.today())
                comment=st.text_area('Comentarios',max_chars=300)
                accepts=st.checkbox('Solicito el adelanto o fraccionamiento indicado y gestionaré con RR. HH. el acuerdo escrito antes del descanso.')
                if st.form_submit_button('Enviar solicitud'):
                    run_action(lambda:service.request_vacation(engine,session,start,end,comment,p['id'],block,accepts))
    with history:
        rows=db.read(engine,'SELECT id,periodo_id,bloque,modalidad,fecha_inicio,fecha_fin,dias,comentarios,estado FROM solicitudes WHERE codigo=:c ORDER BY fecha_inicio DESC',{'c':user['codigo']})
        if rows: st.dataframe(pd.DataFrame(rows),hide_index=True)
        else: st.info('No has realizado solicitudes todavía.')


try:
    main()
except Exception as exc:
    logger.error('Portal operation failed: %s',type(exc).__name__)
    st.error('No se pudo completar la operación. Actualiza la página o consulta con RR. HH.')

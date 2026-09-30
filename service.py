"""Transactional authentication and vacation rules, independent of Streamlit."""
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import text
from auth import verify_password, DUMMY_HASH, PH
from database import audit
from periods import annual_periods, capacity, validate_block, ACTIVE

MONTHS = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']


def today():
    return datetime.now(ZoneInfo('America/Lima')).date()


def login(engine, code, password):
    code = code.strip().upper()
    key = hashlib.sha256(code.encode()).hexdigest()
    with engine.begin() as c:
        # Serialize each account across all browsers and workers. Global limit also
        # bounds password spraying and Argon2 work; unavailable DB never permits login.
        c.execute(text("DELETE FROM login_intentos WHERE ventana < NOW() - INTERVAL '1 day' AND (bloqueado_hasta IS NULL OR bloqueado_hasta < NOW())"))
        for bucket in ('GLOBAL', key):
            c.execute(text('INSERT INTO login_intentos(codigo) VALUES(:k) ON CONFLICT DO NOTHING'), {'k':bucket})
        rows = {}
        for bucket in ('GLOBAL', key):
            rows[bucket] = c.execute(text('SELECT *, NOW() AS ahora FROM login_intentos WHERE codigo=:k FOR UPDATE'), {'k':bucket}).mappings().one()
        for bucket, row in rows.items():
            if row['bloqueado_hasta'] and row['bloqueado_hasta'] > row['ahora']:
                return None
            if row['ventana'] < row['ahora'] - timedelta(minutes=5):
                c.execute(text('UPDATE login_intentos SET fallos=0,ventana=NOW(),bloqueado_hasta=NULL WHERE codigo=:k'), {'k':bucket})
        user = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c FOR UPDATE'), {'c':code}).mappings().first()
        valid = verify_password(password, user['password_hash'] if user else DUMMY_HASH)
        if not user or not user['activo'] or not valid:
            for bucket, limit in (('GLOBAL',100),(key,5)):
                c.execute(text("""UPDATE login_intentos SET fallos=fallos+1,
                    bloqueado_hasta=CASE WHEN fallos+1>=:lim THEN NOW()+INTERVAL '5 minutes' ELSE NULL END WHERE codigo=:k"""), {'k':bucket,'lim':limit})
            audit(c, 'ANONIMO', 'Inicio de sesión rechazado')
            return None
        if PH.check_needs_rehash(user['password_hash']):
            c.execute(text('UPDATE usuarios SET password_hash=:h WHERE codigo=:c'), {'c':code,'h':PH.hash(password)})
        c.execute(text('UPDATE login_intentos SET fallos=0,bloqueado_hasta=NULL WHERE codigo=:k'), {'k':key})
        audit(c, code, 'Inicio de sesión exitoso')
        return {'codigo':code,'revision':user['cred_revision']}


def actor(c, session, admin=False):
    u = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c'), {'c':session['codigo']}).mappings().first()
    if not u or not u['activo'] or u['cred_revision'] != session['revision'] or (admin and not u['es_admin']):
        raise ValueError('Sesión sin autorización; vuelve a ingresar.')
    return u


def ensure_periods(c, user, now=None):
    current = now or today()
    for p in annual_periods(user['fecha_ingreso'], current):
        c.execute(text("""INSERT INTO periodos_vacaciones(codigo,inicio,fin,habilita,adelanto_desde,limite)
            VALUES(:codigo,:inicio,:fin,:habilita,:adelanto_desde,:limite) ON CONFLICT(codigo,inicio) DO NOTHING"""),
            dict(p, codigo=user['codigo']))
    return period_summary(c, user, current)


def period_summary(c, user, now=None):
    current = now or today()
    rows = [dict(r) for r in c.execute(text('SELECT * FROM periodos_vacaciones WHERE codigo=:c ORDER BY inicio'),
            {'c':user['codigo']}).mappings()]
    requests = [dict(r) for r in c.execute(text('SELECT * FROM solicitudes WHERE codigo=:c'),
            {'c':user['codigo']}).mappings()]
    expected = {p['inicio'] for p in annual_periods(user['fecha_ingreso'], current)}
    for p in rows:
        p['inconsistente'] = p['inicio'] not in expected
        used = [r for r in requests if r['periodo_id']==p['id'] and r['estado'] in ACTIVE]
        p['generados'] = 30 if current >= p['habilita'] else 0
        p['adelanto_habilitado'] = 15 if p['adelanto_desde'] <= current < p['habilita'] else 0
        p['pendientes'] = sum(r['dias'] for r in used if r['estado']=='Pendiente')
        p['aprobados'] = sum(r['dias'] for r in used if r['estado']=='Aprobado')
        p['disfrutados'] = sum(r['dias'] for r in used if r['estado']=='Disfrutada')
        p['sin_usar'] = (30 if p['generados'] else p['adelanto_habilitado']) - sum(r['dias'] for r in used)
        p['disponibles'] = capacity(p,current) - sum(r['dias'] for r in used)
        if p['inconsistente']:
            p['disponibles'] = 0
        if p['inconsistente']:
            p['alerta'] = 'Fecha de ingreso modificada: requiere conciliación'
        elif any(r['estado']=='Disfrutada' and r['fecha_fin']>=p['limite'] for r in used) or (current >= p['limite'] and p['generados'] > p['disfrutados']):
            p['alerta'] = 'Fuera de plazo: RR. HH. debe revisar descanso e indemnización'
        elif current >= p['limite'] - timedelta(days=60) and p['generados'] > p['disfrutados']:
            p['alerta'] = 'Próximo al límite para disfrutar vacaciones'
        elif p['generados'] and not p['record_validado']:
            p['alerta'] = 'Validar récord vacacional con RR. HH.'
        else:
            p['alerta'] = ''
    return rows


def vacation_summary(c, user, now=None):
    current = now or today()
    rows = period_summary(c,user,current)
    unassigned = c.execute(text("SELECT COUNT(*) FROM solicitudes WHERE codigo=:c AND periodo_id IS NULL AND estado IN ('Pendiente','Aprobado','Disfrutada')"),{'c':user['codigo']}).scalar_one()
    blocked = bool(unassigned or any(p['inconsistente'] for p in rows))
    mature = [p for p in rows if p['habilita']<=current and not p['inconsistente']]
    recent = mature[-1]['id'] if mature else None
    return {'generados':sum(p['generados'] for p in rows),
        'aprobados':sum(p['aprobados'] for p in rows), 'pendientes':sum(p['pendientes'] for p in rows),
        'disfrutados':sum(p['disfrutados'] for p in rows),
        'disponibles':None if blocked or not user['fecha_ingreso'] else sum(max(0,p['disponibles']) for p in rows),
        'actual':sum(max(0,p['disponibles']) for p in rows if p['id']==recent),
        'anteriores':sum(max(0,p['disponibles']) for p in mature if p['id']!=recent),
        'adelanto':sum(max(0,p['disponibles']) for p in rows if p['habilita']>current),
        'sin_asignar':unassigned, 'periodos':rows, 'legacy':not user['fecha_ingreso'],
        'proxima_fecha':next((p['habilita'] for p in rows if p['habilita']>current),None)}


def remaining(c, user):
    return vacation_summary(c, user)['disponibles']


def validate_dates(start, end, comments, now=None):
    days = (end-start).days+1
    if start < (now or today()) or not 1 <= days <= 30 or len(comments) > 300:
        raise ValueError('Fechas inválidas: solicita entre 1 y 30 días calendario, sin fechas pasadas ni comentarios de más de 300 caracteres.')
    return days


def reserve(c, user, start, end, days, exclude=0):
    balance = remaining(c,user)
    if balance is None or days > balance:
        raise ValueError('Saldo no registrado o insuficiente. Consulta con RR. HH.')
    overlap = c.execute(text("""SELECT 1 FROM solicitudes WHERE codigo=:c AND id<>:id
        AND estado IN ('Pendiente','Aprobado','Disfrutada') AND fecha_inicio<=:fin AND fecha_fin>=:ini LIMIT 1"""),
        {'c':user['codigo'],'id':exclude,'ini':start,'fin':end}).first()
    if overlap:
        raise ValueError('Ya existe una solicitud pendiente o aprobada para esas fechas.')


def request_vacation(engine, session, start, end, comments, period_id=None, block='Flexible', accepts=False):
    days = validate_dates(start,end,comments)
    with engine.begin() as c:
        c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'), {'c':session['codigo']})
        u = actor(c,session)
        if not u['fecha_ingreso']:
            raise ValueError('RR. HH. debe registrar la fecha de ingreso continuo.')
        rows = ensure_periods(c,u)
        if vacation_summary(c,u)['disponibles'] is None:
            raise ValueError('RR. HH. debe conciliar las solicitudes históricas o la fecha de ingreso.')
        eligible = [p for p in rows if p['disponibles']>=days and not p['inconsistente']]
        if period_id is None:
            period_id = eligible[0]['id'] if eligible else None
        p = next((p for p in rows if p['id']==period_id),None)
        if not p or p['disponibles']<days or p['inconsistente']:
            raise ValueError('Saldo insuficiente o récord vacacional sin validar en el período seleccionado.')
        advance = today() < p['habilita']
        if advance and (days!=15 or block!='Principal' or end>=p['habilita']):
            raise ValueError('El adelanto semestral es de 15 días consecutivos y termina antes del aniversario anual.')
        if not accepts:
            raise ValueError('Debes solicitar el acuerdo escrito de adelanto o fraccionamiento antes del descanso.')
        existing = [dict(r) for r in c.execute(text("SELECT dias,bloque FROM solicitudes WHERE periodo_id=:p AND estado IN ('Pendiente','Aprobado','Disfrutada')"),{'p':p['id']}).mappings()]
        validate_block(block,days,existing)
        reserve(c,u,start,end,days)
        ident = c.execute(text("""INSERT INTO solicitudes(codigo,nombre,mes_inicio,anio_inicio,fecha_inicio,fecha_fin,dias,comentarios,periodo_id,bloque,modalidad,acepta_acuerdo)
            VALUES(:c,:n,:m,:a,:ini,:fin,:d,:co,:p,:b,:mode,TRUE) RETURNING id"""),
            {'c':u['codigo'],'n':u['nombre'],'m':MONTHS[start.month-1],'a':start.year,'ini':start,'fin':end,
             'd':days,'co':comments.strip(),'p':p['id'],'b':block,'mode':'Adelanto' if advance else 'Anual'}).scalar_one()
        audit(c,u['codigo'],f'Solicitud #{ident} creada ({days} días), período #{p["id"]}, {block}')
        return ident


def change_status(engine, session, ident, expected, status):
    transitions = {'Pendiente':('Aprobado','Rechazado','Cancelada'), 'Aprobado':('Cancelada','Disfrutada')}
    if status not in transitions.get(expected,()):
        raise ValueError('Transición de estado no permitida.')
    with engine.begin() as c:
        actor(c,session,admin=True)
        code = c.execute(text('SELECT codigo FROM solicitudes WHERE id=:id'),{'id':ident}).scalar_one()
        c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':code})
        actor(c,session,admin=True)
        row = c.execute(text('SELECT * FROM solicitudes WHERE id=:id FOR UPDATE'),{'id':ident}).mappings().one()
        if row['estado'] != expected:
            raise ValueError('La solicitud cambió en otra sesión. Actualiza la página.')
        u = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c'),{'c':code}).mappings().one()
        if status == 'Aprobado':
            if not u['activo'] or row['fecha_inicio']<today():
                raise ValueError('No se puede aprobar una solicitud pasada o de un colaborador inactivo.')
            p = next((p for p in period_summary(c,u) if p['id']==row['periodo_id']),None)
            if not p or p['inconsistente'] or p['disponibles']<0:
                raise ValueError('Asigna un período válido y verifica el saldo.')
            if today()>=p['habilita'] and not p['record_validado']:
                raise ValueError('Valida el récord vacacional antes de aprobar.')
            if row['modalidad']=='Historica' and today()<p['habilita']:
                raise ValueError('Una solicitud histórica pendiente antes del año debe reemplazarse por un adelanto válido de 15 días.')
            if not row['acuerdo'].strip():
                raise ValueError('Registra la referencia del acuerdo escrito antes de aprobar.')
        if status == 'Cancelada' and expected=='Aprobado' and row['fecha_inicio']<=today():
            raise ValueError('Solo se cancela una aprobación antes del inicio; un descanso iniciado requiere revisión de RR. HH.')
        if status == 'Disfrutada' and (row['fecha_fin']>=today() or row['periodo_id'] is None):
            raise ValueError('Confirma el descanso después de la fecha de fin y con el período asignado.')
        c.execute(text('UPDATE solicitudes SET estado=:s,actualizado_en=NOW() WHERE id=:id'),{'s':status,'id':ident})
        audit(c,session['codigo'],f'Solicitud #{ident}: {expected} -> {status}')


def validate_record(engine, session, period_id, evidence):
    if not 5 <= len(evidence.strip()) <= 300:
        raise ValueError('Indica una referencia de validación de entre 5 y 300 caracteres.')
    with engine.begin() as c:
        actor(c,session,admin=True)
        code = c.execute(text('SELECT codigo FROM periodos_vacaciones WHERE id=:p'),{'p':period_id}).scalar_one()
        u = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':code}).mappings().one()
        actor(c,session,admin=True)
        p = next(p for p in period_summary(c,u) if p['id']==period_id)
        if today()<p['habilita'] or p['inconsistente']:
            raise ValueError('El período no ha cumplido un año o no coincide con la fecha de ingreso.')
        c.execute(text('UPDATE periodos_vacaciones SET record_validado=TRUE,validacion_referencia=:e,validado_por=:a,validado_en=NOW() WHERE id=:p'),{'p':period_id,'e':evidence.strip(),'a':session['codigo']})
        audit(c,session['codigo'],f'Récord validado período #{period_id}: {evidence.strip()}')


def register_agreement(engine, session, ident, reference):
    if not 5 <= len(reference.strip()) <= 500:
        raise ValueError('Indica la referencia del acuerdo escrito (5 a 500 caracteres).')
    with engine.begin() as c:
        actor(c,session,admin=True)
        code = c.execute(text('SELECT codigo FROM solicitudes WHERE id=:id'),{'id':ident}).scalar_one()
        c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':code})
        actor(c,session,admin=True)
        row = c.execute(text('SELECT * FROM solicitudes WHERE id=:id FOR UPDATE'),{'id':ident}).mappings().one()
        if row['estado']!='Pendiente':
            raise ValueError('El acuerdo se registra antes de aprobar la solicitud.')
        c.execute(text('UPDATE solicitudes SET acuerdo=:a,actualizado_en=NOW() WHERE id=:id'),{'a':reference.strip(),'id':ident})
        audit(c,session['codigo'],f'Acuerdo registrado solicitud #{ident}: {reference.strip()}')


def assign_historical(engine, session, ident, period_id, block, reason):
    if not 5<=len(reason.strip())<=300:
        raise ValueError('Indica el motivo o referencia de conciliación (5 a 300 caracteres).')
    with engine.begin() as c:
        actor(c,session,admin=True)
        code = c.execute(text('SELECT codigo FROM solicitudes WHERE id=:id'),{'id':ident}).scalar_one()
        u = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':code}).mappings().one()
        actor(c,session,admin=True)
        row = c.execute(text('SELECT * FROM solicitudes WHERE id=:id FOR UPDATE'),{'id':ident}).mappings().one()
        if row['periodo_id'] is not None:
            raise ValueError('La solicitud ya tiene un período; no se reasigna automáticamente.')
        p = next((p for p in ensure_periods(c,u) if p['id']==period_id),None)
        if not p or p['inconsistente']:
            raise ValueError('Período inválido para este colaborador.')
        existing = [dict(r) for r in c.execute(text("SELECT dias,bloque FROM solicitudes WHERE periodo_id=:p AND estado IN ('Pendiente','Aprobado','Disfrutada')"),{'p':period_id}).mappings()]
        validate_block(block,row['dias'],existing)
        if row['estado'] in ACTIVE and sum(r['dias'] for r in existing)+row['dias']>30:
            raise ValueError('La conciliación excede los 30 días del período.')
        c.execute(text('UPDATE solicitudes SET periodo_id=:p,bloque=:b,conciliacion_referencia=:r,actualizado_en=NOW() WHERE id=:id'),{'p':period_id,'b':block,'id':ident,'r':reason.strip()})
        audit(c,session['codigo'],f'Conciliación #{ident} período #{period_id}, {block}: {reason.strip()}')



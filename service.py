"""Transactional authentication and vacation rules, independent of Streamlit."""
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from auth import verify_password, DUMMY_HASH, PH
from database import audit

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


def entitlement(start, now=None):
    """15 days for each completed six-month anniversary; no future credit."""
    current = now or today()
    if start is None:
        return None, None
    if start > current:
        return 0, start + relativedelta(months=6)
    months = (current.year-start.year)*12 + current.month-start.month
    blocks = months // 6
    if start + relativedelta(months=blocks*6) > current:
        blocks -= 1
    return blocks*15, start + relativedelta(months=(blocks+1)*6)


def vacation_summary(c, user, now=None):
    generated, next_date = entitlement(user['fecha_ingreso'], now)
    # Preserve historical manual accounts without a start date during migration.
    legacy = user['fecha_ingreso'] is None and user['cupo_vacaciones'] is not None
    if legacy:
        generated = user['cupo_vacaciones']
    totals = c.execute(text("""SELECT
        COALESCE(SUM(dias) FILTER (WHERE estado='Aprobado'),0) AS approved,
        COALESCE(SUM(dias) FILTER (WHERE estado='Pendiente'),0) AS pending
        FROM solicitudes WHERE codigo=:c"""), {'c':user['codigo']}).mappings().one()
    return {'generados':generated, 'aprobados':totals['approved'],
            'pendientes':totals['pending'], 'disponibles':None if generated is None else generated-totals['approved']-totals['pending'],
            'proxima_fecha':next_date, 'legacy':legacy}


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
        AND estado IN ('Pendiente','Aprobado') AND fecha_inicio<=:fin AND fecha_fin>=:ini LIMIT 1"""),
        {'c':user['codigo'],'id':exclude,'ini':start,'fin':end}).first()
    if overlap:
        raise ValueError('Ya existe una solicitud pendiente o aprobada para esas fechas.')


def request_vacation(engine, session, start, end, comments):
    days = validate_dates(start,end,comments)
    with engine.begin() as c:
        # All reservations, approvals and balance changes use this employee lock.
        c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'), {'c':session['codigo']})
        u = actor(c,session)
        reserve(c,u,start,end,days)
        ident = c.execute(text('''INSERT INTO solicitudes(codigo,nombre,mes_inicio,anio_inicio,fecha_inicio,fecha_fin,dias,comentarios)
            VALUES(:c,:n,:m,:a,:ini,:fin,:d,:co) RETURNING id'''),
            {'c':u['codigo'],'n':u['nombre'],'m':MONTHS[start.month-1],'a':start.year,'ini':start,'fin':end,'d':days,'co':comments.strip()}).scalar_one()
        audit(c,u['codigo'],f'Solicitud #{ident} creada ({days} días)')
        return ident


def change_status(engine, session, ident, expected, status):
    if expected != 'Pendiente' or status not in ('Aprobado','Rechazado'):
        raise ValueError('Solo se puede aprobar o rechazar una solicitud pendiente.')
    with engine.begin() as c:
        actor(c,session,admin=True)
        code = c.execute(text('SELECT codigo FROM solicitudes WHERE id=:id'),{'id':ident}).scalar_one()
        c.execute(text('SELECT codigo FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':code})
        # Authorization again after waiting for the employee lock.
        actor(c,session,admin=True)
        row = c.execute(text('SELECT * FROM solicitudes WHERE id=:id FOR UPDATE'),{'id':ident}).mappings().one()
        if row['estado'] != expected:
            raise ValueError('La solicitud cambió en otra sesión. Actualiza la página.')
        if status == 'Aprobado':
            u = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c'),{'c':code}).mappings().one()
            if remaining(c,u) is None or remaining(c,u)<0:
                raise ValueError('Saldo insuficiente para aprobar.')
        c.execute(text('UPDATE solicitudes SET estado=:s,actualizado_en=NOW() WHERE id=:id'),{'s':status,'id':ident})
        audit(c,session['codigo'],f'Solicitud #{ident}: Pendiente -> {status}')


def set_budget(engine, session, code, budget):
    if not isinstance(budget,int) or not 0 <= budget <= 10000:
        raise ValueError('Cupo inválido.')
    with engine.begin() as c:
        actor(c,session,admin=True)
        u = c.execute(text('SELECT * FROM usuarios WHERE codigo=:c FOR UPDATE'),{'c':code}).mappings().one()
        actor(c,session,admin=True)
        used = c.execute(text("SELECT COALESCE(SUM(dias),0) FROM solicitudes WHERE codigo=:c AND estado IN ('Pendiente','Aprobado')"),{'c':code}).scalar_one()
        if budget < used:
            raise ValueError('El cupo no puede ser menor que los días reservados y aprobados.')
        c.execute(text('UPDATE usuarios SET cupo_vacaciones=:b WHERE codigo=:c'),{'b':budget,'c':code})
        audit(c,session['codigo'],f'Cupo {code}: {u["cupo_vacaciones"]} -> {budget}')

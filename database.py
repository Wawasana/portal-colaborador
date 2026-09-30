"""PostgreSQL schema and explicit, versioned bootstrap configuration."""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from auth import hash_password


def get_engine(url):
    parsed = make_url(url)
    if parsed.drivername in ('postgres', 'postgresql', 'postgresql+psycopg2'):
        parsed = parsed.set(drivername='postgresql+psycopg')
    if parsed.drivername != 'postgresql+psycopg':
        raise ValueError('Se requiere PostgreSQL.')
    query = dict(parsed.query)
    if query.get('sslmode') not in ('require', 'verify-ca', 'verify-full'):
        query['sslmode'] = 'require'
    return create_engine(parsed.set(query=query), pool_pre_ping=True, pool_size=5,
                         max_overflow=2, connect_args={'connect_timeout': 10})


def migrate(engine):
    # Serialized, atomic and compatible with the original PostgreSQL schema.
    with engine.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(71492001)'))
        c.execute(text('''CREATE TABLE IF NOT EXISTS usuarios (
            codigo VARCHAR(50) PRIMARY KEY, nombre VARCHAR(100) NOT NULL,
            cargo VARCHAR(100), fecha_ingreso DATE, es_admin BOOLEAN NOT NULL DEFAULT FALSE,
            password_hash TEXT NOT NULL)'''))
        c.execute(text('ALTER TABLE usuarios ALTER COLUMN password_hash TYPE TEXT'))
        c.execute(text('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE'))
        c.execute(text('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cred_revision INT NOT NULL DEFAULT 0'))
        c.execute(text('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cupo_vacaciones INT CHECK (cupo_vacaciones >= 0)'))
        c.execute(text('''CREATE TABLE IF NOT EXISTS solicitudes (
            id SERIAL PRIMARY KEY, codigo VARCHAR(50) NOT NULL, nombre VARCHAR(100) NOT NULL,
            mes_inicio VARCHAR(20) NOT NULL, anio_inicio INT NOT NULL,
            fecha_inicio DATE NOT NULL, fecha_fin DATE NOT NULL, dias INT NOT NULL,
            comentarios TEXT, estado VARCHAR(20) NOT NULL DEFAULT 'Pendiente',
            creado_en TIMESTAMP NOT NULL DEFAULT NOW(), actualizado_en TIMESTAMP NOT NULL DEFAULT NOW())'''))
        for name, clause in (
            ('solicitudes_usuario_fk', 'FOREIGN KEY (codigo) REFERENCES usuarios(codigo)'),
            ('solicitudes_estado_ck', "CHECK (estado IN ('Pendiente','Aprobado','Rechazado'))"),
            ('solicitudes_fechas_ck', 'CHECK (fecha_fin >= fecha_inicio AND dias = fecha_fin - fecha_inicio + 1 AND dias BETWEEN 1 AND 30)'),
            ('solicitudes_comentarios_ck', 'CHECK (length(comentarios) <= 300)'),
        ):
            exists = c.execute(text("SELECT 1 FROM pg_constraint WHERE conname=:n AND conrelid='solicitudes'::regclass"), {'n': name}).scalar()
            if not exists:
                # Existing invalid rows stop migration rather than being silently removed.
                c.execute(text(f'ALTER TABLE solicitudes ADD CONSTRAINT {name} {clause}'))
        overlap = c.execute(text("""SELECT 1 FROM solicitudes a JOIN solicitudes b ON a.codigo=b.codigo AND a.id<b.id
            WHERE a.estado IN ('Pendiente','Aprobado') AND b.estado IN ('Pendiente','Aprobado')
            AND a.fecha_inicio<=b.fecha_fin AND b.fecha_inicio<=a.fecha_fin LIMIT 1""")).first()
        if overlap:
            raise ValueError('Existen solicitudes históricas solapadas; corregir antes de migrar.')
        c.execute(text('CREATE INDEX IF NOT EXISTS idx_solicitudes_codigo ON solicitudes(codigo)'))
        c.execute(text('''CREATE TABLE IF NOT EXISTS auditoria (
            id SERIAL PRIMARY KEY, fecha TIMESTAMP NOT NULL DEFAULT NOW(), usuario VARCHAR(50), accion VARCHAR(200))'''))
        c.execute(text('''CREATE TABLE IF NOT EXISTS login_intentos (
            codigo VARCHAR(64) PRIMARY KEY, fallos INT NOT NULL DEFAULT 0,
            ventana TIMESTAMPTZ NOT NULL DEFAULT NOW(), bloqueado_hasta TIMESTAMPTZ)'''))


def sync_users(engine, users):
    prepared = []
    for code, data in users.items():
        code = code.strip().upper()
        if not code or len(code) > 50:
            raise ValueError('Código inválido.')
        revision = int(data.get('cred_revision', 1))
        if revision < 1:
            raise ValueError('cred_revision debe ser positiva.')
        prepared.append((code, data, revision))
    with engine.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(71492001)'))
        for code, data, revision in prepared:
            old = c.execute(text('SELECT cred_revision FROM usuarios WHERE codigo=:c FOR UPDATE'), {'c': code}).scalar()
            if old is not None and revision < old:
                raise ValueError('No se permite reducir cred_revision.')
            password_hash = hash_password(data['password']) if old is None or revision > old else None
            if old is None:
                c.execute(text('''INSERT INTO usuarios(codigo,nombre,cargo,fecha_ingreso,es_admin,password_hash,activo,cred_revision)
                    VALUES(:c,:n,:p,:f,:a,:h,:activo,:r)'''),
                    {'c':code,'n':data['nombre'],'p':data.get('cargo',''),'f':data.get('fecha_ingreso'),
                     'a':bool(data.get('es_admin',False)),'h':password_hash,'activo':bool(data.get('activo',True)),'r':revision})
            else:
                c.execute(text('''UPDATE usuarios SET nombre=:n,cargo=:p,fecha_ingreso=:f,es_admin=:a,
                    activo=:activo,password_hash=COALESCE(:h,password_hash),cred_revision=:r WHERE codigo=:c'''),
                    {'c':code,'n':data['nombre'],'p':data.get('cargo',''),'f':data.get('fecha_ingreso'),
                     'a':bool(data.get('es_admin',False)),'h':password_hash,'activo':bool(data.get('activo',True)),'r':revision})


def read(engine, query, params=None):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(query), params or {}).mappings()]


def audit(c, actor, action):
    c.execute(text('INSERT INTO auditoria(usuario,accion) VALUES(:u,:a)'), {'u':actor,'a':action[:200]})

import os
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.exc import IntegrityError
from auth import hash_password,verify_password
from database import migrate,sync_users,read
from service import login,request_vacation,change_status,set_budget,validate_dates
from erp_client import _normalizar,ERPError,obtener_colaborador


def test_passwords():
    a,b=hash_password('secure-pass-12345'),hash_password('secure-pass-12345')
    assert a!=b and a.startswith('$argon2id$')
    assert verify_password('secure-pass-12345',a)
    assert not verify_password('wrong',a)
    assert not verify_password('anything','a'*64)
    assert not verify_password('nonexistent-account-dummy-secret','a'*64)
    with pytest.raises(ValueError): hash_password('12345678')


def test_dates():
    now=date(2026,1,1)
    assert validate_dates(now,now,'',now)==1
    for start,end in [(now-timedelta(days=1),now),(now,now-timedelta(days=1)),(now,now+timedelta(days=30))]:
        with pytest.raises(ValueError): validate_dates(start,end,'',now)


def test_erp_validation():
    cfg={'campos':{'codigo':'id','nombre':'name','dias_vacaciones':'balance'}}
    assert _normalizar({'id':'EMP','name':'Test','balance':10},'EMP',cfg)['dias_vacaciones']==10
    for data in [[],{'id':'OTHER'},{'id':'EMP','name':{}},{'id':'EMP','balance':True},{'id':'EMP','balance':-1}]:
        with pytest.raises(ERPError): _normalizar(data,'EMP',cfg)
    assert obtener_colaborador('EMP',{}) is None
    with pytest.raises(ERPError): obtener_colaborador('EMP',{'SIMPLIFICA_API_KEY':'test','erp':{'base_url':'http://example.com'}})


@pytest.fixture
def engine():
    url=os.environ.get('TEST_DATABASE_URL')
    if not url: pytest.skip('Requires disposable PostgreSQL TEST_DATABASE_URL')
    import streamlit as st
    st.cache_resource.clear()
    e=create_engine(url)
    assert e.url.database=='portal_test'
    with e.begin() as c: c.execute(text('DROP TABLE IF EXISTS solicitudes,usuarios,auditoria,login_intentos CASCADE'))
    migrate(e)
    sync_users(e,{'EMP':{'nombre':'Employee','password':'secure-pass-12345'},'ADMIN':{'nombre':'Admin','password':'admin-pass-12345','es_admin':True}})
    yield e
    e.dispose()


def identities(e): return login(e,'EMP','secure-pass-12345'),login(e,'ADMIN','admin-pass-12345')


def test_fresh_login_sync(engine):
    emp,adm=identities(engine)
    assert emp and adm
    sync_users(engine,{'EMP':{'nombre':'Changed','password':'new-secure-12345','cred_revision':2}})
    assert not login(engine,'EMP','secure-pass-12345')
    assert login(engine,'EMP','new-secure-12345')
    with pytest.raises(ValueError): request_vacation(engine,emp,date.today()+timedelta(days=2),date.today()+timedelta(days=2),'')


def test_lockout(engine):
    for _ in range(5): assert not login(engine,'EMP','wrong')
    assert not login(engine,'EMP','secure-pass-12345')
    with engine.begin() as c: c.execute(text("UPDATE login_intentos SET ventana=NOW()-INTERVAL '6 minutes',bloqueado_hasta=NOW()-INTERVAL '1 minute'"))
    assert login(engine,'EMP','secure-pass-12345')


def test_vacations_permissions(engine):
    emp,adm=identities(engine)
    start=date.today()+timedelta(days=10)
    with pytest.raises(ValueError): set_budget(engine,emp,'EMP',15)
    with pytest.raises(ValueError): request_vacation(engine,emp,start,start,'')
    set_budget(engine,adm,'EMP',3)
    ident=request_vacation(engine,emp,start,start+timedelta(days=1),'')
    with pytest.raises(ValueError): request_vacation(engine,emp,start+timedelta(days=5),start+timedelta(days=6),'')
    with pytest.raises(ValueError): request_vacation(engine,emp,start,start,'')
    with pytest.raises(ValueError): change_status(engine,emp,ident,'Pendiente','Aprobado')
    change_status(engine,adm,ident,'Pendiente','Aprobado')
    with pytest.raises(ValueError): change_status(engine,adm,ident,'Pendiente','Rechazado')
    with pytest.raises(ValueError): set_budget(engine,adm,'EMP',1)


def test_concurrency(engine):
    emp,adm=identities(engine)
    set_budget(engine,adm,'EMP',1)
    start=date.today()+timedelta(days=20)
    def attempt(i):
        try:
            request_vacation(engine,emp,start+timedelta(days=i),start+timedelta(days=i),'')
            return True
        except ValueError: return False
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,[0,1]))
    assert sum(results)==1 and len(read(engine,'SELECT * FROM solicitudes'))==1


def test_constraints(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c: c.execute(text("""INSERT INTO solicitudes(codigo,nombre,mes_inicio,anio_inicio,fecha_inicio,fecha_fin,dias,estado)
        VALUES('EMP','Test','Enero',2026,'2026-01-01','2026-01-01',2,'Invalid')"""))


def test_migration_repeat(engine):
    migrate(engine)
    assert login(engine,'EMP','secure-pass-12345')


def test_app_missing_config():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file('../app.py').run()
    assert not app.exception and app.error


def test_rejection_releases_balance(engine):
    emp,adm=identities(engine)
    set_budget(engine,adm,'EMP',1)
    start=date.today()+timedelta(days=20)
    ident=request_vacation(engine,emp,start,start,'')
    change_status(engine,adm,ident,'Pendiente','Rechazado')
    assert request_vacation(engine,emp,start,start,'')!=ident


def test_disabled_account(engine):
    emp,adm=identities(engine)
    sync_users(engine,{'EMP':{'nombre':'Employee','activo':False,'cred_revision':1}})
    assert not login(engine,'EMP','secure-pass-12345')
    with pytest.raises(ValueError):
        request_vacation(engine,emp,date.today()+timedelta(days=1),date.today()+timedelta(days=1),'')


def test_app_login(engine):
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file('../app.py')
    # App enforces TLS; this fixture server uses non-TLS. Inject the test engine.
    import database
    from unittest.mock import patch
    with patch.object(database,'get_engine',return_value=engine):
        app.secrets['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
        app.run()
        assert not app.exception
        app.text_input[0].set_value('EMP')
        app.text_input[1].set_value('secure-pass-12345')
        app.button[0].click().run()
        assert not app.exception
        assert 'Bienvenido' in app.title[0].value


def test_complete_streamlit_flow(engine):
    """Real AppTest forms plus simulated data_editor edit (unsupported widget)."""
    from streamlit.testing.v1 import AppTest
    from unittest.mock import patch
    import database
    import streamlit as st
    _,adm=identities(engine)
    set_budget(engine,adm,'EMP',3)
    def open_app(code,pw):
        at=AppTest.from_file('../app.py')
        at.secrets['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
        at.run()
        at.text_input[0].set_value(code)
        at.text_input[1].set_value(pw)
        at.button[0].click().run()
        assert not at.exception and not at.error
        return at
    with patch.object(database,'get_engine',return_value=engine):
        employee=open_app('EMP','secure-pass-12345')
        assert employee.metric[0].value=='3'
        start=date.today()+timedelta(days=10)
        employee.date_input[0].set_value(start)
        employee.date_input[1].set_value(start+timedelta(days=1))
        employee.button[1].click().run()
        assert not employee.exception and not employee.error
        # Regression: balance must refresh immediately after form submission.
        assert employee.metric[0].value=='1'
        rows=read(engine,'SELECT * FROM solicitudes')
        assert len(rows)==1 and rows[0]['estado']=='Pendiente'
        admin=open_app('ADMIN','admin-pass-12345')
        assert 'Administración' in admin.title[0].value
        def edit(df,**kwargs):
            result=df.copy()
            result.loc[result['id']==rows[0]['id'],'estado']='Aprobado'
            return result
        with patch.object(st,'data_editor',side_effect=edit):
            admin.button[1].click().run()
        assert not admin.exception and not admin.error
        assert read(engine,'SELECT estado FROM solicitudes')[0]['estado']=='Aprobado'
        employee.run()
        assert employee.metric[0].value=='1'  # approval doesn't charge twice
        assert employee.dataframe[0].value.iloc[0]['estado']=='Aprobado'
        # Another request exhausts the last day; rejection releases it.
        employee.date_input[0].set_value(start+timedelta(days=5))
        employee.date_input[1].set_value(start+timedelta(days=5))
        employee.button[1].click().run()
        assert employee.metric[0].value=='0'
        second=read(engine,"SELECT id FROM solicitudes WHERE estado='Pendiente'")[0]['id']
        admin.run()
        def reject(df,**kwargs):
            result=df.copy()
            result.loc[result['id']==second,'estado']='Rechazado'
            return result
        with patch.object(st,'data_editor',side_effect=reject):
            admin.button[1].click().run()
        assert not admin.error and not admin.exception
        employee.run()
        assert employee.metric[0].value=='1'
        employee.button[0].click().run()
        assert employee.title[0].value=='🏢 Portal Interno'


def test_migration_from_original_schema(engine):
    import hashlib
    with engine.begin() as c:
        c.execute(text('DROP TABLE solicitudes,usuarios,auditoria,login_intentos CASCADE'))
        c.execute(text('''CREATE TABLE usuarios(codigo VARCHAR(50) PRIMARY KEY,nombre VARCHAR(100) NOT NULL,
        cargo VARCHAR(100),fecha_ingreso DATE,es_admin BOOLEAN NOT NULL DEFAULT FALSE,password_hash VARCHAR(64) NOT NULL)'''))
        c.execute(text('''CREATE TABLE solicitudes(id SERIAL PRIMARY KEY,codigo VARCHAR(50) NOT NULL,nombre VARCHAR(100) NOT NULL,
        mes_inicio VARCHAR(20) NOT NULL,anio_inicio INT NOT NULL,fecha_inicio DATE NOT NULL,fecha_fin DATE NOT NULL,
        dias INT NOT NULL,comentarios TEXT,estado VARCHAR(20) NOT NULL DEFAULT 'Pendiente',
        creado_en TIMESTAMP NOT NULL DEFAULT NOW(),actualizado_en TIMESTAMP NOT NULL DEFAULT NOW())'''))
        c.execute(text("INSERT INTO usuarios(codigo,nombre,password_hash) VALUES('EMP','Employee',:h)"),
                  {'h':hashlib.sha256(b'wawasana_portal_sal_fija_v1old-password').hexdigest()})
        c.execute(text("""INSERT INTO solicitudes(codigo,nombre,mes_inicio,anio_inicio,fecha_inicio,fecha_fin,dias)
        VALUES('EMP','Employee','Enero',2026,'2026-01-01','2026-01-02',2)"""))
    migrate(engine)
    assert len(read(engine,'SELECT * FROM solicitudes'))==1
    assert not login(engine,'EMP','old-password')
    sync_users(engine,{'EMP':{'nombre':'Employee','password':'new-secure-12345','cred_revision':1}})
    assert login(engine,'EMP','new-secure-12345')
    assert read(engine,'SELECT dias FROM solicitudes')[0]['dias']==2


@pytest.mark.parametrize('variant',['state','dates','days','comments','fk'])
def test_each_constraint(engine,variant):
    data={'code':'EMP','start':date(2026,1,1),'end':date(2026,1,1),'days':1,'state':'Pendiente','comments':''}
    if variant=='state': data['state']='Invalid'
    if variant=='dates': data['end']=date(2025,12,31)
    if variant=='days': data['days']=2
    if variant=='comments': data['comments']='x'*301
    if variant=='fk': data['code']='UNKNOWN'
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text('''INSERT INTO solicitudes(codigo,nombre,mes_inicio,anio_inicio,fecha_inicio,fecha_fin,dias,comentarios,estado)
            VALUES(:code,'Test','Enero',2026,:start,:end,:days,:comments,:state)'''),data)


def test_audit_failure_rolls_back(engine):
    from unittest.mock import patch
    import service
    emp,adm=identities(engine)
    set_budget(engine,adm,'EMP',2)
    start=date.today()+timedelta(days=2)
    with patch.object(service,'audit',side_effect=RuntimeError('test audit failure')):
        with pytest.raises(RuntimeError): request_vacation(engine,emp,start,start,'')
    assert not read(engine,'SELECT * FROM solicitudes')


def test_concurrent_overlap_with_ample_balance(engine):
    emp,adm=identities(engine)
    set_budget(engine,adm,'EMP',20)
    start=date.today()+timedelta(days=20)
    def attempt(i):
        try:
            request_vacation(engine,emp,start,start+timedelta(days=i),'')
            return True
        except ValueError: return False
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,[0,1]))
    assert sum(results)==1


def test_session_expiry_ui(engine):
    import time
    import database
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest
    emp,_=identities(engine)
    with patch.object(database,'get_engine',return_value=engine):
        app=AppTest.from_file('../app.py')
        app.secrets['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
        app.session_state['identity']=emp
        app.session_state['started']=time.monotonic()
        app.session_state['last_activity']=time.monotonic()-301
        app.run()
        assert not app.exception and app.title[0].value=='🏢 Portal Interno'


def test_erp_transport_errors():
    import requests
    from unittest.mock import patch,Mock
    cfg={'SIMPLIFICA_API_KEY':'test-only','erp':{'base_url':'https://example.com','campos':{'codigo':'id'}}}
    for code in (301,401,403,404,500):
        with patch('erp_client.requests.get',return_value=Mock(status_code=code)) as get:
            with pytest.raises(ERPError): obtener_colaborador('EMP',cfg)
            assert get.call_args.kwargs['allow_redirects'] is False
    with patch('erp_client.requests.get',side_effect=requests.Timeout('secret-example')):
        with pytest.raises(ERPError) as error: obtener_colaborador('EMP',cfg)
        assert 'secret-example' not in str(error.value)


@pytest.mark.parametrize('start,current,earned,next_date',[
    (date(2026,1,1),date(2026,6,30),0,date(2026,7,1)),
    (date(2026,1,1),date(2026,7,1),15,date(2027,1,1)),
    (date(2026,1,1),date(2027,1,1),30,date(2027,7,1)),
    (date(2026,8,31),date(2027,2,27),0,date(2027,2,28)),
    (date(2026,8,31),date(2027,2,28),15,date(2027,8,31)),
    (date(2024,2,29),date(2025,2,28),30,date(2025,8,29)),
    (date(2027,1,1),date(2026,9,30),0,date(2027,7,1)),
    (None,date(2026,9,30),None,None),
])
def test_six_month_anniversaries(start,current,earned,next_date):
    from service import entitlement
    assert entitlement(start,current)==(earned,next_date)


def test_automatic_balance_and_reservations(engine):
    from unittest.mock import patch
    import service
    emp,adm=identities(engine)
    with engine.begin() as c:
        c.execute(text("UPDATE usuarios SET fecha_ingreso='2026-01-01',cupo_vacaciones=999 WHERE codigo='EMP'"))
    with patch.object(service,'today',return_value=date(2026,7,1)):
        ident=request_vacation(engine,emp,date(2026,7,2),date(2026,7,16),'15 días')
        with pytest.raises(ValueError):
            request_vacation(engine,emp,date(2026,8,1),date(2026,8,1),'Sin saldo')
        change_status(engine,adm,ident,'Pendiente','Aprobado')
        with engine.connect() as c:
            u=service.actor(c,emp)
            assert service.remaining(c,u)==0
            assert service.vacation_summary(c,u,date(2027,1,1))['disponibles']==15
    with patch.object(service,'today',return_value=date(2027,1,1)):
        ident=request_vacation(engine,emp,date(2027,1,2),date(2027,1,6),'Reserva')
        with engine.connect() as c:
            assert service.remaining(c,service.actor(c,emp))==10
        change_status(engine,adm,ident,'Pendiente','Rechazado')
        with engine.connect() as c:
            assert service.remaining(c,service.actor(c,emp))==15
    # Renewal/profile synchronization retaining original start does not reset accrual.
    sync_users(engine,{'EMP':{'nombre':'Renewed','fecha_ingreso':'2026-01-01','cred_revision':1}})
    with engine.connect() as c:
        assert service.vacation_summary(c,service.actor(c,emp),date(2027,1,1))['disponibles']==15

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

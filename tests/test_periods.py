from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
from sqlalchemy import text
from database import read, migrate
from periods import annual_periods, capacity, validate_block, validate_historical_block
import service
from test_security import engine, identities

NOW=date(2026,9,30)


@pytest.mark.parametrize('start,now,count,anniversary',[
    (date(2026,1,1),date(2026,6,30),1,date(2027,1,1)),
    (date(2026,1,1),date(2027,1,1),2,date(2028,1,1)),
    (date(2024,2,29),date(2025,2,28),2,date(2026,2,28)),
    (date(2024,2,29),date(2028,2,28),4,date(2028,2,29)),
    (date(2026,8,31),date(2027,2,28),1,date(2027,8,31)),
    (None,NOW,0,None),
    (date(2027,1,1),NOW,0,None),
])
def test_period_boundaries(start,now,count,anniversary):
    periods=annual_periods(start,now)
    assert len(periods)==count
    if periods:
        assert periods[-1]['habilita']==anniversary
        assert periods[-1]['fin']+timedelta(days=1)==anniversary
        assert all(a['fin']+timedelta(days=1)==b['inicio'] for a,b in zip(periods,periods[1:]))


@pytest.mark.parametrize('block,days,existing,allowed',[
    ('Principal',15,[],True), ('Principal',7,[],False),
    ('Principal',8,[{'bloque':'Principal','dias':7}],False),
    ('Principal',7,[{'bloque':'Principal','dias':7}],False),
    ('Principal',1,[],False),('Flexible',1,[],False),
    ('Flexible',5,[{'bloque':'Flexible','dias':11}],False),
    ('Completo',30,[],True),('Completo',29,[],False),
    ('Completo',30,[{'bloque':'Flexible','dias':1}],False),
    ('Flexible',15,[{'bloque':'Principal','dias':15}],True),
    ('Flexible',15,[{'bloque':'Flexible','dias':15}],False),
    ('Principal',15,[{'bloque':'Principal','dias':15}],False),
    ('Principal',15,[{'bloque':'Completo','dias':30}],False),
    ('Principal',15,[{'bloque':'Flexible','dias':11}],False),
    ('Flexible',15,[{'bloque':'Principal','dias':15},{'bloque':'Flexible','dias':15}],False),
])
def test_fraccionamiento(block,days,existing,allowed):
    if allowed: validate_block(block,days,existing)
    else:
        with pytest.raises(ValueError): validate_block(block,days,existing)


def test_only_15_or_30_and_historical_exception():
    for days in range(1,31):
        for block in ('Principal','Flexible','Completo'):
            if days == (30 if block=='Completo' else 15):
                validate_block(block,days,[])
            else:
                with pytest.raises(ValueError): validate_block(block,days,[])
    validate_historical_block('Flexible',11,[])
    with pytest.raises(ValueError): validate_block('Flexible',11,[])


def test_six_month_advance_and_anniversary_capacity():
    p=annual_periods(date(2026,1,1),date(2026,7,1))[0]
    p['record_validado']=False
    assert capacity(p,date(2026,6,30))==0
    assert capacity(p,date(2026,7,1))==15
    assert capacity(p,date(2026,12,31))==15
    assert capacity(p,date(2027,1,1))==0
    p['record_validado']=True
    assert capacity(p,date(2027,1,1))==30


@pytest.fixture
def setup(engine):
    emp,admin=identities(engine)
    with engine.begin() as c:
        c.execute(text("UPDATE usuarios SET fecha_ingreso='2025-01-01' WHERE codigo='EMP'"))
        periods=service.ensure_periods(c,service.actor(c,emp),NOW)
    with patch.object(service,'today',return_value=NOW):
        service.validate_record(engine,admin,periods[0]['id'],'Registro de asistencia validado por RRHH')
        yield engine,emp,admin,periods


def summary(e,emp):
    with e.connect() as c:
        return service.vacation_summary(c,service.actor(c,emp))


def request(e,emp,period,days=15,block='Principal',start=None):
    start=start or date(2026,10,1)
    return service.request_vacation(e,emp,start,start+timedelta(days=days-1),'Prueba',period['id'],block,True)


def approve(e,admin,id):
    service.register_agreement(e,admin,id,'Archivo RRHH / acuerdo firmado de prueba')
    service.change_status(e,admin,id,'Pendiente','Aprobado')


def test_annual_reservation_rejection_and_approval(setup):
    e,emp,admin,ps=setup
    assert summary(e,emp)['actual']==30
    id=request(e,emp,ps[0])
    assert summary(e,emp)['actual']==15
    with pytest.raises(ValueError): service.change_status(e,admin,id,'Pendiente','Aprobado')
    approve(e,admin,id)
    assert summary(e,emp)['actual']==15
    with pytest.raises(ValueError): service.change_status(e,emp,id,'Aprobado','Cancelada')
    service.change_status(e,admin,id,'Aprobado','Cancelada')
    assert summary(e,emp)['actual']==30
    id=request(e,emp,ps[0])
    service.change_status(e,admin,id,'Pendiente','Rechazado')
    assert summary(e,emp)['actual']==30


def test_advance_15_then_annual_15(engine):
    emp,admin=identities(engine)
    with engine.begin() as c:
        c.execute(text("UPDATE usuarios SET fecha_ingreso='2026-01-01' WHERE codigo='EMP'"))
        p=service.ensure_periods(c,service.actor(c,emp),date(2026,7,1))[0]
    with patch.object(service,'today',return_value=date(2026,6,30)):
        with pytest.raises(ValueError): request(engine,emp,p)
    with patch.object(service,'today',return_value=date(2026,7,1)):
        id=request(engine,emp,p,start=date(2026,7,2))
        assert read(engine,'SELECT modalidad FROM solicitudes')[0]['modalidad']=='Adelanto'
        approve(engine,admin,id)
        assert summary(engine,emp)['adelanto']==0
    with patch.object(service,'today',return_value=date(2026,7,17)):
        service.change_status(engine,admin,id,'Aprobado','Disfrutada')
        assert summary(engine,emp)['disfrutados']==15
    with patch.object(service,'today',return_value=date(2027,1,1)):
        with engine.begin() as c: service.ensure_periods(c,service.actor(c,emp))
        service.validate_record(engine,admin,p['id'],'Asistencia del año verificada')
        assert summary(engine,emp)['actual']==15
        assert summary(engine,emp)['disfrutados']==15
        assert len(summary(engine,emp)['periodos'])==2
        second=request(engine,emp,p,15,'Flexible',date(2027,1,2))
        approve(engine,admin,second)
        assert summary(engine,emp)['actual']==0
        assert summary(engine,emp)['aprobados']==15
        with pytest.raises(ValueError): request(engine,emp,p,15,'Flexible',date(2027,2,1))


def test_entire_year_30_and_permission(setup):
    e,emp,admin,ps=setup
    with pytest.raises(ValueError): service.validate_record(e,emp,ps[0]['id'],'No autorizado')
    id=request(e,emp,ps[0],30,'Completo')
    approve(e,admin,id)
    assert summary(e,emp)['actual']==0
    with pytest.raises(ValueError): request(e,emp,ps[0],1,'Flexible',date(2026,11,1))


def test_block_rules_are_enforced_in_server(setup):
    e,emp,admin,ps=setup
    for days in (1,7,8,11,14,16,29):
        with pytest.raises(ValueError): request(e,emp,ps[0],days,'Principal')
    request(e,emp,ps[0],15,'Principal')
    with pytest.raises(ValueError): request(e,emp,ps[0],15,'Principal',date(2026,11,1))
    request(e,emp,ps[0],15,'Flexible',date(2026,11,1))
    assert summary(e,emp)['actual']==0
    with pytest.raises(ValueError): request(e,emp,ps[0],15,'Flexible',date(2026,12,1))


def test_overlap_and_concurrent_exhaustion(setup):
    e,emp,admin,ps=setup
    request(e,emp,ps[0])
    with pytest.raises(ValueError): request(e,emp,ps[0],1,'Flexible')
    def attempt(i):
        try:
            request(e,emp,ps[0],15,'Flexible',date(2026,11,1)+timedelta(days=20*i))
            return True
        except ValueError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt,[0,1]))==1
    assert summary(e,emp)['actual']==0


def test_historical_preservation_and_reconciliation(setup):
    e,emp,admin,ps=setup
    with e.begin() as c:
        id=c.execute(text("""INSERT INTO solicitudes(codigo,nombre,mes_inicio,anio_inicio,fecha_inicio,fecha_fin,dias,estado)
            VALUES('EMP','Employee','Septiembre',2026,'2026-09-30','2026-10-10',11,'Aprobado') RETURNING id""")).scalar_one()
    migrate(e); migrate(e)
    assert len(read(e,'SELECT * FROM solicitudes'))==1
    assert summary(e,emp)['disponibles'] is None
    with pytest.raises(ValueError): request(e,emp,ps[0],1,'Flexible',date(2026,11,1))
    service.assign_historical(e,admin,id,ps[0]['id'],'Flexible','Validación del historial anterior')
    assert summary(e,emp)['actual']==19
    assert read(e,'SELECT estado FROM solicitudes')[0]['estado']=='Aprobado'
    assert read(e,'SELECT dias,fecha_inicio,fecha_fin FROM solicitudes')[0]=={'dias':11,'fecha_inicio':date(2026,9,30),'fecha_fin':date(2026,10,10)}
    with pytest.raises(ValueError): request(e,emp,ps[0],15,'Principal',date(2026,11,1))
    with pytest.raises(ValueError): service.assign_historical(e,admin,id,ps[1]['id'],'Flexible','No reasignar')


def test_record_required_and_no_advance_future_credit(setup):
    e,emp,admin,ps=setup
    with e.begin() as c: c.execute(text('UPDATE periodos_vacaciones SET record_validado=FALSE WHERE id=:p'),{'p':ps[0]['id']})
    with pytest.raises(ValueError): request(e,emp,ps[0])
    with pytest.raises(ValueError): request(e,emp,ps[1],30,'Completo')
    with pytest.raises(ValueError): service.validate_record(e,admin,ps[1]['id'],'Aún no cumplió el año')


def test_approval_rechecks_policy_for_nonhistorical_requests(setup):
    e,emp,admin,ps=setup
    ident=request(e,emp,ps[0])
    service.register_agreement(e,admin,ident,'Acuerdo firmado registrado')
    with e.begin() as c:
        c.execute(text("UPDATE solicitudes SET dias=7,fecha_fin=fecha_inicio+6 WHERE id=:id"),{'id':ident})
    with pytest.raises(ValueError): service.change_status(e,admin,ident,'Pendiente','Aprobado')
    assert read(e,'SELECT estado FROM solicitudes WHERE id=:id',{'id':ident})[0]['estado']=='Pendiente'


def test_identity_and_written_acceptance(setup):
    e,emp,admin,ps=setup
    with pytest.raises(ValueError):
        service.request_vacation(e,emp,date(2026,10,1),date(2026,10,15),'',ps[0]['id'],'Principal',False)
    with e.begin() as c: c.execute(text("UPDATE usuarios SET activo=FALSE WHERE codigo='EMP'"))
    with pytest.raises(ValueError): request(e,emp,ps[0])


def test_rollback_and_stale_status(setup):
    e,emp,admin,ps=setup
    with patch.object(service,'audit',side_effect=RuntimeError('Audit failure')):
        with pytest.raises(RuntimeError): request(e,emp,ps[0])
    assert not read(e,'SELECT * FROM solicitudes')
    id=request(e,emp,ps[0])
    service.change_status(e,admin,id,'Pendiente','Rechazado')
    with pytest.raises(ValueError): service.change_status(e,admin,id,'Pendiente','Aprobado')


def test_disfrutada_keeps_balance_and_cannot_cancel_after_start(setup):
    e,emp,admin,ps=setup
    id=request(e,emp,ps[0]); approve(e,admin,id)
    with pytest.raises(ValueError): service.change_status(e,admin,id,'Aprobado','Disfrutada')
    with patch.object(service,'today',return_value=date(2026,10,16)):
        with pytest.raises(ValueError): service.change_status(e,admin,id,'Aprobado','Cancelada')
        before=summary(e,emp)['actual']
        service.change_status(e,admin,id,'Aprobado','Disfrutada')
        assert summary(e,emp)['actual']==before
        assert summary(e,emp)['disfrutados']==15


def test_previous_period_and_alerts_survive_year_change(setup):
    e,emp,admin,ps=setup
    with patch.object(service,'today',return_value=date(2027,1,1)):
        with e.begin() as c: service.ensure_periods(c,service.actor(c,emp))
        service.validate_record(e,admin,ps[1]['id'],'Registro de asistencia siguiente año')
        s=summary(e,emp)
        assert s['actual']==30 and s['anteriores']==30
        assert 'Fuera de plazo' in s['periodos'][0]['alerta']
        id=service.request_vacation(e,emp,date(2027,1,2),date(2027,1,31),'',block='Completo',accepts=True)
        assert read(e,'SELECT periodo_id FROM solicitudes WHERE id=:id',{'id':id})[0]['periodo_id']==ps[0]['id']
        approve(e,admin,id)
    with patch.object(service,'today',return_value=date(2027,2,1)):
        service.change_status(e,admin,id,'Aprobado','Disfrutada')
        assert 'Fuera de plazo' in summary(e,emp)['periodos'][0]['alerta']


def test_changed_start_is_flagged(setup):
    e,emp,admin,ps=setup
    with e.begin() as c:
        c.execute(text("UPDATE usuarios SET fecha_ingreso='2026-02-01' WHERE codigo='EMP'"))
        service.ensure_periods(c,service.actor(c,emp))
    assert summary(e,emp)['disponibles'] is None
    assert any(p['inconsistente'] for p in summary(e,emp)['periodos'])


def test_streamlit_flow_and_balance_after_approval(setup):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    import database
    import streamlit as st
    e,emp,admin,ps=setup
    def open_app(identity):
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'))
        app.secrets['DATABASE_URL']='postgresql://test-local'
        app.session_state['identity']=identity
        app.run()
        assert not app.exception and not app.error
        return app
    with patch.object(database,'get_engine',return_value=e):
        employee=open_app(emp)
        assert employee.metric[0].value=='30'
        employee.date_input[0].set_value(date(2026,10,1))
        employee.date_input[1].set_value(date(2026,10,15))
        employee.checkbox[0].set_value(True)
        next(b for b in employee.button if b.label=='Enviar solicitud').click().run()
        assert not employee.exception and not employee.error
        assert employee.metric[0].value=='15'
        id=read(e,'SELECT id FROM solicitudes')[0]['id']
        service.register_agreement(e,admin,id,'Documento firmado de prueba')
        app=open_app(admin)
        def edit(df,**kwargs):
            result=df.copy();result.loc[result['id']==id,'estado']='Aprobado';return result
        with patch.object(st,'data_editor',side_effect=edit):
            next(b for b in app.button if b.label=='Guardar cambios de estado').click().run()
        assert not app.exception and not app.error
        employee.run()
        assert employee.metric[0].value=='15'
        assert read(e,'SELECT estado FROM solicitudes')[0]['estado']=='Aprobado'

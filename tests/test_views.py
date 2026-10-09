"""Check Streamlit form interaction without a database server."""
from contextlib import contextmanager
from datetime import date,datetime
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import service
import database

EMP={'codigo':'EMP','nombre':'Empleado','es_admin':False,'fecha_ingreso':date(2025,1,1),'cargo':'Pruebas','activo':True}
PERIOD={'id':1,'inicio':date(2025,1,1),'fin':date(2025,12,31),'habilita':date(2026,1,1),'adelanto_desde':date(2025,7,1),'limite':date(2027,1,1),
    'record_validado':True,'generados':30,'adelanto_habilitado':0,'pendientes':0,'aprobados':0,'disfrutados':0,'sin_usar':30,'disponibles':30,'inconsistente':False,'alerta':''}

class Connection:
    def execute(self,*args,**kwargs): return None

class Engine:
    @contextmanager
    def connect(self): yield Connection()
    @contextmanager
    def begin(self): yield Connection()


def make_summary():
    return {'actual':30,'anteriores':0,'adelanto':0,'disponibles':30,'sin_asignar':0,'periodos':[dict(PERIOD)]}


def app():
    at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'))
    at.secrets['DATABASE_URL']='test-local'
    at.session_state['identity']={'codigo':'EMP','revision':1}
    return at


def test_employee_period_form_and_request():
    import streamlit as st
    st.cache_resource.clear()
    s=make_summary()
    calls=[]
    def submit(*args):
        calls.append(args);s['actual']=15;s['disponibles']=15
        return 1
    with patch.object(database,'get_engine',return_value=Engine()),patch.object(database,'migrate'),patch.object(database,'sync_users'),patch.object(database,'read',return_value=[]),patch.object(service,'actor',return_value=EMP),patch.object(service,'ensure_periods'),patch.object(service,'vacation_summary',side_effect=lambda *args:s),patch.object(service,'request_vacation',side_effect=submit),patch.object(service,'today',return_value=date(2026,9,30)):
        at=app().run()
        assert not at.exception and not at.error
        at.date_input[0].set_value(date(2026,10,1));at.date_input[1].set_value(date(2026,10,15))
        at.checkbox[0].set_value(True)
        next(b for b in at.button if b.label=='Enviar solicitud').click().run()
        assert not at.exception and not at.error and at.metric[0].value=='15'
        assert calls[0][-3:]==(1,'Principal',True)


def test_admin_record_and_agreement_forms_can_submit():
    import streamlit as st
    st.cache_resource.clear()
    admin={**EMP,'es_admin':True}
    row={'id':1,'codigo':'EMP','nombre':'Empleado','mes_inicio':'Octubre','anio_inicio':2026,'fecha_inicio':date(2026,10,1),'fecha_fin':date(2026,10,15),
        'dias':15,'comentarios':'','estado':'Pendiente','creado_en':datetime(2026,9,30,16),'actualizado_en':datetime(2026,9,30,16),
        'periodo_id':1,'bloque':'Principal','modalidad':'Anual','acuerdo':'','acepta_acuerdo':True,'conciliacion_referencia':None}
    def read(e,sql,*args): return [EMP] if 'FROM usuarios' in sql else [row]
    with patch.object(database,'get_engine',return_value=Engine()),patch.object(database,'migrate'),patch.object(database,'sync_users'),patch.object(database,'read',side_effect=read),patch.object(service,'actor',return_value=admin),patch.object(service,'ensure_periods'),patch.object(service,'vacation_summary',return_value=make_summary()),patch.object(service,'validate_record') as record,patch.object(service,'register_agreement') as agreement:
        at=app().run()
        assert not at.exception and not at.error
        at.text_input[0].set_value('Registro de asistencia verificado');at.checkbox[0].set_value(True)
        next(b for b in at.button if b.label=='Validar récord').click().run()
        assert record.call_count==1 and not at.exception and not at.error
        at.text_input[1].set_value('Archivo RRHH / acuerdo firmado');at.checkbox[1].set_value(True)
        next(b for b in at.button if b.label=='Registrar acuerdo').click().run()
        assert agreement.call_count==1 and not at.exception and not at.error

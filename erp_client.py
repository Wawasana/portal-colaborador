"""Read-only ERP adapter. Explicit field map; no inferred vacation entitlement."""
from datetime import date
from urllib.parse import urlsplit, quote
import requests


class ERPError(Exception):
    pass


def _normalizar(data, code, cfg):
    if not isinstance(data,dict):
        raise ERPError('Formato de respuesta ERP inválido.')
    for part in cfg.get('root','').split('.'):
        if part:
            data = data.get(part) if isinstance(data,dict) else None
    if not isinstance(data,dict):
        raise ERPError('La ruta configurada del ERP no contiene un objeto.')
    fields = cfg.get('campos',{})
    if not fields or 'codigo' not in fields:
        raise ERPError('Falta un mapa de campos verificado del ERP.')
    out = {}
    for internal, path in fields.items():
        value = data
        for part in path.split('.'):
            value = value.get(part) if isinstance(value,dict) else None
        out[internal] = value
    if str(out.get('codigo','')).strip().upper() != code:
        raise ERPError('El ERP devolvió un colaborador distinto del solicitado.')
    for field in ('nombre','cargo','estado_contrato'):
        if out.get(field) is not None and not isinstance(out[field],str):
            raise ERPError('Tipo de campo ERP inválido.')
    if out.get('fecha_ingreso'):
        try:
            out['fecha_ingreso'] = date.fromisoformat(str(out['fecha_ingreso'])[:10])
        except ValueError:
            raise ERPError('Fecha ERP inválida.') from None
    if out.get('dias_vacaciones') is not None:
        value = out['dias_vacaciones']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0 <= value <= 10000:
            raise ERPError('Saldo ERP inválido.')
    return out


def obtener_colaborador(code, secrets=None):
    if secrets is None:
        import streamlit as st
        secrets = st.secrets
    cfg = secrets.get('erp',{})
    key = secrets.get('SIMPLIFICA_API_KEY')
    if not cfg and not key:
        return None
    base = cfg.get('base_url','').rstrip('/')
    endpoint = cfg.get('endpoint_colaborador','/colaboradores/{codigo}')
    parsed = urlsplit(base)
    if not key or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ERPError('Configuración ERP incompleta o insegura.')
    if not endpoint.startswith('/') or endpoint.startswith('//') or '{codigo}' not in endpoint:
        raise ERPError('Endpoint ERP inválido.')
    try:
        response = requests.get(base+endpoint.format(codigo=quote(code,safe='')),
            headers={cfg.get('header_auth','Authorization'):f"{cfg.get('prefijo_token','Bearer')} {key}".strip(),
                     'Accept':'application/json'}, timeout=(3,10), allow_redirects=False)
        if response.status_code != 200:
            raise ERPError('El ERP no pudo confirmar los datos del colaborador.')
        if len(response.content)>1024*1024:
            raise ERPError('Respuesta ERP demasiado grande.')
        return _normalizar(response.json(),code,cfg)
    except (requests.RequestException,ValueError,KeyError):
        raise ERPError('No fue posible obtener una respuesta ERP válida.') from None

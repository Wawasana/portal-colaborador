"""Annual service periods, anchored to the original continuous start date."""
from datetime import timedelta
from dateutil.relativedelta import relativedelta

ACTIVE = ('Pendiente', 'Aprobado', 'Disfrutada')
BLOCKS = ('Completo', 'Principal', 'Flexible')


def annual_periods(start, current):
    if start is None or start > current:
        return []
    years = current.year - start.year
    if start + relativedelta(years=years) > current:
        years -= 1
    return [dict(inicio=start + relativedelta(years=i),
                 fin=start + relativedelta(years=i+1) - timedelta(days=1),
                 habilita=start + relativedelta(years=i+1),
                 adelanto_desde=start + relativedelta(years=i, months=6),
                 limite=start + relativedelta(years=i+2)) for i in range(years+1)]


def capacity(period, current):
    if current >= period['habilita']:
        return 30 if period['record_validado'] else 0
    return 15 if current >= period['adelanto_desde'] else 0


def validate_block(block, days, existing):
    """Keep a principal 15-day block (15 or 7+8) and flexible remainder <=15."""
    if block not in BLOCKS:
        raise ValueError('Selecciona un bloque vacacional válido.')
    if any(r['bloque'] == 'Historico' for r in existing):
        raise ValueError('RR. HH. debe clasificar las solicitudes históricas de este período.')
    if block == 'Completo':
        if days != 30 or existing:
            raise ValueError('El bloque completo requiere 30 días y un período sin reservas ni uso.')
        return
    if any(r['bloque'] == 'Completo' for r in existing):
        raise ValueError('El período ya tiene una solicitud por 30 días.')
    lengths = [r['dias'] for r in existing if r['bloque'] == block] + [days]
    if block == 'Flexible' and sum(lengths) <= 15:
        return
    if block == 'Principal' and sorted(lengths) in ([7], [8], [15], [7, 8]):
        return
    raise ValueError('Principal: 15 días seguidos o 7 + 8. Flexible: máximo 15 días por período, en fracciones desde 1 día.')

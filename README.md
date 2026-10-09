# Portal del Colaborador Wawasana

Aplicación Streamlit con PostgreSQL, autenticación Argon2id y consulta ERP opcional. Entrada: `app.py`, Python 3.12.

## Antes de desplegar

1. Respaldar la base existente. El prototipo guardaba solicitudes en memoria: no hay migración automática de esa memoria. Exportar manualmente lo que aún exista antes de reiniciarlo.
2. Crear PostgreSQL externo con TLS y configurar `DATABASE_URL` en Secrets de Streamlit. La aplicación necesita permisos DDL al arrancar para migrar el esquema; una evolución posterior debe separar migraciones y usar un rol de ejecución sin DDL.
3. Configurar usuarios y contraseñas nuevas de 12–256 caracteres. No reutilizar las credenciales publicadas por el prototipo. Eliminar código del último commit no elimina el historial: cualquier secreto real que estuvo publicado debe rotarse.
4. Verificar la política de vacaciones con RR. HH. Esta versión conserva días calendario y máximo 30 por solicitud; no presume derecho anual de 15 días. No pretende determinar el régimen laboral.
5. Configurar fecha_ingreso con la fecha original de ingreso continuo. Cada año de servicio crea un período independiente de 30 días calendario. El derecho anual solo se habilita al cumplir el aniversario y tras validar el récord vacacional desde RR. HH. No se borran días anteriores. Desde los seis meses se admite la solicitud de 15 días de adelanto, previa aprobación y referencia del acuerdo escrito. Si tomó 15 adelantados, al año quedan 15 del mismo período. No cambia con una renovación continua.

   Solicitudes anteriores: conservan IDs, fechas, estados y comentarios. Quedan sin período hasta que RR. HH. las concilie desde «Asignar solicitudes anteriores a su período». No se adivina su asignación; nuevas solicitudes se bloquean para esa persona hasta conciliarlas. Las aprobadas anteriores siguen reservando días y se confirman como disfrutadas después del descanso completo. No se convierte automáticamente una aprobación pasada en vacaciones disfrutadas.

   Política de la empresa para nuevas solicitudes: exactamente dos bloques indivisibles de 15 días calendario consecutivos por período anual (Principal y Flexible, nombres internos conservados), o una solicitud de 30 días completos sin reservas ni uso previo. Cada bloque admite una sola solicitud activa; rechazar o cancelar permite volver a solicitarlo sin aumentar el saldo. Desde los seis meses del año de servicio se habilita el adelanto de 15 días del bloque Principal, finalizando antes del aniversario. Al cumplir el año y validar el récord, quedan 15 si se adelantaron 15, o 30 si no hubo adelanto. La política semestral no es una obligación impuesta por la ley: requiere solicitud y acuerdo escrito, y no obliga a fraccionar los 30 días continuos. Cada solicitud pertenece a un solo período; el cambio de año no borra saldos anteriores.

   Conciliación excepcional del historial anterior: se mantienen las fechas, días y estados originales, incluidas las fracciones de 11 días. No se convierten en 15 ni se borran. Si un período contiene fracciones históricas incompatibles con los bloques nuevos, requiere revisión de RR. HH. antes de admitir nuevas solicitudes en ese período. El registro histórico no habilita nuevas solicitudes fraccionadas. La aprobación vuelve a comprobar la política para solicitudes que no sean históricas.

   El acuerdo firmado permanece en el archivo de RR. HH. El portal guarda la referencia, la solicitud del trabajador y la auditoría; una casilla o referencia no constituye por sí sola un documento firmado. Solo permite aprobar tras registrar esa referencia. Validación del récord: guarda referencia, administrador y fecha. Confirmar el régimen general y el récord antes de validar. La API aún no aporta asistencia ni saldo oficial.

   Reservas: Pendiente/Aprobado; uso efectivo: Disfrutada, confirmada por RR. HH. después de la fecha final. Rechazo y cancelación liberan saldo. La cancelación de una aprobación solo se admite antes del inicio; cambios parciales o descansos interrumpidos requieren revisión fuera de este flujo. Una aprobación no descuenta dos veces. Alertas desde 60 días antes del límite de goce y por períodos fuera de plazo; no calculan ni eliminan una eventual indemnización. Un descanso confirmado fuera de plazo conserva la alerta.

   Cambiar fecha_ingreso si ya hay períodos incompatibles bloquea nuevas solicitudes y señala la necesidad de conciliación. No se reasignan ni borran períodos automáticamente. Cese, reingreso, otros regímenes e historial externo requieren validación antes de producción.

6. Validar con una cuenta de empleado y otra de RR. HH. antes de cambiar Streamlit a esta rama o fusionar el PR. Fusionar `main` puede redeplegar automáticamente la app.

## Secrets de Streamlit (no subir a GitHub)

```toml
DATABASE_URL = "postgresql+psycopg://USUARIO:CLAVE@HOST:5432/BASE?sslmode=require"

[usuarios.EMP001]
nombre = "Colaborador"
cargo = "Cargo"
fecha_ingreso = "2024-01-15"
password = "REEMPLAZAR-POR-CONTRASENA-UNICA"
es_admin = false
activo = true
cred_revision = 1

[usuarios.RRHH]
nombre = "Responsable RRHH"
password = "REEMPLAZAR-POR-OTRA-CONTRASENA-UNICA"
es_admin = true
activo = true
cred_revision = 1
```

No usar literalmente los marcadores de ejemplo. Fecha: formato ISO. Usuarios se sincronizan al arrancar; reiniciar Streamlit después de cambiar Secrets. Cambiar la contraseña requiere incrementar `cred_revision`. Esto invalida sesiones existentes. Mantener la revisión nunca reemplaza el hash. Un usuario retirado de Secrets no se desactiva automáticamente: marcar `activo=false` y reiniciar. Para migrar hashes SHA-256 antiguos, configurar una nueva contraseña y revisión superior a la almacenada (los registros originales reciben revisión 0). Los hashes antiguos no permiten login.

TLS mínimo `require` cifra la conexión. Configurar `verify-full` y certificado de CA del proveedor cuando esté disponible para autenticar también el servidor de BD.

## ERP: solo consulta

No hay escritura ni sincronización de aprobaciones con Simplifica. Sin ERP la ficha local funciona. Un fallo ERP muestra aviso y nunca concede un saldo inventado. El saldo ERP es informativo. Contrato requerido: código que identifique al mismo colaborador, nombres, tipos y rutas confirmados por el proveedor.

Configuración opcional, solo después de verificar el contrato real:

```toml
SIMPLIFICA_API_KEY = "REEMPLAZAR"
[erp]
base_url = "https://HOST-REAL-ERP/v1"
endpoint_colaborador = "/colaboradores/{codigo}"
header_auth = "Authorization"
prefijo_token = "Bearer"
root = "data"
[erp.campos]
codigo = "codigo"
nombre = "nombre"
cargo = "cargo"
fecha_ingreso = "fecha_ingreso"
estado_contrato = "estado_contrato"
dias_vacaciones = "dias_vacaciones"
```

Las claves de ejemplo no constituyen documentación de Simplifica. No activar hasta confirmar el contrato. HTTPS obligatorio; no se siguen redirecciones con el token.

## Controles

- Inicialización antes del login; migración atómica, repetible y serializada.
- Argon2id con salt aleatorio, rehash al autenticar; SHA-256 legado deshabilitado.
- Cinco fallos por cuenta en cinco minutos bloquean cinco minutos en PostgreSQL. Límite global de 100 fallos por ventana. Puede producir denegación de servicio ante ataques; el siguiente paso es identidad corporativa y controles en el perímetro.
- Expiración de sesión: cinco minutos desde la última interacción ejecutada y duración absoluta de ocho horas. Se valida al siguiente rerun; no hay temporizador que borre una pantalla ya abierta.
- Usuarios activos, revisión de credenciales y permiso administrador comprobados en servidor. No se almacenan hashes en la sesión.
- SQL parametrizado, foreign key, CHECKs, control de longitud. Las migraciones rechazan datos históricos inválidos, sin borrarlos.
- Solicitudes y auditoría en la misma transacción; fallo de auditoría revierte el cambio. Locks por empleado impiden doble reserva y solapamientos entre operaciones del portal.
- Pendiente -> Aprobado/Rechazado/Cancelada; Aprobado -> Disfrutada/Cancelada con límites de fechas. Estados finales no se reabren.
- No hay contraseñas ni claves reales en el repositorio nuevo. Errores técnicos no se muestran en la interfaz ni registran parámetros SQL.

## Pruebas

```bash
pip install -r requirements.txt pytest
python -m pytest -q
streamlit run app.py
```

Sin `TEST_DATABASE_URL`, las pruebas de integración se omiten expresamente. Usar exclusivamente una BD desechable cuyo nombre sea `portal_test`: el fixture borra sus tablas.

GitHub Actions levanta PostgreSQL 16 y ejecuta también migraciones, login, revocación, bloqueo, permisos, integridad, reserva concurrente y prueba del login en Streamlit. Su clave es exclusiva del contenedor de pruebas efímero.

## Límites

Aún no incorpora SSO/MFA, recuperación autónoma de contraseña, altas/bajas completas desde UI, regímenes especiales de vacaciones, boletas, notificaciones, archivo histórico paginado, auditoría inmutable externa ni sincronización bidireccional ERP. Panel: 1000 solicitudes recientes. Los fallos operativos deben vigilarse en logs; respaldo y retención son responsabilidad del despliegue.

## Fuentes de la política del régimen general

- Decreto Supremo 002-2019-TR, artículos 4 a 9: https://busquedas.elperuano.pe/dispositivo/NL/1738190-4
- Requisitos de derecho anual y récord, SUNAFIL: https://www.gob.pe/institucion/sunafil/noticias/1092181-cuando-te-corresponden-vacaciones-todo-lo-que-necesitas-saber-para-planificar-tu-descanso

Esta implementación administra solicitudes y evidencia. No determina el régimen laboral ni reemplaza la revisión de RR. HH.

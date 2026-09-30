# Portal del Colaborador Wawasana

Aplicación Streamlit con PostgreSQL, autenticación Argon2id y consulta ERP opcional. Entrada: `app.py`, Python 3.12.

## Antes de desplegar

1. Respaldar la base existente. El prototipo guardaba solicitudes en memoria: no hay migración automática de esa memoria. Exportar manualmente lo que aún exista antes de reiniciarlo.
2. Crear PostgreSQL externo con TLS y configurar `DATABASE_URL` en Secrets de Streamlit. La aplicación necesita permisos DDL al arrancar para migrar el esquema; una evolución posterior debe separar migraciones y usar un rol de ejecución sin DDL.
3. Configurar usuarios y contraseñas nuevas de 12–256 caracteres. No reutilizar las credenciales publicadas por el prototipo. Eliminar código del último commit no elimina el historial: cualquier secreto real que estuvo publicado debe rotarse.
4. Verificar la política de vacaciones con RR. HH. Esta versión conserva días calendario y máximo 30 por solicitud; no presume derecho anual de 15 días. No pretende determinar el régimen laboral.
5. Configurar fecha_ingreso con la fecha original de ingreso continuo. El portal habilita 15 días por cada aniversario de seis meses completos y descuenta solicitudes pendientes y aprobadas de todo el historial. A los 12 meses genera 30 en total: si ya se aprobaron 15, quedan 15. Una renovación continua no cambia fecha_ingreso. El panel muestra saldo y próxima habilitación al actualizar la página. No anticipa derechos por la fecha futura de la solicitud. No elimina saldos históricos ni define vencimiento automático. Las cuentas antiguas sin fecha conservan su cupo manual anterior, marcado explícitamente, hasta configurar la fecha. Sin fecha ni cupo anterior no pueden solicitar. No se escribe al ERP; antes de usar en producción deben conciliarse descansos históricos que no estén registrados en el portal.
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
- Pendiente -> Aprobado/Rechazado; los estados finales no se reabren desde este portal. Rechazo libera la reserva. Aprobación no descuenta dos veces.
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

Aún no incorpora SSO/MFA, recuperación autónoma de contraseña, altas/bajas completas desde UI, anticipo/períodos/regímenes de vacaciones, boletas, notificaciones, archivo histórico paginado, auditoría inmutable externa ni sincronización bidireccional ERP. Panel: 1000 solicitudes recientes. Los fallos operativos deben vigilarse en logs; respaldo y retención son responsabilidad del despliegue.

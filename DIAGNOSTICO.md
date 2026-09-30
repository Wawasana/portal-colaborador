# Diagnóstico de la versión corregida — 30/09/2026

## Resultado

La nueva versión reemplaza el prototipo por una aplicación modular con PostgreSQL. El código está preparado para una validación de integración; no se declara listo para producción ni desplegado. No se ha accedido a la BD real, a Secrets, a los logs ni a la configuración efectiva de Streamlit.

GitHub confirmado: repositorio público Wawasana/portal-colaborador, main en b752cac47e65000ab0dd3f8b70ce45e6d0bc8dc6; la raíz contiene solamente app.py. Esto confirma el código del repositorio, pero no demuestra por sí solo qué revisión está ejecutando Streamlit.

## Correcciones implementadas

| Problema | Corrección | Estado de verificación |
|---|---|---|
| Tablas creadas después del login | Migración antes de autenticar, transacción y bloqueo de inicialización | Compilación; prueba de BD pendiente |
| SHA-256 con salt fijo | Argon2id, salts aleatorios y rehash; hashes antiguos requieren reset | Prueba local aprobada |
| Credenciales simuladas en código | No se incorporan a la versión nueva; usuarios desde Secrets | Revisión estática |
| Contador de login por navegador | Contadores y bloqueo persistentes en PostgreSQL, límite global | Prueba de BD pendiente |
| Saldo fijo e independiente de solicitudes | Cupo explícito de RR. HH.; reserva de pendientes/aprobadas | Prueba de BD pendiente |
| Solapamientos y sobreconsumo concurrente | Bloqueo de fila del empleado; validar e insertar en una transacción | Prueba concurrente pendiente |
| Permisos dependientes de pantalla | Validación de usuario activo, revisión y administrador en servicios | Prueba de BD pendiente |
| Cambios de estado sobrescriben decisiones concurrentes | Solo pendientes; se comprueba estado anterior bajo bloqueo | Prueba de BD pendiente |
| Secrets no sincronizan datos | Actualización de perfil/rol/activo; contraseña mediante cred_revision creciente | Prueba de BD pendiente |
| Falta integridad de BD | FK, CHECKs de estados/fechas/días/comentarios; preflight de solapamientos | Prueba de BD pendiente |
| Auditoría silenciosa / separada del guardado | Auditoría dentro de la transacción; si falla, no se confirma el cambio | Revisión estática; prueba de BD pendiente |
| Antigüedad aproximada | relativedelta y fecha local de Lima | Compilación |
| ERP adivina campos y concede saldo de respaldo | Mapa explícito y validación de código/tipos; lectura informativa | Prueba local aprobada |
| ERP expone errores HTTP / redirecciones con token | Errores genéricos; HTTPS; sin redirecciones; timeout | Prueba local parcial y revisión estática |

## Pruebas realizadas

- Python 3.12: compilación correcta de los módulos.
- pytest: **4 aprobadas, 9 omitidas expresamente** porque no hay PostgreSQL de pruebas accesible en este entorno.
- Aprobadas: hashes diferentes para igual contraseña, aceptación/rechazo de password, rechazo de SHA-256 legado incluso con el password dummy; validación de fechas; validación del esquema ERP; arranque de Streamlit sin Secrets con error controlado.
- Las 9 pruebas pendientes requieren una BD desechable: login inicial/cambio de contraseña/revocación; bloqueo persistente; permisos/saldo/solapamiento; concurrencia; constraints; migración repetible; rechazo libera saldo; cuenta inactiva; login y pantalla de empleado en Streamlit.
- Se incluye GitHub Actions con PostgreSQL 16 para ejecutar toda la batería cuando se autorice publicar el código. La publicación fue autorizada explícitamente por el usuario; el resultado de CI se comprobará en el PR.
- No se realizaron pruebas contra producción ni se usaron credenciales reales.

## Riesgos y trabajo pendiente

1. **La app publicada no recibió estas correcciones.** Los cambios se proponen mediante PR en la rama security/portal-postgresql-2026-09-30, con autorización explícita del usuario. La fusión y el despliegue quedan pendientes.
2. Las contraseñas del prototipo pueden seguir visibles en main y su historial. Rotar cualquier credencial real reutilizada y restringir el acceso al despliegue actual. Esta entrega no rota credenciales ni borra historial.
3. Preparar PostgreSQL, respaldo, Secrets, cuentas nuevas y validar cupos. Datos de session_state no se migran automáticamente.
4. Confirmar con RR. HH. la política y los derechos aplicables. Se conserva el conteo calendario del código fuente; no se asume derecho anual automático. El cupo acumulado exige gestión explícita; períodos, anticipo, arrastre y regímenes distintos no están modelados.
5. ERP requiere documentación/ejemplo de respuesta sin datos personales y confirmación del flujo de aprobaciones. No hay sincronización de solicitudes hacia Simplifica. El portal puede presentar datos locales aunque el ERP esté caído; el permiso de acceso depende del campo local activo, por lo que las bajas deben registrarse también allí.
6. Usuarios siguen administrándose desde Secrets. Es un paso intermedio. Mejor evolución: identidad corporativa con MFA y gestión de roles, manteniendo PostgreSQL para solicitudes y auditoría.
7. Límite global de login reduce password spraying pero permite denegación temporal de servicio. Falta protección por IP/perímetro. No equivale a seguridad completa ante ataques distribuidos.
8. Auditoría de operaciones es transaccional, pero no inmutable frente a administradores de BD. Falta registro externo y alertas operativas. La expiración de sesión se comprueba en la próxima interacción, sin borrar automáticamente la pantalla abierta.
9. Migraciones requieren DDL al arrancar. Mejor alternativa siguiente: migraciones ejecutadas por despliegue y usuario SQL de aplicación con privilegios mínimos.
10. Dependencias tienen límites de versión mayor, sin lock de hashes; el entorno debe fijarse después de probar la instalación. Panel limitado a 1000 solicitudes recientes.

## Recomendación

Conservar Streamlit y desplegar primero una instancia de pruebas con BD independiente. Ejecutar toda la batería, revisar la migración sobre una copia de datos y validar ambos roles. Después publicar la versión validada y rotar credenciales del prototipo. El principal pendiente ya no es escribir la app: es verificarla con infraestructura y reglas laborales reales.

Referencia técnica consultada para la API de Argon2id/rehash: https://argon2-cffi.readthedocs.io/en/stable/api.html
Referencia de pruebas de Streamlit: https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest

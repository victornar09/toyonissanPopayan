# Despliegue del servidor local — Toyonissan Popayán

Esta guía describe cómo ejecutar Toyonissan en un PC Windows que actúa como
servidor para la red local (LAN). Los demás equipos usan únicamente el
navegador; nunca abren la base SQLite ni las carpetas del servidor.

> **No exponga la aplicación a Internet.** No abra puertos en el router ni
> configure redirecciones NAT. La aplicación está pensada solo para la LAN.

---

## 1. Arquitectura

```
PC servidor (Windows)
├── Flask + Waitress (host/puerto configurados)
├── SQLite activa en bdlocal/ (modo WAL)
├── bin/xml/        XML originales
├── bin/pdf/        PDF de facturas
├── bin/codigos/    códigos de barras
├── bartenderFiles/ plantillas .btw
├── temp/           CSV únicos de cada trabajo de impresión
├── backups/        respaldos diarios
├── logs/           servidor.log
└── config/         servidor.json, estado y secreto local

Clientes LAN ── navegador ── HTTP ──► PC servidor
```

BarTender, la impresora de etiquetas y las plantillas están instalados en el
PC servidor. El servidor encola los trabajos y los procesa de uno en uno.

---

## 2. Requisitos en el PC servidor

- Windows 10/11 o Windows Server.
- Python 3.10 o superior (probado con 3.14).
- Dependencias:

```powershell
python -m pip install -r requirementsWindows.txt
```

Paquetes clave: `Flask`, `waitress`, `pywin32`, `cryptography`,
`reportlab`, `pandas`, `python-barcode`.

- BarTender instalado y activado.
- Impresora de etiquetas instalada o compartida en el servidor.
- Plantillas `.btw` disponibles en `bartenderFiles/`.
- La cuenta de Windows que ejecuta el servidor debe poder abrir BarTender por
  COM, ver las plantillas y usar la impresora. Si BarTender se ejecuta como
  otro usuario, la automatización COM fallará.

---

## 3. Variables de entorno recomendadas

Defina una clave secreta propia antes de operar en producción. Si no existe,
la aplicación genera una clave aleatoria en `config/.secret_key` y lo advierte
en el log; nunca usa una contraseña fija en el código.

```powershell
[Environment]::SetEnvironmentVariable(
  "TOYONISSAN_SECRET_KEY",
  "una-cadena-larga-y-aleatoria",
  "Machine"
)
```

Variables opcionales:

| Variable | Uso |
| --- | --- |
| `TOYONISSAN_BACKUP_HORA` | Hora del respaldo diario (0–23). Por defecto `2`. |
| `TOYONISSAN_BARTENDER_SIMULADO=1` | Solo pruebas: no llama a BarTender. |
| `TOYONISSAN_PERMITIR_RED_PUBLICA=1` | Excepción explícita; **no usar** salvo laboratorio aislado. |

Tras cambiar variables de entorno, reinicie el servicio o la consola.

---

## 4. Configuración de red

En **Configuración → Servidor y red local**:

- **Nombre del servidor**: identificación visible.
- **Host de escucha**: `0.0.0.0` (recomendado) para aceptar la LAN.
- **Puerto**: por defecto `5000`; válido entre `1024` y `65535`.
- **Hilos**: entre `4` y `8` (por defecto `6`).
- **IP local detectada**: solo lectura; no se guarda.
- **URL para los otros equipos**: por ejemplo `http://192.168.1.50:5000`.
- **Estado**: indica si el puerto está escuchando.
- **Guardar configuración** y **Probar acceso local**.

La configuración estable se guarda en `config/servidor.json`.
La IP detectada y el estado de ejecución se guardan aparte en
`config/estado_servidor.json` y nunca forman parte de la configuración.

Cambiar host o puerto **no reinicia** la aplicación. Detenga el servicio y
vuelva a iniciarlo. No se reinicia Flask desde una petición web.

### IP fija o reserva DHCP

1. En el router, asigne una **reserva DHCP** por MAC al PC servidor, o
2. En Windows configure una IP fija coherente con la red:
   `Configuración → Red e Internet → Ethernet → Propiedades → IPv4`.

Evite dejar la IP del servidor por DHCP sin reserva: la URL cambiaría y los
clientes dejarían de encontrarlo.

### Firewall de Windows

Permita solo redes privadas y solo el puerto configurado. Ejemplo con
PowerShell (ejecutar como administrador):

```powershell
New-NetFirewallRule `
  -DisplayName "Toyonissan LAN 5000" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 5000 `
  -Action Allow `
  -Profile Private
```

Compruebe que la red del servidor esté marcada como **Privada**, no Pública.

### URL para los clientes

Con IP `192.168.1.50` y puerto `5000`:

```
http://192.168.1.50:5000
```

Los clientes deben escribir esa URL. No deben abrir `bdlocal/*.sqlite`, ni
rutas `\\SERVIDOR\...`, ni archivos locales. PDF y códigos se sirven por rutas
HTTP protegidas por login.

---

## 5. Iniciar y detener

### Consola (pruebas y operación simple)

```powershell
cd C:\ruta\a\toyonissanPopayan
python run_server.py
```

También `python app.py` delega en `run_server.py`; nunca arranca el servidor de
desarrollo de Flask.

La consola y `logs/servidor.log` muestran:

```
Acceso en este equipo : http://127.0.0.1:5000
Acceso para la LAN    : http://192.168.1.50:5000
```

Detener: `Ctrl+C`.

### Como servicio de Windows

Opción recomendada: **NSSM** o el Programador de tareas.

Con NSSM, ejecutando como administrador:

```powershell
nssm install Toyonissan "C:\Python314\python.exe" "C:\ruta\toyonissanPopayan\run_server.py"
nssm set Toyonissan AppDirectory "C:\ruta\toyonissanPopayan"
nssm set Toyonissan AppStdout "C:\ruta\toyonissanPopayan\logs\servicio_out.log"
nssm set Toyonissan AppStderr "C:\ruta\toyonissanPopayan\logs\servicio_err.log"
nssm set Toyonissan ObjectName ".\USUARIO_BARTENDER"
nssm start Toyonissan
```

El servicio debe ejecutarse con la misma cuenta de Windows que tiene BarTender,
la impresora y las plantillas disponibles. Si BarTender exige sesión
interactiva, use el Programador de tareas con “Ejecutar solo cuando el usuario
haya iniciado sesión”.

Detener servicio:

```powershell
nssm stop Toyonissan
```

---

## 6. SQLite y concurrencia

Cada solicitud abre su propia conexión con:

- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`
- `PRAGMA busy_timeout = 30000`
- `PRAGMA synchronous = NORMAL`

Las transacciones son breves. No se mantiene una transacción abierta mientras
se envía correo, se procesa Excel/XML, se genera un PDF o se imprime.

Protecciones incluidas:

- Clave de idempotencia por formulario: un doble clic no crea dos trabajos de
  impresión ni dos envíos.
- Mensaje comprensible si SQLite está ocupado (`database is locked`).
- Reintento y reimpresión sin revertir inventariado ni regenerar códigos.

### Limitaciones actuales

SQLite en WAL soporta bien lecturas concurrentes y un escritor a la vez. Es
adecuado para el uso actual de oficina, pero:

- Las escrituras simultáneas se serializan; muchas escrituras largas pueden
  provocar esperas.
- No hay servidor de base de datos central con réplicas ni failover.
- El archivo SQLite y sus archivos `-wal`/`-shm` no deben copiarse a mano con
  la aplicación en marcha.
- El rendimiento puede degradarse con tablas muy grandes o consultas pesadas.

### Migración futura a PostgreSQL

Si aumentan los usuarios concurrentes o las escrituras simultáneas:

1. Mantener la interfaz de acceso en `db.py` como capa de abstracción.
2. Migrar el esquema (`database/estructura.sql`) a PostgreSQL.
3. Sustituir `sqlite3` por `psycopg` y adaptar `?` a `%s`.
4. Revisar `AUTOINCREMENT`, `datetime('now','localtime')` y `INSERT OR IGNORE`.
5. Mantener la cola de impresión como tabla; el worker no cambia.
6. Configurar respaldos con `pg_dump` en lugar de la API `backup` de SQLite.

---

## 7. Cola de impresión

Estados:

| Estado | Significado |
| --- | --- |
| `PENDIENTE` | En cola, aún no procesado. |
| `PROCESANDO` | El worker lo tomó y está enviando a BarTender. |
| `IMPRESO` | BarTender terminó sin error. |
| `ERROR` | Falló; inventariado y código se conservan. |

Cada trabajo registra: ID, usuario, fecha, artículos, cantidades, plantilla,
archivo temporal, estado, error e intentos.

- Un único worker procesa un trabajo a la vez.
- Archivo único por trabajo:
  `temp/impresion_<id_trabajo>_<fecha_hora>.csv`.
- Antes de imprimir, los mismos datos se publican de forma atómica en el CSV
  que tienen enlazado las plantillas: `productos_temp.csv` en la raíz del
  proyecto. La ruta se puede cambiar con `TOYONISSAN_CSV_IMPRESION`.
- Si el CSV compartido está bloqueado por BarTender, el trabajo queda en
  `ERROR`; así se evita imprimir con los valores del trabajo anterior.
- La transacción se cierra antes de llamar a BarTender.
- Si falla: el trabajo queda en `ERROR`, no se revierte el inventariado, no se
  genera otro código y se puede **Reintentar** o **Reimprimir** desde
  Configuración → Cola de impresión.
- Si el servidor se interrumpe durante un trabajo, al arrancar se marca como
  `ERROR` con el mensaje correspondiente.

La cola se consulta en `GET /impresion/` y el estado individual en
`GET /impresion/<id>`. Las acciones requieren sesión y token CSRF.

---

## 8. Seguridad

- Login obligatorio en todas las rutas salvo `/login` y archivos estáticos.
- Guardia de red: solo loopback y direcciones privadas. Las IP públicas se
  rechazan salvo la variable de excepción explícita.
- CSRF en `POST`, `PUT`, `PATCH` y `DELETE`; el token va en la sesión y se
  envía por formulario o cabecera `X-CSRF-Token`.
- Secreto de sesión por `TOYONISSAN_SECRET_KEY` (o archivo aleatorio local).
- Token de correo cifrado en la base con clave derivada del secreto.
- Auditoría en la tabla `auditoria`: fecha, usuario, IP, equipo, método, ruta,
  acción y estado HTTP. No se registran contraseñas ni tokens.
- No se muestran rutas físicas en la interfaz; las plantillas se muestran con
  su nombre de archivo.
- `debug` desactivado y servidor Waitress, no el servidor de desarrollo.

---

## 9. Respaldos

### Automático

`run_server.py` inicia un hilo que respalda todos los días a las `02:00`
(configurable con `TOYONISSAN_BACKUP_HORA`).

Cada respaldo crea `backups/respaldo_AAAA-MM-DD_HHMMSS/` con:

- `bdlocal/<base>.sqlite` — copia consistente vía API `backup` de SQLite.
- `bin/xml/`, `bin/pdf/`, `bin/codigos/`, `bin/xlsx/`.
- `bartenderFiles/`.
- `config/`.
- `catalogos/catalogo_codigos_barras.csv` y `catalogos/estructura.sql`.
- `manifiesto.json` con fecha, elementos y errores.

Se conservan los últimos 14 respaldos por defecto.

### Manual

```powershell
python respaldo_sistema.py
python respaldo_sistema.py --retencion 30
python respaldo_sistema.py --destino D:\respaldos\toyonissan_manual
```

### Recuperación

1. Detenga el servicio (`Ctrl+C` o `nssm stop Toyonissan`).
2. Copie el respaldo a una carpeta local del servidor.
3. Restaure la base:
   ```powershell
   Copy-Item "backups\respaldo_...\bdlocal\toyonissan.sqlite" "bdlocal\toyonissan.sqlite" -Force
   ```
   Elimine `bdlocal\toyonissan.sqlite-wal` y `bdlocal\toyonissan.sqlite-shm`
   antiguos si existen.
4. Restaure `bin/`, `bartenderFiles/`, `config/` y catálogos si aplica.
5. Inicie el servidor y verifique login, Inventario, un PDF y la cola.
6. No restaure un respaldo mientras el servidor está escribiendo.

---

## 10. Pruebas realizadas

Suite automatizada:

```powershell
python -m unittest discover -s tests
```

Resultado actual: **84 pruebas OK**.

Cobertura de los escenarios solicitados:

1. Acceso desde el servidor — `Probar acceso local` y arranque Waitress.
2. Acceso desde dos PC de la LAN — guardia de IP privada; validar manualmente
   con dos navegadores.
3. Consulta simultánea del Inventario — WAL y conexión por solicitud.
4. Edición desde un cliente y visualización desde otro — base única del servidor.
5. Dos solicitudes de impresión cercanas — prueba de dos trabajos.
6. Archivos temporales únicos — prueba de nombres `impresion_<id>_<fecha>.csv`.
7. Error de BarTender y reimpresión — prueba de estado `ERROR` + reintento.
8. Reinicio conservando host y puerto — prueba de persistencia JSON.
9. Apertura de PDF desde un cliente — rutas HTTP con `send_from_directory`.
10. Intento de abrir la base desde otro PC — no hay rutas compartidas ni
    acceso a SQLite; solo HTTP autenticado.
11. Escrituras concurrentes con WAL — prueba de 8 hilos.
12. Acceso desde red no autorizada — prueba de IP pública bloqueada (403).

Las pruebas de dos equipos físicos, BarTender real y Firewall deben ejecutarse
en el entorno de producción siguiendo esta guía.

---

## 11. Diagnóstico rápido

| Síntoma | Revisión |
| --- | --- |
| Los clientes no conectan | IP/puerto, red privada, regla de Firewall, servicio iniciado. |
| `database is locked` | Otra operación larga; espere; revise respaldos/consultas manuales. |
| BarTender no imprime | Sesión de Windows, COM, plantilla `.btw`, impresora predeterminada. |
| La URL cambió | IP fija o reserva DHCP. |
| Token de correo inválido | Vuelva a guardarlo en Configuración; se cifra de nuevo. |
| Sesiones se cierran al reiniciar | Configure `TOYONISSAN_SECRET_KEY` estable. |

# Toyonissan Popayán - Sistema de Gestión de Inventario

Aplicación web para gestionar el inventario de repuestos de Toyota/Nissan en Popayán. Permite descargar facturas electrónicas desde correo, "inventariar" las líneas de factura estandarizando descripciones, normalizando referencias/marcas/lados/repuestos, generando códigos de barras y etiquetas para impresión con BarTender, y manteniendo un inventario unificado con precios cifrados para impresión.

## Propósito

El objetivo es agilizar el paso de **factura → inventario**:
- **Descargar facturas** desde Gmail (archivos ZIP con XML/PDF enviados por los proveedores).
- **Inventariar** (revisar y procesar) cada línea de factura: ajustar descripción, referencia del repuesto, equivalente, marca/carro, lado, precio de venta, etc.
- **Estandarizar** la información usando el archivo `bin/xlsx/estandarizacion.xlsx` (diccionarios de repuestos, lados, abreviados, ajustes, abreviaturas).
- **Unificar el inventario** (`inventarioUnico`) con clave normalizada (`descripcionClave`) para evitar duplicados, acumulando cantidades al inventariar.
- **Imprimir etiquetas** con BarTender (`bartenderFiles/`) generando un CSV temporal `productos_temp.csv`.
- **Gestionar catálogo maestro**: proveedores, ubicaciones, grupos, base de datos activa, configuraciones de correo, etc.

## Arquitectura

- **Framework**: Flask (blueprints)
- **Base de datos**: SQLite (múltiples bases gestionadas desde `bdlocal/` con archivo `.bd_activa` que indica cuál está activa)
- **Procesamiento de datos**: pandas para normalización/estandarización masiva
- **Lectura de correo**: IMAP (Gmail) con `imaplib`, extracción de ZIP/XML/PDF
- **XML**: `xml.etree.ElementTree` (soporta UBL con namespaces cac/cbc; también maneja `AttachedDocument`)
- **Etiquetado**: BarTender vía COM (`win32com.client`), generación de CSV
- **Cifrado/normalización**: utilidades propias (`utilidades.py`) para normalizar claves y "cifrar" precios para impresión

## Estructura del proyecto

```
toyonissanPopayan/
├── app.py                # Punto de entrada, blueprints, rutas principales (/ , /configuracion), lógica de consolidado
├── auth.py               # Autenticación/login
├── db.py                 # Gestión centralizada de BD (bdlocal/, .bd_activa, conexión)
├── config.py             # Valores por defecto (vacíos)
├── migraciones.py        # Migraciones idempotentes al arrancar
├── utilidades.py         # normalizar_clave, cifrar_precio, a_entero
├── requirements.txt
├── requirementsWindows.txt
├── inserts.txt
├── productos_temp.csv    # Generado al imprimir
├── bartendersFiles/      # Plantillas .btw de BarTender
├── bdlocal/              # Bases SQLite creadas/activas (.sqlite + .bd_activa)
├── bin/
│   ├── codigos/          # PDFs/archivos servidos por /bin/codigos/<filename>
│   ├── pdf/              # PDFs extraídos de facturas
│   ├── xml/              # XML extraídos de facturas
│   ├── xlsx/
│   │   ├── CONSOLIDADO.xlsx        # Origen para actualizar consolidado maestro
│   │   └── estandarizacion.xlsx   # Diccionarios para estandarización
│   └── ... 
├── database/
│   └── estructura.sql    # Esquema inicial + proveedores base + config inicial
├── facturas/             # Blueprint de facturas (descarga IMAP, ver factura, inventariar)
│   ├── __init__.py
│   └── routes.py
├── grupos/               # Blueprint de grupos
├── inventario/           # Blueprint de inventario (listar, actualizar, imprimir)
│   ├── __init__.py
│   └── routes.py
├── ion/                  # Blueprint ion
├── procesamiento/
│   └── consolidado.py    # Lógica de estandarización (leer diccionarios, extraer repuesto/marca/lado/modelo/medida, armar descripcionEstandarizada)
├── static/               # CSS/JS/imagenes
├── templates/            # Plantillas HTML (configuracion, facturas, inventario, etc.)
└── users.py
```

## Flujo de trabajo

### 1. Configuración inicial
- Ir a `/configuracion`.
- Crear/activar base de datos SQLite (se crea desde `database/estructura.sql` y se aplican migraciones con `migraciones.migrar()`).
- Guardar configuración (correo de revisión, token/app password, correos varios).
- Registrar proveedores, ubicaciones, templates de BarTender.

### 2. Descargar facturas desde correo
Ruta: `/facturas/` (POST)
1. Lee configuración activa (email/password) desde tabla `configuracion`.
2. Conecta a IMAP Gmail (`imap.gmail.com`), carpeta `[Gmail]/Todos`.
3. Busca correos con `SINCE/BEFORE` y por `SUBJECT` con el NIT de cada proveedor (tabla `proveedor`).
4. Extrae archivos `.zip` adjuntos, guarda en `bin/`.
5. Extrae `.xml` a `bin/xml/` y `.pdf` a `bin/pdf/`.
6. Parsea XML (UBL): obtiene proveedor, fecha (`IssueDate`), valor total (`LegalMonetaryTotal/PayableAmount`), líneas (`InvoiceLine`). Soporta `AttachedDocument` donde el XML viene en `cbc:Description`.
7. Guarda factura en `facturas` (evita duplicados por `id_factura`). Guarda líneas en `inventarioFacturas` con `ON CONFLICT(id_factura, referencia, descripcion_item) DO UPDATE`.
8. Muestra lista de facturas con conteo de artículos sin procesar.

### 3. Inventariar (factura → inventario)
Ruta: `/facturas/ver/<id_factura>` (POST al seleccionar líneas)
1. Selecciona líneas a inventariar desde `inventarioFacturas` (no inventariadas o según selección).
2. Para cada línea se puede editar descripción/referencia/valor público antes de guardar.
3. Calcula: `precio_venta` (entero), `precio_cifrado` con `cifrar_precio()`, `precio_max_desc = precio_venta * 0.9` redondeado.
4. Genera `descripcionClave = normalizar_clave(descripcion)`.
5. Inserta/actualiza en `inventarioUnico` con `ON CONFLICT(descripcionClave) DO UPDATE`: suma cantidad, actualiza precio/proveedor/fecha/grupo, mantiene referencia.
6. Marca `inventarioFacturas.inventariado = 1` y relaciona `id_inventarioUnico`.
7. Recolecta items a imprimir (con código de barras, descripción cifrada, cantidad, sigla proveedor) y llama `imprimir_varios()` con template BarTender seleccionado → genera `productos_temp.csv` y abre/manda a imprimir con COM.
8. Redirige al detalle.

### 4. Estandarizar y actualizar consolidado maestro
Hay dos caminos:

**A) Desde `/configuracion` → "Actualizar consolidado" (POST con acción `actualizar_consolidado`)**
- Lee `bin/xlsx/CONSOLIDADO.xlsx` y `bin/xlsx/estandarizacion.xlsx`
- `procesamiento.consolidado.procesar_consolidado()`:
  - Carga diccionarios desde hojas de estandarización: `ESTANDARIZACION`, `LADOS`, `ABRELADO`, `AJUSTES`, `ABREVIATURAS`
  - `agrupar_duplicados_consolidado()`: agrupa por `(abrevProveedor, referenciaProveedor)`, escoge registro "principal" por puntuación (prioriza descripción, luego carro, luego referenciaRepuesto, luego equivalente) y completa campos vacíos
  - `reemplazar_descripcion_excel()` aplica ajustes con regex (palabras completas)
  - Por cada fila: detectar marca si falta (`carro`), extraer `lado`/`ladoAbreviado`, extraer/estandarizar `repuesto`, extraer `modelo`, extraer `otros` (paréntesis), extraer `medida` de `referenciaRepuesto` o `equivalente` (busca en `MEDIDAS_VALIDAS`)
  - Genera `descripcionEstandarizada = repuesto + medida + ladoAbreviado + carro + modelo + otros`
- `app.actualizar_consolidado()` sincroniza con tabla `consolidado` por clave `(abrevProveedor, referenciaProveedor)`: inserta nuevos, actualiza si hay cambios en campos monitoreados, cuenta totales.

**B) Desde `/configuracion/procesar-pendientes` (procesar líneas de factura hacia consolidado)**
- Busca factura por `id_factura`, carga líneas desde `inventarioFacturas`
- Construye DataFrame con datos editados (referenciaProveedor, referenciaRepuesto, equivalente, descripcion, marca, carro, precioListado)
- `procesamiento.consolidado.procesar_dataframe_pendientes()` aplica misma estandarización (sin agrupado masivo)
- `actualizar_consolidado()` guarda en tabla `consolidado`

### 5. Inventario general
Ruta: `/inventario/`
- Listado completo de `inventarioUnico` (código de barras, referencia, descripción, cantidad, precios, proveedor, fecha, grupo)
- Acciones: descargar inventario CSV, actualizar inventario desde facturas (`_actualizar_inventario_desde_facturas()`), imprimir etiqueta individual/modificada
- Al actualizar desde facturas: recorre todas las líneas de `inventarioFacturas` ordenadas por fecha desc, agrupa por `normalizar_clave(descripcion_item)`, acumula cantidades, toma precio unitario (redondeado a miles para precio venta), genera precios, hace `UPSERT` en `inventarioUnico` por `descripcionClave`, marca líneas como inventariadas y relaciona

## Modelo de datos (SQLite)

Ver `database/estructura.sql`. Tablas principales:

| Tabla | Propósito |
|---|---|
| `facturas` | Cabecera de facturas descargadas (id_factura, proveedor, fecha, valor_total, correo, texto_xml, ubicacion_pdf) |
| `inventarioFacturas` | Líneas de factura (descripcion_item, cantidad, valor_unitario, referencia, inventariado, id_inventarioUnico). Índice único `(id_factura, referencia, descripcion_item)` |
| `inventarioUnico` | Inventario consolidado unificado. Clave única `descripcionClave` (normalizada). `codigoBarras` auto-generado por trigger con `printf('%08d', id)`. |
| `consolidado` | Catálogo maestro estandarizado por proveedor/referencia (abrevProveedor + referenciaProveedor únicos). Almacena campos estandarizados (repuesto, lado, carro, modelo, medida, descripcionEstandarizada, etc.) |
| `proveedor` | Maestros de proveedores (nombre, siglas, correoFacturas, prefijo, nit, etc.) |
| `configuracion` | Configuración activa (email_revision, token_contraseña/app password, correos_varios) |
| `Ubicacion` | Ubicaciones físicas con trigger para generar `descripcion` tipo `P-X Pllo-Y E/C/V-Z` |
| `bartenderFiles` | Plantillas BarTender (name, ubicacion, numeroColumnas, activo) |
| `grupoUnico` | Grupos de productos |

**Notas importantes:**
- `inventarioUnico.descripcionClave` se normaliza con `utilidades.normalizar_clave()` (quita tildes, pasa a mayúsculas, colapsa no alfanuméricos a espacios).
- Migraciones (`migraciones.py`) corrigen esquemas antiguos: convierten `inventarioFacturas` sin clave única problemática, añaden `descripcionClave` a `inventarioUnico`, fusionan duplicados por clave normalizada.
- `db.py` gestiona base activa vía `bdlocal/.bd_activa`; `db.conexion()` es context manager con commit/rollback.

## Utilidades clave

**`utilidades.py`**
- `normalizar_clave(texto)`: genera clave lógica para deduplicación
- `cifrar_precio(precio)`: ofusca precio con mapa "chapineros" (dígitos→letras) para etiquetas
- `a_entero(valor, defecto)`: limpia separadores de miles y convierte a entero

**`procesamiento/consolidado.py`**
- Lee diccionarios multi-hoja desde `estandarizacion.xlsx`
- Extrae componentes: marca (desde abreviaturas), lado (desde diccionario LADOS), repuesto (palabras antes de marca/lado), modelo (después de marca), otros (entre paréntesis), medida (desde referencias con valores en `MEDIDAS_VALIDAS`)
- Estandariza repuesto con reglas regex desde hoja `ESTANDARIZACION`
- Aplica ajustes desde hoja `AJUSTES`
- Genera `descripcionEstandarizada` concatenando: repuesto + medida + ladoAbreviado + carro + modelo + otros

## Configuración y ejecución

### Requisitos
- Python 3.x
- En Windows: `pywin32` para BarTender COM
- Dependencias en `requirements.txt` / `requirementsWindows.txt`

### Variables de entorno
- `TOYONISSAN_SECRET_KEY` (default: clave-secreta-muy-segura)
- `TOYONISSAN_DEBUG` ('1' por defecto)

### Puesta en marcha
```bash
# Instalar dependencias
pip install -r requirements.txt  # o requirementsWindows.txt en Windows

# Ejecutar
python app.py
```
Al arrancar:
1. Llama `inicializar_bd()` que ejecuta `migraciones.migrar()` sobre la BD activa
2. Abre navegador a `http://localhost:5000` tras 1.25s
3. Flask corre en modo debug según variable

### Primer uso
1. Acceder a `/configuracion`
2. Crear base de datos (ej. `toyonissan`) → crea desde `estructura.sql` + migraciones
3. Configurar email/token de Gmail (usar contraseña de aplicación para IMAP)
4. Revisar proveedores pre-cargados
5. Colocar archivos necesarios: `bin/xlsx/CONSOLIDADO.xlsx`, `bin/xlsx/estandarizacion.xlsx`, plantillas `.btw` en `bartenderFiles/`

## Guía para orientarte en el código

| Si quieres... | Revisar |
|---|---|
| Entender arranque/app general | `app.py` (blueprints, rutas `/`, `/configuracion`, `/ejecutar`, lógica consolidado) |
| Gestionar BD activa/múltiples bases | `db.py` |
| Ver/esquema y datos base | `database/estructura.sql` |
| Descargar facturas desde correo | `facturas/routes.py` (`/facturas/`, parseo XML/ZIP) |
| Inventariar líneas de factura | `facturas/routes.py` (`/facturas/ver/<id_factura>`) |
| Ver/editar/imprimir inventario | `inventario/routes.py` |
| Estandarizar descripciones/referencias | `procesamiento/consolidado.py` |
| Normalizar/cifrar/precios | `utilidades.py` |
| Corregir esquema al actualizar | `migraciones.py` |
| Autenticación | `auth.py` |
| Grupos/config extras | `grupos/`, `ion/` |

## Notas operativas

- **Gmail IMAP**: Requiere contraseña de aplicación. El token se guarda en `configuracion.token_contraseña`.
- **BarTender**: Requiere Windows + BarTender instalado + `pywin32`. Usa COM; abre aplicación visible (`bt.Visible = True`). Genera `productos_temp.csv` en raíz.
- **Precios**: Se redondean a miles para precio venta en algunas rutas (`round(precio_venta, -3)`) y se aplica 10% de descuento para `precioMaxDescuento`.
- **Duplicados**: La deduplicación se hace por `descripcionClave` normalizada. Al inventariar desde factura se suma cantidad si ya existe.
- **PDFs/XML**: Se guardan en `bin/pdf/` y `bin/xml/`. La ruta PDF se guarda en `facturas.ubicacion_pdf`.
- **Consolidado**: Es el catálogo maestro para estandarizar; se actualiza desde Excel o desde "procesar pendientes" de facturas.

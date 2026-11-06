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

## Interfaz y experiencia de usuario

La interfaz se unificó con estilos compartidos en `static/assets/css/toyonissan.css` y encabezados reutilizables en `templates/includes/page_header.html`.

- **Títulos consistentes**: todas las páginas muestran título, descripción breve, icono y breadcrumb (Inicio → módulo → página). Los iconos son SVG inline (`templates/includes/iconos.html`), sin librerías ni fuentes externas.
- **Espaciado general**: `.content` agrega padding superior/inferior y laterales; las secciones se separan en tarjetas con márgenes propios.
- **Diseño responsive**: contenedores fluidos y scroll horizontal (`.tabla-scroll`, `.table-responsive`) en listados amplios (facturas, inventario, grupos, configuración y tablas del detalle), evitando columnas comprimidas.
- **Panel de control**: la página de inicio enlaza a Facturas, Inventario, Grupos, ION, Configuración y Procesar pendientes.

### Detalle de factura

- Se organizó en secciones: **Información de la factura**, **Acciones**, **Leyenda de estados**, **Artículos para inventariar** y **Procesar artículos pendientes**.
- **Leyenda de estados** con badges compactos (icono + color + texto corto + tooltip):
  - **Inventariado**: ya ingresó al inventario y no puede seleccionarse.
  - **Permitido** (NO INVENTARIADO PERMITIDO): existe en consolidado y puede inventariarse.
  - **No permitido** (NO INVENTARIADO NO PERMITIDO): debe procesarse primero en consolidado.
- Los botones **Ver PDF** y **Procesar consolidado** quedan juntos en la barra de acciones.
- Ambas tablas tienen scroll horizontal y cifras alineadas; las filas inventariadas se atenúan y no son interactivas.
- **Consolidado con procesamiento selectivo**: solo se procesan las filas marcadas; el botón **Procesar seleccionados** se habilita con la selección y hay contador de seleccionados.
- Se conserva **Seleccionar todos los pendientes**, el resaltado de filas seleccionadas y el clic sobre la fila para alternar el checkbox.
- **Campos editables** de consolidado con nombres y ayudas claras:
  - **Referencia proveedor** (ID del proveedor)
  - **Descripción**
  - **Referencia repuesto** (código interno)
  - **Equivalente** (referencias alternas)
  - **Marca artículo** (fabricante del repuesto)
  - **Marca vehículo** (Toyota, Nissan…)
  - **Modelo vehículo** (Hilux, Frontier…)
  - **Lado** (izquierdo/derecho)
  - **Medida**
- La sección de consolidado se muestra/oculta en la misma página con el atributo `hidden`; no se abren pestañas nuevas ni se anidan formularios.
- El total de la factura se muestra con formato monetario colombiano (`$ 11.405.736`).
- **Visualización del PDF**: el botón *Ver PDF* abre el archivo en una pestaña nueva; si la factura no tiene PDF asociado, el botón aparece deshabilitado.

### Lectura del total desde XML

El total se extrae con el módulo reutilizable `facturas/xml_utils.py`:

- Soporta XML UBL `Invoice` / `CreditNote` directos y `AttachedDocument` con la factura embebida en `cbc:Description`.
- Compara etiquetas por **nombre local**, por lo que no depende de prefijos de namespace (`cac`, `cbc`, `a`, `fe`, etc.).
- Prioridad: `LegalMonetaryTotal/PayableAmount`; respaldos: `TaxInclusiveAmount` y `LineExtensionAmount`.
- `parsear_importe()` acepta enteros, decimales, separadores de miles/decimales colombianos o anglosajones, espacios y símbolos de moneda; usa `Decimal` para evitar pérdida de precisión.
- Si el XML no contiene un total válido, se registra un diagnóstico, se guarda `NULL` (nunca `0` silencioso) y se informa en consola.
- Los registros históricos con total `0`/`NULL` se reparan automáticamente desde `bin/xml/` al abrir la lista o el detalle (`reparar_totales_faltantes()` / `reparar_total_factura()`).
- `formatear_moneda()` centraliza el formato `$ 1.234.567`.

## Arquitectura

- **Framework**: Flask (blueprints)
- **Base de datos**: SQLite (múltiples bases gestionadas desde `bdlocal/` con archivo `.bd_activa` que indica cuál está activa)
- **Procesamiento de datos**: pandas para normalización/estandarización masiva
- **Lectura de correo**: IMAP (Gmail) con `imaplib`, extracción de ZIP/XML/PDF
- **XML**: `xml.etree.ElementTree` (UBL con namespaces dinámicos; `<Invoice>` directo y `<AttachedDocument>` con factura embebida) mediante `facturas/xml_utils.py`
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
│   ├── routes.py
│   └── xml_utils.py      # Lectura reutilizable de importes XML UBL + formato de moneda
├── grupos/               # Blueprint de grupos
├── inventario/           # Blueprint de inventario (listar, actualizar, imprimir)
│   ├── __init__.py
│   └── routes.py
├── ion/                  # Blueprint ion
├── procesamiento/
│   └── consolidado.py    # Lógica de estandarización (leer diccionarios, extraer repuesto/marca/lado/modelo/medida, armar descripcionEstandarizada)
├── static/               # CSS/JS/imagenes (assets/css/toyonissan.css = estilos compartidos)
├── templates/            # Plantillas HTML (configuracion, facturas, inventario, etc.)
├── tests/                # Pruebas unitarias (lectura de importes XML UBL)
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
6. Parsea XML (UBL): obtiene proveedor, fecha (`IssueDate`), valor total (`LegalMonetaryTotal/PayableAmount` con respaldos `TaxInclusiveAmount`/`LineExtensionAmount`) y líneas (`InvoiceLine`). Soporta `AttachedDocument` donde el XML viene en `cbc:Description`. Ver `facturas/xml_utils.py`.
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
- El mismo flujo está disponible en el detalle de factura, donde solo se procesan las filas seleccionadas.
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


# Visión funcional del módulo Consolidado e Inventario

## Objetivo general

La aplicación debe permitir conocer, identificar, ubicar, etiquetar, consultar, contar y exportar los artículos que existen físicamente en el local de Toyonissan Popayán.

La arquitectura funcional se divide en cuatro responsabilidades:

```text
Facturas = origen documental de las compras
Consolidado = catálogo maestro de artículos conocidos y estandarizados
Inventario = artículos administrados físicamente en el local y asociados a un código de barras
Conteo físico = verificación periódica de cantidades, ubicación y datos comerciales
```

El sistema debe facilitar tanto la gestión del almacén como la venta, permitiendo buscar artículos por código de barras, descripción, tipo de repuesto, referencia, equivalente, proveedor, marca del artículo, marca del vehículo, modelo, lado, grupo, ubicación o estante.

---

## 1. Módulo Facturas

Facturas representa las compras y conserva la información original obtenida desde los archivos XML y PDF.

Flujo esperado:

```text
Correo / XML
→ Factura
→ Líneas de factura
→ Validación y estandarización
→ Consolidado
```

Desde el detalle de una factura se debe poder:

- Consultar el PDF.
- Revisar sus líneas.
- Procesar artículos pendientes hacia Consolidado.
- Editar los datos necesarios antes de consolidar.
- Inventariar artículos permitidos cuando ya tengan una relación válida con Consolidado.

El inventariado desde Facturas debe reutilizar el mismo servicio de inventariado disponible desde Consolidado. No deben existir dos lógicas diferentes para crear artículos, generar códigos o imprimir etiquetas.

---

## 2. Módulo Consolidado

Consolidado debe ser una sección principal visible en el menú, al mismo nivel que Facturas e Inventario.

Su función es ser el catálogo maestro de todos los artículos conocidos y estandarizados, aunque todavía no estén identificados físicamente ni tengan código de barras.

Consolidado debe responder:

- ¿Qué productos han aparecido en las facturas?
- ¿Cómo debe llamarse correctamente cada producto?
- ¿Cuál es su referencia de proveedor, referencia de repuesto o equivalente?
- ¿A qué vehículo, modelo y lado corresponde?
- ¿Está completo y listo para inventariar?
- ¿Ya fue llevado a Inventario?

### Estados sugeridos

- `PENDIENTE_REVISION`: faltan datos o deben verificarse.
- `LISTO_INVENTARIAR`: cumple los campos mínimos.
- `INVENTARIADO`: ya está relacionado con un artículo de Inventario.
- `BLOQUEADO`: presenta duplicidad, ambigüedad o datos inválidos.

### Funciones principales

- Listado con filtros y paginación.
- Búsqueda por descripción, referencias, equivalente, proveedor, vehículo, modelo, lado y estado.
- Edición mediante modal.
- Selección individual o múltiple.
- Acción `Inventariar`.
- Identificación visual de artículos inventariados y pendientes.
- Relación trazable entre la línea de factura, Consolidado e Inventario.

---

## 3. Importar artículos desde Facturas hacia Consolidado

Consolidado debe incluir el botón:

**Importar artículos desde facturas**

Este proceso debe permitir seleccionar:

- Fecha inicial.
- Fecha final.
- Proveedor opcional.
- Solo artículos no procesados.

Antes de guardar debe mostrar una vista previa con:

- Facturas encontradas.
- Líneas encontradas.
- Artículos nuevos.
- Artículos ya existentes.
- Coincidencias ambiguas.
- Líneas sin referencia.
- Líneas bloqueadas.

Al confirmar:

- Se importan únicamente líneas válidas.
- Se aplica la estandarización existente.
- Se evita duplicar registros por proveedor y referencia.
- Se relacionan las líneas de factura con Consolidado.
- No se crean artículos en Inventario.
- No se asignan códigos de barras.
- No se modifican cantidades de Inventario.

La importación debe ser idempotente: procesar dos veces el mismo rango no debe duplicar registros.

---

## 4. Inventariar desde Consolidado

Inventariar desde Consolidado significa registrar formalmente un producto como artículo administrado dentro del local.

Al ejecutar esta acción se debe:

1. Validar que el registro esté completo y no esté bloqueado.
2. Comprobar si ya existe un artículo equivalente en Inventario.
3. Crear el artículo si no existe o reutilizarlo cuando corresponda.
4. Generar o reutilizar el código de barras.
5. Relacionar Consolidado con el artículo de Inventario.
6. Permitir asignar ubicación y estante, o dejarlos pendientes.
7. Preparar e imprimir la etiqueta con BarTender.
8. Registrar fecha y usuario del inventariado.
9. Cambiar el estado de Consolidado a `INVENTARIADO`.

La impresión debe estar desacoplada del guardado. Si BarTender falla, el artículo no debe duplicarse y debe existir una opción de reimpresión.

También debe permitirse inventariar varios registros seleccionados, utilizando las mismas validaciones del inventariado individual.

---

## 5. Significado del módulo Inventario

Inventario contiene exclusivamente artículos que la empresa decidió administrar físicamente dentro del local.

Un artículo en Inventario debe poder tener:

- Código de barras.
- Descripción estandarizada.
- Referencias y equivalentes.
- Marca del artículo.
- Marca y modelo del vehículo.
- Lado y grupo.
- Proveedor.
- Costo y precio.
- Ubicación y estante.
- Estado de etiquetado.
- Estado de conteo.
- Cantidad registrada y última cantidad física aprobada.
- Stock mínimo.
- Fecha de creación y último conteo.

Ya no se requiere diferenciar el artículo como `inventariadoTeorico` o `inventariadoFisico`.

La separación correcta es:

| Concepto | Significado |
|---|---|
| Está en Consolidado | El artículo es conocido y está estandarizado |
| Está en Inventario | Tiene identidad operativa y código de barras |
| Está etiquetado | La etiqueta fue impresa o verificada |
| Está ubicado | Tiene ubicación y estante asignados |
| Está contado | Su cantidad fue verificada físicamente |

Un artículo puede estar en Inventario y continuar pendiente de ubicación, etiquetado o conteo.

### Estados sugeridos

```text
estadoEtiquetado:
- PENDIENTE
- IMPRESO
- VERIFICADO

estadoConteo:
- SIN_CONTAR
- CONTADO
- CON_DIFERENCIA
- APROBADO
```

---

## 6. Inventario como herramienta de búsqueda y venta

Inventario debe facilitar la atención al cliente y la venta de repuestos.

Debe permitir filtrar por:

- Código de barras.
- Descripción.
- Tipo de repuesto.
- Referencia.
- Equivalente.
- Marca del artículo.
- Marca del vehículo.
- Modelo del vehículo.
- Lado.
- Proveedor.
- Grupo.
- Ubicación.
- Estante.
- Disponibilidad.
- Stock bajo.
- Estado de etiquetado.
- Estado de conteo.

La tabla debe usar desplazamiento horizontal y, cuando sea viable, mantener fijas las columnas:

- Código de barras.
- Descripción.
- Cantidad.
- Ubicación.
- Acciones.

El botón `Editar` debe abrir un modal para corregir información comercial y de ubicación. Las cantidades no deben editarse directamente; deben cambiar mediante entradas, conteos o movimientos controlados.

---

## 7. Identificación y etiquetado del almacén

Como actualmente no existe un inventario físico completo, se debe realizar un proceso progresivo por estantes.

Flujo esperado:

1. Seleccionar ubicación o estante.
2. Tomar un artículo físico.
3. Buscarlo en Consolidado.
4. Completar o corregir sus datos.
5. Inventariarlo.
6. Generar o reutilizar su código de barras.
7. Imprimir y colocar su etiqueta.
8. Asignar ubicación y estante.
9. Continuar con el siguiente artículo.

En esta etapa la prioridad no es contar cantidades, sino lograr que cada tipo de artículo tenga una identidad confiable, código y ubicación.

Si el producto físico no existe en Consolidado, se debe permitir crearlo manualmente después de verificar que no sea un duplicado.

Las nuevas compras deben seguir entrando desde Facturas y quedar etiquetadas para no aumentar la cantidad de artículos pendientes de identificar.

---

## 8. Conteo físico con lector de códigos

Cuando la mayoría de los artículos esté etiquetada, Inventario debe incluir la opción:

**Iniciar inventario físico**

El conteo se realizará mediante sesiones por ubicación o estante.

Cada sesión debe registrar:

- Nombre.
- Responsable.
- Fecha y hora de inicio.
- Ubicación o estante.
- Estado.
- Fecha de finalización.
- Observaciones.

Flujo esperado:

1. Crear la sesión.
2. Seleccionar el estante.
3. Mantener activo el campo de lectura.
4. Escanear cada unidad física.
5. Sumar una unidad por lectura.
6. Guardar inmediatamente cada lectura.
7. Mostrar el último producto leído y su cantidad acumulada.
8. Permitir deshacer la última lectura.
9. Permitir pausar y reanudar.
10. Finalizar y comparar resultados.

El sistema debe advertir:

- Códigos desconocidos.
- Productos asociados a otro estante.
- Productos sin ubicación.
- Posibles lecturas duplicadas.
- Productos registrados que no fueron encontrados.

---

## 9. Conciliación de cantidades

Escanear no debe modificar inmediatamente la cantidad oficial.

El flujo debe ser:

```text
Lectura
→ Conteo guardado
→ Comparación
→ Revisión
→ Recuento opcional
→ Aprobación
→ Ajuste
```

Resultados posibles:

- Correcto.
- Faltante.
- Sobrante.
- No contado.
- Código desconocido.
- Ubicación incorrecta.

Solo al aprobar el conteo se debe actualizar la cantidad oficial. Cada ajuste debe registrar cantidad anterior, cantidad contada, diferencia, usuario, fecha, sesión y observación.

Una sesión aplicada no puede ejecutarse nuevamente.

---

## 10. Historial de movimientos

Toda modificación de cantidades debe dejar trazabilidad.

Tipos iniciales:

```text
ENTRADA_FACTURA
AJUSTE_CONTEO
CORRECCION
SALIDA_VENTA
DEVOLUCION
```

Cada movimiento debe registrar:

- Artículo.
- Tipo de movimiento.
- Cantidad anterior.
- Cantidad del movimiento.
- Cantidad nueva.
- Origen e identificador del origen.
- Fecha.
- Usuario.
- Observación.

No se deben modificar cantidades directamente sin registrar el motivo.

---

## 11. Exportación al programa contable

Cuando las cantidades físicas sean confiables, Inventario debe permitir exportar la información al programa contable.

El formato dependerá de la plantilla requerida por dicho programa, pero debe contemplar:

- Código interno.
- Código de barras.
- Descripción.
- Referencia.
- Cantidad aprobada.
- Costo.
- Precio.
- Impuesto.
- Grupo.
- Ubicación.
- Proveedor.

La exportación debe validar registros incompletos, registrar fecha y lote, y advertir sobre exportaciones repetidas.

---

## 12. Modelo de datos esperado

### Consolidado

Agregar o verificar:

```text
estadoConsolidado
idInventarioUnico
fechaInventariado
usuarioInventariado
```

### Inventario

Agregar o verificar:

```text
idUbicacion
estadoEtiquetado
estadoConteo
cantidadFisica
fechaUltimoConteo
stockMinimo
fechaCreacion
```

### Líneas de factura

Mantener relación con Consolidado:

```text
inventarioFacturas.idConsolidado
```

### Nuevas entidades futuras

- Sesión de conteo.
- Detalle del conteo.
- Lecturas individuales, si se requiere auditoría detallada.
- Movimientos de inventario.

Todos los cambios deben implementarse mediante migraciones idempotentes y conservar compatibilidad con las bases SQLite existentes.

---

## 13. Prioridad de implementación

1. Auditar el estado actual de Facturas, Consolidado e Inventario.
2. Crear Consolidado como módulo principal visible.
3. Implementar filtros, edición y estados en Consolidado.
4. Importar artículos desde Facturas por rango de fechas.
5. Relacionar líneas de factura con Consolidado.
6. Implementar inventariado individual y múltiple desde Consolidado.
7. Unificar el servicio de inventariado usado por Facturas y Consolidado.
8. Completar ubicación, etiquetado y reimpresión.
9. Fortalecer filtros y edición de Inventario para apoyar ventas.
10. Crear historial de movimientos.
11. Implementar sesiones de conteo físico por estante.
12. Integrar el lector de códigos de barras.
13. Implementar conciliación y aprobación de diferencias.
14. Agregar alertas de stock bajo.
15. Implementar exportación al programa contable.

---

## 14. Restricciones para futuros desarrollos

- No enviar todas las facturas directamente a Inventario.
- No crear códigos de barras durante la importación hacia Consolidado.
- No duplicar registros al procesar nuevamente un rango de fechas.
- No inventariar registros incompletos, bloqueados o ambiguos.
- No implementar lógicas diferentes para inventariar desde Facturas y Consolidado.
- No modificar cantidades sin registrar movimientos.
- No considerar que un artículo inventariado ya fue contado físicamente.
- No crear productos automáticamente a partir de códigos desconocidos.
- No aplicar un conteo sin revisión y aprobación.
- No mezclar el proceso de exportación con el inventariado.
- No depender únicamente del frontend para validar operaciones.
- Mantener la impresión desacoplada de los cambios de base de datos.
- Utilizar consultas parametrizadas, transacciones y migraciones idempotentes.

---

## 15. Instrucción para asistentes de IA

Antes de implementar nuevas solicitudes relacionadas con Facturas, Consolidado o Inventario:

1. Revisar esta visión funcional.
2. Identificar a cuál módulo y fase pertenece el cambio.
3. Inspeccionar rutas, plantillas, JavaScript, esquema y migraciones existentes.
4. Confirmar qué partes ya funcionan y cuáles son únicamente visuales.
5. Reutilizar los servicios actuales y evitar lógica duplicada.
6. Mantener trazabilidad e idempotencia.
7. Preservar la relación Factura → Consolidado → Inventario → Conteo → Movimiento.
8. Implementar únicamente el alcance solicitado en cada prompt.
9. Explicar cualquier modificación del modelo de datos o las reglas de negocio.
10. Validar que cada cambio acerque la aplicación al objetivo final: identificar, etiquetar, ubicar, contar, consultar, vender y exportar los artículos físicos del local.

from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory, flash
from auth import auth
from ion.routes import ion_bp
from facturas.routes import facturas_bp
from inventario.routes import inventario_bp
from grupos.routes import grupo_bp
from consolidado.routes import consolidado_bp
from impresion.routes import impresion_bp
from inventario import respaldo
import configuracion_servidor
import db
import migraciones
import seguridad
import os
import pandas as pd

app = Flask(__name__)
app.secret_key = seguridad.cargar_secreto()

app.register_blueprint(auth)
app.register_blueprint(ion_bp)
app.register_blueprint(facturas_bp)
app.register_blueprint(inventario_bp)
app.register_blueprint(grupo_bp)
app.register_blueprint(consolidado_bp)
app.register_blueprint(impresion_bp)

# Login obligatorio, guardia de LAN, CSRF, auditoría y filtros de seguridad.
seguridad.configurar_seguridad(app)

# Ruta absoluta del directorio 'bin/codigos'
RUTA_CODIGOS = os.path.join(os.path.dirname(__file__), 'bin', 'codigos')



def ejecutar_sql_archivo(conn, archivo_sql):

    ruta_sql = os.path.join(
        os.path.dirname(__file__),
        "database",
        archivo_sql
    )

    with open(
        ruta_sql,
        "r",
        encoding="utf-8"
    ) as archivo:

        sql = archivo.read()

    conn.executescript(sql)
    conn.commit()



def normalizar_valor(valor):
    """
    Convierte valores vacíos/NaN en None
    y limpia espacios de textos.
    """

    if valor is None:
        return None

    if pd.isna(valor):
        return None

    if isinstance(valor, str):
        valor = valor.strip()

        if not valor:
            return None

    return valor


def _estado_consolidado_desde_datos(datos, duplicado=False):
    """Calcula el estado de Consolidado con las reglas del README."""

    if duplicado:
        return "BLOQUEADO"

    descripcion = normalizar_valor(datos.get("descripcionEstandarizada"))
    referencia = normalizar_valor(datos.get("referenciaProveedor"))
    abrev = normalizar_valor(datos.get("abrevProveedor"))

    referencia_valida = (
        referencia is not None
        and str(referencia).strip().upper()
        not in ("", "SIN_REFERENCIA", "SIN REFERENCIA", "NONE")
    )

    if (
        descripcion
        and len(str(descripcion).strip()) >= 3
        and referencia_valida
        and abrev
    ):
        return "LISTO_INVENTARIAR"
    return "PENDIENTE_REVISION"


def actualizar_consolidado(conn, df):
    """Inserta o actualiza Consolidado desde un DataFrame estandarizado.

    Reglas:
    * La llave es ``abrevProveedor + referenciaProveedor`` normalizada.
    * Si hay más de una fila existente para la misma llave no se elige una
      arbitrariamente: el registro se omite y se reporta como ambiguo.
    * Un valor manual no vacío tiene prioridad. Los valores vacíos del
      DataFrame nunca sobrescriben datos válidos ya guardados.
    """

    campos = [
        "idProveedor",
        "referenciaRepuesto",
        "equivalente",
        "descripcion",
        "marca",
        "carro",
        "tipo",
        "tipoMotor",
        "precioListado",
        "proveedor",
        "lado",
        "ladoAbreviado",
        "repuesto",
        "modelo",
        "otros",
        "medida",
        "descripcionEstandarizada",
        "grupo",
    ]

    columnas_llave = ["abrevProveedor", "referenciaProveedor"]

    faltantes_llave = [
        columna
        for columna in columnas_llave
        if columna not in df.columns
    ]

    if faltantes_llave:
        raise ValueError(
            "Faltan las siguientes columnas en el DataFrame: "
            + ", ".join(faltantes_llave)
        )

    # Los campos operativos opcionales que no vengan en el archivo se tratan
    # como vacíos: nunca deben romper la importación ni sobrescribir datos.
    faltantes = [
        columna
        for columna in campos
        if columna not in df.columns
    ]
    if faltantes:
        df = df.copy()
        for columna in faltantes:
            df[columna] = None

    cursor = conn.cursor()

    columnas_bd = (
        ["id"]
        + ["referenciaProveedor", "abrevProveedor"]
        + campos
        + ["estadoConsolidado"]
    )

    cursor.execute(
        """
        SELECT
            id,
            referenciaProveedor,
            abrevProveedor,
            idProveedor,
            referenciaRepuesto,
            equivalente,
            descripcion,
            marca,
            carro,
            tipo,
            tipoMotor,
            precioListado,
            proveedor,
            lado,
            ladoAbreviado,
            repuesto,
            modelo,
            otros,
            medida,
            descripcionEstandarizada,
            grupo,
            estadoConsolidado
        FROM consolidado
        """
    )

    registros_bd = cursor.fetchall()

    bd = {}
    ids_por_clave = {}
    conteo_por_clave = {}

    for registro in registros_bd:
        datos_bd = dict(zip(columnas_bd, registro))

        referencia = normalizar_valor(datos_bd["referenciaProveedor"])
        abrev_proveedor = normalizar_valor(datos_bd["abrevProveedor"])

        if referencia is None or abrev_proveedor is None:
            continue

        clave = (
            str(abrev_proveedor).strip().upper(),
            str(referencia).strip().upper(),
        )

        conteo_por_clave[clave] = conteo_por_clave.get(clave, 0) + 1
        if clave not in bd:
            bd[clave] = datos_bd
            ids_por_clave[clave] = datos_bd["id"]

    nuevos = 0
    modificados = 0
    sin_cambios = 0
    ambiguos = 0

    for _, fila in df.iterrows():

        referencia = normalizar_valor(fila["referenciaProveedor"])
        abrev_proveedor = normalizar_valor(fila["abrevProveedor"])

        if referencia is None or abrev_proveedor is None:
            continue

        referencia = str(referencia).strip()
        abrev_proveedor = str(abrev_proveedor).strip()

        clave = (
            abrev_proveedor.upper(),
            referencia.upper(),
        )

        datos = {}
        for campo in campos:
            datos[campo] = normalizar_valor(fila[campo])
        datos["abrevProveedor"] = abrev_proveedor
        datos["referenciaProveedor"] = referencia

        if clave not in bd:
            estado_nuevo = _estado_consolidado_desde_datos(datos, duplicado=False)

            cursor.execute(
                """
                INSERT INTO consolidado (
                    referenciaProveedor,
                    abrevProveedor,
                    idProveedor,
                    referenciaRepuesto,
                    equivalente,
                    descripcion,
                    marca,
                    carro,
                    tipo,
                    tipoMotor,
                    precioListado,
                    proveedor,
                    lado,
                    ladoAbreviado,
                    repuesto,
                    modelo,
                    otros,
                    medida,
                    descripcionEstandarizada,
                    grupo,
                    estadoConsolidado,
                    fechaCreacion,
                    fechaActualizacion
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """,
                (
                    referencia,
                    abrev_proveedor,
                    datos["idProveedor"],
                    datos["referenciaRepuesto"],
                    datos["equivalente"],
                    datos["descripcion"],
                    datos["marca"],
                    datos["carro"],
                    datos["tipo"],
                    datos["tipoMotor"],
                    datos["precioListado"],
                    datos["proveedor"],
                    datos["lado"],
                    datos["ladoAbreviado"],
                    datos["repuesto"],
                    datos["modelo"],
                    datos["otros"],
                    datos["medida"],
                    datos["descripcionEstandarizada"],
                    datos["grupo"],
                    estado_nuevo,
                ),
            )

            nuevo_id = cursor.lastrowid
            ids_por_clave[clave] = nuevo_id
            conteo_por_clave[clave] = 1
            bd[clave] = {"id": nuevo_id, **datos}
            nuevos += 1
            continue

        # Coincidencia múltiple: no actualizar ni elegir la primera.
        if conteo_por_clave.get(clave, 1) > 1:
            ambiguos += 1
            try:
                cursor.execute(
                    """
                    UPDATE consolidado
                    SET estadoConsolidado = 'BLOQUEADO'
                    WHERE UPPER(TRIM(abrevProveedor)) = ?
                      AND UPPER(TRIM(referenciaProveedor)) = ?
                      AND COALESCE(estadoConsolidado, '') <> 'INVENTARIADO'
                    """,
                    clave,
                )
            except Exception:
                pass
            continue

        registro_bd = bd[clave]
        cambios = {}

        for campo in campos:
            valor_nuevo = datos.get(campo)
            if valor_nuevo is None:
                continue
            if isinstance(valor_nuevo, str) and not valor_nuevo.strip():
                continue

            valor_bd = normalizar_valor(registro_bd.get(campo))

            comparar_nuevo = (
                valor_nuevo.strip() if isinstance(valor_nuevo, str) else valor_nuevo
            )
            comparar_bd = (
                valor_bd.strip() if isinstance(valor_bd, str) else valor_bd
            )

            # Un 0 o vacío nunca reemplaza un precio existente.
            if campo == "precioListado" and not comparar_nuevo:
                continue

            if comparar_nuevo != comparar_bd:
                cambios[campo] = valor_nuevo

        estado_actual = registro_bd.get("estadoConsolidado")
        datos_estado = dict(registro_bd)
        datos_estado.update(cambios)

        if estado_actual == "INVENTARIADO":
            estado_nuevo = "INVENTARIADO"
        else:
            estado_nuevo = _estado_consolidado_desde_datos(
                datos_estado, duplicado=False
            )

        if cambios:
            asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)
            cursor.execute(
                f"""
                UPDATE consolidado
                SET {asignaciones},
                    fechaActualizacion = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                tuple(cambios.values()) + (registro_bd["id"],),
            )

        if estado_nuevo != estado_actual:
            cursor.execute(
                "UPDATE consolidado SET estadoConsolidado = ? WHERE id = ?",
                (estado_nuevo, registro_bd["id"]),
            )

        if cambios or estado_nuevo != estado_actual:
            modificados += 1
        else:
            sin_cambios += 1

        registro_bd.update(cambios)
        registro_bd["estadoConsolidado"] = estado_nuevo
        ids_por_clave[clave] = registro_bd["id"]

    conn.commit()

    respaldo.actualizar_respaldo_si_posible(conn=conn)

    return {
        "nuevos": nuevos,
        "modificados": modificados,
        "sin_cambios": sin_cambios,
        "ambiguos": ambiguos,
        "total": nuevos + modificados + sin_cambios + ambiguos,
        "ids_por_clave": ids_por_clave,
    }


@app.route('/bin/codigos/<path:filename>')
def servir_codigos(filename):
    """Sirve archivos PDF desde /bin/codigos"""
    return send_from_directory(RUTA_CODIGOS, filename)


@app.route('/')
def index():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))
    
    return render_template('index.html', usuario=session['usuario'])

@app.route('/ejecutar', methods=['POST'])
def ejecutar():
    resultado = "¡Programa ejecutado con éxito!"
    return render_template('index.html', usuario=session['usuario'], mensaje=resultado)

@app.route('/configuracion', methods=['GET', 'POST'])
def configuracion():
    mensaje = None
    nombre_db = ''
    email_revision = ''
    token_contraseña = ''
    token_configurado = False
    correos_varios = ''
    sql_result = None
    sql_error = None
    servidor_config = configuracion_servidor.cargar_configuracion()
    ip_local = configuracion_servidor.detectar_ip_local()
    url_clientes = configuracion_servidor.url_clientes(servidor_config, ip_local)
    servidor_estado = configuracion_servidor.estado_servidor()

    bases_disponibles = db.listar_bases()
    ruta_db = db.ruta_bd_activa()
    nombre_bd_activa = db.obtener_bd_activa()

    # Leer la última configuración si existe
    token_guardado = ''
    if ruta_db:
        try:
            conn = db.conectar(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nombre_db, email_revision, token_contraseña, correos_varios 
                FROM configuracion ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                nombre_db, email_revision, token_guardado, correos_varios = row
                token_configurado = bool(token_guardado)
            conn.close()
        except Exception as e:
            mensaje = f"Error al leer la configuración: {e}"

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'crear_bd':

            nombre_db = request.form.get('nombre_db')

            if nombre_db:

                ruta_db = db.ruta_bd(f"{nombre_db}.sqlite")

                try:

                    conn = db.conectar(ruta_db)

                    ejecutar_sql_archivo(
                        conn,
                        "estructura.sql"
                    )

                    migraciones.migrar(conn)

                    conn.close()

                    db.establecer_bd_activa(f"{nombre_db}.sqlite")
                    nombre_bd_activa = f"{nombre_db}.sqlite"
                    bases_disponibles = db.listar_bases()

                    mensaje = (
                        f"Base de datos '{nombre_db}' "
                        "creada y activada correctamente."
                    )

                except Exception as e:

                    mensaje = (
                        f"Error al crear la base de datos: {e}"
                    )

        elif accion == 'activar_bd':

            nombre_objetivo = request.form.get('bd_seleccionada')

            if nombre_objetivo and nombre_objetivo in db.listar_bases():
                nombre_bd_activa = db.establecer_bd_activa(nombre_objetivo)
                ruta_db = db.ruta_bd_activa()
                mensaje = f"Base de datos activa: {nombre_objetivo}"
            else:
                mensaje = "Seleccione una base de datos válida."

        elif accion == 'guardar_servidor':
            servidor_config, errores = configuracion_servidor.guardar_configuracion(
                {
                    'nombre_servidor': request.form.get('nombre_servidor'),
                    'host': request.form.get('host'),
                    'puerto': request.form.get('puerto'),
                    'hilos': request.form.get('hilos'),
                }
            )
            if errores:
                mensaje = " ".join(errores)
                flash(mensaje, "danger")
            else:
                mensaje = (
                    "Configuración del servidor guardada. "
                    "Los cambios de host o puerto se aplican al reiniciar "
                    "el servicio."
                )
                flash(mensaje, "success")
            ip_local = configuracion_servidor.detectar_ip_local()
            url_clientes = configuracion_servidor.url_clientes(
                servidor_config, ip_local
            )
            servidor_estado = configuracion_servidor.estado_servidor()

        elif accion == 'probar_acceso_local':
            servidor_config = configuracion_servidor.cargar_configuracion()
            prueba = configuracion_servidor.probar_acceso_local(servidor_config)
            mensaje = prueba['mensaje']
            flash(mensaje, "success" if prueba['ok'] else "warning")
            servidor_estado = configuracion_servidor.estado_servidor()

        elif accion == 'guardar_config':
            email_revision = request.form.get('email_revision')
            token_form = (request.form.get('token_contraseña') or '').strip()
            correos_varios = request.form.get('correos_varios')
            if token_form:
                token_guardado = seguridad.cifrar_token(token_form)
            token_contraseña = ''
            if ruta_db:
                try:
                    conn = db.conectar(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO configuracion (nombre_db, email_revision, token_contraseña, correos_varios)
                        VALUES (?, ?, ?, ?)
                    """, (
                        os.path.splitext(os.path.basename(ruta_db))[0],
                        email_revision,
                        token_guardado,
                        correos_varios
                    ))
                    conn.commit()
                    conn.close()
                    token_configurado = bool(token_guardado)
                    mensaje = "Configuración guardada correctamente."
                except Exception as e:
                    mensaje = f"Error al guardar la configuración: {e}"
            else:
                mensaje = "Primero debe crear una base de datos."

        elif accion == 'ejecutar_sql':
            sql_query = request.form.get('sql_query')
            if ruta_db and sql_query:
                # 1. Separar por punto y coma y quitar espacios vacíos
                sql_list = [q.strip() for q in sql_query.split(";") if q.strip()]
                sql_results = [] # Lista para guardar todos los resultados

                try:
                    conn = db.conectar(ruta_db)
                    cursor = conn.cursor()

                    for query in sql_list:
                        cursor.execute(query)
                        
                        # 2. Manejar resultados de cada consulta individual
                        if query.lower().startswith("select"):
                            sql_results.append(cursor.fetchall())
                        else:
                            conn.commit() # Commit solo si hay cambios (INSERT/UPDATE/DELETE)
                            sql_results.append(f"Ejecutada: {query[:20]}...")

                    sql_result = sql_results # Devolver la lista completa
                except Exception as e:
                    sql_error = f"Error en SQL: {str(e)}"
                finally:
                    conn.close()
            else:
                sql_error = "Falta la base de datos o la consulta."
        
        elif accion == 'guardar_template':
            nombre = request.form.get('nombre_template')
            ubicacion = request.form.get('archivo_template')
            numColumnas = request.form.get('numeroColumnas')

            if ruta_db and nombre and ubicacion:
                try:
                    conn = db.conectar(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""          
                        INSERT INTO bartenderFiles (name, ubicacion, numeroColumnas, activo)
                        VALUES (?, ?, ?, ?);
                    """, (nombre, ubicacion, numColumnas, True))
                    conn.commit()
                    conn.close()
                    mensaje = "Template de BarTender registrado correctamente."
                except Exception as e:
                    mensaje = f"Error al registrar template: {e}"
        
        elif accion == 'guardar_ubicacion':
            
            piso = request.form.get('piso')
            pasillo = request.form.get('pasillo')
            tipo = request.form.get('tipo')
            numero = request.form.get('numero')

            if ruta_db and piso and tipo and numero:
                try:
                    conn = db.conectar(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""          
                        INSERT INTO Ubicacion (piso, pasillo, tipo, numero)
                        VALUES (?, ?, ?, ?);
                    """, (piso, pasillo, tipo, numero))
                    conn.commit()
                    conn.close()
                    mensaje = "Ubicación registrada correctamente."
                except Exception as e:
                    mensaje = f"Error al registrar ubicación: {e}"

        elif accion == 'guardar_proveedor':
            nombre_proveedor = request.form.get('nombre_proveedor')
            nombre_proveedor_2 = request.form.get('nombre_proveedor_2')
            sigla = request.form.get('sigla')
            correo_facturas = request.form.get('correo_facturas')
            prefijo = request.form.get('prefijo')
            nit = request.form.get('nit')
            ciudad = request.form.get('ciudad')
            telefono = request.form.get('telefono')

            if ruta_db and nombre_proveedor and sigla and correo_facturas and prefijo and nit:
                try:
                    conn = db.conectar(ruta_db)
                    cursor = conn.cursor()
                    cursor.execute("""          
                        INSERT INTO proveedor (nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """, (nombre_proveedor, nombre_proveedor_2, sigla, correo_facturas, prefijo, nit, ciudad, telefono))
                    conn.commit()
                    conn.close()
                    mensaje = "Proveedor registrado correctamente."
                except Exception as e:
                    mensaje = f"Error al registrar proveedor: {e}"

        elif accion == "actualizar_consolidado":

            #try:

            from procesamiento.consolidado import (
                procesar_consolidado
            )

            ruta_consolidado = os.path.join(
                os.path.dirname(__file__),
                "bin/xlsx",
                "CONSOLIDADO.xlsx"
            )

            ruta_estandarizacion = os.path.join(
                os.path.dirname(__file__),
                "bin/xlsx",
                "estandarizacion.xlsx"
            )


            # 1. Procesar Excel
            df = procesar_consolidado(
                ruta_consolidado,
                ruta_estandarizacion
            )

            print(df.head(10).to_string())
            print("========== PRUEBA DF ==========")

            conn = db.conectar(ruta_db)

            # 2. Actualizar SQLite
            resultado = actualizar_consolidado(
                conn,
                df
            )

            mensaje = (
                "Consolidado actualizado. "
                f"Nuevos: {resultado['nuevos']} | "
                f"Modificados: {resultado['modificados']} | "
                f"Sin cambios: {resultado['sin_cambios']}"
            )

            # except Exception as e:

            #     mensaje = (
            #         f"Error actualizando consolidado: {e}"
            #     )

    bartender_templates = []

    if ruta_db:
        try:
            conn = db.conectar(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT *
                FROM bartenderFiles
                ORDER BY name
            """)
            bartender_templates = cursor.fetchall()
            conn.close()
        except:
            bartender_templates = []
    
    ubicaciones = []

    if ruta_db:
        try:
            conn = db.conectar(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT *
                FROM Ubicacion
                ORDER BY descripcion
            """)
            ubicaciones = cursor.fetchall()
            conn.close()
        except:
            ubicaciones = []
    
    proveedores = []

    if ruta_db:
        try:
            conn = db.conectar(ruta_db)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nombreProveedor, nombreProveedorSecundario, siglas, correoFacturas, prefijo, nit, ciudad, telefono FROM proveedor
            """)
            proveedores = cursor.fetchall()
            conn.close()
        except:
            proveedores = []

    return render_template(
        'configuracion.html',
        mensaje=mensaje,
        nombre_db=nombre_db,
        email_revision=email_revision,
        token_contraseña=token_contraseña,
        token_configurado=token_configurado,
        correos_varios=correos_varios,
        sql_result=sql_result,
        sql_error=sql_error,
        bartender_templates=bartender_templates,
        ubicaciones=ubicaciones,
        proveedores=proveedores,
        bases_disponibles=bases_disponibles,
        nombre_bd_activa=nombre_bd_activa,
        servidor_config=servidor_config,
        servidor_estado=servidor_estado,
        ip_local=ip_local,
        url_clientes=url_clientes,
    )

@app.route("/configuracion/procesar-pendientes", methods=["GET", "POST"])
@app.route(
    "/configuracion/procesar-pendientes",
    methods=["GET", "POST"]
)
@app.route("/facturas/procesar-pendientes", methods=["GET", "POST"])
def procesar_pendientes():

    ruta_estandarizacion = os.path.join(
        os.path.dirname(__file__),
        "bin/xlsx",
        "estandarizacion.xlsx"
    )

    ruta_db = db.ruta_bd_activa()

    if not ruta_db:

        flash(
            "No existe una base de datos.",
            "danger"
        )

        return redirect(
            url_for("configuracion")
        )

    # ==========================================
    # GET
    # ==========================================

    if request.method == "GET":

        return render_template(
            "procesar_pendientes.html"
        )

    # ==========================================
    # POST
    # ==========================================

    accion = request.form.get(
        "accion",
        "buscar_factura"
    )

    id_factura = request.form.get(
        "id_factura"
    )

    if not id_factura:

        flash(
            "Debe ingresar un ID de factura.",
            "warning"
        )

        return redirect(
            url_for("procesar_pendientes")
        )

    try:

        with db.conexion(ruta_db) as conn:

            cursor = conn.cursor()

            # ======================================
            # BUSCAR INFORMACIÓN DE FACTURA
            # ======================================

            cursor.execute("""
                SELECT
                    fac.id_factura,
                    prov.idProveedor,
                    fac.proveedor,
                    fac.fecha,
                    fac.valor_total,
                    prov.siglas
                FROM facturas fac

                LEFT JOIN proveedor prov
                    ON fac.proveedor = prov.nombreProveedor
                    OR fac.proveedor = prov.nombreProveedorSecundario

                WHERE fac.id_factura = ?
            """, (
                id_factura,
            ))

            factura = cursor.fetchone()

            if not factura:

                flash(
                    f"No se encontró la factura {id_factura}.",
                    "danger"
                )

                return redirect(
                    url_for("procesar_pendientes")
                )

            # ======================================
            # ACCIÓN 1:
            # BUSCAR FACTURA
            # ======================================

            if accion == "buscar_factura":

                cursor.execute("""
                    SELECT
                        id,
                        id_factura,
                        referencia,
                        descripcion_item,
                        valor_unitario
                    FROM inventarioFacturas
                    WHERE id_factura = ?
                """, (
                    id_factura,
                ))

                filas = cursor.fetchall()

                articulos = []

                for fila in filas:

                    articulos.append({

                        "idInventarioFactura":
                            fila[0],

                        "id_factura":
                            fila[1],

                        "referenciaProveedor":
                            fila[2] or "",

                        "referenciaRepuesto":
                            "",

                        "equivalente":
                            "",

                        "descripcion":
                            fila[3] or "",

                        "marcaRepuesto":
                            "",

                        "carro":
                            "",

                        "precioListado":
                            fila[4],

                        "idProveedor":
                            factura[1],

                        "proveedor":
                            factura[2],

                        "sigla":
                            factura[5]
                    })

                return render_template(
                    "procesar_pendientes.html",
                    factura=factura,
                    articulos=articulos
                )

            # ======================================
            # ACCIÓN 2:
            # INGRESAR A CONSOLIDADO
            # ======================================

            elif accion == "guardar_consolidado":

                referencias_proveedor = request.form.getlist(
                    "referenciaProveedor[]"
                )

                referencias_repuesto = request.form.getlist(
                    "referenciaRepuesto[]"
                )

                equivalentes = request.form.getlist(
                    "equivalente[]"
                )

                descripciones = request.form.getlist(
                    "descripcion[]"
                )

                marcas = request.form.getlist(
                    "marcaRepuesto[]"
                )

                carros = request.form.getlist(
                    "carro[]"
                )

                precioListado = request.form.getlist(
                    "precioListado[]"
                )

                # ==================================
                # CREAR DATAFRAME
                # ==================================

                articulos_df = []

                total = len(
                    referencias_proveedor
                )

                for i in range(total):

                    articulos_df.append({

                        "referenciaProveedor":
                            referencias_proveedor[i].strip(),

                        "referenciaRepuesto":
                            referencias_repuesto[i].strip(),

                        "equivalente":
                            equivalentes[i].strip(),

                        "descripcion":
                            descripciones[i].strip(),

                        "marca":
                            marcas[i].strip(),

                        "carro":
                            carros[i].strip(),

                        "tipo":
                            "",

                        "tipoMotor":
                            "",

                        "precioListado":
                            precioListado[i].strip(),

                        "proveedor":
                            factura[2],

                        "idProveedor":
                            factura[1],

                        "abrevProveedor":
                            factura[5]

                    })

                df = pd.DataFrame(
                    articulos_df
                )

                # ==================================
                # EJECUTAR ESTANDARIZACIÓN
                # ==================================

                from procesamiento.consolidado import (
                    procesar_dataframe_pendientes
                )

                df = procesar_dataframe_pendientes(
                    df, ruta_estandarizacion
                )

                # ==================================
                # INGRESAR / ACTUALIZAR CONSOLIDADO
                # ==================================

                resultado = actualizar_consolidado(
                    conn,
                    df
                )

                flash(
                    (
                        "Artículos procesados correctamente. "
                        f"Nuevos: {resultado['nuevos']} | "
                        f"Modificados: {resultado['modificados']} | "
                        f"Sin cambios: {resultado['sin_cambios']}"
                    ),
                    "success"
                )

                return redirect(
                    url_for("procesar_pendientes")
                )

            else:

                flash(
                    "Acción no válida.",
                    "danger"
                )

                return redirect(
                    url_for("procesar_pendientes")
                )

    except Exception as e:

        print(
            "ERROR PROCESANDO PENDIENTES:",
            e
        )

        flash(
            f"Error procesando artículos: {e}",
            "danger"
        )

        return redirect(
            url_for("procesar_pendientes")
        )

def inicializar_bd():
    ruta = db.ruta_bd_activa()
    if not ruta:
        return
    conn = db.conectar(ruta)
    try:
        migraciones.migrar(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    # Operación diaria con Waitress (nunca el servidor de desarrollo Flask).
    # ``python app.py`` delega en run_server.py.
    import run_server

    raise SystemExit(run_server.main(app))

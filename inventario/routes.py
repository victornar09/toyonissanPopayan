from flask import (
    Blueprint,
    render_template,
    session,
    redirect,
    url_for,
    request,
    Response,
    jsonify,
)
import os
import csv
from datetime import date
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
import barcode
from barcode.writer import ImageWriter
from io import BytesIO, StringIO
from PIL import Image
from reportlab.platypus import Paragraph, Frame
from reportlab.lib.styles import getSampleStyleSheet
import win32com.client

import db
from utilidades import cifrar_precio, a_entero, normalizar_clave
from consolidado.servicio_inventario import obtener_o_crear_articulo
from inventario import respaldo, servicio_conteo


def imprimir_varios(productos, ruta_template, columna_inicio=1):
    # CSV temporal (BarTender lo lee)
    ruta_csv = os.path.abspath("productos_temp.csv")

    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo", "descripcion", "precio", "copias", "inicio_columna"])
        for codigo, descripcion, precio, cantidad in productos:
            writer.writerow([
                codigo,
                descripcion,
                precio,
                cantidad,
                columna_inicio
            ])

    bt = win32com.client.Dispatch("BarTender.Application")
    bt.Visible = True

    # Abrir template dinámico
    formato = bt.Formats.Open(
        os.path.abspath(ruta_template),
        False,
        ""
    )

    formato.PrintOut(False, False)


def _actualizar_inventario_desde_facturas(ruta_db):
    """Consolida los ítems de facturas en inventarioUnico.

    REGLA: Solo permite pasar líneas con coincidencia válida en consolidado.
    Usa descripción estandarizada de consolidado y genera descripcionClave desde ella.
    """

    from facturas.validacion_consolidado import obtener_estado_linea

    with db.conexion(ruta_db) as conn:

        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                a.id,
                a.descripcion_item,
                a.cantidad,
                a.valor_unitario,
                a.referencia,
                b.proveedor,
                b.fecha,
                a.inventariado,
                a.id_inventarioUnico
            FROM inventarioFacturas a
            LEFT JOIN facturas b ON a.id_factura = b.id_factura
            ORDER BY b.fecha DESC
        """)

        for fila in cursor.fetchall():
            (
                id_fila, descripcion_item, cantidad,
                valor_unitario, referencia, proveedor, fecha,
                inventariado, id_inventario_unico
            ) = fila

            if inventariado == 1 or inventariado is True:
                continue
            if id_inventario_unico:
                cur2 = conn.cursor()
                cur2.execute("SELECT id FROM inventarioUnico WHERE id = ?", (id_inventario_unico,))
                if cur2.fetchone():
                    continue

            estado = obtener_estado_linea(conn, (proveedor, fecha, None, None, None), fila)
            if not estado.get('permitido'):
                continue

            datos_est = estado.get('datos_estandarizados') or {}
            descripcion_est = datos_est.get('descripcion') or datos_est.get('descripcionEstandarizada') or descripcion_item
            clave = normalizar_clave(descripcion_est)
            if not clave:
                continue

            precio_venta = int(round(a_entero(valor_unitario, 0), -3)) if a_entero(valor_unitario, 0) else 0
            precio_max_desc = round(precio_venta * 0.9, 0)
            precio_cifrado = cifrar_precio(precio_venta)
            prov_use = datos_est.get('proveedor') or proveedor or ''

            nuevo_id = obtener_o_crear_articulo(
                conn,
                descripcion=descripcion_est,
                descripcion_clave=clave,
                referencia=referencia,
                proveedor=prov_use,
                precio_venta=precio_venta,
                precio_cifrado=precio_cifrado,
                precio_max_descuento=precio_max_desc,
                grupo=None,
                fecha=fecha or str(date.today()),
                cantidad=a_entero(cantidad, 0),
                sumar_cantidad=True,
            )['id']
            if not nuevo_id:
                continue

            cursor.execute("""
                UPDATE inventarioFacturas
                SET inventariado = 1,
                    id_inventarioUnico = ?,
                    id_consolidado = COALESCE(?, id_consolidado)
                WHERE id = ?
            """, (nuevo_id, datos_est.get('id_consolidado'), id_fila))

    # El respaldo se actualiza después del commit, fuera de la transacción.
    respaldo.actualizar_respaldo_si_posible(ruta_db=ruta_db)


inventario_bp = Blueprint('inventario', __name__, url_prefix='/inventario')


CONSULTA_INVENTARIO = """
    SELECT
        iu.id,
        iu.codigoBarras,
        COALESCE(c.referenciaProveedor, iu.referencia) AS referenciaProveedor,
        c.referenciaRepuesto AS referenciaRepuesto,
        COALESCE(c.descripcionEstandarizada, iu.descripcion) AS descripcion,
        iu.referencia AS referenciaInventario,
        c.equivalente,
        c.marca AS marcaArticulo,
        c.carro AS marcaVehiculo,
        c.modelo,
        c.lado,
        c.medida,
        iu.proveedor,
        COALESCE(c.grupo, iu.grupo) AS grupo,
        iu.cantidad,
        iu.costo,
        iu.precioVenta,
        iu.precioVentaCifrado,
        iu.precioMaxDescuento,
        iu.idUbicacion,
        iu.ubicacion,
        iu.estante,
        iu.estadoEtiquetado,
        iu.estadoConteo,
        iu.fechaUltimoConteo,
        iu.cantidadFisica,
        iu.stockMinimo,
        iu.fechaCreacion,
        iu.fechaActualizacion,
        c.id AS idConsolidado
    FROM inventarioUnico iu
    LEFT JOIN consolidado c
        ON c.id = (
            SELECT MIN(c2.id)
            FROM consolidado c2
            WHERE c2.idInventarioUnico = iu.id
        )
"""

CAMPOS_FILTRO = [
    "codigo",
    "descripcion",
    "referencia_proveedor",
    "referencia_repuesto",
    "equivalente",
    "marca_articulo",
    "marca_vehiculo",
    "modelo",
    "lado",
    "proveedor",
    "grupo",
    "ubicacion",
    "estante",
    "disponibilidad",
    "stock_bajo",
    "estado_conteo",
    "estado_etiquetado",
]


def _filtros_desde_request():
    return {
        campo: (request.args.get(campo, "") or "").strip()
        for campo in CAMPOS_FILTRO
    }


def _where_inventario(filtros):
    condiciones = []
    parametros = []

    mapa = {
        "codigo": "UPPER(TRIM(COALESCE(iu.codigoBarras, '')))",
        "descripcion": (
            "UPPER(TRIM(COALESCE(c.descripcionEstandarizada, iu.descripcion, '')))"
        ),
        "referencia_proveedor": (
            "UPPER(TRIM(COALESCE(c.referenciaProveedor, iu.referencia, '')))"
        ),
        "referencia_repuesto": (
            "UPPER(TRIM(COALESCE(c.referenciaRepuesto, '')))"
        ),
        "equivalente": "UPPER(TRIM(COALESCE(c.equivalente, '')))",
        "marca_articulo": "UPPER(TRIM(COALESCE(c.marca, '')))",
        "marca_vehiculo": "UPPER(TRIM(COALESCE(c.carro, '')))",
        "modelo": "UPPER(TRIM(COALESCE(c.modelo, '')))",
        "lado": "UPPER(TRIM(COALESCE(c.lado, '')))",
        "proveedor": "UPPER(TRIM(COALESCE(iu.proveedor, '')))",
        "grupo": "UPPER(TRIM(COALESCE(c.grupo, iu.grupo, '')))",
        "ubicacion": "UPPER(TRIM(COALESCE(iu.ubicacion, '')))",
        "estante": "UPPER(TRIM(COALESCE(iu.estante, '')))",
    }

    for campo, expresion in mapa.items():
        valor = filtros.get(campo)
        if valor:
            condiciones.append(f"{expresion} LIKE ?")
            parametros.append(f"%{valor.upper()}%")

    disponibilidad = filtros.get("disponibilidad")
    if disponibilidad == "con":
        condiciones.append("COALESCE(iu.cantidad, 0) > 0")
    elif disponibilidad == "sin":
        condiciones.append("COALESCE(iu.cantidad, 0) <= 0")

    if filtros.get("stock_bajo") in ("1", "si", "true"):
        condiciones.append(
            "COALESCE(iu.stockMinimo, 0) > 0 "
            "AND COALESCE(iu.cantidad, 0) <= iu.stockMinimo"
        )

    if filtros.get("estado_conteo"):
        condiciones.append("iu.estadoConteo = ?")
        parametros.append(filtros["estado_conteo"])

    if filtros.get("estado_etiquetado"):
        condiciones.append("iu.estadoEtiquetado = ?")
        parametros.append(filtros["estado_etiquetado"])

    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    return where, parametros


def _listar_inventario(ruta_db, filtros):
    where, parametros = _where_inventario(filtros)
    with db.conexion(ruta_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"{CONSULTA_INVENTARIO} {where} ORDER BY iu.descripcion",
            tuple(parametros),
        )
        columnas = [col[0] for col in cursor.description]
        return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]


def _contexto_inventario(ruta_db):
    with db.conexion(ruta_db) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT idBartenderFiles, name
            FROM bartenderFiles
            WHERE activo = 1
            ORDER BY name
        """)
        bartender_templates = cursor.fetchall()

        cursor.execute("""
            SELECT DISTINCT grupo
            FROM inventarioUnico
            WHERE grupo IS NOT NULL AND TRIM(grupo) <> ''
            ORDER BY grupo
        """)
        grupos = [fila[0] for fila in cursor.fetchall()]

        cursor.execute("""
            SELECT DISTINCT ubicacion
            FROM inventarioUnico
            WHERE ubicacion IS NOT NULL AND TRIM(ubicacion) <> ''
            ORDER BY ubicacion
        """)
        ubicaciones_inventario = [fila[0] for fila in cursor.fetchall()]

    return {
        "bartender_templates": bartender_templates,
        "grupos": grupos,
        "ubicaciones_inventario": ubicaciones_inventario,
    }


@inventario_bp.route('/', methods=['GET', 'POST'])
def inventario_home():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    ruta_db = db.ruta_bd_activa()

    if ruta_db and request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'descargar_inventario':
            return _exportar_inventario_csv(ruta_db)
        if accion == 'actualizar_inventario':
            _actualizar_inventario_desde_facturas(ruta_db)
            return redirect(url_for('inventario.inventario_home'))

    filtros = _filtros_desde_request()
    items = []
    contexto = {
        "bartender_templates": [],
        "grupos": [],
        "ubicaciones_inventario": [],
    }
    ubicaciones = []
    sesiones = []

    if ruta_db:
        items = _listar_inventario(ruta_db, filtros)
        contexto = _contexto_inventario(ruta_db)
        ubicaciones = servicio_conteo.listar_ubicaciones(ruta_db)
        sesiones = servicio_conteo.listar_sesiones(ruta_db, limite=8)

    return render_template(
        'inventario/inventario.html',
        usuario=session.get('usuario'),
        items=items,
        filtros=filtros,
        ubicaciones=ubicaciones,
        sesiones=sesiones,
        **contexto,
    )


def _exportar_inventario_csv(ruta_db):
    with db.conexion(ruta_db) as conn:
        cursor = conn.cursor()
        cursor.execute(f"{CONSULTA_INVENTARIO} ORDER BY iu.descripcion")
        columnas = [col[0] for col in cursor.description]
        filas = cursor.fetchall()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columnas)
    for fila in filas:
        writer.writerow(fila)

    return Response(
        buffer.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=inventario.csv"
        },
    )


@inventario_bp.route('/exportar_catalogo', methods=['GET'])
def exportar_catalogo():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    contenido = respaldo.exportar_catalogo_csv(ruta_db)
    return Response(
        contenido,
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                "attachment; filename=catalogo_codigos_barras.csv"
            )
        },
    )


@inventario_bp.route('/respaldo/verificar', methods=['GET', 'POST'])
def verificar_respaldo():
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    return jsonify(respaldo.verificar_respaldo(ruta_db))


@inventario_bp.route('/respaldo/generar', methods=['POST'])
def generar_respaldo():
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    return jsonify(respaldo.generar_respaldo(ruta_db))


@inventario_bp.route('/conteo/iniciar', methods=['POST'])
def iniciar_conteo():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    datos = request.form if not request.is_json else (request.get_json() or {})
    id_ubicacion = datos.get('idUbicacion') or datos.get('id_ubicacion')

    resultado = servicio_conteo.iniciar_sesion(
        ruta_db,
        nombre=datos.get('nombre'),
        responsable=datos.get('responsable'),
        id_ubicacion=id_ubicacion or None,
        estante=datos.get('estante'),
        observaciones=datos.get('observaciones'),
        usuario=session.get('usuario'),
    )

    if request.is_json:
        return jsonify(resultado), (200 if resultado.get('ok') else 400)

    if not resultado.get('ok'):
        return render_template(
            'inventario/conteo_error.html',
            mensaje=resultado.get('error'),
            usuario=session.get('usuario'),
        ), 400

    return redirect(
        url_for('inventario.conteo_sesion', id_sesion=resultado['idSesion'])
    )


@inventario_bp.route('/conteo/<int:id_sesion>', methods=['GET'])
def conteo_sesion(id_sesion):
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return redirect(url_for('inventario.inventario_home'))

    resultado = servicio_conteo.obtener_sesion(ruta_db, id_sesion)
    if not resultado.get('ok'):
        return render_template(
            'inventario/conteo_error.html',
            mensaje=resultado.get('error'),
            usuario=session.get('usuario'),
        ), 404

    return render_template(
        'inventario/conteo.html',
        usuario=session.get('usuario'),
        id_sesion=id_sesion,
        **resultado,
    )


@inventario_bp.route('/conteo/<int:id_sesion>/estado', methods=['GET'])
def conteo_estado(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    resultado = servicio_conteo.obtener_sesion(ruta_db, id_sesion)
    return jsonify(resultado), (200 if resultado.get('ok') else 404)


@inventario_bp.route('/conteo/<int:id_sesion>/leer', methods=['POST'])
def conteo_leer(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    datos = request.get_json(silent=True) or request.form.to_dict()
    resultado = servicio_conteo.leer_codigo(
        ruta_db,
        id_sesion,
        datos.get('codigo'),
        token=datos.get('token'),
        usuario=session.get('usuario'),
    )
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@inventario_bp.route('/conteo/<int:id_sesion>/deshacer', methods=['POST'])
def conteo_deshacer(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return jsonify({"ok": False, "error": "No hay base de datos."}), 400

    resultado = servicio_conteo.deshacer_ultima_lectura(
        ruta_db, id_sesion, usuario=session.get('usuario')
    )
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@inventario_bp.route('/conteo/<int:id_sesion>/pausar', methods=['POST'])
def conteo_pausar(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    resultado = servicio_conteo.pausar_sesion(
        ruta_db, id_sesion, usuario=session.get('usuario')
    )
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@inventario_bp.route('/conteo/<int:id_sesion>/reanudar', methods=['POST'])
def conteo_reanudar(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    resultado = servicio_conteo.reanudar_sesion(
        ruta_db, id_sesion, usuario=session.get('usuario')
    )
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@inventario_bp.route('/conteo/<int:id_sesion>/finalizar', methods=['POST'])
def conteo_finalizar(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    resultado = servicio_conteo.finalizar_sesion(
        ruta_db, id_sesion, usuario=session.get('usuario')
    )
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@inventario_bp.route('/conteo/<int:id_sesion>/aplicar', methods=['POST'])
def conteo_aplicar(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    resultado = servicio_conteo.aplicar_conteo(
        ruta_db, id_sesion, usuario=session.get('usuario')
    )

    if resultado.get('ok'):
        respaldo.actualizar_respaldo_si_posible(ruta_db=ruta_db)

    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@inventario_bp.route('/conteo/<int:id_sesion>/buscar', methods=['GET'])
def conteo_buscar(id_sesion):
    if 'usuario' not in session:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    ruta_db = db.ruta_bd_activa()
    return jsonify(
        servicio_conteo.buscar_articulos(ruta_db, request.args.get('q', ''))
    )


@inventario_bp.route('/actualizar_item', methods=['POST'])
def actualizar_item():
    """Actualiza datos comerciales y de ubicación.

    La cantidad NO se edita directamente: sólo cambia mediante conteos o
    movimientos controlados (README, módulo Inventario).
    """
    if 'usuario' not in session:
        return {'ok': False, 'error': 'No autenticado'}, 401

    try:
        data = request.get_json()
        item_id = data.get('id')
        referencia = data.get('referencia')
        descripcion = data.get('descripcion')
        precio = data.get('precio')
        costo = data.get('costo')
        grupo = data.get('grupo', '')
        ubicacion = data.get('ubicacion')
        estante = data.get('estante')
        stock_minimo = data.get('stockMinimo')
        estado_etiquetado = data.get('estadoEtiquetado')

        if not descripcion or not str(descripcion).strip():
            return {'ok': False, 'error': 'La descripción es obligatoria'}, 400

        precio_num = a_entero(precio, 0)
        costo_num = a_entero(costo, 0) if costo not in (None, '') else None
        stock_num = a_entero(stock_minimo, 0)

        precio_cifrado = cifrar_precio(precio_num)
        precio_descuento = precio_num * 0.9
        precio_descuento_redondeado = int(round(precio_descuento, -3))
        clave = normalizar_clave(descripcion)

        with db.conexion() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE inventarioUnico
                SET descripcion = ?,
                    descripcionClave = ?,
                    referencia = COALESCE(NULLIF(?, ''), referencia),
                    precioVenta = ?,
                    precioVentaCifrado = ?,
                    precioMaxDescuento = ?,
                    costo = CASE WHEN ? IS NOT NULL THEN ? ELSE costo END,
                    grupo = ?,
                    ubicacion = ?,
                    estante = ?,
                    stockMinimo = ?,
                    estadoEtiquetado = COALESCE(NULLIF(?, ''), estadoEtiquetado),
                    fechaActualizacion = ?
                WHERE id = ?
                """,
                (
                    str(descripcion).strip(),
                    clave,
                    str(referencia or '').strip(),
                    precio_num,
                    precio_cifrado,
                    precio_descuento_redondeado,
                    costo_num,
                    costo_num,
                    grupo,
                    ubicacion,
                    estante,
                    stock_num,
                    estado_etiquetado,
                    str(date.today()),
                    item_id,
                ),
            )

        respaldo.actualizar_respaldo_si_posible(ruta_db=db.ruta_bd_activa())

        return {'ok': True, 'mensaje': 'Item actualizado correctamente'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500


@inventario_bp.route('/imprimir_etiqueta_nueva', methods=['POST'])
def imprimir_etiqueta_nueva():
    """Imprimir etiqueta(s) de un item"""
    if 'usuario' not in session:
        return {'ok': False, 'error': 'No autenticado'}, 401

    try:
        data = request.get_json()

        codigo = data.get('codigo')
        descripcion = data.get('descripcion')
        precio = data.get('precio')
        cantidad = data.get('cantidad', 1)
        columna_inicio = data.get('columna_inicio', 1)
        template_id = data.get('template_id', 'default')

        precioCifrado = cifrar_precio(
            str(precio).replace(".", "").strip()
        )

        with db.conexion() as conn:

            cursor = conn.cursor()

            cursor.execute("""
                SELECT ubicacion
                FROM bartenderFiles
                WHERE idBartenderFiles = ?
            """, (template_id,))

            row = cursor.fetchone()

        if not row:
            raise Exception("Template BarTender no encontrado")

        ruta_template = row[0]

        productos = [(codigo, descripcion, precioCifrado, cantidad)]

        imprimir_varios(
            productos,
            ruta_template=ruta_template,
            columna_inicio=1
        )

        cursor = None
        with db.conexion() as conn:
            conn.execute(
                """
                UPDATE inventarioUnico
                SET estadoEtiquetado = 'IMPRESO',
                    fechaActualizacion = ?
                WHERE codigoBarras = ?
                """,
                (str(date.today()), codigo),
            )

        return {'ok': True, 'mensaje': f'{cantidad} etiqueta(s) enviada(s) a impresión'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}, 500

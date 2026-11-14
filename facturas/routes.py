from flask import Blueprint, render_template, session, redirect, url_for, request, send_from_directory, flash
from datetime import datetime
import os
import imaplib
import email
import zipfile
from email.utils import parseaddr
import xml.etree.ElementTree as ET
import pandas as pd

import db
from utilidades import cifrar_precio, a_entero, normalizar_clave
from facturas.validacion_consolidado import obtener_estados_factura, obtener_estado_linea, resolver_proveedor
from facturas import busqueda as busqueda_facturas
from facturas.xml_utils import (
    extraer_total_xml,
    extraer_total_desde_elemento,
    obtener_invoice_root,
)
from procesamiento.consolidado import procesar_dataframe_pendientes
from consolidado.servicio_inventario import obtener_o_crear_articulo
from inventario import respaldo
from impresion import servicio as impresion_servicio
import seguridad


facturas_bp = Blueprint('facturas', __name__, url_prefix='/facturas')


_RUTA_BUSQUEDA = '/facturas/buscar-articulos'


def return_to_seguro(valor):
    """Devuelve ``return_to`` sólo si apunta a la búsqueda interna.

    Rechaza esquemas, hosts, rutas absolutas de red (``//``), barras
    invertidas y cualquier destino que no sea la propia búsqueda de facturas.
    """

    from urllib.parse import urlsplit

    if not valor:
        return None

    texto = str(valor).strip()
    if not texto.startswith('/') or texto.startswith('//'):
        return None
    if '\\' in texto or '\r' in texto or '\n' in texto:
        return None

    partes = urlsplit(texto)
    if partes.scheme or partes.netloc:
        return None
    if not partes.path.startswith(_RUTA_BUSQUEDA):
        return None

    return texto


def _url_ver_factura(id_factura, return_to=None, **extra):
    parametros = {'id_factura': id_factura}
    if return_to:
        parametros['return_to'] = return_to
    parametros.update(extra)
    return url_for('facturas.ver_factura', **parametros)


def _ruta_xml_factura(id_factura):
    """Ubica el XML original de una factura dentro de bin/xml."""

    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "xml",
    )

    candidatos = [
        os.path.join(base, f"{id_factura}.xml"),
        os.path.join(base, f"{id_factura}.XML"),
    ]

    for candidato in candidatos:
        if os.path.isfile(candidato):
            return candidato

    try:
        for nombre in os.listdir(base):
            if (
                nombre.lower().endswith(".xml")
                and os.path.splitext(nombre)[0] == str(id_factura)
            ):
                return os.path.join(base, nombre)
    except OSError:
        pass

    return None


def reparar_total_factura(conn, id_factura):
    """Recalcula el total de una factura desde su XML original.

    Devuelve el total (float) o ``None`` si no fue posible obtenerlo.
    """

    ruta_xml = _ruta_xml_factura(id_factura)

    if not ruta_xml:
        print(f"[facturas] No se encontró el XML original de {id_factura}.")
        return None

    try:
        with open(ruta_xml, "rb") as archivo:
            contenido = archivo.read()
    except OSError as error:
        print(f"[facturas] No se pudo leer {ruta_xml}: {error}")
        return None

    resultado = extraer_total_xml(contenido)
    total = resultado.get("total")

    if total is None:
        print(
            "[facturas] Total no válido en "
            f"{os.path.basename(ruta_xml)}: {resultado.get('advertencias')}"
        )
        return None

    cursor = conn.cursor()
    cursor.execute(
        "UPDATE facturas SET valor_total = ? WHERE id_factura = ?",
        (float(total), id_factura),
    )
    return float(total)


def reparar_totales_faltantes(conn, limite=500):
    """Recalcula los totales guardados en NULL/0 usando los XML originales."""

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id_factura
        FROM facturas
        WHERE valor_total IS NULL OR valor_total = 0
        ORDER BY fecha DESC
        LIMIT ?
        """,
        (limite,),
    )
    pendientes = [fila[0] for fila in cursor.fetchall() if fila[0]]

    corregidas = 0

    for id_factura in pendientes:
        if reparar_total_factura(conn, id_factura) is not None:
            corregidas += 1

    if corregidas:
        print(f"[facturas] Totales reparados: {corregidas}")

    return corregidas


@facturas_bp.route('/', methods=['GET', 'POST'])
def facturas():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    mensaje = None
    hoy = datetime.now().strftime('%Y-%m-%d')
    fecha_desde = request.form.get('fecha_desde', hoy)
    fecha_hasta = request.form.get('fecha_hasta', hoy)

    if request.method == 'POST':

        EMAIL = obtener_email_configurado()
        PASSWORD = obtener_password_configurado()
        IMAP_SERVER = 'imap.gmail.com'

        FOLDER_DESTINO = 'bin'
        FOLDER_DESTINO_XML = 'bin/xml/'
        FOLDER_DESTINO_PDF = 'bin/pdf/'

        fecha_desde_imap = datetime.strptime(
            fecha_desde, '%Y-%m-%d'
        ).strftime('%d-%b-%Y')

        fecha_hasta_imap = datetime.strptime(
            fecha_hasta, '%Y-%m-%d'
        ).strftime('%d-%b-%Y')

        os.makedirs(FOLDER_DESTINO_XML, exist_ok=True)
        os.makedirs(FOLDER_DESTINO_PDF, exist_ok=True)

        # -----------------------------
        # Abrir la base de datos UNA sola vez
        # -----------------------------

        ruta_db = db.ruta_bd_activa()

        if not ruta_db:
            mensaje = "No existe base de datos."
            return render_template(
                "facturas/facturas.html",
                mensaje=mensaje
            )

        conn = db.conectar(ruta_db)
        cursor = conn.cursor()

        # -----------------------------
        # Facturas ya descargadas
        # -----------------------------

        cursor.execute("SELECT id_factura FROM facturas")
        facturas_existentes = {
            fila[0] for fila in cursor.fetchall()
        }

        # -----------------------------
        # Proveedores
        # -----------------------------

        cursor.execute("""
            SELECT correoFacturas, nit
            FROM proveedor
            WHERE nit IS NOT NULL
            AND TRIM(nit) <> ''
        """)

        proveedores = cursor.fetchall()

        # -----------------------------
        # Conectar Gmail
        # -----------------------------

        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, PASSWORD)
        mail.select("[Gmail]/Todos")

        contador = 0

        # ======================================================
        # Buscar proveedor por proveedor
        # ======================================================

        for correo_proveedor, nit in proveedores:

            criterio = (
                f'(SINCE {fecha_desde_imap} '
                f'BEFORE {fecha_hasta_imap} '
                f'SUBJECT "{nit}")'
            )

            status, messages = mail.search(None, criterio)

            if status != "OK":
                continue

            if not messages[0]:
                continue

            for num in messages[0].split():

                status, data = mail.fetch(num, '(RFC822)')

                if status != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])

                raw_remitente = msg.get("From")
                _, correo_real = parseaddr(raw_remitente)

                print(
                    f"Proveedor: {nit} | "
                    f"Correo: {correo_real}"
                )

                for part in msg.walk():

                    if part.get_content_maintype() == 'multipart':
                        continue

                    if part.get('Content-Disposition') is None:
                        continue

                    nombre_archivo = part.get_filename()

                    if not nombre_archivo:
                        continue

                    if not nombre_archivo.lower().endswith(".zip"):
                        continue

                    zip_path = os.path.join(
                        FOLDER_DESTINO,
                        nombre_archivo
                    )

                    with open(zip_path, "wb") as f:
                        f.write(part.get_payload(decode=True))

                    with zipfile.ZipFile(zip_path, "r") as zip_ref:

                        for file in zip_ref.namelist():

                            if file.lower().endswith(".xml"):

                                zip_ref.extract(file, FOLDER_DESTINO_XML)

                                ruta_xml = os.path.join(
                                    FOLDER_DESTINO_XML,
                                    file
                                )

                                try:

                                    tree = ET.parse(ruta_xml)
                                    root = tree.getroot()

                                    ns = {
                                        'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
                                        'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
                                    }

                                    # Misma lógica reutilizable que usa el lector
                                    # de importes: soporta <Invoice> directo y
                                    # <AttachedDocument> con la factura embebida.
                                    invoice_root = obtener_invoice_root(root)

                                    if invoice_root is None:
                                        continue

                                    proveedor_el = invoice_root.find(
                                        './/cac:AccountingSupplierParty//cac:PartyName/cbc:Name',
                                        ns
                                    )

                                    nombre_proveedor = (
                                        proveedor_el.text.strip()
                                        if proveedor_el is not None and proveedor_el.text
                                        else "Desconocido"
                                    )

                                    fecha_el = invoice_root.find(
                                        ".//cbc:IssueDate",
                                        ns
                                    )

                                    fecha_venta = (
                                        fecha_el.text.strip()
                                        if fecha_el is not None and fecha_el.text
                                        else ""
                                    )

                                    # Total: lectura reutilizable y tolerante a
                                    # namespaces/formatos (Decimal, no float directo).
                                    resultado_total = extraer_total_desde_elemento(
                                        invoice_root
                                    )
                                    total_decimal = resultado_total.get("total")
                                    valor_total = (
                                        float(total_decimal)
                                        if total_decimal is not None
                                        else None
                                    )
                                    if valor_total is None:
                                        print(
                                            "[facturas] Sin total válido en "
                                            f"{nombre_archivo}: "
                                            f"{resultado_total.get('advertencias')}"
                                        )
                                    elif resultado_total.get("advertencias"):
                                        print(
                                            "[facturas] Total con advertencias en "
                                            f"{nombre_archivo}: "
                                            f"{resultado_total.get('advertencias')}"
                                        )

                                    items_fragment = invoice_root.findall(
                                        ".//cac:InvoiceLine",
                                        ns
                                    )

                                    items_xml = ''.join(
                                        ET.tostring(
                                            item,
                                            encoding="unicode"
                                        )
                                        for item in items_fragment
                                    )

                                    id_factura = os.path.splitext(file)[0]

                                    # Ya existe
                                    if id_factura in facturas_existentes:
                                        continue

                                    pdf_filename = ""

                                    for pdf_file in zip_ref.namelist():

                                        if pdf_file.lower().endswith(".pdf"):

                                            zip_ref.extract(
                                                pdf_file,
                                                FOLDER_DESTINO_PDF
                                            )

                                            pdf_filename = pdf_file
                                            break

                                    ubicacion_pdf = (
                                        os.path.join(
                                            FOLDER_DESTINO_PDF,
                                            pdf_filename
                                        )
                                        if pdf_filename
                                        else ""
                                    )

                                    cursor.execute("""
                                        INSERT INTO facturas
                                        (
                                            id_factura,
                                            fecha,
                                            proveedor,
                                            valor_total,
                                            correo,
                                            texto_xml,
                                            ubicacion_pdf
                                        )
                                        VALUES
                                        (?,?,?,?,?,?,?)
                                    """,
                                    (
                                        id_factura,
                                        fecha_venta,
                                        nombre_proveedor,
                                        valor_total,
                                        correo_real,
                                        items_xml,
                                        ubicacion_pdf
                                    ))

                                    facturas_existentes.add(id_factura)

                                    for line in invoice_root.findall(
                                        ".//cac:InvoiceLine",
                                        ns
                                    ):

                                        descripcion_el = line.find(
                                            ".//cac:Item/cbc:Description",
                                            ns
                                        )

                                        descripcion = (
                                            descripcion_el.text.strip()
                                            if descripcion_el is not None and descripcion_el.text
                                            else "SIN_DESCRIPCION"
                                        )

                                        cantidad_el = line.find(
                                            "cbc:InvoicedQuantity",
                                            ns
                                        )

                                        cantidad = int(float(
                                            cantidad_el.text
                                            if cantidad_el is not None
                                            else 0
                                        ))

                                        precio_el = line.find(
                                            ".//cac:Price/cbc:PriceAmount",
                                            ns
                                        )

                                        precio = (
                                            float(precio_el.text)
                                            if precio_el is not None
                                            else 0
                                        )

                                        ref1 = line.find(
                                            ".//cac:SellersItemIdentification/cbc:ID",
                                            ns
                                        )

                                        ref2 = line.find(
                                            ".//cac:StandardItemIdentification/cbc:ID",
                                            ns
                                        )

                                        if ref1 is not None and ref1.text:
                                            referencia = ref1.text.strip()

                                        elif ref2 is not None and ref2.text:
                                            referencia = ref2.text.strip()

                                        else:
                                            referencia = "SIN_REFERENCIA"

                                        cursor.execute("""
                                            INSERT INTO inventarioFacturas
                                            (
                                                id_factura,
                                                descripcion_item,
                                                cantidad,
                                                valor_unitario,
                                                referencia
                                            )
                                            VALUES
                                            (?,?,?,?,?)
                                            ON CONFLICT (id_factura, referencia, descripcion_item)
                                            DO UPDATE SET
                                                cantidad = excluded.cantidad,
                                                valor_unitario = excluded.valor_unitario,
                                                descripcion_item = excluded.descripcion_item
                                        """,
                                        (
                                            id_factura,
                                            descripcion,
                                            cantidad,
                                            precio,
                                            referencia
                                        ))

                                    contador += 1

                                except Exception as e:

                                    print(
                                        f"Error procesando XML {file}: {e}"
                                    )

                            elif file.lower().endswith(".pdf"):

                                zip_ref.extract(
                                    file,
                                    FOLDER_DESTINO_PDF
                                )

                    os.remove(zip_path)
                    # Liberar la escritura antes de la siguiente descarga IMAP.
                    conn.commit()

        conn.commit()
        conn.close()

        mail.logout()

        mensaje = (
            f"Descarga completada. "
            f"Se procesaron {contador} facturas."
        )

    # return render_template(
    #     "facturas/facturas.html",
    #     mensaje=mensaje
    # )

    # Consultar facturas para mostrar en la tabla
    facturas_list = []
    ruta_db = db.ruta_bd_activa()
    if ruta_db:
        conn = db.conectar(ruta_db)
        cursor = conn.cursor()
        try:
            reparar_totales_faltantes(conn)
            conn.commit()
        except Exception as error:
            print(f"[facturas] No se pudieron reparar totales: {error}")
            conn.rollback()
        cursor.execute("""
            SELECT 
                f.id_factura,
                f.proveedor,
                f.fecha,
                f.valor_total,
                f.ubicacion_pdf,
                COUNT(inf.id) as total_articulos,
                SUM(CASE WHEN inf.inventariado = 0 THEN 1 ELSE 0 END) as articulos_sin_procesar
            FROM facturas f
            LEFT JOIN inventarioFacturas inf ON f.id_factura = inf.id_factura
            GROUP BY f.id_factura
            ORDER BY f.fecha DESC
        """)
        
        facturas_list = cursor.fetchall()
        conn.close()

    return render_template(
        'facturas/facturas.html',
        usuario=session.get('usuario'),
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        mensaje=mensaje,
        facturas_list=facturas_list
    )

@facturas_bp.route('/buscar-articulos')
def buscar_articulos():
    """Búsqueda transversal de artículos en facturas (sólo lectura)."""

    ruta_db = db.ruta_bd_activa()

    if not ruta_db:
        return "No hay base de datos creada", 404

    filtros = busqueda_facturas.extraer_filtros(request.args)

    resultado = busqueda_facturas.buscar_articulos(
        ruta_db,
        filtros,
        pagina=request.args.get('pagina', 1),
    )

    # Filtros sin paginación, para conservarlos en la paginación y en el
    # enlace "Volver a resultados" del detalle.
    parametros = {
        clave: valor
        for clave, valor in request.args.to_dict(flat=True).items()
        if clave != 'pagina' and valor
    }

    return render_template(
        'facturas/buscar_articulos.html',
        filtros=filtros,
        resultado=resultado,
        estados=busqueda_facturas.ESTADOS,
        parametros=parametros,
        return_to_actual=request.full_path.rstrip('?'),
    )


@facturas_bp.route('/ver/<id_factura>', methods=['GET', 'POST'])
def ver_factura(id_factura):

    ruta_db = db.ruta_bd_activa()

    if not ruta_db:
        return "No hay base de datos creada", 404

    # ===============================
    # Navegación de regreso a la búsqueda de artículos
    # ===============================
    return_to_post = None
    if request.method == 'POST':
        return_to_post = return_to_seguro(request.form.get('return_to')) or \
            return_to_seguro(session.get('facturas_return_to'))
    else:
        return_to_post = return_to_seguro(request.args.get('return_to'))
        if return_to_post:
            session['facturas_return_to'] = return_to_post
        else:
            session.pop('facturas_return_to', None)

    # ===============================
    # POST → Inventariar o procesar pendientes a consolidado
    # ===============================
    if request.method == 'POST':

        accion_extra = request.form.get('accion_extra')
        if accion_extra == 'procesar_consolidado':
            ruta_estandarizacion = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "bin/xlsx",
                "estandarizacion.xlsx"
            )
            try:
                with db.conexion(ruta_db) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT fac.id_factura, prov.idProveedor, fac.proveedor, fac.fecha, fac.valor_total, prov.siglas
                        FROM facturas fac
                        LEFT JOIN proveedor prov ON fac.proveedor = prov.nombreProveedor OR fac.proveedor = prov.nombreProveedorSecundario
                        WHERE fac.id_factura = ?
                    """, (id_factura,))
                    factura = cursor.fetchone()
                    if not factura:
                        flash("No se encontró la factura.", "danger")
                        return redirect(_url_ver_factura(id_factura, return_to_post))

                    seleccionados_idx = request.form.getlist("seleccionados_consolidado[]")
                    referencias_proveedor = request.form.getlist("referenciaProveedor[]")
                    referencias_repuesto = request.form.getlist("referenciaRepuesto[]")
                    equivalentes = request.form.getlist("equivalente[]")
                    descripciones = request.form.getlist("descripcion[]")
                    marcas = request.form.getlist("marcaRepuesto[]")
                    carros = request.form.getlist("carro[]")
                    lados = request.form.getlist("lado[]")
                    modelos = request.form.getlist("modelo[]")
                    medidas = request.form.getlist("medida[]")
                    grupos = request.form.getlist("grupo[]")
                    precioListado = request.form.getlist("precioListado[]")
                    ids_inv_factura = request.form.getlist("idInventarioFactura[]")

                    seleccionados_set = set()
                    for s in seleccionados_idx:
                        try:
                            seleccionados_set.add(int(s))
                        except Exception:
                            pass

                    omitidos = 0
                    errores = 0
                    ambiguos = 0
                    articulos_df = []
                    lineas_por_clave = []

                    def _valor(lista, indice):
                        return (lista[indice] if indice < len(lista) else '') or ''

                    for i in range(len(referencias_proveedor)):
                        if i not in seleccionados_set:
                            continue
                        ref_p = _valor(referencias_proveedor, i).strip()
                        if not ref_p:
                            errores += 1
                            continue

                        # Validar backend: la línea pertenece a la factura actual.
                        inv_line_id = None
                        try:
                            inv_line_id = int(ids_inv_factura[i])
                        except Exception:
                            inv_line_id = None
                        if inv_line_id is None:
                            errores += 1
                            continue
                        cursor.execute(
                            "SELECT referencia, descripcion_item "
                            "FROM inventarioFacturas "
                            "WHERE id = ? AND id_factura = ?",
                            (inv_line_id, id_factura),
                        )
                        fila_linea = cursor.fetchone()
                        if not fila_linea:
                            errores += 1
                            continue
                        referencia_original = (fila_linea[0] or '').strip()
                        descripcion_original = (fila_linea[1] or '').strip()

                        desc = _valor(descripciones, i)
                        if not desc.strip():
                            errores += 1
                            continue

                        # La llave es proveedor (siglas) + referencia. Si existen
                        # varias filas no se elige la primera: se bloquea.
                        clave = None
                        if factura[5]:
                            siglas = str(factura[5]).strip().upper()
                            clamp_ref = ref_p.upper()
                            cursor.execute(
                                "SELECT COUNT(*) FROM consolidado "
                                "WHERE UPPER(TRIM(abrevProveedor)) = ? "
                                "AND UPPER(TRIM(referenciaProveedor)) = ?",
                                (siglas, clamp_ref),
                            )
                            if (cursor.fetchone() or [0])[0] > 1:
                                ambiguos += 1
                                cursor.execute(
                                    "UPDATE consolidado SET estadoConsolidado = 'BLOQUEADO' "
                                    "WHERE UPPER(TRIM(abrevProveedor)) = ? "
                                    "AND UPPER(TRIM(referenciaProveedor)) = ? "
                                    "AND COALESCE(estadoConsolidado, '') <> 'INVENTARIADO'",
                                    (siglas, clamp_ref),
                                )
                                continue
                            clave = (siglas, clamp_ref)

                        articulos_df.append({
                            "referenciaProveedor": ref_p,
                            "referenciaRepuesto": _valor(referencias_repuesto, i),
                            "equivalente": _valor(equivalentes, i),
                            "descripcion": desc.strip(),
                            "marca": _valor(marcas, i),
                            "carro": _valor(carros, i),
                            "tipo": "",
                            "tipoMotor": "",
                            "lado": _valor(lados, i),
                            "modelo": _valor(modelos, i),
                            "medida": _valor(medidas, i),
                            "grupo": _valor(grupos, i),
                            "precioListado": _valor(precioListado, i),
                            "proveedor": factura[2],
                            "idProveedor": factura[1],
                            "abrevProveedor": factura[5],
                        })
                        lineas_por_clave.append({
                            "id": inv_line_id,
                            "clave": clave,
                            "referenciaOriginal": referencia_original,
                            "referenciaCorregida": ref_p,
                            "descripcion": desc.strip(),
                            "descripcionManual": (
                                desc.strip() != descripcion_original
                            ),
                        })

                    if not articulos_df:
                        flash(
                            f"Procesados: omitidos {omitidos}, ambiguos {ambiguos}, "
                            f"errores {errores}. No se creó ningún artículo.",
                            "warning",
                        )
                        return redirect(_url_ver_factura(id_factura, return_to_post, procesar=1))

                    df = pd.DataFrame(articulos_df)
                    # Los valores enviados por el formulario son ediciones del
                    # usuario: tienen prioridad sobre el cálculo automático.
                    campos_manuales = {
                        campo
                        for campo in (
                            "carro", "referenciaRepuesto", "equivalente",
                            "marca", "modelo", "lado", "medida", "grupo",
                        )
                        if any(
                            str(articulo.get(campo) or '').strip()
                            for articulo in articulos_df
                        )
                    }
                    # La descripción sólo es manual si el usuario la cambió;
                    # de lo contrario se aplican los ajustes de estandarización.
                    if any(
                        linea.get("descripcionManual")
                        for linea in lineas_por_clave
                    ):
                        campos_manuales.add("descripcion")
                    df = procesar_dataframe_pendientes(
                        df, ruta_estandarizacion, campos_manuales=campos_manuales
                    )
                    from app import actualizar_consolidado
                    resultado = actualizar_consolidado(conn, df)

                    # Vincular cada línea de factura con su registro de
                    # consolidado y conservar la referencia original.
                    ids_por_clave = resultado.get("ids_por_clave") or {}
                    for linea in lineas_por_clave:
                        id_consolidado = None
                        if linea["clave"] is not None:
                            id_consolidado = ids_por_clave.get(linea["clave"])
                        corregida = linea["referenciaCorregida"]
                        original = linea["referenciaOriginal"] or corregida
                        cursor.execute(
                            """
                            UPDATE inventarioFacturas
                            SET referenciaOriginal = COALESCE(referenciaOriginal, ?),
                                referenciaCorregida = CASE
                                    WHEN UPPER(TRIM(?)) <> UPPER(TRIM(?)) THEN ?
                                    ELSE referenciaCorregida END,
                                referencia = ?,
                                id_consolidado = COALESCE(?, id_consolidado)
                            WHERE id = ? AND id_factura = ?
                            """,
                            (
                                original,
                                corregida,
                                original,
                                corregida,
                                corregida,
                                id_consolidado,
                                linea["id"],
                                id_factura,
                            ),
                        )
                    conn.commit()

                    creados_res = resultado.get('nuevos', 0)
                    mod_res = resultado.get('modificados', 0)
                    amb_res = resultado.get('ambiguos', 0)
                    flash(
                        f"Procesados: creados {creados_res}, modificados {mod_res}, "
                        f"omitidos {omitidos + amb_res}, ambiguos {ambiguos}, errores {errores}",
                        "success" if creados_res or mod_res else "warning",
                    )
                    return redirect(_url_ver_factura(id_factura, return_to_post, procesar=1))
            except Exception as e:
                flash(f"Error procesando artículos a consolidado: {e}", "danger")
                return redirect(_url_ver_factura(id_factura, return_to_post, procesar=1))

        seleccionados = request.form.getlist('seleccionados')
        template_id = request.form.get("template_id")

        if not seleccionados:
            flash("Selecciona al menos un artículo para inventariar", "warning")
            return redirect(
                _url_ver_factura(id_factura, return_to_post)
            )

        ids = [a_entero(x, 0) for x in seleccionados]

        items_para_imprimir = []
        ruta_template = None
        plantilla_nombre = None
        procesados = 0
        omitidos = 0
        bloqueados = 0

        # ---- Transacción completa para todas las líneas seleccionadas ----
        try:
            with db.conexion(ruta_db) as conn:

                cursor = conn.cursor()

                placeholders = ",".join("?" * len(ids))

                cursor.execute(f"""
                    SELECT
                        a.id,
                        a.referencia,
                        a.descripcion_item,
                        a.cantidad,
                        a.valor_unitario,
                        a.inventariado,
                        a.id_inventarioUnico,
                        b.proveedor,
                        b.fecha,
                        a.referenciaOriginal,
                        a.referenciaCorregida
                    FROM inventarioFacturas a
                    LEFT JOIN facturas b ON a.id_factura = b.id_factura
                    WHERE a.id IN ({placeholders})
                      AND a.id_factura = ?
                """, (*ids, id_factura))

                items_bd = {fila[0]: fila for fila in cursor.fetchall()}

                ids_para_imprimir = []
                datos_impresion = []

                for id_fact_item in ids:

                    fila = items_bd.get(id_fact_item)

                    if not fila:
                        omitidos += 1
                        continue

                    (
                        id_fila, referencia, descripcion_orig, cantidad,
                        valor_unitario, inventariado, id_inventario_unico_bd, proveedor,
                        fecha, referencia_original_bd, referencia_corregida_bd
                    ) = fila

                    # Valor manual del formulario. Si el usuario corrigió la
                    # referencia, se valida contra esa referencia corregida.
                    referencia_form = (
                        request.form.get(f'referencia_{id_fact_item}', '') or ''
                    ).strip()
                    descripcion_form = (
                        request.form.get(f'descripcion_{id_fact_item}', '') or ''
                    ).strip()

                    referencia_use = (
                        referencia_form
                        or (referencia_corregida_bd or '').strip()
                        or (referencia or '').strip()
                    )
                    referencia_original = (
                        (referencia_original_bd or '').strip()
                        or (referencia or '').strip()
                    )

                    linea_validacion = (
                        id_fila,
                        referencia_use,
                        descripcion_orig,
                        cantidad,
                        valor_unitario,
                        inventariado,
                        id_inventario_unico_bd,
                    )

                    # Revalidar estado real en backend con la referencia efectiva.
                    estado = obtener_estado_linea(
                        conn, (proveedor, fecha, None, None, None), linea_validacion
                    )
                    if not estado.get('permitido'):
                        bloqueados += 1
                        continue

                    datos_est = estado.get('datos_estandarizados') or {}
                    descripcion_est = (
                        datos_est.get('descripcion')
                        or datos_est.get('descripcionEstandarizada')
                        or descripcion_orig
                    )

                    # Edición manual de la descripción: sólo se usa cuando
                    # difiere de la descripción original de la factura.
                    if (
                        descripcion_form
                        and descripcion_form != (descripcion_orig or '').strip()
                    ):
                        descripcion_est = descripcion_form

                    valor_publico = request.form.get(
                        f'valor_publico_{id_fact_item}', ''
                    )

                    precio_venta = a_entero(
                        str(valor_publico).replace(".", "").strip(), 0
                    )
                    if precio_venta == 0 and valor_unitario:
                        try:
                            precio_venta = int(round(float(valor_unitario) * 1.5, -3))
                        except Exception:
                            precio_venta = 0
                    precio_cifrado = cifrar_precio(precio_venta)
                    precio_max_desc = round(precio_venta * 0.9, 0)
                    clave = normalizar_clave(descripcion_est)
                    if not clave:
                        bloqueados += 1
                        continue

                    prov_use = datos_est.get('proveedor') or proveedor or ''
                    datos_operativos = {
                        "descripcionEstandarizada": descripcion_est,
                        "referenciaProveedor": referencia_use,
                        "referenciaRepuesto": datos_est.get('referenciaRepuesto'),
                        "equivalente": datos_est.get('equivalente'),
                        "marcaArticulo": datos_est.get('marca'),
                        "marcaVehiculo": datos_est.get('carro'),
                        "modelo": datos_est.get('modelo'),
                        "lado": datos_est.get('lado'),
                        "medida": datos_est.get('medida'),
                    }

                    articulo = obtener_o_crear_articulo(
                        conn,
                        descripcion=descripcion_est,
                        descripcion_clave=clave,
                        referencia=referencia_use,
                        proveedor=prov_use,
                        precio_venta=precio_venta,
                        precio_cifrado=precio_cifrado,
                        precio_max_descuento=precio_max_desc,
                        grupo=datos_est.get('grupo') or '',
                        fecha=fecha or datetime.now().strftime('%Y-%m-%d'),
                        cantidad=cantidad,
                        sumar_cantidad=True,
                        datos_operativos=datos_operativos,
                    )
                    inventario_id = articulo['id']
                    if not inventario_id:
                        raise Exception(f"No se pudo obtener inventarioUnico para {id_fact_item}")

                    cursor.execute("""
                        UPDATE inventarioFacturas
                        SET inventariado = 1,
                            id_inventarioUnico = ?,
                            id_consolidado = COALESCE(?, id_consolidado),
                            referenciaOriginal = COALESCE(referenciaOriginal, ?),
                            referenciaCorregida = CASE
                                WHEN UPPER(TRIM(?)) <> UPPER(TRIM(?)) THEN ?
                                ELSE referenciaCorregida END,
                            referencia = ?
                        WHERE id = ?
                    """, (
                        inventario_id,
                        datos_est.get('id_consolidado'),
                        referencia_original,
                        referencia_use,
                        referencia_original,
                        referencia_use,
                        referencia_use,
                        id_fact_item,
                    ))

                    ids_para_imprimir.append(inventario_id)
                    procesados += 1

                # Datos para impresión
                if ids_para_imprimir and template_id:
                    placeholders_imp = ",".join("?" * len(ids_para_imprimir))

                    cursor.execute(f"""
                        SELECT
                            iU.codigoBarras,
                            iU.descripcion,
                            iU.precioVentaCifrado,
                            iF.cantidad,
                            PR.siglas
                        FROM inventarioFacturas iF
                        JOIN inventarioUnico iU ON iF.id_inventarioUnico = iU.id
                        LEFT JOIN proveedor PR
                            ON PR.nombreProveedor = iU.proveedor
                            OR PR.nombreProveedorSecundario = iU.proveedor
                        WHERE iF.id_factura = ?
                          AND iU.id IN ({placeholders_imp})
                    """, (id_factura, *ids_para_imprimir))
                    datos_impresion = cursor.fetchall()

                    cursor.execute("""
                        SELECT name, ubicacion
                        FROM bartenderFiles
                        WHERE idBartenderFiles = ?
                    """, (template_id,))
                    row_tpl = cursor.fetchone()

                    if not row_tpl:
                        raise Exception("Template BarTender no encontrado")

                    plantilla_nombre = row_tpl[0]
                    ruta_template = row_tpl[1]
                elif ids_para_imprimir and not template_id:
                    raise Exception("Debe seleccionar una etiqueta BarTender")

            respaldo.actualizar_respaldo_si_posible(ruta_db=ruta_db)

            # Fuera de transacción: encolar la impresión en el servidor.
            if datos_impresion and ruta_template:
                try:
                    trabajo = impresion_servicio.crear_trabajo(
                        ruta_db,
                        usuario=session.get('usuario'),
                        articulos=datos_impresion,
                        id_plantilla=template_id,
                        plantilla=plantilla_nombre,
                        ruta_plantilla=ruta_template,
                        origen='facturas',
                        columna_inicio=1,
                        clave_unica=request.form.get('clave_idempotencia'),
                    )
                    if trabajo.get('duplicado'):
                        flash(
                            "La impresión ya estaba en la cola "
                            f"(trabajo #{trabajo['idTrabajo']}).",
                            "info",
                        )
                    else:
                        flash(
                            "Impresión encolada en el servidor "
                            f"(trabajo #{trabajo['idTrabajo']}).",
                            "info",
                        )
                except Exception as e_imp:
                    flash(
                        "Inventariado correcto, pero no se pudo encolar la "
                        f"impresión: {e_imp}",
                        "warning",
                    )
                    return redirect(_url_ver_factura(id_factura, return_to_post))

            if procesados > 0:
                flash(f"Inventariados: {procesados}. Omitidos/bloqueados: {omitidos + bloqueados}", "success")
            elif bloqueados > 0:
                flash("Ninguna línea seleccionada pudo inventariarse. Verifique coincidencia con consolidado.", "danger")
            else:
                flash("No se procesaron líneas.", "warning")

        except Exception as e:
            flash(f"Error al inventariar: {e}", "danger")
            return redirect(_url_ver_factura(id_factura, return_to_post))

        return redirect(
            _url_ver_factura(id_factura, return_to_post)
        )

    # ===============================
    # GET → Mostrar factura
    # ===============================
    with db.conexion(ruta_db) as conn:

        cursor = conn.cursor()

        # Reparar totales históricos guardados en 0/NULL (una sola pasada).
        try:
            reparar_totales_faltantes(conn)
        except Exception as error:
            print(f"[facturas] No se pudieron reparar totales: {error}")

        cursor.execute("""
            SELECT idBartenderFiles, name
            FROM bartenderFiles
            WHERE activo = 1
            ORDER BY name
        """)
        bartender_templates = cursor.fetchall()

        cursor.execute("""
            SELECT proveedor, fecha, valor_total, texto_xml, ubicacion_pdf
            FROM facturas
            WHERE id_factura = ?
        """, (id_factura,))
        factura = cursor.fetchone()

        # Si el total sigue vacío, intentar una reparación puntual.
        if factura and not factura[2]:
            total_reparado = reparar_total_factura(conn, id_factura)
            if total_reparado is not None:
                factura = (
                    factura[0],
                    factura[1],
                    total_reparado,
                    factura[3],
                    factura[4],
                )

        cursor.execute("""
            SELECT fac.id_factura, prov.idProveedor, fac.proveedor, fac.fecha, fac.valor_total, prov.siglas
            FROM facturas fac
            LEFT JOIN proveedor prov ON fac.proveedor = prov.nombreProveedor OR fac.proveedor = prov.nombreProveedorSecundario
            WHERE fac.id_factura = ?
        """, (id_factura,))
        factura_consolidado = cursor.fetchone()

        cursor.execute("""
            SELECT id, referencia, descripcion_item, cantidad, valor_unitario, inventariado, id_inventarioUnico
            FROM inventarioFacturas
            WHERE id_factura = ?
        """, (id_factura,))
        factura_items = cursor.fetchall()

        # Línea resaltada al llegar desde la búsqueda de artículos. Se valida
        # contra la factura abierta: una línea ajena se ignora.
        linea_destacada = None
        linea_solicitada = request.args.get('linea')
        if linea_solicitada:
            try:
                linea_id = int(linea_solicitada)
            except (TypeError, ValueError):
                linea_id = None
            if linea_id is not None:
                cursor.execute(
                    "SELECT 1 FROM inventarioFacturas "
                    "WHERE id = ? AND id_factura = ?",
                    (linea_id, id_factura),
                )
                if cursor.fetchone():
                    linea_destacada = linea_id

        prov_info_get = None
        if factura and factura[0]:
            try:
                prov_info_get = resolver_proveedor(conn, factura[0])
            except Exception:
                prov_info_get = None
        siglas_get = prov_info_get.get('siglas') if prov_info_get else (factura_consolidado[5] if factura_consolidado and len(factura_consolidado) > 5 else None)

        articulos_pendientes = []
        for fila in factura_items:
            ref_p = fila[1] or ""
            ya_consolidado = False
            if siglas_get and ref_p:
                try:
                    cursor.execute("SELECT 1 FROM consolidado WHERE UPPER(TRIM(abrevProveedor)) = ? AND UPPER(TRIM(referenciaProveedor)) = ?", (str(siglas_get).upper().strip(), str(ref_p).upper().strip()))
                    if cursor.fetchone():
                        ya_consolidado = True
                except Exception:
                    ya_consolidado = False
            articulos_pendientes.append({
                "idInventarioFactura": fila[0],
                "id_factura": id_factura,
                "referenciaProveedor": ref_p,
                "referenciaRepuesto": "",
                "equivalente": "",
                "descripcion": fila[2] or "",
                "marcaRepuesto": "",
                "carro": "",
                "lado": "",
                "modelo": "",
                "medida": "",
                "grupo": "",
                "precioListado": fila[4],
                "ya_consolidado": ya_consolidado
            })

        estados = {}
        try:
            estados = obtener_estados_factura(conn, id_factura)
        except Exception:
            estados = {}

        mostrar_consolidado = request.args.get('procesar') == '1' or request.args.get('consolidar') == '1'

    return render_template(
        'facturas/detalle.html',
        factura=factura,
        id_factura=id_factura,
        items_factura=factura_items,
        bartender_templates=bartender_templates,
        estados_linea=estados,
        factura_consolidado=factura_consolidado,
        articulos_pendientes=articulos_pendientes,
        mostrar_consolidado=mostrar_consolidado,
        linea_destacada=linea_destacada,
        return_to=return_to_post,
    )

@facturas_bp.route('/pdf/<filename>')
def ver_pdf(filename):
    pdf_folder = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'bin',
        'pdf',
    )
    return send_from_directory(pdf_folder, filename)

def obtener_ultima_configuracion():
    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return '', '', ''
    try:
        conn = db.conectar(ruta_db)
        cursor = conn.cursor()
        cursor.execute("SELECT email_revision, token_contraseña, correos_varios FROM configuracion ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            email_revision, token_contraseña, correos_varios = row
            token_contraseña = seguridad.descifrar_token(token_contraseña)
            return email_revision, token_contraseña, correos_varios
    except Exception:
        pass
    return '', '', ''

def obtener_email_configurado():
    email, _, _ = obtener_ultima_configuracion()
    return email

def obtener_password_configurado():
    _, password, _ = obtener_ultima_configuracion()
    return password

def obtener_dominios_configurados():
    ruta_db = db.ruta_bd_activa()
    if not ruta_db:
        return ''
    try:
        conn = db.conectar(ruta_db)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT correoFacturas FROM proveedor")
        correos = cursor.fetchall()
        # cambialo a una lista
        correos_lista = [c[0] for c in correos if c[0]]
        print(f"Dominios configurados: {correos_lista}")
        conn.close()
        return correos_lista
    except Exception:
        pass
    return ''


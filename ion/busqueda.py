"""Búsqueda híbrida de ION.

Combina coincidencia exacta (código de barras y referencias), el índice FTS5
de SQLite, normalización con sinónimos y similitud de texto. Siempre separa
los artículos que están en Inventario de los que sólo existen en Consolidado
y nunca presenta un artículo de Consolidado como disponible para venta.
"""

from __future__ import annotations

import difflib
import re
import sqlite3

from . import conocimiento, lexico, normalizacion, preguntas


_RE_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")
ESTADOS_INVENTARIADOS = ("INVENTARIADO",)
MINIMO_PUNTAJE = 0.30
MAXIMO_RESULTADOS = 12
MAXIMO_CANDIDATOS = 600


def _clave_referencia(valor):
    if valor is None:
        return ""
    return _RE_ALFANUMERICO.sub("", str(valor).upper())


def _referencias(valores):
    referencias = set()
    for valor in valores:
        if valor is None:
            continue
        texto = str(valor).strip()
        if not texto:
            continue
        for parte in re.split(r"[,;/|]+", texto):
            clave = _clave_referencia(parte)
            if clave:
                referencias.add(clave)
        clave = _clave_referencia(texto)
        if clave:
            referencias.add(clave)
    return referencias


def _formatear_precio(valor):
    try:
        return "$" + f"{float(valor):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


# ---------------------------------------------------------------------------
# Índice FTS5
# ---------------------------------------------------------------------------
def _fts_disponible(conn):
    try:
        conn.execute(
            "SELECT 1 FROM ionIndice LIMIT 1"
        ).fetchone()
        return True
    except sqlite3.Error:
        return False


def _texto_indice_inventario(fila):
    return normalizacion.normalizar(" ".join(str(parte or "") for parte in fila))


def _texto_indice_consolidado(fila):
    return normalizacion.normalizar(" ".join(str(parte or "") for parte in fila))


def _contar_fuentes(conn):
    inventario = conn.execute(
        "SELECT COUNT(*) FROM inventarioUnico"
    ).fetchone()[0]
    consolidado = conn.execute(
        """
        SELECT COUNT(*) FROM consolidado
        WHERE idInventarioUnico IS NULL
          AND UPPER(COALESCE(estadoConsolidado, '')) NOT IN ('INVENTARIADO')
        """
    ).fetchone()[0]
    return inventario, consolidado


def _leer_meta(conn):
    try:
        return dict(conn.execute("SELECT clave, valor FROM ionIndiceMeta"))
    except sqlite3.Error:
        return {}


def asegurar_indice(conn, forzar=False):
    """Reconstruye el índice FTS5 si las fuentes cambiaron.

    Devuelve ``True`` si el índice quedó disponible. Si SQLite no tiene FTS5
    se devuelve ``False`` y la búsqueda usa el respaldo LIKE/similitud.
    """

    if not _fts_disponible(conn):
        return False

    inventario, consolidado = _contar_fuentes(conn)
    meta = _leer_meta(conn)

    if (
        not forzar
        and str(meta.get("dirty", "1")) != "1"
        and str(meta.get("inv_total", "")) == str(inventario)
        and str(meta.get("con_total", "")) == str(consolidado)
    ):
        return True

    filas = []

    for fila in conn.execute(
        """
        SELECT id, codigoBarras, referencia, descripcion, descripcionClave,
               grupo, proveedor
        FROM inventarioUnico
        """
    ):
        filas.append(
            ("INVENTARIO", fila[0], _texto_indice_inventario(fila[1:]))
        )

    for fila in conn.execute(
        """
        SELECT id, referenciaProveedor, referenciaRepuesto, equivalente,
               descripcion, descripcionEstandarizada, marca, carro, modelo,
               tipo, tipoMotor, lado, ladoAbreviado, repuesto, otros, medida,
               grupo, proveedor
        FROM consolidado
        WHERE idInventarioUnico IS NULL
          AND UPPER(COALESCE(estadoConsolidado, '')) NOT IN ('INVENTARIADO')
        """
    ):
        filas.append(
            ("CONSOLIDADO", fila[0], _texto_indice_consolidado(fila[1:]))
        )

    try:
        conn.execute("DELETE FROM ionIndice")
        if filas:
            conn.executemany(
                "INSERT INTO ionIndice (tipo, idOrigen, contenido) VALUES (?, ?, ?)",
                filas,
            )
        conn.execute(
            """
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '0')
            ON CONFLICT(clave) DO UPDATE SET valor = '0'
            """
        )
        conn.execute(
            """
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('inv_total', ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
            """,
            (str(inventario),),
        )
        conn.execute(
            """
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('con_total', ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
            """,
            (str(consolidado),),
        )
    except sqlite3.Error:
        return False

    return True


def _marcar_indice_obsoleto(conn):
    try:
        conn.execute(
            """
            INSERT INTO ionIndiceMeta (clave, valor) VALUES ('dirty', '1')
            ON CONFLICT(clave) DO UPDATE SET valor = '1'
            """
        )
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------
# Recuperación de candidatos
# ---------------------------------------------------------------------------
def _terminos_busqueda(interpretacion):
    terminos = []
    for grupo in interpretacion.get("grupos", []):
        # Se prefiere la forma más corta/canónica del grupo.
        if grupo:
            terminos.append(sorted(grupo, key=len)[0])
    salida = []
    for termino in terminos:
        if termino not in salida:
            salida.append(termino)
    return salida[:12]


def _consulta_fts(interpretacion):
    terminos = _terminos_busqueda(interpretacion)
    validos = [
        termino
        for termino in terminos
        if re.fullmatch(r"[A-Z0-9]+", termino or "") and len(termino) >= 2
    ]
    if not validos:
        return ""
    return " OR ".join('"%s"*' % termino for termino in validos)


def _ids_fts(conn, consulta, tipo, limite=MAXIMO_CANDIDATOS):
    if not consulta:
        return []
    try:
        filas = conn.execute(
            """
            SELECT idOrigen FROM ionIndice
            WHERE ionIndice MATCH ? AND tipo = ?
            LIMIT ?
            """,
            (consulta, tipo, limite),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [fila[0] for fila in filas]


def _ids_like_consolidado(conn, interpretacion, limite=400):
    terminos = [
        termino
        for termino in _terminos_busqueda(interpretacion)
        if len(termino) >= 3
    ][:6]
    if not terminos:
        return []

    condiciones = []
    parametros = []
    for termino in terminos:
        patron = "%" + termino + "%"
        condiciones.append(
            "(UPPER(COALESCE(descripcion, '')) LIKE ? "
            "OR UPPER(COALESCE(descripcionEstandarizada, '')) LIKE ?)"
        )
        parametros.extend([patron, patron])

    sql = """
        SELECT id FROM consolidado
        WHERE idInventarioUnico IS NULL
          AND UPPER(COALESCE(estadoConsolidado, '')) NOT IN ('INVENTARIADO')
          AND (%s)
        LIMIT ?
    """ % " OR ".join(condiciones)
    parametros.append(limite)

    try:
        return [fila[0] for fila in conn.execute(sql, parametros)]
    except sqlite3.Error:
        return []


def _ids_referencia_exacta(conn, clave, original=""):
    if not clave or len(clave) < 3:
        return []

    patrones = []
    for valor in (original, clave):
        valor = str(valor or "").strip()
        if valor and valor not in patrones:
            patrones.append(valor)
    if not patrones:
        return []

    condiciones = []
    parametros = []
    for patron in patrones:
        condiciones.append(
            "(UPPER(COALESCE(referenciaProveedor, '')) LIKE ? "
            "OR UPPER(COALESCE(referenciaRepuesto, '')) LIKE ? "
            "OR UPPER(COALESCE(equivalente, '')) LIKE ?)"
        )
        parametros.extend(["%" + patron + "%"] * 3)

    try:
        filas = conn.execute(
            """
            SELECT id FROM consolidado
            WHERE idInventarioUnico IS NULL
              AND UPPER(COALESCE(estadoConsolidado, '')) NOT IN ('INVENTARIADO')
              AND (%s)
            LIMIT 25
            """ % " OR ".join(condiciones),
            parametros,
        ).fetchall()
    except sqlite3.Error:
        return []
    return [fila[0] for fila in filas]


def _filas_consolidado(conn, ids):
    if not ids:
        return []
    ids = list(dict.fromkeys(ids))[:MAXIMO_CANDIDATOS]
    marcadores = ",".join("?" for _ in ids)
    return conn.execute(
        """
        SELECT id, referenciaProveedor, referenciaRepuesto, equivalente,
               descripcion, descripcionEstandarizada, marca, carro, modelo,
               tipo, tipoMotor, lado, ladoAbreviado, repuesto, otros, medida,
               grupo, proveedor, estadoConsolidado, idInventarioUnico
        FROM consolidado
        WHERE id IN (%s)
          AND idInventarioUnico IS NULL
          AND UPPER(COALESCE(estadoConsolidado, '')) NOT IN ('INVENTARIADO')
        """ % marcadores,
        ids,
    ).fetchall()


def _ids_similitud_consolidado(conn, interpretacion, limite=120):
    """Respaldo para errores de escritura cuando FTS y LIKE no encuentran."""

    terminos = [
        termino
        for termino in _terminos_busqueda(interpretacion)
        if len(termino) >= 4
    ][:5]
    if not terminos:
        return []

    try:
        prefijos = []
        for termino in terminos:
            prefijo = termino[:4]
            if len(prefijo) >= 3 and prefijo not in prefijos:
                prefijos.append(prefijo)

        condiciones = []
        parametros = []
        for prefijo in prefijos:
            condiciones.append(
                "(UPPER(COALESCE(descripcion, '')) LIKE ? "
                "OR UPPER(COALESCE(descripcionEstandarizada, '')) LIKE ?)"
            )
            parametros.extend(["%" + prefijo + "%"] * 2)

        if not condiciones:
            return []

        filas = conn.execute(
            """
            SELECT id, descripcion, descripcionEstandarizada
            FROM consolidado
            WHERE idInventarioUnico IS NULL
              AND UPPER(COALESCE(estadoConsolidado, '')) NOT IN ('INVENTARIADO')
              AND (%s)
            """ % " OR ".join(condiciones),
            parametros,
        )
    except sqlite3.Error:
        return []

    encontrados = []
    for id_origen, descripcion, estandarizada in filas:
        texto = normalizacion.normalizar(
            "%s %s" % (descripcion or "", estandarizada or "")
        )
        palabras = texto.split(" ")
        mejor = 0.0
        for termino in terminos:
            for palabra in palabras:
                if abs(len(palabra) - len(termino)) > 3:
                    continue
                proporcion = difflib.SequenceMatcher(
                    None, termino, palabra
                ).ratio()
                if proporcion > mejor:
                    mejor = proporcion
            if mejor >= 0.86:
                break
        if mejor >= 0.82:
            encontrados.append((mejor, id_origen))

    encontrados.sort(reverse=True)
    return [id_origen for _, id_origen in encontrados[:limite]]


# ---------------------------------------------------------------------------
# Lectura de artículos
# ---------------------------------------------------------------------------
def _filas_inventario(conn):
    return conn.execute(
        """
        SELECT iu.id, iu.codigoBarras, iu.referencia, iu.descripcion,
               iu.descripcionClave, iu.cantidad, iu.precioVenta,
               iu.precioVentaCifrado, iu.grupo, iu.proveedor, iu.costo,
               iu.ubicacion, iu.estante, iu.estadoEtiquetado,
               iu.estadoConteo, iu.idUbicacion,
               u.piso, u.pasillo, u.numero, u.codigoUbicacion
        FROM inventarioUnico iu
        LEFT JOIN Ubicacion u ON u.idUbicacion = iu.idUbicacion
        ORDER BY iu.id
        """
    ).fetchall()


def _compatibilidad_inventario(conn):
    mapa = {}
    try:
        filas = conn.execute(
            """
            SELECT idInventarioUnico, descripcionEstandarizada, descripcion,
                   referenciaRepuesto, equivalente, marca, carro, modelo,
                   tipoMotor, lado, ladoAbreviado, medida, otros
            FROM consolidado
            WHERE idInventarioUnico IS NOT NULL
            ORDER BY id
            """
        )
    except sqlite3.Error:
        return mapa
    for fila in filas:
        mapa.setdefault(fila[0], []).append(fila[1:])
    return mapa


def _texto_fila_inventario(fila, vinculados):
    partes = [
        fila[1], fila[2], fila[3], fila[4], fila[8], fila[9],
    ]
    for vinculo in vinculados:
        partes.extend(vinculo)
    return normalizacion.normalizar(" ".join(str(p or "") for p in partes))


def _texto_fila_consolidado(fila):
    return normalizacion.normalizar(
        " ".join(str(p or "") for p in fila[1:18])
    )


def _referencias_inventario(fila, vinculados):
    valores = [fila[2]]
    for vinculo in vinculados:
        # vinculo: (descEstandarizada, descripcion, refRepuesto,
        #           equivalente, marca, carro, modelo, tipoMotor, lado,
        #           ladoAbreviado, medida, otros)
        valores.extend([vinculo[2], vinculo[3]])
    return _referencias(valores)


def _referencias_consolidado(fila):
    return _referencias([fila[1], fila[2], fila[3]])


def _compatibilidad_texto(vinculo):
    (
        _desc_estandarizada, _descripcion, _ref_repuesto, _equivalente,
        marca, carro, modelo, tipo_motor, lado, lado_abreviado, medida, _otros,
    ) = vinculo

    partes = []
    for valor in (marca, carro, modelo, tipo_motor):
        if valor:
            partes.append(str(valor).strip())
    lado = lado or lado_abreviado
    if lado:
        partes.append("Lado %s" % str(lado).strip())
    if medida:
        partes.append(str(medida).strip())

    # Evitar repetir exactamente el mismo texto (p. ej. marca = carro).
    salida = []
    for parte in partes:
        if parte.upper() not in [s.upper() for s in salida]:
            salida.append(parte)
    return " · ".join(salida)


def _compatibilidad_consolidado(fila):
    partes = []
    for valor in (fila[6], fila[7], fila[8], fila[10], fila[11] or fila[12],
                  fila[15]):
        if valor:
            texto = str(valor).strip()
            if texto.upper() not in [p.upper() for p in partes]:
                partes.append(texto)
    return " · ".join(partes)


# ---------------------------------------------------------------------------
# Puntuación
# ---------------------------------------------------------------------------
def _coincide_grupo(grupo, texto_relleno, tokens_item):
    for variante in grupo:
        if f" {variante} " in texto_relleno:
            return True, False, 1.0

    mejor = 0.0
    for variante in grupo:
        if len(variante) < 4:
            continue
        for palabra in tokens_item:
            if abs(len(palabra) - len(variante)) > 3:
                continue
            proporcion = difflib.SequenceMatcher(
                None, variante, palabra
            ).ratio()
            if proporcion > mejor:
                mejor = proporcion
    if mejor >= 0.82:
        return True, True, mejor
    return False, False, mejor


def _evaluar_criterios(texto_normalizado, dimensiones, criterios):
    """Devuelve (coincidentes, contradictorios, pendientes)."""

    relleno = f" {texto_normalizado} "
    coincidentes = []
    contradictorios = []
    faltantes = []

    for campo in lexico.ORDEN_DIMENSIONES:
        if campo not in criterios:
            continue
        valor = normalizacion.normalizar(criterios[campo])
        if not valor:
            continue
        valores_articulo = dimensiones.get(campo, set())

        if valor in valores_articulo:
            coincidentes.append(campo)
        elif valores_articulo:
            # El artículo declara otro valor para la misma dimensión.
            contradictorios.append(campo)
        elif f" {valor} " in relleno:
            coincidentes.append(campo)
        else:
            faltantes.append(campo)

    return coincidentes, contradictorios, faltantes


def _campos_pendientes(dimensiones, criterios):
    pendientes = []
    for campo in ("marca", "modelo", "motor", "anio", "medida"):
        if campo in criterios:
            continue
        if not dimensiones.get(campo):
            pendientes.append(campo)
    return pendientes


ETIQUETAS_CAMPO = {
    "lado": "Lado",
    "posicion": "Posición",
    "cabina": "Cabina",
    "traccion": "Tracción",
    "transmision": "Transmisión",
    "combustible": "Combustible",
    "motor": "Motor",
    "anio": "Año",
    "medida": "Medida",
    "marca": "Marca",
    "modelo": "Modelo",
}


def _pendientes_texto(campos):
    return [ETIQUETAS_CAMPO.get(campo, campo.title()) for campo in campos[:4]]


def _explicacion(interpretacion, coincidentes, criterios_coincidentes,
                 exacto_barras=False, exacto_referencia=False):
    if exacto_barras:
        return "Coincide exactamente con el código de barras consultado."
    if exacto_referencia:
        return (
            "Coincide exactamente con la referencia o equivalente consultado."
        )

    partes = [
        "Aparece porque «%s» se interpretó como %s"
        % (interpretacion.get("original", ""), interpretacion.get("repuesto", ""))
    ]
    if coincidentes:
        partes.append(
            "y coincide con: %s" % ", ".join(coincidentes[:5])
        )
    if criterios_coincidentes:
        criterios = interpretacion.get("criterios") or {}
        partes.append(
            "También coincide en %s"
            % ", ".join(
                "%s (%s)" % (
                    ETIQUETAS_CAMPO.get(campo, campo),
                    criterios.get(campo, ""),
                )
                for campo in criterios_coincidentes
            )
        )
    return ". ".join(parte.strip() for parte in partes if parte).strip() + "."


def _nivel(puntaje):
    if puntaje >= 0.72:
        return "alta"
    if puntaje >= 0.45:
        return "media"
    return "baja"


def _puntuar(interpretacion, texto, dimensiones, criterios):
    grupos = interpretacion.get("grupos") or []
    principales = interpretacion.get("principales") or []
    canonicos = interpretacion.get("canonicos") or []
    tokens_item = texto.split(" ")
    relleno = f" {texto} "

    coincidentes = []
    aciertos = 0.0
    difusos = 0
    hubo = 0
    coincidencias_grupo = []

    for indice, grupo in enumerate(grupos):
        coincide, difuso, _ = _coincide_grupo(grupo, relleno, tokens_item)
        coincidencias_grupo.append(coincide)
        if not coincide:
            continue

        hubo += 1
        primario = principales[indice] if indice < len(principales) else []
        canonico = canonicos[indice] if indice < len(canonicos) else []

        directo_primario, difuso_primario, _ = _coincide_grupo(
            primario, relleno, tokens_item
        )
        directo_canonico, difuso_canonico, _ = _coincide_grupo(
            canonico, relleno, tokens_item
        )

        if directo_primario and not difuso_primario:
            peso = 1.0
        elif directo_canonico and not difuso_canonico:
            peso = 0.85
        elif difuso_primario:
            peso = 0.90
            difusos += 1
        elif difuso_canonico:
            peso = 0.60
            difusos += 1
        else:
            peso = 0.50

        if difuso and not (difuso_primario or difuso_canonico):
            difusos += 1

        aciertos += peso
        if peso >= 0.60:
            coincidentes.append(sorted(grupo, key=len)[0])

    if not grupos or hubo == 0:
        return 0.0, coincidentes, [], ([], [])

    indices_repuesto = interpretacion.get("indicesRepuesto") or []
    if indices_repuesto and not all(
        coincidencias_grupo[indice]
        for indice in indices_repuesto
        if indice < len(coincidencias_grupo)
    ):
        return 0.0, coincidentes, [], ([], [])

    cobertura = aciertos / len(grupos)
    precision = hubo / max(1, len(tokens_item))
    puntaje = 0.70 * cobertura + 0.10 * precision + 0.12
    puntaje -= 0.03 * difusos

    criterios_coincidentes, contradictorios, faltantes = _evaluar_criterios(
        texto, dimensiones, criterios
    )
    puntaje += 0.03 * len(criterios_coincidentes)
    puntaje -= 0.04 * len(faltantes)

    return min(puntaje, 0.95), coincidentes, criterios_coincidentes, (
        contradictorios,
        faltantes,
    )


def _resultado_inventario(fila, vinculados, interpretacion, criterios,
                          puntaje, nivel, coincidentes,
                          criterios_coincidentes, pendientes, exacto_barras,
                          exacto_referencia):
    dimensiones = normalizacion.detectar_dimensiones(
        _texto_fila_inventario(fila, vinculados)
    )
    referencias = sorted(
        referencia
        for referencia in _referencias_inventario(fila, vinculados)
        if referencia
    )
    equivalente = ""
    compatibilidad = ""
    if vinculados:
        equivalente = str(vinculados[0][3] or "").strip()
        compatibilidad = _compatibilidad_texto(vinculados[0])

    cantidad = fila[5] or 0
    disponible = cantidad > 0

    ubicacion = fila[11] or ""
    if not ubicacion and fila[16] is not None:
        ubicacion = "Piso %s · Pasillo %s · Número %s" % (
            fila[16], fila[17], fila[18]
        )
    if fila[19] and not ubicacion:
        ubicacion = str(fila[19])

    return {
        "tipo": "INVENTARIO",
        "id": fila[0],
        "nivel": nivel,
        "puntaje": round(puntaje, 3),
        "nombreInterpretado": interpretacion.get("repuesto", ""),
        "descripcion": fila[3] or fila[4] or "",
        "codigoBarras": fila[1] or "",
        "referencia": fila[2] or "",
        "referencias": referencias,
        "equivalente": equivalente,
        "compatibilidad": compatibilidad or "No registrada",
        "cantidad": cantidad,
        "precio": fila[6] or 0,
        "precioTexto": _formatear_precio(fila[6]),
        "grupo": fila[8] or "",
        "proveedor": fila[9] or "",
        "ubicacion": ubicacion,
        "estante": fila[12] or "",
        "estado": (
            "Disponible en Inventario" if disponible
            else "Sin stock en Inventario"
        ),
        "disponible": disponible,
        "explicacion": _explicacion(
            interpretacion, coincidentes, criterios_coincidentes,
            exacto_barras=exacto_barras,
            exacto_referencia=exacto_referencia,
        ),
        "coincidentes": coincidentes + [
            "%s: %s" % (
                ETIQUETAS_CAMPO.get(campo, campo),
                criterios.get(campo, ""),
            )
            for campo in criterios_coincidentes
        ],
        "pendientes": _pendientes_texto(pendientes),
        "dimensiones": dimensiones,
    }


def _resultado_consolidado(fila, interpretacion, criterios, puntaje, nivel,
                           coincidentes, criterios_coincidentes, pendientes,
                           exacto_referencia):
    texto = _texto_fila_consolidado(fila)
    dimensiones = normalizacion.detectar_dimensiones(texto)
    referencias = sorted(
        referencia for referencia in _referencias_consolidado(fila)
        if referencia
    )

    return {
        "tipo": "CONSOLIDADO",
        "id": fila[0],
        "nivel": nivel,
        "puntaje": round(puntaje, 3),
        "nombreInterpretado": interpretacion.get("repuesto", ""),
        "descripcion": fila[5] or fila[4] or "",
        "codigoBarras": "",
        "referencia": fila[2] or fila[1] or "",
        "referencias": referencias,
        "equivalente": str(fila[3] or "").strip(),
        "compatibilidad": _compatibilidad_consolidado(fila) or "No registrada",
        "proveedor": fila[17] or "",
        "estado": fila[18] or "PENDIENTE_REVISION",
        "mensaje": (
            "Todavía no está disponible en Inventario. No se puede vender "
            "como existencia del local."
        ),
        "disponible": False,
        "explicacion": _explicacion(
            interpretacion, coincidentes, criterios_coincidentes,
            exacto_referencia=exacto_referencia,
        ),
        "coincidentes": coincidentes + [
            "%s: %s" % (
                ETIQUETAS_CAMPO.get(campo, campo),
                criterios.get(campo, ""),
            )
            for campo in criterios_coincidentes
        ],
        "pendientes": _pendientes_texto(pendientes),
        "dimensiones": dimensiones,
    }


# ---------------------------------------------------------------------------
# Búsqueda principal
# ---------------------------------------------------------------------------
def buscar(conn, consulta, criterios_extra=None, limite=MAXIMO_RESULTADOS):
    """Ejecuta una búsqueda y devuelve Inventario y Consolidado separados."""

    sinonimos = conocimiento.cargar_sinonimos_aprobados(conn)
    interpretacion = normalizacion.interpretar(consulta, sinonimos)
    normalizacion.combinar_criterios(interpretacion, criterios_extra)
    criterios = interpretacion.get("criterios") or {}

    usar_fts = asegurar_indice(conn)

    inventario = _filas_inventario(conn)
    vinculados = _compatibilidad_inventario(conn)

    consulta_norm = interpretacion.get("normalizada", "")
    original_consulta = str(interpretacion.get("original", "")).strip()

    texto_referencia = original_consulta
    coincidencia_ref = re.match(
        r"^\s*(REF(?:ERENCIA)?|COD(?:IGO)?|PARTE|NUM(?:ERO)?)\s+(.+)$",
        original_consulta,
        re.IGNORECASE,
    )
    if coincidencia_ref:
        texto_referencia = coincidencia_ref.group(2).strip()

    clave_ref = _clave_referencia(texto_referencia)
    buscar_ref = (
        bool(clave_ref)
        and len(clave_ref) >= 3
        and (
            any(caracter.isdigit() for caracter in clave_ref)
            or len(clave_ref) <= 12
        )
        and (" " not in texto_referencia or " " not in consulta_norm)
    )
    if not buscar_ref:
        clave_ref = ""

    ids_consolidado = []
    ids_fts = []
    if usar_fts:
        ids_fts = _ids_fts(
            conn, _consulta_fts(interpretacion), "CONSOLIDADO"
        )
        ids_consolidado.extend(ids_fts)
    if len(set(ids_fts)) < 20:
        ids_consolidado.extend(_ids_like_consolidado(conn, interpretacion))
    if clave_ref:
        ids_consolidado.extend(
            _ids_referencia_exacta(
                conn, clave_ref, texto_referencia
            )
        )

    # Si casi no hay candidatos, tolerar errores de escritura.
    if len(set(ids_consolidado)) < 3:
        ids_consolidado.extend(
            _ids_similitud_consolidado(conn, interpretacion)
        )

    resultados_inventario = []
    resultados_consolidado = []
    referencias_inventario = set()

    for fila in inventario:
        vinculos = vinculados.get(fila[0], [])
        texto = _texto_fila_inventario(fila, vinculos)
        dimensiones = normalizacion.detectar_dimensiones(texto)
        referencias = _referencias_inventario(fila, vinculos)
        referencias_inventario |= referencias

        exacto_barras = False
        codigo = str(fila[1] or "")
        if (
            consulta_norm.isdigit()
            and codigo.lstrip("0") == consulta_norm.lstrip("0")
            and consulta_norm
        ):
            exacto_barras = True

        exacto_referencia = bool(
            clave_ref and clave_ref in referencias
        )

        if exacto_barras or exacto_referencia:
            puntaje = 1.0
            coincidentes = []
            criterios_coincidentes = []
            contradictorios = []
            faltantes = []
        else:
            puntaje, coincidentes, criterios_coincidentes, estados = _puntuar(
                interpretacion, texto, dimensiones, criterios
            )
            contradictorios, faltantes = estados
            if contradictorios:
                continue

        if puntaje < MINIMO_PUNTAJE:
            continue

        nivel = "alta" if (exacto_barras or exacto_referencia) else _nivel(puntaje)
        pendientes = faltantes + _campos_pendientes(dimensiones, criterios)
        resultados_inventario.append(
            _resultado_inventario(
                fila, vinculos, interpretacion, criterios, puntaje, nivel,
                coincidentes, criterios_coincidentes, pendientes,
                exacto_barras, exacto_referencia,
            )
        )

    for fila in _filas_consolidado(conn, ids_consolidado):
        referencias = _referencias_consolidado(fila)
        texto = _texto_fila_consolidado(fila)
        dimensiones = normalizacion.detectar_dimensiones(texto)

        exacto_referencia = bool(clave_ref and clave_ref in referencias)
        if exacto_referencia:
            puntaje = 1.0
            coincidentes = []
            criterios_coincidentes = []
            contradictorios = []
            faltantes = []
        else:
            puntaje, coincidentes, criterios_coincidentes, estados = _puntuar(
                interpretacion, texto, dimensiones, criterios
            )
            contradictorios, faltantes = estados
            if contradictorios:
                continue

        if puntaje < MINIMO_PUNTAJE:
            continue

        # No duplicar en Consolidado algo que ya está en Inventario.
        if referencias & referencias_inventario:
            continue

        nivel = "alta" if exacto_referencia else _nivel(puntaje)
        pendientes = faltantes + _campos_pendientes(dimensiones, criterios)
        resultados_consolidado.append(
            _resultado_consolidado(
                fila, interpretacion, criterios, puntaje, nivel,
                coincidentes, criterios_coincidentes, pendientes,
                exacto_referencia,
            )
        )

    resultados_inventario.sort(
        key=lambda item: (-item["puntaje"], item["descripcion"])
    )
    resultados_consolidado.sort(
        key=lambda item: (-item["puntaje"], item["descripcion"])
    )

    # Evitar duplicados visuales dentro de Consolidado.
    vistos = set()
    consolidado_unico = []
    for item in resultados_consolidado:
        clave = (
            normalizacion.normalizar(item.get("descripcion"))
            or _clave_referencia(item.get("referencia"))
        )
        if clave in vistos:
            continue
        vistos.add(clave)
        consolidado_unico.append(item)

    inventario_mostrado = resultados_inventario[:limite]
    consolidado_mostrado = consolidado_unico[:limite]

    exactos = [
        item for item in inventario_mostrado
        if item["puntaje"] >= 0.999
    ] + [
        item for item in consolidado_mostrado
        if item["puntaje"] >= 0.999
    ]

    pregunta = None
    if len(exactos) != 1:
        candidatos_pregunta = inventario_mostrado + consolidado_mostrado
        pregunta = preguntas.elegir(candidatos_pregunta, criterios)

    return {
        "interpretacion": {
            "consulta": interpretacion.get("original", ""),
            "repuesto": interpretacion.get("repuesto", ""),
            "criterios": criterios,
            "tokens": interpretacion.get("terminos", []),
        },
        "inventario": inventario_mostrado,
        "consolidado": consolidado_mostrado,
        "pregunta": pregunta,
        "sinResultados": not inventario_mostrado and not consolidado_mostrado,
        "totalInventario": len(resultados_inventario),
        "totalConsolidado": len(consolidado_unico),
        "indice": "fts5" if usar_fts else "respaldo",
    }

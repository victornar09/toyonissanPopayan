import pandas as pd
import re
import unicodedata


MEDIDAS_VALIDAS = [
    "STD",
    "010",
    "025",
    "050",
    "075",
    "100",
    "125",
    "150"
]

# ==========================
# LEER ESTANDARIZACIÓN
# ==========================

def normalizar_valor(valor):

    if valor is None:
        return None

    if pd.isna(valor):
        return None

    if isinstance(valor, str):

        valor = valor.strip()

        if not valor:
            return None

    return valor

def agrupar_duplicados_consolidado(df):

    df = df.copy()

    # ==========================================================
    # VALIDAR COLUMNAS DE LA LLAVE
    # ==========================================================

    columnas_llave = [
        "abrevProveedor",
        "referenciaProveedor"
    ]

    faltantes = [
        columna
        for columna in columnas_llave
        if columna not in df.columns
    ]

    if faltantes:
        raise ValueError(
            "Faltan columnas necesarias para agrupar: "
            + ", ".join(faltantes)
        )

    # ==========================================================
    # NORMALIZAR LLAVE
    # ==========================================================

    df["abrevProveedor"] = (
        df["abrevProveedor"]
        .apply(normalizar_valor)
        .apply(
            lambda x: str(x).strip().upper()
            if x is not None else None
        )
    )

    df["referenciaProveedor"] = (
        df["referenciaProveedor"]
        .apply(normalizar_valor)
        .apply(
            lambda x: str(x).strip().upper()
            if x is not None else None
        )
    )

    # ==========================================================
    # COLUMNAS QUE REALMENTE PUEDEN EXISTIR EN ESTE MOMENTO
    # ==========================================================

    columnas_completar = [
        "descripcion",
        "carro",
        "referenciaRepuesto",
        "equivalente",
        "marca",
        "tipo",
        "tipoMotor",
        "precioListado",
        "proveedor"
    ]

    # Solamente usar columnas que existan
    columnas_completar = [
        columna
        for columna in columnas_completar
        if columna in df.columns
    ]

    # ==========================================================
    # FUNCIÓN PARA SABER SI TIENE VALOR
    # ==========================================================

    def tiene_valor(valor):

        if valor is None:
            return False

        if pd.isna(valor):
            return False

        if isinstance(valor, str):
            return valor.strip() != ""

        return True

    # ==========================================================
    # AGRUPAR
    # ==========================================================

    grupos = []

    for clave, grupo in df.groupby(
        [
            "abrevProveedor",
            "referenciaProveedor"
        ],
        sort=False,
        dropna=False
    ):

        grupo = grupo.copy()

        # ======================================================
        # UN SOLO REGISTRO
        # ======================================================

        if len(grupo) == 1:

            grupos.append(
                grupo.iloc[0].copy()
            )

            continue

        # ======================================================
        # DUPLICADOS
        # ======================================================

        indices = list(grupo.index)

        def puntuacion(indice):

            fila = grupo.loc[indice]

            puntos = 0

            # PRIORIDAD 1: DESCRIPCION
            if (
                "descripcion" in grupo.columns
                and tiene_valor(
                    fila["descripcion"]
                )
            ):
                puntos += 100

            # PRIORIDAD 2: CARRO
            if (
                "carro" in grupo.columns
                and tiene_valor(
                    fila["carro"]
                )
            ):
                puntos += 50

            # PRIORIDAD 3
            if (
                "referenciaRepuesto" in grupo.columns
                and tiene_valor(
                    fila["referenciaRepuesto"]
                )
            ):
                puntos += 20

            # PRIORIDAD 4
            if (
                "equivalente" in grupo.columns
                and tiene_valor(
                    fila["equivalente"]
                )
            ):
                puntos += 10

            return puntos

        indice_principal = max(
            indices,
            key=puntuacion
        )

        principal = grupo.loc[
            indice_principal
        ].copy()

        # ======================================================
        # COMPLETAR CAMPOS VACÍOS
        # ======================================================

        for campo in columnas_completar:

            # Si ya tiene información,
            # no la reemplazamos
            if tiene_valor(
                principal.get(campo)
            ):
                continue

            # Buscar información en los duplicados
            for indice in indices:

                if indice == indice_principal:
                    continue

                valor = grupo.loc[
                    indice,
                    campo
                ]

                if tiene_valor(valor):

                    principal[campo] = valor

                    break

        grupos.append(principal)

    # ==========================================================
    # CREAR DATAFRAME FINAL
    # ==========================================================

    df_final = pd.DataFrame(
        grupos
    ).reset_index(drop=True)

    return df_final

def cargar_diccionarios(archivo):

    hojas = pd.read_excel(
        archivo,
        sheet_name=None
    )

    diccionarios = {}

    for nombre_hoja, df in hojas.items():


        # ==================================================
        # TABLAS CON:
        # original | estandarizado
        # ==================================================

        if (
            "original" in df.columns
            and "estandarizado" in df.columns
            and "abreviado" not in df.columns
        ):

            diccionario = {}

            for _, fila in df.iterrows():

                original = fila["original"]
                estandarizado = fila["estandarizado"]

                if pd.isna(original) or pd.isna(estandarizado):
                    continue

                original = str(original).strip().upper()
                estandarizado = str(estandarizado).strip().upper()

                if original:
                    diccionario[original] = estandarizado

            diccionarios[nombre_hoja] = diccionario

            continue

        # ==================================================
        # TABLA LADOS_ORDEN
        # ==================================================

        if (
            "estandarizado" in df.columns
            and "abreviado" in df.columns
            and "orden" in df.columns
        ):

            diccionario = {}

            for _, fila in df.iterrows():

                estandarizado = fila["estandarizado"]
                abreviado = fila["abreviado"]
                orden = fila["orden"]

                if (
                    pd.isna(estandarizado)
                    or pd.isna(abreviado)
                    or pd.isna(orden)
                ):
                    continue

                estandarizado = str(
                    estandarizado
                ).strip().upper()

                abreviado = str(
                    abreviado
                ).strip().upper()

                diccionario[estandarizado] = {
                    "abreviado": abreviado,
                    "orden": int(orden)
                }

            diccionarios[nombre_hoja] = diccionario

    return diccionarios

def normalizar_texto(texto):
    """
    Normaliza un texto para facilitar las comparaciones.

    Ejemplos:
        'Bomba Ppal. Clutch'     -> 'BOMBA PPAL CLUTCH'
        'BOMBA  PPAL - CLUTCH'   -> 'BOMBA PPAL CLUTCH'
        'BóMBA PáL CLUTCH'       -> 'BOMBA PAL CLUTCH'
    """

    if pd.isna(texto):
        return ""

    texto = str(texto).upper().strip()

    # Quitar tildes
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )

    # Reemplazar caracteres especiales por espacios
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)

    # Quitar espacios repetidos
    texto = re.sub(r"\s+", " ", texto).strip()

    return texto

def estandarizar_repuesto(repuesto, patrones):

    if pd.isna(repuesto):
        return ""

    texto = str(repuesto).strip()

    if not texto:
        return ""

    for patron, reemplazo in patrones:

        texto = patron.sub(
            reemplazo,
            texto
        )

    return texto

def extraer_lado(descripcion, LADOS):

    if pd.isna(descripcion):
        return ""

    texto = str(descripcion).upper()

    texto = re.sub(
        r'"[^"]*"',
        '',
        texto
    )

    texto = re.sub(
        r'[^A-Z0-9&]',
        ' ',
        texto
    )

    texto = re.sub(
        r'\s+',
        ' ',
        texto
    ).strip()

    palabras = texto.split()

    encontrados = []

    for palabra in palabras:

        if palabra in LADOS:

            lado = LADOS[palabra]

            if lado not in encontrados:
                encontrados.append(lado)

    return " ".join(encontrados)

def abreviar_lado(lado, LADOS_ORDEN):

    if pd.isna(lado):
        return ""

    lado = str(lado).strip().upper()

    if not lado:
        return ""

    palabras = lado.split()

    encontrados = []

    for palabra in palabras:

        if palabra not in LADOS_ORDEN:
            continue

        info = LADOS_ORDEN[palabra]

        encontrados.append(
            (
                info["orden"],
                info["abreviado"]
            )
        )

    # Ordenar por el campo orden
    encontrados.sort(
        key=lambda x: x[0]
    )

    # Evitar duplicados
    resultado = []
    vistos = set()

    for orden, abreviado in encontrados:

        if abreviado not in vistos:

            resultado.append(abreviado)
            vistos.add(abreviado)

    return " ".join(resultado)

def extraer_repuesto(descripcion, ABREVIATURAS, LADOS):

    if pd.isna(descripcion):
        return ""

    texto = str(descripcion).upper()

    # Eliminar texto entre comillas
    texto = re.sub(r'"[^"]*"', '', texto)

    # Eliminar texto entre paréntesis
    texto = re.sub(r'\([^)]*\)', '', texto)

    texto = re.sub(r'[^A-ZÑ0-9&]', ' ', texto)

    texto = re.sub(r'\s+', ' ', texto).strip()

    palabras = texto.split()

    repuesto = []

    for palabra in palabras:

        if palabra in ABREVIATURAS:
            break

        if palabra in LADOS:
            continue

        repuesto.append(palabra)

    return " ".join(repuesto).strip()

def detectar_marca(descripcion,ABREVIATURAS):

    if pd.isna(descripcion):
        return ""

    descripcion = str(descripcion).upper()

    # Extrae únicamente palabras y números
    palabras = re.findall(r'[A-Z0-9]+', descripcion)

    for palabra in palabras:

        if palabra in ABREVIATURAS:
            return ABREVIATURAS[palabra]

    return ""

def extraer_modelo(descripcion, ABREVIATURAS, LADOS):

    if pd.isna(descripcion):
        return ""

    texto = str(descripcion).upper()

    # Eliminar texto entre comillas y paréntesis
    texto = re.sub(r'"[^"]*"', ' ', texto)
    texto = re.sub(r'\([^)]*\)', ' ', texto)

    # Dejar solo letras, números y &
    texto = re.sub(r'[^A-Z0-9&]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()

    palabras = texto.split()

    resultado = []

    antes_de_marca = True

    for palabra in palabras:

        # Todo antes de la marca corresponde al repuesto
        if antes_de_marca:
            if palabra in ABREVIATURAS:
                antes_de_marca = False
            continue

        # Quitar la marca
        if palabra in ABREVIATURAS:
            continue

        # Quitar el lado
        if palabra in LADOS:
            continue

        resultado.append(palabra)

    return " ".join(resultado)

def extraer_otros(descripcion):

    if pd.isna(descripcion):
        return ""

    texto = str(descripcion).upper()

    # Extrae todo lo que está entre paréntesis
    encontrados = re.findall(r'\((.*?)\)', texto)

    # Limpia espacios y elimina vacíos
    encontrados = [x.strip() for x in encontrados if x.strip()]

    return " ".join(encontrados)

def extraer_medida(texto):

    if pd.isna(texto):
        return ""

    texto = str(texto).upper().strip()

    coincidencias = re.findall(
        r'[-/]([A-Z0-9]+)',
        texto
    )

    for valor in reversed(coincidencias):

        if valor in MEDIDAS_VALIDAS:
            return valor

    return ""

def reemplazar_descripcion_excel(df, ajustes):

    # Validar que exista la columna
    if "descripcion" not in df.columns:
        raise ValueError(
            "El archivo no contiene una columna llamada 'descripcion'."
        )

    # Ordenar por longitud: primero términos más específicos
    ajustes_ordenados = sorted(
        ajustes.items(),
        key=lambda x: len(str(x[0]).strip()),
        reverse=True
    )

    # Crear patrones individuales
    patrones = []

    for palabra, reemplazo in ajustes_ordenados:

        palabra = str(palabra).strip()

        # Escapar caracteres especiales de regex
        palabra_regex = re.escape(palabra)

        # Permitir uno o varios espacios entre palabras
        palabra_regex = palabra_regex.replace(r"\ ", r"\s+")

        patrones.append(
            (re.compile(
                r"(?<!\w)" + palabra_regex + r"(?!\w)",
                flags=re.IGNORECASE
            ), reemplazo)
        )

    def reemplazar(texto):

        if pd.isna(texto):
            return texto

        texto = str(texto)

        # Aplicar cada reemplazo
        for patron, reemplazo in patrones:
            texto = patron.sub(reemplazo, texto)

        return texto

    # Aplicar únicamente a descripcion
    df["descripcion"] = df["descripcion"].apply(reemplazar)

    return df

def concatenar_descripcion(fila):

    campos = [
        fila.get("repuesto", ""),
        fila.get("medida", ""),
        fila.get("lado", ""),
        fila.get("carro", ""),
        fila.get("modelo", ""),
        fila.get("otros", "")
    ]

    valores = []

    for valor in campos:

        if pd.isna(valor):
            continue

        valor = str(valor).strip()

        if valor:
            valores.append(valor)

    return " ".join(valores)

def procesar_consolidado(
    archivo_consolidado,
    archivo_estandarizacion
):

    # --------------------------------
    # Cargar diccionarios
    # --------------------------------

    diccionarios = cargar_diccionarios(
        archivo_estandarizacion
    )

    ESTANDARIZACION = diccionarios["ESTANDARIZACION"]
    LADOS = diccionarios["LADOS"]
    LADOS_ORDEN = diccionarios["ABRELADO"]
    AJUSTES = diccionarios["AJUSTES"]
    ABREVIATURAS = diccionarios["ABREVIATURAS"]

    # --------------------------------
    # Preparar reglas
    # --------------------------------

    estandarizacion_reglas = sorted(
        ESTANDARIZACION.items(),
        key=lambda x: len(str(x[0]).strip()),
        reverse=True
    )

    patrones = []

    for original, estandarizado in estandarizacion_reglas:

        original = str(original).strip()

        if not original:
            continue

        patron = re.compile(
            r"(?<!\w)"
            + re.escape(original)
            + r"(?!\w)",
            flags=re.IGNORECASE
        )

        patrones.append(
            (
                patron,
                str(estandarizado).strip()
            )
        )

    # --------------------------------
    # Leer consolidado
    # --------------------------------

    df = pd.read_excel(
        archivo_consolidado
    )


    df = agrupar_duplicados_consolidado(df)

    # --------------------------------
    # Aplicar ajustes
    # --------------------------------

    df = reemplazar_descripcion_excel(
        df,
        AJUSTES
    )

    # --------------------------------
    # Procesar registros
    # --------------------------------

    for i, fila in df.iterrows():

        # CARRO
        carro = fila.get("carro", "")

        if (
            pd.isna(carro)
            or str(carro).strip() == ""
        ):

            marca = detectar_marca(
                fila["descripcion"],
                ABREVIATURAS
            )

            if marca:
                df.at[i, "carro"] = marca

        # LADO
        lado = extraer_lado(
            fila["descripcion"],
            LADOS
        )

        df.at[i, "lado"] = lado

        # LADO ABREVIADO
        df.at[i, "ladoAbreviado"] = abreviar_lado(
            lado,
            LADOS_ORDEN
        )

        # REPUESTO
        repuesto = extraer_repuesto(
            fila["descripcion"],
            ABREVIATURAS,
            LADOS
        )

        repuesto = estandarizar_repuesto(
            repuesto,
            patrones
        )

        df.at[i, "repuesto"] = repuesto

        # MODELO
        df.at[i, "modelo"] = extraer_modelo(
            fila["descripcion"],
            ABREVIATURAS,
            LADOS
        )

        # OTROS
        df.at[i, "otros"] = extraer_otros(
            fila["descripcion"]
        )

        # MEDIDA
        medida = extraer_medida(
            fila.get("referenciaRepuesto")
        )

        if not medida:
            medida = extraer_medida(
                fila.get("equivalente")
            )

        df.at[i, "medida"] = medida

    # --------------------------------
    # DESCRIPCIÓN ESTANDARIZADA
    # --------------------------------

    df["descripcionEstandarizada"] = df.apply(
        concatenar_descripcion,
        axis=1
    )

    print(df.head(10).to_string())

    return df
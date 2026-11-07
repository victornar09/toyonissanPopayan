"""Lexicón técnico de ION para repuestos automotrices.

Contiene el conocimiento base incorporado (sinónimos, nombres populares,
abreviaturas, marcas, modelos y dimensiones técnicas). Es deliberadamente
conservador: sólo agrupa términos que significan lo mismo o que el negocio
usa de forma intercambiable. Los sinónimos que sugieran los vendedores se
guardan en la base de datos y requieren aprobación antes de usarse.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Palabras vacías / conectores que no aportan a la búsqueda
# ---------------------------------------------------------------------------
STOPWORDS = {
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "UN", "UNA", "UNOS", "UNAS",
    "PARA", "POR", "CON", "EN", "Y", "O", "AL", "A", "QUE", "SE", "SU",
    "MI", "ME", "ES", "SON", "COMO", "MAS", "MUY", "FAVOR", "NECESITO",
    "BUSCO", "QUIERO", "TENGO", "TIENE", "HAY", "SERIA", "SERA",
    "JUEGO", "JUEGOS", "KIT", "KITS", "SET", "SETS", "PAR", "PARES",
    "PIEZA", "PIEZAS", "REPUESTO", "REPUESTOS", "ARTICULO", "ARTICULOS",
    "MODELO", "MARCA", "VEHICULO", "CARRO", "AUTO", "CAMIONETA",
}

# Verbos/frases que sólo indican intención y no describen el repuesto.
FRASES_INTENCION = (
    "EL QUE VA EN",
    "LA QUE VA EN",
    "LOS QUE VAN EN",
    "LAS QUE VAN EN",
    "EL QUE LLEVA",
    "LA QUE LLEVA",
    "NECESITO EL",
    "NECESITO LA",
    "NECESITO UN",
    "NECESITO UNA",
    "BUSCO EL",
    "BUSCO LA",
    "BUSCO UN",
    "BUSCO UNA",
    "QUIERO EL",
    "QUIERO LA",
    "QUIERO UN",
    "QUIERO UNA",
    "ME SIRVE EL",
    "ME SIRVE LA",
    "TIENE EL",
    "TIENE LA",
    "TIENE UN",
    "TIENE UNA",
    "HAY UN",
    "HAY UNA",
)

# ---------------------------------------------------------------------------
# Frases completas -> término canónico
# Se aplican antes de tokenizar. El orden importa: primero las más largas.
# ---------------------------------------------------------------------------
FRASES = {
    "JUEGO DE ANILLOS": "ANILLOS",
    "JUEGO DE AROS": "ANILLOS",
    "JUEGO ANILLOS": "ANILLOS",
    "JUEGO DE SEGMENTOS": "ANILLOS",
    "ANILLOS DEL MOTOR": "ANILLOS",
    "ANILLOS DE MOTOR": "ANILLOS",
    "JUEGO DE PASTAS": "PASTAS",
    "PASTAS DE FRENO": "PASTAS",
    "PASTILLAS DE FRENO": "PASTAS",
    "JUEGO DE PASTILLAS": "PASTAS",
    "RETEN DEL CARDAN": "RETEN SALIDA CAJA",
    "RETEN DE CARDAN": "RETEN SALIDA CAJA",
    "RETEN CARDAN": "RETEN SALIDA CAJA",
    "RETEN DEL CIGUENAL": "RETEN CIGUENAL",
    "RETEN DE CIGUENAL": "RETEN CIGUENAL",
    "FAROLA DELANTERA": "FAROLA DELANTERO",
    "FAROLA TRASERA": "FAROLA TRASERO",
    "FARO DELANTERO": "FAROLA DELANTERO",
    "FARO TRASERO": "FAROLA TRASERO",
    "LUZ DELANTERA": "FAROLA DELANTERO",
    "LUZ TRASERA": "FAROLA TRASERO",
    "GUARDAPOLVO DE HOMOCINETICA": "GUARDAPOLVO HOMOCINETICA",
    "FUEL INJECTION": "INYECCION",
    "BOMBA DE AGUA": "BOMBA AGUA",
    "BOMBA DE ACEITE": "BOMBA ACEITE",
    "BOMBA DE GASOLINA": "BOMBA GASOLINA",
    "BOMBA DE INYECCION": "BOMBA INYECCION",
    "CAJA DE DIRECCION": "CAJA DIRECCION",
    "TERMINAL DE DIRECCION": "TERMINAL DIRECCION",
    "CAÑA DE DIRECCION": "CAÑA DIRECCION",
    "CAÑA DIRECCION": "CAÑA DIRECCION",
    "CORREA DE REPARTICION": "CORREA REPARTICION",
    "CORREA DE ACCESORIOS": "CORREA ACCESORIOS",
    "CORREA DE ALTERNADOR": "CORREA ALTERNADOR",
    "TENSOR DE CORREA": "TENSOR CORREA",
    "DISCO DE CLUTCH": "DISCO CLUTCH",
    "DISCO DE EMBRAGUE": "DISCO CLUTCH",
    "PRENSA DE CLUTCH": "PRENSA CLUTCH",
    "PRENSA DE EMBRAGUE": "PRENSA CLUTCH",
    "COLLARIN DE CLUTCH": "COLLARIN CLUTCH",
    "COLLARIN DE EMBRAGUE": "COLLARIN CLUTCH",
    "RODILLO CONICO": "RODILLO CONICO",
    "RODAMIENTO CONICO": "RODILLO CONICO",
    "RODAMIENTO DE RUEDA": "RODAMIENTO RUEDA",
    "MASA DE RUEDA": "MASA RUEDA",
    "BUJE DE BARRA": "BUJE BARRA",
    "BUJES DE BARRA": "BUJE BARRA",
    "AMORTIGUADOR DELANTERO": "AMORTIGUADOR DELANTERO",
    "AMORTIGUADOR TRASERO": "AMORTIGUADOR TRASERO",
    "FILTRO DE ACEITE": "FILTRO ACEITE",
    "FILTRO DE AIRE": "FILTRO AIRE",
    "FILTRO DE COMBUSTIBLE": "FILTRO COMBUSTIBLE",
    "FILTRO DE GASOLINA": "FILTRO GASOLINA",
    "FILTRO DE DIESEL": "FILTRO DIESEL",
    "BATERIA SECA": "BATERIA SECA",
    "SENSOR DE CIGUENAL": "SENSOR CIGUENAL",
    "SENSOR DE ARBOL": "SENSOR ARBOL",
    "SENSOR MAP": "SENSOR MAP",
    "SENSOR MAF": "SENSOR MAF",
}

# ---------------------------------------------------------------------------
# Grupos de sinónimos: primer elemento = término canónico.
# Todos los términos del grupo se consideran equivalentes para la búsqueda.
# ---------------------------------------------------------------------------
GRUPOS_SINONIMOS = (
    ("ANILLOS", ("ANILLO", "SEGMENTOS", "SEGMENTO",
                  "ANILLOS MOTOR", "ANILLO MOTOR")),
    ("PASTAS", ("PASTA", "PASTILLAS", "PASTILLA", "BALATAS", "BALATA",
                 "PASTAS FRENO", "PASTILLAS FRENO")),
    ("RETEN", ("RETENES", "RETENEDOR", "RETENEDORES", "SELLO", "SELLOS",
                "EMPAQUE", "EMPAQUES")),
    ("FAROLA", ("FAROLAS", "FARO", "FAROS", "FAROL", "FAROLES",
                 "FAROLA DELANTERA", "FARO DELANTERO")),
    ("FAROLA", ("FAROLA TRASERA", "FARO TRASERO")),
    ("BUJE", ("BUJES", "BUJES", "BUSHING", "BUSHINGS", "CAUCHO", "CAUCHOS")),
    ("RODAMIENTO", ("RODAMIENTOS", "RODILLO", "RODILLOS", "ROL", "ROLES",
                     "BALINERA", "BALINERAS")),
    ("RODILLO CONICO", ("RODAMIENTO CONICO", "RODILLOS CONICOS",
                         "RODAMIENTOS CONICOS", "CONICO", "CONICOS")),
    ("BOMBA", ("BOMBAS", "BOMBIN", "BOMBINES")),
    ("BOMBA AGUA", ("BOMBA DE AGUA", "BOMBAS AGUA")),
    ("BOMBA ACEITE", ("BOMBA DE ACEITE", "BOMBAS ACEITE")),
    ("BOMBA INYECCION", ("BOMBA DE INYECCION", "BOMBA INYECTORA",
                          "BOMBA INYECTORA DE COMBUSTIBLE")),
    ("BOMBA GASOLINA", ("BOMBA DE GASOLINA", "BOMBA COMBUSTIBLE",
                         "BOMBA DE COMBUSTIBLE")),
    ("CORREA", ("CORREAS", "BANDA", "BANDAS", "FAN BELT")),
    ("CORREA REPARTICION", ("CORREA DE REPARTICION", "CORREA TIMING",
                              "BANDA REPARTICION", "BANDA TIMING")),
    ("CORREA ACCESORIOS", ("CORREA DE ACCESORIOS", "CORREA ALTERNADOR",
                             "CORREA DE ALTERNADOR", "BANDA ACCESORIOS")),
    ("TENSOR", ("TENSORES", "TEMPLADOR", "TEMPLADORES", "TENSOR CORREA")),
    ("CLUTCH", ("EMBRAGUE", "CLOCH", "CLUTCHS", "EMBRAGUES")),
    ("DISCO CLUTCH", ("DISCO DE CLUTCH", "DISCO EMBRAGUE",
                        "DISCO DE EMBRAGUE", "DISCO")),
    ("PRENSA CLUTCH", ("PRENSA DE CLUTCH", "PRENSA EMBRAGUE",
                         "PRENSA DE EMBRAGUE", "PLATO CLUTCH",
                         "PLATO DE CLUTCH", "PLATO EMBRAGUE")),
    ("COLLARIN CLUTCH", ("COLLARIN DE CLUTCH", "COLLARIN EMBRAGUE",
                           "COLLARIN DE EMBRAGUE", "COLLARIN",
                           "DESEMBRAGADOR", "DESEMBRAGADORES")),
    ("AMORTIGUADOR", ("AMORTIGUADORES", "AMORTIGUACION", "SHOCK",
                        "SHOCKS", "SUSPENSION")),
    ("TERMINAL DIRECCION", ("TERMINAL DE DIRECCION", "TERMINALES DIRECCION",
                              "ROTULA DIRECCION", "ROTULAS DIRECCION")),
    ("CAÑA DIRECCION", ("CAÑA DE DIRECCION", "CAÑAS DIRECCION",
                          "BARRA DIRECCION", "BARRA DE DIRECCION")),
    ("CAJA DIRECCION", ("CAJA DE DIRECCION", "CREMALLERA",
                          "CREMALLERAS", "DIRECCION HIDRAULICA")),
    ("MASA RUEDA", ("MASA DE RUEDA", "MASA", "MASAS", "CUBO RUEDA",
                      "CUBO DE RUEDA", "MASA DELANTERA", "MASA TRASERA")),
    ("RODAMIENTO RUEDA", ("RODAMIENTO DE RUEDA", "RODAMIENTOS RUEDA",
                            "RODILLO RUEDA")),
    ("FILTRO ACEITE", ("FILTRO DE ACEITE", "FILTROS ACEITE")),
    ("FILTRO AIRE", ("FILTRO DE AIRE", "FILTROS AIRE")),
    ("FILTRO COMBUSTIBLE", ("FILTRO DE COMBUSTIBLE", "FILTRO GASOLINA",
                              "FILTRO DIESEL", "FILTROS COMBUSTIBLE")),
    ("BUJIA", ("BUJIAS", "BUJIA ENCENDIDO", "SPARK PLUG")),
    ("BUJIAS", ("BUJIA", "BUJIAS ENCENDIDO")),
    ("SENSOR", ("SENSORES", "CAPTADOR", "CAPTADORES")),
    ("SENSOR CIGUENAL", ("SENSOR DE CIGUENAL", "SENSOR CKP",
                           "SENSOR POSICION CIGUENAL")),
    ("SENSOR ARBOL", ("SENSOR DE ARBOL", "SENSOR CMP",
                        "SENSOR ARBOL DE LEVAS", "SENSOR LEVAS")),
    ("VALVULA", ("VALVULAS", "VALVULA MOTOR")),
    ("EMPAQUE", ("EMPAQUES", "JUNTA", "JUNTAS", "SELLO", "SELLOS")),
    ("CULATA", ("CULATAS", "CABEZA MOTOR", "CABEZA DE MOTOR")),
    ("PISTON", ("PISTONES", "PISTON MOTOR")),
    ("CIGUENAL", ("CIGUEÑAL", "CIGUENALES", "CIGUEÑALES", "CRANKSHAFT")),
    ("ARBOL LEVAS", ("ARBOL DE LEVAS", "LEVAS", "ARBOL LEVA")),
    ("INYECTOR", ("INYECTORES", "TOBERA", "TOBERAS", "INJECTOR")),
    ("TURBO", ("TURBOS", "TURBOCOMPRESOR", "TURBOCOMPRESORES")),
    ("RADIADOR", ("RADIADORES", "ENFRIADOR", "ENFRIADORES")),
    ("ALTERNADOR", ("ALTERNADORES", "GENERADOR", "GENERADORES")),
    ("ARRANQUE", ("ARRANQUES", "MOTOR ARRANQUE", "MARCHA", "MARCHAS",
                    "ESTARTER")),
    ("COMPRESOR", ("COMPRESORES", "COMPRESOR AIRE", "AIRE ACONDICIONADO")),
    ("GUARDAPOLVO", ("GUARDAPOLVOS", "POLVO", "FUELLE", "FUELLES")),
    ("HOMOCINETICA", ("HOMOCINETICAS", "HOMOCINETICO", "CARDAN",
                        "CARDANES", "TRANSMISION")),
    ("EJE", ("EJES", "SEMIEJE", "SEMIEJES", "PUNTA EJE")),
    ("DIFERENCIAL", ("DIFERENCIALES", "CORONA", "CORONA Y PIÑON",
                       "PIÑON", "PINON")),
    ("COMPRESOR AIRE", ("COMPRESOR DE AIRE", "BOMBA AIRE")),
    ("ESPEJO", ("ESPEJOS", "RETROVISOR", "RETROVISORES", "LUNA")),
    ("VIDRIO", ("VIDRIOS", "CRISTAL", "CRISTALES", "PANEL")),
    ("PUERTA", ("PUERTAS", "PUERTA DELANTERA", "PUERTA TRASERA")),
    ("CAPO", ("CAPOT", "CAPOS", "CAPOTS")),
    ("GUARDABARRO", ("GUARDABARROS", "GUARDAFANGO", "GUARDAFANGOS")),
    ("PARACHOQUES", ("PARACHOQUE", "BUMPER", "BUMPERS")),
    ("PLUMILLA", ("PLUMILLAS", "LIMPIAPARABRISAS", "LIMPIAPARABRISA",
                    "WIPER")),
    ("BATERIA", ("BATERIAS", "ACUMULADOR", "ACUMULADORES")),
    ("TERMOSTATO", ("TERMOSTATOS", "VALVULA TERMOSTATICA")),
    ("TAPA", ("TAPAS", "TAPA VALVULAS", "TAPA DE VALVULAS")),
    ("CARTER", ("CARTERS", "CARTER ACEITE")),
    ("MULTIPLE", ("MULTIPLES", "MULTIPLE ADMISION", "MULTIPLE ESCAPE")),
)


# Sinónimos de una sola palabra usados para expandir la consulta.
SINONIMOS = {}


def _agregar_sinonimo(termino, canonico):
    SINONIMOS[termino] = canonico


for _canonico, _variantes in GRUPOS_SINONIMOS:
    for _variante in _variantes:
        _agregar_sinonimo(_variante, _canonico)
    # El canónico también apunta a sí mismo, pero conserva su propio grupo.
    SINONIMOS.setdefault(_canonico, _canonico)


# ---------------------------------------------------------------------------
# Abreviaturas frecuentes -> palabra completa
# ---------------------------------------------------------------------------
ABREVIATURAS = {
    "PPAL": "PRINCIPAL",
    "PRAL": "PRINCIPAL",
    "IZQ": "IZQUIERDO",
    "IZQ.": "IZQUIERDO",
    "DER": "DERECHO",
    "DEL": "DELANTERO",
    "DELT": "DELANTERO",
    "TRAS": "TRASERO",
    "TRAS.": "TRASERO",
    "TRAC": "TRACCION",
    "TRANSM": "TRANSMISION",
    "MEC": "MECANICA",
    "AUT": "AUTOMATICA",
    "AUTOM": "AUTOMATICA",
    "DIES": "DIESEL",
    "GAS": "GASOLINA",
    "REF": "REFERENCIA",
    "EQ": "EQUIVALENTE",
    "CAB": "CABINA",
    "SENC": "SENCILLA",
    "DBL": "DOBLE",
    "D/C": "DOBLE CABINA",
    "C/D": "DOBLE CABINA",
    "S/C": "SENCILLA CABINA",
    "4X2": "4X2",
    "4X4": "4X4",
    "2WD": "4X2",
    "4WD": "4X4",
    "AWD": "4X4",
}


# ---------------------------------------------------------------------------
# Marcas y modelos usados en el catálogo
# ---------------------------------------------------------------------------
MARCAS = {
    "TOYOTA", "NISSAN", "CHEVROLET", "MAZDA", "KIA", "HYUNDAI",
    "MITSUBISHI", "HONDA", "FORD", "VOLKSWAGEN", "SUZUKI", "ISUZU",
    "DODGE", "JEEP", "RENAULT", "PEUGEOT", "CITROEN", "FIAT", "BMW",
    "MERCEDES", "BENZ", "AUDI", "SUBARU", "DAIHATSU", "DAEWOO",
    "SSANGYONG", "LAND", "ROVER", "JMC", "GREAT", "WALL", "CHERY",
    "DONGFENG", "FOTON", "HINO", "UD", "SCANIA", "VOLVO", "INTERNATIONAL",
}

MODELOS = {
    "HILUX", "FRONTIER", "NAVARA", "DMAX", "D-MAX", "LUV", "SAMURAI",
    "PATROL", "ALLEGRO", "SEPHIA", "TROOPER", "COROLLA", "CAMRY",
    "PRADO", "PRADO", "LAND CRUISER", "RUNNER", "4RUNNER", "RAV4",
    "YARIS", "AVANZA", "HIACE", "URVAN", "SENTRA", "TIIDA", "VERSA",
    "X-TRAIL", "XTRAIL", "PATHFINDER", "MURANO", "KICKS", "MARCH",
    "VERSO", "SPRINTER", "CARIBE", "TERCEL", "STARLET", "PRIUS",
    "TUCSON", "SANTA FE", "ACCENT", "ELANTRA", "RIO", "SPORTAGE",
    "CERATO", "PICANTO", "MONTERO", "L200", "L300", "NATIVA", "OUTLANDER",
    "RANGER", "F150", "F-150", "EXPLORER", "ESCAPE", "ECOSPORT",
    "AMAROK", "GOL", "SAVEIRO", "VOYAGE", "JETTA", "GOLF", "PASSAT",
    "JIMNY", "VITARA", "SWIFT", "GRAND VITARA", "TRACKER", "CORSA",
    "AVEO", "OPTRA", "SPARK", "NPR", "NKR", "NQR", "DUTRO", "HD65",
    "HD72", "HD78", "PORTER", "K2700", "MB", "ACCELO", "TECTOR",
    "DAKAR", "BLAZER", "S10", "SONOMA", "SILVERADO", "TAHOE",
    "SUPER DUTY", "RANGER", "MAVERICK", "BRONCO", "F100", "F-100",
    "JOURNEY", "DURANGO", "RAM", "CHARGER", "CHALLENGER", "AVENGER",
    "WRANGLER", "CHEROKEE", "GRAND CHEROKEE", "COMPASS", "RENEGADE",
}


# ---------------------------------------------------------------------------
# Dimensiones técnicas y sus valores canónicos
# ---------------------------------------------------------------------------
VALORES_DIMENSION = {
    "lado": {
        "IZQUIERDO": ("IZQUIERDO", "IZQUIERDA", "IZQ", "LEFT", "LH", "LADO IZQ"),
        "DERECHO": ("DERECHO", "DERECHA", "DER", "RIGHT", "RH", "LADO DER"),
    },
    "posicion": {
        "DELANTERO": ("DELANTERO", "DELANTERA", "FRONTAL", "FRENTE",
                       "FRONT", "DELT"),
        "TRASERO": ("TRASERO", "TRASERA", "TRAS", "POSTERIOR", "ATRAS",
                     "REAR", "TRAS."),
    },
    "cabina": {
        "SENCILLA": ("SENCILLA", "SIMPLE", "SENC", "CABINA SENCILLA",
                      "CABINA SIMPLE", "S/C"),
        "DOBLE": ("DOBLE", "DOBLE CABINA", "CABINA DOBLE", "DBL", "D/C",
                   "C/D"),
    },
    "traccion": {
        "4X2": ("4X2", "4 X 2", "2WD", "4X2", "DOS RUEDAS", "SENCILLA TRACCION"),
        "4X4": ("4X4", "4 X 4", "4WD", "AWD", "CUATRO POR CUATRO",
                 "DOBLE TRACCION", "TRACCION 4X4"),
    },
    "transmision": {
        "MECANICA": ("MECANICA", "MECANICO", "MANUAL", "MEC", "STD",
                      "SINCRONICA"),
        "AUTOMATICA": ("AUTOMATICA", "AUTOMATICO", "AUT", "AUTOM",
                        "AUTOMATIZADA"),
    },
    "combustible": {
        "GASOLINA": ("GASOLINA", "GAS", "NAFTERO", "NAFTA", "BENCINA"),
        "DIESEL": ("DIESEL", "DIES", "ACPM", "PETROLEO", "DIESELT"),
    },
}


# Preguntas asociadas a cada dimensión. Sólo se usa una a la vez.
PREGUNTAS = {
    "lado": "Para identificar el repuesto correcto, ¿es del lado izquierdo o derecho?",
    "posicion": "¿El repuesto es delantero o trasero?",
    "cabina": "¿El vehículo es de cabina sencilla o doble cabina?",
    "traccion": "¿El vehículo es 4x2 o 4x4?",
    "transmision": "¿La transmisión es mecánica o automática?",
    "combustible": "¿El vehículo es a gasolina o diésel?",
    "motor": "¿Qué motor o cilindraje tiene el vehículo?",
    "anio": "¿De qué año es el vehículo?",
    "medida": "¿Qué medida o referencia adicional conoce del repuesto?",
    "marca": "¿De qué marca es el vehículo?",
    "modelo": "¿Qué modelo de vehículo es?",
}

# Orden en que se prefieren las preguntas.
ORDEN_DIMENSIONES = (
    "lado", "posicion", "cabina", "traccion", "transmision", "combustible",
    "motor", "anio", "medida", "marca", "modelo",
)


def valores_de_dimension(campo):
    """Devuelve los valores canónicos de una dimensión (vacío si no aplica)."""

    return tuple(VALORES_DIMENSION.get(campo, {}).keys())


def dimensiones_tecnicas():
    return tuple(ORDEN_DIMENSIONES)

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account

    ERROR_DEPENDENCIA = None
except ImportError as error:
    ERROR_DEPENDENCIA = str(error)

ZONA_HORARIA = ZoneInfo("America/Santiago")

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SERVICE_ACCOUNT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "manosvivas-calendar-a2e0e23082e2.json",
)
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "manosvivascl@gmail.com")
# Mismo horario de atención que ofrece /api/disponibilidad.
HORA_INICIO = int(os.environ.get("DISPONIBILIDAD_HORA_INICIO", "9"))
HORA_FIN = int(os.environ.get("DISPONIBILIDAD_HORA_FIN", "20"))

# Una reserva sin pagar se reutiliza si el mismo cliente vuelve con el mismo
# servicio y horario dentro de este plazo, en vez de crear otra fila.
VENTANA_REUTILIZACION = timedelta(hours=2)
ESTADOS_REUTILIZABLES = ("pendiente_pago", "pago_rechazado")

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
# La Public Key es pública, pero viaja desde acá para que el modo (prueba o
# producción) lo defina una variable de entorno y no una edición de código:
# así cambiar de modo no exige desplegar ni acordarse de revertir nada.
MERCADOPAGO_PUBLIC_KEY = os.environ.get("MERCADOPAGO_PUBLIC_KEY")
MERCADOPAGO_PREFERENCIAS_URL = "https://api.mercadopago.com/checkout/preferences"

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

SITE_URL = os.environ.get("SITE_URL", "https://manosvivas.cl")

DURACIONES_VALIDAS = (60, 90)


class ErrorSolicitud(Exception):
    def __init__(self, status, mensaje, codigo=None):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje
        # El frontend usa el código para decidir qué hacer; el mensaje es
        # solo para mostrar.
        self.codigo = codigo


def _validar_configuracion():
    faltantes = [
        nombre
        for nombre, valor in (
            ("MERCADOPAGO_ACCESS_TOKEN", MERCADOPAGO_ACCESS_TOKEN),
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY),
        )
        if not valor
    ]
    if faltantes:
        raise RuntimeError(f"Faltan variables de entorno: {', '.join(faltantes)}")


def _supabase_request(metodo, ruta, cuerpo=None, headers_extra=None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if headers_extra:
        headers.update(headers_extra)

    data = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    request = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{ruta}", data=data, method=metodo, headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            crudo = response.read()
            return json.loads(crudo) if crudo else None
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error de Supabase ({error.code}): {detalle}")


def obtener_producto(producto_id):
    fila_escapada = urllib.parse.quote(str(producto_id), safe="")
    resultado = _supabase_request("GET", f"catalogo?id=eq.{fila_escapada}&select=*")
    if not resultado:
        raise ErrorSolicitud(404, "El producto solicitado no existe.")
    producto = resultado[0]
    if producto.get("activo") is False:
        raise ErrorSolicitud(409, "Este producto ya no está disponible.")
    if producto.get("tipo") == "suscripcion":
        raise ErrorSolicitud(400, "Las suscripciones no se procesan por este endpoint.")
    return producto


def obtener_complementos(ids_complementos):
    """Cada fila de 'complementos' ya es una combinación específica de
    nombre + duracion_min + precio (ej. Reflexología 10 min y Reflexología
    15 min son filas distintas) — el frontend manda el id de la variante
    exacta que el cliente eligió, no hace falta cruzar por duración aquí.
    """
    if not ids_complementos:
        return []
    lista = ",".join(urllib.parse.quote(str(i), safe="") for i in ids_complementos)
    resultado = _supabase_request("GET", f"complementos?id=in.({lista})&select=*")
    return resultado or []


def calcular_precio_producto(producto, duracion_minutos):
    tiene_precio_por_duracion = (
        producto.get("precio_60") is not None or producto.get("precio_90") is not None
    )
    if tiene_precio_por_duracion:
        if duracion_minutos not in DURACIONES_VALIDAS:
            raise ErrorSolicitud(
                400, f"'duracion' debe ser uno de {DURACIONES_VALIDAS} para este producto."
            )
        campo = f"precio_{duracion_minutos}"
        precio = producto.get(campo)
        if precio is None:
            raise ErrorSolicitud(400, f"Este producto no tiene precio definido para {duracion_minutos} minutos.")
        return precio

    precio = producto.get("precio")
    if precio is None:
        raise ErrorSolicitud(500, "El producto no tiene un precio configurado en el catálogo.")
    return precio


def fecha_hora_con_zona(valor):
    """'fecha_hora_solicitada' es timestamptz. Si se manda la hora sin zona,
    Postgres la toma como UTC y la reserva queda corrida varias horas: acá
    se fija explícitamente la hora de Chile, que es la que se muestra."""
    momento = datetime.fromisoformat(valor)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=ZONA_HORARIA)
    return momento.isoformat()


def crear_reserva_pendiente(datos, producto, complementos, monto_total):
    fila = {
        "servicio": producto.get("nombre"),
        "duracion": datos.get("duracion"),
        # La columna es text: se guarda legible, para que la reserva se
        # entienda leyendo la tabla sin cruzarla con otra.
        "complementos": ", ".join(f"{c['nombre']} · {c['duracion_min']} min" for c in complementos),
        "monto_total": monto_total,
        "nombre_cliente": datos["cliente"]["nombre"],
        "email_cliente": datos["cliente"]["email"].strip(),
        "telefono_cliente": datos["cliente"].get("telefono"),
        "direccion": datos.get("direccion"),
        "estacionamiento": datos.get("estacionamiento"),
        "fecha_hora_solicitada": fecha_hora_con_zona(datos["fecha_hora"]),
        "estado": "pendiente_pago",
    }
    resultado = _supabase_request(
        "POST", "reservas", cuerpo=fila, headers_extra={"Prefer": "return=representation"}
    )
    if not resultado:
        raise RuntimeError("Supabase no devolvió la reserva creada.")
    return resultado[0]


def eliminar_reserva(reserva_id):
    try:
        _supabase_request("DELETE", f"reservas?id=eq.{urllib.parse.quote(str(reserva_id), safe='')}")
    except RuntimeError:
        pass


def crear_preferencia_mercadopago(reserva, producto, complementos, datos):
    items = [
        {
            "title": producto.get("nombre", "Servicio Manos Vivas"),
            "quantity": 1,
            "currency_id": "CLP",
            "unit_price": float(calcular_precio_producto(producto, datos.get("duracion"))),
        }
    ]
    for complemento in complementos:
        duracion = complemento.get("duracion_min")
        titulo = complemento.get("nombre", "Complemento")
        if duracion:
            titulo = f"{titulo} ({duracion} min)"
        items.append(
            {
                "title": titulo,
                "quantity": 1,
                "currency_id": "CLP",
                "unit_price": float(complemento.get("precio", 0)),
            }
        )

    payload = {
        "items": items,
        "payer": {
            "name": datos["cliente"]["nombre"],
            "email": datos["cliente"]["email"],
        },
        "external_reference": str(reserva["id"]),
        "notification_url": f"{SITE_URL}/api/webhook-pago",
        "back_urls": {
            "success": f"{SITE_URL}/?reserva=confirmada&id={reserva['id']}",
            "failure": f"{SITE_URL}/?reserva=fallida&id={reserva['id']}",
            "pending": f"{SITE_URL}/?reserva=pendiente&id={reserva['id']}",
        },
        "auto_return": "approved",
        "statement_descriptor": "MANOS VIVAS",
    }

    request = urllib.request.Request(
        MERCADOPAGO_PREFERENCIAS_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Error de Mercado Pago ({error.code}): {detalle}")


# ---------- Disponibilidad (se vuelve a validar antes de cobrar) ----------

def minutos_de_complementos(complementos):
    return sum(int(c.get("duracion_min") or 0) for c in complementos)


def _token_calendar():
    if ERROR_DEPENDENCIA:
        raise RuntimeError(f"Falta la dependencia google-auth en el despliegue ({ERROR_DEPENDENCIA}).")
    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        cred = service_account.Credentials.from_service_account_info(json.loads(GOOGLE_SERVICE_ACCOUNT_JSON), scopes=scopes)
    elif os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        cred = service_account.Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=scopes)
    else:
        raise RuntimeError("Falta la credencial de Google Calendar: define GOOGLE_SERVICE_ACCOUNT_JSON.")
    cred.refresh(GoogleAuthRequest())
    return cred.token


def validar_horario(fecha_hora, minutos):
    """El horario que el cliente eligió puede haberse ocupado mientras
    llenaba el formulario, o venir precargado de una visita anterior. Se
    revisa contra el calendario real justo antes de generar el cobro."""
    inicio = datetime.fromisoformat(fecha_hora)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=ZONA_HORARIA)
    inicio = inicio.astimezone(ZONA_HORARIA)
    fin = inicio + timedelta(minutes=minutos)

    no_disponible = ErrorSolicitud(
        409, "Ese horario ya no está disponible. Elige otra hora.", codigo="horario_no_disponible"
    )
    if inicio <= datetime.now(ZONA_HORARIA):
        raise no_disponible
    apertura = inicio.replace(hour=HORA_INICIO, minute=0, second=0, microsecond=0)
    cierre = inicio.replace(hour=HORA_FIN, minute=0, second=0, microsecond=0)
    if inicio < apertura or fin > cierre:
        raise no_disponible

    request = urllib.request.Request(
        "https://www.googleapis.com/calendar/v3/freeBusy",
        data=json.dumps({
            "timeMin": inicio.astimezone(timezone.utc).isoformat(),
            "timeMax": fin.astimezone(timezone.utc).isoformat(),
            "items": [{"id": GOOGLE_CALENDAR_ID}],
        }).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {_token_calendar()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            calendario = json.loads(response.read())["calendars"].get(GOOGLE_CALENDAR_ID, {})
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Error al consultar la agenda ({error.code}): {error.read().decode('utf-8', 'ignore')}")

    if calendario.get("errors"):
        raise RuntimeError(f"Google Calendar devolvió errores: {calendario['errors']}")
    if calendario.get("busy"):
        raise no_disponible


# ---------- Reutilización de reservas sin pagar ----------

def buscar_reserva_reutilizable(producto, fecha_hora_con_offset, email):
    """Quien vuelve atrás desde el pago y avanza de nuevo con el mismo
    servicio y horario no debe generar otra fila en la base."""
    desde = (datetime.now(timezone.utc) - VENTANA_REUTILIZACION).isoformat()
    filtros = "&".join([
        f"servicio=eq.{urllib.parse.quote(producto.get('nombre') or '', safe='')}",
        f"fecha_hora_solicitada=eq.{urllib.parse.quote(fecha_hora_con_offset, safe='')}",
        f"email_cliente=eq.{urllib.parse.quote(email, safe='')}",
        f"estado=in.({','.join(ESTADOS_REUTILIZABLES)})",
        f"created_at=gte.{urllib.parse.quote(desde, safe='')}",
    ])
    filas = _supabase_request("GET", f"reservas?{filtros}&select=*&order=id.desc&limit=1")
    return filas[0] if filas else None


def actualizar_reserva_reutilizada(reserva, datos, complementos, monto_total):
    """Los datos que el cliente pudo corregir al volver atrás se actualizan
    sobre la misma fila."""
    cambios = {
        "duracion": datos.get("duracion"),
        "complementos": ", ".join(f"{c['nombre']} · {c['duracion_min']} min" for c in complementos),
        "monto_total": monto_total,
        "nombre_cliente": datos["cliente"]["nombre"],
        "telefono_cliente": datos["cliente"].get("telefono"),
        "direccion": datos.get("direccion"),
        "estacionamiento": datos.get("estacionamiento"),
        "estado": "pendiente_pago",
    }
    filas = _supabase_request(
        "PATCH",
        f"reservas?id=eq.{urllib.parse.quote(str(reserva['id']), safe='')}",
        cuerpo=cambios,
        headers_extra={"Prefer": "return=representation"},
    )
    return filas[0] if filas else {**reserva, **cambios}


def procesar_solicitud(datos):
    if not isinstance(datos, dict):
        raise ErrorSolicitud(400, "Cuerpo de la solicitud inválido.")

    producto_id = datos.get("producto_id")
    cliente = datos.get("cliente") or {}
    fecha_hora = datos.get("fecha_hora")
    if not producto_id:
        raise ErrorSolicitud(400, "Falta 'producto_id'.")
    if not cliente.get("nombre") or not cliente.get("email"):
        raise ErrorSolicitud(400, "Falta 'cliente.nombre' o 'cliente.email'.")
    if not fecha_hora:
        raise ErrorSolicitud(400, "Falta 'fecha_hora' (formato ISO 8601, ej. 2026-09-20T15:00:00).")
    try:
        datetime.fromisoformat(fecha_hora)
    except ValueError:
        raise ErrorSolicitud(400, "'fecha_hora' debe tener formato ISO 8601, ej. 2026-09-20T15:00:00.")

    producto = obtener_producto(producto_id)
    complementos = obtener_complementos(datos.get("complementos"))
    monto_total = calcular_precio_producto(producto, datos.get("duracion")) + sum(
        float(c.get("precio", 0)) for c in complementos
    )

    validar_horario(fecha_hora, (datos.get("duracion") or 60) + minutos_de_complementos(complementos))

    reserva = buscar_reserva_reutilizable(
        producto, fecha_hora_con_zona(fecha_hora), cliente["email"].strip()
    )
    reutilizada = reserva is not None
    if reutilizada:
        reserva = actualizar_reserva_reutilizada(reserva, datos, complementos, monto_total)
    else:
        reserva = crear_reserva_pendiente(datos, producto, complementos, monto_total)

    try:
        preferencia = crear_preferencia_mercadopago(reserva, producto, complementos, datos)
    except RuntimeError:
        # Solo se borra si la creó esta misma llamada: una reutilizada sigue
        # siendo del cliente.
        if not reutilizada:
            eliminar_reserva(reserva["id"])
        raise

    return {
        "reserva_id": reserva["id"],
        "preference_id": preferencia["id"],
        "init_point": preferencia.get("init_point"),
        "monto_total": monto_total,
        "public_key": MERCADOPAGO_PUBLIC_KEY,
        "reutilizada": reutilizada,
        "modo_prueba": bool(MERCADOPAGO_ACCESS_TOKEN and MERCADOPAGO_ACCESS_TOKEN.startswith("TEST-")),
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            _validar_configuracion()

            largo_contenido = int(self.headers.get("Content-Length", 0))
            cuerpo_crudo = self.rfile.read(largo_contenido) if largo_contenido else b"{}"
            try:
                datos = json.loads(cuerpo_crudo)
            except json.JSONDecodeError:
                self._responder(400, {"error": "El cuerpo debe ser JSON válido."})
                return

            resultado = procesar_solicitud(datos)
            self._responder(201, resultado)
        except ErrorSolicitud as error:
            self._responder(error.status, {"error": error.mensaje, "codigo": error.codigo})
        except RuntimeError as error:
            self._responder(502, {"error": str(error)})
        except Exception as error:
            self._responder(500, {"error": "Error inesperado.", "detalle": str(error)})

    def _responder(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

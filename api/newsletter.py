import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
BREVO_LIST_ID = os.environ.get("BREVO_LIST_ID")
BREVO_CONTACTOS_URL = "https://api.brevo.com/v3/contacts"

PATRON_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErrorSolicitud(Exception):
    def __init__(self, status, mensaje):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje


def suscribir_contacto(email, nombre=None):
    payload = {"email": email, "updateEnabled": True}
    if BREVO_LIST_ID:
        payload["listIds"] = [int(BREVO_LIST_ID)]
    if nombre:
        payload["attributes"] = {"FIRSTNAME": nombre}

    request = urllib.request.Request(
        BREVO_CONTACTOS_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return
    except urllib.error.HTTPError as error:
        cuerpo = error.read().decode("utf-8", "ignore")
        try:
            detalle = json.loads(cuerpo)
        except json.JSONDecodeError:
            detalle = {}

        # Ya suscrito: no es un error para quien llena el formulario.
        if error.code == 400 and detalle.get("code") == "duplicate_parameter":
            return

        raise RuntimeError(f"Error de Brevo ({error.code}): {cuerpo}")


def procesar_solicitud(datos):
    if not isinstance(datos, dict):
        raise ErrorSolicitud(400, "Cuerpo de la solicitud inválido.")

    email = (datos.get("email") or "").strip().lower()
    nombre = (datos.get("nombre") or "").strip() or None

    if not email or not PATRON_EMAIL.match(email):
        raise ErrorSolicitud(400, "Ingresa un correo válido.")

    suscribir_contacto(email, nombre)
    return {"ok": True}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            if not BREVO_API_KEY:
                raise RuntimeError("Falta la variable de entorno BREVO_API_KEY.")

            largo_contenido = int(self.headers.get("Content-Length", 0))
            cuerpo_crudo = self.rfile.read(largo_contenido) if largo_contenido else b"{}"
            try:
                datos = json.loads(cuerpo_crudo)
            except json.JSONDecodeError:
                self._responder(400, {"error": "El cuerpo debe ser JSON válido."})
                return

            resultado = procesar_solicitud(datos)
            self._responder(200, resultado)
        except ErrorSolicitud as error:
            self._responder(error.status, {"error": error.mensaje})
        except RuntimeError as error:
            self._responder(502, {"error": str(error)})
        except Exception as error:
            self._responder(500, {"error": "Error inesperado.", "detalle": str(error)})

    def _responder(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

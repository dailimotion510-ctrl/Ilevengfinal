"""
Cliente para la API de Picta (sacado del panel admin.picta.cu).
NO PROBADO: los endpoints salen del código del panel. Si algo falla,
usa validar() para ver qué campo pide el servidor.

pip install requests requests-toolbelt
"""
import os
import requests

try:
    from requests_toolbelt import MultipartEncoder
except ImportError:  # sin toolbelt se sube el archivo entero en memoria
    MultipartEncoder = None

B = "https://api.picta.cu"
CLIENT_ID = "lBNcdmsTc5Om3N9MTP4Dy9Rnzc6eooPU4QVHVvS5"
CAMPOS_S3 = ["bucket", "key", "Content-Type", "policy", "x-amz-algorithm",
             "x-amz-credential", "x-amz-date", "x-amz-signature"]


class PictaError(Exception):
    pass


class Picta:
    def __init__(self, usuario, clave):
        self.usuario, self.clave = usuario, clave
        self.token = None
        self.s = requests.Session()
        self.login()

    # ---------- sesión ----------
    def login(self):
        r = requests.post(f"{B}/o/token/", data={
            "username": self.usuario, "password": self.clave,
            "grant_type": "password", "client_id": CLIENT_ID}, timeout=30)
        if r.status_code != 200:
            raise PictaError(f"Login falló: {r.status_code} {r.text[:200]}")
        self.token = r.json()["access_token"]

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _req(self, metodo, url, **kw):
        kw.setdefault("timeout", 60)
        r = self.s.request(metodo, url, headers={**self.h, **kw.pop("headers", {})}, **kw)
        if r.status_code in (401, 403):  # token vencido: reintenta una vez
            self.login()
            r = self.s.request(metodo, url, headers={**self.h, **kw.get("headers", {})}, **kw)
        return r

    def _get(self, path, **params):
        r = self._req("GET", f"{B}{path}", params=params)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _lista(d):
        return d["results"] if isinstance(d, dict) and "results" in d else d

    # ---------- catálogos ----------
    def tipologias(self):
        return self._lista(self._get("/v1/tipologia/", page_size=100))

    def generos(self, tipo=None):
        """Confirmado con HAR real: genero se filtra por `tipo` (ej. 'ci', 'mu').
        El código exacto para cada tipología no está confirmado del todo."""
        p = {"ordering": "nombre", "page_size": 100}
        if tipo:
            p["tipo"] = tipo
        return self._lista(self._get("/v2/genero/", **p))

    def extras(self, tipo, texto=""):
        """Catálogo genérico de Picta (premio, productora, etc).
        Confirmado con HAR real: /v2/extra/?tipo=<tipo>&nombre__contains=<texto>."""
        p = {"tipo": tipo, "page_size": 10}
        if texto:
            p["nombre__contains"] = texto
        return self._lista(self._get("/v2/extra/", **p))

    def palabras_clave(self, texto):
        """Confirmado con HAR real: /v2/palabra_clave/?palabra__contains=<texto>."""
        return self._lista(self._get("/v2/palabra_clave/", page_size=10,
                                      ordering="palabra", palabra__contains=texto))

    def eventos(self):
        return self._lista(self._get("/v2/evento/", page_size=50))

    def canales(self, texto=""):
        p = {"page_size": 10, "ordering": "nombre"}
        if texto:
            p["nombre__contains"] = texto
        return self._lista(self._get("/v2/canal/", **p))

    def paises(self):
        return self._lista(self._get("/v1/country_code/", page_size=300))

    def personas(self, texto=""):
        p = {"page_size": 10}
        if texto:
            p["nombre__contains"] = texto
        return self._lista(self._get("/v2/persona/", **p))

    def crear_persona(self, nombre):
        """Confirmado con HAR real: POST /v1/persona/ solo con 'nombre'.
        Úsalo cuando buscas a alguien y no existe en el catálogo."""
        r = self._req("POST", f"{B}/v1/persona/", data={"nombre": nombre})
        r.raise_for_status()
        return r.json()

    def series(self, texto="", canal_nombre=None):
        """Confirmado con HAR real: para listar las series de TU canal se usa
        canal_nombre_raw=<nombre del canal> (no un id); nombre__contains busca por texto."""
        p = {"page_size": 10, "ordering": "nombre"}
        if texto:
            p["nombre__contains"] = texto
        if canal_nombre:
            p["canal_nombre_raw"] = canal_nombre
        return self._lista(self._get("/v2/serie/", **p))

    def temporadas(self, serie_id=None):
        p = {"page_size": 50}
        if serie_id:
            p["serie_id"] = serie_id
        return self._lista(self._get("/v2/temporada/", **p))

    def crear_persona(self, nombre):
        """Confirmado con HAR real: POST /v1/persona/ con solo `nombre`."""
        r = self._req("POST", f"{B}/v1/persona/", data={"nombre": nombre})
        if not r.ok:
            raise PictaError(f"Crear persona falló: {r.status_code} {r.text[:300]}")
        return r.json()["id"]

    def resolver_persona(self, nombre, crear_si_falta=True):
        """Busca a alguien por nombre en /v2/persona/; si no existe, lo crea.
        Devuelve el id, o None si `nombre` viene vacío."""
        nombre = (nombre or "").strip()
        if not nombre:
            return None
        for p in self.personas(nombre):
            if p.get("nombre", "").strip().lower() == nombre.lower():
                return p["id"]
        if crear_si_falta:
            return self.crear_persona(nombre)
        return None

    def resolver_extra(self, tipo, nombre):
        """Busca en /v2/extra/ (productora, premio...) por nombre.
        No está confirmado que se pueda crear uno nuevo, así que si no
        aparece devuelve None (ese campo se deja sin mandar)."""
        nombre = (nombre or "").strip()
        if not nombre:
            return None
        res = self.extras(tipo, nombre)
        for e in res:
            if e.get("nombre", "").strip().lower() == nombre.lower():
                return e["id"]
        return res[0]["id"] if res else None

    def crear_serie(self, **campos):
        """Confirmado con HAR real. Campos típicos: nombre, sinopsis, canal,
        ano, pais(texto), genero(id/s separados por coma), director, reparto,
        productora, fotografia, musica, guion (todos ids), cantidad_temporadas,
        clasificacion, visible, internacional."""
        datos = self._form(campos, {})
        r = self._req("POST", f"{B}/v1/serie/", data=datos)
        if not r.ok:
            raise PictaError(f"Crear serie falló: {r.status_code} {r.text[:500]}")
        return r.json()

    def crear_temporada(self, serie_id, canal, numero, nombre=None):
        """Confirmado con HAR real: POST /v1/temporada/ con nombre, numero,
        serie, canal y cantidad_capitulos=0."""
        datos = {"serie": serie_id, "canal": canal, "numero": numero,
                 "nombre": nombre or f"Temporada {numero}", "cantidad_capitulos": 0}
        r = self._req("POST", f"{B}/v1/temporada/", data=datos)
        if not r.ok:
            raise PictaError(f"Crear temporada falló: {r.status_code} {r.text[:500]}")
        return r.json()

    def crear_serie(self, nombre, canal, **campos):
        """Confirmado con HAR real: POST /v1/serie/.
        campos admitidos: sinopsis, ano, pais, genero(id), cantidad_temporadas,
        cantidad_capitulos, clasificacion, director(id), reparto(id),
        productora(id), fotografia(id), musica(id), guion(id), visible,
        internacional."""
        datos = self._form(dict(nombre=nombre, canal=canal), campos)
        r = self._req("POST", f"{B}/v1/serie/", data=datos)
        if not r.ok:
            raise PictaError(f"Crear serie falló: {r.status_code} {r.text[:500]}")
        return r.json()

    def crear_temporada(self, serie, canal, numero, cantidad_capitulos=0, nombre=None):
        """Confirmado con HAR real: POST /v1/temporada/."""
        datos = {"serie": serie, "canal": canal, "numero": numero,
                 "cantidad_capitulos": cantidad_capitulos,
                 "nombre": nombre or f"Temporada {numero}"}
        r = self._req("POST", f"{B}/v1/temporada/", data=self._form(datos, {}))
        if not r.ok:
            raise PictaError(f"Crear temporada falló: {r.status_code} {r.text[:500]}")
        return r.json()

    def mis_publicaciones(self, **filtros):
        return self._get("/v2/publicacion/", **filtros)

    def resumen(self, fecha_inicial=None, fecha_final=None):
        """Confirmado con HAR real: los parámetros son date__gte / date__lte,
        y la respuesta trae resultados separados en 'nacional' e 'internacional'."""
        p = {}
        if fecha_inicial:
            p["date__gte"] = fecha_inicial
        if fecha_final:
            p["date__lte"] = fecha_final
        d = self._get("/v2/resumen/datos/", **p)
        return d["results"][0] if d.get("results") else d

    # ---------- subida ----------
    def subir_archivo(self, ruta, progreso=None):
        """Sube un video (o subtítulo) a MinIO. Devuelve la key (url_manifiesto)."""
        cred = self._get("/v2/publicacion/devolver_url_minio/")
        url, f = cred["url_minio"]
        campos = [(k, f[k]) for k in CAMPOS_S3 if k in f]
        nombre = os.path.basename(ruta)
        with open(ruta, "rb") as fh:
            if MultipartEncoder:
                from requests_toolbelt import MultipartEncoderMonitor
                enc = MultipartEncoder(fields=campos + [("file", (nombre, fh))])
                cuerpo = MultipartEncoderMonitor(
                    enc, (lambda m: progreso(m.bytes_read, enc.len)) if progreso else None)
                r = requests.post(url, data=cuerpo,
                                  headers={"Content-Type": enc.content_type}, timeout=None)
            else:
                r = requests.post(url, data=campos, files={"file": (nombre, fh)}, timeout=None)
        if r.status_code not in (200, 201, 204):
            raise PictaError(f"Subida falló: {r.status_code} {r.text[:300]}")
        return f["key"]

    # ---------- publicación ----------
    @staticmethod
    def _form(datos, extra):
        d = {}
        for k, v in {**datos, **extra}.items():
            if v is None or v == "":
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            elif isinstance(v, (list, tuple)):
                v = ",".join(str(x) for x in v)
            d[k] = str(v)
        return d

    def _datos(self, nombre, canal, tipologia, tipo, key, descripcion="",
               publicado=True, descargable=True, mostrar_comentarios=True,
               internacional=False, es_exclusivo=False, clasificacion="ATP",
               palabras="", subtitulo=None, extra=None):
        # Confirmado con HAR real: el campo "tipo" de la publicación SIEMPRE
        # vale el texto fijo "publicacion", sin importar la tipología elegida.
        # El "modelo" de la tipología (documental, video, capitulo...) solo
        # decide qué campos extra van en `extra` (pais, genero, serie, etc).
        base = dict(nombre=nombre, canal=canal, descripcion=descripcion,
                    tipologia=tipologia, tipo="publicacion", url_manifiesto=key,
                    publicado=publicado, descargable=descargable,
                    mostrar_comentarios=mostrar_comentarios,
                    internacional=internacional, es_exclusivo=es_exclusivo,
                    clasificacion=clasificacion, palabraClave=palabras,
                    url_subtitulo=subtitulo)
        return self._form(base, extra or {})

    def validar(self, **kw):
        """Valida los campos sin crear nada. Devuelve (ok, respuesta)."""
        r = self._req("POST", f"{B}/v1/publicacion/validar_campos/", data=self._datos(**kw))
        try:
            return r.ok, r.json()
        except ValueError:
            return r.ok, r.text

    def crear(self, portada=None, **kw):
        """
        kw: nombre, canal(id), tipologia(id), tipo(modelo), key, descripcion,
            extra={"pais":..,"ano":..,"genero":[ids],"director":[ids],...}
        portada: ruta de la imagen (url_imagen).
        """
        datos = self._datos(**kw)
        files = {}
        if portada:
            files["url_imagen"] = (os.path.basename(portada), open(portada, "rb"))
        try:
            r = self._req("POST", f"{B}/v1/publicacion/", data=datos, files=files or None)
        finally:
            for _, (_, fh) in files.items():
                fh.close()
        if not r.ok:
            raise PictaError(f"Crear falló: {r.status_code} {r.text[:500]}")
        return r.json()

    def convertir(self, minio_id, descargable=True):
        """Lanza la conversión del video (el panel usa esto vía proxy_nomad)."""
        r = self._req("POST", f"{B}/v2/publicacion/proxy_nomad/", data={
            "action": "convert", "minio_id": minio_id,
            "descargable": "true" if descargable else "false"})
        return r.ok, r.text

    def borrar(self, pub_id):
        return self._req("DELETE", f"{B}/v1/publicacion/{pub_id}/").ok


if __name__ == "__main__":
    # Prueba rápida: lista catálogos y sube un video de ejemplo.
    p = Picta(os.environ["PICTA_USER"], os.environ["PICTA_PASS"])
    for t in p.tipologias():
        print("tipologia", t["id"], t["nombre"], t["modelo"])
    print("canales:", [(c["id"], c["nombre"]) for c in p.canales()])
    print("generos:", [(g["id"], g["nombre"]) for g in p.generos()][:10])

    # Descomenta para probar una subida real:
    # key = p.subir_archivo("video.mp4", lambda a, b: print(f"{a*100//b}%", end="\r"))
    # ok, resp = p.validar(nombre="Prueba", canal=CANAL_ID, tipologia=TIPOLOGIA_ID,
    #                      tipo="pelicula", key=key,
    #                      extra={"ano": 2024, "genero": [1], "pais": 1})
    # print(ok, resp)
    # pub = p.crear(portada="portada.jpg", nombre="Prueba", canal=CANAL_ID,
    #               tipologia=TIPOLOGIA_ID, tipo="pelicula", key=key,
    #               extra={"ano": 2024, "genero": [1], "pais": 1})
    # print(pub)

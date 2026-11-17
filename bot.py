"""
Pictaileven: bot de Telegram (Telethon) que sube videos a Picta.
Flujo: mandas un video -> asistente (nombre, tipologia, generos, ...) -> cola -> Picta.
"""
import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telethon import Button, TelegramClient, events

from picta_api import Picta, PictaError

load_dotenv()
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
AUTO_CONVERT = os.environ.get("AUTO_CONVERT", "false").lower() == "true"

DOWNLOADS = Path("downloads")
DOWNLOADS.mkdir(exist_ok=True)
CONFIG_FILE = Path("config.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pictaileven")

bot = TelegramClient("pictaileven", API_ID, API_HASH, base_logger=log)
bot.parse_mode = "html"
picta: Picta = None
QUEUE: asyncio.Queue = asyncio.Queue()
SESS = {}      # uid -> {"msg":..., "d": {...}, "step": str}
NAMES = {}     # ("can"|"gen"|"tip"|"ser"|"tmp", id) -> nombre
CACHE = {}     # catalogos cacheados


# ---------------------------------------------------------------- utilidades
def esc(x):
    return html.escape(str(x))


def cfg():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def cfg_set(**kw):
    c = cfg()
    c.update(kw)
    CONFIG_FILE.write_text(json.dumps(c, ensure_ascii=False, indent=2))


async def P(fn, *a, **k):
    """Ejecuta una funcion bloqueante de Picta en un hilo."""
    return await asyncio.to_thread(fn, *a, **k)


def tam(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def barra(a, b, ancho=12):
    p = a / b if b else 0
    llenos = int(p * ancho)
    return "█" * llenos + "░" * (ancho - llenos) + f" {p*100:.0f}%"


def limpiar_nombre(s):
    s = os.path.splitext(s)[0]
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120] or "Sin título"


def es_admin(ev):
    return ev.sender_id in ADMIN_IDS


def es_video(m):
    if not m or not m.media:
        return False
    if getattr(m, "video", None):
        return True
    d = getattr(m, "document", None)
    return bool(d and (d.mime_type or "").startswith("video"))


async def catalogo(nombre, fn):
    """Cachea catalogos 10 minutos."""
    t, v = CACHE.get(nombre, (0, None))
    if v is None or time.time() - t > 600:
        v = await P(fn)
        CACHE[nombre] = (time.time(), v)
    return v


def filas(botones, n=2):
    return [botones[i:i + n] for i in range(0, len(botones), n)]


class Progreso:
    """Edita un mensaje con barra de progreso (limitado a 1 edicion / 3 s)."""

    def __init__(self, msg, titulo):
        self.msg, self.titulo, self.t = msg, titulo, 0

    async def up(self, a, b, extra=""):
        if time.time() - self.t < 3 and a < b:
            return
        self.t = time.time()
        try:
            await self.msg.edit(f"{self.titulo}\n<code>{barra(a, b)}</code>\n{tam(a)} / {tam(b)} {extra}")
        except Exception:
            pass


# ---------------------------------------------------------------- asistente
def siguiente(s):
    d = s["d"]
    if "nombre" not in d:
        return "nombre"
    if "canal" not in d:
        return "canal"
    if "tipologia" not in d:
        return "tipologia"
    if d["modelo"] == "capitulo":
        for k in ("serie", "temporada", "numero"):
            if k not in d:
                return k
    else:
        if "genero" not in d:
            return "genero"
        if "ano" not in d:
            return "ano"
    if "descripcion" not in d:
        return "descripcion"
    if "portada" not in d:
        return "portada"
    return "confirm"


def resumen_texto(d):
    L = [f"🎬 <b>{esc(d['nombre'])}</b>",
         f"📺 Canal: {esc(d['canal_nombre'])}",
         f"🏷 Tipología: {esc(d['tipologia_nombre'])} ({esc(d['modelo'])})"]
    if d.get("generos_nombres"):
        L.append("🎭 Géneros: " + esc(", ".join(d["generos_nombres"])))
    if d.get("ano"):
        L.append(f"📅 Año: {esc(d['ano'])}")
    if d.get("serie"):
        L.append(f"📚 Serie: {esc(NAMES.get(('ser', d['serie']), d['serie']))} · "
                 f"Temporada: {esc(NAMES.get(('tmp', d.get('temporada')), d.get('temporada')))} · "
                 f"Cap. {esc(d.get('numero'))}")
    if d.get("descripcion"):
        L.append("📝 " + esc(d["descripcion"][:200]))
    L.append("🖼 Portada: " + ("sí" if d.get("portada") else "no"))
    return "\n".join(L)


async def pedir(uid):
    s = SESS[uid]
    step = s["step"] = siguiente(s)
    d = s["d"]
    skip = lambda tag: [Button.inline("⏭ Omitir", f"skip:{tag}".encode())]

    if step == "nombre":
        await bot.send_message(uid, "✏️ <b>Nombre</b> de la publicación:",
                               buttons=[[Button.inline(f"Usar: {d['_guess'][:40]}", b"nom:ok")]])
    elif step == "canal":
        await bot.send_message(uid, "📺 No hay canal por defecto. Escribe el <b>nombre del canal</b> para buscarlo:")
    elif step == "tipologia":
        tips = await catalogo("tipologias", picta.tipologias)
        for t in tips:
            NAMES[("tip", t["id"])] = (t["nombre"], t.get("modelo", ""))
        bt = [Button.inline(t["nombre"], f"tip:{t['id']}".encode()) for t in tips]
        await bot.send_message(uid, "🏷 Elige la <b>tipología</b>:", buttons=filas(bt, 2))
    elif step == "genero":
        await mostrar_generos(uid)
    elif step == "ano":
        await bot.send_message(uid, "📅 Escribe el <b>año</b> (ej. 2024):", buttons=[skip("ano")])
    elif step == "serie":
        await bot.send_message(uid, "📚 Escribe el nombre de la <b>serie</b> para buscarla:")
    elif step == "temporada":
        tmp = await P(picta.temporadas, d["serie"])
        if not tmp:
            d["temporada"] = None
            return await pedir(uid)
        for t in tmp:
            NAMES[("tmp", t["id"])] = str(t.get("nombre") or t.get("numero") or t["id"])
        bt = [Button.inline(NAMES[("tmp", t["id"])], f"tmp:{t['id']}".encode()) for t in tmp]
        await bot.send_message(uid, "🗂 Elige la <b>temporada</b>:", buttons=filas(bt, 3))
    elif step == "numero":
        await bot.send_message(uid, "🔢 Escribe el <b>número del capítulo</b>:")
    elif step == "descripcion":
        await bot.send_message(uid, "📝 Escribe la <b>descripción</b>:", buttons=[skip("descripcion")])
    elif step == "portada":
        await bot.send_message(
            uid, "🖼 Envía una <b>foto</b> como portada, o elige:",
            buttons=[[Button.inline("📸 Miniatura del video", b"por:thumb")], skip("portada")])
    elif step == "confirm":
        await bot.send_message(uid, resumen_texto(d) + "\n\n¿Publico?", buttons=[
            [Button.inline("🚀 Publicar", b"go"), Button.inline("❌ Cancelar", b"cancel")]])


async def mostrar_generos(uid, editar=None):
    s = SESS[uid]
    gens = await catalogo("generos", picta.generos)
    sel = s["d"].setdefault("_gsel", set())
    bt = []
    for g in gens:
        NAMES[("gen", g["id"])] = g["nombre"]
        pref = "✅ " if g["id"] in sel else ""
        bt.append(Button.inline(pref + g["nombre"], f"gen:{g['id']}".encode()))
    rows = filas(bt, 2) + [[Button.inline("✔ Listo", b"gen:done"), Button.inline("⏭ Omitir", b"skip:genero")]]
    txt = "🎭 Elige los <b>géneros</b> (puedes marcar varios):"
    if editar:
        await editar.edit(txt, buttons=rows)
    else:
        await bot.send_message(uid, txt, buttons=rows)


# ---------------------------------------------------------------- comandos
AYUDA = (
    "🎬 <b>Pictaileven</b>\n\n"
    "Mándame un <b>video</b> y te guío para subirlo a Picta.\n\n"
    "/panel – estadísticas del último mes\n"
    "/canal [texto] – ver o cambiar el canal por defecto\n"
    "/tipologias – lista de tipologías\n"
    "/cola – subidas pendientes\n"
    "/cancelar – cancela el asistente actual"
)


@bot.on(events.NewMessage(pattern=r"^/(start|ayuda|help)\b"))
async def cmd_start(ev):
    if not es_admin(ev):
        return await ev.reply(f"⛔ No autorizado. Tu ID es <code>{ev.sender_id}</code>.")
    await ev.reply(AYUDA)


@bot.on(events.NewMessage(pattern=r"^/cancelar\b"))
async def cmd_cancelar(ev):
    if es_admin(ev):
        SESS.pop(ev.sender_id, None)
        await ev.reply("Cancelado.")


@bot.on(events.NewMessage(pattern=r"^/cola\b"))
async def cmd_cola(ev):
    if es_admin(ev):
        await ev.reply(f"📥 En cola: <b>{QUEUE.qsize()}</b>")


@bot.on(events.NewMessage(pattern=r"^/tipologias\b"))
async def cmd_tipologias(ev):
    if not es_admin(ev):
        return
    tips = await catalogo("tipologias", picta.tipologias)
    await ev.reply("🏷 <b>Tipologías</b>\n" + "\n".join(
        f"• {esc(t['nombre'])} <code>{esc(t.get('modelo',''))}</code> (id {t['id']})" for t in tips))


@bot.on(events.NewMessage(pattern=r"^/canal\b(.*)"))
async def cmd_canal(ev):
    if not es_admin(ev):
        return
    q = ev.pattern_match.group(1).strip()
    c = cfg()
    if not q:
        actual = c.get("canal_nombre", "ninguno")
        return await ev.reply(f"📺 Canal por defecto: <b>{esc(actual)}</b>\nPara cambiarlo: <code>/canal nombre</code>")
    await buscar_canales(ev.sender_id, q, guardar=True)


@bot.on(events.NewMessage(pattern=r"^/panel\b"))
async def cmd_panel(ev):
    if not es_admin(ev):
        return
    m = await ev.reply("⏳ Consultando Picta…")
    try:
        hoy = date.today()
        d = await P(picta.resumen, (hoy - timedelta(days=30)).isoformat(), hoy.isoformat())
    except Exception as e:
        return await m.edit(f"❌ No pude leer el panel:\n<code>{esc(e)}</code>")
    L = ["📊 <b>Panel · último mes</b>"]
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                L.append(f"• {esc(k)}: <b>{v:,}</b>")
            elif isinstance(v, list):
                nombres = [str(x.get("nombre") or x.get("titulo") or "") for x in v[:5] if isinstance(x, dict)]
                L.append(f"• {esc(k)}: {len(v)}" + (": " + esc(", ".join(n for n in nombres if n)) if any(nombres) else ""))
    else:
        L.append(esc(str(d)[:1500]))
    await m.edit("\n".join(L))


async def buscar_canales(uid, q, guardar=False):
    res = await P(picta.canales, q)
    if not res:
        return await bot.send_message(uid, "No encontré canales con ese nombre. Prueba otro.")
    for c in res:
        NAMES[("can", c["id"])] = c["nombre"]
    pref = "cset" if guardar else "can"
    bt = [[Button.inline(c["nombre"][:50], f"{pref}:{c['id']}".encode())] for c in res]
    await bot.send_message(uid, "📺 Elige el canal:", buttons=bt)


# ---------------------------------------------------------------- mensajes
@bot.on(events.NewMessage(func=lambda e: e.is_private and es_video(e.message)))
async def on_video(ev):
    if not es_admin(ev):
        return await ev.reply(f"⛔ No autorizado. Tu ID es <code>{ev.sender_id}</code>.")
    m = ev.message
    nombre_arch = next((a.file_name for a in m.document.attributes if hasattr(a, "file_name")), "") if m.document else ""
    guess = limpiar_nombre((m.message or "").split("\n")[0] or nombre_arch or f"Video {m.id}")
    d = {"_guess": guess}
    c = cfg()
    if c.get("canal_id"):
        d["canal"], d["canal_nombre"] = c["canal_id"], c["canal_nombre"]
    SESS[ev.sender_id] = {"msg": m, "d": d, "step": "nombre"}
    size = m.document.size if m.document else 0
    await ev.reply(f"✅ Video recibido ({tam(size)}). Vamos a configurarlo.")
    await pedir(ev.sender_id)


@bot.on(events.NewMessage(func=lambda e: e.is_private and e.photo))
async def on_foto(ev):
    s = SESS.get(ev.sender_id)
    if not es_admin(ev) or not s or s["step"] != "portada":
        return
    p = DOWNLOADS / f"portada_{ev.sender_id}_{ev.id}.jpg"
    await ev.download_media(file=str(p))
    s["d"]["portada"] = str(p)
    await pedir(ev.sender_id)


@bot.on(events.NewMessage(func=lambda e: e.is_private and e.raw_text and not e.raw_text.startswith("/")))
async def on_texto(ev):
    uid = ev.sender_id
    s = SESS.get(uid)
    if not es_admin(ev) or not s:
        return
    t, d, step = ev.raw_text.strip(), s["d"], s["step"]
    if step == "nombre":
        d["nombre"] = t[:200]
    elif step == "canal":
        return await buscar_canales(uid, t)
    elif step == "ano":
        if not re.fullmatch(r"(19|20)\d{2}", t):
            return await ev.reply("Escribe un año válido (ej. 2024) o pulsa Omitir.")
        d["ano"] = t
    elif step == "serie":
        res = await P(picta.series, t)
        if not res:
            return await ev.reply("No encontré esa serie. Prueba otro nombre.")
        for x in res:
            NAMES[("ser", x["id"])] = x["nombre"]
        bt = [[Button.inline(x["nombre"][:50], f"ser:{x['id']}".encode())] for x in res]
        return await bot.send_message(uid, "📚 Elige la serie:", buttons=bt)
    elif step == "numero":
        if not t.isdigit():
            return await ev.reply("Escribe solo el número del capítulo.")
        d["numero"] = t
    elif step == "descripcion":
        d["descripcion"] = t[:2000]
    else:
        return
    await pedir(uid)


# ---------------------------------------------------------------- botones
@bot.on(events.CallbackQuery)
async def on_cb(ev):
    uid = ev.sender_id
    if uid not in ADMIN_IDS:
        return await ev.answer("No autorizado", alert=True)
    data = ev.data.decode()
    tag, _, val = data.partition(":")

    if tag == "cset":  # guardar canal por defecto (desde /canal)
        cid = int(val)
        cfg_set(canal_id=cid, canal_nombre=NAMES.get(("can", cid), str(cid)))
        await ev.edit(f"✅ Canal por defecto: <b>{esc(NAMES.get(('can', cid)))}</b>")
        return

    s = SESS.get(uid)
    if not s:
        return await ev.answer("Esta sesión ya no existe. Manda el video otra vez.", alert=True)
    d = s["d"]

    if data == "cancel":
        SESS.pop(uid, None)
        return await ev.edit("❌ Cancelado.")
    if data == "go":
        SESS.pop(uid, None)
        await ev.edit(resumen_texto(d) + "\n\n⏳ <b>En cola…</b>")
        job = {"uid": uid, "msg": s["msg"], "d": d}
        await QUEUE.put(job)
        return
    if data == "gen:done" or data == "skip:genero":
        ids = sorted(d.get("_gsel", set()))
        d["genero"] = ids
        d["generos_nombres"] = [NAMES.get(("gen", i), str(i)) for i in ids]
        await ev.edit("🎭 Géneros: " + (esc(", ".join(d["generos_nombres"])) or "ninguno"))
    elif tag == "gen":
        gid = int(val)
        sel = d.setdefault("_gsel", set())
        sel.symmetric_difference_update({gid})
        return await mostrar_generos(uid, editar=ev)
    elif data == "nom:ok":
        d["nombre"] = d["_guess"]
        await ev.edit(f"✏️ Nombre: <b>{esc(d['nombre'])}</b>")
    elif tag == "can":
        cid = int(val)
        d["canal"], d["canal_nombre"] = cid, NAMES.get(("can", cid), str(cid))
        cfg_set(canal_id=cid, canal_nombre=d["canal_nombre"])
        await ev.edit(f"📺 Canal: <b>{esc(d['canal_nombre'])}</b> (guardado por defecto)")
    elif tag == "tip":
        tid = int(val)
        nombre, modelo = NAMES[("tip", tid)]
        d["tipologia"], d["tipologia_nombre"], d["modelo"] = tid, nombre, modelo
        await ev.edit(f"🏷 Tipología: <b>{esc(nombre)}</b> ({esc(modelo)})")
    elif tag == "ser":
        d["serie"] = int(val)
        await ev.edit(f"📚 Serie: <b>{esc(NAMES.get(('ser', int(val))))}</b>")
    elif tag == "tmp":
        d["temporada"] = int(val)
        await ev.edit(f"🗂 Temporada: <b>{esc(NAMES.get(('tmp', int(val))))}</b>")
    elif data == "por:thumb":
        p = DOWNLOADS / f"thumb_{uid}.jpg"
        try:
            r = await bot.download_media(s["msg"], file=str(p), thumb=-1)
            d["portada"] = str(r) if r else None
        except Exception:
            d["portada"] = None
        await ev.edit("🖼 Portada: " + ("miniatura del video" if d["portada"] else "no disponible, sin portada"))
    elif tag == "skip":
        d[val] = None if val in ("ano", "portada", "descripcion") else d.get(val)
        await ev.edit("⏭ Omitido")
    else:
        return await ev.answer()
    await ev.answer()
    await pedir(uid)


# ---------------------------------------------------------------- cola de subidas
async def procesar(job):
    uid, msg, d = job["uid"], job["msg"], job["d"]
    st = await bot.send_message(uid, f"⏳ Preparando <b>{esc(d['nombre'])}</b>…")
    loop = asyncio.get_running_loop()
    ruta = None
    try:
        # 1) descargar de Telegram
        prog = Progreso(st, f"📥 Descargando <b>{esc(d['nombre'])}</b>")
        ext = ".mp4"
        if msg.document:
            n = next((a.file_name for a in msg.document.attributes if hasattr(a, "file_name")), "")
            ext = os.path.splitext(n)[1] or ext
        ruta = str(DOWNLOADS / f"{uid}_{msg.id}{ext}")
        await bot.download_media(msg, file=ruta, progress_callback=lambda a, b: asyncio.ensure_future(prog.up(a, b)))

        # 2) subir a Picta (almacenamiento)
        prog2 = Progreso(st, f"📤 Subiendo a Picta <b>{esc(d['nombre'])}</b>")

        def cb(a, b):
            asyncio.run_coroutine_threadsafe(prog2.up(a, b), loop)

        key = await P(picta.subir_archivo, ruta, cb)

        # 3) validar y crear
        await st.edit("🧩 Creando publicación…")
        extra = {}
        if d["modelo"] == "capitulo":
            extra.update(serie=d.get("serie"), temporada=d.get("temporada"), numero=d.get("numero"))
        else:
            extra.update(genero=d.get("genero"), ano=d.get("ano"))
        kw = dict(nombre=d["nombre"], canal=d["canal"], tipologia=d["tipologia"], tipo=d["modelo"],
                  key=key, descripcion=d.get("descripcion") or "", extra=extra)
        ok, resp = await P(picta.validar, **kw)
        if not ok:
            raise PictaError(f"Picta rechazó los campos: {json.dumps(resp, ensure_ascii=False)[:600]}")
        pub = await P(lambda: picta.crear(portada=d.get("portada"), **kw))

        if AUTO_CONVERT:
            await P(picta.convertir, key)

        slug = pub.get("slug_url_raw") or pub.get("slug_url") or ""
        link = f"https://www.picta.cu/medias/{slug}" if slug else f"(id {pub.get('id')})"
        await st.edit(f"✅ <b>Publicado</b>\n🎬 {esc(d['nombre'])}\n🔗 {esc(link)}")
    except Exception as e:
        log.exception("fallo subiendo")
        await st.edit(f"❌ <b>Error</b> con {esc(d['nombre'])}:\n<code>{esc(str(e)[:900])}</code>")
    finally:
        for p in (ruta, d.get("portada")):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


async def worker():
    while True:
        job = await QUEUE.get()
        try:
            await procesar(job)
        except Exception:
            log.exception("worker")
        finally:
            QUEUE.task_done()


# ---------------------------------------------------------------- arranque
async def main():
    global picta
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS vacío: nadie podrá usar el bot.")
    picta = await asyncio.to_thread(Picta, os.environ["PICTA_USER"], os.environ["PICTA_PASS"])
    log.info("Login en Picta correcto")
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    log.info("Bot listo: @%s", me.username)
    asyncio.create_task(worker())
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())

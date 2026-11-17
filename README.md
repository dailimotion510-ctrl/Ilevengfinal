# Pictaileven (bot de Telegram -> Picta)

## Puesta en marcha en Codespaces
1. Sube esta carpeta a un repositorio **privado** de GitHub y abre un Codespace.
2. Copia `.env.example` a `.env` y rellénalo:
   `cp .env.example .env` y edítalo (API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS, PICTA_USER, PICTA_PASS).
3. Instala y arranca:
   ```
   pip install -r requirements.txt
   nohup python bot.py >> bot.log 2>&1 &
   tail -f bot.log
   ```
   Con el `.devcontainer` incluido el bot se arranca solo cada vez que el Codespace se enciende.

## Uso
Manda un video al bot -> nombre, tipología, géneros, año, descripción, portada -> **Publicar**.
Comandos: `/panel`, `/canal`, `/tipologias`, `/cola`, `/cancelar`.

## Notas
- Tu ID numérico lo ves escribiéndole al bot: si no estás en `ADMIN_IDS` te lo muestra.
- Los videos se borran del disco al terminar la subida.
- Para ver errores: `tail -n 50 bot.log`. Para reiniciar: `pkill -f "python bot.py"; nohup python bot.py >> bot.log 2>&1 &`
- Codespaces se apaga por inactividad (ajusta el tiempo en GitHub -> Settings -> Codespaces).
- Nunca subas `.env` ni el archivo `.session` a GitHub (ya están en `.gitignore`).
- Los endpoints de Picta salen del código del panel y no están probados: si Picta rechaza
  una publicación, el bot te muestra el error exacto del servidor.

# CLIP BAEZ AI — MVP inicial

Esta primera versión web permite pegar un enlace de YouTube, descargar el video con `yt-dlp`, dividirlo en segmentos de duración elegida, convertirlos a formato vertical 9:16 con FFmpeg y reproducir/descargar los clips.

## Requisitos

- Python 3.10 o superior
- FFmpeg instalado y disponible en PATH (`ffmpeg` y `ffprobe`)
- Conexión a Internet
- Acceso autorizado al contenido de YouTube. Respeta derechos de autor y condiciones de la plataforma.

## Instalación en Windows

1. Instala Python.
2. Instala FFmpeg y asegúrate de que `ffmpeg` y `ffprobe` funcionen desde CMD.
3. Abre una terminal dentro de esta carpeta.
4. Ejecuta:

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app:app --reload
```

5. Abre `http://127.0.0.1:8000` en el navegador.

## Alcance real de esta versión

- Acepta enlaces de YouTube y youtu.be.
- Genera hasta 18 segmentos consecutivos.
- Convierte los segmentos a 1080x1920.
- Incluye una galería local de clips.

## Todavía no incluido

- Detección semántica de los momentos más virales (la versión inicial corta por intervalos, no decide qué es viral).
- Subtítulos transcritos y quemados dentro del video.
- Registro/login y aislamiento de archivos por usuario.
- Procesamiento de directos en vivo.
- Despliegue en un servidor público.

Estos módulos se deben agregar y probar por etapas antes de publicar la aplicación.

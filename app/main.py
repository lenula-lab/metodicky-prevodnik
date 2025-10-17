
import os
import io
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, UploadFile, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.utils.generate import process_inputs_and_generate, GenerationError

app = FastAPI(title="Metodický převodník – výběrová řízení")

BASE = Path(__file__).resolve().parent
OUTPUTS = BASE / "outputs"
OUTPUTS.mkdir(exist_ok=True, parents=True)

app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload", response_class=JSONResponse)
async def upload(
    request: Request,
    zipfile_input: Optional[UploadFile] = File(default=None),
    files: Optional[List[UploadFile]] = File(default=None),
    audience: str = Form(default="personální útvary"),
    style: str = Form(default="návod"),
):
    try:
        work_id = os.urandom(8).hex()
        workdir = OUTPUTS / work_id
        workdir.mkdir(parents=True, exist_ok=True)

        input_dir = workdir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)

        if zipfile_input is not None and getattr(zipfile_input, "filename", ""):
            data = await zipfile_input.read()
            if data:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(input_dir)

        if files:
            for f in files:
                if not f or not getattr(f, "filename", ""):
                    continue
                content = await f.read()
                if not content:
                    continue
                safe_name = Path(f.filename).name
                (input_dir / safe_name).write_bytes(content)
                
        result = await process_inputs_and_generate(
            input_dir=input_dir,
            output_dir=workdir,
            audience=audience,
            style=style,
        )

        return JSONResponse({
            "status": "ok",
            "docx_path": f"/download/{work_id}/{Path(result['docx']).name}",
            "png_path": f"/download/{work_id}/{Path(result['png']).name}",
            "audio_path": f"/download/{work_id}/{Path(result['audio']).name}" if result.get("audio") else None,
            "work_id": work_id,
        })
    except GenerationError as ge:
        return JSONResponse({"status": "error", "detail": str(ge)}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "detail": f"Unexpected error: {e}"}, status_code=500)

@app.get("/download/{work_id}/{fname}")
async def download(work_id: str, fname: str):
    path = OUTPUTS / work_id / fname
    if not path.exists():
        return JSONResponse({"status": "error", "detail": "File not found"}, status_code=404)
    return FileResponse(str(path))

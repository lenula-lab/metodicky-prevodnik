
import os
from pathlib import Path
from typing import Dict, Optional

from docx import Document as DocxDocument
import pdfminer.high_level

import asyncio
import httpx

import matplotlib.pyplot as plt
import matplotlib.patches as patches

USE_AZURE_SPEECH = bool(os.getenv("AZURE_SPEECH_KEY"))

class GenerationError(Exception):
    pass

def _read_text_from_path(p: Path) -> str:
    try:
        if p.suffix.lower() == ".docx":
            d = DocxDocument(str(p))
            return "\n".join([para.text for para in d.paragraphs])
        if p.suffix.lower() == ".pdf":
            return pdfminer.high_level.extract_text(str(p))
        if p.suffix.lower() in [".txt", ".md"]:
            return p.read_text(encoding="utf-8", errors="ignore")
        return ""
    except Exception:
        return ""

def _collect_corpus(input_dir: Path) -> str:
    texts = []
    for p in input_dir.rglob("*"):
        if p.is_file():
            texts.append(_read_text_from_path(p))
    corpus = "\n\n".join(t for t in texts if t.strip())
    if not corpus.strip():
        raise GenerationError("Nebyl nalezen žádný čitelný text (PDF/DOCX/TXT).")
    return corpus[:200000]

def _build_prompt(corpus: str, audience: str, style: str) -> str:
    return f"""
Převeď následující metodický pokyn do srozumitelné češtiny pro „{audience}“.
Styl: „{style}“, krokový návod bez anglicismů. Vrať 4 části v tomto pořadí:

1) Úvod – smysl pokynu (max 6 vět).
2) Fáze výběrového řízení – názvy fází (bez čísel) s krátkým popisem.
3) Kroky a odpovědnosti – tabulka v markdownu se sloupci: Kdo | Co | Jak | Do kdy | Vzor (P1–P23).
4) Na co si dát pozor – 5–8 bodů.

Zachovej neutrální tón. Odkazuj na vzory P1–P23, pokud z textu vyplývají.
Korpus:
{corpus}
"""

async def _call_llm(prompt: str) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    azure_key = os.getenv("AZURE_OPENAI_KEY")
    if azure_key and os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT"):
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT").rstrip("/")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-15-preview"
        headers = {"api-key": azure_key, "Content-Type": "application/json"}
        payload = {
            "messages": [
                {"role": "system", "content": "Jsi konzervativní asistent. Převádíš metodické pokyny do srozumitelné češtiny bez anglicismů pro pracovníky personálních útvarů."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 2200,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
    elif openai_key:
        headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Jsi konzervativní asistent. Převádíš metodické pokyny do srozumitelné češtiny bez anglicismů pro pracovníky personálních útvarů."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 2200,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
    else:
        return "POZNÁMKA: LLM není nakonfigurováno. Uveďte API klíče. Náhled shrnutí:\n\n" + prompt[:1500]

def _make_docx(text: str, out_path: Path):
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    title = doc.add_paragraph()
    r = title.add_run("Metodický pokyn – výběrová řízení\n(zjednodušený výklad pro personální útvary)")
    r.bold = True
    r.font.size = Pt(16)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for line in text.splitlines():
        if line.strip().startswith("# "):
            doc.add_heading(line.strip("# ").strip(), level=1)
        elif line.strip().startswith("## "):
            doc.add_heading(line.strip("# ").strip(), level=2)
        else:
            doc.add_paragraph(line)
    doc.save(out_path)

def _make_png(output_dir: Path):
    png_path = output_dir / "schema.png"
    steps = [
        "Příprava VŘ","Vyhlášení a oznámení","Příjem a kontrola žádostí","Komise a pravidla",
        "Pozvánky a zkoušky","Pohovor / ověřování","Hodnocení a protokoly","Dohoda / zrušení",
        "Rozhodnutí o přijetí","Nástup a služební slib"
    ]
    fig, ax = plt.subplots(figsize=(9,13))
    ax.axis('off')
    W, H = 0.8, 0.07; x = 0.1
    ys = [0.9 - i*0.08 for i in range(len(steps))]
    for i, (y, text) in enumerate(zip(ys, steps)):
        rect = patches.FancyBboxPatch((x, y), W, H, boxstyle="round,pad=0.02,rounding_size=0.02", linewidth=1, edgecolor="black", facecolor="white")
        ax.add_patch(rect)
        ax.text(x + W/2, y + H/2, text, ha='center', va='center', wrap=True, fontsize=10)
        if i < len(ys)-1:
            ax.annotate("", xy=(x+W/2, y), xytext=(x+W/2, ys[i+1]+H), arrowprops=dict(arrowstyle="->", lw=1))
    fig.savefig(png_path, dpi=200, bbox_inches="tight")
    return str(png_path)

def _make_tts(text: str, out_path: Path) -> Optional[str]:
    if not USE_AZURE_SPEECH:
        return None
    try:
        import azure.cognitiveservices.speech as speechsdk
        speech_config = speechsdk.SpeechConfig(subscription=os.getenv("AZURE_SPEECH_KEY"), region=os.getenv("AZURE_SPEECH_REGION", "westeurope"))
        speech_config.speech_synthesis_language = "cs-CZ"
        speech_config.speech_synthesis_voice_name = os.getenv("AZURE_SPEECH_VOICE", "cs-CZ-AntoninNeural")
        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(out_path))
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        synthesizer.speak_text_async(text[:5000]).get()
        return str(out_path)
    except Exception:
        return None

async def process_inputs_and_generate(input_dir: Path, output_dir: Path, audience: str, style: str) -> Dict[str, str]:
    corpus = _collect_corpus(input_dir)
    prompt = _build_prompt(corpus, audience, style)
    text = await _call_llm(prompt)

    docx_path = output_dir / "vystup_navod.docx"
    _make_docx(text, docx_path)
    png_path = _make_png(output_dir)
    audio_path = _make_tts(text, output_dir / "shrnutí.mp3")

    return {"docx": str(docx_path), "png": str(png_path), "audio": audio_path}

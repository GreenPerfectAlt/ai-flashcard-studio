import os
import io
import re
import tempfile
import urllib.request
from html.parser import HTMLParser
from datetime import datetime
from typing import List, Optional, Dict
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, func, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

from llm_config import ask_litert, init_engine, unload_engine

# ---------- База данных (Авто-путь + WAL режим против блокировок) ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "flashcards.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Deck(Base):
    __tablename__ = "decks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    cards = relationship("Card", back_populates="deck", cascade="all, delete-orphan")

class Card(Base):
    __tablename__ = "cards"
    id = Column(Integer, primary_key=True, index=True)
    front = Column(String)
    back = Column(String)
    source_quote = Column(String, nullable=True)
    mnemonic = Column(String, nullable=True)
    status = Column(String, default="inbox")
    due_date = Column(DateTime, nullable=True)
    order = Column(Integer, default=0)
    deck_id = Column(Integer, ForeignKey("decks.id"))
    created_at = Column(DateTime, default=func.now())
    deck = relationship("Deck", back_populates="cards")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_cyrillic_font() -> Optional[str]:
    """Автоматически ищет в системе TTF-шрифт с полноценной поддержкой кириллицы"""
    possible_paths = [
        # Windows
        "C:\\Windows\\Fonts\\Arial.ttf",
        "C:\\Windows\\Fonts\\Calibri.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # macOS
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None

# ---------- Глобальный трекер прогресса ----------
task_progress: Dict[int, dict] = {}

# ---------- HTML Парсер ----------
class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.ignore_tags = {'script', 'style', 'head', 'nav', 'footer', 'header', 'noscript', 'aside'}
        self.tag_stack = []

    def handle_starttag(self, tag, attrs):
        self.tag_stack.append(tag)

    def handle_endtag(self, tag):
        if self.tag_stack:
            self.tag_stack.pop()

    def handle_data(self, data):
        if not any(t in self.ignore_tags for t in self.tag_stack):
            text = data.strip()
            if text:
                self.result.append(text)

    def get_text(self):
        return " ".join(self.result)

# ---------- Pydantic ----------
class DeckCreate(BaseModel):
    name: str

class DeckUpdate(BaseModel):
    name: str

class CardUpdate(BaseModel):
    front: str
    back: str
    source_quote: Optional[str] = ""
    mnemonic: Optional[str] = ""
    status: Optional[str] = None
    due_date: Optional[str] = None

class TextRequest(BaseModel):
    content: str

# ---------- FastAPI ----------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

current_model = "gemma-4-E2B-it"

@app.on_event("startup")
async def startup_event():
    try:
        init_engine(model_name=current_model)
    except Exception as e:
        print(f"[STARTUP WARN] Не удалось автоматически загрузить модель: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    unload_engine()

def split_text_into_chunks(text: str, chunk_size: int = 3000) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    current = ""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for sent in sentences:
        if len(current) + len(sent) + 2 <= chunk_size:
            current += sent + " "
        else:
            if current:
                chunks.append(current.strip())
            current = sent + " "
    if current:
        chunks.append(current.strip())
    return chunks

def generate_prompt_for_chunk(chunk: str) -> str:
    return (
        "<bos><start_of_turn>user\n"
        "Ты — ведущий эксперт по мнемонике, методологии эффективного обучения и интервальным повторениям.\n"
        "Проанализируй текст и извлеки ключевые факты в виде массива JSON-карточек.\n\n"
        "ЖЕСТКИЕ ПРАВИЛА:\n"
        "1. Атомарность: Одна карточка — ОДИН изолированный факт. Никаких списков в ответах.\n"
        "2. Однозначность: Четкий, короткий вопрос.\n"
        "3. Мнемоника: Поле 'mnemonic' должно содержать яркую ассоциацию или каламбур.\n"
        "4. Цитата: Поле 'source_quote' содержит исходный фрагмент текста.\n"
        "5. Экранирование: Никогда не используй символы кавычек внутри значений параметров front, back и mnemonic, используй синонимы, чтобы не ломать JSON.\n\n"
        "Выведи ответ СТРОГО в виде валидного JSON-массива без markdown разметки и без лишних слов:\n"
        "[\n"
        "  {\n"
        "    \"front\": \"Вопрос?\",\n"
        "    \"back\": \"Ответ.\",\n"
        "    \"source_quote\": \"Цитата.\",\n"
        "    \"mnemonic\": \"Ассоциация.\"\n"
        "  }\n"
        "]\n\n"
        f"ТЕКСТ ДЛЯ АНАЛИЗА:\n{chunk}<end_of_turn>\n<start_of_turn>model\n"
    )

# ---------- Фоновый воркер генерации ----------
async def background_card_generator(deck_id: int, text: str):
    db = SessionLocal()
    try:
        chunks = split_text_into_chunks(text, chunk_size=3000)
        task_progress[deck_id] = {"status": "processing", "current": 0, "total": len(chunks), "message": "Обработка текста..."}
        
        for chunk in chunks:
            try:
                cards = await ask_litert(generate_prompt_for_chunk(chunk))
                if not cards:
                    task_progress[deck_id]["current"] += 1
                    continue
                
                # Находим минимальный текущий order в колоде и вычитаем из него
                min_order = db.query(func.min(Card.order)).filter(Card.deck_id == deck_id).scalar() or 0
                
                for i, card in enumerate(cards):
                    db.add(Card(
                        front=card["front"],
                        back=card["back"],
                        source_quote=card.get("source_quote", ""),
                        mnemonic=card.get("mnemonic", ""),
                        deck_id=deck_id,
                        status="inbox",
                        order=min_order - (len(cards) - i)
                    ))
                db.commit()
                task_progress[deck_id]["current"] += 1
            except Exception as chunk_error:
                print(f"[Фоновый процесс] Критическая ошибка чанка: {chunk_error}")
                task_progress[deck_id] = {
                    "status": "error", 
                    "message": f"Ошибка генерации: {str(chunk_error)}"
                }
                return
                
        if task_progress[deck_id]["status"] != "error":
            task_progress[deck_id]["status"] = "completed"
    except Exception as fatal_error:
        print(f"[Фоновый процесс] Фатальный сбой: {fatal_error}")
        task_progress[deck_id] = {"status": "error", "message": f"Критический сбой системы: {fatal_error}"}
    finally:
        db.close()

# ---------- Эндпоинты API ----------
@app.get("/api/decks/{deck_id}/progress")
async def get_deck_progress(deck_id: int):
    return task_progress.get(deck_id, {"status": "idle", "current": 0, "total": 0, "message": ""})

@app.get("/api/decks")
async def get_decks(db: Session = Depends(get_db)):
    decks = db.query(Deck).order_by(Deck.created_at.desc()).all()
    return [{"id": d.id, "name": d.name, "created_at": d.created_at.isoformat(), "card_count": len(d.cards)} for d in decks]

@app.post("/api/decks")
async def create_deck(deck_data: DeckCreate, db: Session = Depends(get_db)):
    deck = Deck(name=deck_data.name)
    db.add(deck)
    db.commit()
    db.refresh(deck)
    return {"id": deck.id, "name": deck.name, "created_at": deck.created_at.isoformat()}

@app.put("/api/decks/{deck_id}")
async def update_deck(deck_id: int, deck_data: DeckUpdate, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    deck.name = deck_data.name
    db.commit()
    return {"status": "success"}

@app.delete("/api/decks/{deck_id}")
async def delete_deck(deck_id: int, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    db.delete(deck)
    db.commit()
    return {"status": "success"}

@app.get("/api/decks/{deck_id}/cards")
async def get_cards(deck_id: int, db: Session = Depends(get_db)):
    # 🔥 Главное исправление: Сортируем сначала по дате создания (свежие строго сверху), 
    # а внутри одной пачки сохраняем правильную последовательность генерации по Card.order.
    cards = db.query(Card).filter(Card.deck_id == deck_id).order_by(Card.created_at.desc(), Card.order.asc()).all()
    return [{
        "id": c.id,
        "front": c.front,
        "back": c.back,
        "source_quote": c.source_quote or "",
        "mnemonic": c.mnemonic or "",
        "status": c.status,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "created_at": c.created_at.isoformat() if c.created_at else None
    } for c in cards]

@app.post("/api/decks/{deck_id}/cards/generate")
async def generate_cards(deck_id: int, request: TextRequest, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    bg_tasks.add_task(background_card_generator, deck_id, request.content)
    return {"status": "processing"}

@app.post("/api/decks/{deck_id}/cards/generate-from-file")
async def generate_cards_from_file(deck_id: int, bg_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    content = await file.read()
    filename = file.filename.lower()
    text = ""
    if filename.endswith('.pdf'):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка PDF: {e}")
    elif filename.endswith('.docx'):
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            text = '\n'.join([p.text for p in doc.paragraphs])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка DOCX: {e}")
    elif filename.endswith('.txt'):
        text = content.decode('utf-8', errors='ignore')
    elif filename.endswith('.epub'):
        try:
            import ebooklib
            from ebooklib import epub
            book = epub.read_epub(io.BytesIO(content))
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    text += item.get_body_content().decode('utf-8', errors='ignore') + "\n"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка EPUB: {e}")
    elif filename.endswith('.fb2'):
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(io.BytesIO(content))
            root = tree.getroot()
            ns = {'fb2': 'http://www.gribuser.ru/xml/fictionbook/2.0'}
            body = root.find('.//fb2:body', ns)
            if body is not None:
                paragraphs = body.findall('.//fb2:p', ns)
                text = "\n".join([p.text for p in paragraphs if p.text])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка FB2: {e}")
    else:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат")
        
    if not text.strip():
        raise HTTPException(status_code=400, detail="Текст в файле не найден")
        
    bg_tasks.add_task(background_card_generator, deck_id, text)
    return {"status": "processing"}

@app.post("/api/decks/{deck_id}/cards/generate-from-youtube")
async def generate_from_youtube(deck_id: int, request: dict, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    url = request.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
    
    video_id = None
    if "v=" in url:
        video_id = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        video_id = url.split("youtu.be/")[1].split("?")[0]
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['ru', 'en'])
        except:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_active_transcript(['ru', 'en', 'uk']).fetch()
        full_text = " ".join([seg['text'] for seg in transcript])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка субтитров YouTube: {str(e)}")
    
    bg_tasks.add_task(background_card_generator, deck_id, full_text)
    return {"status": "processing"}

@app.post("/api/decks/{deck_id}/cards/generate-from-url")
async def generate_from_url(deck_id: int, request: dict, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    url = request.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
        
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
        
        extractor = HTMLTextExtractor()
        extractor.feed(html_content)
        text = extractor.get_text()
        text = re.sub(r'\s+', ' ', text).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка парсинга сайта: {str(e)}")
        
    if not text or len(text) < 50:
        raise HTTPException(status_code=400, detail="Не удалось извлечь текст с указанного адреса")
        
    bg_tasks.add_task(background_card_generator, deck_id, text)
    return {"status": "processing"}

@app.put("/api/cards/{card_id}")
async def update_card(card_id: int, card_data: CardUpdate, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if card_data.front is not None:
        card.front = card_data.front
    if card_data.back is not None:
        card.back = card_data.back
    if card_data.source_quote is not None:
        card.source_quote = card_data.source_quote
    if card_data.mnemonic is not None:
        card.mnemonic = card_data.mnemonic
    if card_data.status is not None:
        card.status = card_data.status
    if card_data.due_date is not None:
        try:
            card.due_date = datetime.fromisoformat(card_data.due_date.replace('Z', '+00:00'))
        except:
            card.due_date = None
    db.commit()
    return {"status": "success"}

@app.delete("/api/cards/{card_id}")
async def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    db.delete(card)
    db.commit()
    return {"status": "success"}

@app.post("/api/cards/reorder")
async def reorder_cards(data: dict, db: Session = Depends(get_db)):
    deck_id = data.get("deck_id")
    card_ids = data.get("card_ids")
    if not deck_id or not card_ids:
        raise HTTPException(status_code=400, detail="Missing deck_id or card_ids")
    for idx, card_id in enumerate(card_ids):
        db.query(Card).filter(Card.id == card_id, Card.deck_id == deck_id).update({"order": idx})
    db.commit()
    return {"status": "success"}

@app.patch("/api/cards/{card_id}/status")
async def update_card_status(card_id: int, status_data: dict, db: Session = Depends(get_db)):
    new_status = status_data.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing status")
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    card.status = new_status
    db.commit()
    return {"status": "success"}

@app.get("/api/decks/{deck_id}/export/anki")
async def export_deck_anki(deck_id: int, db: Session = Depends(get_db)):
    try:
        import genanki
    except ImportError:
        raise HTTPException(status_code=500, detail="Установите genanki: pip install genanki")
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    cards = db.query(Card).filter(Card.deck_id == deck_id).order_by(Card.order).all()
    if not cards:
        raise HTTPException(status_code=404, detail="No cards")
        
    model_id = 1607392319
    model = genanki.Model(
        model_id,
        'Flashcard Model',
        fields=[{'name': 'Question'}, {'name': 'Answer'}, {'name': 'Source'}, {'name': 'Mnemonic'}],
        templates=[{
            'name': 'Card 1',
            'qfmt': '{{Question}}',
            'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}<br><br><i>Источник: {{Source}}</i><br><i>Мнемоника: {{Mnemonic}}</i>',
        }]
    )
    
    anki_deck_id = abs(hash(deck.name)) % (10 ** 10)
    anki_deck = genanki.Deck(anki_deck_id, deck.name)
    
    for card in cards:
        anki_deck.add_note(genanki.Note(model=model, fields=[
            card.front or '', card.back or '', card.source_quote or '', card.mnemonic or ''
        ]))
        
    package = genanki.Package(anki_deck)
    with tempfile.NamedTemporaryFile(suffix=".apkg", delete=False) as tmp:
        tmp_path = tmp.name
        
    try:
        package.write_to_file(tmp_path)
        with open(tmp_path, "rb") as f:
            file_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            
    encoded_filename = quote(f"{deck.name}.apkg")
    return Response(
        content=file_bytes,
        media_type="application/apkg",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )

@app.get("/api/decks/{deck_id}/export/pdf")
async def export_deck_pdf(deck_id: int, db: Session = Depends(get_db)):
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        raise HTTPException(status_code=500, detail="Установите reportlab: pip install reportlab")
        
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    cards = db.query(Card).filter(Card.deck_id == deck_id).order_by(Card.order).all()
    if not cards:
        raise HTTPException(status_code=404, detail="No cards")
        
    font_path = get_cyrillic_font()
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont('Cyrillic', font_path))
            font_name = 'Cyrillic'
        except:
            font_name = 'Helvetica'
    else:
        font_name = 'Helvetica'
        
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    y = height - 40
    c.setFont(font_name, 14)
    c.drawString(50, y, f"Колода: {deck.name}")
    y -= 30
    
    for idx, card in enumerate(cards, 1):
        if y < 100:
            c.showPage()
            y = height - 40
            
        c.setFont(font_name, 11)
        front_lines = simpleSplit(f"Карточка {idx}: {card.front}", font_name, 11, width - 100)
        for line in front_lines:
            if y < 40:
                c.showPage()
                y = height - 40
            c.setFont(font_name, 11)
            c.drawString(50, y, line)
            y -= 14
        
        lines = simpleSplit(card.back or "", font_name, 10, width - 100)
        for line in lines:
            if y < 40:
                c.showPage()
                y = height - 40
            c.setFont(font_name, 10)
            c.drawString(50, y, line)
            y -= 14
            
        if card.source_quote:
            y -= 4
            src_lines = simpleSplit(f"Источник: {card.source_quote}", font_name, 9, width - 100)
            for line in src_lines:
                if y < 40:
                    c.showPage()
                    y = height - 40
                c.setFont(font_name, 9)
                c.drawString(50, y, line)
                y -= 12
                
        if card.mnemonic:
            y -= 4
            mne_lines = simpleSplit(f"Мнемоника: {card.mnemonic}", font_name, 9, width - 100)
            for line in mne_lines:
                if y < 40:
                    c.showPage()
                    y = height - 40
                c.setFont(font_name, 9)
                c.drawString(50, y, line)
                y -= 12
                
        y -= 15
        
    c.save()
    buffer.seek(0)
    
    encoded_filename = quote(f"{deck.name}.pdf")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        "Access-Control-Expose-Headers": "Content-Disposition"
    }
    return Response(content=buffer.read(), media_type="application/pdf", headers=headers)

# ---------- Управление моделью ----------
@app.post("/api/model/switch")
async def switch_model(model_name: str):
    global current_model
    if model_name not in ["gemma-4-E2B-it", "supergemma4-e4b-abliterated"]:
        raise HTTPException(status_code=400, detail="Unknown model")
    current_model = model_name
    unload_engine()
    init_engine(model_name=current_model)
    return {"status": "success", "current_model": current_model}

@app.get("/api/model/current")
async def get_current_model():
    return {"current_model": current_model}

@app.get("/")
async def root():
    return FileResponse("index.html")
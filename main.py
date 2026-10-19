from fastapi import FastAPI, Depends, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db, SessionLocal, engine, Base
from models import BarberoModel, ServicioCatalogoModel, ServicioDiarioModel
from pydantic import BaseModel
from typing import Optional
import io
import csv

# Crear las tablas físicas si no existen
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Casa Peter Frank")
templates = Jinja2Templates(directory="templates")

# Definición del esquema Pydantic para la validación de entrada
class ServicioDiarioCreate(BaseModel):
    cliente_nombre: str
    barbero_id: int
    servicio_id: int
    metodo_pago: str
    precio_cobrado: float
    fecha_hora: Optional[datetime] = None

# Evento de inicio para poblar datos semilla automáticamente si está vacío en la nube
@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    try:
        # Verificar si hay barberos, si no, crearlos
        if db.query(BarberoModel).count() == 0:
            for b in ["Peter", "Frank"]:
                db.add(BarberoModel(nombre=b))
            print("Barberos semilla creados.")

        # Verificar si hay catálogo, si no, crearlo
        if db.query(ServicioCatalogoModel).count() == 0:
            servicios = [
                "Corte", "Corte + barba", "Corte + barba + ceja",
                "Corte + ceja", "Barba + ceja", "Corte a tijera", "Tapper"
            ]
            for s in servicios:
                db.add(ServicioCatalogoModel(nombre_servicio=s))
            print("Catálogo semilla creado.")
            
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error en startup: {e}")
    finally:
        db.close()

@app.get("/", include_in_schema=False)
def redirectToApp():
    return RedirectResponse(url="/app")

@app.get("/app", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/barberos/")
def listar_barberos(db: Session = Depends(get_db)):
    return db.query(BarberoModel).all()

@app.get("/servicios-catalogo/")
def listar_catalogo(db: Session = Depends(get_db)):
    return db.query(ServicioCatalogoModel).all()

@app.post("/servicios-diarios/", status_code=status.HTTP_201_CREATED)
def crear_servicio_diario(servicio: ServicioDiarioCreate, db: Session = Depends(get_db)):
    neto = servicio.precio_cobrado
    if servicio.metodo_pago.lower() == "tarjeta":
        neto = servicio.precio_cobrado * (1 - 0.04)  # Comisión bancaria actualizada al 4%

    nuevo_registro = ServicioDiarioModel(
        cliente_nombre=servicio.cliente_nombre,
        barbero_id=servicio.barbero_id,
        servicio_id=servicio.servicio_id,
        metodo_pago=servicio.metodo_pago,
        precio_cobrado=servicio.precio_cobrado,
        total_cobrado_neto=round(neto, 2),
        fecha_hora=servicio.fecha_hora if servicio.fecha_hora else datetime.utcnow()
    )

    db.add(nuevo_registro)
    db.commit()
    db.refresh(nuevo_registro)
    
    return {
        "mensaje": "Registrado con éxito", 
        "total_cobrado_neto": nuevo_registro.total_cobrado_neto
    }

@app.get("/servicios-diarios/")
def listar_servicios_diarios(
    db: Session = Depends(get_db),
    mes: Optional[int] = Query(None, description="Filtrar por mes (1-12)"),
    anio: Optional[int] = Query(None, description="Filtrar por año (ej. 2026)")
):
    query = db.query(ServicioDiarioModel)
    resultados = query.all()
    
    datos_formateados = []
    for reg in resultados:
        if mes and reg.fecha_hora.month != mes:
            continue
        if anio and reg.fecha_hora.year != anio:
            continue
            
        barbero = db.query(BarberoModel).filter_by(id=reg.barbero_id).first()
        servicio = db.query(ServicioCatalogoModel).filter_by(id=reg.servicio_id).first()
        
        datos_formateados.append({
            "id": reg.id,
            "fecha_hora": reg.fecha_hora.strftime("%Y-%m-%d %H:%M"),
            "cliente_nombre": reg.cliente_nombre,
            "barbero": barbero.nombre if barbero else "Desconocido",
            "servicio": servicio.nombre_servicio if servicio else "Desconocido",
            "metodo_pago": reg.metodo_pago,
            "precio_cobrado": reg.precio_cobrado,
            "total_cobrado_neto": reg.total_cobrado_neto
        })
        
    return datos_formateados

@app.get("/exportar-csv/")
def exportar_csv(db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(["ID", "Fecha y Hora", "Cliente", "Barbero", "Servicio", "Método de Pago", "Precio Cobrado", "Total Neto"])
    
    registros = db.query(ServicioDiarioModel).all()
    for reg in registros:
        barbero = db.query(BarberoModel).filter_by(id=reg.barbero_id).first()
        servicio = db.query(ServicioCatalogoModel).filter_by(id=reg.servicio_id).first()
        
        writer.writerow([
            reg.id,
            reg.fecha_hora.strftime("%Y-%m-%d %H:%M"),
            reg.cliente_nombre,
            barbero.nombre if barbero else "",
            servicio.nombre_servicio if servicio else "",
            reg.metodo_pago,
            reg.precio_cobrado,
            reg.total_cobrado_neto
        ])
        
    output.seek(0)
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_casa_peter_frank.csv"}
    )

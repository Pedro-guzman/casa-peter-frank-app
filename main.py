from fastapi import FastAPI, Depends, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from database import get_db, SessionLocal, engine, Base
from models import BarberoModel, ServicioCatalogoModel, ServicioDiarioModel, CitaModel
from pydantic import BaseModel
from typing import Optional
import io
import csv

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Casa Peter Frank")
templates = Jinja2Templates(directory="templates")

class ServicioDiarioCreate(BaseModel):
    cliente_nombre: str
    barbero_id: int
    servicio_id: int
    metodo_pago: str
    precio_cobrado: float
    fecha_hora: Optional[datetime] = None

class CitaCreate(BaseModel):
    cliente_nombre: str
    fecha: str  # YYYY-MM-DD
    hora: str   # HH:MM
    anticipo: Optional[str] = "No"
    barbero_id: Optional[int] = None

@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    try:
        if db.query(BarberoModel).count() == 0:
            for b in ["Peter", "Frank"]:
                db.add(BarberoModel(nombre=b))
        if db.query(ServicioCatalogoModel).count() == 0:
            for s in ["Corte", "Corte + barba", "Corte + barba + ceja", "Corte + ceja", "Barba + ceja", "Corte a tijera", "Tapper"]:
                db.add(ServicioCatalogoModel(nombre_servicio=s))
        db.commit()
    except Exception as e:
        db.rollback()
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

# --- SERVICIOS DIARIOS ---

@app.post("/servicios-diarios/", status_code=status.HTTP_201_CREATED)
def crear_servicio_diario(servicio: ServicioDiarioCreate, db: Session = Depends(get_db)):
    neto = servicio.precio_cobrado
    if servicio.metodo_pago.lower() == "tarjeta":
        neto = servicio.precio_cobrado * (1 - 0.04)

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
    return {"mensaje": "Registrado con éxito", "total_cobrado_neto": nuevo_registro.total_cobrado_neto}

@app.put("/servicios-diarios/{id}")
def editar_servicio_diario(id: int, servicio: ServicioDiarioCreate, db: Session = Depends(get_db)):
    reg = db.query(ServicioDiarioModel).filter_by(id=id).first()
    if not reg:
        return {"error": "Registro no encontrado"}, 404

    neto = servicio.precio_cobrado
    if servicio.metodo_pago.lower() == "tarjeta":
        neto = servicio.precio_cobrado * (1 - 0.04)

    reg.cliente_nombre = servicio.cliente_nombre
    reg.barbero_id = servicio.barbero_id
    reg.servicio_id = servicio.servicio_id
    reg.metodo_pago = servicio.metodo_pago
    reg.precio_cobrado = servicio.precio_cobrado
    reg.total_cobrado_neto = round(neto, 2)
    if servicio.fecha_hora:
        reg.fecha_hora = servicio.fecha_hora

    db.commit()
    return {"mensaje": "Registro actualizado con éxito"}

@app.delete("/servicios-diarios/{id}")
def eliminar_servicio_diario(id: int, db: Session = Depends(get_db)):
    reg = db.query(ServicioDiarioModel).filter_by(id=id).first()
    if not reg:
        return {"error": "Registro no encontrado"}, 404
    db.delete(reg)
    db.commit()
    return {"mensaje": "Registro eliminado con éxito"}

from datetime import datetime, timedelta

@app.get("/servicios-diarios/")
def listar_servicios_diarios(
    db: Session = Depends(get_db),
    filtro_tipo: Optional[str] = Query(None, description="dia, semana, mes, anio"),
    fecha_ref: Optional[str] = Query(None, description="Fecha de referencia YYYY-MM-DD"),
    mes: Optional[int] = Query(None, description="Mes (1-12)"),
    anio: Optional[int] = Query(None, description="Año (ej. 2026)")
):
    query = db.query(ServicioDiarioModel)
    resultados = query.all()
    
    # Determinar fecha de referencia actual si no se provee
    ref_date = datetime.strptime(fecha_ref, "%Y-%m-%d") if fecha_ref else datetime.utcnow()

    datos_formateados = []
    for reg in resultados:
        # Filtrado temporal inteligente
        if filtro_tipo == "dia" and fecha_ref:
            if reg.fecha_hora.strftime("%Y-%m-%d") != fecha_ref:
                continue
        elif filtro_tipo == "semana":
            # Semana actual o semana de la fecha de referencia (lunes a domingo)
            start_week = ref_date - timedelta(days=ref_date.weekday())
            end_week = start_week + timedelta(days=6)
            if not (start_week.date() <= reg.fecha_hora.date() <= end_week.date()):
                continue
        elif filtro_tipo == "mes":
            m = mes if mes else ref_date.month
            a = anio if anio else ref_date.year
            if reg.fecha_hora.month != m or reg.fecha_hora.year != a:
                continue
        elif filtro_tipo == "anio":
            a = anio if anio else ref_date.year
            if reg.fecha_hora.year != a:
                continue
        # Compatibilidad con filtros anteriores (mes/anio sueltos)
        elif mes and reg.fecha_hora.month != mes:
            continue
        elif anio and reg.fecha_hora.year != anio:
            continue
            
        barbero = db.query(BarberoModel).filter_by(id=reg.barbero_id).first()
        servicio = db.query(ServicioCatalogoModel).filter_by(id=reg.servicio_id).first()
        
        datos_formateados.append({
            "id": reg.id,
            "fecha_hora": reg.fecha_hora.strftime("%Y-%m-%d %H:%M"),
            "cliente_nombre": reg.cliente_nombre,
            "barbero_id": reg.barbero_id,
            "barbero": barbero.nombre if barbero else "Desconocido",
            "servicio_id": reg.servicio_id,
            "servicio": servicio.nombre_servicio if servicio else "Desconocido",
            "metodo_pago": reg.metodo_pago,
            "precio_cobrado": reg.precio_cobrado,
            "total_cobrado_neto": reg.total_cobrado_neto
        })
        
    return datos_formateados


# --- CITAS ---

def obtener_horarios_dia(fecha_str: str):
    dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    dia_semana = dt.weekday() # 0=Lun, 1=Mar, 2=Mié, 3=Jue, 4=Vie, 5=Sáb, 6=Dom

    if dia_semana in [0, 1, 3, 4]:
        return ["09:00", "09:40", "10:20", "11:00", "11:40", "12:20", "15:40", "16:20", "17:00", "17:40", "18:20", "19:00"]
    elif dia_semana == 2:
        return ["09:00", "09:40", "10:20", "11:00", "11:40", "12:20"]
    elif dia_semana == 5:
        return ["08:40", "09:20", "10:00", "10:40", "11:20", "12:00", "14:00", "14:40", "15:20", "16:00"]
    elif dia_semana == 6:
        return ["08:40", "09:20", "10:00", "10:40", "11:20", "12:00"]
    return []

@app.get("/citas-disponibilidad/")
def consultar_disponibilidad(fecha: str, db: Session = Depends(get_db)):
    horarios = obtener_horarios_dia(fecha)
    if not horarios:
        return {"mensaje": "La barbería permanece cerrada este día.", "bloques": []}

    barberos = db.query(BarberoModel).all()
    citas_existentes = db.query(CitaModel).filter_by(fecha=fecha).all()

    reporte_bloques = []
    for hora in horarios:
        espacios = [{"barbero_id": b.id, "barbero_nombre": b.nombre, "ocupado": False, "cliente": None, "anticipo": None, "cita_id": None} for b in barberos]
        
        for cita in citas_existentes:
            if cita.hora == hora:
                if cita.barbero_id:
                    for esp in espacios:
                        if esp["barbero_id"] == cita.barbero_id and not esp["ocupado"]:
                            esp["ocupado"] = True
                            esp["cliente"] = cita.cliente_nombre
                            esp["anticipo"] = cita.anticipo
                            esp["cita_id"] = cita.id
                            break
                else:
                    for esp in espacios:
                        if not esp["ocupado"]:
                            esp["ocupado"] = True
                            esp["cliente"] = cita.cliente_nombre
                            esp["anticipo"] = cita.anticipo
                            esp["cita_id"] = cita.id
                            break

        reporte_bloques.append({"hora": hora, "espacios": espacios})

    return {"fecha": fecha, "bloques": reporte_bloques}

@app.post("/citas/", status_code=status.HTTP_201_CREATED)
def agendar_cita(cita: CitaCreate, db: Session = Depends(get_db)):
    horarios = obtener_horarios_dia(cita.fecha)
    if cita.hora not in horarios:
        return {"error": "Horario no válido o fuera de servicio."}, 400

    citas_en_hora = db.query(CitaModel).filter_by(fecha=cita.fecha, hora=cita.hora).all()
    if len(citas_en_hora) >= 2:
        return {"error": "Este horario ya está completamente lleno."}, 400

    if cita.barbero_id:
        if any(c.barbero_id == cita.barbero_id for c in citas_en_hora):
            return {"error": "El barbero seleccionado ya tiene cita a esta hora."}, 400
    else:
        barberos_ocupados = [c.barbero_id for c in citas_en_hora if c.barbero_id]
        todos_barberos = db.query(BarberoModel).all()
        libre = next((b for b in todos_barberos if b.id not in barberos_ocupados), None)
        if libre:
            cita.barbero_id = libre.id
        else:
            cita.barbero_id = todos_barberos[0].id if todos_barberos else 1

    nueva_cita = CitaModel(
        cliente_nombre=cita.cliente_nombre,
        fecha=cita.fecha,
        hora=cita.hora,
        anticipo=cita.anticipo if cita.anticipo else "No",
        barbero_id=cita.barbero_id
    )
    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)
    return {"mensaje": "Cita agendada con éxito", "cita_id": nueva_cita.id}

@app.put("/citas/{id}")
def editar_cita(id: int, cita: CitaCreate, db: Session = Depends(get_db)):
    reg = db.query(CitaModel).filter_by(id=id).first()
    if not reg:
        return {"error": "Cita no encontrada"}, 404

    reg.cliente_nombre = cita.cliente_nombre
    reg.fecha = cita.fecha
    reg.hora = cita.hora
    reg.anticipo = cita.anticipo
    reg.barbero_id = cita.barbero_id

    db.commit()
    return {"mensaje": "Cita actualizada con éxito"}

@app.delete("/citas/{id}")
def cancelar_cita(id: int, db: Session = Depends(get_db)):
    reg = db.query(CitaModel).filter_by(id=id).first()
    if not reg:
        return {"error": "Cita no encontrada"}, 404
    db.delete(reg)
    db.commit()
    return {"mensaje": "Cita cancelada con éxito"}

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
            reg.id, reg.fecha_hora.strftime("%Y-%m-%d %H:%M"), reg.cliente_nombre,
            barbero.nombre if barbero else "", servicio.nombre_servicio if servicio else "",
            reg.metodo_pago, reg.precio_cobrado, reg.total_cobrado_neto
        ])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_casa_peter_frank.csv"})

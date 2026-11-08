import io
import csv
import calendar
from datetime import datetime, timedelta
from typing import Optional, List, Any, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, status, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from database import get_db, SessionLocal, engine, Base
from models import (
    BarberoModel,
    ServicioCatalogoModel,
    ServicioDiarioModel,
    CitaModel,
    GastoModel,
    HorarioExcepcionModel,
)

# Crear tablas en caso de que no existan
Base.metadata.create_all(bind=engine)


# --- EVENTO DE INICIO (LIFESPAN SIN WARNINGS) ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        if db.query(BarberoModel).count() == 0:
            for b in ["Peter", "Frank"]:
                db.add(BarberoModel(nombre=b))
        if db.query(ServicioCatalogoModel).count() == 0:
            servicios_base = [
                "Corte",
                "Ceja",
                "Contornos",
                "Barba",
                "Corte + barba",
                "Corte + barba + ceja",
                "Corte + ceja",
                "Barba + ceja",
                "Corte a tijera",
                "Tapper",
            ]
            for s in servicios_base:
                db.add(ServicioCatalogoModel(nombre_servicio=s))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    yield


app = FastAPI(title="Casa Peter Frank - Sistema de Gestión", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- MODELOS PYDANTIC ---

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
    servicio_id: Optional[int] = None
    metodo_pago: Optional[str] = None
    precio_cobrado: Optional[float] = None


class HorarioExtraCreate(BaseModel):
    fecha: str
    hora: str  # Formato "HH:MM"


class GastoSchema(BaseModel):
    fecha: str  # YYYY-MM-DD
    categoria: str
    monto: float
    descripcion: Optional[str] = None


# --- REGLAS DE NEGOCIO Y HELPERS ---

def calcular_ingreso_neto(precio_cobrado: Optional[float], metodo_pago: Optional[str]) -> float:
    """
    Reglas de negocio:
    - Efectivo: 0% comisión (neto = precio).
    - Transferencia: 0% comisión (neto = precio).
    - Tarjeta: 4% comisión (neto = precio * 0.96).
    """
    if precio_cobrado is None:
        return 0.0
    metodo = (metodo_pago or "").strip().lower()
    if metodo == "tarjeta":
        return round(float(precio_cobrado) * (1 - 0.04), 2)
    # Efectivo y Transferencia sin comisión
    return round(float(precio_cobrado), 2)


def obtener_rango_fechas(
    filtro_tipo: Any = None,
    fecha_ref: Any = None,
    mes: Any = None,
    anio: Any = None
) -> Tuple[Optional[datetime], Optional[datetime], Optional[str], Optional[str], str]:
    """
    Calcula rangos de fechas (datetime y strings) compatibles tanto con SQLite como con PostgreSQL.
    Retorna: (start_dt, end_dt, start_str, end_str, label_periodo)
    """
    # Si viene un objeto Query de FastAPI en llamadas directas o None, normalizar
    tipo = str(filtro_tipo).strip().lower() if isinstance(filtro_tipo, str) else "todos"
    f_ref = str(fecha_ref).strip() if isinstance(fecha_ref, str) else None
    m_val = int(mes) if isinstance(mes, int) or (isinstance(mes, str) and mes.isdigit()) else None
    a_val = int(anio) if isinstance(anio, int) or (isinstance(anio, str) and anio.isdigit()) else None

    if tipo == "dia":
        ref_str = f_ref if f_ref else datetime.now().strftime("%Y-%m-%d")
        try:
            dt = datetime.strptime(ref_str, "%Y-%m-%d")
        except Exception:
            dt = datetime.now()
            ref_str = dt.strftime("%Y-%m-%d")
        start_dt = datetime(dt.year, dt.month, dt.day, 0, 0, 0)
        end_dt = datetime(dt.year, dt.month, dt.day, 23, 59, 59, 999999)
        return start_dt, end_dt, ref_str, ref_str, f"Día: {ref_str}"

    elif tipo == "semana":
        if f_ref:
            try:
                ref_dt = datetime.strptime(f_ref, "%Y-%m-%d")
            except Exception:
                ref_dt = datetime.now()
        else:
            ref_dt = datetime.now()

        # Semana de lunes (0) a domingo (6)
        monday = ref_dt - timedelta(days=ref_dt.weekday())
        sunday = monday + timedelta(days=6)
        start_dt = datetime(monday.year, monday.month, monday.day, 0, 0, 0)
        end_dt = datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, 999999)
        start_str = monday.strftime("%Y-%m-%d")
        end_str = sunday.strftime("%Y-%m-%d")
        return start_dt, end_dt, start_str, end_str, f"Semana: {start_str} al {end_str}"

    elif tipo == "mes":
        now = datetime.now()
        m = m_val if m_val else now.month
        a = a_val if a_val else now.year
        _, last_day = calendar.monthrange(a, m)
        start_dt = datetime(a, m, 1, 0, 0, 0)
        end_dt = datetime(a, m, last_day, 23, 59, 59, 999999)
        start_str = f"{a}-{str(m).zfill(2)}-01"
        end_str = f"{a}-{str(m).zfill(2)}-{str(last_day).zfill(2)}"
        return start_dt, end_dt, start_str, end_str, f"Mes: {str(m).zfill(2)}/{a}"

    elif tipo == "anio":
        now = datetime.now()
        a = a_val if a_val else now.year
        start_dt = datetime(a, 1, 1, 0, 0, 0)
        end_dt = datetime(a, 12, 31, 23, 59, 59, 999999)
        start_str = f"{a}-01-01"
        end_str = f"{a}-12-31"
        return start_dt, end_dt, start_str, end_str, f"Año: {a}"

    return None, None, None, None, "Todo el historial"





# --- RUTAS DE NAVEGACIÓN ---

@app.get("/", include_in_schema=False)
def redirectToApp():
    return RedirectResponse(url="/app")


@app.get("/app", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- CATÁLOGOS BASE ---

@app.get("/barberos/")
def listar_barberos(db: Session = Depends(get_db)):
    return db.query(BarberoModel).order_by(BarberoModel.id.asc()).all()


@app.get("/servicios-catalogo/")
def listar_catalogo(db: Session = Depends(get_db)):
    return db.query(ServicioCatalogoModel).order_by(ServicioCatalogoModel.id.asc()).all()


# --- SERVICIOS DIARIOS (INGRESOS) ---

@app.post("/servicios-diarios/", status_code=status.HTTP_201_CREATED)
def crear_servicio_diario(servicio: ServicioDiarioCreate, db: Session = Depends(get_db)):
    neto = calcular_ingreso_neto(servicio.precio_cobrado, servicio.metodo_pago)

    nuevo_registro = ServicioDiarioModel(
        cliente_nombre=servicio.cliente_nombre.strip(),
        barbero_id=servicio.barbero_id,
        servicio_id=servicio.servicio_id,
        metodo_pago=servicio.metodo_pago.strip(),
        precio_cobrado=servicio.precio_cobrado,
        total_cobrado_neto=neto,
        fecha_hora=servicio.fecha_hora if servicio.fecha_hora else datetime.now()
    )
    db.add(nuevo_registro)
    db.commit()
    db.refresh(nuevo_registro)
    return {
        "mensaje": "Registrado con éxito",
        "total_cobrado_neto": nuevo_registro.total_cobrado_neto,
        "id": nuevo_registro.id
    }


@app.put("/servicios-diarios/{id}")
def editar_servicio_diario(id: int, servicio: ServicioDiarioCreate, db: Session = Depends(get_db)):
    reg = db.query(ServicioDiarioModel).filter_by(id=id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    neto = calcular_ingreso_neto(servicio.precio_cobrado, servicio.metodo_pago)

    reg.cliente_nombre = servicio.cliente_nombre.strip()
    reg.barbero_id = servicio.barbero_id
    reg.servicio_id = servicio.servicio_id
    reg.metodo_pago = servicio.metodo_pago.strip()
    reg.precio_cobrado = servicio.precio_cobrado
    reg.total_cobrado_neto = neto
    if servicio.fecha_hora:
        reg.fecha_hora = servicio.fecha_hora

    db.commit()
    return {"mensaje": "Registro actualizado con éxito", "total_cobrado_neto": neto}


@app.get("/servicios-diarios/")
def listar_servicios_diarios(
    db: Session = Depends(get_db),
    filtro_tipo: Optional[str] = Query(None),
    fecha_ref: Optional[str] = Query(None),
    mes: Optional[int] = Query(None),
    anio: Optional[int] = Query(None)
):
    query = db.query(ServicioDiarioModel).options(
        joinedload(ServicioDiarioModel.barbero),
        joinedload(ServicioDiarioModel.servicio)
    )

    start_dt, end_dt, _, _, _ = obtener_rango_fechas(filtro_tipo, fecha_ref, mes, anio)
    if start_dt and end_dt:
        query = query.filter(
            ServicioDiarioModel.fecha_hora >= start_dt,
            ServicioDiarioModel.fecha_hora <= end_dt
        )

    query = query.order_by(ServicioDiarioModel.fecha_hora.desc(), ServicioDiarioModel.id.desc())
    resultados = query.all()

    datos_formateados = []
    for reg in resultados:
        fecha_str = reg.fecha_hora.strftime("%Y-%m-%d %H:%M") if reg.fecha_hora else ""
        datos_formateados.append({
            "id": reg.id,
            "fecha_hora": fecha_str,
            "cliente_nombre": reg.cliente_nombre,
            "barbero_id": reg.barbero_id,
            "barbero": reg.barbero.nombre if reg.barbero else "Desconocido",
            "servicio_id": reg.servicio_id,
            "servicio": reg.servicio.nombre_servicio if reg.servicio else "Desconocido",
            "metodo_pago": reg.metodo_pago,
            "precio_cobrado": reg.precio_cobrado,
            "total_cobrado_neto": reg.total_cobrado_neto
        })
    return datos_formateados


# --- CITAS Y DISPONIBILIDAD ---

def obtener_horarios_dia(fecha_str: str) -> List[str]:
    try:
        dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    except Exception:
        return []
    dia_semana = dt.weekday()  # 0=Lun, 1=Mar, 2=Mié, 3=Jue, 4=Vie, 5=Sáb, 6=Dom

    # Lunes(0), Martes(1), Jueves(3), Viernes(4)
    if dia_semana in [0, 1, 3, 4]:
        return ["09:00", "09:40", "10:20", "11:00", "11:40", "12:20", "15:40", "16:20", "17:00", "17:40", "18:20", "19:00"]
    # Miércoles(2)
    elif dia_semana == 2:
        return ["09:00", "09:40", "10:20", "11:00", "11:40", "12:20"]
    # Sábado(5) - Iniciando 8:00 AM y extendido hasta 5:20 PM (17:20)
    elif dia_semana == 5:
        return ["08:00", "08:40", "09:20", "10:00", "10:40", "11:20", "12:00", "14:00", "14:40", "15:20", "16:00", "16:40", "17:20"]
    # Domingo(6) - Iniciando 8:00 AM
    elif dia_semana == 6:
        return ["08:00", "08:40", "09:20", "10:00", "10:40", "11:20", "12:00"]
    return []


@app.get("/citas-disponibilidad/")
def consultar_disponibilidad(fecha: str, db: Session = Depends(get_db)):
    # 1. Obtener horarios base del día y fusionarlos con los horarios extra habilitados
    horarios_base = obtener_horarios_dia(fecha)
    excepciones_db = db.query(HorarioExcepcionModel).filter_by(fecha=fecha, activo=True).all()
    horas_extra = [ex.hora for ex in excepciones_db]

    # Combinar y ordenar cronológicamente sin duplicados
    horarios = sorted(list(set(horarios_base + horas_extra)))

    if not horarios:
        return {"mensaje": "La barbería permanece cerrada este día.", "bloques": [], "total_citas": 0, "fecha": fecha}

    barberos = db.query(BarberoModel).order_by(BarberoModel.id.asc()).all()
    citas_existentes = db.query(CitaModel).filter_by(fecha=fecha).all()

    reporte_bloques = []
    total_ocupadas = 0

    for hora in horarios:
        espacios = [
            {
                "barbero_id": b.id,
                "barbero_nombre": b.nombre,
                "ocupado": False,
                "cliente": None,
                "anticipo": None,
                "cita_id": None,
                "servicio_id": None,
                "metodo_pago": None,
                "precio_cobrado": None
            }
            for b in barberos
        ]

        for cita in citas_existentes:
            if cita.hora == hora:
                total_ocupadas += 1
                if cita.barbero_id:
                    for esp in espacios:
                        if esp["barbero_id"] == cita.barbero_id and not esp["ocupado"]:
                            esp["ocupado"] = True
                            esp["cliente"] = cita.cliente_nombre
                            esp["anticipo"] = cita.anticipo
                            esp["cita_id"] = cita.id
                            esp["servicio_id"] = cita.servicio_id
                            esp["metodo_pago"] = cita.metodo_pago
                            esp["precio_cobrado"] = cita.precio_cobrado
                            break
                else:
                    for esp in espacios:
                        if not esp["ocupado"]:
                            esp["ocupado"] = True
                            esp["cliente"] = cita.cliente_nombre
                            esp["anticipo"] = cita.anticipo
                            esp["cita_id"] = cita.id
                            esp["servicio_id"] = cita.servicio_id
                            esp["metodo_pago"] = cita.metodo_pago
                            esp["precio_cobrado"] = cita.precio_cobrado
                            break

        reporte_bloques.append({"hora": hora, "espacios": espacios})

    return {
        "fecha": fecha,
        "bloques": reporte_bloques,
        "total_citas": len(citas_existentes)
    }


@app.post("/citas/", status_code=status.HTTP_201_CREATED)
def agendar_cita(cita: CitaCreate, db: Session = Depends(get_db)):
    horarios_base = obtener_horarios_dia(cita.fecha)
    excepciones_db = db.query(HorarioExcepcionModel).filter_by(fecha=cita.fecha, activo=True).all()
    horas_extra = [ex.hora for ex in excepciones_db]
    horarios = sorted(list(set(horarios_base + horas_extra)))

    if cita.hora not in horarios:
        raise HTTPException(status_code=400, detail="Horario no válido o fuera de servicio.")

    todos_barberos = db.query(BarberoModel).all()
    capacidad_maxima = len(todos_barberos) if todos_barberos else 2

    citas_en_hora = db.query(CitaModel).filter_by(fecha=cita.fecha, hora=cita.hora).all()
    if len(citas_en_hora) >= capacidad_maxima:
        raise HTTPException(status_code=400, detail="Este horario ya está completamente lleno.")

    if cita.barbero_id:
        if any(c.barbero_id == cita.barbero_id for c in citas_en_hora):
            raise HTTPException(status_code=400, detail="El barbero seleccionado ya tiene cita a esta hora.")
    else:
        barberos_ocupados = [c.barbero_id for c in citas_en_hora if c.barbero_id]
        libre = next((b for b in todos_barberos if b.id not in barberos_ocupados), None)
        if libre:
            cita.barbero_id = libre.id
        else:
            cita.barbero_id = todos_barberos[0].id if todos_barberos else 1

    # Sincronización automática con ingresos diarios si se incluye pago/servicio
    servicio_diario_id = None
    if cita.servicio_id and cita.precio_cobrado is not None and cita.metodo_pago:
        neto = calcular_ingreso_neto(cita.precio_cobrado, cita.metodo_pago)

        try:
            dt_completa = datetime.strptime(f"{cita.fecha} {cita.hora}", "%Y-%m-%d %H:%M")
        except Exception:
            dt_completa = datetime.now()

        nuevo_ingreso = ServicioDiarioModel(
            cliente_nombre=cita.cliente_nombre.strip(),
            barbero_id=cita.barbero_id,
            servicio_id=cita.servicio_id,
            metodo_pago=cita.metodo_pago.strip(),
            precio_cobrado=cita.precio_cobrado,
            total_cobrado_neto=neto,
            fecha_hora=dt_completa
        )
        db.add(nuevo_ingreso)
        db.commit()
        db.refresh(nuevo_ingreso)
        servicio_diario_id = nuevo_ingreso.id

    nueva_cita = CitaModel(
        cliente_nombre=cita.cliente_nombre.strip(),
        fecha=cita.fecha,
        hora=cita.hora,
        anticipo=cita.anticipo if cita.anticipo and cita.anticipo != "null" else "No",
        barbero_id=cita.barbero_id,
        servicio_id=cita.servicio_id,
        metodo_pago=cita.metodo_pago.strip() if cita.metodo_pago else None,
        precio_cobrado=cita.precio_cobrado,
        servicio_diario_id=servicio_diario_id
    )
    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)

    return {"mensaje": "Cita agendada con éxito", "cita_id": nueva_cita.id}


@app.put("/citas/{id}")
def editar_cita(id: int, cita: CitaCreate, db: Session = Depends(get_db)):
    reg = db.query(CitaModel).filter_by(id=id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Cita no encontrada")

    reg.cliente_nombre = cita.cliente_nombre.strip()
    reg.fecha = cita.fecha
    reg.hora = cita.hora
    reg.anticipo = cita.anticipo if cita.anticipo and cita.anticipo != "null" else "No"
    reg.barbero_id = cita.barbero_id
    reg.servicio_id = cita.servicio_id
    reg.metodo_pago = cita.metodo_pago.strip() if cita.metodo_pago else None
    reg.precio_cobrado = cita.precio_cobrado

    # Sincronizar o actualizar el registro financiero asociado
    if cita.servicio_id and cita.precio_cobrado is not None and cita.metodo_pago:
        neto = calcular_ingreso_neto(cita.precio_cobrado, cita.metodo_pago)

        try:
            dt_completa = datetime.strptime(f"{cita.fecha} {cita.hora}", "%Y-%m-%d %H:%M")
        except Exception:
            dt_completa = datetime.now()

        if reg.servicio_diario_id:
            ingreso_reg = db.query(ServicioDiarioModel).filter_by(id=reg.servicio_diario_id).first()
            if ingreso_reg:
                ingreso_reg.cliente_nombre = cita.cliente_nombre.strip()
                ingreso_reg.barbero_id = cita.barbero_id
                ingreso_reg.servicio_id = cita.servicio_id
                ingreso_reg.metodo_pago = cita.metodo_pago.strip()
                ingreso_reg.precio_cobrado = cita.precio_cobrado
                ingreso_reg.total_cobrado_neto = neto
                ingreso_reg.fecha_hora = dt_completa
            else:
                nuevo_ingreso = ServicioDiarioModel(
                    cliente_nombre=cita.cliente_nombre.strip(),
                    barbero_id=cita.barbero_id,
                    servicio_id=cita.servicio_id,
                    metodo_pago=cita.metodo_pago.strip(),
                    precio_cobrado=cita.precio_cobrado,
                    total_cobrado_neto=neto,
                    fecha_hora=dt_completa
                )
                db.add(nuevo_ingreso)
                db.commit()
                db.refresh(nuevo_ingreso)
                reg.servicio_diario_id = nuevo_ingreso.id
        else:
            nuevo_ingreso = ServicioDiarioModel(
                cliente_nombre=cita.cliente_nombre.strip(),
                barbero_id=cita.barbero_id,
                servicio_id=cita.servicio_id,
                metodo_pago=cita.metodo_pago.strip(),
                precio_cobrado=cita.precio_cobrado,
                total_cobrado_neto=neto,
                fecha_hora=dt_completa
            )
            db.add(nuevo_ingreso)
            db.commit()
            db.refresh(nuevo_ingreso)
            reg.servicio_diario_id = nuevo_ingreso.id

    db.commit()
    return {"mensaje": "Cita actualizada con éxito"}


@app.delete("/citas/{id}")
def cancelar_cita(id: int, db: Session = Depends(get_db)):
    reg = db.query(CitaModel).filter_by(id=id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Cita no encontrada")

    db.delete(reg)
    db.commit()
    return {"mensaje": "Cita cancelada con éxito"}


# --- EXPORTAR REPORTES CSV ---

@app.get("/exportar-csv/")
def exportar_csv(db: Session = Depends(get_db)):
    try:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Fecha y Hora", "Cliente", "Barbero", "Servicio", "Método de Pago", "Precio Cobrado", "Total Neto"])

        registros = db.query(ServicioDiarioModel).options(
            joinedload(ServicioDiarioModel.barbero),
            joinedload(ServicioDiarioModel.servicio)
        ).order_by(ServicioDiarioModel.fecha_hora.desc()).all()

        for reg in registros:
            fecha_str = reg.fecha_hora.strftime("%Y-%m-%d %H:%M") if reg.fecha_hora else ""
            barbero_nombre = reg.barbero.nombre if reg.barbero else "Desconocido"
            servicio_nombre = reg.servicio.nombre_servicio if reg.servicio else "Desconocido"

            writer.writerow([
                reg.id,
                fecha_str,
                reg.cliente_nombre or "Sin nombre",
                barbero_nombre,
                servicio_nombre,
                reg.metodo_pago or "Efectivo",
                reg.precio_cobrado if reg.precio_cobrado is not None else 0.0,
                reg.total_cobrado_neto if reg.total_cobrado_neto is not None else 0.0
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=reporte_casa_peter_frank.csv"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al exportar CSV: {str(e)}")


# --- GASTOS Y BALANCE FINANCIERO ---

@app.post("/gastos/", status_code=status.HTTP_201_CREATED)
def crear_gasto(gasto: GastoSchema, db: Session = Depends(get_db)):
    nuevo_gasto = GastoModel(
        fecha=gasto.fecha.strip(),
        categoria=gasto.categoria.strip(),
        monto=float(gasto.monto),
        descripcion=gasto.descripcion.strip() if gasto.descripcion else None
    )
    db.add(nuevo_gasto)
    db.commit()
    db.refresh(nuevo_gasto)
    return {"mensaje": "Gasto registrado con éxito", "id": nuevo_gasto.id}


@app.get("/gastos/")
def listar_gastos(
    filtro_tipo: str = "todos",
    fecha_ref: Optional[str] = None,
    mes: Optional[int] = None,
    anio: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(GastoModel)
    _, _, start_str, end_str, _ = obtener_rango_fechas(filtro_tipo, fecha_ref, mes, anio)

    if start_str and end_str:
        if start_str == end_str:
            query = query.filter(GastoModel.fecha == start_str)
        else:
            query = query.filter(GastoModel.fecha >= start_str, GastoModel.fecha <= end_str)

    query = query.order_by(GastoModel.fecha.desc(), GastoModel.id.desc())
    return query.all()


@app.get("/balance-financiero/")
def obtener_balance_financiero(
    filtro_tipo: str = "todos",
    fecha_ref: Optional[str] = None,
    mes: Optional[int] = None,
    anio: Optional[int] = None,
    db: Session = Depends(get_db)
):
    start_dt, end_dt, start_str, end_str, periodo_label = obtener_rango_fechas(filtro_tipo, fecha_ref, mes, anio)

    # 1. Total y desglose de Gastos
    query_gastos = db.query(GastoModel)
    if start_str and end_str:
        if start_str == end_str:
            query_gastos = query_gastos.filter(GastoModel.fecha == start_str)
        else:
            query_gastos = query_gastos.filter(GastoModel.fecha >= start_str, GastoModel.fecha <= end_str)
    gastos = query_gastos.all()
    total_gastos = round(sum(g.monto for g in gastos), 2)

    # 2. Total y desglose de Ingresos Netos
    query_ingresos = db.query(ServicioDiarioModel)
    if start_dt and end_dt:
        query_ingresos = query_ingresos.filter(
            ServicioDiarioModel.fecha_hora >= start_dt,
            ServicioDiarioModel.fecha_hora <= end_dt
        )
    servicios = query_ingresos.all()

    total_ingresos = round(sum(s.total_cobrado_neto for s in servicios), 2)
    total_efectivo = round(sum(s.total_cobrado_neto for s in servicios if (s.metodo_pago or "").strip().lower() == "efectivo"), 2)
    total_tarjeta = round(sum(s.total_cobrado_neto for s in servicios if (s.metodo_pago or "").strip().lower() == "tarjeta"), 2)
    total_transferencia = round(sum(s.total_cobrado_neto for s in servicios if (s.metodo_pago or "").strip().lower() == "transferencia"), 2)

    ganancia_neta = round(total_ingresos - total_gastos, 2)

    return {
        "total_ingresos": total_ingresos,
        "total_gastos": total_gastos,
        "ganancia_neta": ganancia_neta,
        "total_efectivo": total_efectivo,
        "total_tarjeta": total_tarjeta,
        "total_transferencia": total_transferencia,
        "periodo_label": periodo_label,
        "filtro_tipo": filtro_tipo,
        "fecha_ref": fecha_ref
    }


# --- GESTIÓN DE HORARIOS EXTRAORDINARIOS ---

@app.post("/horarios-extra/", status_code=status.HTTP_201_CREATED)
def agregar_horario_extra(item: HorarioExtraCreate, db: Session = Depends(get_db)):
    existente = db.query(HorarioExcepcionModel).filter_by(fecha=item.fecha, hora=item.hora).first()
    if existente:
        existente.activo = True
    else:
        nuevo = HorarioExcepcionModel(fecha=item.fecha, hora=item.hora, activo=True)
        db.add(nuevo)
    db.commit()
    return {"mensaje": "Horario extraordinario habilitado con éxito"}


@app.delete("/horarios-extra/")
def eliminar_horario_extra(fecha: str, hora: str, db: Session = Depends(get_db)):
    excepcion = db.query(HorarioExcepcionModel).filter_by(fecha=fecha, hora=hora).first()
    if excepcion:
        db.delete(excepcion)
        db.commit()
    return {"mensaje": "Horario extraordinario removido"}

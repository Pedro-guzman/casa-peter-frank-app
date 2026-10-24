from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.sql import func
from database import Base

class BarberoModel(Base):
    __tablename__ = "barberos"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, index=True)

class ServicioCatalogoModel(Base):
    __tablename__ = "servicios_catalogo"
    id = Column(Integer, primary_key=True, index=True)
    nombre_servicio = Column(String, unique=True, index=True)

class ServicioDiarioModel(Base):
    __tablename__ = "servicios_diarios"
    id = Column(Integer, primary_key=True, index=True)
    cliente_nombre = Column(String, nullable=False)
    barbero_id = Column(Integer, ForeignKey("barberos.id"), nullable=False)
    servicio_id = Column(Integer, ForeignKey("servicios_catalogo.id"), nullable=False)
    metodo_pago = Column(String, nullable=False)
    precio_cobrado = Column(Float, nullable=False)
    total_cobrado_neto = Column(Float, nullable=False)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())

class CitaModel(Base):
    __tablename__ = "citas"
    id = Column(Integer, primary_key=True, index=True)
    cliente_nombre = Column(String, nullable=False)
    fecha = Column(String, nullable=False)  # Formato "YYYY-MM-DD"
    hora = Column(String, nullable=False)   # Formato "08:00"
    anticipo = Column(String, nullable=True)  
    barbero_id = Column(Integer, ForeignKey("barberos.id"), nullable=True)
    
    # Nuevos campos opcionales para unificar el pago desde la cita
    servicio_id = Column(Integer, ForeignKey("servicios_catalogo.id"), nullable=True)
    metodo_pago = Column(String, nullable=True)
    precio_cobrado = Column(Float, nullable=True)
    servicio_diario_id = Column(Integer, ForeignKey("servicios_diarios.id"), nullable=True) # Vinculo opcional con ingresos

class GastoModel(Base):
    __tablename__ = "gastos"

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(String, index=True)          # Formato "YYYY-MM-DD"
    categoria = Column(String, index=True)      # Personal, Renta, Luz, etc.
    monto = Column(Float)
    descripcion = Column(String, nullable=True)

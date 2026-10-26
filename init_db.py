from database import engine, SessionLocal, Base
from models import BarberoModel, ServicioCatalogoModel

print("🔄 Creando tablas y limpiando base de datos...")
Base.metadata.create_all(bind=engine)
print("¡Tablas creadas exitosamente!")

db = SessionLocal()

try:
    # Barberos predeterminados
    barberos = ["Peter", "Frank"]
    for b in barberos:
        existe = db.query(BarberoModel).filter_by(nombre=b).first()
        if not existe:
            db.add(BarberoModel(nombre=b))
            print(f"Barbero agregado: {b}")

    # Catálogo de servicios sin precios
    servicios = [
        "Corte",
        "Ceja",
        "Contornos",
        "Barba",
        "Barba + ceja",
        "Corte + barba",
        "Corte + barba + ceja",
        "Corte + ceja",
        "Barba + ceja",
        "Corte a tijera",
        "Tapper",
    ]
    for s in servicios:
        existe = db.query(ServicioCatalogoModel).filter_by(nombre_servicio=s).first()
        if not existe:
            db.add(ServicioCatalogoModel(nombre_servicio=s))
            print(f"Servicio agregado: {s}")

    db.commit()
    print("¡Datos semilla inyectados con éxito!")
except Exception as e:
    db.rollback()
    print(f"Error al inicializar: {e}")
finally:
    db.close()

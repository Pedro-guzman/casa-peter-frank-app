import unittest
from datetime import datetime, timedelta
import io
import csv

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import (
    BarberoModel,
    ServicioCatalogoModel,
    ServicioDiarioModel,
    CitaModel,
    GastoModel,
    HorarioExcepcionModel,
)
import main
from main import (
    calcular_ingreso_neto,
    obtener_rango_fechas,
    ServicioDiarioCreate,
    CitaCreate,
    GastoSchema,
    HorarioExtraCreate,
)
from fastapi import HTTPException


class TestCasaPeterFrank(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Base de datos SQLite en memoria aislada para las pruebas
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        cls.TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)
        Base.metadata.create_all(bind=cls.engine)

    def setUp(self):
        # Limpiar y preparar sesión para cada test
        self.db = self.TestingSessionLocal()
        # Seed inicial
        for b in ["Peter", "Frank"]:
            self.db.add(BarberoModel(nombre=b))
        for s in ["Corte", "Barba", "Corte + barba"]:
            self.db.add(ServicioCatalogoModel(nombre_servicio=s))
        self.db.commit()

    def tearDown(self):
        self.db.query(CitaModel).delete()
        self.db.query(ServicioDiarioModel).delete()
        self.db.query(GastoModel).delete()
        self.db.query(HorarioExcepcionModel).delete()
        self.db.query(BarberoModel).delete()
        self.db.query(ServicioCatalogoModel).delete()
        self.db.commit()
        self.db.close()

    # --- 1. PRUEBAS DE REGLAS DE NEGOCIO: FORMAS DE PAGO Y COMISIONES ---

    def test_calcular_ingreso_neto_efectivo(self):
        """Efectivo no debe generar comisión (0%)"""
        self.assertEqual(calcular_ingreso_neto(200.0, "Efectivo"), 200.0)
        self.assertEqual(calcular_ingreso_neto(150.5, "efectivo"), 150.5)

    def test_calcular_ingreso_neto_transferencia(self):
        """Transferencia no debe generar comisión (0%)"""
        self.assertEqual(calcular_ingreso_neto(300.0, "Transferencia"), 300.0)
        self.assertEqual(calcular_ingreso_neto(250.0, "transferencia"), 250.0)

    def test_calcular_ingreso_neto_tarjeta(self):
        """Tarjeta debe descontar exactamente el 4% de comisión"""
        # 100 * (1 - 0.04) = 96.0
        self.assertEqual(calcular_ingreso_neto(100.0, "Tarjeta"), 96.0)
        # 200 * 0.96 = 192.0
        self.assertEqual(calcular_ingreso_neto(200.0, "tarjeta"), 192.0)
        # 170 * 0.96 = 163.2
        self.assertEqual(calcular_ingreso_neto(170.0, "Tarjeta"), 163.2)

    def test_calcular_ingreso_neto_nulos_y_ceros(self):
        """Manejo seguro de valores None o cero"""
        self.assertEqual(calcular_ingreso_neto(None, "Efectivo"), 0.0)
        self.assertEqual(calcular_ingreso_neto(0.0, "Tarjeta"), 0.0)

    # --- 2. PRUEBAS DE REGISTRO DE SERVICIOS DIARIOS ---

    def test_crear_servicio_diario_las_tres_modalidades(self):
        """Probar inserción de servicios con Efectivo, Tarjeta y Transferencia"""
        barbero = self.db.query(BarberoModel).first()
        servicio = self.db.query(ServicioCatalogoModel).first()

        # 1. Efectivo
        resp_ef = main.crear_servicio_diario(
            ServicioDiarioCreate(
                cliente_nombre="Cliente Efectivo",
                barbero_id=barbero.id,
                servicio_id=servicio.id,
                metodo_pago="Efectivo",
                precio_cobrado=200.0
            ),
            db=self.db
        )
        self.assertEqual(resp_ef["total_cobrado_neto"], 200.0)

        # 2. Transferencia
        resp_tr = main.crear_servicio_diario(
            ServicioDiarioCreate(
                cliente_nombre="Cliente Transferencia",
                barbero_id=barbero.id,
                servicio_id=servicio.id,
                metodo_pago="Transferencia",
                precio_cobrado=200.0
            ),
            db=self.db
        )
        self.assertEqual(resp_tr["total_cobrado_neto"], 200.0)

        # 3. Tarjeta
        resp_tj = main.crear_servicio_diario(
            ServicioDiarioCreate(
                cliente_nombre="Cliente Tarjeta",
                barbero_id=barbero.id,
                servicio_id=servicio.id,
                metodo_pago="Tarjeta",
                precio_cobrado=200.0
            ),
            db=self.db
        )
        self.assertEqual(resp_tj["total_cobrado_neto"], 192.0)

    # --- 3. PRUEBAS DE CITAS Y REGISTRO DE PAGO (COBRO RÁPIDO) ---

    def test_agendar_y_cobrar_cita_con_transferencia(self):
        """Agendar una cita y registrar el pago con Transferencia"""
        barbero = self.db.query(BarberoModel).first()
        servicio = self.db.query(ServicioCatalogoModel).first()

        # Agendar cita sin pago inicial
        resp_cita = main.agendar_cita(
            CitaCreate(
                cliente_nombre="Carlos Santana",
                fecha="2026-11-09", # Lunes
                hora="09:00",
                anticipo="No",
                barbero_id=barbero.id
            ),
            db=self.db
        )
        cita_id = resp_cita["cita_id"]

        # Registrar pago mediante edición / cobro rápido con Transferencia
        main.editar_cita(
            id=cita_id,
            cita=CitaCreate(
                cliente_nombre="Carlos Santana",
                fecha="2026-11-09",
                hora="09:00",
                anticipo="No",
                barbero_id=barbero.id,
                servicio_id=servicio.id,
                metodo_pago="Transferencia",
                precio_cobrado=250.0
            ),
            db=self.db
        )

        cita_db = self.db.query(CitaModel).filter_by(id=cita_id).first()
        self.assertEqual(cita_db.metodo_pago, "Transferencia")
        self.assertEqual(cita_db.precio_cobrado, 250.0)
        self.assertIsNotNone(cita_db.servicio_diario_id)

        # Verificar que el registro financiero se haya creado con 0% de comisión
        ingreso = self.db.query(ServicioDiarioModel).filter_by(id=cita_db.servicio_diario_id).first()
        self.assertEqual(ingreso.total_cobrado_neto, 250.0)
        self.assertEqual(ingreso.metodo_pago, "Transferencia")

    def test_agendar_cita_con_anticipo(self):
        """Agendar cita con anticipo y verificar persistencia y retorno en disponibilidad"""
        barbero = self.db.query(BarberoModel).first()
        resp = main.agendar_cita(
            CitaCreate(
                cliente_nombre="Roberto Gomez",
                fecha="2026-11-09",
                hora="10:20",
                anticipo="$150 en efectivo",
                barbero_id=barbero.id
            ),
            db=self.db
        )
        cita_id = resp["cita_id"]
        disp = main.consultar_disponibilidad("2026-11-09", db=self.db)
        bloque_1020 = next(b for b in disp["bloques"] if b["hora"] == "10:20")
        espacio_ocupado = next(e for e in bloque_1020["espacios"] if e["ocupado"])
        self.assertEqual(espacio_ocupado["anticipo"], "$150 en efectivo")
        self.assertEqual(espacio_ocupado["cliente"], "Roberto Gomez")

    def test_agendar_cita_fuera_de_horario_arroja_http_exception(self):
        """Validar que agendar fuera de horario arroja HTTPException 400 y no tupla 200"""
        barbero = self.db.query(BarberoModel).first()
        with self.assertRaises(HTTPException) as cm:
            main.agendar_cita(
                CitaCreate(
                    cliente_nombre="Invalido",
                    fecha="2026-11-09",
                    hora="03:00", # Horario cerrado
                    barbero_id=barbero.id
                ),
                db=self.db
            )
        self.assertEqual(cm.exception.status_code, 400)

    # --- 4. PRUEBAS DE FILTROS POR PERIODO: DÍA, SEMANA, MES ---

    def test_obtener_rango_fechas_dia(self):
        s_dt, e_dt, s_str, e_str, label = obtener_rango_fechas("dia", "2026-11-05")
        self.assertEqual(s_str, "2026-11-05")
        self.assertEqual(e_str, "2026-11-05")
        self.assertEqual(s_dt.day, 5)
        self.assertEqual(e_dt.day, 5)
        self.assertIn("2026-11-05", label)

    def test_obtener_rango_fechas_semana(self):
        # 2026-11-05 es Jueves. Lunes es 2026-11-02, Domingo es 2026-11-08
        s_dt, e_dt, s_str, e_str, label = obtener_rango_fechas("semana", "2026-11-05")
        self.assertEqual(s_str, "2026-11-02")
        self.assertEqual(e_str, "2026-11-08")
        self.assertEqual(s_dt.day, 2)
        self.assertEqual(e_dt.day, 8)
        self.assertIn("Semana", label)

    def test_obtener_rango_fechas_mes(self):
        s_dt, e_dt, s_str, e_str, label = obtener_rango_fechas("mes", mes=11, anio=2026)
        self.assertEqual(s_str, "2026-11-01")
        self.assertEqual(e_str, "2026-11-30")
        self.assertEqual(s_dt.day, 1)
        self.assertEqual(e_dt.day, 30)
        self.assertIn("11/2026", label)

    def test_balance_financiero_filtros_dia_semana_mes(self):
        """Verificar cálculo sincronizado de ingresos, gastos y ganancia neta por periodos"""
        barbero = self.db.query(BarberoModel).first()
        servicio = self.db.query(ServicioCatalogoModel).first()

        # Ingreso día 2026-11-05: Tarjeta 200 (neto 192)
        self.db.add(ServicioDiarioModel(
            cliente_nombre="Test 1",
            barbero_id=barbero.id,
            servicio_id=servicio.id,
            metodo_pago="Tarjeta",
            precio_cobrado=200.0,
            total_cobrado_neto=192.0,
            fecha_hora=datetime(2026, 11, 5, 10, 0)
        ))

        # Ingreso día 2026-11-06: Transferencia 300 (neto 300)
        self.db.add(ServicioDiarioModel(
            cliente_nombre="Test 2",
            barbero_id=barbero.id,
            servicio_id=servicio.id,
            metodo_pago="Transferencia",
            precio_cobrado=300.0,
            total_cobrado_neto=300.0,
            fecha_hora=datetime(2026, 11, 6, 15, 0)
        ))

        # Gasto día 2026-11-05: 50.0
        self.db.add(GastoModel(fecha="2026-11-05", categoria="Insumos", monto=50.0, descripcion="Cuchillas"))

        # Gasto día 2026-11-06: 80.0
        self.db.add(GastoModel(fecha="2026-11-06", categoria="Personal", monto=80.0, descripcion="Comida"))

        self.db.commit()

        # 1. Filtro por Día (2026-11-05): Ingreso 192, Gasto 50, Neto 142
        bal_dia = main.obtener_balance_financiero("dia", fecha_ref="2026-11-05", db=self.db)
        self.assertEqual(bal_dia["total_ingresos"], 192.0)
        self.assertEqual(bal_dia["total_gastos"], 50.0)
        self.assertEqual(bal_dia["ganancia_neta"], 142.0)
        self.assertEqual(bal_dia["total_tarjeta"], 192.0)
        self.assertEqual(bal_dia["total_transferencia"], 0.0)

        # 2. Filtro por Semana (semana del 2 al 8 de nov): Ingresos 192 + 300 = 492, Gastos 50 + 80 = 130, Neto 362
        bal_sem = main.obtener_balance_financiero("semana", fecha_ref="2026-11-05", db=self.db)
        self.assertEqual(bal_sem["total_ingresos"], 492.0)
        self.assertEqual(bal_sem["total_gastos"], 130.0)
        self.assertEqual(bal_sem["ganancia_neta"], 362.0)
        self.assertEqual(bal_sem["total_tarjeta"], 192.0)
        self.assertEqual(bal_sem["total_transferencia"], 300.0)

        # 3. Filtro por Mes (Noviembre 2026)
        bal_mes = main.obtener_balance_financiero("mes", mes=11, anio=2026, db=self.db)
        self.assertEqual(bal_mes["total_ingresos"], 492.0)
        self.assertEqual(bal_mes["total_gastos"], 130.0)
        self.assertEqual(bal_mes["ganancia_neta"], 362.0)

    # --- 5. PRUEBA DE DISPONIBILIDAD Y CALENDARIO ---

    def test_consultar_disponibilidad_dia(self):
        """Verificar reporte de bloques y total_citas"""
        res = main.consultar_disponibilidad("2026-11-09", db=self.db) # Lunes
        self.assertEqual(res["fecha"], "2026-11-09")
        self.assertGreater(len(res["bloques"]), 0)
        self.assertEqual(res["total_citas"], 0)
        # Cada bloque debe tener los 2 barberos disponibles
        self.assertEqual(len(res["bloques"][0]["espacios"]), 2)
        self.assertFalse(res["bloques"][0]["espacios"][0]["ocupado"])

    # --- 6. PRUEBA DE HORARIOS EXTRAORDINARIOS Y CANCELACIÓN ---

    def test_horarios_extraordinarios_y_disponibilidad(self):
        """Habilitar horario extra dinámico y verificar que aparezca en disponibilidad"""
        # Habilitar horario 07:00 para el lunes 2026-11-09
        resp = main.agregar_horario_extra(HorarioExtraCreate(fecha="2026-11-09", hora="07:00"), db=self.db)
        self.assertIn("éxito", resp["mensaje"])

        disp = main.consultar_disponibilidad("2026-11-09", db=self.db)
        horas = [b["hora"] for b in disp["bloques"]]
        self.assertIn("07:00", horas)

        # Eliminar horario extra
        del_resp = main.eliminar_horario_extra(fecha="2026-11-09", hora="07:00", db=self.db)
        self.assertIn("removido", del_resp["mensaje"])

        disp2 = main.consultar_disponibilidad("2026-11-09", db=self.db)
        horas2 = [b["hora"] for b in disp2["bloques"]]
        self.assertNotIn("07:00", horas2)

    def test_cancelar_cita(self):
        """Cancelar cita y verificar eliminación"""
        barbero = self.db.query(BarberoModel).first()
        c = main.agendar_cita(
            CitaCreate(
                cliente_nombre="A Cancelar",
                fecha="2026-11-09",
                hora="10:20",
                barbero_id=barbero.id
            ),
            db=self.db
        )
        cita_id = c["cita_id"]
        del_res = main.cancelar_cita(cita_id, db=self.db)
        self.assertIn("cancelada", del_res["mensaje"])

        # Intentar cancelar de nuevo debe arrojar 404
        with self.assertRaises(HTTPException) as cm:
            main.cancelar_cita(cita_id, db=self.db)
        self.assertEqual(cm.exception.status_code, 404)

    # --- 7. PRUEBA DE EXPORTACIÓN CSV ---

    def test_exportar_csv(self):
        """Verificar exportación de CSV con todas las columnas y sin errores"""
        barbero = self.db.query(BarberoModel).first()
        servicio = self.db.query(ServicioCatalogoModel).first()
        self.db.add(ServicioDiarioModel(
            cliente_nombre="Cliente CSV",
            barbero_id=barbero.id,
            servicio_id=servicio.id,
            metodo_pago="Transferencia",
            precio_cobrado=150.0,
            total_cobrado_neto=150.0,
            fecha_hora=datetime(2026, 11, 5, 10, 0)
        ))
        self.db.commit()

        import asyncio

        async def _leer_async(aiter):
            res = []
            async for x in aiter:
                res.append(x)
            return res

        stream_resp = main.exportar_csv(db=self.db)
        self.assertEqual(stream_resp.status_code, 200)
        chunks = asyncio.run(_leer_async(stream_resp.body_iterator))
        content = b"".join(chunks).decode("utf-8") if isinstance(chunks[0], bytes) else "".join(chunks)
        self.assertIn("Cliente CSV", content)
        self.assertIn("Transferencia", content)
        self.assertIn("150.0", content)


if __name__ == "__main__":
    unittest.main()

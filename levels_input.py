import re
import os
from datetime import datetime

ENGINE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "engine.py"
)

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def input_level(name, example):
    while True:
        try:
            raw = input("  " + name + " : ").strip()
            val = float(raw)
            if val <= 0:
                print("  ERROR: debe ser mayor a 0")
                continue
            return val
        except ValueError:
            print("  ERROR: ingresa un numero valido (ej: " + example + ")")

def update_engine(vah, poc, val):
    try:
        with open(ENGINE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"^VAH\s*=\s*[\d.]+", "VAH = " + str(vah), content, flags=re.MULTILINE)
        content = re.sub(r"^POC\s*=\s*[\d.]+", "POC = " + str(poc), content, flags=re.MULTILINE)
        content = re.sub(r"^VAL\s*=\s*[\d.]+", "VAL = " + str(val), content, flags=re.MULTILINE)
        with open(ENGINE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print("  ERROR: " + str(e))
        return False

def verify_levels(vah, poc, val):
    warnings = []
    if not (val < poc < vah):
        warnings.append("CRITICO: VAL < POC < VAH requerido")
    if vah - val > 100:
        warnings.append("Rango muy amplio (" + str(vah - val) + " pts)")
    if vah - val < 5:
        warnings.append("Rango muy estrecho (" + str(vah - val) + " pts)")
    return warnings

def run():
    clear()
    today = datetime.now().strftime("%A %d/%m/%Y  %H:%M CR")
    print("=" * 50)
    print("  GIBBZ SMC COP — Niveles del Dia")
    print("  " + today)
    print("=" * 50)
    print()
    print("  Fuente: SpotGamma / ATAS VA Profile")
    print("  Instrumento: MES (Micro E-mini S&P 500)")
    print()
    print("  Ingresa los niveles de hoy:")
    print()

    vah = input_level("VAH (Value Area High)", "7320.0")
    poc = input_level("POC (Point of Control)", "7300.0")
    val = input_level("VAL (Value Area Low) ", "7280.0")
    print()

    warnings = verify_levels(vah, poc, val)
    if warnings:
        print("  ADVERTENCIAS:")
        for w in warnings:
            print("  ! " + w)
        print()
        if any("CRITICO" in w for w in warnings):
            print("  ERROR CRITICO — corrige los niveles.")
            input("  Presiona Enter para reintentar...")
            return run()
        confirm = input("  Continuar? (s/n): ").strip().lower()
        if confirm != "s":
            return run()
        print()

    print("  Actualizando engine.py...")
    ok = update_engine(vah, poc, val)

    if ok:
        print()
        print("=" * 50)
        print("  NIVELES ACTUALIZADOS")
        print("=" * 50)
        print()
        print("  VAH  =  " + str(vah))
        print("  POC  =  " + str(poc))
        print("  VAL  =  " + str(val))
        print()
        print("  Rango VA   : " + str(round(vah - val, 2)) + " pts")
        print("  POC-VAH    : " + str(round(vah - poc, 2)) + " pts")
        print("  POC-VAL    : " + str(round(poc - val, 2)) + " pts")
        print()
        print("=" * 50)
        print("  SISTEMA LISTO PARA OPERAR")
        print("  Ejecuta: python engine.py")
        print("=" * 50)
        print()
    else:
        print("  FALLO — edita engine.py manualmente.")

    input("  Presiona Enter para cerrar...")

if __name__ == "__main__":
    run()
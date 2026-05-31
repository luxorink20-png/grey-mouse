import os

print("=== GIBBZ SYSTEM CHECK ===")
print()

files = ["state.py", "event_engine.py", "engine_view.py", "levels.py", "engine.py"]
print("ARCHIVOS:")
for f in files:
    status = "OK  " if os.path.exists(f) else "FALTA"
    print("  " + status + "  " + f)
print()

print("IMPORTS:")
try:
    from state import GibbzState
    print("  OK   state.py")
except Exception as e:
    print("  ERROR  state.py -> " + str(e))

try:
    from event_engine import EventEngine
    print("  OK   event_engine.py")
except Exception as e:
    print("  ERROR  event_engine.py -> " + str(e))

try:
    from engine_view import EngineView
    print("  OK   engine_view.py")
except Exception as e:
    print("  ERROR  engine_view.py -> " + str(e))

try:
    from levels import create_levels
    print("  OK   levels.py")
except Exception as e:
    print("  ERROR  levels.py -> " + str(e))
print()

print("TEST FUNCIONAL:")
from state import GibbzState
from event_engine import EventEngine
from levels import create_levels
import random

s = GibbzState()
e = EventEngine(window=10)
l = create_levels(vah=7260, poc=7245, val=7230)
s.start()
print("  OK   GibbzState is_running=" + str(s.is_running))

p = 7245.0
for i in range(5):
    move = random.uniform(-4.0, 4.0)
    p    = round(p + move, 2)
    raw  = {"price": p, "bid_volume": 400, "ask_volume": 600, "trades": 50}
    r    = e.process(raw)
    ctx  = l.get_context(p)
    print("  tick " + str(i+1) + "  price=" + str(p) + "  event=" + r["event"] + "  zone=" + ctx.zone + "  bias=" + ctx.reaction_bias)

print()
print("=== SISTEMA LISTO ===")
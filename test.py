from state import GibbzState

gibbz = GibbzState()

gibbz.start()
gibbz.update_price(7225)
gibbz.set_bias("bullish")

gibbz.add_level({"price": 7200, "type": "support"})
gibbz.add_alert("test alert")

print("RUNNING:", gibbz.is_running)
print("PRICE:", gibbz.price)
print("BIAS:", gibbz.bias)
print("LEVELS:", gibbz.levels)
print("ALERTS:", gibbz.alerts)
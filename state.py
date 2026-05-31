class GibbzState:
    def __init__(self):
        self.price         = 0.0
        self.is_running    = False
        self.last_event    = "INIT"
        self.level_context = None

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False

    def update_price(self, price: float):
        self.price = price